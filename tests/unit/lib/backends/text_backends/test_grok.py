"""Replay gRPC frames through the xAI SDK's cumulative Response and incremental Chunk."""

import asyncio
import inspect
import json
from unittest.mock import AsyncMock, MagicMock

import grpc
import httpx
import pytest
from xai_sdk.proto import chat_pb2, chat_pb2_grpc, sample_pb2, usage_pb2

from lib.backends.grok_shared import create_grok_client
from lib.backends.text_backends.base import RequestTokens, TextGenerationRequest, TextOutputTruncatedError
from lib.backends.text_backends.grok import GrokTextBackend
from tests.fakes import bounded_poll_clock
from tests.unit.lib.backends.text_backends.response_schema_support import (
    class_docstring_texts,
    response_schema_params,
)


def _frame(text="", *, thinking="", finish="REASON_INVALID", usage=None):
    return chat_pb2.GetChatCompletionChunk(
        outputs=[
            chat_pb2.CompletionOutputChunk(
                delta=chat_pb2.Delta(role=chat_pb2.ROLE_ASSISTANT, content=text, reasoning_content=thinking),
                finish_reason=sample_pb2.FinishReason.Value(finish),
            )
        ],
        usage=usage,
    )


_USAGE = usage_pb2.SamplingUsage(
    prompt_tokens=1000, cached_prompt_text_tokens=600, completion_tokens=50, reasoning_tokens=200
)
_TOKENS = RequestTokens(input_tokens=1000, cache_read_tokens=600, output_tokens=250)


@pytest.fixture
def channel(monkeypatch):
    factory = MagicMock()
    factory.return_value.channel_ready = AsyncMock()
    monkeypatch.setattr(grpc.aio, "secure_channel", factory)
    return factory


@pytest.fixture
def replay(monkeypatch, channel):
    stub = MagicMock()
    monkeypatch.setattr(chat_pb2_grpc, "ChatStub", lambda channel: stub)

    def setup(*streams, **backend_kwargs):
        replies = iter(streams)

        async def stream(request):
            for frame in next(replies):
                if isinstance(frame, BaseException):
                    raise frame
                yield frame

        stub.GetCompletionChunk.side_effect = stream
        return GrokTextBackend(api_key="test", **backend_kwargs), stub.GetCompletionChunk

    return setup


async def test_chunk_progress_and_terminal_usage(replay, channel):
    backend, rpc = replay(
        [_frame(thinking="think"), _frame("hello"), _frame(" world", finish="REASON_STOP", usage=_USAGE)]
    )
    progress, billed = [], []
    result = await backend.generate(
        TextGenerationRequest(prompt="hi", max_output_tokens=123, on_progress=progress.append, on_usage=billed.append)
    )
    assert (result.text, result.provider, result.model) == ("hello world", "grok", "grok-4.3")
    assert [(p.characters, p.thinking) for p in progress if p.phase == "receiving"] == [
        (0, True),
        (5, False),
        (11, False),
    ]
    assert result.request_tokens() == billed == [_TOKENS]
    assert rpc.call_args.args[0].max_tokens == 123
    assert dict(channel.call_args.kwargs["options"])["grpc.enable_retries"] == 0
    client = create_grok_client(api_key="test")
    assert client.image
    assert client.video
    assert dict(channel.call_args.kwargs["options"])["grpc.enable_retries"] == 1


@pytest.mark.parametrize("finish", ["REASON_MAX_LEN", "REASON_MAX_CONTEXT"])
async def test_truncation(replay, finish, caplog):
    frames = [_frame("partial", finish=finish, usage=_USAGE)]
    backend, rpc = replay(frames, frames)
    with caplog.at_level("WARNING", logger="lib.backends.text_backends.base"):
        result = await backend.generate(TextGenerationRequest(prompt="hi"))
    assert result.truncated
    assert result.text == "partial"
    assert any(finish in record.message for record in caplog.records)
    billed = []
    with pytest.raises(TextOutputTruncatedError):
        await backend.generate(TextGenerationRequest(prompt="hi", response_schema={}, on_usage=billed.append))
    assert billed == [_TOKENS]
    assert rpc.call_count == 2


@pytest.mark.parametrize(
    "failure",
    [
        None,
        asyncio.CancelledError(),
        grpc.aio.AioRpcError(grpc.StatusCode.UNAVAILABLE, grpc.aio.Metadata(), grpc.aio.Metadata()),
    ],
)
async def test_no_terminal_keeps_last_reported_usage_once(replay, failure):
    frames = [_frame("{}", usage=_USAGE), _frame()]
    backend, rpc = replay(frames if failure is None else [*frames, failure])
    billed = []
    with pytest.raises(type(failure) if failure is not None else RuntimeError):
        await backend.generate(TextGenerationRequest(prompt="hi", response_schema={}, on_usage=billed.append))
    assert billed == [_TOKENS]
    assert rpc.call_count == 1


@pytest.mark.parametrize("reject", [True, False])
async def test_native_format_and_prompt_fallback(replay, reject):
    schema = {"type": "array", "items": {"type": "integer"}}
    invalid = grpc.aio.AioRpcError(
        grpc.StatusCode.INVALID_ARGUMENT, grpc.aio.Metadata(), grpc.aio.Metadata(), "response_format unsupported"
    )
    backend, rpc = replay(
        [invalid] if reject else [_frame("invalid", finish="REASON_STOP", usage=_USAGE)],
        [_frame("```json\n[1]\n```", finish="REASON_STOP")],
    )
    result = await backend.generate(TextGenerationRequest(prompt="hi", response_schema=schema))
    native, fallback = [call.args[0] for call in rpc.call_args_list]
    assert json.loads(native.response_format.schema) == schema
    assert not native.HasField("max_tokens")
    assert not fallback.HasField("response_format")
    assert '"items"' in fallback.messages[-1].content[0].text
    assert result.text == "[1]"
    assert result.request_tokens() == ([] if reject else [_TOKENS])


@pytest.mark.parametrize("model", response_schema_params())
async def test_pydantic_schema_omits_docstrings(replay, model):
    backend, rpc = replay([_frame("partial", finish="REASON_MAX_LEN")])
    with pytest.raises(TextOutputTruncatedError):
        await backend.generate(TextGenerationRequest(prompt="hi", response_schema=model))
    wire_json = rpc.call_args.args[0].response_format.schema
    first_lines = (inspect.cleandoc(text).splitlines()[0] for text in class_docstring_texts(model))
    assert not [line for line in first_lines if json.dumps(line, ensure_ascii=False)[1:-1] in wire_json]


async def test_connection_deadline_retries_before_rpc(replay, channel):
    backend, rpc = replay(connect_timeout=0)
    channel.return_value.channel_ready.side_effect = asyncio.Event().wait
    with bounded_poll_clock(step=0.1), pytest.raises(httpx.ConnectTimeout):
        await backend.generate(TextGenerationRequest(prompt="hi"))
    assert channel.return_value.channel_ready.await_count == 3
    rpc.assert_not_called()


async def test_connection_deadline_recovers_on_retry(replay, channel):
    backend, rpc = replay([_frame("ok", finish="REASON_STOP", usage=_USAGE)], connect_timeout=0)
    attempts = 0

    async def ready():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await asyncio.Event().wait()

    channel.return_value.channel_ready.side_effect = ready
    with bounded_poll_clock(step=0.1):
        result = await backend.generate(TextGenerationRequest(prompt="hi"))
    assert attempts == 2
    assert rpc.call_count == 1
    assert result.text == "ok"
