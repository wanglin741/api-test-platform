"""每分钟 tick:扫描到期调度并派发 run(功能模块 §2.2,spec #35 tick 语义)。

流程:扫 id(不加锁)→ 逐条 FOR UPDATE SKIP LOCKED 重校验 → 每条一个事务
→ 先提交再投递 → 投递异常落 error run(summary 标 dispatch_failed),避免
永久 pending 挡住重叠检查。

beat 必须单实例(架构设计 §9);SKIP LOCKED 只是误配兜底。misfire 基准 =
max(next_run_at, now):tick 只在 next_run_at <= now 时进入,基准即 now,
不补跑、不遍历中间时点。

测试缝(spec Testing Decisions #2):任务同步体,`.apply()` 直接调;派发在
调用点按属性取 execute_run,测试整体替换 app.tasks.engine_tasks.execute_run
即可(替身只记录 run_id,或抛异常驱动补偿路径)。next_run_at 用同步连接改成
过期,不等真实时间。
"""

import datetime
import logging

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.schedule_time import compute_next
from app.db.session import make_sync_engine, make_sync_session_factory
from app.models import Project, Run, Schedule
from app.run_builder import build_plan_run
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# 单批处理上限(spec 用户故事 #18):到期很多时单次派发不失控
TICK_BATCH_LIMIT = 100

# 重叠判定:该调度存在未终态的 run 就跳过本次,不排队(spec 用户故事 #16)
OVERLAP_STATUSES = ("pending", "running", "stopping")

# last_skip_reason 取值(前端有中文映射,是稳定契约)
SKIP_OVERLAP = "overlap"
SKIP_ENVIRONMENT_MISSING = "environment_missing"
SKIP_PLAN_EMPTY = "plan_empty"
SKIP_PLAN_DELETED = "plan_deleted"
SKIP_PROJECT_DELETED = "project_deleted"

# builder 的校验错误码 → 跳过原因
_SKIP_REASONS = {
    "plan_not_found": SKIP_PLAN_DELETED,
    "plan_items_empty": SKIP_PLAN_EMPTY,
    "environment_required": SKIP_ENVIRONMENT_MISSING,
    "environment_not_found": SKIP_ENVIRONMENT_MISSING,
}
# 这些原因同时停用(计划/项目已删,留着只会一直失败);
# 环境缺失/清单为空是暂态 → 跳过推进、保留启用(spec 决策 #4)
_DISABLE_REASONS = {SKIP_PLAN_DELETED, SKIP_PROJECT_DELETED}


def _skip(schedule: Schedule, now: datetime.datetime, reason: str) -> None:
    """记录最近一次跳过并推进 next_run_at;计划/项目已删则停用置空(决策 #2/#4)。"""
    schedule.last_skip_at = now
    schedule.last_skip_reason = reason
    if reason in _DISABLE_REASONS:
        schedule.enabled = False
        schedule.next_run_at = None
    else:
        schedule.next_run_at = compute_next(schedule.cron_expression, schedule.timezone, now)


@celery_app.task(name="engine.dispatch_due_schedules")
def dispatch_due_schedules() -> int:
    """扫到期调度逐条派发,返回本批派发的 run 数。"""
    settings = get_settings()
    engine = make_sync_engine(settings.database_url_sync)
    try:
        factory = make_sync_session_factory(engine)
        # 第一遍只扫 id 不加锁:锁粒度收敛到逐条事务,长扫描不占行锁
        with factory() as session:
            now = datetime.datetime.now(datetime.UTC)
            due_ids = session.execute(
                select(Schedule.id)
                .where(
                    Schedule.enabled.is_(True),
                    Schedule.deleted_at.is_(None),
                    Schedule.next_run_at.is_not(None),
                    Schedule.next_run_at <= now,
                )
                .order_by(Schedule.next_run_at, Schedule.id)
                .limit(TICK_BATCH_LIMIT)
            ).scalars().all()
        return sum(1 for schedule_id in due_ids if _dispatch_one(factory, schedule_id, now))
    finally:
        engine.dispose()


def _dispatch_one(factory, schedule_id: int, now: datetime.datetime) -> bool:
    """单条调度一个事务:加锁重校验 → 决策 → 提交 → 投递。返回是否派发。"""
    with factory() as session:
        schedule = session.execute(
            select(Schedule).where(Schedule.id == schedule_id).with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        # 加锁后重校验到期条件:误配的多 beat 并发时,可能已被别人处理或停用
        if (
            schedule is None
            or not schedule.enabled
            or schedule.deleted_at is not None
            or schedule.next_run_at is None
            or schedule.next_run_at > now
        ):
            return False

        project = session.get(Project, schedule.project_id)
        if project is None or project.deleted_at is not None:
            _skip(schedule, now, SKIP_PROJECT_DELETED)
            session.commit()
            return False

        if _has_active_run(session, schedule.id):
            _skip(schedule, now, SKIP_OVERLAP)
            session.commit()
            return False

        try:
            run = build_plan_run(
                session,
                project_id=schedule.project_id,
                plan_id=schedule.plan_id,
                environment_id=schedule.environment_id,
                trigger_mode="schedule",
                triggered_by=None,  # 定时 run 不归属个人(spec 决策 #6)
                schedule_id=schedule.id,
            )
        except AppError as exc:
            # 失效语义:按 builder 的稳定 code 映射,不重复写一遍校验
            _skip(schedule, now, _SKIP_REASONS.get(exc.code, exc.code))
            session.commit()
            return False

        schedule.last_run_at = now
        schedule.last_run_id = run.id
        schedule.next_run_at = compute_next(schedule.cron_expression, schedule.timezone, now)
        # 先提交再投递:worker(真实或 eager)用独立会话读 run,
        # 且执行期间不得持有 schedule 行锁
        session.commit()

        try:
            # 函数内取执行任务:模块顶层导入会成环(engine_tasks → celery_app
            # → schedule_tasks → engine_tasks 未初始化完)。调用时按当前属性
            # 取,测试整体替换 app.tasks.engine_tasks.execute_run 即生效。
            from app.tasks.engine_tasks import execute_run

            execute_run.delay(str(run.id))
        except Exception:
            # 投递失败补偿:落 error 终态,重叠检查不会被永久 pending 挡住
            logger.exception("调度 %s 派发 run %s 失败,落 error 终态", schedule.id, run.id)
            session.rollback()
            stranded = session.get(Run, run.id)
            if stranded is not None and stranded.status == "pending":
                stranded.status = "error"
                stranded.finished_at = func.now()
                stranded.summary = {
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "error": 1,
                    "skipped": 0,
                    "assertion_total": 0,
                    "assertion_passed": 0,
                    "dispatch_failed": True,
                }
                session.commit()
        return True


def _has_active_run(session, schedule_id: int) -> bool:
    return (
        session.scalar(
            select(func.count())
            .select_from(Run)
            .where(Run.schedule_id == schedule_id, Run.status.in_(OVERLAP_STATUSES))
        )
        or 0
    ) > 0
