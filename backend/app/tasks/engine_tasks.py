"""执行任务:触发到落库的胶水层(执行引擎 §13)。

职责仅四步:同步会话加载配置 → 组装 EngineContext → 驱动引擎 → 汇总写终态。
任何业务规则(变量、断言、取消时机)都在引擎里。

worker 全程同步 Session + psycopg(D6):AsyncClient 只在协程内部创建,
引擎协程跑在独立线程的新事件循环里,不跨事件循环复用任何异步对象。
"""

import asyncio
import hashlib
import logging
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import httpx
import redis as redis_py
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import make_sync_engine, make_sync_session_factory
from app.engine import (
    EngineContext,
    PlanCase,
    execute as engine_execute,
    execute_plan as engine_execute_plan,
)
from app.engine.dsl import parse_dsl
from app.engine.results import StepResult, response_size
from app.engine.variables import compose_scope_variables
from app.event_names import RUN_FINISHED, RUN_STARTED
from app.models import (
    RUN_TERMINAL_STATUSES,
    Environment,
    Run,
    RunStepResult,
    TestCase,
    TestCaseVersion,
    TestPlan,
    TestPlanItem,
    Variable,
)
from app.quality_gate import evaluate_quality_gate
from app.run_events import StreamPublisher, finished_payload
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


class OwnershipLost(Exception):
    """所有权令牌不匹配:本进程已被判定为僵尸 worker,放弃写入。"""


class RedisCancellationToken:
    """D3:持久化的带外取消信号,worker 协作式轮询,本地缓存 0.2s。"""

    def __init__(self, client: redis_py.Redis, key: str, cache_ttl: float = 0.2) -> None:
        self._client = client
        self._key = key
        self._cache_ttl = cache_ttl
        self._cache_until = 0.0
        self._cached = False

    def cancelled(self) -> bool:
        now = time.monotonic()
        if now >= self._cache_until:
            self._cached = bool(self._client.exists(self._key))
            self._cache_until = now + self._cache_ttl
        return self._cached


class DbSink:
    """每步结束立即写 run_step_result 并提交 —— 执行中查询即可看到已完成步骤。

    step_types 按用例序号分组:单用例执行传 {0: {...}},计划执行传
    {case_index: {step_index: step_type}}(结果行的 case_index 缺省 0,两形统一)。
    """

    def __init__(
        self,
        session: Session,
        run_id: uuid.UUID,
        worker_id: str,
        step_types: dict[int, dict[int, str]],
    ) -> None:
        self._session = session
        self._run_id = run_id
        self._worker_id = worker_id
        self._step_types = step_types
        self.assertion_total = 0
        self.assertion_passed = 0

    def on_step_finished(self, result: StepResult) -> None:
        run = self._session.get(Run, self._run_id)
        if run is None or run.worker_id != self._worker_id:
            raise OwnershipLost(f"run {self._run_id} 所有权已转移,放弃写入")

        request = result.request
        response = result.response
        body_bytes = response.body.encode("utf-8") if response and response.body is not None else None
        assertions = [asdict(a) for a in result.assertions]
        self.assertion_total += len(assertions)
        self.assertion_passed += sum(1 for a in assertions if a["passed"])
        row = RunStepResult(
            run_id=self._run_id,
            step_index=result.step_index,
            step_name=result.step_name,
            step_type=self._step_types.get(result.case_index, {}).get(result.step_index, "http"),
            status=result.status,
            duration_ms=int(result.duration_ms or 0),
            case_index=result.case_index,
            case_id=result.case_id,
            request_method=request.method if request else None,
            request_url=request.url if request else None,
            request_headers=request.headers if request else None,
            request_body=request.body if request else None,
            response_status=response.status_code if response else None,
            response_headers=response.headers if response else None,
            response_size=response_size(response),
            response_body=body_bytes,
            # >64KB 转对象存储(MVP 不接 MinIO),object_key 始终为空
            response_body_object_key=None,
            response_body_sha256=hashlib.sha256(body_bytes).hexdigest() if body_bytes else None,
            response_body_truncated=bool(response.truncated) if response else False,
            extracted_variables=result.extracted,
            assertions=assertions,
            assertion_total=len(assertions),
            assertion_passed=sum(1 for a in assertions if a["passed"]),
            error_message=result.error_message,
        )
        self._session.add(row)
        self._session.commit()


def _load_variables(session: Session, run: Run) -> tuple[dict, dict]:
    rows = session.execute(
        select(Variable.name, Variable.value, Variable.environment_id).where(
            Variable.project_id == run.project_id
        )
    ).all()
    environment = session.get(Environment, run.environment_id)
    return compose_scope_variables(
        rows, run.environment_id, environment.base_url if environment else None
    )


