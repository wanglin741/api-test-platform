"""报告查询端点集成测试:run 详情汇总、步骤明细、截断标识、404 语义。"""

import uuid


async def _setup_case_and_run(client, dsl: dict) -> str:
    from tests.integration.test_cases import _dsl as _base_dsl

    project = (await client.post("/api/v1/projects", json={"name": "报告项目"})).json()
    env = (
        await client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        )
    ).json()
    case = (
        await client.post(
            f"/api/v1/projects/{project['id']}/cases",
            json={"name": "报告用例", "dsl": dsl},
        )
    ).json()
    run = (
        await client.post(
            f"/api/v1/projects/{project['id']}/runs",
            json={"case_id": case["id"], "environment_id": env["id"]},
        )
    ).json()
    return run["run_id"]


async def test_run_detail_404(client):
    resp = await client.get(f"/api/v1/runs/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_steps_of_unknown_run_404(client):
    resp = await client.get(f"/api/v1/runs/{uuid.uuid4()}/steps")
    assert resp.status_code == 404


async def test_steps_detail_content(client, local_server):
    from tests.integration.test_cases import _dsl

    dsl = _dsl(url=f"{local_server}/json")
    run_id = await _setup_case_and_run(client, dsl)

    resp = await client.get(f"/api/v1/runs/{run_id}/steps")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["truncated"] is False

    step = body["items"][0]
    assert step["status"] == "passed"
    assert step["duration_ms"] >= 0
    assert step["request"]["method"] == "GET"
    assert step["response"]["status_code"] == 200
    assert step["response"]["truncated"] is False
    assert step["response"]["size"] > 0


async def test_truncated_body_identifiable(client, local_server):
    """/huge 返回 50MB,引擎在 64KB 截断;明细中截断体可识别且不超限。"""
    from tests.integration.test_cases import _dsl

    dsl = _dsl(url=f"{local_server}/huge")
    run_id = await _setup_case_and_run(client, dsl)

    resp = await client.get(f"/api/v1/runs/{run_id}/steps")
    step = resp.json()["items"][0]
    assert step["status"] == "passed"
    assert step["response"]["truncated"] is True
    assert len(step["response"]["body"].encode("utf-8")) <= 64 * 1024


async def test_error_step_has_error_message(client):
    """不可达地址 → error 步骤,错误信息可读,run 终态 error。"""
    from tests.integration.test_cases import _dsl

    run_id = await _setup_case_and_run(client, _dsl(url="http://127.0.0.1:1/x"))

    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["status"] == "error"
    assert run["summary"]["error"] == 1

    steps = (await client.get(f"/api/v1/runs/{run_id}/steps")).json()
    step = steps["items"][0]
    assert step["status"] == "error"
    assert step["error_message"]
    # 网络类错误无响应快照
    assert step["response"] is None


async def test_skipped_step_visible(client, local_server):
    """未定义变量 → 该步骤 skipped(不崩溃、不重试);汇总里 skipped 可见。"""
    from tests.integration.test_cases import _dsl

    dsl = _dsl(url="{{undefined_var}}/x")
    run_id = await _setup_case_and_run(client, dsl)

    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["summary"]["skipped"] == 1

    steps = (await client.get(f"/api/v1/runs/{run_id}/steps")).json()
    step = steps["items"][0]
    assert step["status"] == "skipped"
