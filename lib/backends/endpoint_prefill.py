"""按模型名称预填调用端点的规则，供内置与自定义供应商共用。"""

from __future__ import annotations

import re
from collections.abc import Mapping

from arcreel_catalog_core import BUILTIN_ENDPOINTS
from lib.backends.builtin_endpoints import get_provider_endpoints
from lib.backends.dashscope_model_names import classify_wan_model
from lib.catalog import query as catalog

_PROVIDER_IMAGE_PREFILL = frozenset({"ark", "ark-agent-plan", "dashscope", "agnes"})
_GEMINI_INTERACTIONS_ENDPOINTS = {
    "gemini-aistudio": "gemini-interactions-video",
    "gemini-vertex": "gemini-vertex-interactions-video",
}


def _prefill_media_type(endpoint: str) -> str:
    """名称推断的端点媒体类型，含仅供自定义供应商使用的 OpenAI TTS。"""
    if endpoint == "openai-tts":
        return "audio"
    return BUILTIN_ENDPOINTS[endpoint].media_type


def seedance_endpoint_for_name(name: str) -> str | None:
    """名字里写明的 Seedance 代际对应的端点；名字不带 2.0 / 2.5 时为 ``None``。

    用 ``in`` 子串兼容多套前缀命名（doubao- 火山国内站 / dreamina- BytePlus 国际站），版本号收窄到已登记的代际：同代的
    未知后缀（fast、mini、日期快照）落同一端点。只用于添加模型时的预填
    （``lib.custom_provider.endpoints.infer_endpoint``）。
    alembic ``e5b1c9a3d7f2`` 冻结了一份同样的规则回填已有模型。
    """
    lowered = name.lower()
    if "seedance-2-5" in lowered or "seedance-2.5" in lowered:
        return "ark-seedance-2-5"
    if "seedance-2-0" in lowered or "seedance-2.0" in lowered:
        return "ark-seedance-2"
    return None


_VIDU_PROFILES = {
    "viduq3-pro": "q3-pro",
    "viduq3-turbo": "q3-turbo",
    "viduq3-pro-fast": "q3-fast",
    "viduq3": "q3-reference",
    "viduq3-drama": "q3-drama",
    "viduq3-mix": "q3-mix",
    "viduq3-ad": "q3-ad",
    "viduq2": "q2",
    "viduq2-pro": "q2-pro",
    "viduq2-pro-fast": "q2-fast",
    "viduq2-turbo": "q2-fast",
    "viduq1": "q1",
    "viduq1-classic": "q1-classic",
    "vidu2.0": "2",
    "viduq4-preview": "q4",
}
_KLING_PROFILES = {
    "kling-v2-5-turbo": "pro-silent",
    "kling-v2-6": "pro-audio",
    "kling-v3": "audio",
    "kling-v3-omni": "audio",
    "kling-video-o1": "silent",
    "kling-4.0": "model-path",
    "kling-4.0-flash": "model-path",
    "kling-3.0-turbo": "model-path",
}


def request_profile_endpoint_for_name(provider_id: str, name: str) -> str | None:
    """按名字识别 Grok、Vidu、可灵视频的请求端点，只用于添加时预填；迁移冻结了同一规则。"""
    if provider_id == "grok":
        return "grok-video-classic" if name == "grok-imagine-video" else None
    if provider_id == "vidu":
        profile = _VIDU_PROFILES.get(name)
        return f"vidu-video-{profile}" if profile else None
    if provider_id == "kling":
        profile = _KLING_PROFILES.get(name.replace(":", "/").rsplit("/", 1)[-1].strip().lower())
        return f"kling-video-{profile}" if profile else None
    return None


def image_endpoint_for_name(provider_id: str, model_id: str) -> str:
    """图片端点的名字规则，仅用于新增模型预填。"""
    lowered = model_id.lower()
    if provider_id in ("ark", "ark-agent-plan"):
        return "ark-seedream-3" if "seedream-3" in lowered else "ark-seedream-4"
    if provider_id == "dashscope":
        if lowered.startswith("wan"):
            return "dashscope-wan-image"
        return "dashscope-qwen-image-edit" if "qwen-image-edit" in lowered else "dashscope-qwen-image"
    return "agnes-image-tier" if model_id.startswith("agnes-image-2.5") else "agnes-image-pixels"


