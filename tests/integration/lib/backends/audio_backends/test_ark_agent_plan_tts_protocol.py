"""方舟 Agent Plan 语音合成的线路协议：后端照常拼出生产地址，经 transport 注入层改投到本机回环替身。

替身在真实 socket 上按 HTTP Chunked 写出 JSON 对象流，分块边界、截断与业务错误都发生在线路上；协议依据
docs/api-docs/endpoints/ark-agent-plan-tts.md 所列官方文档。装配路径见单测 test_ark_agent_plan_audio_backend.py。
"""

from __future__ import annotations

import base64
import io
import json
import wave
from functools import partial
from pathlib import Path

import pytest

from lib.backends.audio_backends import AudioSynthesisRequest
from lib.backends.audio_backends.ark_agent_plan import ArkAgentPlanAudioBackend
from lib.backends.http_status_errors import AmbiguousSubmitError
from lib.generation.task_failure import render_failure
from lib.generation.task_failure_encoding import encode_task_failure_message
from lib.i18n import _ as translate
from lib.infra.api_errors import ApiError, UnprocessableError
from tests.factories import make_display_names
from tests.loopback_http import ScriptedReply, loopback_server

pytestmark = pytest.mark.local_port

_PATH = "/api/v3/plan/tts/unidirectional"
_URL = f"https://openspeech.bytedance.com{_PATH}"
_MODEL = "seed-tts-2.0"
_PCM = b"\x01\x00\x02\x00\x03\x00\x04\x00"
_END = {"code": 20000000, "message": "ok", "data": None, "usage": {"text_words": 4}}


def _audio(data: bytes) -> dict:
    return {"code": 0, "message": "", "data": base64.b64encode(data).decode()}


def _sentence() -> dict:
    return {"code": 0, "message": "", "data": None, "sentence": {"text": "你好世界", "words": []}}


def _lines(*items: dict) -> list[bytes]:
    """一个分块一个对象，对象后跟换行。"""
    return [json.dumps(item).encode() + b"\n" for item in items]


def _request(tmp_path: Path, *, name: str = "o.wav", **kwargs) -> AudioSynthesisRequest:
    return AudioSynthesisRequest(
        text="你好世界", output_path=tmp_path / name, voice="zh_female_vv_uranus_bigtts", **kwargs
    )


def _frames(path: Path) -> tuple[bytes, tuple[int, int, int]]:
    with wave.open(io.BytesIO(path.read_bytes())) as wav:
        return wav.readframes(wav.getnframes()), (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())


async def _synthesize(server_replies: list[ScriptedReply], request: AudioSynthesisRequest):
    async with loopback_server(server_replies) as server:
        layer = server.redirect()
        backend = ArkAgentPlanAudioBackend(api_key="plan-key", model=_MODEL, transport_layer=layer)
        try:
            result = await backend.synthesize(request)
        except Exception as exc:  # 用例按需断言异常
            return server, None, exc
    # 改投前的地址就是生产地址：没有改用别的接口
    assert set(layer.seen) <= {_URL}
    return server, result, None


async def test_sends_the_documented_request_and_saves_wav(tmp_path: Path):
    replies = [ScriptedReply(chunks=_lines(_audio(_PCM[:4]), _sentence(), _audio(_PCM[4:]), _END))]
    server, result, error = await _synthesize(replies, _request(tmp_path, speed=1.2))

    assert error is None
    [received] = server.requests
    assert (received.method, received.path) == ("POST", _PATH)
    assert received.headers["x-api-key"] == "plan-key"
    assert received.headers["x-api-resource-id"] == "seed-tts-2.0"
    assert received.headers["x-control-require-usage-tokens-return"] == "text_words"
    assert received.headers["x-api-request-id"]
    assert json.loads(received.body) == {
        "req_params": {
            "text": "你好世界",
            "speaker": "zh_female_vv_uranus_bigtts",
            "audio_params": {"format": "pcm", "sample_rate": 24000, "speech_rate": 20},
        }
    }
    assert _frames(tmp_path / "o.wav") == (_PCM, (1, 2, 24000))
    assert (result.provider, result.model, result.text) == ("ark-agent-plan", _MODEL, "你好世界")
    assert result.billed_characters == 4


