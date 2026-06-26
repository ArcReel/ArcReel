from __future__ import annotations

from dataclasses import dataclass, field

from lib.ark_shared import ARK_BASE_URL
from lib.dashscope_shared import DASHSCOPE_BASE_URL
from lib.minimax_shared import MINIMAX_BASE_URL
from lib.pricing.types import (
    PerCharacter,
    PerImageByResolution,
    PerImageFlat,
    PerImageOpenAIToken,
    PerSecondMatrix,
    PerSecondTiered,
    PerToken,
    PerTokenVideo,
    PerVideoBucket,
    Pricing,
    ViduDelegate,
)


@dataclass(frozen=True)
class ModelInfo:
    display_name: str
    media_type: str
    capabilities: list[str]
    default: bool = False
    supported_durations: list[int] = field(default_factory=list)
    duration_resolution_constraints: dict[str, list[int]] = field(default_factory=dict)
    resolutions: list[str] = field(default_factory=list)
    # 参考生视频单镜头参考图上限；0 = 不适用（图像/文本模型，或视频模型未声明）。
    max_reference_images: int = 0
    # 计费定价声明（单一真相源）；None = 该模型按 provider 默认模型 / Gemini 默认费率兜底计费。
    pricing: Pricing | None = None
    # 从 UI 下拉剔除但保留条目（供"入队后、finish 前被下线"的边角仍能算价）。
    hidden: bool = False
    # 发给供应商 API 的模型名；None 时回退到 registry 键名。两栖模型（同一 API 模型名同时有
    # 图像 / 视频两个 registry 条目）用此字段让两条目共用一个 API 模型名，而 registry 键名各自
    # 唯一——键名兼作 UI 标识与计费查表键，不能重复，故 API 模型名需与键名解耦。
    api_model_name: str | None = None


@dataclass(frozen=True)
class ProviderMeta:
    display_name: str
    description: str
    required_keys: list[str]
    optional_keys: list[str] = field(default_factory=list)
    secret_keys: list[str] = field(default_factory=list)
    models: dict[str, ModelInfo] = field(default_factory=dict)
    default_base_url: str | None = None

    @property
    def media_types(self) -> list[str]:
        return sorted(set(m.media_type for m in self.models.values()))

    @property
    def capabilities(self) -> list[str]:
        return sorted(set(c for m in self.models.values() for c in m.capabilities))


# Gemini 文本费率（美元/百万 token），Standard paid tier、prompt ≤200K 区间。
def _gemini_text_pricing(model_id: str, input_rate: float, output_rate: float) -> PerToken:
    return PerToken(
        rates={model_id: {"input": input_rate, "output": output_rate}},
        default_model=model_id,
        currency="USD",
    )


# Gemini 图片费率（美元/张），按分辨率档位。
def _gemini_image_pricing(model_id: str, rates: dict[str, float]) -> PerImageByResolution:
    return PerImageByResolution(rates={model_id: rates}, default_model=model_id, currency="USD")


# Veo 视频费率（美元/秒），按 (分辨率, 是否生成音频)。
def _veo_video_pricing(model_id: str, rates: dict[tuple[str, bool | None], float]) -> PerSecondMatrix:
    return PerSecondMatrix(
        rates={model_id: rates},
        default_model=model_id,
        dimensions="resolution_audio",
        currency="USD",
    )


_VEO_STANDARD_RATES: dict[tuple[str, bool | None], float] = {
    ("720p", True): 0.40,
    ("720p", False): 0.20,
    ("1080p", True): 0.40,
    ("1080p", False): 0.20,
    ("4k", True): 0.60,
    ("4k", False): 0.40,
}
_VEO_FAST_RATES: dict[tuple[str, bool | None], float] = {
    ("720p", True): 0.15,
    ("720p", False): 0.10,
    ("1080p", True): 0.15,
    ("1080p", False): 0.10,
    ("4k", True): 0.35,
    ("4k", False): 0.30,
}
_VEO_LITE_RATES: dict[tuple[str, bool | None], float] = {
    ("720p", True): 0.05,
    ("720p", False): 0.05,
    ("1080p", True): 0.08,
    ("1080p", False): 0.08,
}


# Ark 文本费率（元/百万 token），在线推理、输入 [0, 32k] 区间。
def _ark_text_pricing(model_id: str, input_rate: float, output_rate: float) -> PerToken:
    return PerToken(
        rates={model_id: {"input": input_rate, "output": output_rate}},
        default_model=model_id,
        currency="CNY",
    )


# Ark 图片费率（元/张）。
def _ark_image_pricing(model_id: str, per_image: float) -> PerImageFlat:
    return PerImageFlat(rates={model_id: per_image}, default_model=model_id, currency="CNY")


# Ark 视频费率（元/百万 token），按 (service_tier, 是否生成音频)。
def _ark_video_pricing(model_id: str, rates: dict[tuple[str, bool], float]) -> PerTokenVideo:
    return PerTokenVideo(rates={model_id: rates}, default_model=model_id)


# Grok 文本费率（美元/百万 token）。
def _grok_text_pricing(model_id: str, input_rate: float, output_rate: float) -> PerToken:
    return PerToken(
        rates={model_id: {"input": input_rate, "output": output_rate}},
        default_model=model_id,
        currency="USD",
    )


# Grok 图片费率（美元/张）。
def _grok_image_pricing(model_id: str, per_image: float) -> PerImageFlat:
    return PerImageFlat(rates={model_id: per_image}, default_model=model_id, currency="USD")


# OpenAI 文本费率（美元/百万 token）。
def _openai_text_pricing(model_id: str, input_rate: float, output_rate: float) -> PerToken:
    return PerToken(
        rates={model_id: {"input": input_rate, "output": output_rate}},
        default_model=model_id,
        currency="USD",
    )


