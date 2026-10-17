"""执行任务:触发到落库的胶水层(执行引擎 §13)。

职责仅四步:同步会话加载配置 → 组装 EngineContext → 驱动引擎 → 汇总写终态。
任何业务规则(变量、断言、取消时机)都在引擎里。

worker 全程同步 Session + psycopg(D6):AsyncClient 只在协程内部创建,
引擎协程跑在独立线程的新事件循环里,不跨事件循环复用任何异步对象。
"""

import asyncio
import hashlib
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import httpx
import redis as redis_py
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import make_sync_engine, make_sync_session_factory
from app.engine import EngineContext, execute as engine_execute
from app.engine.dsl import parse_dsl
from app.engine.results import StepResult, response_size
from app.models import RUN_TERMINAL_STATUSES, Environment, Run, RunStepResult, TestCaseVersion, Variable
from app.tasks.celery_app import celery_app


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
    """每步结束立即写 run_step_result 并提交 —— 执行中查询即可看到已完成步骤。"""

    def __init__(self, session: Session, run_id: uuid.UUID, worker_id: str, step_types: dict[int, str]) -> None:
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
            step_type=self._step_types.get(result.step_index, "http"),
            status=result.status,
            duration_ms=int(result.duration_ms or 0),
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
    project_vars: dict = {}
    env_vars: dict = {}
    rows = session.execute(
        select(Variable).where(Variable.project_id == run.project_id)
    ).scalars()
    for var in rows:
        if var.environment_id is None:
            project_vars[var.name] = var.value
        elif var.environment_id == run.environment_id:
            env_vars[var.name] = var.value
    environment = session.get(Environment, run.environment_id)
    if environment is not None and environment.base_url:
        env_vars.setdefault("base_url", environment.base_url)
    return project_vars, env_vars


@celery_app.task(name="engine.execute_run", bind=True)
def execute_run(self, run_id: str) -> None:
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{uuid.uuid4()}"
    sync_engine = make_sync_engine(settings.database_url_sync)
    try:
        with make_sync_session_factory(sync_engine)() as session:
            run = session.get(Run, uuid.UUID(run_id))
            # 幂等检查:acks_late + reject_on_worker_lost 下任务会被重投
            if run is None or run.status in RUN_TERMINAL_STATUSES:
                return

            run.status = "running"
            run.worker_id = worker_id
            run.started_at = func.now()  # 时间戳一律 DB now(),不信任应用时钟
            run.celery_task_id = self.request.id
            session.commit()
            session.refresh(run)  # 取回 DB 生成的时间戳

            version = session.execute(
                select(TestCaseVersion).where(
                    TestCaseVersion.test_case_id == run.case_id,
                    TestCaseVersion.version == run.case_version,
                )
            ).scalar_one()
            dsl = parse_dsl(version.dsl)
            project_vars, env_vars = _load_variables(session, run)

            redis_client = redis_py.Redis.from_url(settings.redis_url)
            cancellation = RedisCancellationToken(redis_client, f"run:cancel:{run_id}")
            sink = DbSink(
                session, run.id, worker_id,
                {i: step.type for i, step in enumerate(dsl.steps)},
            )

            def http_client_factory(verify: bool = True) -> httpx.AsyncClient:
                # 在引擎的事件循环内被调用,AsyncClient 不跨循环复用
                return httpx.AsyncClient(follow_redirects=True, verify=verify)

            context = EngineContext(
                sink=sink,
                http_client_factory=http_client_factory,
                project_variables=project_vars,
                environment_variables=env_vars,
                cancellation=cancellation,
                body_max_bytes=settings.body_max_bytes,
            )
            # 引擎协程跑在独立线程的新事件循环里:worker 主线程是同步上下文,
            # eager 模式下则嵌在 API 的事件循环中,asyncio.run 两边都不能直接用
            with ThreadPoolExecutor(max_workers=1) as pool:
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
            run.summary = {
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
            session.commit()
    except OwnershipLost:
        return
    finally:
        sync_engine.dispose()
