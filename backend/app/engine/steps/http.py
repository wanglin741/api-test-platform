"""HTTP 步骤执行器:构建请求(变量解析)→ 发送 → 产出请求/响应快照。

设计文档《执行引擎》:
- 请求构建阶段遇到变量解析失败 → 步骤 skipped(不污染报告)
- 网络异常 → 步骤 error(T4 在此接重试)
- 响应体截断在 T5 接入流式读取
"""

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
from app.engine.results import RequestSnapshot, ResponseSnapshot, StepResult
from app.engine.variables import VariableResolutionError, VariableScope, stringify


@dataclass
class PreparedBody:
    content: bytes | None = None
    files: dict[str, tuple[None, str]] | None = None  # 纯文本 multipart 用 files 形式
    snapshot: str | None = None
    content_type: str | None = None


def build_body(body: RequestBody, scope: VariableScope) -> PreparedBody:
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


async def run_http_step(
    step: StepSpec,
    scope: VariableScope,
    context: EngineContext,
    client: httpx.AsyncClient,
) -> StepResult:
    result = StepResult(step_id=step.id, step_name=step.name, step_index=0, status="passed")

    if not step.enabled:
        result.status = "skipped"
        result.error_message = "步骤已禁用 (enabled: false)"
        return result

    # ── 构建阶段:变量解析失败 → skipped ────────────────────────────
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

    # ── 发送阶段:网络异常 → error ─────────────────────────────────
    kwargs: dict[str, Any] = {}
    if prepared.files:
        kwargs["files"] = prepared.files
    elif prepared.content is not None:
        kwargs["content"] = prepared.content

    start = time.perf_counter()
    try:
        response = await client.request(
            step.request.method, url, headers=headers, params=params,
            follow_redirects=step.request.follow_redirects, **kwargs,
        )
    except httpx.HTTPError as exc:
        result.status = "error"
        result.error_message = f"{type(exc).__name__}: {exc}"
        result.duration_ms = (time.perf_counter() - start) * 1000
        return result
    result.duration_ms = (time.perf_counter() - start) * 1000

    # form-data 经 httpx 编码后的真实请求体在 resp.request 里
    snapshot_body = prepared.snapshot
    if snapshot_body is None and prepared.files is not None:
        snapshot_body = response.request.content.decode("utf-8", errors="replace")

    result.response = ResponseSnapshot(
        status_code=response.status_code,
        headers=dict(response.headers.items()),
        body=response.text,
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
