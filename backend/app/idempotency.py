"""Idempotency-Key 幂等触发(spec #35 决策 #7)。

Redis 键 `idem:{project_id}:{principal}:{sha256(header)}`:项目与调用主体
进键名,同一字符串在不同项目/凭据之间互不干扰(防跨项目/跨凭据碰撞)。
值是一条 UTF-8 JSON:

- pending:`{"state":"pending","fingerprint":...,"nonce":...}`,SETNX 抢锁,
  短 TTL 只管「校验 + 建 run」这段窗口,进程崩溃也不会永久占坑;
- done:`{"state":"done","fingerprint":...,"run_id":...}`,run 提交后由 Lua
  按 nonce CAS 写入并保留 24 小时;非本次请求持有的 pending 不覆盖。

fingerprint 是目标字段(用例/计划/环境)的规范化 JSON 哈希 —— 同 key
不同请求体必须 409,不能悄悄返回不匹配的旧 run。业务校验失败由调用方
abort:只删自己持有(同 nonce)的 pending,修正后同一 key 可重试。
"""

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass

import redis.asyncio as redis_async

KEY_PREFIX = "idem:"
MAX_KEY_LENGTH = 255
PENDING_TTL_SECONDS = 60
DONE_TTL_SECONDS = 24 * 60 * 60
POLL_INTERVAL_SECONDS = 0.1
POLL_ATTEMPTS = 10
RETRY_AFTER_SECONDS = 1

# GET → 校验 nonce → 覆盖为 done。pending 已过期并被别的请求重抢时 CAS 失败,
# 绝不覆盖后来者的锁。
_MARK_DONE_LUA = """
local current = redis.call('GET', KEYS[1])
if not current then return 0 end
local ok, data = pcall(cjson.decode, current)
if not ok or data['nonce'] ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""

# 只删自己持有的 pending:不误删后来者重新抢到的锁。
_ABORT_LUA = """
local current = redis.call('GET', KEYS[1])
if not current then return 0 end
local ok, data = pcall(cjson.decode, current)
if not ok or data['nonce'] ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
"""


@dataclass
class AcquireResult:
    """acquire 的四种结果:首次持有 / 重放 / 同键不同请求 / 并发进行中。"""

    state: str  # acquired | replay | reused | in_progress
    run_id: uuid.UUID | None = None
    nonce: str | None = None


def make_client(settings) -> redis_async.Redis:
    return redis_async.Redis.from_url(settings.redis_url, decode_responses=True)


def principal_key(principal) -> str:
    """调用主体:Token id 或用户 id(Token 触发不归属个人,但幂等隔离按凭据)。"""
    if principal.token is not None:
        return f"token:{principal.token.id}"
    return f"user:{principal.user.id}"


def idempotency_key(project_id: int, principal, header_value: str) -> str:
    digest = hashlib.sha256(header_value.encode()).hexdigest()
    return f"{KEY_PREFIX}{project_id}:{principal_key(principal)}:{digest}"


def request_fingerprint(payload) -> str:
    """规范化目标字段(排序 + 紧凑 JSON)后取哈希;同 key 换 body 即 409。

    门禁参数也是执行语义的一部分:同 key 改门禁阈值必须 409,不能悄悄
    重放一个按旧阈值求值的 run。
    """
    target = {
        "case_id": payload.case_id,
        "plan_id": payload.plan_id,
        "environment_id": payload.environment_id,
        "quality_gate": (
            payload.quality_gate.model_dump(exclude_none=True)
            if payload.quality_gate is not None
            else None
        ),
    }
    canonical = json.dumps(target, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _decode(raw: str) -> dict | None:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def acquire(client: redis_async.Redis, key: str, fingerprint: str) -> AcquireResult:
    """SETNX 抢 pending;已有值时轮询等待并发请求落 done。

    Redis 异常不在这里吞:调用方按 fail closed 映射 503。
    """
    nonce = uuid.uuid4().hex
    pending = json.dumps(
        {"state": "pending", "fingerprint": fingerprint, "nonce": nonce}
    )
    # 轮询上限 = POLL_ATTEMPTS;多给一轮给「键在 SETNX 与 GET 之间过期」的重抢
    for attempt in range(POLL_ATTEMPTS + 2):
        if await client.set(key, pending, nx=True, ex=PENDING_TTL_SECONDS):
            return AcquireResult("acquired", nonce=nonce)
        raw = await client.get(key)
        if raw is None:
            continue
        state = _decode(raw)
        if state is None or state.get("fingerprint") != fingerprint:
            return AcquireResult("reused")
        if state.get("state") == "done":
            try:
                return AcquireResult("replay", run_id=uuid.UUID(state["run_id"]))
            except (KeyError, TypeError, ValueError):
                return AcquireResult("reused")
        if attempt < POLL_ATTEMPTS:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return AcquireResult("in_progress")


async def mark_done(
    client: redis_async.Redis, key: str, fingerprint: str, nonce: str, run_id: uuid.UUID
) -> bool:
    """run 提交后写 done 并续期 24 小时;CAS 失败说明 pending 已不属于本次请求。"""
    done = json.dumps(
        {"state": "done", "fingerprint": fingerprint, "run_id": str(run_id)}
    )
    result = await client.eval(
        _MARK_DONE_LUA, 1, key, nonce, done, str(DONE_TTL_SECONDS)
    )
    return bool(result)


async def abort(client: redis_async.Redis, key: str, nonce: str) -> bool:
    """业务校验失败:释放本次请求持有的 pending,不消耗 key。"""
    result = await client.eval(_ABORT_LUA, 1, key, nonce)
    return bool(result)


__all__ = [
    "AcquireResult",
    "DONE_TTL_SECONDS",
    "KEY_PREFIX",
    "MAX_KEY_LENGTH",
    "PENDING_TTL_SECONDS",
    "RETRY_AFTER_SECONDS",
    "abort",
    "acquire",
    "idempotency_key",
    "make_client",
    "mark_done",
    "request_fingerprint",
]