@pytest.mark.parametrize(
    "chunks",
    [
        [json.dumps(_audio(_PCM)).encode() + json.dumps(_END).encode()],
        [(json.dumps(_audio(_PCM)) + "\n\n" + json.dumps(_END) + "\n").encode()],
        [json.dumps(_audio(_PCM)).encode()[:9], json.dumps(_audio(_PCM)).encode()[9:], json.dumps(_END).encode()],
        [
            b"  " + json.dumps(_audio(_PCM)).encode() + b"\r\n" + json.dumps(_END).encode()[:5],
            json.dumps(_END).encode()[5:],
        ],
    ],
    ids=["concatenated", "blank-lines", "object-split-across-chunks", "objects-share-a-chunk"],
)
async def test_stream_framing_does_not_depend_on_chunk_or_line_boundaries(tmp_path: Path, chunks):
    _, result, error = await _synthesize([ScriptedReply(chunks=chunks)], _request(tmp_path))
    assert error is None
    assert _frames(tmp_path / "o.wav")[0] == _PCM
    assert result.billed_characters == 4


async def test_mp3_artifact_requests_mp3_and_keeps_bytes(tmp_path: Path):
    mp3 = b"ID3\x04mp3-frames"
    server, _, error = await _synthesize(
        [ScriptedReply(chunks=_lines(_audio(mp3), _END))], _request(tmp_path, name="o.mp3")
    )
    assert error is None
    assert json.loads(server.requests[0].body)["req_params"]["audio_params"]["format"] == "mp3"
    assert (tmp_path / "o.mp3").read_bytes() == mp3


@pytest.mark.parametrize(("speed", "rate"), [(0.5, -50), (1.0, 0), (2.0, 100), (None, None)])
async def test_speed_maps_to_the_documented_speech_rate(tmp_path: Path, speed, rate):
    server, _, error = await _synthesize(
        [ScriptedReply(chunks=_lines(_audio(_PCM), _END))], _request(tmp_path, speed=speed)
    )
    assert error is None
    assert json.loads(server.requests[0].body)["req_params"]["audio_params"].get("speech_rate") == rate


@pytest.mark.parametrize("speed", [0.49, 2.01])
async def test_out_of_range_speed_fails_before_any_billed_request(tmp_path: Path, speed):
    server, _, error = await _synthesize([], _request(tmp_path, speed=speed))
    assert isinstance(error, UnprocessableError)
    assert error.key == "narration_tts_speed_out_of_range"
    assert server.requests == []


def _rendered(error: Exception) -> list[str]:
    stored = encode_task_failure_message(error)
    rendered: list[str] = []
    for locale in ("zh", "en"):
        text = render_failure(stored, partial(translate, locale=locale), make_display_names(locale))
        # 渲染成本地化文案，而不是原样透传的结构化编码
        assert text is not None
        assert not text.startswith("[")
        rendered.append(text)
    return rendered


@pytest.mark.parametrize(
    ("reply", "key", "code"),
    [
        (
            ScriptedReply(
                chunks=_lines(_audio(_PCM), {"code": 45000000, "message": "quota exceeded for types: concurrency"})
            ),
            "ark_agent_plan_tts_access_denied",
            45000000,
        ),
        (
            ScriptedReply(chunks=_lines({"code": 55000000, "message": "server error detail"})),
            "ark_agent_plan_tts_failed",
            55000000,
        ),
        (
            ScriptedReply(chunks=_lines({"code": 40402003, "message": "TTSExceededTextLimit:exceed max limit"})),
            "ark_agent_plan_tts_failed",
            40402003,
        ),
        (
            ScriptedReply(status=403, chunks=_lines({"code": 45000000, "message": "access denied"})),
            "ark_agent_plan_tts_access_denied",
            45000000,
        ),
        (
            ScriptedReply(status=401, chunks=[b"unauthorized"], content_type="text/plain"),
            "ark_agent_plan_tts_access_denied",
            401,
        ),
        (
            ScriptedReply(status=400, chunks=_lines({"code": 40402003, "message": "too long"})),
            "ark_agent_plan_tts_failed",
            40402003,
        ),
    ],
    ids=[
        "200-access-denied-after-audio",
        "200-server-error",
        "200-text-too-long",
        "403-business-code",
        "401-no-json",
        "400-business-code",
    ],
)
async def test_upstream_errors_never_become_artifacts_and_are_not_resubmitted(tmp_path: Path, reply, key, code):
    server, _, error = await _synthesize([reply], _request(tmp_path))

    assert isinstance(error, ApiError)
    assert (error.key, error.params) == (key, {"code": code})
    assert [request.path for request in server.requests] == [_PATH]
    assert not (tmp_path / "o.wav").exists()
    for text in _rendered(error):
        assert str(code) in text
        # 上游原文只进日志
        assert "detail" not in text
        assert "exceed max limit" not in text
        assert "quota exceeded" not in text


