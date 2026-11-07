"""GeminiAudioBackend: Gemini synthesis (``generateContent`` + ``response_modalities=["AUDIO"]``).

Built-in Gemini AI Studio / Vertex and custom ``gemini-tts`` endpoints share this backend. Audio returns
in ``candidates[0].content.parts[].inline_data``. Gemini 3.8 TTS nonstreaming defaults to complete WAV;
earlier models return headerless 16-bit PCM (``audio/L16;codec=pcm;rate=24000``). Wrap PCM into WAV using
declared MIME sample rates/channels so all persisted artifacts play/edit directly. Official schema
sources are indexed in docs/api-docs/endpoints/gemini-tts.md.
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

# Official 30 preset voices, sourced through docs/api-docs/endpoints/gemini-tts.md. Labels use ids, like openai-tts.
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

# Official defaults without rate parameters: 24 kHz, mono, 16-bit.
_DEFAULT_SAMPLE_RATE = 24000
_PCM_SAMPLE_WIDTH = 2


def _pcm_params(mime_type: str | None) -> tuple[int, int]:
    """Read (sample rate, channel count) from MIME forms like ``audio/L16;codec=pcm;rate=24000``."""
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
    # Wrap only raw PCM as WAV; wrapping compressed relay mp3 responses creates noise.
    if mime_type and not mime_type.lower().startswith(("audio/l16", "audio/pcm")):
        raise RuntimeError(f"Gemini 语音合成返回了不支持的音频格式: {mime_type}")
    rate, channels = _pcm_params(mime_type)
    with wave.open(str(output_path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(_PCM_SAMPLE_WIDTH)
        wav.setframerate(rate)
        wav.writeframes(data)


class GeminiAudioBackend:
    """Gemini synthesis backend supporting AI Studio and Vertex AI."""

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
        # Custom gemini-tts accounts under actual providers, matching wrapper .name.
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
            # Gemini 3.8 TTS is global-only.
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
        # Do not declare SPEECH_SPEED: Gemini lacks rate parameters; style instructions cannot provide reproducible multipliers.
        return {AudioCapability.TEXT_TO_SPEECH}

    def list_voices(self) -> list[VoiceOption]:
        return list(_VOICE_CATALOG)

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        if request.speed is not None:
            logger.debug("Gemini TTS 不支持语速参数 speed=%s，已忽略", request.speed)
        # Omit language_type: Gemini detects languages from input text.
        # Separate billed calls/writes: retries wrap APIs only, never repeating paid synthesis after write failures.
        data, mime_type, usage = await self._request_speech(request)
        await run_noninterruptible_sync(_write_audio, data, mime_type, request.output_path)

        logger.info("Gemini 语音合成完成: %s", request.output_path)
        return AudioSynthesisResult(
            provider=self._provider_name,
            model=self._model,
            text=request.text,
            output_path=request.output_path,
            # Token pricing: input tokens are text, output tokens entirely audio.
            input_tokens=usage.prompt_token_count if usage is not None else None,
            audio_output_tokens=usage.candidates_token_count if usage is not None else None,
        )

    @with_retry_async()
    async def _request_speech(
        self, request: AudioSynthesisRequest
    ) -> tuple[bytes, str | None, GenerateContentResponseUsageMetadata | None]:
        """Submit billed synthesis, returning audio bytes, MIME, and this call's usage."""
        types = self._types
        # Old/new TTS accept prebuilt_voice_config; older models do not recognize 3.8 voice_config.voice.
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
        # Already billed by providers: retries bill again, so fail directly for task handling.
        raise RuntimeError("Gemini 语音合成未返回音频")
