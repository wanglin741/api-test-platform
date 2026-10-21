"""Idempotency-Key 幂等触发(issue #39,spec #35 决策 #7)。

主缝:HTTP 集成测试(真实 PostgreSQL + Redis + eager Celery)。覆盖:
重放同 run、body 变化 409、跨项目/凭据隔离、并发只建一条、业务失败后
同 key 可重试、Redis 不可用 503、TTL 量级、无 key 行为不变。
"""

import asyncio
import os

import redis as redis_py

IDEM_HEADER = "Idempotency-Key"


async def _setup_plan(client, *, default_env: bool = True) -> dict:
    """建项目 + 环境 + 用例 + 计划(与 test_api_tokens 同形)。"""
    project = (await client.post("/api/v1/projects", json={"name": "幂等项目"})).json()
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
    plan_payload = {"name": "幂等计划"}
    if default_env:
        plan_payload["environment_id"] = env["id"]
    plan = (
        await client.post(
            f"/api/v1/projects/{project['id']}/plans", json=plan_payload
        )
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


async def _trigger(client, setup: dict, key: str | None = None, **overrides):
    body = {"plan_id": setup["plan_id"], "environment_id": setup["env_id"], **overrides}
    headers = {IDEM_HEADER: key} if key is not None else {}
    return await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs", json=body, headers=headers
    )


async def _run_count(client, project_id: int) -> int:
    resp = await client.get(f"/api/v1/projects/{project_id}/runs?limit=100")
    assert resp.status_code == 200, resp.text
    return len(resp.json()["items"])


# ---------- 重放与无 key 行为 ----------


async def test_replay_returns_first_run_with_header(client):
    setup = await _setup_plan(client)
    first = await _trigger(client, setup, key="ci-build-1")
    assert first.status_code == 202, first.text
    assert first.headers.get("Idempotent-Replayed") is None

    second = await _trigger(client, setup, key="ci-build-1")
    assert second.status_code == 202, second.text
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert second.json()["run_id"] == first.json()["run_id"]
    assert await _run_count(client, setup["project_id"]) == 1


async def test_without_key_keeps_existing_behavior(client):
    setup = await _setup_plan(client)
    first = await _trigger(client, setup)
    second = await _trigger(client, setup)
    assert first.status_code == second.status_code == 202
    assert first.json()["run_id"] != second.json()["run_id"]
    assert second.headers.get("Idempotent-Replayed") is None
    assert await _run_count(client, setup["project_id"]) == 2


# ---------- fingerprint 与隔离 ----------


async def test_same_key_different_body_conflicts(client):
    setup = await _setup_plan(client)
    other_env = (
        await client.post(
            f"/api/v1/projects/{setup['project_id']}/environments",
            json={"name": "other", "base_url": "http://unused"},
        )
    ).json()
    first = await _trigger(client, setup, key="ci-build-2")
    assert first.status_code == 202, first.text

    changed = await _trigger(client, setup, key="ci-build-2", environment_id=other_env["id"])
    assert changed.status_code == 409, changed.text
    assert changed.json()["error"]["code"] == "idempotency_key_reused"
    assert await _run_count(client, setup["project_id"]) == 1


async def test_same_key_different_quality_gate_conflicts(client):
    """门禁参数进入 fingerprint:同 key 换门禁必须 409,相同则重放(#40)。"""
    setup = await _setup_plan(client)
    first = await _trigger(client, setup, key="ci-build-gate", quality_gate={"min_pass_rate": 0.9})
    assert first.status_code == 202, first.text

    changed = await _trigger(
        client, setup, key="ci-build-gate", quality_gate={"min_pass_rate": 0.95}
    )
    assert changed.status_code == 409, changed.text
    assert changed.json()["error"]["code"] == "idempotency_key_reused"

    same = await _trigger(client, setup, key="ci-build-gate", quality_gate={"min_pass_rate": 0.9})
    assert same.status_code == 202, same.text
    assert same.headers.get("Idempotent-Replayed") == "true"
    assert same.json()["run_id"] == first.json()["run_id"]
    assert await _run_count(client, setup["project_id"]) == 1


async def test_same_key_isolated_across_projects(client):
    setup_a = await _setup_plan(client)
    setup_b = await _setup_plan(client)
    first = await _trigger(client, setup_a, key="shared-build")
    second = await _trigger(client, setup_b, key="shared-build")
    assert first.status_code == second.status_code == 202
    # 项目不同 ⇒ 互不重放,各自建一条
    assert first.json()["run_id"] != second.json()["run_id"]
    assert await _run_count(client, setup_a["project_id"]) == 1
    assert await _run_count(client, setup_b["project_id"]) == 1


