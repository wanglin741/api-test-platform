"""计划执行端到端(#30):真实 PG + Redis + eager Celery。

覆盖:串行端到端(明细带用例归属、summary 求和)、触发校验、取消到终态、
SSE 快照/重放带用例维度;单用例执行的既有行为不变(由既有用例钉住)。
"""

import json


def _dsl(steps: list[dict], base_url: str) -> dict:
    return {
        "dsl_version": 1,
        "config": {},
        "steps": [{**s, "request": {**s["request"], "url": f"{base_url}/json"}} for s in steps],
    }


def _step(step_id: str, name: str, expected: int = 200) -> dict:
    return {
        "id": step_id,
        "name": name,
        "type": "http",
        "request": {"method": "GET", "url": "PLACEHOLDER"},
        "assert": [{"type": "status_code", "operator": "eq", "expected": expected}],
    }


async def _setup_plan(
    client, base_url: str, specs: list[tuple[str, list[dict]]], **plan_overrides
) -> dict:
    """建项目 + 环境 + 用例 + 计划(清单按 specs 顺序),返回含各 id 的 dict。"""
    project = (await client.post("/api/v1/projects", json={"name": "计划执行项目"})).json()
    env = (
        await client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "local", "base_url": "http://unused"},
        )
    ).json()
    case_ids = []
    for name, steps in specs:
        case = (
            await client.post(
                f"/api/v1/projects/{project['id']}/cases",
                json={"name": name, "dsl": _dsl(steps, base_url)},
            )
        ).json()
        case_ids.append(case["id"])
    plan = (
        await client.post(
            f"/api/v1/projects/{project['id']}/plans",
            json={"name": "回归计划", **plan_overrides},
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


async def _trigger(client, setup: dict, **overrides) -> object:
    payload = {"plan_id": setup["plan_id"], "environment_id": setup["env_id"], **overrides}
    return await client.post(f"/api/v1/projects/{setup['project_id']}/runs", json=payload)


# ---------- 串行端到端 ----------


async def test_serial_plan_run_end_to_end(client, local_server):
    setup = await _setup_plan(
        client,
        local_server,
        [("登录", [_step("a1", "打开登录"), _step("a2", "提交")]), ("下单", [_step("b1", "下单")])],
    )
    resp = await _trigger(client, setup)
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]

    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["status"] == "success"
    assert run["plan_id"] == setup["plan_id"]
    assert run["case_id"] is None and run["case_version"] is None

    # 计划级 summary = 用例级求和(同一份字段形状)+ 用例归属明细
    summary = run["summary"]
    assert summary["total"] == 3 and summary["passed"] == 3 and summary["failed"] == 0
    assert summary["assertion_total"] == 3
    assert [(c["case_index"], c["case_name"], c["status"]) for c in summary["cases"]] == [
        (0, "登录", "success"),
        (1, "下单", "success"),
    ]
    assert [c["total"] for c in summary["cases"]] == [2, 1]

    # 步骤明细带用例归属,按 (case_index, step_index) 排序
    steps = (await client.get(f"/api/v1/runs/{run_id}/steps")).json()["items"]
    assert [(s["case_index"], s["case_id"], s["step_index"]) for s in steps] == [
        (0, setup["case_ids"][0], 0),
        (0, setup["case_ids"][0], 1),
        (1, setup["case_ids"][1], 0),
    ]


async def test_failed_case_lands_plan_run_failed_with_skip(client, local_server):
    setup = await _setup_plan(
        client,
        local_server,
        [("登录", [_step("a1", "打开登录", expected=500)]), ("下单", [_step("b1", "下单")])],
        stop_on_fail=True,
    )
    resp = await _trigger(client, setup)
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]

    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["status"] == "failed"
    summary = run["summary"]
    assert summary["total"] == 1 and summary["failed"] == 1
    assert [(c["case_index"], c["status"]) for c in summary["cases"]] == [
        (0, "failed"),
        (1, "skipped"),
    ]
    # 被跳用例没有步骤行(不发步骤事件,也无归属行)
    steps = (await client.get(f"/api/v1/runs/{run_id}/steps")).json()["items"]
    assert [s["case_index"] for s in steps] == [0]


# ---------- 触发校验 ----------