def _publish_run_finished(
    run_id: str, status: str, summary: dict | None, settings: Settings
) -> None:
    """终态事件兜底:无常驻 publisher 的退出路径也补发一条,订阅端才知道收尾。

    覆盖两类窗口 —— 任务重投时执行已终态(如 pending 期被取消)、崩溃发生
    在 publisher 构造之前(DB 错误、版本缺失、DSL 解析失败)。重复终态
    事件对客户端幂等无害。发布失败由 StreamPublisher 吞掉,不影响执行。
    """
    StreamPublisher(
        redis_py.Redis.from_url(settings.redis_url),
        run_id,
        max_len=settings.run_stream_max_len,
        ttl_seconds=settings.run_stream_ttl_seconds,
    ).publish(RUN_FINISHED, finished_payload(status, summary))


@celery_app.task(name="engine.execute_run", bind=True)
def execute_run(self, run_id: str) -> None:
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{uuid.uuid4()}"
    sync_engine = make_sync_engine(settings.database_url_sync)
    # 进度事件发布器:在 Redis 客户端创建前崩溃则保持 None,兜底路径跳过发布
    publisher: StreamPublisher | None = None
    try:
        with make_sync_session_factory(sync_engine)() as session:
            run = session.get(Run, uuid.UUID(run_id))
            # 幂等检查:acks_late + reject_on_worker_lost 下任务会被重投
            if run is None or run.status in RUN_TERMINAL_STATUSES:
                if run is not None:
                    # 终态可能来自 pending 期取消(无 publisher 发过事件):
                    # 补一条终态,订阅端才不会对着一个已结束的执行干等
                    _publish_run_finished(run_id, run.status, run.summary, settings)
                return

            run.status = "running"
            run.worker_id = worker_id
            run.started_at = func.now()  # 时间戳一律 DB now(),不信任应用时钟
            run.celery_task_id = self.request.id
            session.commit()
            session.refresh(run)  # 取回 DB 生成的时间戳

            # ── 组装执行输入:单用例锁一个版本,计划锁整份清单的当前版本 ──
            plan_cases: list[PlanCase] = []
            if run.plan_id is not None:
                plan = session.get(TestPlan, run.plan_id)
                items = session.execute(
                    select(TestPlanItem)
                    .where(TestPlanItem.plan_id == run.plan_id)
                    .order_by(TestPlanItem.sort_order, TestPlanItem.id)
                ).scalars()
                for item in items:
                    case = session.get(TestCase, item.item_id)
                    if case is None or case.deleted_at is not None:
                        raise LookupError(f"计划用例已不存在: {item.item_id}")
                    version = session.execute(
                        select(TestCaseVersion).where(
                            TestCaseVersion.test_case_id == item.item_id,
                            TestCaseVersion.version == case.current_version,
                        )
                    ).scalar_one()
                    plan_cases.append(
                        PlanCase(case_id=item.item_id, case_name=case.name, dsl=parse_dsl(version.dsl))
                    )
                total_steps = sum(len(pc.dsl.steps) for pc in plan_cases)
                step_types = {
                    i: {j: step.type for j, step in enumerate(pc.dsl.steps)}
                    for i, pc in enumerate(plan_cases)
                }
            else:
                version = session.execute(
                    select(TestCaseVersion).where(
                        TestCaseVersion.test_case_id == run.case_id,
                        TestCaseVersion.version == run.case_version,
                    )
                ).scalar_one()
                dsl = parse_dsl(version.dsl)
                total_steps = len(dsl.steps)
                step_types = {0: {i: step.type for i, step in enumerate(dsl.steps)}}

            project_vars, env_vars = _load_variables(session, run)

            redis_client = redis_py.Redis.from_url(settings.redis_url)
            cancellation = RedisCancellationToken(redis_client, f"run:cancel:{run_id}")
            publisher = StreamPublisher(
                redis_client,
                run_id,
                max_len=settings.run_stream_max_len,
                ttl_seconds=settings.run_stream_ttl_seconds,
                progress_interval_ms=settings.run_progress_interval_ms,
            )
            sink = DbSink(session, run.id, worker_id, step_types)

            def http_client_factory(verify: bool = True) -> httpx.AsyncClient:
                # 在引擎的事件循环内被调用,AsyncClient 不跨循环复用。
                # trust_env=False:执行流量直达配置的 base_url,不读系统/环境代理
                # (httpx 在 Windows 会读注册表系统代理,宿主机开着代理时
                #  不可达目标会变成代理的 502 响应,连接失败语义被吞掉)。
                return httpx.AsyncClient(follow_redirects=True, verify=verify, trust_env=False)

            # run.started 由任务层发:started_at 已取回 DB 时间,步数来自锁定的输入
            publisher.publish(
                RUN_STARTED,
                {"total_steps": total_steps, "started_at": run.started_at.isoformat()},
            )

            context = EngineContext(
                sink=sink,
                http_client_factory=http_client_factory,
                project_variables=project_vars,
                environment_variables=env_vars,
                publisher=publisher,
                cancellation=cancellation,
                body_max_bytes=settings.body_max_bytes,
            )
            # 引擎协程跑在独立线程的新事件循环里:worker 主线程是同步上下文,
            # eager 模式下则嵌在 API 的事件循环中,asyncio.run 两边都不能直接用
            with ThreadPoolExecutor(max_workers=1) as pool:
                if run.plan_id is not None:
                    summary = pool.submit(
                        asyncio.run,
                        engine_execute_plan(
                            plan_cases,
                            context,
                            stop_on_fail=plan.stop_on_fail,
                            mode=plan.execute_mode,
                            concurrency=plan.concurrency,
                        ),
                    ).result()
                else:
                    summary = pool.submit(asyncio.run, engine_execute(dsl, context)).result()

            # 终态写入前再校验所有权(僵尸 worker 保护)
            session.expire(run)
            run = session.get(Run, run.id)
            if run.worker_id != worker_id:
                return

            run.status = summary.status
            run.finished_at = func.now()
            run.duration_ms = int(summary.duration_ms)
            # skipped 步骤不参与通过率(执行引擎 §3)
            executed = summary.total_steps - summary.skipped
            summary_payload = {
                "total": summary.total_steps,
                "passed": summary.passed,
                "failed": summary.failed,
                "error": summary.errored,
                "skipped": summary.skipped,
                "assertion_total": sink.assertion_total,
                "assertion_passed": sink.assertion_passed,
                "request_duration_ms": int(summary.duration_ms),
                "pass_rate": round(summary.passed / executed, 3) if executed else 0,
            }
            if run.plan_id is not None:
                # 计划执行附用例级结果(含 skipped 用例 —— 它们没有步骤行,
                # 无法从明细聚合得到),字段形状与步骤汇总一致(total/error 同名)
                summary_payload["cases"] = [
                    {
                        "case_index": o.case_index,
                        "case_id": o.case_id,
                        "case_name": o.case_name,
                        "status": o.status,
                        "total": o.total_steps,
                        "passed": o.passed,
                        "failed": o.failed,
                        "error": o.errored,
                        "skipped": o.skipped,
                    }
                    for o in summary.cases
                ]
            # 正常终态(含 stopped)求值门禁;无配置时不写 gate_passed
            run.summary = evaluate_quality_gate(run.quality_gate, summary_payload)
            session.commit()
            # run.finished 由任务层发(与 run.started 成对):漏发会让前端永久挂起
            publisher.publish(RUN_FINISHED, finished_payload(run.status, run.summary))
    except OwnershipLost:
        return
    except Exception:
        # 兜底:引擎意外崩溃(理论上 DSL 深校验后不应发生)也不能把 run 永远留在
        # running —— 报告页会无限轮询。落 error 终态,明细以已落库的为准。
        # 任务本身被吞成成功,堆栈必须留在日志里,否则崩溃无从排查。
        logger.exception("run %s 引擎意外崩溃,落 error 终态", run_id)
        session.rollback()
        run = session.get(Run, uuid.UUID(run_id))
        if run is None or run.worker_id != worker_id or run.status in RUN_TERMINAL_STATUSES:
            return
        run.status = "error"
        run.finished_at = func.now()
        # 异常兜底也是终态:门禁照常求值(不改 state)
        run.summary = evaluate_quality_gate(
            run.quality_gate,
            {
                "total": 0, "passed": 0, "failed": 0, "error": 1, "skipped": 0,
                "assertion_total": 0, "assertion_passed": 0,
            },
        )
        session.commit()
        # 崩溃兜底路径同样要发终态事件:订阅端才知道该收尾了。
        # publisher 尚未构造的窗口(DB 错误 / 版本缺失 / DSL 解析失败)走兜底发布
        if publisher is None:
            _publish_run_finished(run_id, run.status, run.summary, settings)
        else:
            publisher.publish(RUN_FINISHED, finished_payload(run.status, run.summary))
    finally:
        sync_engine.dispose()