async def test_same_key_isolated_across_credentials(client):
    setup = await _setup_plan(client)
    tokens = []
    for name in ("pipeline-a", "pipeline-b"):
        created = await client.post(
            f"/api/v1/projects/{setup['project_id']}/api-tokens", json={"name": name}
        )
        assert created.status_code == 201, created.text
        tokens.append(created.json()["token"])

    body = {"plan_id": setup["plan_id"], "environment_id": setup["env_id"]}
    first = await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs",
        json=body,
        headers={IDEM_HEADER: "shared-build", "Authorization": f"Bearer {tokens[0]}"},
    )
    second = await client.post(
        f"/api/v1/projects/{setup['project_id']}/runs",
        json=body,
        headers={IDEM_HEADER: "shared-build", "Authorization": f"Bearer {tokens[1]}"},
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["run_id"] != second.json()["run_id"]
    assert await _run_count(client, setup["project_id"]) == 2


# ---------- 并发、失败重试、不可用 ----------


async def test_concurrent_same_key_creates_one_run(client):
    setup = await _setup_plan(client)
    body = {"plan_id": setup["plan_id"], "environment_id": setup["env_id"]}
    url = f"/api/v1/projects/{setup['project_id']}/runs"
    responses = await asyncio.gather(
        client.post(url, json=body, headers={IDEM_HEADER: "ci-build-concurrent"}),
        client.post(url, json=body, headers={IDEM_HEADER: "ci-build-concurrent"}),
    )
    statuses = sorted(r.status_code for r in responses)
    assert statuses in ([202, 202], [202, 409]), [r.text for r in responses]
    if statuses == [202, 202]:
        assert responses[0].json()["run_id"] == responses[1].json()["run_id"]
    else:
        conflict = next(r for r in responses if r.status_code == 409)
        assert conflict.json()["error"]["code"] == "idempotency_in_progress"
        assert conflict.headers.get("Retry-After") is not None
    assert await _run_count(client, setup["project_id"]) == 1


async def test_business_failure_does_not_consume_key(client):
    setup = await _setup_plan(client, default_env=False)
    # 计划没有默认环境,且请求不带环境 ⇒ 业务校验 422
    failed = await _trigger(client, setup, key="ci-build-retry", environment_id=None)
    assert failed.status_code == 422, failed.text
    assert failed.json()["error"]["code"] == "environment_required"

    # 修正后同一 key 可重试,并正常建 run
    retried = await _trigger(client, setup, key="ci-build-retry")
    assert retried.status_code == 202, retried.text
    assert retried.headers.get("Idempotent-Replayed") is None
    assert await _run_count(client, setup["project_id"]) == 1


async def test_redis_unavailable_fails_closed_only_with_key(client, monkeypatch):
    from app.core.config import get_settings

    setup = await _setup_plan(client)
    monkeypatch.setattr(get_settings(), "redis_url", "redis://127.0.0.1:1/0")

    keyed = await _trigger(client, setup, key="ci-build-no-redis")
    assert keyed.status_code == 503, keyed.text
    assert keyed.json()["error"]["code"] == "idempotency_unavailable"

    # 不带 key 的请求不依赖 Redis,行为不变
    plain = await _trigger(client, setup)
    assert plain.status_code == 202, plain.text


async def test_invalid_idempotency_header_422(client):
    setup = await _setup_plan(client)
    too_long = await _trigger(client, setup, key="k" * 256)
    assert too_long.status_code == 422, too_long.text
    assert too_long.json()["error"]["code"] == "idempotency_key_invalid"

    blank = await _trigger(client, setup, key="   ")
    assert blank.status_code == 422, blank.text
    assert blank.json()["error"]["code"] == "idempotency_key_invalid"


async def test_done_key_ttl_is_24_hours(client):
    setup = await _setup_plan(client)
    resp = await _trigger(client, setup, key="ci-build-ttl")
    assert resp.status_code == 202, resp.text

    client_redis = redis_py.Redis.from_url(os.environ["APITEST_REDIS_URL"])
    keys = list(client_redis.scan_iter("idem:*"))
    assert len(keys) == 1, keys
    ttl = client_redis.ttl(keys[0])
    assert 80_000 < ttl <= 86_400, ttl
