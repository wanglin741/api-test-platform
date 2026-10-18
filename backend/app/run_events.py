"""执行进度事件的 Redis Streams 契约与发布器(架构设计 §6)。

通道用 Streams 而非 Pub/Sub:Pub/Sub 至多一次投递,刷新页面、网络抖动、
打开已跑一半的执行都会丢事件。流键与取消标志键(run:cancel:{id})同构,
从执行 id 派生;Redis 里的一切都必须可重建 —— 任何时刻丢失都能从数据库
重新推出当前状态,所以裁剪(XADD maxlen)与过期(TTL)策略可以激进,
也兜得住「worker 被强杀、永不发终态」的孤儿流。

发布失败一律吞掉并记 warning:事件通道只是进度展示,执行与报告落库
不依赖它,前端自然降级为轮询。

放应用根层而非任务层/API 层:契约被两侧共用,放任一侧都会造成反向依赖;
也不能放引擎层 —— 引擎不 import 框架/Redis。
"""

import json
import logging
import uuid
from typing import Any

import redis as redis_py

from app.event_names import RUN_FINISHED

logger = logging.getLogger(__name__)

# 终态后事件不再增长,流保留到刚打开的历史报告页还能补一次终态即可
FINISHED_TTL_SECONDS = 3600


def stream_key(run_id: uuid.UUID | str) -> str:
    return f"run:events:{run_id}"


class StreamPublisher:
    """同步 Redis 客户端:引擎协程跑在独立线程的一次性事件循环里,
    与 RedisCancellationToken 同构;连接池线程安全,worker 主线程
    (执行级事件)与引擎线程(逐步事件)可共用同一个客户端。
    """

    def __init__(
        self,
        client: redis_py.Redis,
        run_id: uuid.UUID | str,
        *,
        max_len: int = 10_000,
        ttl_seconds: int = 86_400,
    ) -> None:
        self._client = client
        self._key = stream_key(run_id)
        self._max_len = max_len
        self._ttl_seconds = ttl_seconds

    def publish(self, event: str, payload: dict[str, Any]) -> None:
        try:
            self._client.xadd(
                self._key,
                {"event": event, "data": json.dumps(payload, ensure_ascii=False, default=str)},
                maxlen=self._max_len,
                approximate=True,
            )
            self._client.expire(
                self._key,
                FINISHED_TTL_SECONDS if event == RUN_FINISHED else self._ttl_seconds,
            )
        except Exception:
            logger.warning(
                "run %s 进度事件 %s 发布失败,前端将降级轮询", self._key, event, exc_info=True
            )
