"""Model-reference setting keys: which global ``system_settings`` / project ``project.json`` values are ``provider/model``."""

from __future__ import annotations

#: Shared global/project text-backend keys.
TEXT_BACKEND_SETTING_KEYS: tuple[str, ...] = (
    "default_text_backend",
    "text_backend_simple",
    "text_backend_complex",
)

#: All model-reference global system_settings keys.
GLOBAL_MODEL_SETTING_KEYS: tuple[str, ...] = (
    *TEXT_BACKEND_SETTING_KEYS,
    "default_image_backend",
    "default_image_backend_t2i",
    "default_image_backend_i2i",
    "default_video_backend",
    "default_video_backend_i2v",
    "default_video_backend_r2v",
    "default_audio_backend",
)

#: All model-reference project.json keys.
PROJECT_MODEL_SETTING_KEYS: tuple[str, ...] = (
    *TEXT_BACKEND_SETTING_KEYS,
    "default_image_backend",
    "image_provider_t2i",
    "image_provider_i2i",
    "video_backend",
    "video_provider_i2v",
    "video_provider_r2v",
    "audio_backend",
)

#: Global/project model-reference keys → media, validating writes and matching entries on reads.
MODEL_SETTING_MEDIA_TYPES: dict[str, str] = {
    "video_backend": "video",
    "video_provider_i2v": "video",
    "video_provider_r2v": "video",
    "default_video_backend": "video",
    "default_video_backend_i2v": "video",
    "default_video_backend_r2v": "video",
    "image_provider_t2i": "image",
    "image_provider_i2i": "image",
    "default_image_backend": "image",
    "default_image_backend_t2i": "image",
    "default_image_backend_i2i": "image",
    "audio_backend": "audio",
    "default_audio_backend": "audio",
    "text_backend_simple": "text",
    "text_backend_complex": "text",
    "default_text_backend": "text",
}