def dashscope_video_endpoint_for_name(name: str) -> str:
    """按名字预填百炼请求契约；执行时只读取已经选定的端点。"""
    normalized = name.strip().lower()
    if re.search(r"(?<![a-z0-9])wan[-_]?3(?![a-z0-9])", normalized):
        return "dashscope-wan3-video"
    wan27 = re.search(r"(?<![a-z0-9])wan[-_]?2\.7(?![a-z0-9])", normalized)
    if wan27:
        suffix = normalized[wan27.start() :]
        if re.search(r"(?<![a-z0-9])(?:video[-_]?edit|[sv]2v)(?![a-z0-9])", suffix):
            return "dashscope-video"
        suffix = re.sub(r"wan[-_]?2\.7[_-]", "wan2.7-", suffix)
        suffix = re.sub(r"(?<![a-z0-9])image[-_]?(?:to|2)[-_]?video(?![a-z0-9])", "i2v", suffix)
        normalized = normalized[: wan27.start()] + suffix
    if re.search(r"(?<![a-z0-9])wan2\.7-r2v(?![a-z0-9])", normalized):
        return "dashscope-reference-voice-video"
    if re.search(r"(?<![a-z0-9])happyhorse-1\.[01]-r2v(?![a-z0-9])", normalized):
        return "dashscope-reference-video"
    if re.search(r"(?<![a-z0-9])(?:wan2\.7|happyhorse-1\.[01])-(?:t2v|i2v)(?![a-z0-9])", normalized):
        return "dashscope-always-on-video"
    return "dashscope-video"


def agnes_video_endpoint_for_name(name: str) -> str | None:
    """按已知代际预填 Agnes 契约；未识别时由供应商默认端点决定。"""
    if name in {"agnes-video-2.5", "agnes-video-2.5-flash"}:
        return "agnes-video-2-5"
    if name == "agnes-video-v2.0":
        return "agnes-video-2"
    return None


def gemini_video_endpoint_for_name(provider_id: str, name: str) -> str | None:
    """按名字预填 Gemini 视频的请求协议：Gemini Omni 走 Interactions API，其余由供应商默认端点决定。"""
    if "gemini-omni" not in name.strip().lower():
        return None
    return _GEMINI_INTERACTIONS_ENDPOINTS[provider_id]


def infer_builtin_endpoint(provider_id: str, media_type: str, model_id: str) -> str:
    """内置供应商下添加模型时预填的端点：按名字推断（与自定义供应商同一套规则），不在可选端点里时取默认端点。"""
    if media_type == "image" and provider_id in _PROVIDER_IMAGE_PREFILL:
        return image_endpoint_for_name(provider_id, model_id)
    declared = get_provider_endpoints(provider_id, media_type)
    if media_type == "video" and provider_id == "grok":
        entry = catalog.find_entry(model_id, media_type="video")
        names = (entry.name, *entry.aliases) if entry is not None else (model_id,)
        for name in names:
            endpoint = request_profile_endpoint_for_name(provider_id, name)
            if endpoint is not None:
                return endpoint
        return declared.default
    if media_type == "video" and provider_id in _GEMINI_INTERACTIONS_ENDPOINTS:
        return gemini_video_endpoint_for_name(provider_id, model_id) or declared.default
    discovery_format = "google" if provider_id in _GEMINI_INTERACTIONS_ENDPOINTS else "openai"
    inferred = (
        agnes_video_endpoint_for_name(model_id) if provider_id == "agnes" and media_type == "video" else None
    ) or infer_endpoint(model_id, discovery_format, media_type)
    return inferred if inferred in declared.endpoints else declared.default


_IMAGE_PATTERN = re.compile(r"image|dall|img|imagen|flux|seedream|jimeng|viduq[12](?:[-_].*)?", re.IGNORECASE)
_VIDEO_PATTERN = re.compile(
    r"video|sora|kling|wan|seedance|cog|mochi|veo|pika|runway|"
    r"vidu2(?:\.0)?(?:[-_].*)?|viduq[34](?:[-_].*)?",
    re.IGNORECASE,
)
_AUDIO_PATTERN = re.compile(r"tts|speech|cosyvoice", re.IGNORECASE)
_QWEN_TTS_PATTERN = re.compile(r"qwen3?-tts", re.IGNORECASE)
_QWEN_AUDIO_TTS_PATTERN = re.compile(r"qwen-audio-[\d.]+-tts", re.IGNORECASE)
_MINIMAX_TTS_PATTERN = re.compile(r"speech-[\d.]+-(?:hd|turbo)")
_ASR_PATTERN = re.compile(r"transcribe|speech.?to.?text|recognition", re.IGNORECASE)

_MINIMAX_FAST_MODELS = frozenset({"minimax-hailuo-2.3-fast"})
_MINIMAX_S2V_MODELS = frozenset({"s2v-01", "minimax-s2v-01"})


_FAMILY_ENDPOINTS: tuple[tuple[str, Mapping[str, str]], ...] = (
    ("kling", {"image": "kling-image", "video": "kling-video-pro-silent"}),
    ("gemini", {"text": "gemini-generate", "image": "gemini-image", "audio": "gemini-tts"}),
    ("seedance", {"video": "ark-seedance-1"}),
    ("seedream", {"image": "ark-seedream-4"}),
    ("viduq", {"video": "vidu-video-q3-turbo"}),
    ("vidu2", {"video": "vidu-video-2"}),
    ("hailuo", {"video": "minimax-hailuo-v1"}),
)

