"""spec #1 验收场景端到端:建数据(API)→ 触发执行 → 轮询 → 每步完整明细 → success。

与 seed 脚本(backend/scripts/seed.py)灌入的是同一条链路:
① POST 登录,提取 $.data.token;② GET 查询,带 {{token}} 头,断言状态码与 $.code。
"""

import asyncio


def _acceptance_dsl(base_url: str) -> dict:
    return {
        "dsl_version": 1,
        "config": {"variables": {}},
        "steps": [
            {
                "id": "login",
                "name": "登录提取 token",
                "type": "http",
                "request": {
                    "method": "POST",
                    "url": "{{base_url}}/login",
                    "headers": {"Content-Type": "application/json"},
                    "body": {
                        "type": "json",
                        "content": {"username": "{{username}}", "password": "{{password}}"},
                    },
                },
                "extract": [{"name": "token", "type": "jsonpath", "expression": "$.data.token"}],
                "assert": [
                    {"type": "status_code", "operator": "eq", "expected": 200},
                    {"type": "jsonpath", "expression": "$.code", "operator": "eq", "expected": 0},
                ],
            },
            {
                "id": "profile",
                "name": "携带 token 查询",
                "type": "http",
                "request": {
                    "method": "GET",
                    "url": "{{base_url}}/profile",
                    "headers": {"Authorization": "Bearer {{token}}"},
                },
                "assert": [
                    {"type": "status_code", "operator": "eq", "expected": 200},
                    {"type": "jsonpath", "expression": "$.code", "operator": "eq", "expected": 0},
                    {
                        "type": "jsonpath",
                        "expression": "$.data.username",
                        "operator": "eq",
                        "expected": "demo_user",
                    },
                ],
            },
        ],
    }


async def _create_run(client, project_id: int, case_id: int, env_id: int) -> str:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/runs",
        json={"case_id": case_id, "environment_id": env_id},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["run_id"]


async def _poll_run(client, run_id: str, *, timeout_s: float = 10.0) -> dict:
    """轮询到终态(eager 模式首查即终态,轮询是为真实队列路径留同一形状)。"""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        got = await client.get(f"/api/v1/runs/{run_id}")
        body = got.json()
        if body["status"] not in ("pending", "running", "stopping"):
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"run {run_id} 在 {timeout_s}s 内未到终态")


async def test_spec_acceptance_scenario(client, local_server):
    # ---- 建数据:项目 / 环境 / 环境级变量 / 2 步用例 ----
    project = (await client.post("/api/v1/projects", json={"name": "验收项目"})).json()

    env = (
        await client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev", "base_url": local_server},
        )
    ).json()

    vars_resp = await client.put(
        f"/api/v1/environments/{env['id']}/variables",
        json={
            "items": [
                {"name": "base_url", "value": local_server},
                {"name": "username", "value": "demo_user"},
                {"name": "password", "value": "demo_pass", "is_secret": True},
            ]
        },
    )
    assert vars_resp.status_code == 200

    case_resp = await client.post(
        f"/api/v1/projects/{project['id']}/cases",
        json={
            "name": "登录后查询",
            "priority": "P0",
            "dsl": _acceptance_dsl(local_server),
        },
    )
    assert case_resp.status_code == 201, case_resp.text
    case = case_resp.json()

    # ---- 触发执行 → 轮询 ----
    run_id = await _create_run(client, project["id"], case["id"], env["id"])
    run = await _poll_run(client, run_id)

    # ---- run 终态 + 汇总统计 ----
    assert run["status"] == "success"
    summary = run["summary"]
    assert summary["total"] == 2
    assert summary["passed"] == 2
    assert summary["failed"] == 0
    assert summary["pass_rate"] == 1.0
    assert summary["assertion_total"] == 5
    assert summary["assertion_passed"] == 5
    assert run["case_version"] == 1

    # ---- 步骤完整明细 ----
    steps_resp = await client.get(f"/api/v1/runs/{run_id}/steps")
    assert steps_resp.status_code == 200
    steps = steps_resp.json()
    assert steps["count"] == 2
    assert steps["truncated"] is False
    login, profile = steps["items"]

    # 步骤①:请求(method/URL/头/体)+ 提取的变量 + 每条断言
    assert login["step_name"] == "登录提取 token"
    assert login["status"] == "passed"
    assert login["request"]["method"] == "POST"
    assert login["request"]["url"] == f"{local_server}/login"
    assert login["request"]["headers"]["Content-Type"] == "application/json"
    assert "demo_user" in login["request"]["body"]
    assert "demo_pass" in login["request"]["body"]  # 请求体存原文,回显可见
    assert login["extracted_variables"]["token"] == "tok-demo_user-123"
    assert login["assertion_total"] == 2
    assert all(a["passed"] for a in login["assertions"])
    assert login["response"]["status_code"] == 200

    # 步骤②:{{token}} 引用成功 + 断言 $.data.username
    assert profile["status"] == "passed"
    assert profile["request"]["method"] == "GET"
    assert profile["request"]["headers"]["Authorization"] == "Bearer tok-demo_user-123"
    assert profile["assertion_total"] == 3
    assert all(a["passed"] for a in profile["assertions"])
    assert profile["response"]["body"] is not None
    assert '"username": "demo_user"' in profile["response"]["body"]

    # 敏感变量在变量接口层是打码的,但执行层用真值
    env_vars = (await client.get(f"/api/v1/environments/{env['id']}/variables")).json()
    password = next(v for v in env_vars if v["name"] == "password")
    assert password["value"] == "***"
