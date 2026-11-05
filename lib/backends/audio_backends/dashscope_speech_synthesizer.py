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
import re
from typing import Any

from lib.backends.audio_backends.base import AudioCapability, AudioSynthesisRequest, VoiceOption
from lib.backends.audio_backends.dashscope import DashScopeAudioBackend
from lib.backends.transport_layer import TransportLayer

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen-audio-3.1-tts-flash"

SPEECH_SYNTHESIZER_PATH = "/services/audio/tts/SpeechSynthesizer"

# 非流式可用的输出格式，按落盘扩展名选取，保证文件内容与扩展名一致（资源路径约定 .wav）。
_SUPPORTED_FORMATS = frozenset({"mp3", "pcm", "wav", "opus"})
_FALLBACK_FORMAT = "wav"
# 官方示例取 24000；该值对 opus 与其他格式都合法。
_SAMPLE_RATE = 24000
# ``rate`` 的合法区间，超出的语速收拢到边界而不是让供应商 400。
_RATE_MIN = 0.5
_RATE_MAX = 2.0


def _voices(*voice_ids: str) -> tuple[VoiceOption, ...]:
    # label 是 lib/i18n 翻译 key（voice_label_dashscope_*，见 lib/i18n/<locale>/assets.py），由音色接口按请求
    # locale 渲染；点号不进 key。
    return tuple(
        VoiceOption(id=voice_id, label=f"voice_label_dashscope_{voice_id.lower().replace('.', '_')}")
        for voice_id in voice_ids
    )


# 各模型的系统音色子集，出处：https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list 。
# 只收旁白与角色配音常用的普通话与英文音色；小语种音色与 3.0 系 500+ 基础音色不在此暴露，
# 照文档填写 voice 照常可用（设置页的音色是可自由填写的输入框）。
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
# 名字认不出代际的模型（中转别名、后续同端点的新模型）取 3.1 两款共有的旗舰音色。
_FALLBACK_VOICES = _voices(*_V31_FLAGSHIP)


def voices_for_model(model: str) -> tuple[VoiceOption, ...]:
    """该模型可用的音色子集：剥去中转命名空间前缀后按型号前缀匹配（带日期快照后缀的同代型号同表）。"""
    canonical = re.split(r"[/:]", model.strip().lower())[-1]
    for prefix, voices in _VOICES_BY_MODEL:
        if canonical.startswith(prefix):
            return voices
    return _FALLBACK_VOICES


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
        return list(voices_for_model(self._model))

    def _synthesis_input(self, request: AudioSynthesisRequest) -> dict[str, Any]:
        # language_type 不发送：本端点的 language_hints 是可选的目标语种提示，而上游传来的
        # language_type 是固定缺省值（Chinese），并非按文本判定，发出去反会误导英文等文本的合成。
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
