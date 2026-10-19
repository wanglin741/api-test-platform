"""HTTP 步骤执行器:构建请求(变量解析)→ 发送 → 提取 → 断言 → 结果。

设计文档《执行引擎》:
- 请求构建阶段遇到变量解析失败 → 步骤 skipped(不污染报告)
- 只有网络类 TransportError 重试(§9);重试时重新求值变量;断言 failed 不重试
- 取消是协作式的:重试退避期间分段 sleep、每 0.2s 查一次 token(§10)
- 响应体截断在 T5 接入流式读取
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import orjson

from app.engine.assertions import evaluate_all
from app.engine.context import EngineContext
from app.engine.dsl import RequestBody, StepSpec
from app.engine.extractors import ExtractionFailure, extract_value
from app.engine.results import CaseRef, RequestSnapshot, ResponseSnapshot, StepResult
from app.engine.variables import VariableResolutionError, VariableScope, stringify
from app.event_names import STEP_STARTED

CANCEL_POLL_INTERVAL = 0.2  # 分段 sleep 的检查粒度(§10)
CONNECT_POOL_TIMEOUT = 5.0  # 连接 / 连接池超时上限(§9 超时层次)


@dataclass
class PreparedBody:
    content: bytes | None = None
    files: dict[str, tuple[None, str]] | None = None  # 纯文本 multipart 用 files 形式
    snapshot: str | None = None
    content_type: str | None = None


def build_request_timeout(
    *, step_timeout_ms: int | None, default_timeout_ms: int
) -> httpx.Timeout:
    """单步超时可覆盖用例默认;连接与连接池固定 5s(§9 超时层次表)。"""
    total = (step_timeout_ms or default_timeout_ms) / 1000
    return httpx.Timeout(total, connect=CONNECT_POOL_TIMEOUT, pool=CONNECT_POOL_TIMEOUT)


def build_body(body: RequestBody, scope: VariableScope) -> PreparedBody:
    if body.content is None or (isinstance(body.content, str) and body.content.strip() == ""):
        # 声明了 body 类型但没有内容:不发请求体
        # (否则 json 会发出字面量 "null"/orjson.loads('') 报错、raw 发出 "None";
        #  前端表单清空文本时给的是空串,同样按无内容处理)
        return PreparedBody()

    if body.type == "json":
        # 先 parse 再 resolve:整体占位符的值替换进已解析结构,才能保留
        # 原生类型({"id": "{{user_id}}"} + user_id=5 → 5 而非 "5")
        if isinstance(body.content, str):
            data = scope.resolve(orjson.loads(body.content))
        else:
            data = scope.resolve(body.content)
        payload = orjson.dumps(data).decode("utf-8")
        return PreparedBody(content=payload.encode("utf-8"), snapshot=payload, content_type="application/json")

    if body.type == "raw":
        resolved = scope.resolve(body.content)
        text = resolved if isinstance(resolved, str) else str(resolved)
        return PreparedBody(content=text.encode("utf-8"), snapshot=text)

    # form-data / urlencoded 的 content 是键值对,值统一转字符串
    resolved_pairs = scope.resolve(body.content)
    flat = {k: stringify(v) for k, v in resolved_pairs.items()}
    if body.type == "urlencoded":
        text = urlencode(flat)
        return PreparedBody(
            content=text.encode("utf-8"),
            snapshot=text,
            content_type="application/x-www-form-urlencoded",
        )
    return PreparedBody(files={k: (None, v) for k, v in flat.items()}, snapshot=None)


async def _cancellable_sleep(seconds: float, context: EngineContext) -> bool:
    """分段 sleep,每 0.2s 查一次取消 token。返回 True 表示已取消。"""
    deadline = time.perf_counter() + seconds
    while True:
        if context.cancellation.cancelled():
            return True
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(CANCEL_POLL_INTERVAL, remaining))


async def run_http_step(
    step: StepSpec,
    scope: VariableScope,
    context: EngineContext,
    client: httpx.AsyncClient,
    default_timeout_ms: int,
    step_index: int = 0,
    case_ref: CaseRef | None = None,
) -> StepResult:
    result = StepResult(step_id=step.id, step_name=step.name, step_index=step_index, status="passed")

    if not step.enabled:
        result.status = "skipped"
        result.error_message = "步骤已禁用 (enabled: false)"
        return result

    timeout = build_request_timeout(
        step_timeout_ms=step.request.timeout_ms, default_timeout_ms=default_timeout_ms
    )
    attempt = 0
    while True:
        attempt += 1
        result.attempts = attempt
        result.status = "passed"
        result.error_message = None

        # ── 构建阶段:变量解析失败 → skipped(每次重试重新求值,§9)──
        try:
            url = scope.resolve(step.request.url)
            if not isinstance(url, str):
                raise VariableResolutionError(f"URL 解析结果不是字符串: {url!r}")
            headers = {k: stringify(scope.resolve(v)) for k, v in step.request.headers.items()}
            params = {k: stringify(scope.resolve(v)) for k, v in step.request.params.items()}
            prepared = build_body(step.request.body, scope) if step.request.body else PreparedBody()
        except VariableResolutionError as exc:
            result.status = "skipped"
            result.error_message = str(exc)
            return result
        except orjson.JSONDecodeError as exc:
            # DSL 模板不是合法 JSON —— 保存时校验应拦住(T7),这里兜底归类
            result.status = "error"
            result.error_message = f"请求体不是合法 JSON: {exc}"
            return result

        # 步骤真正开始(构建成功、即将发请求)才发 step.started:跳过与
        # 构建失败都没有「开始」;重试不重发。事件归属引擎(逐步事实),
        # 载荷字段与落库列同源(step_index/step_name/step_type)
        if attempt == 1:
            started_payload = {
                "step_index": step_index,
                "step_name": step.name,
                "step_type": step.type,
            }
            if case_ref is not None:
                started_payload["case_index"] = case_ref.case_index
                started_payload["case_name"] = case_ref.case_name
            context.publisher.publish(STEP_STARTED, started_payload)

        if prepared.content_type and not any(
            k.lower() == "content-type" for k in headers
        ):
            headers = {**headers, "Content-Type": prepared.content_type}

        result.request = RequestSnapshot(
            method=step.request.method,
            url=url,
            headers=headers,
            query=params,
            body=prepared.snapshot,
        )

        # ── 发送阶段:网络异常 → error,按 RetrySpec 重试 ─────────────
        kwargs: dict[str, Any] = {}
        if prepared.files:
            kwargs["files"] = prepared.files
        elif prepared.content is not None:
            kwargs["content"] = prepared.content

        start = time.perf_counter()
        try:
            async with client.stream(
                step.request.method, url, headers=headers, params=params,
                follow_redirects=step.request.follow_redirects,
                timeout=timeout, **kwargs,
            ) as response:
                # 流式读取:累计到 body_max_bytes 即停止并关闭连接(§11 硬要求,
                # 先整读进内存再判断会让超大响应 OOM worker)
                body_bytes = bytearray()
                truncated = False
                async for chunk in response.aiter_bytes():
                    if len(body_bytes) + len(chunk) > context.body_max_bytes:
                        body_bytes += chunk[: context.body_max_bytes - len(body_bytes)]
                        truncated = True
                        break
                    body_bytes += chunk
        except httpx.TransportError as exc:
            result.status = "error"
            result.error_message = f"{type(exc).__name__}: {exc}"
            result.duration_ms = (time.perf_counter() - start) * 1000
            if attempt <= step.retry.times and "error" in step.retry.on:
                delay_ms = step.retry.interval_ms
                if step.retry.backoff == "exponential":
                    delay_ms *= 2 ** (attempt - 1)
                cancelled = await _cancellable_sleep(delay_ms / 1000, context)
                if cancelled:
                    return result  # 退避期间收到取消:停止重试,终态由 run 汇总为 stopped
                continue
            return result
        result.duration_ms = (time.perf_counter() - start) * 1000
        break

    # form-data 经 httpx 编码后的真实请求体在 resp.request 里
    snapshot_body = prepared.snapshot
    if snapshot_body is None and prepared.files is not None:
        snapshot_body = response.request.content.decode("utf-8", errors="replace")

    result.response = ResponseSnapshot(
        status_code=response.status_code,
        headers=dict(response.headers.items()),
        body=bytes(body_bytes).decode("utf-8", errors="replace"),
        truncated=truncated,
    )
    result.request.body = snapshot_body

    # ── 提取(断言执行前,§4)──────────────────────────────────────
    extraction_errors: list[str] = []
    for extractor in step.extract:
        try:
            value = extract_value(extractor, result.response)
            scope.set(extractor.name, value)
            result.extracted[extractor.name] = value
        except ExtractionFailure as exc:
            if extractor.required:
                extraction_errors.append(str(exc))
            # required: false → 变量不注入,后续引用它的步骤会 skipped

    # ── 断言:全部执行,不短路(§5)────────────────────────────────
    result.assertions = evaluate_all(step.assertions, result.response, result.duration_ms)

    failures = extraction_errors + [a.message for a in result.assertions if not a.passed]
    if failures:
        result.status = "failed"
        result.error_message = "; ".join(failures)
    return result
