"""异步文本协议的流消费、等待边界、复验与逐请求计量。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import aclosing, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, replace

import httpx
from grpc.aio import AioRpcError

from lib.backends.backend_runtime import is_retryable_http_status
from lib.backends.grok_shared import grok_should_retry
from lib.backends.http_status_errors import TextWaitTimeout as TextWaitTimeout
from lib.backends.text_backends.base import (
    StreamingBodyCounter,
    TextGenerationRequest,
    TextGenerationResult,
    TextOutputTruncatedError,
    TextProgress,
    merge_billed_usage,
    prompt_json_fallback,
    strip_json_code_fence,
    strip_leading_think_block,
    structured_fallback_reason,
)
from lib.infra.retry import resolve_clock

logger = logging.getLogger(__name__)


@dataclass
class TextTaskContext:
    task_id: str
    task_type: str
    on_progress: Callable[[TextProgress], None] | None = None
    characters: int = 0


_text_task: ContextVar[TextTaskContext | None] = ContextVar("text_task", default=None)


def current_text_task() -> TextTaskContext | None:
    return _text_task.get()


@contextmanager
def bind_text_task(context: TextTaskContext) -> Generator[None]:
    token = _text_task.set(context)
    try:
        yield
    finally:
        _text_task.reset(token)


class NativeSchemaRejected(Exception):
    pass


@dataclass(frozen=True)
class TextDelta:
    text: str
    thinking: bool = False


type StreamDriver = Callable[[TextGenerationRequest], AsyncGenerator[TextDelta | TextGenerationResult]]


@dataclass(frozen=True)
class WaitLimits:
    idle: float = 180
    total: float = 900


CONNECT_TIMEOUT = 15
DEFAULT_WAIT_LIMITS = WaitLimits()


def _transport_failure(exc: Exception) -> tuple[bool, bool]:
    candidate: BaseException | None = exc
    seen = set()
    while candidate is not None and id(candidate) not in seen:
        seen.add(id(candidate))
        if isinstance(candidate, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ProxyError)):
            return True, False
        if isinstance(candidate, httpx.TimeoutException):
            return False, True
        status = candidate.response.status_code if isinstance(candidate, httpx.HTTPStatusError) else None
        if status is None:
            for attr in ("status_code", "code"):
                value = getattr(candidate, attr, None)
                if isinstance(value, int) and not isinstance(value, bool):
                    status = value
                    break
        if isinstance(status, int) and not isinstance(status, bool):
            return is_retryable_http_status(status), False
        if isinstance(candidate, AioRpcError):
            return grok_should_retry(candidate), False
        candidate = candidate.__cause__
    return False, isinstance(exc, TimeoutError)


class _Execution:
    def __init__(self, request: TextGenerationRequest, driver: StreamDriver, model: str, limits: WaitLimits) -> None:
        self.request = request
        self.driver = driver
        self.model = model
        self.limits = limits
        self.clock = resolve_clock(None)
        self.started = self.clock.monotonic()
        self.deadline = self.started + limits.total
        self.first_text: float | None = None
        self.last_delta: float | None = None
        self.output_tokens: int | None = None
        self.characters = 0
        self.retries = 0
        self.fallback = False
        self.truncated = False
        self.received = False
        self.task = _text_task.get()

    def report(self, progress: TextProgress) -> None:
        if self.request.on_progress:
            self.request.on_progress(progress)
        if self.task and self.task.on_progress:
            self.task.on_progress(replace(progress, characters=self.task.characters))

    def complete(self, result: TextGenerationResult) -> TextGenerationResult:
        if self.request.on_usage:
            for tokens in result.request_tokens():
                self.request.on_usage(tokens)
        if result.output_tokens is not None:
            self.output_tokens = (self.output_tokens or 0) + result.output_tokens
        self.truncated = result.truncated
        schema = self.request.response_schema
        if result.truncated and schema is not None:
            raise TextOutputTruncatedError(
                provider=result.provider, model=result.model, output_tokens=result.output_tokens
            )
        result.text = strip_leading_think_block(result.text)
        if schema is not None:
            result.text = strip_json_code_fence(result.text)
        return result

    async def consume(self, wire: TextGenerationRequest, attempt: int) -> TextGenerationResult:
        self.received = False
        counter = StreamingBodyCounter()
        self.report(TextProgress("waiting", self.model, self.characters, attempt=attempt))
        idle_deadline = self.clock.monotonic() + self.limits.idle
        async with asyncio.timeout(self.limits.idle) as watchdog, aclosing(self.driver(wire)) as events:
            async for event in events:
                self.received = True
                now = self.clock.monotonic()
                if isinstance(event, TextGenerationResult):
                    result = self.complete(event)
                    if now >= min(self.deadline, idle_deadline):
                        raise TextWaitTimeout()
                    return result
                if now >= min(self.deadline, idle_deadline):
                    raise TextWaitTimeout()
                if not event.text:
                    continue
                self.last_delta = now
                idle_deadline = now + self.limits.idle
                watchdog.reschedule(asyncio.get_running_loop().time() + self.limits.idle)
                count, thinking = (0, True) if event.thinking else counter.consume(event.text)
                if count:
                    if self.first_text is None:
                        self.first_text = now
                    self.characters += count
                    if self.task:
                        self.task.characters += count
                if count or thinking:
                    self.report(TextProgress("receiving", self.model, self.characters, thinking, attempt))
        raise RuntimeError("Text stream closed without a terminal event")

    async def once(self, wire: TextGenerationRequest) -> TextGenerationResult:
        for attempt in range(1, 4):
            if self.clock.monotonic() >= self.deadline:
                raise TextWaitTimeout()
            try:
                return await self.consume(wire, attempt)
            except Exception as exc:
                retryable, timeout = _transport_failure(exc)
                if self.received or not retryable or attempt == 3:
                    if timeout:
                        raise TextWaitTimeout() from exc
                    raise
                self.retries += 1
                self.report(
                    TextProgress("retrying", self.model, self.characters, attempt=attempt + 1, error=type(exc).__name__)
                )
                await self.clock.sleep(min(2**attempt, max(0, self.deadline - self.clock.monotonic())))
        raise AssertionError("unreachable")

    async def generate(self, provider: str, strict: bool) -> TextGenerationResult:
        request = self.request
        if request.response_schema is None:
            return await self.once(request)
        native = None
        if request.snapshot is None or request.snapshot.effective.get("structured_output") is not False:
            with suppress(NativeSchemaRejected):
                native = await self.once(request)
            if native is not None:
                reason = structured_fallback_reason(native.text, request.response_schema, strict=strict)
                if reason is None:
                    return native
                logger.warning("Native schema validation failed: %s characters=%d", reason, len(native.text))
        self.fallback = True
        result = await prompt_json_fallback(request, self.once, provider=provider, model=self.model)
        if native is not None:
            merge_billed_usage(result, native)
        return result


async def execute_stream(
    request: TextGenerationRequest,
    driver: StreamDriver,
    *,
    provider: str,
    protocol: str,
    model: str,
    strict: bool = False,
    limits: WaitLimits = DEFAULT_WAIT_LIMITS,
) -> TextGenerationResult:
    run = _Execution(request, driver, model, limits)
    failure = None
    try:
        async with asyncio.timeout(limits.total):
            result = await run.generate(provider, strict)
            if run.clock.monotonic() >= run.deadline:
                raise TextWaitTimeout()
            return result
    except TimeoutError as exc:
        failure = TextWaitTimeout.code
        raise TextWaitTimeout() from exc
    except BaseException as exc:
        failure = TextWaitTimeout.code if isinstance(exc, TextWaitTimeout) else type(exc).__name__
        raise
    finally:
        now = run.clock.monotonic()
        logger.info(
            "text_generation %s",
            json.dumps(
                {
                    "task_id": run.task.task_id if run.task else None,
                    "task_type": run.task.task_type if run.task else request.task_type,
                    "provider": request.snapshot.provider_id if request.snapshot else provider,
                    "protocol": protocol,
                    "model": model,
                    "input_chars": len(request.prompt) + len(request.system_prompt or ""),
                    "streaming": True,
                    "first_text_seconds": None if run.first_text is None else run.first_text - run.started,
                    "tail_seconds": None if run.last_delta is None else now - run.last_delta,
                    "elapsed_seconds": now - run.started,
                    "received_chars": run.characters,
                    "retries": run.retries,
                    "fallback": run.fallback,
                    "output_tokens": run.output_tokens,
                    "end_reason": "truncated" if run.truncated else ("failed" if failure else "completed"),
                    "failure_code": failure,
                },
                ensure_ascii=False,
            ),
        )
