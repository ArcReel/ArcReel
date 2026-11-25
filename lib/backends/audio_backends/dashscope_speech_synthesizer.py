"""DashScopeSpeechSynthesizerBackend — 阿里百炼 qwen-audio TTS 语音合成后端（同步 SpeechSynthesizer）。

走 ``POST {host}/api/v1/services/audio/tts/SpeechSynthesizer``，收录 ``qwen-audio-3.1-tts-flash`` /
``qwen-audio-3.1-tts-plus`` / ``qwen-audio-3.0-tts-flash`` / ``qwen-audio-3.0-tts-plus``。与
multimodal-generation 端点（:class:`DashScopeAudioBackend`）的差异：

- 请求体 ``input`` 携带 ``text`` / ``voice`` / ``format`` / ``sample_rate``，语速字段是 ``rate``
  （0.5–2.0），没有 ``language_type``。
- 音色按模型区分，不能跨模型混用（不匹配时供应商回 ``InvalidParameter``），音色表因此按模型声明。
- 非流式响应同样在 ``output.audio.url`` 给出音频文件 URL（24h 有效，``output.audio.data`` 为空串），
  用量在 ``usage``：``characters`` 是计费字符数，3.1 系另报 ``input_tokens`` / ``output_tokens``。
  官方说明 3.1-tts-flash 按输入与输出 token 计费，3.1-tts-plus 与 3.0 系按字符计费；本后端有 token
  就如实上报，计价口径由价目表决定。

接入域名：官方示例用业务空间专属域名 ``{WorkspaceId}.{地域}.maas.aliyuncs.com``，DashScope 域名
（``dashscope.aliyuncs.com``）是同一套 ``/api/v1`` 路径的存量域名；两者都由供应商配置的 base_url 决定，
本后端只拼路径。出处：https://help.aliyun.com/zh/model-studio/qwen-audio-tts-http-api 、
https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list 、
https://help.aliyun.com/zh/model-studio/regions 。
"""

from __future__ import annotations

import logging
from typing import Any

from lib.backends.audio_backends.base import AudioCapability, AudioSynthesisRequest, VoiceOption
from lib.backends.audio_backends.dashscope import DashScopeAudioBackend
from lib.backends.audio_profiles import endpoint_profile
from lib.backends.audio_profiles import voices_for_model as voices_for_model
from lib.backends.transport_layer import TransportLayer

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen-audio-3.1-tts-flash"

SPEECH_SYNTHESIZER_PATH = "/services/audio/tts/SpeechSynthesizer"

_SUPPORTED_FORMATS = frozenset({"mp3", "pcm", "wav", "opus"})
_FALLBACK_FORMAT = "wav"
_SAMPLE_RATE = 24000


def _format_for(request: AudioSynthesisRequest) -> str:
    suffix = request.output_path.suffix.lstrip(".").lower()
    return suffix if suffix in _SUPPORTED_FORMATS else _FALLBACK_FORMAT


def _token_count(value: object) -> int | None:
    """用量字段取非负整数；缺失、布尔或其他类型一律视为没有回报。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class DashScopeSpeechSynthesizerBackend(DashScopeAudioBackend):
    """阿里百炼语音合成后端（同步 SpeechSynthesizer 端点）。"""

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
        return list(endpoint_profile("dashscope-speech-synthesizer", self._model).voices)

    def _synthesis_input(self, request: AudioSynthesisRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "text": request.text,
            "voice": request.voice,
            "format": _format_for(request),
            "sample_rate": _SAMPLE_RATE,
        }
        endpoint_profile("dashscope-speech-synthesizer", self._model).validate(request.voice, request.speed)
        if request.speed is not None:
            body["rate"] = request.speed
        return body

    def _usage_tokens(self, response: dict[str, Any]) -> tuple[int | None, int | None]:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return None, None
        return _token_count(usage.get("input_tokens")), _token_count(usage.get("output_tokens"))
