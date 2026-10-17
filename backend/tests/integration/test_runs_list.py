"""执行列表端点(T10):报告列表页的分页数据源。"""

LIMIT_DEFAULT = 20


async def _setup(client, name: str = "列表项目") -> tuple[int, int, int]:
    """建项目 + 环境 + 单步用例,返回 (project_id, env_id, case_id)。"""
    from tests.integration.test_cases import _dsl

    project = (await client.post("/api/v1/projects", json={"name": name})).json()
    env = (
        await client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "local", "base_url": "unused"},
        )
    ).json()
    resp = await client.post(
        f"/api/v1/projects/{project['id']}/cases",
        json={"name": "列表用例", "dsl": _dsl(url="http://127.0.0.1:1/unreachable")},
    )
    assert resp.status_code == 201, resp.text
    return project["id"], env["id"], resp.json()["id"]


async def _trigger(client, project_id: int, env_id: int, case_id: int) -> str:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/runs",
        json={"case_id": case_id, "environment_id": env_id},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["run_id"]


async def test_empty_list(client):
    project_id, _, _ = await _setup(client, name="空列表项目")
    resp = await client.get(f"/api/v1/projects/{project_id}/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["limit"] == LIMIT_DEFAULT
    assert body["offset"] == 0


async def test_list_ordered_desc_with_meta(client):
    project_id, env_id, case_id = await _setup(client, name="倒序项目")
    ids = [await _trigger(client, project_id, env_id, case_id) for _ in range(3)]

    body = (await client.get(f"/api/v1/projects/{project_id}/runs")).json()
    assert body["total"] == 3
    assert [r["run_id"] for r in body["items"]] == ids[::-1]  # created_at 倒序
    # 元素结构与单 run 查询一致
    assert set(body["items"][0]) >= {
        "run_id", "project_id", "case_id", "case_version", "environment_id",
        "trigger_mode", "status", "created_at",
    }


async def test_pagination_window(client):
    project_id, env_id, case_id = await _setup(client, name="分页项目")
    ids = [await _trigger(client, project_id, env_id, case_id) for _ in range(3)]

    page1 = (
        await client.get(f"/api/v1/projects/{project_id}/runs", params={"limit": 2, "offset": 0})
    ).json()
    page2 = (
        await client.get(f"/api/v1/projects/{project_id}/runs", params={"limit": 2, "offset": 2})
    ).json()
    assert [r["run_id"] for r in page1["items"]] == ids[::-1][:2]
    assert [r["run_id"] for r in page2["items"]] == ids[::-1][2:]
    assert (page1["total"], page2["total"]) == (3, 3)


async def test_project_isolation(client):
    project_id, env_id, case_id = await _setup(client, name="隔离 A")
    await _trigger(client, project_id, env_id, case_id)
    other = (await client.post("/api/v1/projects", json={"name": "隔离 B"})).json()

    body = (await client.get(f"/api/v1/projects/{other['id']}/runs")).json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_project_not_found(client):
    resp = await client.get("/api/v1/projects/9999/runs")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "project_not_found"


async def test_invalid_pagination_422(client):
    project_id, _, _ = await _setup(client, name="非法分页项目")
    resp = await client.get(f"/api/v1/projects/{project_id}/runs", params={"limit": 0})
    assert resp.status_code == 422
    resp = await client.get(f"/api/v1/projects/{project_id}/runs", params={"limit": 101})
    assert resp.status_code == 422
    resp = await client.get(f"/api/v1/projects/{project_id}/runs", params={"offset": -1})
    assert resp.status_code == 422
