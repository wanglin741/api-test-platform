"""执行进度事件的 Redis Streams 契约与发布器(架构设计 §6)。

通道用 Streams 而非 Pub/Sub:Pub/Sub 至多一次投递,刷新页面、网络抖动、
打开已跑一半的执行都会丢事件。流键与取消标志键(run:cancel:{id})同构,
从执行 id 派生;Redis 里的一切都必须可重建 —— 任何时刻丢失都能从数据库
重新推出当前状态,所以裁剪(XADD maxlen)与过期(TTL)策略可以激进,
也兜得住「worker 被强杀、永不发终态」的孤儿流。

发布失败一律吞掉并记 warning:事件通道只是进度展示,执行与报告落库
不依赖它。

放应用根层而非任务层/API 层:契约被两侧共用,放任一侧都会造成反向依赖;
也不能放引擎层 —— 引擎不 import 框架/Redis。
"""

import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

import redis as redis_py

from app.event_names import RUN_FINISHED, RUN_PROGRESS

logger = logging.getLogger(__name__)

# 终态后事件不再增长,流保留到刚打开的历史报告页还能补一次终态即可
FINISHED_TTL_SECONDS = 3600


def stream_key(run_id: uuid.UUID | str) -> str:
    return f"run:events:{run_id}"


def finished_payload(status: str, summary: Any) -> dict[str, Any]:
    """run.finished 的载荷:终态状态 + 汇总统计(与报告详情接口同一份 summary)。

    终态事件有五个发射点(任务层正常/兜底 ×2、SSE 快照补发/空闲查库补发),
    载荷形状只在这里定义一次,加字段不致各处漂移。
    """
    return {"status": status, "summary": summary}


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
        progress_interval_ms: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._key = stream_key(run_id)
        self._max_len = max_len
        self._ttl_seconds = ttl_seconds
        self._progress_interval_ms = progress_interval_ms
        self._clock = clock
        # -inf:第一条进度必发;步骤串行 ⇒ 发布无并发,限流状态无需加锁
        self._last_progress_at = float("-inf")

    def publish(self, event: str, payload: dict[str, Any]) -> None:
        if not self._progress_allowed(event, payload):
            return
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

    def _progress_allowed(self, event: str, payload: dict[str, Any]) -> bool:
        """run.progress 的限流是投递策略,不是引擎逻辑:引擎每步都发。
        completed == total 恒放行 —— 客户端至少能看到终态进度。
        """
        if event != RUN_PROGRESS:
            return True
        if payload.get("completed") == payload.get("total"):
            return True
        now = self._clock()
        if (now - self._last_progress_at) * 1000 < self._progress_interval_ms:
            return False
        self._last_progress_at = now
        return True
