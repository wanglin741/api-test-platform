"""执行链路应用边界测试:ASGI 客户端 + 进程内 eager 任务 + 真实 PG + 真实本地 HTTP 服务。

覆盖 spec 的管道:触发(202 + run_id)→ Celery eager 执行 → 引擎 → 逐步落库
→ 状态/汇总查询。取消路径一并在此验证(共用同一套基础设施)。
"""

import uuid


async def _setup(client, url: str = "http://127.0.0.1:1/unreachable") -> tuple[int, int, int]:
    """建项目 + 环境 + 用例(单步 http),返回 (project_id, env_id, case_id)。"""
    from tests.integration.test_cases import _dsl

    project = (await client.post("/api/v1/projects", json={"name": "执行项目"})).json()
    env = (
        await client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "local", "base_url": "unused"},
        )
    ).json()
    resp = await client.post(
        f"/api/v1/projects/{project['id']}/cases",
        json={"name": "两步用例", "dsl": _dsl(url=url)},
    )
    assert resp.status_code == 201, resp.text
    return project["id"], env["id"], resp.json()["id"]


async def test_trigger_execute_and_query(client, local_server):
    project_id, env_id, case_id = await _setup(client, url=f"{local_server}/json")

    resp = await client.post(
        f"/api/v1/projects/{project_id}/runs",
        json={"case_id": case_id, "environment_id": env_id},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    run_id = body["run_id"]
    assert body["status"] == "pending"
    assert resp.headers["Location"] == f"/api/v1/runs/{run_id}"

    # eager 模式下任务已同步跑完:状态 + 汇总
    got = await client.get(f"/api/v1/runs/{run_id}")
    assert got.status_code == 200
    run = got.json()
    assert run["status"] == "success"
    assert run["summary"]["total"] == 1
    assert run["summary"]["passed"] == 1
    assert run["summary"]["pass_rate"] == 1.0
    assert run["trigger_mode"] == "manual"
    assert run["started_at"] is not None
    assert run["finished_at"] is not None


async def test_failed_assertion_maps_to_failed_run(client, local_server):
    from tests.integration.test_cases import _dsl

    project_id, env_id, case_id = await _setup(client, url=f"{local_server}/json")
    # 追加一条必败断言
    dsl = _dsl(url=f"{local_server}/json")
    dsl["steps"][0]["assert"] = [
        {"type": "status_code", "operator": "eq", "expected": 418}
    ]
    resp = await client.patch(f"/api/v1/cases/{case_id}", json={"dsl": dsl})
    assert resp.status_code == 200

    triggered = (
        await client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"case_id": case_id, "environment_id": env_id},
        )
    ).json()

    got = await client.get(f"/api/v1/runs/{triggered['run_id']}")
    assert got.json()["status"] == "failed"
    assert got.json()["summary"]["failed"] == 1

    # 锁定版本:run 记录的是触发时的 case_version
    assert got.json()["case_version"] == 2


async def test_trigger_validates_ownership(client, local_server):
    project_id, env_id, case_id = await _setup(client)
    other_project = (await client.post("/api/v1/projects", json={"name": "别的项目"})).json()

    resp = await client.post(
        f"/api/v1/projects/{other_project['id']}/runs",
        json={"case_id": case_id, "environment_id": env_id},
    )
    assert resp.status_code == 404
    # 归属不符按「不存在」返回,code 资源化且不区分「不存在/不属于」
    assert resp.json()["error"]["code"] == "case_not_found"


async def test_cancel_finished_run_conflict(client, local_server):
    project_id, env_id, case_id = await _setup(client, url=f"{local_server}/json")
    run = (
        await client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"case_id": case_id, "environment_id": env_id},
        )
    ).json()

    resp = await client.post(f"/api/v1/runs/{run['run_id']}/cancel")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "run_already_finished"


async def test_cancel_unknown_run_404(client):
    resp = await client.post(f"/api/v1/runs/{uuid.uuid4()}/cancel")
    assert resp.status_code == 404


async def test_step_results_persisted(client, local_server):
    """步骤结果落库:请求/响应快照、断言明细、提取变量。"""
    from tests.integration.test_cases import _dsl

    project_id, env_id, case_id = await _setup(client)
    dsl = _dsl(url=f"{local_server}/json")
    dsl["steps"][0]["extract"] = [
        {"name": "hello", "type": "jsonpath", "expression": "$.data.hello"}
    ]
    dsl["steps"][0]["assert"] = [
        {"type": "status_code", "operator": "eq", "expected": 200},
        {"type": "jsonpath", "expression": "$.data.hello", "operator": "eq", "expected": "world"},
    ]
    await client.patch(f"/api/v1/cases/{case_id}", json={"dsl": dsl})

    run = (
        await client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"case_id": case_id, "environment_id": env_id},
        )
    ).json()

    got = await client.get(f"/api/v1/runs/{run['run_id']}")
    assert got.json()["status"] == "success"

    # 步骤明细直查 DB(报告端点属 T9);这里验证落库契约
    import os

    import psycopg

    sync_url = os.environ["APITEST_DATABASE_URL_SYNC"].replace("+psycopg", "")
    with psycopg.connect(sync_url) as conn:
        row = conn.execute(
            "SELECT status, step_type, request_method, request_url, response_status, "
            "response_body, response_body_truncated, extracted_variables, "
            "assertion_total, assertion_passed "
            "FROM run_step_result ORDER BY step_index"
        ).fetchone()
    assert row is not None
    status, step_type, method, url, resp_status, resp_body, truncated, extracted, a_total, a_passed = row
    assert status == "passed"
    assert step_type == "http"
    assert method == "GET"
    assert url == f"{local_server}/json"
    assert resp_status == 200
    assert '"hello": "world"' in bytes(resp_body).decode("utf-8")
    assert truncated is False
    assert extracted == {"hello": "world"}
    assert (a_total, a_passed) == (2, 2)


async def test_worker_ownership_and_idempotency(client, local_server):
    """重复投递同一 run:终态后任务直接返回,不产生第二条步骤结果。"""
    from app.tasks.engine_tasks import execute_run

    project_id, env_id, case_id = await _setup(client, url=f"{local_server}/json")
    run = (
        await client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"case_id": case_id, "environment_id": env_id},
        )
    ).json()

    task = execute_run.run(str(uuid.UUID(run["run_id"])))  # 再次投递(eager 直接执行)
    assert task is None  # 幂等检查命中,直接返回

    got = await client.get(f"/api/v1/runs/{run['run_id']}")
    assert got.json()["status"] == "success"
