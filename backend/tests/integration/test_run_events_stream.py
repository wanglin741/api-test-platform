"""SSE 实时进度事件流(架构设计 §6 / API设计 §5):真实 PG + 真实 Redis + eager Celery。

覆盖 #23 骨干 + #25 快照与续传:事件序列与载荷、快照路径、Last-Event-ID
续传、补偿兜底、过滤参数、响应头、404。快照/续传的游标比较用合成游标,
不造上万条真实数据。
"""

import json
import uuid
from datetime import datetime

import pytest


async def _setup_and_run(client, url: str, dsl: dict | None = None) -> str:
    """建项目 + 环境 + 用例并触发执行(eager 同步跑完),返回 run_id。"""
    from tests.integration.test_cases import _dsl

    project = (await client.post("/api/v1/projects", json={"name": "事件流项目"})).json()
    env = (
        await client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "local", "base_url": "unused"},
        )
    ).json()
    case = (
        await client.post(
            f"/api/v1/projects/{project['id']}/cases",
            json={"name": "单步用例", "dsl": dsl or _dsl(url=url)},
        )
    ).json()
    resp = await client.post(
        f"/api/v1/projects/{project['id']}/runs",
        json={"case_id": case["id"], "environment_id": env["id"]},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["run_id"]


async def _read_sse(client, run_id: str, **kwargs) -> tuple[list[dict], dict[str, str]]:
    """消费事件流直到连接自然关闭;返回 (事件列表, 响应头)。用超时包住防挂死。"""
    import asyncio

    events: list[dict] = []
    headers: dict[str, str] = {}
    current: dict = {}
    async with client.stream("GET", f"/api/v1/runs/{run_id}/events", **kwargs) as resp:
        assert resp.status_code == 200
        headers = dict(resp.headers)
        async for line in resp.aiter_lines():
            if line.startswith(":"):  # keep-alive 注释
                continue
            if not line:  # 空行 = 事件边界
                if current:
                    events.append(current)
                    current = {}
                continue
            field, _, value = line.partition(": ")
            if field == "event":
                current["event"] = value
            elif field == "data":
                current["data"] = value
            elif field == "id":
                current["id"] = value
        if current:  # 流结束时未跟空行的最后一条
            events.append(current)
    return events, headers


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stream_head_id(run_id: str) -> str:
    """直接从 Redis 取流首消息 id:首连走快照路径,流内事件不经 SSE 全量投出。"""
    import redis as redis_py

    from app.core.config import get_settings

    client = redis_py.Redis.from_url(get_settings().redis_url)
    entries = client.xrange(f"run:events:{run_id}", count=1)
    assert entries, "流为空,无法取流首 id"
    return entries[0][0].decode()


async def test_first_connect_gets_snapshot_then_terminal_and_closes(
    client, local_server
):
    """首次连接(无 Last-Event-ID)走快照路径:内容与数据库一致,终态补发后自然关闭。"""
    run_id = await _setup_and_run(client, f"{local_server}/json")

    events, headers = await _read_sse(client, run_id)

    # 三个响应头;text/event-stream 由 EventSourceResponse 自带
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["cache-control"] == "no-cache"
    assert headers["x-accel-buffering"] == "no"

    names = [e["event"] for e in events]
    assert names == ["run.snapshot", "run.finished"]  # 终态执行:快照 + 终态即收尾

    snapshot = json.loads(events[0]["data"])
    db_run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert snapshot["run"]["status"] == db_run["status"] == "success"
    assert snapshot["run"]["summary"] == db_run["summary"]
    assert _ts(snapshot["run"]["started_at"]) == _ts(db_run["started_at"])
    assert snapshot["steps_truncated"] is False
    assert [s["step_index"] for s in snapshot["steps"]] == [0]  # 与数据库步骤一致

    finished = json.loads(events[-1]["data"])
    assert finished["status"] == "success"
    assert finished["summary"] == db_run["summary"]

    # 收到终态后连接自然关闭(消费循环能结束本身就是断言)


async def test_replay_from_last_event_id_continues_after_cursor(
    client, local_server
):
    """带 Last-Event-ID 重连:从游标之后续传,首事件不是快照,事件 id 均大于游标。"""
    run_id = await _setup_and_run(client, f"{local_server}/json")
    cursor = _stream_head_id(run_id)  # 流首(run.started)作为重连游标

    events, _ = await _read_sse(client, run_id, headers={"Last-Event-ID": cursor})

    names = [e["event"] for e in events]
    assert "run.snapshot" not in names  # 游标仍有效:重放,不同步快照
    assert names == [
        "step.started", "step.finished", "run.progress", "run.finished",
    ]  # run.started 及之前的(含自身)不重发
    ids = [int(e["id"].split("-")[0]) for e in events if e.get("id")]
    cursor_ms = int(cursor.split("-")[0])
    assert all(i > cursor_ms for i in ids)


async def test_cursor_earlier_than_stream_head_falls_back_to_snapshot(
    client, local_server
):
    """游标早于流首(合成小游标,模拟该段已被裁剪):走快照,不重放。"""
    run_id = await _setup_and_run(client, f"{local_server}/json")

    events, _ = await _read_sse(client, run_id, headers={"Last-Event-ID": "1-0"})

    names = [e["event"] for e in events]
    assert names == ["run.snapshot", "run.finished"]


async def test_deleted_stream_falls_back_to_snapshot(client, local_server):
    """流已被删除(过期/flush)而客户端仍带着游标:补偿走快照,不挂起。"""
    import redis as redis_py

    from app.core.config import get_settings

    run_id = await _setup_and_run(client, f"{local_server}/json")
    client_py = redis_py.Redis.from_url(get_settings().redis_url)
    client_py.delete(f"run:events:{run_id}")

    events, _ = await _read_sse(client, run_id, headers={"Last-Event-ID": "1-0"})

    names = [e["event"] for e in events]
    assert names == ["run.snapshot", "run.finished"]


async def test_idle_db_check_publishes_terminal_when_stream_has_none(
    client, local_server, monkeypatch
):
    """worker 被强杀、流里永无终态:连续空读到阈值后查库补发终态并关闭。"""
    import app.api.v1.runs as runs_module

    # 缩短空读间隔与阈值:~2 次空读(每次 0.05s)即触发查库,不必等 60 秒
    monkeypatch.setattr(runs_module, "READ_BLOCK_SECONDS", 0.05)
    monkeypatch.setattr(runs_module, "IDLE_DB_CHECK_SECONDS", 0.1)

    run_id = await _setup_and_run(client, f"{local_server}/json")

    # 带「指向未来」的游标(流在、游标有效):重放路径空转,
    # 流里再无新事件 —— 模拟 worker 强杀后终态永不落流
    events, _ = await _read_sse(
        client, run_id, headers={"Last-Event-ID": "9999999999999999-0"}
    )

    names = [e["event"] for e in events]
    assert names == ["run.finished"]
    assert json.loads(events[0]["data"])["status"] == "success"


async def test_stream_unknown_run_404(client):
    resp = await client.get(f"/api/v1/runs/{uuid.uuid4()}/events")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "run_not_found"


async def test_events_finished_filter_passes_only_finished(client, local_server):
    """?events=finished:只放行 step.finished/run.finished(快照恒放行)。"""
    run_id = await _setup_and_run(client, f"{local_server}/json")

    events, _ = await _read_sse(client, run_id, params={"events": "finished"})

    names = [e["event"] for e in events]
    assert names == ["run.snapshot", "run.finished"]  # step.started/progress 被滤掉


async def test_events_filter_invalid_value_400(client, local_server):
    """非法过滤值立刻 400,不静默忽略。"""
    run_id = await _setup_and_run(client, f"{local_server}/json")

    resp = await client.get(f"/api/v1/runs/{run_id}/events", params={"events": "bogus"})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_events_filter"


async def test_crash_fallback_still_publishes_run_finished(
    client, local_server, monkeypatch
):
    """引擎意外崩溃的兜底路径也落 error 终态:首连快照即见,不永久挂起。"""
    from app.tasks import engine_tasks

    async def boom(dsl, context):
        raise RuntimeError("引擎炸了")

    monkeypatch.setattr(engine_tasks, "engine_execute", boom)
    run_id = await _setup_and_run(client, f"{local_server}/json")

    events, _ = await _read_sse(client, run_id)

    names = [e["event"] for e in events]
    assert names == ["run.snapshot", "run.finished"]
    assert json.loads(events[0]["data"])["run"]["status"] == "error"
    assert json.loads(events[-1]["data"])["status"] == "error"


async def test_crash_before_publisher_still_lands_error_status(
    client, local_server, monkeypatch
):
    """publisher 构造之前的崩溃(DB 错误 / 版本缺失 / DSL 解析失败)也落 error 终态。"""
    from app.tasks import engine_tasks

    def boom(dsl):
        raise RuntimeError("DSL 炸了")

    monkeypatch.setattr(engine_tasks, "parse_dsl", boom)
    run_id = await _setup_and_run(client, f"{local_server}/json")

    events, _ = await _read_sse(client, run_id)

    names = [e["event"] for e in events]
    assert names == ["run.snapshot", "run.finished"]
    assert json.loads(events[-1]["data"])["status"] == "error"


async def test_redelivery_of_terminal_run_publishes_run_finished(client, local_server):
    """任务重投时执行已终态(如 pending 期被取消):早退前补发终态事件。

    直接查流而非重连 SSE:端点见到首条终态即关闭,第二条终态只在流里。
    """
    import redis as redis_py

    from app.core.config import get_settings
    from app.tasks.engine_tasks import execute_run

    run_id = await _setup_and_run(client, f"{local_server}/json")
    events_before, _ = await _read_sse(client, run_id)
    assert sum(1 for e in events_before if e["event"] == "run.finished") == 1

    execute_run.run(run_id)  # 重投:命中幂等早退,但补发一条终态

    client_py = redis_py.Redis.from_url(get_settings().redis_url)
    stream = client_py.xrange(f"run:events:{run_id}")
    finished = [fields for _, fields in stream if fields[b"event"] == b"run.finished"]
    assert len(finished) == 2  # 原始 + 重投补发(重复终态对客户端幂等无害)
    assert json.loads(finished[-1][b"data"])["status"] == "success"


async def test_stream_step_events_match_execution(client, local_server):
    """逐步事件与实际执行对应(重放路径验证序列):跳过的步骤只有 step.finished。"""

    def two_step_dsl(url: str) -> dict:
        return {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {"id": "s1", "name": "第一步", "type": "http",
                 "request": {"method": "GET", "url": url}},
                # 引用未定义变量 → 跳过(没有「开始」)
                {"id": "s2", "name": "第二步", "type": "http",
                 "request": {"method": "GET", "url": url + "/{{missing_var}}"}},
            ],
        }

    run_id = await _setup_and_run(client, f"{local_server}/json", dsl=two_step_dsl(f"{local_server}/json"))

    # 直接取流首 id 作游标,经重放路径验证逐步序列
    cursor = _stream_head_id(run_id)

    events, _ = await _read_sse(client, run_id, headers={"Last-Event-ID": cursor})

    names = [e["event"] for e in events]
    # 第一步:开始→结束→进度;第二步(跳过):结束→进度;末尾 run.finished
    assert names == [
        "step.started", "step.finished", "run.progress",
        "step.finished", "run.progress",
        "run.finished",
    ]

    finished_first = json.loads(events[1]["data"])
    assert finished_first["step_index"] == 0
    assert finished_first["step_name"] == "第一步"
    assert finished_first["status"] == "passed"

    skipped = json.loads(events[3]["data"])
    assert skipped["step_index"] == 1
    assert skipped["status"] == "skipped"

    progress_events = [json.loads(e["data"]) for e in events if e["event"] == "run.progress"]
    assert progress_events == [{"completed": 1, "total": 2}, {"completed": 2, "total": 2}]
