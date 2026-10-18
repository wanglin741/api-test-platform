"""目录 CRUD + move + 用例挂目录过滤(issue #20)。

只测外部行为:状态码与响应体。真实 PG,走 HTTP 层。
"""

from tests.integration.test_cases import _dsl


async def _create_project(client, name="目录项目") -> int:
    resp = await client.post("/api/v1/projects", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_folder(client, project_id: int, name: str, **overrides) -> dict:
    payload = {"name": name, **overrides}
    resp = await client.post(f"/api/v1/projects/{project_id}/folders", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_case(client, project_id: int, name: str, **overrides) -> dict:
    payload = {"name": name, "dsl": _dsl(), **overrides}
    resp = await client.post(f"/api/v1/projects/{project_id}/cases", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------- CRUD ----------


async def test_create_and_list_folders(client):
    project_id = await _create_project(client)
    top = await _create_folder(client, project_id, "登录链路")
    child = await _create_folder(client, project_id, "验证码", parent_id=top["id"], sort_order=3)

    assert child["parent_id"] == top["id"]
    assert child["sort_order"] == 3
    assert child["scope"] == "case"

    listed = await client.get(f"/api/v1/projects/{project_id}/folders")
    assert listed.status_code == 200
    assert [f["name"] for f in listed.json()] == ["登录链路", "验证码"]


async def test_create_folder_sibling_name_conflict(client):
    project_id = await _create_project(client)
    await _create_folder(client, project_id, "登录链路")

    resp = await client.post(f"/api/v1/projects/{project_id}/folders", json={"name": "登录链路"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "folder_name_conflict"

    # 不同父节点下允许同名
    other = await _create_folder(client, project_id, "退款链路")
    ok = await client.post(
        f"/api/v1/projects/{project_id}/folders",
        json={"name": "登录链路", "parent_id": other["id"]},
    )
    assert ok.status_code == 201


async def test_create_folder_depth_limit_three(client):
    project_id = await _create_project(client)
    top = await _create_folder(client, project_id, "L1")
    mid = await _create_folder(client, project_id, "L2", parent_id=top["id"])
    await _create_folder(client, project_id, "L3", parent_id=mid["id"])

    resp = await client.post(
        f"/api/v1/projects/{project_id}/folders",
        json={"name": "L4", "parent_id": mid["id"]},
    )
    # L3 已在第 3 级,L4 挂在 L2 下 depth=3 是允许的;挂在 L3 下才是第 4 级
    assert resp.status_code == 201, resp.text
    l3 = (await client.get(f"/api/v1/projects/{project_id}/folders")).json()
    l3_id = next(f["id"] for f in l3 if f["name"] == "L3")
    too_deep = await client.post(
        f"/api/v1/projects/{project_id}/folders",
        json={"name": "L4-under-L3", "parent_id": l3_id},
    )
    assert too_deep.status_code == 409
    assert too_deep.json()["error"]["code"] == "folder_depth_exceeded"


async def test_rename_folder(client):
    project_id = await _create_project(client)
    folder = await _create_folder(client, project_id, "旧名")

    resp = await client.patch(f"/api/v1/folders/{folder['id']}", json={"name": "新名"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "新名"


async def test_rename_folder_conflict(client):
    project_id = await _create_project(client)
    await _create_folder(client, project_id, "已有")
    folder = await _create_folder(client, project_id, "别的")

    resp = await client.patch(f"/api/v1/folders/{folder['id']}", json={"name": "已有"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "folder_name_conflict"


# ---------- move:环检测 / 深度 / 跨项目 ----------


async def test_move_folder_reorders(client):
    project_id = await _create_project(client)
    a = await _create_folder(client, project_id, "A")
    b = await _create_folder(client, project_id, "B")

    resp = await client.post(f"/api/v1/folders/{b['id']}/move", json={"parent_id": None, "sort_order": -1})
    assert resp.status_code == 200
    listed = await client.get(f"/api/v1/projects/{project_id}/folders")
    assert [f["name"] for f in listed.json()] == ["B", "A"]


async def test_move_folder_into_self_or_descendant_rejected(client):
    project_id = await _create_project(client)
    top = await _create_folder(client, project_id, "父")
    child = await _create_folder(client, project_id, "子", parent_id=top["id"])

    # 移到自身
    self_move = await client.post(f"/api/v1/folders/{top['id']}/move", json={"parent_id": top["id"]})
    assert self_move.status_code == 409
    assert self_move.json()["error"]["code"] == "folder_cycle"

    # 移到自己的后代:环
    resp = await client.post(f"/api/v1/folders/{top['id']}/move", json={"parent_id": child["id"]})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "folder_cycle"


async def test_move_folder_depth_validation(client):
    project_id = await _create_project(client)
    # 一条 3 级链(L1→L2→L3)+ 另一条 2 级链(M1→M2)
    l1 = await _create_folder(client, project_id, "L1")
    l2 = await _create_folder(client, project_id, "L2", parent_id=l1["id"])
    await _create_folder(client, project_id, "L3", parent_id=l2["id"])
    m1 = await _create_folder(client, project_id, "M1")
    m2 = await _create_folder(client, project_id, "M2", parent_id=m1["id"])

    # 把 L1(子树高 3)挪到 M2(深度 2)下 → 5 级,拒绝
    resp = await client.post(f"/api/v1/folders/{l1['id']}/move", json={"parent_id": m2["id"]})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "folder_depth_exceeded"

    # 挪 L2(子树高 2)到 M1(深度 1)下 → 3 级,可以
    resp2 = await client.post(f"/api/v1/folders/{l2['id']}/move", json={"parent_id": m1["id"]})
    assert resp2.status_code == 200
    assert resp2.json()["parent_id"] == m1["id"]


async def test_move_folder_cross_project_parent_rejected(client):
    project_id = await _create_project(client)
    folder = await _create_folder(client, project_id, "我的")
    other_project = await _create_project(client, "别项目")
    foreign = await _create_folder(client, other_project, "别家的")

    resp = await client.post(f"/api/v1/folders/{folder['id']}/move", json={"parent_id": foreign["id"]})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "folder_not_found"


# ---------- delete ----------


async def test_delete_empty_folder(client):
    project_id = await _create_project(client)
    folder = await _create_folder(client, project_id, "可删")

    resp = await client.delete(f"/api/v1/folders/{folder['id']}")
    assert resp.status_code == 204
    listed = await client.get(f"/api/v1/projects/{project_id}/folders")
    assert listed.json() == []


async def test_delete_folder_with_child_or_cases_conflict(client):
    project_id = await _create_project(client)
    top = await _create_folder(client, project_id, "父")
    child = await _create_folder(client, project_id, "子", parent_id=top["id"])

    resp = await client.delete(f"/api/v1/folders/{top['id']}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "folder_not_empty"

    # 移走子目录后仍因有用例被拒
    await client.post(f"/api/v1/folders/{child['id']}/move", json={"parent_id": None})
    await _create_case(client, project_id, "登录用例", folder_id=top["id"])
    resp2 = await client.delete(f"/api/v1/folders/{top['id']}")
    assert resp2.status_code == 409
    assert resp2.json()["error"]["code"] == "folder_not_empty"


# ---------- 用例挂目录与列表过滤 ----------


async def test_case_with_folder_create_patch_and_list_filter(client):
    project_id = await _create_project(client)
    top = await _create_folder(client, project_id, "登录链路")
    child = await _create_folder(client, project_id, "短信", parent_id=top["id"])
    elsewhere = await _create_folder(client, project_id, "下单链路")

    await _create_case(client, project_id, "A", folder_id=top["id"])
    await _create_case(client, project_id, "B", folder_id=child["id"])
    await _create_case(client, project_id, "C", folder_id=elsewhere["id"])
    await _create_case(client, project_id, "D")

    # 选中目录 = 含子目录的用例
    resp = await client.get(f"/api/v1/projects/{project_id}/cases?folder_id={top['id']}")
    assert [c["name"] for c in resp.json()["items"]] == ["B", "A"]  # id desc

    # 未分类哨兵 0
    resp = await client.get(f"/api/v1/projects/{project_id}/cases?folder_id=0")
    assert [c["name"] for c in resp.json()["items"]] == ["D"]

    # 不过滤 = 全部
    resp = await client.get(f"/api/v1/projects/{project_id}/cases")
    assert resp.json()["total"] == 4

    # PATCH 显式 null = 移回未分类
    case_a = await client.get(f"/api/v1/projects/{project_id}/cases?keyword=A")
    case_id = case_a.json()["items"][0]["id"]
    patched = await client.patch(f"/api/v1/cases/{case_id}", json={"folder_id": None})
    assert patched.status_code == 200
    assert patched.json()["folder_id"] is None
    resp = await client.get(f"/api/v1/projects/{project_id}/cases?folder_id=0")
    assert [c["name"] for c in resp.json()["items"]] == ["D", "A"]  # id desc


async def test_case_folder_validation(client):
    project_id = await _create_project(client)
    folder = await _create_folder(client, project_id, "F")
    other_project = await _create_project(client, "别项目")

    # 创建时挂不存在的目录
    resp = await client.post(
        f"/api/v1/projects/{project_id}/cases", json={"name": "x", "dsl": _dsl(), "folder_id": 9999}
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "folder_not_found"

    # 跨项目目录
    resp = await client.post(
        f"/api/v1/projects/{project_id}/cases",
        json={"name": "x", "dsl": _dsl(), "folder_id": folder["id"]},
    )
    assert resp.status_code == 201

    foreign = await _create_folder(client, other_project, "别家的")
    resp = await client.patch(f"/api/v1/cases/{resp.json()['id']}", json={"folder_id": foreign["id"]})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "folder_not_found"

    # 列表过滤时目录不存在
    resp = await client.get(f"/api/v1/projects/{project_id}/cases?folder_id=9999")
    assert resp.status_code == 404
