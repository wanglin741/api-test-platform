"""质量门禁集成测试(issue #40,spec #35 决策 #9/#10)。

主缝:HTTP 集成测试(真实 PostgreSQL + Redis + eager Celery),断言外部
可观察行为 —— run.status、summary.gate_passed。覆盖:达标/不达标(精确
通过率)、error 阈值、无配置缺省(键不存在)、异常兜底、stopped、pending
取消、定时 run 无门禁、run 状态不被门禁改变、空配置校验。
"""

from tests.integration.test_plan_runs import _trigger
from tests.integration.test_schedule_tick import _force_due, _tick
from tests.integration.test_schedules import _create_schedule


def _step(url: str, expected: int = 200, step_id: str = "s1", name: str = "步骤") -> dict:
    return {
        "id": step_id,
        "name": name,
        "type": "http",
        "request": {"method": "GET", "url": url},
        "assert": [{"type": "status_code", "operator": "eq", "expected": expected}],
    }


def _dsl(steps: list[dict]) -> dict:
    return {"dsl_version": 1, "config": {}, "steps": steps}


async def _setup(client, cases: list[tuple[str, dict]], **plan_overrides) -> dict:
    """项目 + 环境 + 用例 + 计划(清单按 cases 顺序)。"""
    project = (await client.post("/api/v1/projects", json={"name": "门禁项目"})).json()
    env = (
        await client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "local", "base_url": "http://unused"},
        )
    ).json()
    case_ids = []
    for name, dsl in cases:
        case = (
            await client.post(
                f"/api/v1/projects/{project['id']}/cases", json={"name": name, "dsl": dsl}
            )
        ).json()
        case_ids.append(case["id"])
    plan = (
        await client.post(
            f"/api/v1/projects/{project['id']}/plans",
            json={"name": "门禁计划", "environment_id": env["id"], **plan_overrides},
        )
    ).json()
    put = await client.put(
        f"/api/v1/plans/{plan['id']}/items",
        json={"items": [{"case_id": cid} for cid in case_ids]},
    )
    assert put.status_code == 200, put.text
    return {
        "project_id": project["id"],
        "env_id": env["id"],
        "plan_id": plan["id"],
        "case_ids": case_ids,
    }


async def _run(client, run_id: str) -> dict:
    resp = await client.get(f"/api/v1/runs/{run_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _gate_body(setup: dict, gate: dict | None) -> dict:
    body = {"plan_id": setup["plan_id"], "environment_id": setup["env_id"]}
    if gate is not None:
        body["quality_gate"] = gate
    return body


# ---------- 达标 / 不达标 ----------


async def test_gate_passes_without_changing_success_status(client, local_server):
    setup = await _setup(client, [("登录", _dsl([_step(f"{local_server}/json")]))])
    resp = await _trigger(client, setup, quality_gate={"min_pass_rate": 0.9})
    assert resp.status_code == 202, resp.text

    run = await _run(client, resp.json()["run_id"])
    assert run["status"] == "success"
    assert run["summary"]["gate_passed"] is True


async def test_gate_fails_on_low_pass_rate_with_exact_value(client, local_server):
    # 3 步:2 通过 1 失败 → 精确通过率 2/3 ≈ 0.6667 < 0.667。
    # 展示值 round(2/3, 3) = 0.667 会误判通过 —— 门禁必须用精确值。
    steps = [
        _step(f"{local_server}/json", 200, "a", "通过一"),
        _step(f"{local_server}/json", 200, "b", "通过二"),
        _step(f"{local_server}/json", 500, "c", "失败"),
    ]
    setup = await _setup(client, [("混合", _dsl(steps))])
    resp = await _trigger(client, setup, quality_gate={"min_pass_rate": 0.667})
    assert resp.status_code == 202, resp.text

    run = await _run(client, resp.json()["run_id"])
    # 门禁不改变 run 状态:状态由执行结果决定
    assert run["status"] == "failed"
    assert run["summary"]["pass_rate"] == 0.667
    assert run["summary"]["gate_passed"] is False


async def test_max_error_count_threshold(client, local_server):
    # 连接失败 → 步骤级 error(不是 failed)
    setup = await _setup(client, [("基础设施", _dsl([_step("http://127.0.0.1:1/boom")]))])

    strict = await _trigger(client, setup, quality_gate={"max_error_count": 0})
    assert strict.status_code == 202, strict.text
    run = await _run(client, strict.json()["run_id"])
    assert run["status"] == "error"
    assert run["summary"]["error"] == 1
    assert run["summary"]["gate_passed"] is False

    lenient = await _trigger(client, setup, quality_gate={"max_error_count": 1})
    assert lenient.status_code == 202, lenient.text
    run = await _run(client, lenient.json()["run_id"])
    assert run["status"] == "error"
    assert run["summary"]["gate_passed"] is True


async def test_no_gate_omits_gate_passed_key(client, local_server):
    setup = await _setup(client, [("登录", _dsl([_step(f"{local_server}/json")]))])
    resp = await _trigger(client, setup)
    assert resp.status_code == 202, resp.text

    run = await _run(client, resp.json()["run_id"])
    assert run["status"] == "success"
    # 未配置 ≠ 未通过:键必须不存在,而不是 null/false
    assert "gate_passed" not in run["summary"]


# ---------- 校验 ----------


async def test_empty_quality_gate_rejected(client, local_server):
    setup = await _setup(client, [("登录", _dsl([_step(f"{local_server}/json")]))])
    resp = await _trigger(client, setup, quality_gate={})
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "validation_error"


# ---------- 异常兜底、取消/停止 ----------


async def test_fallback_terminal_path_evaluates_gate(client, local_server):
    from app.tasks.celery_app import celery_app
    from app.tasks.engine_tasks import execute_run

    setup = await _setup(client, [("登录", _dsl([_step(f"{local_server}/json")]))])

    # 关 eager 让 trigger 只入队;删掉锁定的用例版本,任务执行时抛错走兜底
    celery_app.conf.task_always_eager = False
    try:
        resp = await _trigger(client, setup, quality_gate={"min_pass_rate": 1.0})
    finally:
        celery_app.conf.task_always_eager = True
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]

    deleted = await client.delete(f"/api/v1/cases/{setup['case_ids'][0]}")
    assert deleted.status_code == 204, deleted.text
    execute_run.apply(args=[run_id])

    run = await _run(client, run_id)
    assert run["status"] == "error"
    assert run["summary"]["error"] == 1
    assert run["summary"]["gate_passed"] is False