async def test_trigger_requires_exactly_one_target(client, local_server):
    setup = await _setup_plan(client, local_server, [("登录", [_step("a1", "打开")])])

    # 两个都给 → 422
    resp = await _trigger(client, setup, case_id=setup["case_ids"][0])
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "run_target_invalid"

    # 都不给 → 422
    resp = await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs",
        json={"environment_id": setup["env_id"]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "run_target_invalid"


async def test_trigger_plan_validates_plan_and_items(client, local_server):
    setup = await _setup_plan(client, local_server, [("登录", [_step("a1", "打开")])])

    # 未知计划 404
    resp = await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs",
        json={"plan_id": 99999, "environment_id": setup["env_id"]},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "plan_not_found"

    # 并行计划本工单未支持 → 422(#32 接线)
    parallel = await _setup_plan(
        client,
        local_server,
        [("下单", [_step("b1", "下单")])],
        execute_mode="parallel",
        concurrency=2,
    )
    resp = await _trigger(client, parallel)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "plan_mode_unsupported"

    # 计划无默认环境且请求未带 → 422
    plan = (
        await client.post(
            f"/api/v1/projects/{setup['project_id']}/plans", json={"name": "无环境计划"}
        )
    ).json()
    await client.put(
        f"/api/v1/plans/{plan['id']}/items", json={"items": [{"case_id": setup["case_ids"][0]}]}
    )
    resp = await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs", json={"plan_id": plan["id"]}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "environment_required"


async def test_trigger_plan_uses_plan_default_environment(client, local_server):
    setup = await _setup_plan(
        client, local_server, [("登录", [_step("a1", "打开")])], environment_id=None
    )
    # 给计划配默认环境后再触发,请求体不带 environment_id
    await client.patch(f"/api/v1/plans/{setup['plan_id']}", json={"environment_id": setup["env_id"]})
    resp = await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs", json={"plan_id": setup["plan_id"]}
    )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["environment_id"] == setup["env_id"]
    assert run["status"] == "success"


# ---------- 取消到终态 ----------


async def test_cancel_flag_skips_remaining_cases_to_stopped(client, local_server):
    from app.tasks.celery_app import celery_app
    from app.tasks.engine_tasks import execute_run

    setup = await _setup_plan(
        client,
        local_server,
        [("登录", [_step("a1", "打开登录")]), ("下单", [_step("b1", "下单")])],
    )

    # 关 eager 让 trigger 只入队不执行,好把取消标志插在执行前
    celery_app.conf.task_always_eager = False
    try:
        resp = await _trigger(client, setup)
    finally:
        celery_app.conf.task_always_eager = True
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]

    import redis as redis_py

    from app.core.config import get_settings

    # 执行前插入带外取消标志(等价 cancel API 在 running 态写入的标志;
    # pending 态走 cancel API 会直接落 stopped 终态,覆盖不到任务路径)
    redis_py.Redis.from_url(get_settings().redis_url).set(f"run:cancel:{run_id}", 1, ex=3600)

    # 同步执行任务:引擎全程看到取消标志 → 全部用例跳过,落 stopped
    execute_run.apply(args=[run_id])

    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["status"] == "stopped"
    assert [(c["case_index"], c["status"]) for c in run["summary"]["cases"]] == [
        (0, "skipped"),
        (1, "skipped"),
    ]
    steps = (await client.get(f"/api/v1/runs/{run_id}/steps")).json()
    assert steps["count"] == 0


# ---------- SSE 事件流带用例维度 ----------


async def _read_sse(client, run_id: str, **kwargs) -> list[dict]:
    """消费 SSE 到连接自然关闭,返回事件列表。"""
    events: list[dict] = []
    current: dict = {}
    async with client.stream("GET", f"/api/v1/runs/{run_id}/events", **kwargs) as resp:
        async for line in resp.aiter_lines():
            if line == "":
                if current:
                    events.append(current)
                    current = {}
                continue
            if line.startswith("event:"):
                current["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                current["data"] = line.split(":", 1)[1].strip()
            elif line.startswith("id:"):
                current["id"] = line.split(":", 1)[1].strip()
    if current:
        events.append(current)
    return events


async def test_sse_snapshot_and_replay_carry_case_dimensions(client, local_server):
    setup = await _setup_plan(
        client,
        local_server,
        [("登录", [_step("a1", "打开登录")]), ("下单", [_step("b1", "下单")])],
    )
    resp = await _trigger(client, setup)
    run_id = resp.json()["run_id"]

    # 快照路径:steps 带用例归属
    events = await _read_sse(client, run_id)
    assert events[0]["event"] == "run.snapshot"
    snapshot = json.loads(events[0]["data"])
    assert [(s["case_index"], s["case_id"]) for s in snapshot["steps"]] == [
        (0, setup["case_ids"][0]),
        (1, setup["case_ids"][1]),
    ]
    assert events[-1]["event"] == "run.finished"
    assert json.loads(events[-1]["data"])["summary"]["cases"][1]["case_name"] == "下单"


async def test_sse_step_events_carry_case_fields(client, local_server):
    from app.run_events import stream_key

    setup = await _setup_plan(
        client,
        local_server,
        [("登录", [_step("a1", "打开登录")]), ("下单", [_step("b1", "下单")])],
    )
    resp = await _trigger(client, setup)
    run_id = resp.json()["run_id"]

    import redis as redis_py

    from app.core.config import get_settings

    entries = redis_py.Redis.from_url(get_settings().redis_url).xrange(stream_key(run_id), count=100)
    payloads = [json.loads(fields[b"data"]) for _, fields in entries]
    # step.finished 载荷带 status;与 run.finished(无 case_index)、快照区分
    step_finished = [p for p in payloads if "status" in p and "case_index" in p]
    assert len(step_finished) == 2
    assert step_finished[0]["case_index"] == 0 and step_finished[0]["case_name"] == "登录"
    assert step_finished[1]["case_index"] == 1 and step_finished[1]["case_name"] == "下单"
    # 进度连续计数
    progresses = [p for p in payloads if "completed" in p]
    assert [p["completed"] for p in progresses] == [1, 2]
    assert all(p["total"] == 2 for p in progresses)
