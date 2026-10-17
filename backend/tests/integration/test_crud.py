"""项目 / 环境 / 变量 CRUD 集成测试(走 HTTP 层 + 真实 PG)。"""

import pytest


async def _create_project(client, name="项目A") -> dict:
    resp = await client.post("/api/v1/projects", json={"name": name})
    assert resp.status_code == 201
    return resp.json()


async def _create_environment(client, project_id: int, name="dev") -> dict:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/environments", json={"name": name, "base_url": "http://dev.example.com"}
    )
    assert resp.status_code == 201
    return resp.json()


# ---------- 项目 ----------


async def test_create_and_get_project(client):
    body = await _create_project(client)
    assert body["name"] == "项目A"
    assert body["owner_id"] > 0

    got = await client.get(f"/api/v1/projects/{body['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]


async def test_project_not_found(client):
    resp = await client.get("/api/v1/projects/9999")
    assert resp.status_code == 404
    err = resp.json()["error"]
    assert err["code"] == "project_not_found"


async def test_patch_project(client):
    body = await _create_project(client)
    resp = await client.patch(
        f"/api/v1/projects/{body['id']}", json={"description": "描述", "settings": {"timeout_ms": 5000}}
    )
    assert resp.status_code == 200
    assert resp.json()["description"] == "描述"
    assert resp.json()["settings"] == {"timeout_ms": 5000}


async def test_delete_project_soft(client):
    body = await _create_project(client)
    resp = await client.delete(f"/api/v1/projects/{body['id']}")
    assert resp.status_code == 204

    got = await client.get(f"/api/v1/projects/{body['id']}")
    assert got.status_code == 404
    listed = await client.get("/api/v1/projects")
    assert listed.json() == []


async def test_create_project_validation_error(client):
    resp = await client.post("/api/v1/projects", json={"name": ""})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "validation_error"
    assert err["details"]["errors"]


# ---------- 环境 ----------


async def test_environment_lifecycle(client):
    project = await _create_project(client)
    env = await _create_environment(client, project["id"])

    listed = await client.get(f"/api/v1/projects/{project['id']}/environments")
    assert [e["id"] for e in listed.json()] == [env["id"]]

    patched = await client.patch(
        f"/api/v1/environments/{env['id']}", json={"base_url": "http://staging.example.com"}
    )
    assert patched.json()["base_url"] == "http://staging.example.com"

    deleted = await client.delete(f"/api/v1/environments/{env['id']}")
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/environments/{env['id']}")).status_code == 404
    # 软删行的名额仍被表级唯一约束占用:同名重建 → 409
    conflict = await client.post(
        f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
    )
    assert conflict.status_code == 409


async def test_duplicate_environment_name_conflict(client):
    project = await _create_project(client)
    await _create_environment(client, project["id"], name="prod")
    resp = await client.post(
        f"/api/v1/projects/{project['id']}/environments", json={"name": "prod"}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "environment_name_conflict"


async def test_rename_environment_conflict(client):
    project = await _create_project(client)
    await _create_environment(client, project["id"], name="a")
    env_b = await _create_environment(client, project["id"], name="b")
    resp = await client.patch(f"/api/v1/environments/{env_b['id']}", json={"name": "a"})
    assert resp.status_code == 409


async def test_environment_of_deleted_project_404(client):
    project = await _create_project(client)
    env = await _create_environment(client, project["id"])
    await client.delete(f"/api/v1/projects/{project['id']}")
    assert (await client.get(f"/api/v1/environments/{env['id']}")).status_code == 404
    listed = await client.get(f"/api/v1/projects/{project['id']}/environments")
    assert listed.status_code == 404


# ---------- 变量 ----------


async def test_project_variables_put_replaces(client):
    project = await _create_project(client)
    url = f"/api/v1/projects/{project['id']}/variables"

    first = await client.put(
        url,
        json={"items": [{"name": "base_token", "value": "s1", "is_secret": True}, {"name": "region", "value": "cn"}]},
    )
    assert first.status_code == 200
    items = first.json()
    assert {v["name"] for v in items} == {"base_token", "region"}
    # is_secret 打码,明文不回传
    assert items[0]["value"] == "***"

    second = await client.put(url, json={"items": [{"name": "region", "value": "us"}]})
    assert second.status_code == 200
    assert [v["name"] for v in second.json()] == ["region"]


async def test_environment_variables_scoped(client):
    project = await _create_project(client)
    env_a = await _create_environment(client, project["id"], name="a")
    env_b = await _create_environment(client, project["id"], name="b")

    await client.put(
        f"/api/v1/environments/{env_a['id']}/variables",
        json={"items": [{"name": "host", "value": "a.example.com"}]},
    )
    await client.put(
        f"/api/v1/environments/{env_b['id']}/variables",
        json={"items": [{"name": "host", "value": "b.example.com"}]},
    )

    got_a = (await client.get(f"/api/v1/environments/{env_a['id']}/variables")).json()
    assert [v["value"] for v in got_a] == ["a.example.com"]
    # 项目级变量列表不含环境级变量
    project_level = (await client.get(f"/api/v1/projects/{project['id']}/variables")).json()
    assert project_level == []


async def test_variable_name_validation(client):
    project = await _create_project(client)
    resp = await client.put(
        f"/api/v1/projects/{project['id']}/variables",
        json={"items": [{"name": "1bad-name", "value": "x"}]},
    )
    assert resp.status_code == 422


async def test_variables_of_deleted_environment_404(client):
    project = await _create_project(client)
    env = await _create_environment(client, project["id"])
    await client.delete(f"/api/v1/environments/{env['id']}")
    resp = await client.put(
        f"/api/v1/environments/{env['id']}/variables", json={"items": []}
    )
    assert resp.status_code == 404
