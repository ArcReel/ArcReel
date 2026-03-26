from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderMeta:
    display_name: str
    description: str
    media_types: list[str]
    required_keys: list[str]
    optional_keys: list[str] = field(default_factory=list)
    secret_keys: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)


PROVIDER_REGISTRY: dict[str, ProviderMeta] = {
    "gemini-aistudio": ProviderMeta(
        display_name="AI Studio",
        description="Google AI Studio 提供 Gemini 系列模型，支持图片和视频生成，适合快速原型和个人项目。",
        media_types=["video", "image"],
        required_keys=["api_key"],
        optional_keys=["base_url", "image_rpm", "video_rpm", "request_gap", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        capabilities=["text_to_video", "image_to_video", "text_to_image", "negative_prompt", "video_extend"],
    ),
    "gemini-vertex": ProviderMeta(
        display_name="Vertex AI",
        description="Google Cloud Vertex AI 企业级平台，支持 Gemini 和 Imagen 模型，提供更高配额和音频生成能力。",
        media_types=["video", "image"],
        required_keys=["credentials_path"],
        optional_keys=["gcs_bucket", "image_rpm", "video_rpm", "request_gap", "image_max_workers", "video_max_workers"],
        secret_keys=[],
        capabilities=["text_to_video", "image_to_video", "text_to_image", "generate_audio", "negative_prompt", "video_extend"],
    ),
    "ark": ProviderMeta(
        display_name="火山方舟",
        description="字节跳动火山方舟 AI 平台，支持 Seedance 视频生成和 Seedream 图片生成，具备音频生成和种子控制能力。",
        media_types=["video", "image"],
        required_keys=["api_key"],
        optional_keys=["file_service_base_url", "video_rpm", "image_rpm", "request_gap", "video_max_workers", "image_max_workers"],
        secret_keys=["api_key"],
        capabilities=["text_to_video", "image_to_video", "text_to_image", "image_to_image", "generate_audio", "seed_control", "flex_tier"],
    ),
    "grok": ProviderMeta(
        display_name="Grok",
        description="xAI Grok 模型，支持视频和图片生成。",
        media_types=["video", "image"],
        required_keys=["api_key"],
        optional_keys=["video_rpm", "image_rpm", "request_gap", "video_max_workers", "image_max_workers"],
        secret_keys=["api_key"],
        capabilities=["text_to_video", "image_to_video", "text_to_image", "image_to_image"],
    ),
}
