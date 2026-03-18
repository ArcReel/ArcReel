from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderMeta:
    display_name: str
    media_types: list[str]
    required_keys: list[str]
    optional_keys: list[str] = field(default_factory=list)
    secret_keys: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)


PROVIDER_REGISTRY: dict[str, ProviderMeta] = {
    "gemini-aistudio": ProviderMeta(
        display_name="Gemini AI Studio",
        media_types=["video", "image"],
        required_keys=["api_key"],
        optional_keys=["base_url", "image_rpm", "video_rpm", "request_gap", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        capabilities=["text_to_video", "image_to_video", "text_to_image", "negative_prompt", "video_extend"],
    ),
    "gemini-vertex": ProviderMeta(
        display_name="Gemini Vertex AI",
        media_types=["video", "image"],
        required_keys=["credentials_path"],
        optional_keys=["gcs_bucket", "image_rpm", "video_rpm", "request_gap", "image_max_workers", "video_max_workers"],
        secret_keys=[],
        capabilities=["text_to_video", "image_to_video", "text_to_image", "generate_audio", "negative_prompt", "video_extend"],
    ),
    "seedance": ProviderMeta(
        display_name="Seedance",
        media_types=["video"],
        required_keys=["api_key"],
        optional_keys=["file_service_base_url", "video_rpm", "request_gap", "video_max_workers"],
        secret_keys=["api_key"],
        capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "flex_tier"],
    ),
    "grok": ProviderMeta(
        display_name="Grok",
        media_types=["video"],
        required_keys=["api_key"],
        optional_keys=["video_rpm", "request_gap", "video_max_workers"],
        secret_keys=["api_key"],
        capabilities=["text_to_video", "image_to_video"],
    ),
}
