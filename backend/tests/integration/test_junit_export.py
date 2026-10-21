"""JUnit XML 导出集成测试(issue #41,spec #35 决策 #8)。

主缝:HTTP 集成测试(真实 PostgreSQL + eager Celery)。覆盖端点行为与
外部可观察的 XML 形态:映射、响应头、404/409/422、Token 权限、名称回退。
"""

import uuid
import xml.etree.ElementTree as ET

from tests.integration.test_api_tokens import _create_token
from tests.integration.test_cases import _dsl
from tests.integration.test_plan_runs import _setup_plan, _step, _trigger


async def _export(client, run_id: str, *, query: str = "?format=junit", headers=None):
    return await client.get(f"/api/v1/runs/{run_id}/export{query}", headers=headers)


async def test_plan_run_export_maps_cases_and_headers(client, local_server):
    setup = await _setup_plan(
        client,
        local_server,
        [
            ("登录", [_step("s1", "请求登录")]),
            ("下单", [_step("s2", "创建订单", expected=500)]),
        ],
    )
    resp = await _trigger(client, setup)
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["status"] == "failed"

    export = await _export(client, run_id)
    assert export.status_code == 200, export.text
    assert export.headers["content-type"] == "application/xml; charset=utf-8"
    assert export.headers["content-disposition"] == f'attachment; filename="run-{run_id}.xml"'

    root = ET.fromstring(export.text)
    assert root.tag == "testsuites"
    suite = root.find("testsuite")
    assert suite is not None
    assert suite.attrib["name"] == "回归计划"
    assert suite.attrib["tests"] == "2"
    assert suite.attrib["failures"] == "1"
    assert suite.attrib["errors"] == "0"
    assert suite.attrib["skipped"] == "0"

    testcases = suite.findall("testcase")
    assert [tc.attrib["name"] for tc in testcases] == ["登录", "下单"]
    assert all(tc.attrib["classname"] == "回归计划" for tc in testcases)
    assert testcases[0].find("failure") is None
    assert testcases[1].find("failure") is not None
    # system-out 是步骤摘要,不含响应体(被测 /json 响应体含 hello/world)
    assert "请求登录" in testcases[0].find("system-out").text
    assert "hello" not in export.text

    # 用例 time = 该用例步骤耗时之和(与步骤明细一致)
    steps = (await client.get(f"/api/v1/runs/{run_id}/steps")).json()["items"]
    for testcase, case_index in zip(testcases, (0, 1), strict=True):
        total_ms = sum(s["duration_ms"] for s in steps if s["case_index"] == case_index)
        assert testcase.attrib["time"] == f"{total_ms / 1000:.3f}"


async def test_single_case_run_export_uses_run_duration(client, local_server):
    project = (await client.post("/api/v1/projects", json={"name": "单用例项目"})).json()
    env = (
        await client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        )
    ).json()
    case = (
        await client.post(
            f"/api/v1/projects/{project['id']}/cases",
            json={"name": "登录用例", "dsl": _dsl(url=f"{local_server}/json")},
        )
    ).json()
    triggered = await client.post(
        f"/api/v1/projects/{project['id']}/runs",
        json={"case_id": case["id"], "environment_id": env["id"]},
    )
    run_id = triggered.json()["run_id"]
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["status"] == "success"

    root = ET.fromstring((await _export(client, run_id)).text)
    suite = root.find("testsuite")
    assert suite.attrib["tests"] == "1"
    testcase = suite.find("testcase")
    assert testcase.attrib["name"] == "登录用例"
    # 单用例 run 的 time 用 run 时长,不是步骤耗时之和
    assert testcase.attrib["time"] == f"{run['duration_ms'] / 1000:.3f}"
    assert "step one" in testcase.find("system-out").text


async def test_export_unknown_run_404(client):
    resp = await _export(client, str(uuid.uuid4()))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "run_not_found"


