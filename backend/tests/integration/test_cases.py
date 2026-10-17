"""用例 CRUD + 版本快照行为集成测试。"""

import pytest


def _dsl(url: str = "http://inner.example.com/api") -> dict:
    return {
        "dsl_version": 1,
        "config": {"variables": {"name": "world"}},
        "steps": [
            {
                "id": "s1",
                "name": "step one",
                "type": "http",
                "request": {"method": "GET", "url": url},
            }
        ],
    }


async def _create_project(client) -> int:
    resp = await client.post("/api/v1/projects", json={"name": "p"})
    return resp.json()["id"]


async def _create_case(client, project_id: int, **overrides) -> dict:
    payload = {"name": "登录用例", "dsl": _dsl(), **overrides}
    resp = await client.post(f"/api/v1/projects/{project_id}/cases", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_case_writes_v1_snapshot(client):
    project_id = await _create_project(client)
    body = await _create_case(client, project_id, priority="P1", tags=["smoke"], change_note="初始")
    assert body["current_version"] == 1
    assert body["dsl"] == _dsl()
    assert body["change_note"] == "初始"
    assert body["tags"] == ["smoke"]
    assert body["created_by"] > 0


async def test_get_case_returns_current_dsl(client):
    project_id = await _create_project(client)
    created = await _create_case(client, project_id)
    got = await client.get(f"/api/v1/cases/{created['id']}")
    assert got.status_code == 200
    assert got.json()["dsl"] == _dsl()


async def test_patch_with_dsl_increments_version(client):
    project_id = await _create_project(client)
    created = await _create_case(client, project_id)
    new_dsl = _dsl(url="http://inner.example.com/v2")
    resp = await client.patch(
        f"/api/v1/cases/{created['id']}", json={"dsl": new_dsl, "change_note": "换地址"}
    )
    assert resp.status_code == 200
    assert resp.json()["current_version"] == 2
    assert resp.json()["dsl"]["steps"][0]["request"]["url"] == "http://inner.example.com/v2"

    # 快照行为:旧版本行仍在 DB,当前读取返回 v2
    got = await client.get(f"/api/v1/cases/{created['id']}")
    assert got.json()["current_version"] == 2


async def test_patch_metadata_only_keeps_version(client):
    project_id = await _create_project(client)
    created = await _create_case(client, project_id)
    resp = await client.patch(
        f"/api/v1/cases/{created['id']}", json={"name": "改名", "priority": "P0", "tags": ["a"]}
    )
    assert resp.status_code == 200
    assert resp.json()["current_version"] == 1
    assert resp.json()["name"] == "改名"
    assert resp.json()["priority"] == "P0"


async def test_invalid_dsl_422_with_field_location(client):
    project_id = await _create_project(client)
    bad_dsl = {"dsl_version": 2, "config": {}, "steps": []}  # 版本错 + steps 为空
    resp = await client.post(
        f"/api/v1/projects/{project_id}/cases", json={"name": "x", "dsl": bad_dsl}
    )
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "dsl_invalid"
    locs = [tuple(e["loc"]) for e in err["details"]["errors"]]
    assert ("dsl_version",) in locs
    assert ("steps",) in locs


async def test_patch_invalid_dsl_not_snapshotted(client):
    project_id = await _create_project(client)
    created = await _create_case(client, project_id)
    resp = await client.patch(
        f"/api/v1/cases/{created['id']}", json={"dsl": {"dsl_version": 9, "steps": []}}
    )
    assert resp.status_code == 422
    got = await client.get(f"/api/v1/cases/{created['id']}")
    assert got.json()["current_version"] == 1  # 无效 DSL 不产生快照


async def test_list_cases_pagination_and_keyword(client):
    project_id = await _create_project(client)
    for i in range(3):
        await _create_case(client, project_id, name=f"case {i} login" if i == 0 else f"case {i}")

    resp = await client.get(f"/api/v1/projects/{project_id}/cases", params={"page_size": 2})
    body = resp.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2

    resp = await client.get(
        f"/api/v1/projects/{project_id}/cases", params={"page": 2, "page_size": 2}
    )
    assert len(resp.json()["items"]) == 1

    resp = await client.get(
        f"/api/v1/projects/{project_id}/cases", params={"keyword": "LOGIN"}
    )
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["name"] == "case 0 login"

    # page_size 超限钳制到 100
    resp = await client.get(
        f"/api/v1/projects/{project_id}/cases", params={"page_size": 500}
    )
    assert resp.json()["page_size"] == 100


async def test_delete_case_soft(client):
    project_id = await _create_project(client)
    created = await _create_case(client, project_id)
    assert (await client.delete(f"/api/v1/cases/{created['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/cases/{created['id']}")).status_code == 404
    assert (await client.get(f"/api/v1/projects/{project_id}/cases")).json()["total"] == 0


async def test_case_of_deleted_project_404(client):
    project_id = await _create_project(client)
    await client.delete(f"/api/v1/projects/{project_id}")
    resp = await client.post(
        f"/api/v1/projects/{project_id}/cases", json={"name": "x", "dsl": _dsl()}
    )
    assert resp.status_code == 404
