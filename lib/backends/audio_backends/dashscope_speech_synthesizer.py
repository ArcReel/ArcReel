"""DashScopeSpeechSynthesizerBackend: synchronous Alibaba Bailian qwen-audio TTS SpeechSynthesizer.

Use ``POST {host}/api/v1/services/audio/tts/SpeechSynthesizer`` for ``qwen-audio-3.1-tts-flash`` /
``qwen-audio-3.1-tts-plus`` / ``qwen-audio-3.0-tts-flash`` / ``qwen-audio-3.0-tts-plus``.
Differences from multimodal-generation (:class:`DashScopeAudioBackend`):

- ``input`` carries ``text`` / ``voice`` / ``format`` / ``sample_rate``; speed is ``rate`` 0.5–2.0,
  without ``language_type``.
- Voices are model-specific and noninterchangeable: providers return ``InvalidParameter`` for mismatches,
  so declare voice tables per model.
- Nonstreaming returns ``output.audio.url`` with 24-hour lifetime and empty ``output.audio.data``.
  ``usage`` reports billed ``characters``, plus ``input_tokens`` / ``output_tokens`` for 3.1.
  Official pricing uses input/output tokens for 3.1-tts-flash and characters for 3.1-tts-plus / 3.0.
  Report available tokens faithfully; price tables determine pricing dimensions.

Domains: official examples use workspace-specific ``{WorkspaceId}.{地域}.maas.aliyuncs.com``;
``dashscope.aliyuncs.com`` is the legacy domain for identical ``/api/v1`` paths. Provider base_url
configuration determines either; this backend only appends paths. Sources:
https://help.aliyun.com/zh/model-studio/qwen-audio-tts-http-api ,
https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list ,
https://help.aliyun.com/zh/model-studio/regions .
"""

from __future__ import annotations

import logging
import re
from typing import Any

from lib.backends.audio_backends.base import AudioCapability, AudioSynthesisRequest, VoiceOption
from lib.backends.audio_backends.dashscope import DashScopeAudioBackend
from lib.backends.transport_layer import TransportLayer

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen-audio-3.1-tts-flash"

SPEECH_SYNTHESIZER_PATH = "/services/audio/tts/SpeechSynthesizer"

# Nonstreaming formats selected by output extension to keep bytes/extensions consistent (.wav resource paths).
_SUPPORTED_FORMATS = frozenset({"mp3", "pcm", "wav", "opus"})
_FALLBACK_FORMAT = "wav"
# Official examples use 24000, valid for opus and other formats.
_SAMPLE_RATE = 24000
# Legal ``rate`` interval; clamp excess rates instead of causing provider 400 errors.
_RATE_MIN = 0.5
_RATE_MAX = 2.0


def _voices(*voice_ids: str) -> tuple[VoiceOption, ...]:
    # label uses lib/i18n keys (voice_label_dashscope_*, lib/i18n/<locale>/assets.py), rendered by voice APIs in request
    # locales; dots never enter keys.
    return tuple(
        VoiceOption(id=voice_id, label=f"voice_label_dashscope_{voice_id.lower().replace('.', '_')}")
        for voice_id in voice_ids
    )


# Model-specific system voice subsets, sourced from https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list .
# Expose common Mandarin/English narration and character voices only, not minor-language voices or 500+ 3.0 basics.
# Other documented voice values still work: settings accept freely entered voice ids.
_V31_FLAGSHIP = ("longanhuan_v3.1", "longanlingxin_v3.1", "longanfengyue_v3.1", "xunanchuan_v3.1")
_VOICES_BY_MODEL: tuple[tuple[str, tuple[VoiceOption, ...]], ...] = (
    ("qwen-audio-3.1-tts-plus", _voices(*_V31_FLAGSHIP)),
    (
        "qwen-audio-3.1-tts-flash",
        _voices(
            *_V31_FLAGSHIP,
            "yuxiaoyun_v3.1",
            "wenhuaiqing_v3.1",
            "xieshurou_v3.1",
            "anmingyuan_v3.1",
            "huozhuoshi_v3.1",
            "longshu_v3.1",
            "longze_v3.1",
            "longxiaocheng_v3.1",
            "longke_v3.1",
            "Emily_v3.1",
            "Eric_v3.1",
            "Ava_v3.1",
            "Brian_v3.1",
        ),
    ),
    ("qwen-audio-3.0-tts-plus", _voices("longanlingxin", "longanlufeng")),
    (
        "qwen-audio-3.0-tts-flash",
        _voices(
            "longanfengyue",
            "longanyuanfei",
            "longanlingxi",
            "longanxiaoxin",
            "longanhuan_v3.6",
            "longjielidou_v3.6",
            "longpaopao_v3.6",
            "longhuohuo_v3.6",
            "longchuanshu_v3.6",
            "loongmary",
            "loongeva_v3.6",
            "loongjohn",
        ),
    ),
)
# Models with unrecognized generations, such as relay aliases/future same-endpoint models, use shared flagship 3.1 voices.
_FALLBACK_VOICES = _voices(*_V31_FLAGSHIP)


def voices_for_model(model: str) -> tuple[VoiceOption, ...]:
    """Available voice subsets by model prefix after stripping relay namespaces; dated snapshots share their generation's table."""
    canonical = re.split(r"[/:]", model.strip().lower())[-1]
    for prefix, voices in _VOICES_BY_MODEL:
        if canonical.startswith(prefix):
            return voices
    return _FALLBACK_VOICES


def _format_for(request: AudioSynthesisRequest) -> str:
    suffix = request.output_path.suffix.lstrip(".").lower()
    return suffix if suffix in _SUPPORTED_FORMATS else _FALLBACK_FORMAT


def _token_count(value: object) -> int | None:
    """Read nonnegative integer usage; missing, boolean, and other types mean unreported."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class DashScopeSpeechSynthesizerBackend(DashScopeAudioBackend):
    """Synchronous Alibaba Bailian SpeechSynthesizer synthesis backend."""

    _endpoint_path = SPEECH_SYNTHESIZER_PATH

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model or DEFAULT_MODEL,
            base_url=base_url,
            http_timeout=http_timeout,
            transport_layer=transport_layer,
        )

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH, AudioCapability.SPEECH_SPEED}

    def list_voices(self) -> list[VoiceOption]:
        return list(voices_for_model(self._model))

    def _synthesis_input(self, request: AudioSynthesisRequest) -> dict[str, Any]:
        # Omit language_type: optional language_hints targets languages, but incoming language_type is a fixed Chinese default,
        # not text-derived; sending it would misdirect English and other synthesis.
        body: dict[str, Any] = {
            "text": request.text,
            "voice": request.voice,
            "format": _format_for(request),
            "sample_rate": _SAMPLE_RATE,
        }
        if request.speed is not None:
            rate = min(max(request.speed, _RATE_MIN), _RATE_MAX)
            if rate != request.speed:
                logger.debug("DashScope SpeechSynthesizer 语速 %s 超出 [0.5, 2.0]，按 %s 提交", request.speed, rate)
            body["rate"] = rate
        return body

    def _usage_tokens(self, response: dict[str, Any]) -> tuple[int | None, int | None]:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return None, None
        return _token_count(usage.get("input_tokens")), _token_count(usage.get("output_tokens"))
