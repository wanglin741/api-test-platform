"""执行列表端点(T10):报告列表页的分页数据源。游标分页,只支持下一页。"""


def _dsl(url: str = "http://127.0.0.1:1/unreachable") -> dict:
    return {
        "dsl_version": 1,
        "steps": [
            {
                "id": "s1",
                "name": "step one",
                "type": "http",
                "request": {"method": "GET", "url": url},
            }
        ],
    }


async def _setup(client, name: str = "列表项目") -> tuple[int, int, int]:
    """建项目 + 环境 + 单步用例,返回 (project_id, env_id, case_id)。"""
    project = (await client.post("/api/v1/projects", json={"name": name})).json()
    env = (
        await client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "local", "base_url": "unused"},
        )
    ).json()
    resp = await client.post(
        f"/api/v1/projects/{project['id']}/cases",
        json={"name": "列表用例", "dsl": _dsl()},
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
    assert body["next_cursor"] is None


async def test_list_ordered_desc(client):
    project_id, env_id, case_id = await _setup(client, name="倒序项目")
    ids = [await _trigger(client, project_id, env_id, case_id) for _ in range(3)]

    body = (await client.get(f"/api/v1/projects/{project_id}/runs")).json()
    assert [r["run_id"] for r in body["items"]] == ids[::-1]  # created_at 倒序
    assert body["next_cursor"] is None  # 未到 limit,没有下一页
    # 元素结构与单 run 查询一致
    assert set(body["items"][0]) >= {
        "run_id", "project_id", "case_id", "case_version", "environment_id",
        "trigger_mode", "status", "created_at",
    }


async def test_cursor_walks_all_without_dup_or_gap(client):
    project_id, env_id, case_id = await _setup(client, name="翻页项目")
    ids = [await _trigger(client, project_id, env_id, case_id) for _ in range(5)]

    # limit=2 翻页链:应恰好走完 5 条,不重不漏
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):  # 防死循环上界
        params = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        body = (
            await client.get(f"/api/v1/projects/{project_id}/runs", params=params)
        ).json()
        seen.extend(r["run_id"] for r in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == ids[::-1]


async def test_project_isolation(client):
    project_id, env_id, case_id = await _setup(client, name="隔离 A")
    await _trigger(client, project_id, env_id, case_id)
    other = (await client.post("/api/v1/projects", json={"name": "隔离 B"})).json()

    body = (await client.get(f"/api/v1/projects/{other['id']}/runs")).json()
    assert body["items"] == []
    assert body["next_cursor"] is None


async def test_project_not_found(client):
    resp = await client.get("/api/v1/projects/9999/runs")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "project_not_found"


async def test_invalid_cursor_422(client):
    project_id, _, _ = await _setup(client, name="非法游标项目")
    resp = await client.get(
        f"/api/v1/projects/{project_id}/runs", params={"cursor": "not-a-cursor"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_cursor"


async def test_filter_by_plan_id(client):
    project_id, env_id, case_id = await _setup(client, name="计划筛选项目")
    plan = (
        await client.post(f"/api/v1/projects/{project_id}/plans", json={"name": "筛选计划"})
    ).json()
    put = await client.put(
        f"/api/v1/plans/{plan['id']}/items", json={"items": [{"case_id": case_id}]}
    )
    assert put.status_code == 200, put.text

    # 混合动线:两条计划执行 + 一条单用例执行
    plan_run_ids = []
    for _ in range(2):
        resp = await client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"plan_id": plan["id"], "environment_id": env_id},
        )
        assert resp.status_code == 202, resp.text
        plan_run_ids.append(resp.json()["run_id"])
    await _trigger(client, project_id, env_id, case_id)

    # 按计划筛选:只有计划执行,倒序
    body = (
        await client.get(
            f"/api/v1/projects/{project_id}/runs", params={"plan_id": plan["id"]}
        )
    ).json()
    assert [r["run_id"] for r in body["items"]] == plan_run_ids[::-1]
    assert all(r["plan_id"] == plan["id"] for r in body["items"])
    # 计划行带 plan_id,单用例行 case_id 非空(列表出参二选一)
    assert body["items"][0]["case_id"] is None

    # 不带筛选仍是全量倒序(4 条)
    body = (await client.get(f"/api/v1/projects/{project_id}/runs")).json()
    assert len(body["items"]) == 3


async def test_invalid_limit_422(client):
    project_id, _, _ = await _setup(client, name="非法limit项目")
    resp = await client.get(f"/api/v1/projects/{project_id}/runs", params={"limit": 0})
    assert resp.status_code == 422
    resp = await client.get(f"/api/v1/projects/{project_id}/runs", params={"limit": 101})
    assert resp.status_code == 422
