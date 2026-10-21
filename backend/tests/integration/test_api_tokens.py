"""API Token 认证与管理(issue #38)。

主缝:HTTP 集成测试(spec #35 测试缝 #1)。覆盖:Token 管理端点、认证
分派(401/403 边界)、Token 触发 run 的来源语义、SSE 认证同源。
"""

from sqlalchemy import select

from app.core.tokens import hash_token
from app.models import ApiToken


async def _create_token(client, project_id: int, **overrides) -> dict:
    payload = {"name": "ci-pipeline", **overrides}
    resp = await client.post(f"/api/v1/projects/{project_id}/api-tokens", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _new_project(client, name: str = "Token项目") -> int:
    resp = await client.post("/api/v1/projects", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------- Token 管理端点 ----------


async def test_create_returns_plaintext_once_and_hashes_in_db(client):
    project_id = await _new_project(client)
    created = await _create_token(client, project_id)

    # 明文只在这里出现一次,at_ 前缀
    assert created["token"].startswith("at_")
    assert "token_hash" not in created

    # 哈希入库:SHA-256(明文),且库里查不到明文
    from app.main import app

    session_factory = app.state.session_factory
    async with session_factory() as session:
        row = (await session.scalars(select(ApiToken))).one()
        assert row.token_hash == hash_token(created["token"])
        assert row.token_hash != created["token"]

    # 列表不含哈希也不含明文
    listed = await client.get(f"/api/v1/projects/{project_id}/api-tokens")
    assert listed.status_code == 200
    items = listed.json()
    assert len(items) == 1
    assert "token" not in items[0]
    assert "token_hash" not in items[0]
    assert items[0]["name"] == "ci-pipeline"
    # 默认 scopes
    assert items[0]["scopes"] == ["run:trigger", "report:read"]


async def test_create_with_custom_scopes_and_expiry(client):
    project_id = await _new_project(client)
    created = await _create_token(
        client,
        project_id,
        scopes=["run:trigger"],
        expires_at="2030-01-01T00:00:00Z",
    )
    assert created["scopes"] == ["run:trigger"]
    assert created["expires_at"] is not None

    # 非法 scope 422
    bad = await client.post(
        f"/api/v1/projects/{project_id}/api-tokens",
        json={"name": "x", "scopes": ["admin:all"]},
    )
    assert bad.status_code == 422


async def test_revoke_token_soft_deletes_and_disappears_from_list(client):
    project_id = await _new_project(client)
    created = await _create_token(client, project_id)

    resp = await client.delete(f"/api/v1/api-tokens/{created['id']}")
    assert resp.status_code == 204

    listed = await client.get(f"/api/v1/projects/{project_id}/api-tokens")
    assert listed.json() == []

    again = await client.delete(f"/api/v1/api-tokens/{created['id']}")
    assert again.status_code == 404


# ---------- 认证分派(spec 决策 #1/#12)----------


async def test_unknown_token_401_invalid_token(client):
    resp = await client.get(
        "/api/v1/projects",
        headers={"Authorization": "Bearer at_" + "0" * 48},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


async def test_revoked_token_401_immediately(client):
    project_id = await _new_project(client)
    created = await _create_token(client, project_id)
    await client.delete(f"/api/v1/api-tokens/{created['id']}")

    resp = await client.get(
        f"/api/v1/projects/{project_id}/runs",
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


async def test_expired_token_401_invalid_token(client):
    project_id = await _new_project(client)
    created = await _create_token(client, project_id, expires_at="2020-01-01T00:00:00Z")

    resp = await client.get(
        f"/api/v1/projects/{project_id}/runs",
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


async def test_token_on_non_run_endpoint_403_token_not_allowed(client):
    project_id = await _new_project(client)
    created = await _create_token(client, project_id)
    for method, url in [
        ("GET", "/api/v1/projects"),
        ("GET", f"/api/v1/projects/{project_id}/plans"),
        ("GET", f"/api/v1/projects/{project_id}/schedules"),
        ("GET", f"/api/v1/projects/{project_id}/api-tokens"),
    ]:
        resp = await client.request(method, url, headers={"Authorization": f"Bearer {created['token']}"})
        assert resp.status_code == 403, (method, url, resp.text)
        assert resp.json()["error"]["code"] == "token_not_allowed"


async def test_token_updates_last_used_at(client):
    import datetime as dt

    from sqlalchemy import select

    from app.main import app

    project_id = await _new_project(client)
    created = await _create_token(client, project_id)
    resp = await client.get(
        f"/api/v1/projects/{project_id}/runs",
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert resp.status_code == 200
    session_factory = app.state.session_factory
    async with session_factory() as session:
        row = (await session.scalars(select(ApiToken))).one()
        assert row.last_used_at is not None
        assert row.last_used_at > dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)


async def test_no_bearer_header_keeps_seed_user_behavior(client):
    """无 Bearer → 种子用户:既有前端动线不回归。"""
    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 200
    # 非 at_ 的 Bearer 凭据同样回退种子用户(MVP 无浏览器登录)
    resp = await client.get("/api/v1/projects", headers={"Authorization": "Bearer whatever"})
    assert resp.status_code == 200


# ---------- Token 触发 run(spec 决策 #1)----------


async def _setup_plan(client) -> dict:
    """建项目 + 环境 + 用例 + 计划(与 test_plan_runs 同形,返回各 id)。"""
    project = (await client.post("/api/v1/projects", json={"name": "CI触发项目"})).json()
    env = (
        await client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "local", "base_url": "http://unused"},
        )
    ).json()
    case = (
        await client.post(
            f"/api/v1/projects/{project['id']}/cases",
            json={
                "name": "冒烟",
                "dsl": {
                    "dsl_version": 1,
                    "config": {},
                    "steps": [
                        {
                            "id": "s1",
                            "name": "ping",
                            "type": "http",
                            "request": {"method": "GET", "url": "http://unused/ping"},
                            "assert": [{"type": "status_code", "operator": "eq", "expected": 200}],
                        }
                    ],
                },
            },
        )
    ).json()
    plan = (
        await client.post(f"/api/v1/projects/{project['id']}/plans", json={"name": "CI计划"})
    ).json()
    put = await client.put(
        f"/api/v1/plans/{plan['id']}/items",
        json={"items": [{"case_id": case["id"]}]},
    )
    assert put.status_code == 200, put.text
    return {
        "project_id": project["id"],
        "env_id": env["id"],
        "plan_id": plan["id"],
        "case_id": case["id"],
    }


async def test_token_triggered_run_is_ci_with_null_user(client):
    setup = await _setup_plan(client)
    created = await _create_token(client, setup["project_id"])
    resp = await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs",
        json={"plan_id": setup["plan_id"], "environment_id": setup["env_id"]},
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert resp.status_code == 202, resp.text
    run = (await client.get(f"/api/v1/runs/{resp.json()['run_id']}")).json()
    assert run["trigger_mode"] == "ci"
    assert run["triggered_by"] is None


async def test_run_body_cannot_self_report_trigger_mode(client):
    setup = await _setup_plan(client)
    created = await _create_token(client, setup["project_id"])
    resp = await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs",
        json={
            "plan_id": setup["plan_id"],
            "environment_id": setup["env_id"],
            "trigger_mode": "manual",
        },
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert resp.status_code == 202, resp.text
    run = (await client.get(f"/api/v1/runs/{resp.json()['run_id']}")).json()
    # 请求体里的 trigger_mode 不被采纳,服务端判定为 ci
    assert run["trigger_mode"] == "ci"


async def test_no_bearer_trigger_still_manual_with_user(client):
    setup = await _setup_plan(client)
    resp = await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs",
        json={"plan_id": setup["plan_id"], "environment_id": setup["env_id"]},
    )
    assert resp.status_code == 202
    run = (await client.get(f"/api/v1/runs/{resp.json()['run_id']}")).json()
    assert run["trigger_mode"] == "manual"
    assert run["triggered_by"] is not None


async def test_token_cross_project_trigger_and_read_403(client):
    setup_a = await _setup_plan(client)
    created = await _create_token(client, setup_a["project_id"])

    other = (await client.post("/api/v1/projects", json={"name": "别的项目"})).json()
    resp = await client.post(
        f"/api/v1/projects/{other['id']}/runs",
        json={"case_id": 1, "environment_id": 1},
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "token_project_mismatch"

    # 读边界:别的项目里的 run(种子用户触发)对 Token 不可见
    other_run = await client.post(
        f"/api/v1/projects/{other['id']}/runs",
        json={"case_id": 1, "environment_id": 1},
    )
    assert other_run.status_code in (202, 404)  # 环境不存在也行,边界由触发用例覆盖
    listed = await client.get(
        f"/api/v1/projects/{other['id']}/runs",
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert listed.status_code == 403
    assert listed.json()["error"]["code"] == "token_project_mismatch"


async def test_token_scope_boundaries(client):
    setup = await _setup_plan(client)
    read_only = await _create_token(client, setup["project_id"], scopes=["report:read"])
    resp = await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs",
        json={"plan_id": setup["plan_id"], "environment_id": setup["env_id"]},
        headers={"Authorization": f"Bearer {read_only['token']}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "insufficient_scope"

    trigger_only = await _create_token(client, setup["project_id"], scopes=["run:trigger"])
    triggered = await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs",
        json={"plan_id": setup["plan_id"], "environment_id": setup["env_id"]},
        headers={"Authorization": f"Bearer {trigger_only['token']}"},
    )
    assert triggered.status_code == 202
    run_id = triggered.json()["run_id"]
    for url in (f"/api/v1/runs/{run_id}", f"/api/v1/runs/{run_id}/steps"):
        resp = await client.get(url, headers={"Authorization": f"Bearer {trigger_only['token']}"})
        assert resp.status_code == 403, url
        assert resp.json()["error"]["code"] == "insufficient_scope"


# ---------- SSE 认证同源 ----------


async def test_sse_accepts_token_with_report_read(client):
    import json as json_mod

    setup = await _setup_plan(client)
    created = await _create_token(client, setup["project_id"])
    resp = await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs",
        json={"plan_id": setup["plan_id"], "environment_id": setup["env_id"]},
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    run_id = resp.json()["run_id"]
    headers = {"Authorization": f"Bearer {created['token']}"}
    async with client.stream("GET", f"/api/v1/runs/{run_id}/events", headers=headers) as stream:
        assert stream.status_code == 200
        first = None
        # 单迭代器消费行流:重复调用 aiter_lines() 会拿到已消费的流
        event = None
        async for line in stream.aiter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event == "run.snapshot":
                first = json_mod.loads(line.split(":", 1)[1])
                break
        assert first is not None
        assert first["run"]["trigger_mode"] == "ci"


async def test_sse_rejects_invalid_token_401(client):
    setup = await _setup_plan(client)
    triggered = await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs",
        json={"plan_id": setup["plan_id"], "environment_id": setup["env_id"]},
    )
    run_id = triggered.json()["run_id"]
    resp = await client.get(
        f"/api/v1/runs/{run_id}/events",
        headers={"Authorization": "Bearer at_" + "f" * 48},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


async def test_sse_token_without_report_read_403(client):
    setup = await _setup_plan(client)
    created = await _create_token(client, setup["project_id"], scopes=["run:trigger"])
    triggered = await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs",
        json={"plan_id": setup["plan_id"], "environment_id": setup["env_id"]},
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    run_id = triggered.json()["run_id"]
    resp = await client.get(
        f"/api/v1/runs/{run_id}/events",
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "insufficient_scope"


async def test_sse_token_cross_project_run_403(client):
    setup_a = await _setup_plan(client)
    created = await _create_token(client, setup_a["project_id"])
    other = (await client.post("/api/v1/projects", json={"name": "他项目"})).json()
    other_env = (
        await client.post(
            f"/api/v1/projects/{other['id']}/environments",
            json={"name": "e", "base_url": "http://unused"},
        )
    ).json()
    other_case = (
        await client.post(
            f"/api/v1/projects/{other['id']}/cases",
            json={
                "name": "c",
                "dsl": {
                    "dsl_version": 1,
                    "config": {},
                    "steps": [
                        {
                            "id": "s1",
                            "name": "ping",
                            "type": "http",
                            "request": {"method": "GET", "url": "http://unused/ping"},
                            "assert": [{"type": "status_code", "operator": "eq", "expected": 200}],
                        }
                    ],
                },
            },
        )
    ).json()
    other_run = await client.post(
        f"/api/v1/projects/{other['id']}/runs",
        json={"case_id": other_case["id"], "environment_id": other_env["id"]},
    )
    assert other_run.status_code == 202
    resp = await client.get(
        f"/api/v1/runs/{other_run.json()['run_id']}/events",
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "token_project_mismatch"
