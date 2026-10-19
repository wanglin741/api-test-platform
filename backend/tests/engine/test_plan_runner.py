"""计划执行器(#30):串行多用例编排,纯库,事件与落库带用例归属。

契约(spec #28 定案):
- step.* 事件载荷带 case_index / case_name;run.progress 保持步骤级
  连续计数(全计划 completed/total,不随用例切换重置)
- stop_on_fail / 取消:未开始的用例只记归属(outcome),不发步骤事件
  —— 跳过没有「开始」
- 计划级 summary = 用例级求和,同一份字段形状
"""

import json

from app.engine.dsl import parse_dsl
from app.engine.plan_runner import PlanCase, execute_plan
from app.engine.runner import execute  # noqa: F401  (单用例入口不动,导入防回归)
from tests.engine.conftest import json_response, make_context

from app.engine.defaults import InMemorySink

from tests.engine.test_events import RecordingPublisher


def _step(step_id: str, name: str, expected: int = 200) -> dict:
    return {
        "id": step_id,
        "name": name,
        "type": "http",
        "request": {"method": "GET", "url": f"https://api.example.com/{step_id}"},
        "assert": [{"type": "status_code", "operator": "eq", "expected": expected}],
    }


def _case_doc(*steps: dict) -> dict:
    return {"dsl_version": 1, "config": {}, "steps": list(steps)}


def _plan(cases: list[tuple[int, str, dict]]) -> list[PlanCase]:
    return [PlanCase(case_id=cid, case_name=name, dsl=parse_dsl(doc)) for cid, name, doc in cases]


class FlippableCancellation:
    """测试用取消令牌:置位后保持取消。"""

    def __init__(self) -> None:
        self.flag = False

    def cancelled(self) -> bool:
        return self.flag


def _make(sink: InMemorySink, handler, cancellation=None):
    publisher = RecordingPublisher()
    kwargs = {"publisher": publisher}
    if cancellation is not None:
        kwargs["cancellation"] = cancellation
    context = make_context(sink, handler, **kwargs)
    return context, publisher


async def test_serial_events_carry_case_attribution(sink: InMemorySink) -> None:
    def handler(request):
        return json_response(200)

    context, publisher = _make(sink, handler)
    summary = await execute_plan(
        _plan(
            [
                (11, "登录", _case_doc(_step("a1", "打开登录"), _step("a2", "提交"))),
                (12, "下单", _case_doc(_step("b1", "下单"))),
            ]
        ),
        context,
    )

    names = [e for e, _ in publisher.events]
    # 用例 A 两步 + 用例 B 一步:每步 开始 → 结束 → 进度
    assert names.count("step.started") == 3
    assert names.count("step.finished") == 3
    assert names.count("run.progress") == 3

    first_started = dict(publisher.events[0][1])
    assert first_started["case_index"] == 0
    assert first_started["case_name"] == "登录"

    # 进度连续计数:不随用例切换重置,total = 全计划步骤数
    progresses = [dict(p) for e, p in publisher.events if e == "run.progress"]
    assert [p["completed"] for p in progresses] == [1, 2, 3]
    assert all(p["total"] == 3 for p in progresses)

    # 最后一步归属用例 B
    last_finished = dict(publisher.events[-2][1])
    assert last_finished["case_index"] == 1
    assert last_finished["case_name"] == "下单"

    # 计划级 summary = 用例级求和
    assert summary.total_steps == 3
    assert summary.passed == 3
    assert summary.failed == 0
    assert summary.status == "success"


async def test_sink_rows_carry_case_attribution(sink: InMemorySink) -> None:
    def handler(request):
        return json_response(200)

    context, _ = _make(sink, handler)
    await execute_plan(
        _plan(
            [
                (11, "登录", _case_doc(_step("a1", "打开登录"))),
                (12, "下单", _case_doc(_step("b1", "下单"))),
            ]
        ),
        context,
    )

    assert [(r.case_index, r.case_id) for r in sink.results] == [(0, 11), (1, 12)]


async def test_stop_on_fail_skips_remaining_cases_without_step_events(sink: InMemorySink) -> None:
    def handler(request):
        return json_response(200)  # 断言期望 500 → 失败

    context, publisher = _make(sink, handler)
    summary = await execute_plan(
        _plan(
            [
                (11, "登录", _case_doc(_step("a1", "打开登录", expected=500))),
                (12, "下单", _case_doc(_step("b1", "下单"))),
            ]
        ),
        context,
        stop_on_fail=True,
    )

    # 被跳用例无步骤事件(只记归属);失败用例自身的步骤事件在
    step_names = [dict(p)["step_name"] for e, p in publisher.events if e == "step.started"]
    assert step_names == ["打开登录"]

    assert summary.status == "failed"
    assert summary.total_steps == 1  # 只有失败用例的步骤计入求和
    assert [(o.case_index, o.status) for o in summary.cases] == [(0, "failed"), (1, "skipped")]


async def test_cancel_skips_remaining_cases_to_stopped(sink: InMemorySink) -> None:
    cancellation = FlippableCancellation()

    def handler(request):
        return json_response(200)

    context, publisher = _make(sink, handler, cancellation)
    # 第一个用例跑完后置位取消
    original = publisher.publish

    def flip_after_first_case(event: str, payload: dict) -> None:
        if event == "run.progress" and payload.get("completed") == 1:
            cancellation.flag = True
        original(event, payload)

    publisher.publish = flip_after_first_case

    summary = await execute_plan(
        _plan(
            [
                (11, "登录", _case_doc(_step("a1", "打开登录"))),
                (12, "下单", _case_doc(_step("b1", "下单"))),
                (13, "支付", _case_doc(_step("c1", "支付"))),
            ]
        ),
        context,
    )

    step_names = [dict(p)["step_name"] for e, p in publisher.events if e == "step.started"]
    assert step_names == ["打开登录"]
    assert summary.status == "stopped"
    # 用例 A 在取消置位前已跑完;取消只在用例边界检查,已完成的保持原状
    assert [(o.case_index, o.status) for o in summary.cases] == [
        (0, "success"),
        (1, "skipped"),
        (2, "skipped"),
    ]


async def test_payloads_are_json_serializable(sink: InMemorySink) -> None:
    def handler(request):
        return json_response(200)

    context, publisher = _make(sink, handler)
    await execute_plan(
        _plan([(11, "登录", _case_doc(_step("a1", "打开登录"))), (12, "下单", _case_doc(_step("b1", "下单")))]),
        context,
    )

    for event, payload in publisher.events:
        dumped = json.dumps(payload, ensure_ascii=False)
        assert isinstance(dumped, str)


async def test_empty_plan_returns_zero_summary(sink: InMemorySink) -> None:
    def handler(request):
        return json_response(200)

    context, publisher = _make(sink, handler)
    summary = await execute_plan([], context)

    assert summary.total_steps == 0
    assert summary.status == "success"
    assert summary.cases == []
    assert publisher.events == []
