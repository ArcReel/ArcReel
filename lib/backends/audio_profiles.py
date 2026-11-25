"""语音合成端点的静态音色与参数契约。"""

from dataclasses import dataclass

from lib.infra.api_errors import UnprocessableError
from lib.speech.narration_config import parse_tts_speed


@dataclass(frozen=True)
class VoiceOption:
    """音色目录条目。``id`` 是合成请求携带的 ``voice`` 参数值，``label`` 是给人看的展示名。"""

    id: str
    label: str


_QWEN3_TTS_FLASH = "qwen3-tts-flash"
DASHSCOPE_VOICES: tuple[VoiceOption, ...] = (
    VoiceOption(id="Cherry", label="voice_label_dashscope_cherry"),
    VoiceOption(id="Serena", label="voice_label_dashscope_serena"),
    VoiceOption(id="Ethan", label="voice_label_dashscope_ethan"),
    VoiceOption(id="Chelsie", label="voice_label_dashscope_chelsie"),
    VoiceOption(id="Nofish", label="voice_label_dashscope_nofish"),
    VoiceOption(id="Jennifer", label="voice_label_dashscope_jennifer"),
    VoiceOption(id="Ryan", label="voice_label_dashscope_ryan"),
    VoiceOption(id="Bellona", label="voice_label_dashscope_bellona"),
    VoiceOption(id="Neil", label="voice_label_dashscope_neil"),
    VoiceOption(id="Elias", label="voice_label_dashscope_elias"),
    VoiceOption(id="Momo", label="voice_label_dashscope_momo"),
    VoiceOption(id="Vivian", label="voice_label_dashscope_vivian"),
    VoiceOption(id="Moon", label="voice_label_dashscope_moon"),
    VoiceOption(id="Maia", label="voice_label_dashscope_maia"),
    VoiceOption(id="Kai", label="voice_label_dashscope_kai"),
    VoiceOption(id="Katerina", label="voice_label_dashscope_katerina"),
    VoiceOption(id="Bella", label="voice_label_dashscope_bella"),
    VoiceOption(id="Eldric Sage", label="voice_label_dashscope_eldric_sage"),
    VoiceOption(id="Vincent", label="voice_label_dashscope_vincent"),
)


def _voices(*voice_ids: str) -> tuple[VoiceOption, ...]:
    return tuple(
        VoiceOption(id=voice_id, label=f"voice_label_dashscope_{voice_id.lower().replace('.', '_')}")
        for voice_id in voice_ids
    )