_DEFAULT_ENDPOINTS: Mapping[str, tuple[str, str]] = {
    "text": ("gemini-generate", "openai-chat"),
    "image": ("gemini-image", "openai-images"),
    "video": ("gemini-video", "openai-video"),
    "audio": ("gemini-tts", "openai-tts"),
}


def _request_profile_endpoint(provider: str, model_id: str) -> str:
    entry = catalog.find_entry(model_id, media_type="video")
    names = (entry.name, *entry.aliases) if entry is not None else (model_id,)
    for name in names:
        endpoint = request_profile_endpoint_for_name(provider, name)
        if endpoint is not None:
            return endpoint
    return {
        "vidu": "vidu-video-generic",
        "kling": "kling-video-silent",
    }[provider]


def _seedance_endpoint(model_id: str) -> str:
    """名字含 seedance 的视频模型预填的端点：名字写明 2.5 / 2.0 时取对应代际，否则取 1.x 端点。"""

    return seedance_endpoint_for_name(model_id) or "ark-seedance-1"


def infer_endpoint(model_id: str, discovery_format: str, media_type: str | None = None) -> str:
    """推默认调用端点；``media_type`` 是模型已知的媒体类型（对应上的条目给出），不给时按名字一并推断。

    给了媒体类型时，名字规则推出的端点属于这个媒体类型就用它；否则在这个媒体类型内按名字族选
    （见 :data:`_FAMILY_ENDPOINTS`），选不到时取该媒体类型在当前模型发现协议下的默认端点（docs/adr/0105）。
    """
    by_name = _infer_endpoint_by_name(model_id, discovery_format)
    if media_type is None or _prefill_media_type(by_name) == media_type:
        return by_name
    lowered = model_id.lower()
    for token, endpoints in _FAMILY_ENDPOINTS:
        if token in lowered and media_type in endpoints:
            if token == "seedance":
                return _seedance_endpoint(model_id)
            if token == "seedream":
                return image_endpoint_for_name("ark", model_id)
            if media_type == "video" and ("kling" in token or "vidu" in token):
                return _request_profile_endpoint("kling" if "kling" in token else "vidu", model_id)
            return endpoints[media_type]
    google_default, other_default = _DEFAULT_ENDPOINTS[media_type]
    return google_default if discovery_format == "google" else other_default


def infer_media_type(model_id: str, discovery_format: str) -> str:
    """按名字推断的媒体类型，即名字规则推出的端点的媒体类型。"""
    return _prefill_media_type(_infer_endpoint_by_name(model_id, discovery_format))


