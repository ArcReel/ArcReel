from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelInfo:
    display_name: str
    media_type: str
    capabilities: list[str]
    default: bool = False
    supported_durations: list[int] = field(default_factory=list)
    duration_resolution_constraints: dict[str, list[int]] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderMeta:
    display_name: str
    description: str
    required_keys: list[str]
    optional_keys: list[str] = field(default_factory=list)
    secret_keys: list[str] = field(default_factory=list)
    models: dict[str, ModelInfo] = field(default_factory=dict)

    @property
    def media_types(self) -> list[str]:
        return sorted(set(m.media_type for m in self.models.values()))

    @property
    def capabilities(self) -> list[str]:
        return sorted(set(c for m in self.models.values() for c in m.capabilities))


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
            ),
            "gemini-3-flash-preview": ModelInfo(
                display_name="Gemini 3 Flash",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                default=True,
            ),
            "gemini-3.1-flash-lite-preview": ModelInfo(
                display_name="Gemini 3.1 Flash Lite",
                media_type="text",
                capabilities=["text_generation", "structured_output"],
            ),
            # --- image ---
            "gemini-3-pro-image-preview": ModelInfo(
                display_name="Gemini 3 Pro Image",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
            ),
            "gemini-3.1-flash-image-preview": ModelInfo(
                display_name="Gemini 3.1 Flash Image",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
            ),
            # --- video ---
            "veo-3.1-generate-preview": ModelInfo(
                display_name="Veo 3.1",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "negative_prompt", "video_extend"],
                supported_durations=[4, 6, 8],
                duration_resolution_constraints={"1080p": [8]},
            ),
            "veo-3.1-fast-generate-preview": ModelInfo(
                display_name="Veo 3.1 Fast",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "negative_prompt", "video_extend"],
                supported_durations=[4, 6, 8],
                duration_resolution_constraints={"1080p": [8]},
            ),
            "veo-3.1-lite-generate-preview": ModelInfo(
                display_name="Veo 3.1 Lite",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "negative_prompt", "video_extend"],
                default=True,
                supported_durations=[4, 6, 8],
                duration_resolution_constraints={"1080p": [8]},
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
            ),
            "gemini-3-flash-preview": ModelInfo(
                display_name="Gemini 3 Flash",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                default=True,
            ),
            "gemini-3.1-flash-lite-preview": ModelInfo(
                display_name="Gemini 3.1 Flash Lite",
                media_type="text",
                capabilities=["text_generation", "structured_output"],
            ),
            # --- image ---
            "gemini-3-pro-image-preview": ModelInfo(
                display_name="Gemini 3 Pro Image",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
            ),
            "gemini-3.1-flash-image-preview": ModelInfo(
                display_name="Gemini 3.1 Flash Image",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
            ),
            # --- video ---
            "veo-3.1-generate-001": ModelInfo(
                display_name="Veo 3.1",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "negative_prompt", "video_extend"],
                supported_durations=[4, 6, 8],
            ),
            "veo-3.1-fast-generate-001": ModelInfo(
                display_name="Veo 3.1 Fast",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "negative_prompt", "video_extend"],
                default=True,
                supported_durations=[4, 6, 8],
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
            ),
            "doubao-seed-2-0-lite-260215": ModelInfo(
                display_name="豆包 Seed 2.0 Lite",
                media_type="text",
                capabilities=["text_generation", "vision"],
                default=True,
            ),
            "doubao-seed-2-0-mini-260215": ModelInfo(
                display_name="豆包 Seed 2.0 Mini",
                media_type="text",
                capabilities=["text_generation", "vision"],
            ),
            "doubao-seed-1-8-251228": ModelInfo(
                display_name="豆包 Seed 1.8",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
            ),
            # --- image ---
            "doubao-seedream-5-0-lite-260128": ModelInfo(
                display_name="Seedream 5.0 Lite",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
            ),
            "doubao-seedream-5-0-260128": ModelInfo(
                display_name="Seedream 5.0",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
            ),
            "doubao-seedream-4-5-251128": ModelInfo(
                display_name="Seedream 4.5",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
            ),
            "doubao-seedream-4-0-250828": ModelInfo(
                display_name="Seedream 4.0",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
            ),
            # --- video ---
            "doubao-seedance-1-5-pro-251215": ModelInfo(
                display_name="Seedance 1.5 Pro",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "flex_tier"],
                default=True,
                supported_durations=list(range(4, 13)),
            ),
            "doubao-seedance-2-0-260128": ModelInfo(
                display_name="Seedance 2.0",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "video_extend"],
                supported_durations=list(range(4, 16)),
            ),
            "doubao-seedance-2-0-fast-260128": ModelInfo(
                display_name="Seedance 2.0 Fast",
                media_type="video",
                capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "video_extend"],
                supported_durations=list(range(4, 16)),
            ),
        },
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
            ),
            "grok-4.20-0309-non-reasoning": ModelInfo(
                display_name="Grok 4.20 Non-Reasoning",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
            ),
            "grok-4-1-fast-reasoning": ModelInfo(
                display_name="Grok 4.1 Fast Reasoning",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                default=True,
            ),
            "grok-4-1-fast-non-reasoning": ModelInfo(
                display_name="Grok 4.1 Fast (Non-Reasoning)",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
            ),
            # --- image ---
            "grok-imagine-image-pro": ModelInfo(
                display_name="Grok Imagine Image Pro",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
            ),
            "grok-imagine-image": ModelInfo(
                display_name="Grok Imagine Image",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
            ),
            # --- video ---
            "grok-imagine-video": ModelInfo(
                display_name="Grok Imagine Video",
                media_type="video",
                capabilities=["text_to_video", "image_to_video"],
                default=True,
                supported_durations=list(range(1, 16)),
            ),
        },
    ),
    "openai": ProviderMeta(
        display_name="OpenAI",
        description="OpenAI 官方平台，支持 GPT-5.4 文本、GPT Image 图片和 Sora 视频生成。",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        models={
            # --- text ---
            "gpt-5.4": ModelInfo(
                display_name="GPT-5.4",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
            ),
            "gpt-5.4-mini": ModelInfo(
                display_name="GPT-5.4 Mini",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                default=True,
            ),
            "gpt-5.4-nano": ModelInfo(
                display_name="GPT-5.4 Nano",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
            ),
            # --- image ---
            "gpt-image-1.5": ModelInfo(
                display_name="GPT Image 1.5",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
            ),
            "gpt-image-1-mini": ModelInfo(
                display_name="GPT Image 1 Mini",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
            ),
            # --- video ---
            "sora-2": ModelInfo(
                display_name="Sora 2",
                media_type="video",
                capabilities=["text_to_video", "image_to_video"],
                default=True,
                supported_durations=[4, 8, 12],
            ),
            "sora-2-pro": ModelInfo(
                display_name="Sora 2 Pro",
                media_type="video",
                capabilities=["text_to_video", "image_to_video"],
                supported_durations=[4, 8, 12],
            ),
        },
    ),
}
