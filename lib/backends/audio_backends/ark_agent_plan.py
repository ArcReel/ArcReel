"""ArkAgentPlanAudioBackend — 方舟 Agent Plan 专属语音合成（豆包语音合成模型 2.0）。

``POST https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional``，用 Agent Plan 的专属 API Key 鉴权
（``X-Api-Key``），资源固定为 ``seed-tts-2.0``。响应是 HTTP Chunked 流，流里依次是 JSON 对象：``code=0`` 的对象
带 base64 音频（``data``）或句子时间戳（``sentence``），``code=20000000`` 的对象表示合成成功结束，并在请求头要求时带
``usage.text_words``（计费字符数）；其余 ``code`` 是业务错误，HTTP 状态码仍可能是 200。

只有收到成功结束对象、且拼出的音频非空时才算合成成功：业务错误、流在结束对象之前中断、对象残缺或音频解不开都不落成产物。
套餐权限或额度不足时直接报错，不改用按量付费的语音合成接口。schema 与错误码依据
docs/api-docs/endpoints/ark-agent-plan-tts.md 所列官方文档。只服务内置 ``ark-agent-plan`` 供应商。
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.audio_backends.base import (
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.backends.backend_runtime import redact_provider_text, should_retry_submit, submit_post
from lib.backends.providers import PROVIDER_ARK_AGENT_PLAN
from lib.backends.transport_layer import TransportLayer
from lib.infra.api_errors import BadGatewayError, UnprocessableError
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

_TTS_URL = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"

# Agent Plan 文档写明资源 ID 固定为 seed-tts-2.0；它同时决定效果版本与计费商品，不随模型名变化。
_RESOURCE_ID = "seed-tts-2.0"

# 上游请求体没有模型字段，这是 ArcReel 的本地标识：用于目录条目、记账和选择模型，与资源 ID 同名。
DEFAULT_MODEL = "seed-tts-2.0"

#: 音频或句子对象的 code。
_CHUNK_CODE = 0
#: 合成成功结束的 code。
_SUCCESS_CODE = 20000000
#: 音色鉴权失败或并发超限：官方错误码表里两种拒因共用这一个 code。
_ACCESS_DENIED_CODE = 45000000
_ACCESS_DENIED_STATUSES = frozenset({401, 403})

# 单次合成的读超时。合成是计费请求，超时后不能重试，所以留足余量。
_HTTP_TIMEOUT_SECONDS = 300.0

# speech_rate 取值 [-50, 100]：-50 为 0.5 倍速、100 为 2.0 倍速、0 为原速。
_MIN_SPEED = 0.5
_MAX_SPEED = 2.0

_SAMPLE_RATE = 24000
# 流式场景下 wav 会多次返回文件头，官方建议改用 pcm。资源路径约定 .wav，所以请求 pcm 再在本地封装 WAV 头。
# pcm 按 16 bit 单声道封装。
_PCM_SAMPLE_WIDTH = 2
_PCM_CHANNELS = 1
_FORMAT_BY_SUFFIX = {"mp3": "mp3", "ogg": "ogg_opus", "opus": "ogg_opus", "pcm": "pcm"}

# 官方音色列表中「豆包语音合成模型 2.0」的中文音色子集，出处见 docs/api-docs/endpoints/ark-agent-plan-tts.md。
# 新建项目预填列表首项，首项取 Agent Plan 文档示例里的音色。label 是 lib/i18n 翻译 key。
_VOICE_CATALOG: tuple[VoiceOption, ...] = tuple(
    VoiceOption(id=f"zh_{voice}_uranus_bigtts", label=f"voice_label_ark_agent_plan_{voice.split('_', 1)[1]}")
    for voice in (
        "female_vv",
        "female_gaolengyujie",
        "male_cixingjieshuonan",
        "male_xuanyijieshuo",
        "female_wenroushunv",
        "male_ruyaqingnian",
        "male_baqiqingshu",
        "female_shaoergushi",
        "female_zhixingnv",
        "male_yuanboxiaoshu",
    )
)


@dataclass(frozen=True)
class _Synthesis:
    audio: bytes
    #: 上游回报的计费字符数（``usage.text_words``）；结束对象没带用量时为 ``None``。
    billed_characters: int | None


class _IncompleteStream(Exception):
    """流在合成成功结束之前中断，或内容残缺。只在本模块内部使用，对外统一换成领域错误。"""


def _format_for(output_path: Path) -> tuple[str, bool]:
    """按落盘扩展名选上游格式；返回 (format, 是否封装 WAV 头)。其余扩展名（含 .wav）请求 pcm 再封装。"""
    suffix = output_path.suffix.lstrip(".").lower()
    if suffix in _FORMAT_BY_SUFFIX:
        return _FORMAT_BY_SUFFIX[suffix], False
    return "pcm", True


def _speech_rate(speed: float) -> int:
    return round((speed - 1) * 100)


def _int_code(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_stream(body: bytes, *, log_id: str) -> _Synthesis:
    """依次解出响应体里的 JSON 对象并拼接音频。

    对象之间按任意空白分隔或首尾相接都能解析。业务错误抛领域错误；结束对象之前流就结束、对象残缺或音频解不开时抛
    :class:`_IncompleteStream`。
    """
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _IncompleteStream("响应体不是 UTF-8 文本") from exc
    decoder = json.JSONDecoder()
    audio = bytearray()
    position = 0
    while True:
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text):
            raise _IncompleteStream("流在合成成功结束之前中断")
        try:
            item, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError as exc:
            raise _IncompleteStream("流里有残缺的 JSON 对象") from exc
        if not isinstance(item, dict):
            raise _IncompleteStream("流里的对象不是 JSON 对象")
        code = _int_code(item.get("code"))
        if code == _CHUNK_CODE:
            data = item.get("data")
            if data is None:
                continue
            if not isinstance(data, str):
                raise _IncompleteStream("音频对象的 data 不是字符串")
            try:
                audio += base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise _IncompleteStream("音频对象的 data 不是合法 base64") from exc
            continue
        if code == _SUCCESS_CODE:
            usage = item.get("usage")
            words = _int_code(usage.get("text_words")) if isinstance(usage, dict) else None
            if text[position:].strip():
                logger.warning("方舟 Agent Plan 语音合成结束对象之后还有内容，已忽略 logid=%s", log_id)
            return _Synthesis(bytes(audio), words if words is not None and words >= 0 else None)
        _log_upstream_error(code, item.get("message"), log_id)
        raise _business_error(code)


def _business_error(code: int | None) -> BadGatewayError:
    if code == _ACCESS_DENIED_CODE:
        return BadGatewayError("ark_agent_plan_tts_access_denied", code=code)
    if code is None:
        return BadGatewayError("ark_agent_plan_tts_incomplete")
    return BadGatewayError("ark_agent_plan_tts_failed", code=code)


def _log_upstream_error(code: object, message: object, log_id: str) -> None:
    # 上游原文只进日志，不进面向用户的失败原因。
    logger.warning(
        "方舟 Agent Plan 语音合成返回错误 code=%s message=%s logid=%s",
        code,
        redact_provider_text(message)[:300] if message is not None else "",
        log_id,
    )


def _rejection(response: httpx.Response) -> BadGatewayError:
    """把 HTTP 错误状态换成领域错误：错误体里有业务码时按业务码分流，否则按 HTTP 状态码。"""
    log_id = response.headers.get("X-Tt-Logid", "")
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    code = _int_code(payload.get("code")) if isinstance(payload, dict) else None
    message = payload.get("message") if isinstance(payload, dict) else None
    _log_upstream_error(code if code is not None else response.status_code, message, log_id)
    if code is not None and code not in (_CHUNK_CODE, _SUCCESS_CODE):
        return _business_error(code)
    if response.status_code in _ACCESS_DENIED_STATUSES:
        return BadGatewayError("ark_agent_plan_tts_access_denied", code=response.status_code)
    return BadGatewayError("ark_agent_plan_tts_failed", code=response.status_code)


def _encode_artifact(audio: bytes, *, wrap_wav: bool) -> bytes:
    if not wrap_wav:
        return audio
    if len(audio) % (_PCM_SAMPLE_WIDTH * _PCM_CHANNELS):
        # 16 bit pcm 的字节数必为偶数；奇数说明音频被截断。
        raise _IncompleteStream("pcm 音频字节数不是整帧")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(_PCM_CHANNELS)
        wav.setsampwidth(_PCM_SAMPLE_WIDTH)
        wav.setframerate(_SAMPLE_RATE)
        wav.writeframes(audio)
    return buffer.getvalue()


class ArkAgentPlanAudioBackend:
    """方舟 Agent Plan 专属语音合成后端（HTTP Chunked 单向流式接口）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        if api_key is None or not api_key.strip():
            raise ValueError("请到系统配置页填写方舟 Agent Plan API Key")
        self._api_key = api_key.strip()
        self._model = model or DEFAULT_MODEL
        self._transport_layer = transport_layer

    @property
    def name(self) -> str:
        return PROVIDER_ARK_AGENT_PLAN

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH, AudioCapability.SPEECH_SPEED}

    def list_voices(self) -> list[VoiceOption]:
        return list(_VOICE_CATALOG)

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        # 越界语速在发出计费请求前报错，不静默改成边界值。
        if request.speed is not None and not _MIN_SPEED <= request.speed <= _MAX_SPEED:
            raise UnprocessableError(
                "narration_tts_speed_out_of_range", speed=request.speed, minimum=_MIN_SPEED, maximum=_MAX_SPEED
            )
        upstream_format, wrap_wav = _format_for(request.output_path)
        # 计费调用与写盘分离：重试只包 API 调用，写盘失败绝不回头重跑会再次计费的合成请求。
        try:
            artifact, billed_characters = await self._request_speech(request, upstream_format, wrap_wav)
        except httpx.HTTPStatusError as exc:
            # 429、5xx 等可重试状态在重试用尽后仍要落成可读的失败原因；状态码留在因果链上供使用记录分类
            raise _rejection(exc.response) from exc
        # 供应商已计费返回，写盘不可被取消打断，结果照常成为产物
        await run_noninterruptible_sync(request.output_path.write_bytes, artifact)
        logger.info("方舟 Agent Plan 语音合成完成: %s", request.output_path)
        return AudioSynthesisResult(
            provider=PROVIDER_ARK_AGENT_PLAN,
            model=self._model,
            text=request.text,
            output_path=request.output_path,
            billed_characters=billed_characters,
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _request_speech(
        self, request: AudioSynthesisRequest, upstream_format: str, wrap_wav: bool
    ) -> tuple[bytes, int | None]:
        audio_params: dict[str, Any] = {"format": upstream_format, "sample_rate": _SAMPLE_RATE}
        if request.speed is not None:
            audio_params["speech_rate"] = _speech_rate(request.speed)
        payload = {"req_params": {"text": request.text, "speaker": request.voice, "audio_params": audio_params}}
        headers = {
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": _RESOURCE_ID,
            "X-Api-Request-Id": str(uuid.uuid4()),
            # 要求结束对象带回计费字符数。
            "X-Control-Require-Usage-Tokens-Return": "text_words",
        }
        logger.info(
            "调用 %s 语音合成 API model=%s voice=%s format=%s chars=%d",
            self.name,
            self._model,
            request.voice,
            upstream_format,
            len(request.text),
        )
        async with artifact_http_client(timeout=_HTTP_TIMEOUT_SECONDS, transport_layer=self._transport_layer) as client:
            try:
                # 合成是计费 POST：submit_post 把歧义传输错误（含读到一半断开的流）转终态失败，>=400 交状态码分流
                resp = await submit_post(
                    lambda: client.post(_TTS_URL, json=payload, headers=headers),
                    provider=self.name,
                    include_provider_reason=False,
                )
            except httpx.HTTPStatusError as exc:
                if should_retry_submit(exc):
                    raise
                raise _rejection(exc.response) from exc
        log_id = resp.headers.get("X-Tt-Logid", "")
        try:
            # 供应商已计费返回：整段响应的解析与解码放进线程且不可被取消打断
            synthesis = await run_noninterruptible_sync(lambda: _parse_stream(resp.content, log_id=log_id))
            if not synthesis.audio:
                raise _IncompleteStream("合成成功结束，但没有音频")
            artifact = _encode_artifact(synthesis.audio, wrap_wav=wrap_wav)
        except _IncompleteStream as exc:
            logger.warning("方舟 Agent Plan 语音合成结果不完整: %s logid=%s", exc, log_id)
            raise BadGatewayError("ark_agent_plan_tts_incomplete") from exc
        return artifact, synthesis.billed_characters
