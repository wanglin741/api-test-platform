"""SSE 实时进度事件流(架构设计 §6 / API设计 §5):真实 PG + 真实 Redis + eager Celery。

覆盖 #23 骨干契约:事件序列与载荷、终态即关、响应头、404、崩溃兜底。
快照补偿 / Last-Event-ID 续传 / 过滤参数由后续票覆盖。
"""

import json
import uuid
from datetime import datetime


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


async def _read_sse(client, run_id: str) -> tuple[list[dict], dict[str, str]]:
    """消费事件流直到连接自然关闭;返回 (事件列表, 响应头)。用超时包住防挂死。"""
    import asyncio

    events: list[dict] = []
    headers: dict[str, str] = {}
    current: dict = {}
    async with client.stream("GET", f"/api/v1/runs/{run_id}/events") as resp:
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


async def test_stream_replays_events_and_closes_after_finished(client, local_server):
    run_id = await _setup_and_run(client, f"{local_server}/json")

    events, headers = await _read_sse(client, run_id)

    # 三个响应头;text/event-stream 由 EventSourceResponse 自带
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["cache-control"] == "no-cache"
    assert headers["x-accel-buffering"] == "no"

    names = [e["event"] for e in events]
    assert names[0] == "run.started"
    assert names[-1] == "run.finished"
    assert "step.finished" in names  # 逐步事件(载荷契约在引擎票里收紧)
    # 事件的 id 就是 Redis Stream 消息 id
    assert all(e.get("id") for e in events)

    started = json.loads(events[0]["data"])
    assert started["total_steps"] == 1
    db_run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    # started_at 与数据库一致(同一时刻,格式可能差 Z/offset)
    assert _ts(started["started_at"]) == _ts(db_run["started_at"])

    finished = json.loads(events[-1]["data"])
    assert finished["status"] == db_run["status"] == "success"
    # summary 与报告详情接口返回的同一份
    assert finished["summary"] == db_run["summary"]

    # 收到终态后连接自然关闭(消费循环能结束本身就是断言)


async def test_stream_unknown_run_404(client):
    resp = await client.get(f"/api/v1/runs/{uuid.uuid4()}/events")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "run_not_found"


async def test_crash_fallback_still_publishes_run_finished(
    client, local_server, monkeypatch
):
    """引擎意外崩溃的兜底路径也发终态事件:订阅端才知道该收尾,不会永久挂起。"""
    from app.tasks import engine_tasks

    async def boom(dsl, context):
        raise RuntimeError("引擎炸了")

    monkeypatch.setattr(engine_tasks, "engine_execute", boom)
    run_id = await _setup_and_run(client, f"{local_server}/json")

    events, _ = await _read_sse(client, run_id)

    names = [e["event"] for e in events]
    assert names[0] == "run.started"  # 崩溃前已发布
    assert names[-1] == "run.finished"
    assert json.loads(events[-1]["data"])["status"] == "error"


async def test_crash_before_publisher_still_publishes_run_finished(
    client, local_server, monkeypatch
):
    """publisher 构造之前的崩溃(DB 错误 / 版本缺失 / DSL 解析失败)也补发终态。"""
    from app.tasks import engine_tasks

    def boom(dsl):
        raise RuntimeError("DSL 炸了")

    monkeypatch.setattr(engine_tasks, "parse_dsl", boom)
    run_id = await _setup_and_run(client, f"{local_server}/json")

    events, _ = await _read_sse(client, run_id)

    names = [e["event"] for e in events]
    assert names == ["run.finished"]  # 崩溃在 run.started 之前,只有终态
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
    """逐步事件与实际执行对应:跳过的步骤只有 step.finished,进度推进到总数。"""

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

    events, _ = await _read_sse(client, run_id)

    names = [e["event"] for e in events if e["event"] != "run.started"]
    # 第一步:开始→结束→进度;第二步(跳过):结束→进度;末尾 run.finished
    assert names == [
        "step.started", "step.finished", "run.progress",
        "step.finished", "run.progress",
        "run.finished",
    ]

    finished_first = json.loads(events[2]["data"])
    assert finished_first["step_index"] == 0
    assert finished_first["step_name"] == "第一步"
    assert finished_first["status"] == "passed"

    skipped = json.loads(events[4]["data"])
    assert skipped["step_index"] == 1
    assert skipped["status"] == "skipped"

    progress_events = [json.loads(e["data"]) for e in events if e["event"] == "run.progress"]
    assert progress_events == [{"completed": 1, "total": 2}, {"completed": 2, "total": 2}]