_V31_FLAGSHIP = ("longanhuan_v3.1", "longanlingxin_v3.1", "longanfengyue_v3.1", "xunanchuan_v3.1")
_DASHSCOPE_SPEECH_SYNTHESIZER_BY_MODEL: dict[str, tuple[VoiceOption, ...]] = {
    "qwen-audio-3.1-tts-plus": _voices(*_V31_FLAGSHIP),
    "qwen-audio-3.1-tts-flash": _voices(
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
    "qwen-audio-3.0-tts-plus": _voices("longanlingxin", "longanlufeng"),
    "qwen-audio-3.0-tts-flash": _voices(
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
}


def voices_for_model(model: str) -> tuple[VoiceOption, ...]:
    """该模型可用的音色子集；音色表没有收录这个型号名时为空，不借用其他型号的音色。"""
    return _DASHSCOPE_SPEECH_SYNTHESIZER_BY_MODEL.get(model, ())


GEMINI_VOICES: tuple[VoiceOption, ...] = tuple(
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


GROK_VOICES: tuple[VoiceOption, ...] = tuple(
    VoiceOption(id=voice_id, label=voice_id.capitalize())
    for voice_id in (
        "eve",
        "ara",
        "rex",
        "sal",
        "leo",
        "carina",
        "zagan",
        "helix",
        "orion",
        "luna",
        "iris",
        "altair",
        "zenith",
        "perseus",
        "helios",
        "lux",
        "kepler",
        "rigel",
        "cosmo",
        "celeste",
        "ursa",
        "sirius",
        "lumen",
        "castor",
        "naksh",
        "atlas",
        "aurora",
        "liora",
    )
)


ARK_AGENT_PLAN_VOICES: tuple[VoiceOption, ...] = tuple(
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


MINIMAX_VOICES: tuple[VoiceOption, ...] = tuple(
    VoiceOption(id=voice_id, label=f"voice_label_minimax_{voice_id.replace('-', '_')}")
    for voice_id in (
        "male-qn-qingse",
        "male-qn-jingying",
        "male-qn-badao",
        "male-qn-daxuesheng",
        "female-shaonv",
        "female-yujie",
        "female-chengshu",
        "female-tianmei",
    )
)
_T2A_MODELS = frozenset(
    {
        "speech-2.8-hd",
        "speech-2.8-turbo",
        "speech-2.6-hd",
        "speech-2.6-turbo",
        "speech-02-hd",
        "speech-02-turbo",
        "speech-01-hd",
        "speech-01-turbo",
    }
)


OPENAI_VOICES: tuple[VoiceOption, ...] = tuple(
    VoiceOption(id=voice_id, label=voice_id)
    for voice_id in (
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "fable",
        "nova",
        "onyx",
        "sage",
        "shimmer",
        "verse",
        "marin",
        "cedar",
    )
)

_LEGACY_UNSUPPORTED_VOICE_IDS = frozenset({"ballad", "verse", "marin", "cedar"})
_OPENAI_BY_MODEL: dict[str, tuple[VoiceOption, ...]] = {
    "gpt-4o-mini-tts": OPENAI_VOICES,
    "gpt-4o-mini-tts-2025-12-15": OPENAI_VOICES,
    **dict.fromkeys(
        ("tts-1", "tts-1-hd"), tuple(v for v in OPENAI_VOICES if v.id not in _LEGACY_UNSUPPORTED_VOICE_IDS)
    ),
}


@dataclass(frozen=True)
class AudioProfile:
    voices: tuple[VoiceOption, ...] = ()
    supports_speed: bool = False
    bounds: tuple[float, float] | None = None
    closed: bool = False

    @property
    def first_voice(self) -> str:
        return self.voices[0].id if self.voices else ""

    def accepts_voice(self, voice: str) -> bool:
        return bool(voice.strip()) and (not self.closed or any(item.id == voice for item in self.voices))

    def validate(self, voice: str, speed: float | None) -> None:
        if not voice.strip():
            raise UnprocessableError("narration_tts_voice_required")
        if not self.accepts_voice(voice):
            raise UnprocessableError("narration_tts_voice_invalid")
        if speed is not None:
            parsed = parse_tts_speed(speed)
            if parsed is None:
                raise UnprocessableError("narration_tts_speed_invalid")
            if self.bounds:
                minimum, maximum = self.bounds
                if not minimum <= parsed <= maximum:
                    raise UnprocessableError(
                        "narration_tts_speed_out_of_range", speed=parsed, minimum=minimum, maximum=maximum
                    )


def endpoint_profile(
    endpoint: str, model: str, *, official_host: bool = False, custom_provider: bool = False
) -> AudioProfile:
    match endpoint:
        case "dashscope-tts":
            return AudioProfile(DASHSCOPE_VOICES if model == _QWEN3_TTS_FLASH else ())
        case "dashscope-speech-synthesizer":
            return AudioProfile(voices_for_model(model), True, (0.5, 2.0))
        case "grok-tts":
            return AudioProfile(GROK_VOICES, True, (0.7, 1.5), closed=not custom_provider)
        case "gemini-tts":
            voices = GEMINI_VOICES if model in _VOICE_MODELS else ()
            return AudioProfile(voices, closed=bool(voices) and not custom_provider)
        case "ark-agent-plan-tts":
            return AudioProfile(ARK_AGENT_PLAN_VOICES, True, (0.5, 2.0))
        case "minimax-tts":
            return AudioProfile(MINIMAX_VOICES if model in _T2A_MODELS else (), True, (0.5, 2.0))
        case "openai-tts":
            return AudioProfile(
                _OPENAI_BY_MODEL.get(model, ()) if official_host else (), True, (0.25, 4.0) if official_host else None
            )
        case _:
            return AudioProfile()