async def test_stopped_run_evaluates_gate(client, local_server):
    import redis as redis_py

    from app.core.config import get_settings
    from app.tasks.celery_app import celery_app
    from app.tasks.engine_tasks import execute_run

    setup = await _setup(client, [("登录", _dsl([_step(f"{local_server}/json")]))])

    celery_app.conf.task_always_eager = False
    try:
        resp = await _trigger(client, setup, quality_gate={"min_pass_rate": 0.5})
    finally:
        celery_app.conf.task_always_eager = True
    run_id = resp.json()["run_id"]

    redis_py.Redis.from_url(get_settings().redis_url).set(f"run:cancel:{run_id}", 1, ex=3600)
    execute_run.apply(args=[run_id])

    run = await _run(client, run_id)
    assert run["status"] == "stopped"
    # 全部用例未开始:精确通过率 0 < 0.5;状态仍是 stopped
    assert run["summary"]["gate_passed"] is False


async def test_pending_cancel_evaluates_gate(client, local_server):
    from app.tasks.celery_app import celery_app

    setup = await _setup(client, [("登录", _dsl([_step(f"{local_server}/json")]))])

    celery_app.conf.task_always_eager = False
    try:
        resp = await _trigger(client, setup, quality_gate={"max_error_count": 0})
    finally:
        celery_app.conf.task_always_eager = True
    run_id = resp.json()["run_id"]

    cancel = await client.post(f"/api/v1/runs/{run_id}/cancel")
    assert cancel.status_code == 200, cancel.text

    run = await _run(client, run_id)
    assert run["status"] == "stopped"
    # 排队中被取消没有步骤级 error,error 阈值门禁通过
    assert run["summary"]["gate_passed"] is True


# ---------- 定时 run 无门禁 ----------


async def test_scheduled_run_has_no_gate(client, local_server):
    setup = await _setup(client, [("登录", _dsl([_step(f"{local_server}/json")]))])
    schedule = await _create_schedule(
        client,
        setup["project_id"],
        setup["plan_id"],
        environment_id=setup["env_id"],
    )
    _force_due(schedule["id"])
    assert _tick() == 1

    page = (
        await client.get(f"/api/v1/projects/{setup['project_id']}/runs")
    ).json()
    run = page["items"][0]
    assert run["trigger_mode"] == "schedule"
    assert run["status"] == "success"
    assert "gate_passed" not in run["summary"]
