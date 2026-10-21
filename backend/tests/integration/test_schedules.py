"""调度 CRUD + 启停(issue #36,API 设计 §3 schedules)。

只测外部行为:HTTP 状态码、响应体、next_run_at 语义。真实 PG,走 HTTP 层。
时间语义(spec 决策 #2/#5):创建/启用/改 cron 或时区 → 从当前时间重算;
停用 → 置空;仅改名/改环境不重算;软删同时停用并清空 next_run_at。
"""

import datetime

from tests.integration.test_plans import _create_environment, _create_plan, _create_project


async def _create_schedule(client, project_id: int, plan_id: int, **overrides) -> dict:
    payload = {
        "name": "每晚回归",
        "plan_id": plan_id,
        "cron_expression": "0 2 * * *",
        "timezone": "Asia/Shanghai",
        **overrides,
    }
    resp = await client.post(f"/api/v1/projects/{project_id}/schedules", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _is_future_utc(value: str | None) -> bool:
    """next_run_at 是 aware UTC 且晚于此刻(容忍 1 分钟时钟/处理延迟)。"""
    if value is None:
        return False
    nxt = datetime.datetime.fromisoformat(value)
    return (
        nxt.tzinfo is not None
        and nxt > datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
    )


# ---------- 创建 ----------


async def test_create_schedule_computes_next_run_at(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")

    schedule = await _create_schedule(client, project_id, plan["id"])

    assert schedule["project_id"] == project_id
    assert schedule["plan_id"] == plan["id"]
    assert schedule["cron_expression"] == "0 2 * * *"
    assert schedule["timezone"] == "Asia/Shanghai"
    assert schedule["enabled"] is True
    assert schedule["environment_id"] is None
    # 每天凌晨2点上海 → next_run_at 非空、aware、未来(持久化,UTC)
    assert _is_future_utc(schedule["next_run_at"])


async def test_create_schedule_invalid_cron_422(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")

    resp = await client.post(
        f"/api/v1/projects/{project_id}/schedules",
        json={"name": "x", "plan_id": plan["id"], "cron_expression": "0 2 * *", "timezone": "UTC"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "cron_invalid"

    # 6 段(带秒)不支持
    resp = await client.post(
        f"/api/v1/projects/{project_id}/schedules",
        json={
            "name": "x",
            "plan_id": plan["id"],
            "cron_expression": "0 * * * * *",
            "timezone": "UTC",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "cron_invalid"


async def test_create_schedule_invalid_timezone_422(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")

    resp = await client.post(
        f"/api/v1/projects/{project_id}/schedules",
        json={"name": "x", "plan_id": plan["id"], "cron_expression": "* * * * *", "timezone": "UTC+8"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "timezone_invalid"


async def test_create_schedule_cross_project_plan_404(client):
    project_id = await _create_project(client)
    other = await _create_project(client, "别家")
    foreign_plan = await _create_plan(client, other, "别家的计划")

    resp = await client.post(
        f"/api/v1/projects/{project_id}/schedules",
        json={
            "name": "x",
            "plan_id": foreign_plan["id"],
            "cron_expression": "* * * * *",
            "timezone": "UTC",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "plan_not_found"


async def test_create_schedule_cross_project_environment_404(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")
    other = await _create_project(client, "别家")
    foreign_env = await _create_environment(client, other, "别家的环境")

    resp = await client.post(
        f"/api/v1/projects/{project_id}/schedules",
        json={
            "name": "x",
            "plan_id": plan["id"],
            "cron_expression": "* * * * *",
            "timezone": "UTC",
            "environment_id": foreign_env["id"],
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "environment_not_found"


# ---------- 列表与过滤 ----------


async def test_list_schedules_filters_by_plan(client):
    project_id = await _create_project(client)
    plan_a = await _create_plan(client, project_id, "A")
    plan_b = await _create_plan(client, project_id, "B")
    await _create_schedule(client, project_id, plan_a["id"], name="每晚全量")
    await _create_schedule(client, project_id, plan_b["id"], name="早间冒烟")

    listed = await client.get(f"/api/v1/projects/{project_id}/schedules")
    assert [(s["name"], s["plan_id"]) for s in listed.json()] == [("每晚全量", plan_a["id"]), ("早间冒烟", plan_b["id"])]

    filtered = await client.get(f"/api/v1/projects/{project_id}/schedules", params={"plan_id": plan_b["id"]})
    assert [s["name"] for s in filtered.json()] == ["早间冒烟"]


async def test_list_schedules_other_project_isolated(client):
    project_id = await _create_project(client)
    other = await _create_project(client, "别家")
    other_plan = await _create_plan(client, other, "别家的计划")
    await _create_schedule(client, other, other_plan["id"])

    listed = await client.get(f"/api/v1/projects/{project_id}/schedules")
    assert listed.json() == []


# ---------- 更新与重算 ----------


async def test_patch_cron_recomputes_next_run_at(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")
    schedule = await _create_schedule(client, project_id, plan["id"])

    resp = await client.patch(
        f"/api/v1/schedules/{schedule['id']}",
        json={"cron_expression": "30 8 * * 1-5"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cron_expression"] == "30 8 * * 1-5"
    assert _is_future_utc(body["next_run_at"])


async def test_patch_timezone_recomputes(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")
    schedule = await _create_schedule(client, project_id, plan["id"], timezone="UTC")

    resp = await client.patch(
        f"/api/v1/schedules/{schedule['id']}", json={"timezone": "Asia/Shanghai"}
    )
    assert resp.status_code == 200
    assert resp.json()["timezone"] == "Asia/Shanghai"
    assert _is_future_utc(resp.json()["next_run_at"])


async def test_patch_name_only_keeps_next_run_at(client):
    """仅改名不重算(spec 决策 #5):next_run_at 保持原值不动。"""
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")
    schedule = await _create_schedule(client, project_id, plan["id"])
    before = schedule["next_run_at"]

    resp = await client.patch(
        f"/api/v1/schedules/{schedule['id']}", json={"name": "改个名"}
    )
    assert resp.status_code == 200
    assert resp.json()["next_run_at"] == before


async def test_patch_environment_only_keeps_next_run_at(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")
    env = await _create_environment(client, project_id, "staging")
    schedule = await _create_schedule(client, project_id, plan["id"])
    before = schedule["next_run_at"]

    resp = await client.patch(
        f"/api/v1/schedules/{schedule['id']}", json={"environment_id": env["id"]}
    )
    assert resp.status_code == 200
    assert resp.json()["environment_id"] == env["id"]
    assert resp.json()["next_run_at"] == before


async def test_patch_invalid_cron_and_timezone_422(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")
    schedule = await _create_schedule(client, project_id, plan["id"])

    resp = await client.patch(
        f"/api/v1/schedules/{schedule['id']}", json={"cron_expression": "61 * * * *"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "cron_invalid"

    resp = await client.patch(
        f"/api/v1/schedules/{schedule['id']}", json={"timezone": "Not/AZone"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "timezone_invalid"


async def test_patch_cross_project_environment_404(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")
    schedule = await _create_schedule(client, project_id, plan["id"])
    other = await _create_project(client, "别家")
    foreign_env = await _create_environment(client, other, "别家的环境")

    resp = await client.patch(
        f"/api/v1/schedules/{schedule['id']}", json={"environment_id": foreign_env["id"]}
    )
    assert resp.status_code == 404


# ---------- 启停 ----------


async def test_disable_clears_next_run_at_enable_recomputes(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")
    schedule = await _create_schedule(client, project_id, plan["id"])

    resp = await client.post(f"/api/v1/schedules/{schedule['id']}/disable")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["next_run_at"] is None

    resp = await client.post(f"/api/v1/schedules/{schedule['id']}/enable")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    # 从当前时间重算,不补跑停用期间
    assert _is_future_utc(body["next_run_at"])


# ---------- 删除(软删)----------


async def test_delete_schedule_soft_and_404(client):
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")
    schedule = await _create_schedule(client, project_id, plan["id"])

    resp = await client.delete(f"/api/v1/schedules/{schedule['id']}")
    assert resp.status_code == 204

    listed = await client.get(f"/api/v1/projects/{project_id}/schedules")
    assert listed.json() == []
    # 再删一次 404
    assert (await client.delete(f"/api/v1/schedules/{schedule['id']}")).status_code == 404
    # 已删的调度不可再操作
    assert (
        await client.post(f"/api/v1/schedules/{schedule['id']}/enable")
    ).status_code == 404


async def test_unknown_schedule_404(client):
    resp = await client.patch("/api/v1/schedules/99999", json={"name": "x"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "schedule_not_found"


async def test_patch_explicit_null_422(client):
    """显式 null 是非法值(缺省用「不传」表达),挡成 422 而不是 500。"""
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")
    schedule = await _create_schedule(client, project_id, plan["id"])

    for field in ("name", "cron_expression", "timezone"):
        resp = await client.patch(
            f"/api/v1/schedules/{schedule['id']}", json={field: None}
        )
        assert resp.status_code == 422, (field, resp.text)

    # environment_id 例外:显式 null = 清空覆盖环境,合法
    resp = await client.patch(
        f"/api/v1/schedules/{schedule['id']}", json={"environment_id": None}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["environment_id"] is None


async def test_patch_disabled_schedule_keeps_next_run_at_null(client):
    """停用态改 cron/时区不重算:保持「停用 → 置空」不变量。"""
    project_id = await _create_project(client)
    plan = await _create_plan(client, project_id, "回归")
    schedule = await _create_schedule(client, project_id, plan["id"])
    disabled = await client.post(f"/api/v1/schedules/{schedule['id']}/disable")
    assert disabled.json()["next_run_at"] is None

    resp = await client.patch(
        f"/api/v1/schedules/{schedule['id']}", json={"cron_expression": "30 8 * * 1-5"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is False
    assert resp.json()["next_run_at"] is None
