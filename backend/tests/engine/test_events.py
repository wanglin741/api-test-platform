"""逐步事件契约:引擎是唯一知道逐步事实的地方,只发逐步事件。

事件序列与载荷是引擎对订阅端的公开契约(spec #22 定案):
- 单步成功恰为「步骤开始 → 步骤结束 → 进度」,无执行级事件
- 跳过的步骤只发 step.finished 不发 step.started(跳过没有「开始」)
- 载荷字段对齐既有数据库列与步骤出参(step_index 而非 index)
- 进度事件的限流在发布器(投递策略),引擎每步都发、单测因此确定
"""

import json

import pytest

from app.engine.dsl import parse_dsl
from app.engine.runner import execute
from tests.engine.conftest import json_response, make_context

from app.engine.defaults import InMemorySink


class RecordingPublisher:
    """记录型发布器:断言事件序列与载荷。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))


def make_context_with_publisher(sink: InMemorySink, handler) -> tuple:
    publisher = RecordingPublisher()
    context = make_context(sink, handler, publisher=publisher)
    return context, publisher


def two_step_doc(**config) -> dict:
    return {
        "dsl_version": 1,
        "config": config,
        "steps": [
            {"id": "a", "name": "步骤A", "type": "http",
             "request": {"method": "GET", "url": "https://api.example.com/a"},
             "assert": [{"type": "status_code", "operator": "eq", "expected": 200}]},
            {"id": "b", "name": "步骤B", "type": "http",
             "request": {"method": "GET", "url": "https://api.example.com/b"}},
        ],
    }


def one_step_doc(**config) -> dict:
    return {**two_step_doc(**config), "steps": two_step_doc(**config)["steps"][:1]}


class TestStepEventSequence:
    async def test_single_step_success_sequence_has_no_run_level_events(self, sink) -> None:
        def handler(request):
            return json_response(200)

        context, publisher = make_context_with_publisher(sink, handler)
        await execute(parse_dsl(one_step_doc()), context)

        names = [e for e, _ in publisher.events]
        # 恰为 开始 → 结束 → 进度,无执行级事件(执行级归任务层)
        assert names == ["step.started", "step.finished", "run.progress"]

    async def test_payloads_align_with_db_columns(self, sink) -> None:
        def handler(request):
            return json_response(200)

        doc = two_step_doc()
        context, publisher = make_context_with_publisher(sink, handler)
        await execute(parse_dsl(doc), context)

        started = dict(publisher.events[0][1])
        assert started == {"step_index": 0, "step_name": "步骤A", "step_type": "http"}

        finished = dict(publisher.events[1][1])
        assert finished["step_index"] == 0
        assert finished["step_name"] == "步骤A"
        assert finished["status"] == "passed"
        assert finished["duration_ms"] >= 0
        assert finished["assertion_total"] == 1  # status_code 断言
        assert finished["assertion_passed"] == 1

        progress = dict(publisher.events[2][1])
        assert progress == {"completed": 1, "total": 2}

    async def test_payloads_json_serializable(self, sink) -> None:
        """契约守卫:每个载荷必须能过 JSON 序列化,否则发布器写流即失败。"""

        def handler(request):
            return json_response(200, {"ok": True})

        doc = two_step_doc()
        context, publisher = make_context_with_publisher(sink, handler)
        await execute(parse_dsl(doc), context)

        for event, payload in publisher.events:
            json.dumps(payload, ensure_ascii=False)


class TestSkippedStepEvents:
    async def test_fail_fast_skips_publish_finished_without_started(self, sink) -> None:
        def handler(request):
            return json_response(500, {"msg": "boom"})

        doc = two_step_doc(fail_fast=True)
        context, publisher = make_context_with_publisher(sink, handler)
        await execute(parse_dsl(doc), context)

        names = [e for e, _ in publisher.events]
        # 步骤A:开始+结束;步骤B(fail_fast 跳过):只发结束;每步都有进度
        assert names == [
            "step.started", "step.finished", "run.progress",
            "step.finished", "run.progress",
        ]
        skipped = dict(publisher.events[3][1])
        assert skipped["step_index"] == 1
        assert skipped["status"] == "skipped"

    async def test_disabled_step_publishes_finished_without_started(self, sink) -> None:
        def handler(request):
            return json_response(200)

        doc = two_step_doc()
        doc["steps"][0]["enabled"] = False
        context, publisher = make_context_with_publisher(sink, handler)
        await execute(parse_dsl(doc), context)

        names = [e for e, _ in publisher.events]
        assert names == [
            "step.finished", "run.progress",
            "step.started", "step.finished", "run.progress",
        ]

    async def test_progress_reaches_total_on_cancellation(self, sink) -> None:
        """取消后其余步骤标记 skipped,进度仍推进到总数(客户端至少看到终态进度)。"""

        class AlwaysCancelled:
            def cancelled(self) -> bool:
                return True

        def handler(request):
            return json_response(200)

        doc = two_step_doc()
        context, publisher = make_context_with_publisher(sink, handler)
        context.cancellation = AlwaysCancelled()
        await execute(parse_dsl(doc), context)

        progress = [p for e, p in publisher.events if e == "run.progress"]
        assert progress[-1] == {"completed": 2, "total": 2}


@pytest.mark.parametrize("step_index", [0, 1])
async def test_step_type_matches_dsl(sink, step_index) -> None:
    """step_type 来自 DSL 的 type 字段(与落库的 step_type 列同源)。"""

    def handler(request):
        return json_response(200)

    doc = two_step_doc()
    context, publisher = make_context_with_publisher(sink, handler)
    await execute(parse_dsl(doc), context)

    started = dict(publisher.events[step_index * 3][1])
    assert started["step_type"] == "http"
