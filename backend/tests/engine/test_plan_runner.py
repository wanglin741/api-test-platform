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

# ---------- 并行(#32)----------


async def test_parallel_respects_concurrency_limit(sink: InMemorySink) -> None:
    import asyncio

    state = {"active": 0, "max_active": 0}

    async def handler(request):
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return json_response(200)

    context, _ = _make(sink, handler)
    summary = await execute_plan(
        _plan([(i, f"用例{i}", _case_doc(_step(f"s{i}", f"步骤{i}"))) for i in range(6)]),
        context,
        mode="parallel",
        concurrency=2,
    )

    assert state["max_active"] == 2  # 受并发上限约束,且确实并行了(不是串行)
    assert summary.status == "success"
    assert summary.total_steps == 6 and summary.passed == 6
    # gather 保序:outcomes 按清单位置排列
    assert [o.case_index for o in summary.cases] == list(range(6))


async def test_parallel_stop_on_fail_lets_started_cases_finish(sink: InMemorySink) -> None:
    import asyncio

    async def handler(request):
        # 失败用例耗时长(0.15):快用例 0.1 一个 —— 并发 2 下第二个名额
        # 在 0.2 才轮到第 4 个用例,此时慢用例(0.15)已失败置停 → 只拦它
        if "a1" in str(request.url):
            await asyncio.sleep(0.15)
        else:
            await asyncio.sleep(0.1)
        return json_response(200)

    context, publisher = _make(sink, handler)
    summary = await execute_plan(
        _plan(
            [
                (11, "慢失败", _case_doc(_step("a1", "打开登录", expected=500))),
                (12, "快一", _case_doc(_step("b1", "快一"))),
                (13, "快二", _case_doc(_step("c1", "快二"))),
                (14, "未开始", _case_doc(_step("d1", "未开始"))),
            ]
        ),
        context,
        stop_on_fail=True,
        mode="parallel",
        concurrency=2,
    )

    step_names = [dict(p)["step_name"] for e, p in publisher.events if e == "step.started"]
    # 已开始的用例自然跑完(不强杀);未开始的只记归属不发事件
    assert step_names == ["打开登录", "快一", "快二"]
    assert [(o.case_index, o.status) for o in summary.cases] == [
        (0, "failed"),
        (1, "success"),
        (2, "success"),
        (3, "skipped"),
    ]
    assert summary.status == "failed"
    assert summary.total_steps == 3 and summary.failed == 1 and summary.passed == 2


async def test_parallel_progress_advances_to_total(sink: InMemorySink) -> None:
    import asyncio

    async def handler(request):
        await asyncio.sleep(0.01)
        return json_response(200)

    context, publisher = _make(sink, handler)
    summary = await execute_plan(
        _plan(
            [
                (11, "A", _case_doc(_step("a1", "A1"), _step("a2", "A2"))),
                (12, "B", _case_doc(_step("b1", "B1"))),
            ]
        ),
        context,
        mode="parallel",
        concurrency=2,
    )

    progresses = [dict(p) for e, p in publisher.events if e == "run.progress"]
    # 并行下进度仍推进到总数,completed 值两两不同(每步占一个唯一刻度)
    assert len(progresses) == 3
    completed = sorted(p["completed"] for p in progresses)
    assert completed == [1, 2, 3]
    assert all(p["total"] == 3 for p in progresses)


async def test_cancel_wins_over_stop_on_fail(sink: InMemorySink) -> None:
    cancellation = FlippableCancellation()

    def handler(request):
        return json_response(200)

    context, publisher = _make(sink, handler, cancellation)
    original = publisher.publish

    def flip_after_first_step(event: str, payload: dict) -> None:
        if event == "run.progress" and payload.get("completed") == 1:
            cancellation.flag = True
        original(event, payload)

    publisher.publish = flip_after_first_step

    summary = await execute_plan(
        _plan(
            [
                (11, "A", _case_doc(_step("a1", "A1", expected=500))),
                (12, "B", _case_doc(_step("b1", "B1"))),
                (13, "C", _case_doc(_step("c1", "C1"))),
            ]
        ),
        context,
        stop_on_fail=True,
    )

    # 取消优先:状态是 stopped 而不是 failed;剩余用例全部跳过
    assert summary.status == "stopped"
    assert [(o.case_index, o.status) for o in summary.cases] == [
        (0, "failed"),
        (1, "skipped"),
        (2, "skipped"),
    ]