# OpenAI 图片费率：token 主路径 + (quality, size) 兜底表。
def _openai_image_pricing(
    model_id: str,
    token_rates: dict[str, float],
    fallback_rates: dict[tuple[str, str], float],
) -> PerImageOpenAIToken:
    return PerImageOpenAIToken(
        token_rates={model_id: token_rates},
        fallback_rates={model_id: fallback_rates},
        default_model=model_id,
        currency="USD",
    )


# Sora 视频费率（美元/秒），按分辨率。
def _sora_video_pricing(model_id: str, rates: dict[str, float]) -> PerSecondMatrix:
    return PerSecondMatrix(
        rates={model_id: {(res, None): rate for res, rate in rates.items()}},
        default_model=model_id,
        dimensions="resolution_only",
        currency="USD",
    )


# DashScope（阿里百炼）文本费率（元/百万 token），标准在线推理价。
def _dashscope_text_pricing(model_id: str, input_rate: float, output_rate: float) -> PerToken:
    return PerToken(
        rates={model_id: {"input": input_rate, "output": output_rate}},
        default_model=model_id,
        currency="CNY",
    )


# DashScope 图片费率（元/张），T2I 与 I2I 同价。
def _dashscope_image_pricing(model_id: str, per_image: float) -> PerImageFlat:
    return PerImageFlat(rates={model_id: per_image}, default_model=model_id, currency="CNY")


# DashScope 视频费率（元/秒），按分辨率（音频恒开，不入计费维度）。
def _dashscope_video_pricing(model_id: str, rates: dict[str, float]) -> PerSecondMatrix:
    return PerSecondMatrix(
        rates={model_id: {(res, None): rate for res, rate in rates.items()}},
        default_model=model_id,
        dimensions="resolution_only",
        currency="CNY",
    )


# DashScope 语音合成费率（元/万字符）。
def _dashscope_audio_pricing(model_id: str, per_10k_chars: float) -> PerCharacter:
    return PerCharacter(rates={model_id: per_10k_chars}, default_model=model_id, currency="CNY")


# MiniMax（海螺）文本费率（元/百万 token），标准在线推理价；缓存折扣首批不建模。
def _minimax_text_pricing(model_id: str, input_rate: float, output_rate: float) -> PerToken:
    return PerToken(
        rates={model_id: {"input": input_rate, "output": output_rate}},
        default_model=model_id,
        currency="CNY",
    )


# MiniMax 图片费率（元/张），T2I 与 I2I 同价。
def _minimax_image_pricing(model_id: str, per_image: float) -> PerImageFlat:
    return PerImageFlat(rates={model_id: per_image}, default_model=model_id, currency="CNY")


# MiniMax 海螺视频按 (分辨率, 时长) 离散档计费（元/次，CNY）。
def _minimax_video_pricing(model_id: str, buckets: dict[tuple[str, int], float]) -> PerVideoBucket:
    return PerVideoBucket(rates={model_id: buckets}, default_model=model_id, currency="CNY")


# 可灵 Kling 视频「质量档 × 是否有声」¥/s 矩阵（官方一手核实，CNY，1 积分 = ¥1）。
# 全部 video 模型共享同一档位矩阵（官方按维度组合定价、不分模型）：4K 档仅 v3/v3-omni 可达、
# 有声仅 v2-6（pro）；turbo 仅触达 std/pro 无声/有声档。
_KLING_VIDEO_TIERED_RATES: dict[tuple[str, bool], float] = {
    ("std", False): 0.6,
    ("std", True): 0.8,
    ("pro", False): 0.8,
    ("pro", True): 1.0,
    ("4k", False): 3.0,
    ("4k", True): 3.0,
}


def _kling_video_pricing(model_id: str) -> PerSecondTiered:
    return PerSecondTiered(rates={model_id: _KLING_VIDEO_TIERED_RATES}, default_model=model_id, currency="CNY")


# 可灵 Kling 图像费率（元/张，CNY，图像 1 积分 = ¥0.025，官方一手核实）。
# image-o1 各长宽比同价（flat）；v3-omni 按分辨率分档（1K/2K 同价、4K 翻倍）。
def _kling_image_flat_pricing(model_id: str, per_image: float) -> PerImageFlat:
    return PerImageFlat(rates={model_id: per_image}, default_model=model_id, currency="CNY")


def _kling_image_by_resolution_pricing(model_id: str, rates: dict[str, float]) -> PerImageByResolution:
    return PerImageByResolution(rates={model_id: rates}, default_model=model_id, currency="CNY")