@pytest.mark.parametrize(
    "chunks",
    [
        _lines(_audio(_PCM)),
        _lines(_audio(_PCM), _sentence()),
        [json.dumps(_audio(_PCM)).encode(), json.dumps(_END).encode()[:-3]],
        _lines({"code": 0, "message": "", "data": "%%not-base64%%"}, _END),
        _lines({"code": 0, "message": "", "data": 12}, _END),
        _lines(_sentence(), _END),
        _lines(_audio(_PCM[:3]), _END),
        _lines(_audio(_PCM), {"message": "no code"}),
        [b"<html>gateway</html>"],
        [],
    ],
    ids=[
        "no-end-object",
        "ends-on-sentence",
        "cut-end-object",
        "invalid-base64",
        "data-not-string",
        "end-without-audio",
        "partial-pcm-frame",
        "object-without-code",
        "not-json",
        "empty-body",
    ],
)
async def test_incomplete_streams_are_not_recorded_as_success(tmp_path: Path, chunks):
    server, _, error = await _synthesize([ScriptedReply(chunks=chunks)], _request(tmp_path))

    assert isinstance(error, ApiError)
    assert error.key == "ark_agent_plan_tts_incomplete"
    assert len(server.requests) == 1
    assert not (tmp_path / "o.wav").exists()
    _rendered(error)


async def test_connection_cut_mid_stream_is_ambiguous_and_not_resubmitted(tmp_path: Path):
    replies = [
        ScriptedReply(chunks=_lines(_audio(_PCM), _audio(_PCM)), truncate=True),
        ScriptedReply(chunks=_lines(_END)),
    ]
    server, _, error = await _synthesize(replies, _request(tmp_path))

    assert isinstance(error, AmbiguousSubmitError)
    assert len(server.requests) == 1
    assert not (tmp_path / "o.wav").exists()


async def test_explicit_server_error_status_is_retried(tmp_path: Path, poll_clock):
    replies = [
        ScriptedReply(status=503, chunks=[b"busy"], content_type="text/plain"),
        ScriptedReply(chunks=_lines(_audio(_PCM), _END)),
    ]
    server, _, error = await _synthesize(replies, _request(tmp_path))

    assert error is None
    assert len(server.requests) == 2
    assert _frames(tmp_path / "o.wav")[0] == _PCM


async def test_retryable_status_still_fails_with_a_readable_reason(tmp_path: Path, poll_clock):
    busy = ScriptedReply(
        status=429, chunks=_lines({"code": 45000000, "message": "quota exceeded for types: concurrency"})
    )
    server, _, error = await _synthesize([busy] * 3, _request(tmp_path))

    # 限流按状态码重试，用尽后报上游业务码，不改用其他接口
    assert isinstance(error, ApiError)
    assert (error.key, error.params) == ("ark_agent_plan_tts_access_denied", {"code": 45000000})
    assert [request.path for request in server.requests] == [_PATH] * 3
    assert not (tmp_path / "o.wav").exists()
    _rendered(error)


async def test_write_failure_does_not_synthesize_again(tmp_path: Path):
    request = AudioSynthesisRequest(text="x", output_path=tmp_path / "missing" / "o.wav", voice="v")
    server, _, error = await _synthesize([ScriptedReply(chunks=_lines(_audio(_PCM), _END))] * 2, request)

    assert isinstance(error, FileNotFoundError)
    assert len(server.requests) == 1


async def test_missing_usage_keeps_the_synthesis_and_reports_no_billed_count(tmp_path: Path):
    end = {"code": 20000000, "message": "ok", "data": None}
    _, result, error = await _synthesize([ScriptedReply(chunks=_lines(_audio(_PCM), end))], _request(tmp_path))
    assert error is None
    assert result.billed_characters is None
    assert _frames(tmp_path / "o.wav")[0] == _PCM
