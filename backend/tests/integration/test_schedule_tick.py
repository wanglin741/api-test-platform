"""tick 派发集成测试(issue #37,spec #35 tick 语义)。

测试缝(spec Testing Decisions #2):tick 任务 `.apply()` 同步调,派发经
schedule_tasks 模块级 execute_run 引用可整体替换 —— 替身只记录 run_id(或
抛异常驱动补偿路径);到期条件用同步连接把 next_run_at 改成过期,不等真实
时间。另有一条端到端:不替换派发,eager 下投递同步执行,run 真跑完。

只断言外部可观察行为:run 表终态、schedule 表的推进/跳过字段。
"""

import datetime
import os

import psycopg
from celery.schedules import crontab

from app.core.schedule_time import compute_next
from app.tasks.schedule_tasks import dispatch_due_schedules
from tests.integration.test_cases import _dsl
from tests.integration.test_plans import _create_environment, _create_plan, _create_project
from tests.integration.test_schedules import _create_schedule


# ---------- 基建:同步连接改到期条件 / 读行 ----------


def _sync_url() -> str:
    return os.environ["APITEST_DATABASE_URL_SYNC"].replace("+psycopg", "")


def _db_exec(sql: str, params: tuple = ()) -> None:
    with psycopg.connect(_sync_url(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)


def _db_one(sql: str, params: tuple = ()) -> tuple:
    with psycopg.connect(_sync_url()) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _force_due(schedule_id: int, *, stale: bool = False) -> None:
    """把 next_run_at 改成过期(不等真实时间);stale 模拟停机多日后的 misfire。"""
    offset = "- interval '3 days'" if stale else "- interval '1 minute'"
    _db_exec(
        f"UPDATE schedule SET next_run_at = now() {offset} WHERE id = %s", (schedule_id,)
    )


def _schedule_row(schedule_id: int) -> dict:
    row = _db_one(
        "SELECT enabled, next_run_at, last_run_at, last_run_id, last_skip_at, "
        "last_skip_reason FROM schedule WHERE id = %s",
        (schedule_id,),
    )
    return {
        "enabled": row[0],
        "next_run_at": row[1],
        "last_run_at": row[2],
        "last_run_id": row[3],
        "last_skip_at": row[4],
        "last_skip_reason": row[5],
    }


def _run_row(run_id: str) -> dict:
    row = _db_one(
        "SELECT status, trigger_mode, schedule_id, triggered_by, environment_id, "
        "plan_id, summary FROM run WHERE id = %s",
        (run_id,),
    )
    return {
        "status": row[0],
        "trigger_mode": row[1],
        "schedule_id": row[2],
        "triggered_by": row[3],
        "environment_id": row[4],
        "plan_id": row[5],
        "summary": row[6],
    }


def _schedule_run_count(schedule_id: int) -> int:
    return _db_one("SELECT count(*) FROM run WHERE schedule_id = %s", (schedule_id,))[0]


class _DispatchStub:
    """派发替身:记录 run_id;给了 error 就抛,驱动投递失败补偿路径。

    tick 在调用点按当前属性取 execute_run,替换 engine_tasks 模块属性即生效。
    """

    def __init__(self, error: Exception | None = None) -> None:
        self.run_ids: list[str] = []
        self._error = error

    def delay(self, run_id: str) -> None:
        if self._error is not None:
            raise self._error
        self.run_ids.append(run_id)


def _patch_dispatch(monkeypatch, stub: _DispatchStub) -> None:
    monkeypatch.setattr("app.tasks.engine_tasks.execute_run", stub)


def _tick() -> int:
    return dispatch_due_schedules.apply().get()


async def _add_plan_item(client, project_id: int, plan_id: int) -> None:
    case = (
        await client.post(
            f"/api/v1/projects/{project_id}/cases", json={"name": "用例", "dsl": _dsl()}
        )
    ).json()
    put = await client.put(
        f"/api/v1/plans/{plan_id}/items", json={"items": [{"case_id": case["id"]}]}
    )
    assert put.status_code == 200, put.text


async def _setup(
    client,
    *,
    plan_environment: bool = True,
    schedule_environment: int | None = None,
    add_item: bool = True,
    cron: str = "* * * * *",
) -> dict:
    """项目 + 环境 + 计划 + 调度(默认每分钟 cron)。"""
    project_id = await _create_project(client)
    env = await _create_environment(client, project_id, "staging")
    plan = await _create_plan(
        client, project_id, "回归计划", environment_id=env["id"] if plan_environment else None
    )
    if add_item:
        await _add_plan_item(client, project_id, plan["id"])
    schedule = await _create_schedule(
        client,
        project_id,
        plan["id"],
        cron_expression=cron,
        environment_id=schedule_environment,
    )
    return {
        "project_id": project_id,
        "env_id": env["id"],
        "plan_id": plan["id"],
        "schedule_id": schedule["id"],
    }


def _insert_pending_run(schedule_id: int, project_id: int, plan_id: int, env_id: int) -> str:
    _db_exec(
        "INSERT INTO run (id, project_id, plan_id, environment_id, trigger_mode, "
        "schedule_id, status) VALUES (gen_random_uuid(), %s, %s, %s, 'schedule', %s, 'pending')",
        (project_id, plan_id, env_id, schedule_id),
    )
    return _db_one(
        "SELECT id FROM run WHERE schedule_id = %s ORDER BY created_at DESC", (schedule_id,)
    )[0]


# ---------- beat 注册 ----------


def test_beat_registers_per_minute_tick():
    from app.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["dispatch-due-schedules"]
    assert entry["task"] == "engine.dispatch_due_schedules"
    assert entry["schedule"] == crontab()  # 每分钟


# ---------- 到期派发 ----------


async def test_due_schedule_dispatches_run(client, monkeypatch):
    setup = await _setup(client)
    stub = _DispatchStub()
    _patch_dispatch(monkeypatch, stub)
    _force_due(setup["schedule_id"])

    dispatched = _tick()

    assert dispatched == 1
    assert len(stub.run_ids) == 1
    run = _run_row(stub.run_ids[0])
    assert run["status"] == "pending"
    assert run["trigger_mode"] == "schedule"
    assert run["schedule_id"] == setup["schedule_id"]
    # 定时 run 不归属个人(spec 决策 #6)
    assert run["triggered_by"] is None
    # 未指定覆盖环境 → 用计划默认环境
    assert run["environment_id"] == setup["env_id"]
    assert run["plan_id"] == setup["plan_id"]

    row = _schedule_row(setup["schedule_id"])
    assert str(row["last_run_id"]) == stub.run_ids[0]
    assert row["last_run_at"] is not None
    assert row["next_run_at"] is not None
    assert row["next_run_at"] > datetime.datetime.now(datetime.UTC)
    assert row["last_skip_reason"] is None


async def test_due_schedule_executes_end_to_end(client, local_server):
    """不替换派发:eager 下投递同步执行,定时 run 真跑完。"""
    from tests.integration.test_plan_runs import _setup_plan

    setup = await _setup_plan(
        client, local_server, [("冒烟", [_step_ok("s1", "探活")])]
    )
    schedule = await _create_schedule(
        client,
        setup["project_id"],
        setup["plan_id"],
        cron_expression="* * * * *",
        environment_id=setup["env_id"],
    )
    _force_due(schedule["id"])

    dispatched = _tick()

    assert dispatched == 1
    row = _schedule_row(schedule["id"])
    run = _run_row(row["last_run_id"])
    assert run["status"] == "success"
    assert run["trigger_mode"] == "schedule"
    assert run["schedule_id"] == schedule["id"]
    assert run["triggered_by"] is None


def _step_ok(step_id: str, name: str) -> dict:
    return {
        "id": step_id,
        "name": name,
        "type": "http",
        "request": {"method": "GET", "url": "PLACEHOLDER"},
        "assert": [{"type": "status_code", "operator": "eq", "expected": 200}],
    }


async def test_not_due_schedule_not_dispatched(client, monkeypatch):
    setup = await _setup(client)
    stub = _DispatchStub()
    _patch_dispatch(monkeypatch, stub)

    assert _tick() == 0
    assert stub.run_ids == []


async def test_disabled_schedule_not_dispatched(client, monkeypatch):
    setup = await _setup(client)
    stub = _DispatchStub()
    _patch_dispatch(monkeypatch, stub)
    # 停用但仍留有「过期」的 next_run_at(直接改库模拟停用瞬间的到期)
    _db_exec(
        "UPDATE schedule SET enabled = false, next_run_at = now() - interval '1 minute' "
        "WHERE id = %s",
        (setup["schedule_id"],),
    )

    assert _tick() == 0
    assert stub.run_ids == []


# ---------- 重叠保护 ----------


async def test_overlap_skips_and_advances(client, monkeypatch):
    setup = await _setup(client)
    _patch_dispatch(monkeypatch, _DispatchStub())
    _insert_pending_run(
        setup["schedule_id"], setup["project_id"], setup["plan_id"], setup["env_id"]
    )
    _force_due(setup["schedule_id"])

    assert _tick() == 0

    row = _schedule_row(setup["schedule_id"])
    assert row["last_skip_reason"] == "overlap"
    assert row["last_skip_at"] is not None
    assert row["enabled"] is True
    assert row["next_run_at"] > datetime.datetime.now(datetime.UTC)
    # 不排队:没有新 run
    assert _schedule_run_count(setup["schedule_id"]) == 1


async def test_two_ticks_do_not_double_dispatch(client, monkeypatch):
    setup = await _setup(client)
    stub = _DispatchStub()
    _patch_dispatch(monkeypatch, stub)
    _force_due(setup["schedule_id"])

    assert _tick() == 1
    # 替身不执行,run 停在 pending;把 next_run_at 再次改成到期,
    # 第二次 tick 命中重叠保护,不重复派发
    _force_due(setup["schedule_id"])
    assert _tick() == 0
    assert len(stub.run_ids) == 1
    assert _schedule_run_count(setup["schedule_id"]) == 1
    assert _schedule_row(setup["schedule_id"])["last_skip_reason"] == "overlap"


# ---------- misfire ----------


async def test_misfire_computes_next_from_now_not_catch_up(client, monkeypatch):
    setup = await _setup(client, cron="0 2 * * *")
    stub = _DispatchStub()
    _patch_dispatch(monkeypatch, stub)
    _force_due(setup["schedule_id"], stale=True)  # 停机 3 天,错过多个时点

    assert _tick() == 1  # 只派发一次,不补跑

    expected = compute_next(
        "0 2 * * *", "Asia/Shanghai", datetime.datetime.now(datetime.UTC)
    )
    row = _schedule_row(setup["schedule_id"])
    delta = abs((row["next_run_at"] - expected).total_seconds())
    assert delta < 60  # 下一个凌晨 2 点,不是 3 天前的错过的时点序列


# ---------- 环境语义 ----------


async def test_schedule_environment_overrides_plan_default(client, monkeypatch):
    setup = await _setup(client)
    other_env = await _create_environment(client, setup["project_id"], "prod")
    await client.patch(
        f"/api/v1/schedules/{setup['schedule_id']}", json={"environment_id": other_env["id"]}
    )
    stub = _DispatchStub()
    _patch_dispatch(monkeypatch, stub)
    _force_due(setup["schedule_id"])

    assert _tick() == 1
    assert _run_row(stub.run_ids[0])["environment_id"] == other_env["id"]


async def test_missing_environment_skips_but_stays_enabled(client, monkeypatch):
    # 计划无默认环境、调度也未覆盖 → environment_required
    setup = await _setup(client, plan_environment=False, add_item=True)
    _patch_dispatch(monkeypatch, _DispatchStub())
    _force_due(setup["schedule_id"])

    assert _tick() == 0

    row = _schedule_row(setup["schedule_id"])
    assert row["last_skip_reason"] == "environment_missing"
    assert row["enabled"] is True
    assert row["next_run_at"] > datetime.datetime.now(datetime.UTC)
    assert _schedule_run_count(setup["schedule_id"]) == 0


async def test_deleted_environment_skips_but_stays_enabled(client, monkeypatch):
    setup = await _setup(client)
    resp = await client.delete(f"/api/v1/environments/{setup['env_id']}")
    assert resp.status_code == 204
    _patch_dispatch(monkeypatch, _DispatchStub())
    _force_due(setup["schedule_id"])

    assert _tick() == 0
    row = _schedule_row(setup["schedule_id"])
    assert row["last_skip_reason"] == "environment_missing"
    assert row["enabled"] is True


# ---------- 计划失效语义 ----------


async def test_plan_deleted_disables_schedule(client, monkeypatch):
    setup = await _setup(client)
    resp = await client.delete(f"/api/v1/plans/{setup['plan_id']}")
    assert resp.status_code == 204
    _patch_dispatch(monkeypatch, _DispatchStub())
    _force_due(setup["schedule_id"])  # 删计划不改调度,到期后由 tick 判定

    assert _tick() == 0

    row = _schedule_row(setup["schedule_id"])
    assert row["enabled"] is False
    assert row["next_run_at"] is None
    assert row["last_skip_reason"] == "plan_deleted"
    assert _schedule_run_count(setup["schedule_id"]) == 0


async def test_empty_plan_skips_and_advances(client, monkeypatch):
    setup = await _setup(client, add_item=False)
    _patch_dispatch(monkeypatch, _DispatchStub())
    _force_due(setup["schedule_id"])

    assert _tick() == 0

    row = _schedule_row(setup["schedule_id"])
    assert row["last_skip_reason"] == "plan_empty"
    assert row["enabled"] is True
    assert row["next_run_at"] > datetime.datetime.now(datetime.UTC)


# ---------- 投递失败补偿 ----------


async def test_dispatch_failure_marks_run_error(client, monkeypatch):
    setup = await _setup(client)
    stub = _DispatchStub(error=RuntimeError("broker down"))
    _patch_dispatch(monkeypatch, stub)
    _force_due(setup["schedule_id"])

    assert _tick() == 1  # run 已创建

    row = _schedule_row(setup["schedule_id"])
    run = _run_row(row["last_run_id"])
    assert run["status"] == "error"
    assert run["summary"]["dispatch_failed"] is True
    # 推进不受影响:下一次照常
    assert row["next_run_at"] > datetime.datetime.now(datetime.UTC)