PROVIDER_REGISTRY: dict[str, ProviderMeta] = {
    "gemini-aistudio": ProviderMeta(
        display_name="AI Studio",
        description="Google AI Studio 提供 Gemini 系列模型，支持图片和视频生成，适合快速原型和个人项目。",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_rpm", "video_rpm", "request_gap", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        models={
            # --- text ---
            "gemini-3.1-pro-preview": ModelInfo(
                display_name="Gemini 3.1 Pro",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                pricing=_gemini_text_pricing("gemini-3.1-pro-preview", 2.00, 12.00),
            ),
            "gemini-3-flash-preview": ModelInfo(
                display_name="Gemini 3 Flash",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                default=True,
                pricing=_gemini_text_pricing("gemini-3-flash-preview", 0.50, 3.00),
            ),
            "gemini-3.1-flash-lite-preview": ModelInfo(
                display_name="Gemini 3.1 Flash Lite",
                media_type="text",
                capabilities=["text_generation", "structured_output"],
                pricing=_gemini_text_pricing("gemini-3.1-flash-lite-preview", 0.25, 1.50),
            ),
            # --- image ---
            "gemini-3-pro-image-preview": ModelInfo(
                display_name="Gemini 3 Pro Image",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                resolutions=["1K", "2K", "4K"],
                pricing=_gemini_image_pricing("gemini-3-pro-image-preview", {"1K": 0.134, "2K": 0.134, "4K": 0.24}),
            ),
            "gemini-3.1-flash-image-preview": ModelInfo(
                display_name="Gemini 3.1 Flash Image",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
                resolutions=["1K", "2K", "4K"],
                pricing=_gemini_image_pricing(
                    "gemini-3.1-flash-image-preview",
                    {"512PX": 0.045, "1K": 0.067, "2K": 0.101, "4K": 0.151},
                ),
            ),
            # --- video ---
            "veo-3.1-generate-preview": ModelInfo(
                display_name="Veo 3.1",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "negative_prompt", "video_extend"],
                supported_durations=[4, 6, 8],
                duration_resolution_constraints={"1080p": [8]},
                resolutions=["720p", "1080p"],
                max_reference_images=3,
                pricing=_veo_video_pricing("veo-3.1-generate-preview", _VEO_STANDARD_RATES),
            ),
            "veo-3.1-fast-generate-preview": ModelInfo(
                display_name="Veo 3.1 Fast",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "negative_prompt", "video_extend"],
                supported_durations=[4, 6, 8],
                duration_resolution_constraints={"1080p": [8]},
                resolutions=["720p", "1080p"],
                max_reference_images=3,
                pricing=_veo_video_pricing("veo-3.1-fast-generate-preview", _VEO_FAST_RATES),
            ),
            "veo-3.1-lite-generate-preview": ModelInfo(
                display_name="Veo 3.1 Lite",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "negative_prompt", "video_extend"],
                default=True,
                supported_durations=[4, 6, 8],
                duration_resolution_constraints={"1080p": [8]},
                resolutions=["720p", "1080p"],
                max_reference_images=3,
                pricing=_veo_video_pricing("veo-3.1-lite-generate-preview", _VEO_LITE_RATES),
            ),
        },
    ),
    "gemini-vertex": ProviderMeta(
        display_name="Vertex AI",
        description="Google Cloud Vertex AI 企业级平台，支持 Gemini 和 Imagen 模型，提供更高配额和音频生成能力。",
        required_keys=["credentials_path"],
        optional_keys=["gcs_bucket", "image_rpm", "video_rpm", "request_gap", "image_max_workers", "video_max_workers"],
        secret_keys=[],
        models={
            # --- text ---
            "gemini-3.1-pro-preview": ModelInfo(
                display_name="Gemini 3.1 Pro",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                pricing=_gemini_text_pricing("gemini-3.1-pro-preview", 2.00, 12.00),
            ),
            "gemini-3-flash-preview": ModelInfo(
                display_name="Gemini 3 Flash",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                default=True,
                pricing=_gemini_text_pricing("gemini-3-flash-preview", 0.50, 3.00),
            ),
            "gemini-3.1-flash-lite-preview": ModelInfo(
                display_name="Gemini 3.1 Flash Lite",
                media_type="text",
                capabilities=["text_generation", "structured_output"],
                pricing=_gemini_text_pricing("gemini-3.1-flash-lite-preview", 0.25, 1.50),
            ),
            # --- image ---
            "gemini-3-pro-image-preview": ModelInfo(
                display_name="Gemini 3 Pro Image",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                resolutions=["1K", "2K", "4K"],
                pricing=_gemini_image_pricing("gemini-3-pro-image-preview", {"1K": 0.134, "2K": 0.134, "4K": 0.24}),
            ),
            "gemini-3.1-flash-image-preview": ModelInfo(
                display_name="Gemini 3.1 Flash Image",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
                resolutions=["1K", "2K", "4K"],
                pricing=_gemini_image_pricing(
                    "gemini-3.1-flash-image-preview",
                    {"512PX": 0.045, "1K": 0.067, "2K": 0.101, "4K": 0.151},
                ),
            ),
            # --- video ---
            "veo-3.1-generate-001": ModelInfo(
                display_name="Veo 3.1",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "negative_prompt", "video_extend"],
                supported_durations=[4, 6, 8],
                resolutions=["720p", "1080p"],
                max_reference_images=3,
                pricing=_veo_video_pricing("veo-3.1-generate-001", _VEO_STANDARD_RATES),
            ),
            "veo-3.1-fast-generate-001": ModelInfo(
                display_name="Veo 3.1 Fast",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "negative_prompt", "video_extend"],
                default=True,
                supported_durations=[4, 6, 8],
                resolutions=["720p", "1080p"],
                max_reference_images=3,
                pricing=_veo_video_pricing("veo-3.1-fast-generate-001", _VEO_FAST_RATES),
            ),
        },
    ),
    "ark": ProviderMeta(
        display_name="火山方舟",
        description="字节跳动火山方舟 AI 平台，支持 Seedance 视频生成和 Seedream 图片生成，具备音频生成和种子控制能力。",
        required_keys=["api_key"],
        optional_keys=["video_max_workers", "image_max_workers"],
        secret_keys=["api_key"],
        models={
            # --- text ---
            "doubao-seed-2-0-pro-260215": ModelInfo(
                display_name="豆包 Seed 2.0 Pro",
                media_type="text",
                capabilities=["text_generation", "vision"],
                pricing=_ark_text_pricing("doubao-seed-2-0-pro-260215", 3.20, 16.00),
            ),
            "doubao-seed-2-0-lite-260215": ModelInfo(
                display_name="豆包 Seed 2.0 Lite",
                media_type="text",
                capabilities=["text_generation", "vision"],
                default=True,
                pricing=_ark_text_pricing("doubao-seed-2-0-lite-260215", 0.60, 3.60),
            ),
            "doubao-seed-2-0-mini-260215": ModelInfo(
                display_name="豆包 Seed 2.0 Mini",
                media_type="text",
                capabilities=["text_generation", "vision"],
                pricing=_ark_text_pricing("doubao-seed-2-0-mini-260215", 0.20, 2.00),
            ),
            "doubao-seed-1-8-251228": ModelInfo(
                display_name="豆包 Seed 1.8",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                pricing=_ark_text_pricing("doubao-seed-1-8-251228", 0.80, 2.00),
            ),
            # --- image ---
            "doubao-seedream-5-0-lite-260128": ModelInfo(
                display_name="Seedream 5.0 Lite",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
                pricing=_ark_image_pricing("doubao-seedream-5-0-lite-260128", 0.22),
            ),
            "doubao-seedream-5-0-260128": ModelInfo(
                display_name="Seedream 5.0",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                pricing=_ark_image_pricing("doubao-seedream-5-0-260128", 0.22),
            ),
            "doubao-seedream-4-5-251128": ModelInfo(
                display_name="Seedream 4.5",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                pricing=_ark_image_pricing("doubao-seedream-4-5-251128", 0.25),
            ),
            "doubao-seedream-4-0-250828": ModelInfo(
                display_name="Seedream 4.0",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                pricing=_ark_image_pricing("doubao-seedream-4-0-250828", 0.20),
            ),
            # --- video ---
            "doubao-seedance-1-5-pro-251215": ModelInfo(
                display_name="Seedance 1.5 Pro",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "flex_tier"],
                supported_durations=list(range(4, 13)),
                resolutions=["480p", "720p", "1080p"],
                max_reference_images=9,
                pricing=_ark_video_pricing(
                    "doubao-seedance-1-5-pro-251215",
                    {
                        ("default", True): 16.00,
                        ("default", False): 8.00,
                        ("flex", True): 8.00,
                        ("flex", False): 4.00,
                    },
                ),
            ),
            "doubao-seedance-2-0-260128": ModelInfo(
                display_name="Seedance 2.0",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "video_extend"],
                supported_durations=list(range(4, 16)),
                resolutions=["480p", "720p", "1080p"],
                max_reference_images=9,
                pricing=_ark_video_pricing(
                    "doubao-seedance-2-0-260128",
                    {("default", True): 46.00, ("default", False): 46.00},
                ),
            ),
            "doubao-seedance-2-0-fast-260128": ModelInfo(
                display_name="Seedance 2.0 Fast",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "video_extend"],
                supported_durations=list(range(4, 16)),
                resolutions=["480p", "720p"],
                max_reference_images=9,
                pricing=_ark_video_pricing(
                    "doubao-seedance-2-0-fast-260128",
                    {("default", True): 37.00, ("default", False): 37.00},
                ),
            ),
            "doubao-seedance-2-0-mini-260615": ModelInfo(
                display_name="Seedance 2.0 Mini",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "video_extend"],
                default=True,
                supported_durations=list(range(4, 16)),
                resolutions=["480p", "720p"],
                max_reference_images=9,
                pricing=_ark_video_pricing(
                    "doubao-seedance-2-0-mini-260615",
                    {("default", True): 23.00, ("default", False): 23.00},
                ),
            ),
        },
        default_base_url=ARK_BASE_URL,
    ),
    "ark-agent-plan": ProviderMeta(
        display_name="火山方舟 Agent Plan",
        description="火山方舟 Agent Plan 套餐，聚合豆包及多家主流大模型，覆盖文本、图片与视频生成。",
        required_keys=["api_key"],
        optional_keys=["video_max_workers", "image_max_workers"],
        secret_keys=["api_key"],
        models={
            # --- text ---
            # Agent Plan 套餐价当前无独立费率表，沿用历史行为：按 Gemini 默认费率兜底（pricing=None）。
            "doubao-seed-2.0-mini": ModelInfo(
                display_name="豆包 Seed 2.0 Mini",
                media_type="text",
                capabilities=["text_generation", "vision"],
            ),
            "doubao-seed-2.0-lite": ModelInfo(
                display_name="豆包 Seed 2.0 Lite",
                media_type="text",
                capabilities=["text_generation", "vision"],
                default=True,
            ),
            "doubao-seed-2.0-pro": ModelInfo(
                display_name="豆包 Seed 2.0 Pro",
                media_type="text",
                capabilities=["text_generation", "vision"],
            ),
            "doubao-seed-2.0-code": ModelInfo(
                display_name="豆包 Seed 2.0 Code",
                media_type="text",
                capabilities=["text_generation"],
            ),
            "deepseek-v4-flash": ModelInfo(
                display_name="DeepSeek V4 Flash",
                media_type="text",
                capabilities=["text_generation"],
            ),
            "deepseek-v4-pro": ModelInfo(
                display_name="DeepSeek V4 Pro",
                media_type="text",
                capabilities=["text_generation"],
            ),
            "glm-5.1": ModelInfo(
                display_name="GLM 5.1",
                media_type="text",
                capabilities=["text_generation"],
            ),
            "kimi-k2.6": ModelInfo(
                display_name="Kimi K2.6",
                media_type="text",
                capabilities=["text_generation"],
            ),
            "minimax-m2.7": ModelInfo(
                display_name="MiniMax M2.7",
                media_type="text",
                capabilities=["text_generation"],
            ),
            # --- image ---
            "doubao-seedream-5.0-lite": ModelInfo(
                display_name="Seedream 5.0 Lite",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
            ),
            # --- video ---
            "doubao-seedance-1.5-pro": ModelInfo(
                display_name="Seedance 1.5 Pro",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "flex_tier"],
                supported_durations=list(range(4, 13)),
                resolutions=["480p", "720p", "1080p"],
                max_reference_images=9,
            ),
            "doubao-seedance-2.0": ModelInfo(
                display_name="Seedance 2.0",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "video_extend"],
                supported_durations=list(range(4, 16)),
                resolutions=["480p", "720p", "1080p"],
                max_reference_images=9,
            ),
            "doubao-seedance-2.0-fast": ModelInfo(
                display_name="Seedance 2.0 Fast",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "video_extend"],
                supported_durations=list(range(4, 16)),
                resolutions=["480p", "720p"],
                max_reference_images=9,
            ),
            "doubao-seedance-2.0-mini": ModelInfo(
                display_name="Seedance 2.0 Mini",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "video_extend"],
                default=True,
                supported_durations=list(range(4, 16)),
                resolutions=["480p", "720p"],
                max_reference_images=9,
            ),
        },
        default_base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
    ),
    "grok": ProviderMeta(
        display_name="Grok",
        description="xAI Grok 模型，支持视频和图片生成。",
        required_keys=["api_key"],
        optional_keys=["video_max_workers", "image_max_workers"],
        secret_keys=["api_key"],
        models={
            # --- text ---
            "grok-4.20-0309-reasoning": ModelInfo(
                display_name="Grok 4.20 Reasoning",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                pricing=_grok_text_pricing("grok-4.20-0309-reasoning", 2.00, 6.00),
            ),
            "grok-4.20-0309-non-reasoning": ModelInfo(
                display_name="Grok 4.20 Non-Reasoning",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                pricing=_grok_text_pricing("grok-4.20-0309-non-reasoning", 2.00, 6.00),
            ),
            "grok-4-1-fast-reasoning": ModelInfo(
                display_name="Grok 4.1 Fast Reasoning",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                default=True,
                pricing=_grok_text_pricing("grok-4-1-fast-reasoning", 0.20, 0.50),
            ),
            "grok-4-1-fast-non-reasoning": ModelInfo(
                display_name="Grok 4.1 Fast (Non-Reasoning)",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                pricing=_grok_text_pricing("grok-4-1-fast-non-reasoning", 0.20, 0.50),
            ),
            # --- image ---
            "grok-imagine-image-pro": ModelInfo(
                display_name="Grok Imagine Image Pro",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                resolutions=["1K", "2K"],
                pricing=_grok_image_pricing("grok-imagine-image-pro", 0.07),
            ),
            "grok-imagine-image": ModelInfo(
                display_name="Grok Imagine Image",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
                resolutions=["1K", "2K"],
                pricing=_grok_image_pricing("grok-imagine-image", 0.02),
            ),
            # --- video ---
            "grok-imagine-video": ModelInfo(
                display_name="Grok Imagine Video",
                media_type="video",
                capabilities=["text_to_video", "image_to_video"],
                default=True,
                supported_durations=list(range(1, 16)),
                resolutions=["480p", "720p"],
                # 参考图上限值来自第三方来源，官方文档未明确列出。
                max_reference_images=7,
                # 不区分分辨率/音频的单一秒费率。
                pricing=PerSecondMatrix(
                    rates={"grok-imagine-video": {("", None): 0.050}},
                    default_model="grok-imagine-video",
                    dimensions="flat",
                    currency="USD",
                ),
            ),
        },
    ),
    "openai": ProviderMeta(
        display_name="OpenAI",
        description="OpenAI 官方平台，支持 GPT-5.5 / GPT-5.4 文本、GPT Image 2 图片和 Sora 视频生成。",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        models={
            # --- text ---
            "gpt-5.5": ModelInfo(
                display_name="GPT-5.5",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                pricing=_openai_text_pricing("gpt-5.5", 5.00, 30.00),
            ),
            "gpt-5.4": ModelInfo(
                display_name="GPT-5.4",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                pricing=_openai_text_pricing("gpt-5.4", 2.50, 15.00),
            ),
            "gpt-5.4-mini": ModelInfo(
                display_name="GPT-5.4 Mini",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                default=True,
                pricing=_openai_text_pricing("gpt-5.4-mini", 0.75, 4.50),
            ),
            "gpt-5.4-nano": ModelInfo(
                display_name="GPT-5.4 Nano",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                pricing=_openai_text_pricing("gpt-5.4-nano", 0.20, 1.25),
            ),
            # --- image ---
            "gpt-image-2": ModelInfo(
                display_name="GPT Image 2",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
                resolutions=["512px", "1K", "2K"],
                pricing=_openai_image_pricing(
                    "gpt-image-2",
                    {
                        "image_in": 8.0,
                        "image_cached_in": 2.0,
                        "image_out": 30.0,
                        "text_in": 5.0,
                        "text_cached_in": 1.25,
                        "text_out": 0.0,
                    },
                    {
                        ("low", "1024x1024"): 0.006,
                        ("low", "1024x1792"): 0.012,
                        ("low", "1792x1024"): 0.012,
                        ("medium", "1024x1024"): 0.053,
                        ("medium", "1024x1792"): 0.106,
                        ("medium", "1792x1024"): 0.106,
                        ("high", "1024x1024"): 0.211,
                        ("high", "1024x1792"): 0.317,
                        ("high", "1792x1024"): 0.317,
                    },
                ),
            ),
            # --- video ---
            "sora-2": ModelInfo(
                display_name="Sora 2",
                media_type="video",
                capabilities=["text_to_video", "image_to_video"],
                default=True,
                supported_durations=[4, 8, 12],
                resolutions=["720p"],
                max_reference_images=1,
                pricing=_sora_video_pricing("sora-2", {"720p": 0.10}),
            ),
            "sora-2-pro": ModelInfo(
                display_name="Sora 2 Pro",
                media_type="video",
                capabilities=["text_to_video", "image_to_video"],
                supported_durations=[4, 8, 12],
                resolutions=["720p", "1080p"],
                max_reference_images=1,
                pricing=_sora_video_pricing("sora-2-pro", {"720p": 0.30, "1024p": 0.50, "1080p": 0.70}),
            ),
        },
    ),
    "vidu": ProviderMeta(
        display_name="Vidu",
        description="生数科技 Vidu 视频生成平台，支持文生视频、图生视频、首尾帧、参考生视频与参考生图，仅图片与视频能力。",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        models={
            # --- image ---
            # Vidu 计费以响应 credits 为准，费率逻辑在 lib.vidu_shared；此处统一委托标记。
            "viduq2": ModelInfo(
                display_name="Vidu Q2 Image",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
                resolutions=["1080p", "2K", "4K"],
                pricing=ViduDelegate(),
            ),
            "viduq1": ModelInfo(
                display_name="Vidu Q1 Image",
                media_type="image",
                capabilities=["image_to_image"],
                resolutions=["1080p"],
                pricing=ViduDelegate(),
            ),
            # --- video ---
            "viduq3-turbo": ModelInfo(
                display_name="Vidu Q3 Turbo",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control"],
                default=True,
                supported_durations=list(range(1, 17)),
                resolutions=["540p", "720p", "1080p"],
                max_reference_images=7,
                pricing=ViduDelegate(),
            ),
            "viduq3-pro": ModelInfo(
                display_name="Vidu Q3 Pro",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control"],
                supported_durations=list(range(1, 17)),
                resolutions=["540p", "720p", "1080p"],
                max_reference_images=7,
                pricing=ViduDelegate(),
            ),
            "viduq3": ModelInfo(
                display_name="Vidu Q3 (Reference)",
                media_type="video",
                capabilities=["image_to_video", "generate_audio", "seed_control"],
                supported_durations=list(range(3, 17)),
                resolutions=["540p", "720p", "1080p"],
                max_reference_images=7,
                pricing=ViduDelegate(),
            ),
            "vidu2.0": ModelInfo(
                display_name="Vidu 2.0",
                media_type="video",
                capabilities=["image_to_video", "seed_control"],
                supported_durations=[4, 8],
                resolutions=["360p", "720p", "1080p"],
                max_reference_images=7,
                pricing=ViduDelegate(),
            ),
        },
    ),
    "dashscope": ProviderMeta(
        display_name="阿里百炼",
        description="阿里云百炼（Model Studio）全模态平台，支持 Qwen 文本、Qwen-Image / 万相图像与 HappyHorse / 万相视频（含参考生视频）。",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers", "audio_max_workers"],
        secret_keys=["api_key"],
        models={
            # --- text ---
            "qwen-plus": ModelInfo(
                display_name="Qwen Plus",
                media_type="text",
                capabilities=["text_generation", "structured_output"],
                default=True,
                pricing=_dashscope_text_pricing("qwen-plus", 0.8, 2.0),
            ),
            "qwen3.6-plus": ModelInfo(
                display_name="Qwen3.6 Plus",
                media_type="text",
                capabilities=["text_generation", "structured_output"],
                pricing=_dashscope_text_pricing("qwen3.6-plus", 2.0, 12.0),
            ),
            "qwen3-max": ModelInfo(
                display_name="Qwen3 Max",
                media_type="text",
                capabilities=["text_generation", "structured_output"],
                pricing=_dashscope_text_pricing("qwen3-max", 2.5, 10.0),
            ),
            "qwen3.7-max": ModelInfo(
                display_name="Qwen3.7 Max",
                media_type="text",
                capabilities=["text_generation", "structured_output"],
                pricing=_dashscope_text_pricing("qwen3.7-max", 12.0, 36.0),
            ),
            "qwen3.6-flash": ModelInfo(
                display_name="Qwen3.6 Flash",
                media_type="text",
                capabilities=["text_generation", "structured_output"],
                pricing=_dashscope_text_pricing("qwen3.6-flash", 1.2, 7.2),
            ),
            "qwen-long": ModelInfo(
                display_name="Qwen Long",
                media_type="text",
                capabilities=["text_generation", "structured_output"],
                pricing=_dashscope_text_pricing("qwen-long", 0.5, 2.0),
            ),
            # --- image ---
            # qwen-image-2.0 融合系列：T2I + I2I 同模型，size 用像素值 宽*高。
            "qwen-image-2.0": ModelInfo(
                display_name="Qwen Image 2.0",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
                resolutions=["2048*2048", "2688*1536", "1536*2688", "2368*1728", "1728*2368"],
                pricing=_dashscope_image_pricing("qwen-image-2.0", 0.2),
            ),
            "qwen-image-2.0-pro": ModelInfo(
                display_name="Qwen Image 2.0 Pro",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                resolutions=["2048*2048", "2688*1536", "1536*2688", "2368*1728", "1728*2368"],
                pricing=_dashscope_image_pricing("qwen-image-2.0-pro", 0.5),
            ),
            # 编辑专用系列：仅图生图（角色一致性增强）。
            "qwen-image-edit-plus": ModelInfo(
                display_name="Qwen Image Edit Plus",
                media_type="image",
                capabilities=["image_to_image"],
                # 编辑系列宽高均 ∈ [512, 2048]，像素档不超过 2048
                resolutions=["2048*2048", "2048*1152", "1152*2048", "2048*1536", "1536*2048"],
                pricing=_dashscope_image_pricing("qwen-image-edit-plus", 0.2),
            ),
            "qwen-image-edit-max": ModelInfo(
                display_name="Qwen Image Edit Max",
                media_type="image",
                capabilities=["image_to_image"],
                resolutions=["2048*2048", "2048*1152", "1152*2048", "2048*1536", "1536*2048"],
                pricing=_dashscope_image_pricing("qwen-image-edit-max", 0.5),
            ),
            # 万相 2.7 图像系列：size 用档位 1K/2K(/4K)。
            "wan2.7-image": ModelInfo(
                display_name="万相 2.7 图像",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                resolutions=["1K", "2K"],
                pricing=_dashscope_image_pricing("wan2.7-image", 0.2),
            ),
            "wan2.7-image-pro": ModelInfo(
                display_name="万相 2.7 图像 Pro",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                resolutions=["1K", "2K", "4K"],
                pricing=_dashscope_image_pricing("wan2.7-image-pro", 0.5),
            ),
            # --- video ---
            # HappyHorse 1.0 系列：720P ¥0.9/s，1080P ¥1.6/s（音频恒开）。
            "happyhorse-1.0-i2v": ModelInfo(
                display_name="HappyHorse 1.0 图生视频",
                media_type="video",
                capabilities=["image_to_video", "generate_audio", "seed_control"],
                default=True,
                supported_durations=list(range(3, 16)),
                resolutions=["720p", "1080p"],
                pricing=_dashscope_video_pricing("happyhorse-1.0-i2v", {"720p": 0.9, "1080p": 1.6}),
            ),
            "happyhorse-1.0-t2v": ModelInfo(
                display_name="HappyHorse 1.0 文生视频",
                media_type="video",
                capabilities=["text_to_video", "generate_audio", "seed_control"],
                supported_durations=list(range(3, 16)),
                resolutions=["720p", "1080p"],
                pricing=_dashscope_video_pricing("happyhorse-1.0-t2v", {"720p": 0.9, "1080p": 1.6}),
            ),
            "happyhorse-1.0-r2v": ModelInfo(
                display_name="HappyHorse 1.0 参考生视频",
                media_type="video",
                capabilities=["image_to_video", "generate_audio", "seed_control"],
                supported_durations=list(range(3, 16)),
                resolutions=["720p", "1080p"],
                max_reference_images=9,
                pricing=_dashscope_video_pricing("happyhorse-1.0-r2v", {"720p": 0.9, "1080p": 1.6}),
            ),
            # 万相 2.7 视频系列：720P ¥0.6/s，1080P ¥1.0/s（音频恒开）。
            "wan2.7-i2v": ModelInfo(
                display_name="万相 2.7 图生视频",
                media_type="video",
                capabilities=["image_to_video", "generate_audio", "seed_control"],
                supported_durations=list(range(2, 16)),
                resolutions=["720p", "1080p"],
                pricing=_dashscope_video_pricing("wan2.7-i2v", {"720p": 0.6, "1080p": 1.0}),
            ),
            "wan2.7-t2v": ModelInfo(
                display_name="万相 2.7 文生视频",
                media_type="video",
                capabilities=["text_to_video", "generate_audio", "seed_control"],
                supported_durations=list(range(2, 16)),
                resolutions=["720p", "1080p"],
                pricing=_dashscope_video_pricing("wan2.7-t2v", {"720p": 0.6, "1080p": 1.0}),
            ),
            "wan2.7-r2v": ModelInfo(
                display_name="万相 2.7 参考生视频",
                media_type="video",
                capabilities=["image_to_video", "generate_audio", "seed_control"],
                supported_durations=list(range(2, 16)),
                resolutions=["720p", "1080p"],
                max_reference_images=5,
                pricing=_dashscope_video_pricing("wan2.7-r2v", {"720p": 0.6, "1080p": 1.0}),
            ),
            # --- audio ---
            # qwen3-tts-flash：同步 HTTP 语音合成，按字符计费（¥0.8/万字符）。
            "qwen3-tts-flash": ModelInfo(
                display_name="Qwen3 TTS Flash",
                media_type="audio",
                capabilities=["text_to_speech"],
                default=True,
                pricing=_dashscope_audio_pricing("qwen3-tts-flash", 0.8),
            ),
        },
        default_base_url=DASHSCOPE_BASE_URL,
    ),
    "minimax": ProviderMeta(
        display_name="MiniMax",
        description="MiniMax（海螺）多模态平台，提供文本、图片、视频生成。默认连接国内站，海外可将 base_url 切换到国际站。",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        models={
            # --- text ---
            "MiniMax-M3": ModelInfo(
                display_name="MiniMax M3",
                media_type="text",
                capabilities=["text_generation", "structured_output"],
                default=True,
                pricing=_minimax_text_pricing("MiniMax-M3", 2.1, 8.4),
            ),
            "MiniMax-M2.7": ModelInfo(
                display_name="MiniMax M2.7",
                media_type="text",
                capabilities=["text_generation", "structured_output"],
                pricing=_minimax_text_pricing("MiniMax-M2.7", 2.1, 8.4),
            ),
            # --- image ---
            # image-01：单步同步取 URL，T2I + I2I（subject_reference 单脸参考）；
            # 尺寸用 width/height ∈ [512, 2048]（8 倍数），档位短边经精确比例算出。
            "image-01": ModelInfo(
                display_name="MiniMax Image 01",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
                resolutions=["1K", "2K"],
                max_reference_images=1,
                pricing=_minimax_image_pricing("image-01", 0.025),
            ),
            # --- video ---
            # 1080P 仅 6s（10s 仅 768P）；细粒度越界由 MiniMaxVideoBackend 抛 VideoCapabilityError，
            # duration_resolution_constraints 同步给前端做下拉门控。
            "MiniMax-Hailuo-2.3": ModelInfo(
                display_name="MiniMax Hailuo 2.3",
                media_type="video",
                capabilities=["text_to_video", "image_to_video"],
                default=True,
                supported_durations=[6, 10],
                resolutions=["768p", "1080p"],
                duration_resolution_constraints={"1080p": [6]},
                pricing=_minimax_video_pricing(
                    "MiniMax-Hailuo-2.3",
                    {("768p", 6): 2.0, ("768p", 10): 4.0, ("1080p", 6): 3.5},
                ),
            ),
            "MiniMax-Hailuo-2.3-Fast": ModelInfo(
                display_name="MiniMax Hailuo 2.3 Fast",
                media_type="video",
                capabilities=["image_to_video"],
                supported_durations=[6, 10],
                resolutions=["768p", "1080p"],
                duration_resolution_constraints={"1080p": [6]},
                pricing=_minimax_video_pricing(
                    "MiniMax-Hailuo-2.3-Fast",
                    {("768p", 6): 1.35, ("768p", 10): 2.25, ("1080p", 6): 2.31},
                ),
            ),
            # S2V-01：单张人脸驱动整段视频角色一致性（subject_reference 单脸 R2V）。固定输出
            # 720P/6s，请求不接受 resolution/duration（MiniMaxVideoBackend 走专门的 subject_reference
            # 路径，忽略这两项）；supported_durations=[6] 仅供编排层时长守卫与档价口径。
            # max_reference_images=1：编排层解析器读 registry ModelInfo.max_reference_images，据此只取 1 张参考图。
            # 定价单档约 ¥3（资源包 1.5 积分近似，半核实）；键到 minimax 缺省档 768P/6s 求精确命中，
            # 任意分辨率漂移由 per_video_bucket 最近档回落到唯一档。
            "S2V-01": ModelInfo(
                display_name="MiniMax S2V-01",
                media_type="video",
                capabilities=["image_to_video"],
                supported_durations=[6],
                resolutions=["768p"],
                max_reference_images=1,
                pricing=_minimax_video_pricing("S2V-01", {("768p", 6): 3.0}),
            ),
        },
        default_base_url=MINIMAX_BASE_URL,
    ),
    "kling": ProviderMeta(
        display_name="可灵 Kling",
        description="快手可灵 Kling 视频与图像生成平台，JWT（access_key + secret_key）鉴权。",
        # 首个需要两个 secret 字符串的内置 provider（JWT HS256 鉴权），凭证按 registry key 名
        # 存入 provider_credential 的 access_key / secret_key 定型列（见 ADR 0037）。
        required_keys=["access_key", "secret_key"],
        secret_keys=["access_key", "secret_key"],
        # JWT 直连视频：默认 kling-v2-5-turbo（性价比走量）+ v3/v3-omni（旗舰 4K + 多图主体）、
        # v2-6（pro 人声）、video-o1（多图主体 R2V）。图像模型留后续片接入。
        models={
            "kling-v2-5-turbo": ModelInfo(
                display_name="可灵 2.5 Turbo",
                media_type="video",
                capabilities=["text_to_video", "image_to_video"],
                default=True,
                supported_durations=[5, 10],
                resolutions=["720p", "1080p"],
                pricing=_kling_video_pricing("kling-v2-5-turbo"),
            ),
            # --- image ---
            "kling-image-o1": ModelInfo(
                display_name="可灵图像 O1",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
                resolutions=["1K", "2K"],
                pricing=_kling_image_flat_pricing("kling-image-o1", 0.2),
            ),
            # 两栖模型：API 模型名 kling-v3-omni 同时承载图像/视频；图像条目用别名键避开与视频
            # 条目（归视频片）撞 model_id 主键，api_model_name 回指真实 API 名。
            "kling-v3-omni-image": ModelInfo(
                display_name="可灵 V3-Omni（图像）",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                resolutions=["1K", "2K", "4K"],
                api_model_name="kling-v3-omni",
                pricing=_kling_image_by_resolution_pricing("kling-v3-omni-image", {"1K": 0.2, "2K": 0.2, "4K": 0.4}),
            ),
            # --- video ---
            "kling-v3": ModelInfo(
                display_name="可灵 v3",
                media_type="video",
                capabilities=["text_to_video", "image_to_video"],
                supported_durations=list(range(3, 16)),
                resolutions=["720p", "1080p", "4k"],
                pricing=_kling_video_pricing("kling-v3"),
            ),
            "kling-v3-omni": ModelInfo(
                display_name="可灵 v3 Omni",
                media_type="video",
                capabilities=["text_to_video", "image_to_video"],
                supported_durations=list(range(3, 16)),
                resolutions=["720p", "1080p", "4k"],
                # 多图主体（R2V）参考上限保守值；编排层裁剪读此处，与 backend caps 同值，
                # 待 app.klingai.com 控制台核对，不硬编当既成事实。
                max_reference_images=4,
                pricing=_kling_video_pricing("kling-v3-omni"),
            ),
            "kling-v2-6": ModelInfo(
                display_name="可灵 v2.6",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio"],
                supported_durations=[5, 10],
                resolutions=["720p", "1080p"],
                pricing=_kling_video_pricing("kling-v2-6"),
            ),
            "kling-video-o1": ModelInfo(
                display_name="可灵 Video O1",
                media_type="video",
                capabilities=["image_to_video"],
                supported_durations=[5, 10],
                resolutions=["720p", "1080p"],
                # 多图主体（R2V）参考上限保守值；编排层裁剪读此处，与 backend caps 同值，
                # 待 app.klingai.com 控制台核对，不硬编当既成事实。
                max_reference_images=4,
                pricing=_kling_video_pricing("kling-video-o1"),
            ),
        },
        default_base_url="https://api.klingai.com/v1",
    ),
}


def default_model_for_provider(provider_id: str, media_type: str) -> str | None:
    """返回该 provider 在 ``PROVIDER_REGISTRY`` 中指定 media_type 的默认 model_id；无则 None。"""
    meta = PROVIDER_REGISTRY.get(provider_id)
    if meta is None:
        return None
    for model_id, model_info in meta.models.items():
        if model_info.media_type == media_type and model_info.default:
            return model_id
    return None