async def test_export_non_terminal_run_409(client):
    project = (await client.post("/api/v1/projects", json={"name": "p"})).json()
    env = (
        await client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        )
    ).json()
    case = (
        await client.post(
            f"/api/v1/projects/{project['id']}/cases",
            json={"name": "c", "dsl": _dsl()},
        )
    ).json()

    # eager Celery 下经 API 触发的 run 会立即终态;直接落一条 pending 的 run 钉 409
    from app.main import app
    from app.models import Run

    async with app.state.session_factory() as session:
        run = Run(
            project_id=project["id"],
            case_id=case["id"],
            case_version=case["current_version"],
            environment_id=env["id"],
            trigger_mode="manual",
            status="pending",
        )
        session.add(run)
        await session.commit()
        run_id = str(run.id)

    resp = await _export(client, run_id)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "run_not_finished"


async def test_export_rejects_unsupported_or_missing_format(client, local_server):
    setup = await _setup_plan(client, local_server, [("登录", [_step("s1", "步骤")])])
    run_id = (await _trigger(client, setup)).json()["run_id"]

    for query in ("?format=html", ""):
        resp = await _export(client, run_id, query=query)
        assert resp.status_code == 422, query
        assert resp.json()["error"]["code"] == "export_format_invalid"


async def test_export_gate_property_present_only_when_configured(client, local_server):
    setup = await _setup_plan(client, local_server, [("登录", [_step("s1", "步骤")])])
    with_gate = await _trigger(client, setup, quality_gate={"min_pass_rate": 1.0})
    gated_run = (await client.get(f"/api/v1/runs/{with_gate.json()['run_id']}")).json()
    assert gated_run["summary"]["gate_passed"] is True

    gated = ET.fromstring((await _export(client, gated_run["run_id"])).text)
    prop = gated.find("testsuite/properties/property")
    assert prop is not None
    assert prop.attrib == {"name": "gate_passed", "value": "true"}

    plain_run_id = (await _trigger(client, setup)).json()["run_id"]
    plain = ET.fromstring((await _export(client, plain_run_id)).text)
    assert plain.find("testsuite/properties") is None


async def test_export_token_permissions(client, local_server):
    setup = await _setup_plan(client, local_server, [("登录", [_step("s1", "步骤")])])
    run_id = (await _trigger(client, setup)).json()["run_id"]
    url = f"/api/v1/runs/{run_id}/export?format=junit"

    # 默认 scopes(run:trigger + report:read)且项目一致 → 可导出
    own_token = await _create_token(client, setup["project_id"])
    ok = await client.get(url, headers={"Authorization": f"Bearer {own_token['token']}"})
    assert ok.status_code == 200, ok.text

    # 缺 report:read → 403
    trigger_only = await _create_token(
        client, setup["project_id"], scopes=["run:trigger"]
    )
    scoped = await client.get(
        url, headers={"Authorization": f"Bearer {trigger_only['token']}"}
    )
    assert scoped.status_code == 403
    assert scoped.json()["error"]["code"] == "insufficient_scope"

    # 跨项目 Token → 403(不泄露 run 存在性以外的信息)
    other_project = (await client.post("/api/v1/projects", json={"name": "别家"})).json()
    foreign = await _create_token(client, other_project["id"])
    crossed = await client.get(
        url, headers={"Authorization": f"Bearer {foreign['token']}"}
    )
    assert crossed.status_code == 403
    assert crossed.json()["error"]["code"] == "token_project_mismatch"


async def test_export_falls_back_to_ids_after_plan_and_case_deleted(client, local_server):
    setup = await _setup_plan(client, local_server, [("登录", [_step("s1", "步骤")])])
    run_id = (await _trigger(client, setup)).json()["run_id"]

    assert (await client.delete(f"/api/v1/cases/{setup['case_ids'][0]}")).status_code == 204
    assert (await client.delete(f"/api/v1/plans/{setup['plan_id']}")).status_code == 204

    root = ET.fromstring((await _export(client, run_id)).text)
    suite = root.find("testsuite")
    assert suite.attrib["name"] == f"plan #{setup['plan_id']}"
    testcase = suite.find("testcase")
    assert testcase.attrib["classname"] == f"plan #{setup['plan_id']}"
    assert testcase.attrib["name"] == f"用例 #{setup['case_ids'][0]}"
