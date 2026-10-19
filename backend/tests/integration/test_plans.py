"""计划 CRUD + 用例清单整体替换(#29)。

只测外部行为:状态码与响应体。真实 PG,走 HTTP 层。
"""

from tests.integration.test_cases import _dsl


async def _create_project(client, name="计划项目") -> int:
    resp = await client.post("/api/v1/projects", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_case(client, project_id: int, name: str) -> dict:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/cases", json={"name": name, "dsl": _dsl()}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_environment(client, project_id: int, name: str) -> dict:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/environments", json={"name": name, "base_url": "http://x"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_plan(client, project_id: int, name: str, **overrides) -> dict:
    payload = {"name": name, **overrides}
    resp = await client.post(f"/api/v1/projects/{project_id}/plans", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------- 计划 CRUD ----------


async def test_create_and_get_plan_defaults(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "每晚回归")

    assert plan["project_id"] == project_id
    assert plan["name"] == "每晚回归"
    assert plan["execute_mode"] == "serial"  # 缺省串行
    assert plan["concurrency"] == 1
    assert plan["stop_on_fail"] is False
    assert plan["environment_id"] is None
    assert plan["item_count"] == 0

    got = await client.get(f"/api/v1/plans/{plan['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == plan["id"]


async def test_create_plan_with_config_and_environment(client):
    project_id = await _create_project(client)
    env = await _create_environment(client, project_id, "staging")

    plan = await _create_plan(
        client,
        project_id,
        "并行回归",
        description="全量回归",
        environment_id=env["id"],
        execute_mode="parallel",
        concurrency=4,
        stop_on_fail=True,
    )
    assert plan["environment_id"] == env["id"]
    assert plan["execute_mode"] == "parallel"
    assert plan["concurrency"] == 4
    assert plan["stop_on_fail"] is True


async def test_create_plan_unknown_environment_404(client):
    project_id = await _create_project(client)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/plans", json={"name": "x", "environment_id": 99999}
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "environment_not_found"

    # 环境属于别的项目:归属不符一律 404,不探测
    other_project = await _create_project(client, "别的项目")
    other_env = await _create_environment(client, other_project, "别家的")
    resp = await client.post(
        f"/api/v1/projects/{project_id}/plans",
        json={"name": "x", "environment_id": other_env["id"]},
    )
    assert resp.status_code == 404


async def test_patch_plan_updates_config(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")

    resp = await client.patch(
        f"/api/v1/plans/{plan['id']}",
        json={"name": "每晚回归", "execute_mode": "parallel", "concurrency": 8},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "每晚回归"
    assert body["execute_mode"] == "parallel"
    assert body["concurrency"] == 8
    assert body["stop_on_fail"] is False  # 未传不动


async def test_delete_plan_soft_then_404(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")

    resp = await client.delete(f"/api/v1/plans/{plan['id']}")
    assert resp.status_code == 204

    assert (await client.get(f"/api/v1/plans/{plan['id']}")).status_code == 404
    listed = await client.get(f"/api/v1/projects/{project_id}/plans")
    assert listed.json() == []

    # 再删一次 404
    assert (await client.delete(f"/api/v1/plans/{plan['id']}")).status_code == 404


async def test_list_plans_orders_and_item_count(client):
    project_id = await _create_project(client)
    first = await _create_plan(client, project_id, "A")
    second = await _create_plan(client, project_id, "B")
    case = await _create_case(client, project_id, "登录")
    resp = await client.put(
        f"/api/v1/plans/{second['id']}/items", json={"items": [{"case_id": case["id"]}]}
    )
    assert resp.status_code == 200, resp.text

    listed = await client.get(f"/api/v1/projects/{project_id}/plans")
    assert [(p["name"], p["item_count"]) for p in listed.json()] == [("A", 0), ("B", 1)]


# ---------- 用例清单整体替换 ----------


async def test_items_replace_orders_with_case_names(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")
    login = await _create_case(client, project_id, "登录")
    order = await _create_case(client, project_id, "下单")

    resp = await client.put(
        f"/api/v1/plans/{plan['id']}/items",
        json={"items": [{"case_id": order["id"]}, {"case_id": login["id"]}]},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert [(i["case_id"], i["case_name"], i["sort_order"]) for i in items] == [
        (order["id"], "下单", 0),
        (login["id"], "登录", 1),
    ]

    got = await client.get(f"/api/v1/plans/{plan['id']}/items")
    assert [i["case_id"] for i in got.json()] == [order["id"], login["id"]]


async def test_items_replace_swaps_order_entirely(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")
    a = await _create_case(client, project_id, "A")
    b = await _create_case(client, project_id, "B")
    url = f"/api/v1/plans/{plan['id']}/items"
    await client.put(url, json={"items": [{"case_id": a["id"]}, {"case_id": b["id"]}]})

    resp = await client.put(url, json={"items": [{"case_id": b["id"]}, {"case_id": a["id"]}]})
    assert resp.status_code == 200
    assert [i["case_id"] for i in resp.json()] == [b["id"], a["id"]]


async def test_items_rejects_cross_project_case_404(client):
    project_id = await _create_project(client)
    other = await _create_project(client, "别家")
    foreign = await _create_case(client, other, "别家的用例")
    plan = await _create_plan(client, project_id, "回归")

    resp = await client.put(
        f"/api/v1/plans/{plan['id']}/items", json={"items": [{"case_id": foreign["id"]}]}
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "case_not_found"


async def test_items_rejects_deleted_case_404(client):
    project_id = await _create_project(client)
    case = await _create_case(client, project_id, "登录")
    plan = await _create_plan(client, project_id, "回归")
    assert (await client.delete(f"/api/v1/cases/{case['id']}")).status_code == 204

    resp = await client.put(
        f"/api/v1/plans/{plan['id']}/items", json={"items": [{"case_id": case["id"]}]}
    )
    assert resp.status_code == 404


async def test_items_rejects_duplicate_case_422(client):
    project_id = await _create_project(client)
    case = await _create_case(client, project_id, "登录")
    plan = await _create_plan(client, project_id, "回归")

    resp = await client.put(
        f"/api/v1/plans/{plan['id']}/items",
        json={"items": [{"case_id": case["id"]}, {"case_id": case["id"]}]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "plan_items_invalid"


async def test_items_rejects_empty_list_is_allowed(client):
    """空清单 = 清空计划:合法(建了计划还没挑用例)。"""
    project_id = await _create_project(client)
    case = await _create_case(client, project_id, "登录")
    plan = await _create_plan(client, project_id, "回归")
    url = f"/api/v1/plans/{plan['id']}/items"
    await client.put(url, json={"items": [{"case_id": case["id"]}]})

    resp = await client.put(url, json={"items": []})
    assert resp.status_code == 200
    assert resp.json() == []
    got = await client.get(f"/api/v1/plans/{plan['id']}")
    assert got.json()["item_count"] == 0


async def test_items_unknown_plan_404(client):
    resp = await client.put("/api/v1/plans/99999/items", json={"items": []})
    assert resp.status_code == 404
