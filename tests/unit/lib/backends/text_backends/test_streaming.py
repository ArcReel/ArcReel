from __future__ import annotations

import asyncio

import httpx
import pytest

from lib.backends.text_backends.base import TextGenerationRequest, TextGenerationResult
from lib.backends.text_backends.streaming import TextDelta, TextWaitTimeout, WaitLimits, execute_stream
from lib.infra.retry import bind_clock


class Clock:
    time = 0.0

    def monotonic(self):
        return self.time

    async def sleep(self, delay):
        self.time += delay


@pytest.mark.parametrize(
    ("status", "limit", "expected"),
    [(408, 900, 3), (425, 900, 3), (429, 900, 3), (503, 900, 3), (503, 2, 1), (401, 900, 1)],
)
async def test_status_retries_obey_total_deadline(status, limit, expected):
    clock = Clock()
    calls = []
    progress = []

    async def stream(request):
        calls.append(clock.time)
        response = httpx.Response(status, request=httpx.Request("POST", "https://example.org"))
        response.raise_for_status()
        yield TextDelta("")

    error = TextWaitTimeout if limit == 2 else httpx.HTTPStatusError
    with bind_clock(clock), pytest.raises(error):
        await execute_stream(
            TextGenerationRequest("q", on_progress=progress.append),
            stream,
            provider="p",
            protocol="test",
            model="m",
            limits=WaitLimits(total=limit),
        )
    assert len(calls) == expected
    assert all(t < limit for t in calls)
    assert all(p.max_attempts == 3 for p in progress)


@pytest.mark.parametrize("kind", ["empty", "thinking", "body", "disconnect", "read", "silent"])
async def test_only_effective_deltas_extend_idle_deadline(kind):
    clock = Clock()
    calls = []

    async def stream(request):
        calls.append(request)
        if kind == "silent":
            await asyncio.Event().wait()
        for _ in range(3):
            clock.time += 0.5
            yield TextDelta("" if kind == "empty" else "x", thinking=kind == "thinking")
        if kind == "read":
            raise httpx.ReadTimeout("read")
        if kind == "disconnect":
            raise httpx.RemoteProtocolError("EOF")
        yield TextGenerationResult("body", "p", "m")

    async def run():
        return await execute_stream(
            TextGenerationRequest("q"),
            stream,
            provider="p",
            protocol="test",
            model="m",
            limits=WaitLimits(idle=0.001 if kind == "silent" else 1),
        )

    with bind_clock(clock):
        if kind in ("thinking", "body"):
            assert (await run()).text == "body"
        else:
            with pytest.raises(httpx.RemoteProtocolError if kind == "disconnect" else TextWaitTimeout):
                await run()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "cause",
    [httpx.ConnectTimeout("connect"), httpx.PoolTimeout("pool"), httpx.ProxyError("proxy"), httpx.ReadTimeout("read")],
)
async def test_wrapped_transport_failures_preserve_delivery_boundary(cause):
    calls = []

    async def stream(request):
        calls.append(request)
        if len(calls) == 1:
            raise RuntimeError("gateway") from cause
        yield TextGenerationResult("ok", "p", "m")

    with bind_clock(Clock()):
        run = execute_stream(TextGenerationRequest("q"), stream, provider="p", protocol="test", model="m")
        if isinstance(cause, httpx.ReadTimeout):
            with pytest.raises(TextWaitTimeout):
                await run
            assert len(calls) == 1
        else:
            assert (await run).text == "ok"
            assert len(calls) == 2
