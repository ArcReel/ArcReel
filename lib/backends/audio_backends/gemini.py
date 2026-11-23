"""GeminiAudioBackend — Gemini TTS 语音合成后端（``generateContent`` + ``response_modalities=["AUDIO"]``）。

内置 Gemini（AI Studio / Vertex）与自定义供应商的 ``gemini-tts`` 端点共用本后端。音频在
``candidates[0].content.parts[].inline_data`` 里返回：Gemini 3.8 TTS 的非流式请求默认给完整 WAV，
更早的 TTS 模型给不带头的 16-bit PCM（``audio/L16;codec=pcm;rate=24000``）。后者按 mime 声明的
采样率与声道封装为 WAV，落盘产物一律可直接播放和剪辑。schema 依据
docs/api-docs/endpoints/gemini-tts.md 所列官方文档。
"""

from __future__ import annotations

import json as json_module
import logging
import wave
from pathlib import Path
from typing import TYPE_CHECKING

from lib.backends.audio_backends.base import (
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.backends.gemini_shared import VERTEX_SCOPES, gemini_http_options, resolve_gemini_api_key, with_retry_async
from lib.backends.transport_layer import TransportLayer
from lib.config.system_config import resolve_vertex_credentials_path
from lib.config.url_utils import normalize_base_url
from lib.infra.async_thread import run_noninterruptible_sync

if TYPE_CHECKING:
    from google.genai.types import GenerateContentResponseUsageMetadata

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.8-flash-tts"

_VOICE_CATALOG: tuple[VoiceOption, ...] = tuple(
    VoiceOption(id=voice_id, label=voice_id)
    for voice_id in (
        "Zephyr",
        "Puck",
        "Charon",
        "Kore",
        "Fenrir",
        "Leda",
        "Orus",
        "Aoede",
        "Callirrhoe",
        "Autonoe",
        "Enceladus",
        "Iapetus",
        "Umbriel",
        "Algieba",
        "Despina",
        "Erinome",
        "Algenib",
        "Rasalgethi",
        "Laomedeia",
        "Achernar",
        "Alnilam",
        "Schedar",
        "Gacrux",
        "Pulcherrima",
        "Achird",
        "Zubenelgenubi",
        "Vindemiatrix",
        "Sadachbia",
        "Sadaltager",
        "Sulafat",
    )
)
_VOICE_MODELS = frozenset({"gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"})

_DEFAULT_SAMPLE_RATE = 24000
_PCM_SAMPLE_WIDTH = 2


def _pcm_params(mime_type: str | None) -> tuple[int, int]:
    """从 ``audio/L16;codec=pcm;rate=24000`` 形态的 mime 读出 (采样率, 声道数)。"""
    params: dict[str, str] = {}
    for item in (mime_type or "").split(";")[1:]:
        key, _, value = item.partition("=")
        params[key.strip().lower()] = value.strip()
    rate = int(params.get("rate") or _DEFAULT_SAMPLE_RATE)
    channels = int(params.get("channels") or 1)
    return rate, channels


def _write_audio(data: bytes, mime_type: str | None, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if data[:4] == b"RIFF":
        output_path.write_bytes(data)
        return
    if mime_type and not mime_type.lower().startswith(("audio/l16", "audio/pcm")):
        raise RuntimeError(f"Gemini 语音合成返回了不支持的音频格式: {mime_type}")
    rate, channels = _pcm_params(mime_type)
    with wave.open(str(output_path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(_PCM_SAMPLE_WIDTH)
        wav.setframerate(rate)
        wav.writeframes(data)


class GeminiAudioBackend:
    """Gemini TTS 语音合成后端，支持 AI Studio 与 Vertex AI。"""

    def __init__(
        self,
        *,
        backend_type: str = "aistudio",
        api_key: str | None = None,
        base_url: str | None = None,
        credentials_path: str | None = None,
        model: str | None = None,
        provider_name: str | None = None,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self._backend_type = backend_type.strip().lower()
        self._model = model or DEFAULT_MODEL
        self._provider_name = provider_name or f"gemini-{self._backend_type}"

        if self._backend_type == "vertex":
            from google.oauth2 import service_account

            credentials_file = Path(credentials_path) if credentials_path else resolve_vertex_credentials_path()
            if credentials_file is None:
                raise ValueError("未找到 Vertex AI 凭证文件")
            with open(credentials_file, encoding="utf-8") as f:
                project_id = json_module.load(f).get("project_id")
            credentials = service_account.Credentials.from_service_account_file(
                str(credentials_file), scopes=VERTEX_SCOPES
            )
            self._client = genai.Client(vertexai=True, project=project_id, location="global", credentials=credentials)
        else:
            effective_base_url = normalize_base_url(base_url)
            self._client = genai.Client(
                api_key=resolve_gemini_api_key(api_key),
                http_options=gemini_http_options(effective_base_url, transport_layer),
            )

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH}

    def list_voices(self) -> list[VoiceOption]:
        return list(_VOICE_CATALOG) if self._model in _VOICE_MODELS else []

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        if request.speed is not None:
            logger.debug("Gemini TTS 不支持语速参数 speed=%s，已忽略", request.speed)
        data, mime_type, usage = await self._request_speech(request)
        await run_noninterruptible_sync(_write_audio, data, mime_type, request.output_path)

        logger.info("Gemini 语音合成完成: %s", request.output_path)
        return AudioSynthesisResult(
            provider=self._provider_name,
            model=self._model,
            text=request.text,
            output_path=request.output_path,
            input_tokens=usage.prompt_token_count if usage is not None else None,
            audio_output_tokens=usage.candidates_token_count if usage is not None else None,
        )

    @with_retry_async()
    async def _request_speech(
        self, request: AudioSynthesisRequest
    ) -> tuple[bytes, str | None, GenerateContentResponseUsageMetadata | None]:
        """提交合成请求（计费段），返回音频字节、mime 与这次调用的 usage。"""
        types = self._types
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=request.voice),
                )
            ),
        )
        logger.info(
            "调用 %s 语音合成 API model=%s voice=%s chars=%d",
            self.name,
            self._model,
            request.voice,
            len(request.text),
        )
        response = await self._client.aio.models.generate_content(
            model=self._model, contents=request.text, config=config
        )
        for candidate in response.candidates or []:
            for part in (candidate.content.parts if candidate.content else None) or []:
                if part.inline_data is not None and part.inline_data.data:
                    return part.inline_data.data, part.inline_data.mime_type, response.usage_metadata
        raise RuntimeError("Gemini 语音合成未返回音频")
