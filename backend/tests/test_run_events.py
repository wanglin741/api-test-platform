"""Redis Streams 发布器:流契约与失败兜底(架构设计 §6)。

用桩 Redis 客户端,不连真实基础设施 —— 只断言「发布器写进 Redis 的
字段与 TTL 策略」这一外部行为。
"""

import json
import logging

import pytest

from app.event_names import RUN_FINISHED, RUN_STARTED
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
