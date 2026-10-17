"""单步调试端点(前端工作台的「发送」):同步执行 + 快照返回。

真实 PG + 真实本地 HTTP 服务;不走 Celery —— 调试的手感要求一次请求一次返回。
"""

from tests.integration.test_cases import _dsl


def _debug_dsl(url: str) -> dict:
    dsl = _dsl(url=url)
    dsl["steps"] = [
        {
            "id": "s1",
            "name": "取 hello",
            "type": "http",
            "request": {"method": "GET", "url": url},
            "extract": [
                {"name": "hello", "type": "jsonpath", "expression": "$.data.hello"}
            ],
            "assert": [{"type": "status_code", "operator": "eq", "expected": 200}],
        },
        {
            "id": "s2",
            "name": "引用上一步变量",
            "type": "http",
            "request": {"method": "GET", "url": f"{url}?q={{{{hello}}}}"},
        },
    ]
    return dsl


async def _setup(client, dsl: dict) -> tuple[int, int, int]:
    project = (await client.post("/api/v1/projects", json={"name": "调试项目"})).json()
    env = (
        await client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "local", "base_url": "unused"},
        )
    ).json()
    resp = await client.post(
        f"/api/v1/projects/{project['id']}/cases", json={"name": "调试用例", "dsl": dsl}
    )
    assert resp.status_code == 201, resp.text
    return project["id"], env["id"], resp.json()["id"]


async def test_debug_returns_request_response_extract_assert(client, local_server):
    project_id, env_id, case_id = await _setup(client, _debug_dsl(f"{local_server}/json"))

    resp = await client.post(
        f"/api/v1/cases/{case_id}/steps/s1/debug?environment_id={env_id}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["step_id"] == "s1"
    assert body["status"] == "passed"
    assert body["attempts"] == 1
    # 变量替换后的实际请求(base_url 来自环境)
    assert body["request"]["url"] == f"{local_server}/json"
    assert body["request"]["method"] == "GET"
    # 响应快照
    assert body["response"]["status_code"] == 200
    assert "hello" in body["response"]["body"]
    assert body["response"]["truncated"] is False
    assert body["response"]["size"] > 0
    # 提取与断言明细
    assert body["extracted"] == {"hello": "world"}
    assert body["assertions"][0]["passed"] is True
    assert body["assertions"][0]["actual"] == 200
    assert body["duration_ms"] >= 0


async def test_debug_is_isolated_from_other_steps(client, local_server):
    """调试 s2 不先跑 s1:s2 引用的变量不存在 → skipped,不报 500。"""
    project_id, env_id, case_id = await _setup(client, _debug_dsl(f"{local_server}/json"))

    resp = await client.post(
        f"/api/v1/cases/{case_id}/steps/s2/debug?environment_id={env_id}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "skipped"
    assert body["error_message"] is not None
    assert body["response"] is None


async def test_debug_failed_assertion_reported(client, local_server):
    dsl = _debug_dsl(f"{local_server}/json")
    dsl["steps"][0]["assert"] = [{"type": "status_code", "operator": "eq", "expected": 418}]
    project_id, env_id, case_id = await _setup(client, dsl)

    resp = await client.post(
        f"/api/v1/cases/{case_id}/steps/s1/debug?environment_id={env_id}"
    )
    body = resp.json()
    assert body["status"] == "failed"
    assert body["assertions"][0]["passed"] is False
    assert body["assertions"][0]["actual"] == 200


async def test_debug_disabled_step_is_skipped(client, local_server):
    dsl = _debug_dsl(f"{local_server}/json")
    dsl["steps"][0]["enabled"] = False
    project_id, env_id, case_id = await _setup(client, dsl)

    resp = await client.post(
        f"/api/v1/cases/{case_id}/steps/s1/debug?environment_id={env_id}"
    )
    body = resp.json()
    assert body["status"] == "skipped"
    assert "禁用" in body["error_message"]


async def test_debug_step_not_found(client, local_server):
    project_id, env_id, case_id = await _setup(client, _debug_dsl(f"{local_server}/json"))

    resp = await client.post(
        f"/api/v1/cases/{case_id}/steps/nope/debug?environment_id={env_id}"
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "step_not_found"


async def test_debug_case_not_found(client):
    resp = await client.post("/api/v1/cases/999/steps/s1/debug?environment_id=1")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "case_not_found"


async def test_debug_request_query_snapshot_resolved(client, local_server):
    """查询参数单列返回且经过变量解析(引擎把 params 放进 query,不拼进 url)。

    本地服务按精确 path 匹配,带 query 会 404 —— 但 query 快照在发送前已生成,
    恰好验证「快照来自实际构建的请求」而非响应。
    """
    dsl = _debug_dsl(f"{local_server}/json")
    dsl["steps"][0]["request"]["params"] = {"q": "{{name}}", "page": "2"}
    project_id, env_id, case_id = await _setup(client, dsl)

    resp = await client.post(
        f"/api/v1/cases/{case_id}/steps/s1/debug?environment_id={env_id}"
    )
    body = resp.json()
    assert body["request"]["query"] == {"q": "world", "page": "2"}


async def test_debug_engine_exception_becomes_step_error_not_500(client, local_server):
    """坏 JSONPath 表达式等非 TransportError 异常:步骤级 error,而非 500。"""
    dsl = _debug_dsl(f"{local_server}/json")
    dsl["steps"][0]["assert"] = [
        {"type": "jsonpath", "operator": "eq", "expected": 1, "expression": "$.a["}
    ]
    project_id, env_id, case_id = await _setup(client, dsl)

    resp = await client.post(
        f"/api/v1/cases/{case_id}/steps/s1/debug?environment_id={env_id}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["error_message"] is not None


async def test_debug_environment_must_belong_to_case_project(client, local_server):
    project_id, env_id, case_id = await _setup(client, _debug_dsl(f"{local_server}/json"))
    other = (await client.post("/api/v1/projects", json={"name": "别的项目"})).json()
    other_env = (
        await client.post(
            f"/api/v1/projects/{other['id']}/environments", json={"name": "x"}
        )
    ).json()

    resp = await client.post(
        f"/api/v1/cases/{case_id}/steps/s1/debug?environment_id={other_env['id']}"
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "environment_not_found"