def _infer_endpoint_by_name(model_id: str, discovery_format: str) -> str:
    """根据模型 id 与 discovery_format 推默认 endpoint（content-first）。

    model id 内容优先于 discovery_format：中转站普遍 discovery_format="openai"，但模型
    列表常夹带 gemini-* 原生 id，必须按内容纠偏到 Google 端点，否则被错推到
    openai-chat/openai-images，每次都要手动改回。

    1) 阿里百炼视频 → happyhorse / wan2.7 / wan3 家族（含 wan-2.7-xxx / wan-3-xxx 连字符形态、
       image-to-video 续接别名）走 "百炼异步视频端点"（原生异步端点）。happyhorse 不在
       _VIDEO_PATTERN 须显式；万相视频抢在通用 is_video 前拦截。真正的图像变体不自动推 dashscope
       （中转可能是 OpenAI 兼容）：qwen-image / wan2.7-image / wan-2.7-image / wan3.0-video-image
       及带版本/日期后缀的同类 id 落到图像家族推断；wan-3-turbo-image-to-video /
       wan3-image2video 这类显式 image-to-video 续接语法仍归视频（同 2.5 节 kling-image2video
       的处理原则），按 classify_wan_model 的 is_image_to_video 精确挑出这一种形态，不对图像变体
       的命名形态（结尾 token 等）做任何假设。原生路由只认 2.7（含点号形态 "wan2.x"，见下方
       classify_wan_model 的说明）与 wan3；其余 2.x 连字符/下划线形态（wan-2.1、wan_2.2-s2v 等）
       落到下方 4) 的通用视频端点。2.7 家族内 videoedit 模态（wan2.7-videoedit）本后端未实现
       请求构造，同样排除出原生路由（见 classify_wan_model 的 is_videoedit 处的说明）。
    2) MiniMax 原生 token → 海螺（Fast 与非 Fast 各一键）/ S2V / H3 分别走各自声明式端点，
       image-01 走 "minimax-image"，MiniMax-H3-Image 系列走 "minimax-h3-image"。先于通用
       is_video/is_image 拦截：s2v 不在 _VIDEO_PATTERN、image-01 含 "image" 否则会被推到通用图像家族。
       输入受限的 Fast / S2V 只认精确型号名（剥离中转命名空间前缀后比较），非精确海螺别名落
       通用海螺键，其余 s2v 形态落 4) 的通用视频端点。
    2.5) 可灵 kling token → 含 video 语义优先归 "kling-video"（kling-image2video 等 i2v 含 image
       语义但本质是视频）；其余含 image 语义走 "kling-image"，否则走 "kling-video"。kling 同时命中
       _VIDEO_PATTERN，须先于通用 is_video 拦截，否则视频会落到 openai-video；v3-omni 图像/视频同名
       默认归视频、图像手动选。
    3) gemini-omni → "gemini-interactions-video"；其余 gemini 原生模型（非 video）→ TTS 走 "gemini-tts"，
       image 形态走 "gemini-image"，否则文本走 "gemini-generate"（不分 discovery_format）
    4) 视频家族 → seedance 按名字里的代际取 "ark-seedance-2-5" / "ark-seedance-2"，没写明代际取
       "ark-seedance-1"；viduq3 / viduq4→"vidu-video"、Veo 且 discovery_format=google→"gemini-video"、否则 "openai-video"
    5) 图像家族 → seedream→"ark-seedream-3" / "ark-seedream-4"（不分 discovery_format），其余 discovery_format=google 走
       "gemini-image" 否则 "openai-images"（imagen 随图像家族推断，不再单独拦截）
    6) TTS 家族（tts/speech/cosyvoice）→ qwen-audio-<版本>-tts 走 "dashscope-speech-synthesizer"，
       qwen-tts / qwen3-tts 走 "dashscope-tts"，MiniMax 的
       speech-<版本>-hd|turbo 走 "minimax-tts"，其余 "openai-tts"
       （不分 discovery_format；precedence 在 text 默认之前）
    7) 默认（文本）→ discovery_format=google 走 "gemini-generate" 否则 "openai-chat"
    """
    lowered = model_id.lower()
    is_image = bool(_IMAGE_PATTERN.search(model_id))
    classification = classify_wan_model(model_id)
    is_wan_family = classification.family is not None and classification.family != "happyhorse"
    contains_wan_token = "wan" in lowered
    wan_video_continuation = contains_wan_token and (
        classification.is_image_to_video
        or (classification.family == "wan2.7" and classification.is_known_video_modality)
    )
    wan_image_variant = contains_wan_token and is_image and not wan_video_continuation
    wan_unsupported_modality = is_wan_family and not classification.has_known_modality

    if classification.family == "happyhorse":
        return dashscope_video_endpoint_for_name(model_id)
    if is_wan_family and not wan_image_variant and not wan_unsupported_modality:
        return dashscope_video_endpoint_for_name(model_id)

    canonical = re.split(r"[/:]", lowered)[-1]
    if "hailuo" in lowered:
        return "minimax-hailuo-v1-fast" if canonical in _MINIMAX_FAST_MODELS else "minimax-hailuo-v1"
    if canonical in _MINIMAX_S2V_MODELS:
        return "minimax-s2v-01"
    if "minimax-h3-image" in lowered:
        return "minimax-h3-image"
    if "minimax-h3" in lowered:
        return "minimax-h3"
    if "image-01" in lowered:
        return "minimax-image"

    if "kling" in lowered:
        if "video" in lowered:
            return _request_profile_endpoint("kling", model_id)
        return "kling-image" if is_image else _request_profile_endpoint("kling", model_id)

    is_video = bool(_VIDEO_PATTERN.search(model_id)) and not wan_image_variant

    if "gemini-omni" in lowered:
        return "gemini-interactions-video"
    if "gemini" in lowered and not is_video:
        if "tts" in lowered:
            return "gemini-tts"
        return "gemini-image" if is_image else "gemini-generate"
    if is_video:
        if "seedance" in lowered:
            return _seedance_endpoint(model_id)
        if "viduq3" in lowered or "viduq4" in lowered:
            return _request_profile_endpoint("vidu", model_id)
        if "veo" in lowered and discovery_format == "google":
            return "gemini-video"
        return "openai-video"
    if is_image:
        if "seedream" in lowered:
            return image_endpoint_for_name("ark", model_id)
        return "gemini-image" if discovery_format == "google" else "openai-images"
    if _AUDIO_PATTERN.search(model_id) and not _ASR_PATTERN.search(model_id):
        if _QWEN_AUDIO_TTS_PATTERN.search(model_id):
            return "dashscope-speech-synthesizer"
        if _QWEN_TTS_PATTERN.search(model_id):
            return "dashscope-tts"
        return "minimax-tts" if _MINIMAX_TTS_PATTERN.fullmatch(canonical) else "openai-tts"
    return "gemini-generate" if discovery_format == "google" else "openai-chat"
