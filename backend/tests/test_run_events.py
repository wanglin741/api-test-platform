"""Redis Streams 发布器:流契约与失败兜底(架构设计 §6)。

用桩 Redis 客户端,不连真实基础设施 —— 只断言「发布器写进 Redis 的
字段与 TTL 策略」这一外部行为。
"""

import json
import logging

import pytest

from app.event_names import RUN_FINISHED, RUN_PROGRESS, RUN_STARTED, STEP_FINISHED
from app.run_events import FINISHED_TTL_SECONDS, StreamPublisher, stream_key


class FakeRedis:
    """记录 xadd/expire 调用的桩;可注入异常模拟 Redis 故障。"""

    def __init__(self) -> None:
        self.added: list[tuple[str, dict, int | None]] = []
        self.expired: list[tuple[str, int]] = []
        self.error: Exception | None = None

    def xadd(self, key, fields, maxlen=None, approximate=False):
        if self.error is not None:
            raise self.error
        self.added.append((key, dict(fields), maxlen))

    def expire(self, key, seconds):
        if self.error is not None:
            raise self.error
        self.expired.append((key, seconds))


def make_publisher(client: FakeRedis) -> StreamPublisher:
    return StreamPublisher(
        client, "0f9d1c3e-0000-0000-0000-000000000001", max_len=10_000, ttl_seconds=86_400
    )


def test_stream_key_derived_from_run_id():
    assert stream_key("abc") == "run:events:abc"


def test_publish_writes_event_and_json_data_fields():
    client = FakeRedis()
    make_publisher(client).publish(RUN_STARTED, {"total_steps": 3, "started_at": "2026-01-01T00:00:00"})

    key, fields, maxlen = client.added[0]
    assert key == "run:events:0f9d1c3e-0000-0000-0000-000000000001"
    assert fields["event"] == RUN_STARTED
    assert json.loads(fields["data"]) == {
        "total_steps": 3,
        "started_at": "2026-01-01T00:00:00",
    }
    assert maxlen == 10_000  # 长度上限封住流大小


def test_publish_renews_ttl_and_shortens_after_terminal_event():
    client = FakeRedis()
    publisher = make_publisher(client)

    publisher.publish(RUN_STARTED, {})
    assert client.expired == [("run:events:0f9d1c3e-0000-0000-0000-000000000001", 86_400)]

    publisher.publish(RUN_FINISHED, {"status": "success", "summary": {}})
    assert client.expired[-1][1] == FINISHED_TTL_SECONDS  # 终态后降到短 TTL


def test_publish_swallows_redis_failure(caplog):
    """Redis 故障不能让执行失败:吞掉并记 warning,前端自然降级轮询。"""
    client = FakeRedis()
    client.error = ConnectionError("redis down")
    with caplog.at_level(logging.WARNING):
        make_publisher(client).publish(RUN_STARTED, {"total_steps": 1})

    assert client.added == []
    assert any("发布失败" in r.message for r in caplog.records)


@pytest.mark.parametrize("payload", [{"n": 1}, {"nested": {"a": ["b"]}}, {"dt": "2026-01-01T00:00:00"}])
def test_payload_always_json_serializable(payload):
    client = FakeRedis()
    make_publisher(client).publish("any.event", payload)
    json.loads(client.added[0][1]["data"])


class FakeClock:
    """假时钟:测试里手动推进,验证限流窗口。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestProgressThrottle:
    """run.progress 的 1 秒限流是投递策略,归发布器;completed == total 恒放行。"""

    def make_throttled(self, interval_ms: int = 1000):
        client = FakeRedis()
        clock = FakeClock()
        publisher = StreamPublisher(
            client, "run-1", progress_interval_ms=interval_ms, clock=clock
        )
        return publisher, client, clock

    def test_progress_within_interval_is_suppressed(self):
        publisher, client, clock = self.make_throttled()
        publisher.publish(RUN_PROGRESS, {"completed": 1, "total": 10})
        clock.advance(0.5)
        publisher.publish(RUN_PROGRESS, {"completed": 2, "total": 10})

        assert len(client.added) == 1  # 0.5s 内的第二条被限流吞掉

    def test_progress_after_interval_passes(self):
        publisher, client, clock = self.make_throttled()
        publisher.publish(RUN_PROGRESS, {"completed": 1, "total": 10})
        clock.advance(1.0)
        publisher.publish(RUN_PROGRESS, {"completed": 2, "total": 10})

        assert len(client.added) == 2

    def test_terminal_progress_always_passes(self):
        publisher, client, clock = self.make_throttled()
        publisher.publish(RUN_PROGRESS, {"completed": 9, "total": 10})
        clock.advance(0.0)
        publisher.publish(RUN_PROGRESS, {"completed": 10, "total": 10})

        assert len(client.added) == 2  # completed == total 恒放行

    def test_first_progress_always_passes(self):
        publisher, client, _ = self.make_throttled()
        publisher.publish(RUN_PROGRESS, {"completed": 1, "total": 10})

        assert len(client.added) == 1

    def test_throttle_only_applies_to_progress_events(self):
        """其他事件不受限流:限流是 run.progress 的投递策略,不是发布器的全局行为。"""
        publisher, client, clock = self.make_throttled()
        publisher.publish(STEP_FINISHED, {"step_index": 0})
        clock.advance(0.0)
        publisher.publish(STEP_FINISHED, {"step_index": 1})

        assert len(client.added) == 2

    def test_interval_configurable(self):
        publisher, client, clock = self.make_throttled(interval_ms=100)
        publisher.publish(RUN_PROGRESS, {"completed": 1, "total": 10})
        clock.advance(0.2)
        publisher.publish(RUN_PROGRESS, {"completed": 2, "total": 10})

        assert len(client.added) == 2
