"""引用模型的设置字段名：全局 ``system_settings`` 与项目 ``project.json`` 各自哪些键的值是 ``provider/model``。"""

from __future__ import annotations

#: 全局 system_settings 与项目 project.json 同名的文本 backend 字段。
TEXT_BACKEND_SETTING_KEYS: tuple[str, ...] = (
    "default_text_backend",
    "text_backend_simple",
    "text_backend_complex",
)

#: 全局 system_settings 里所有引用模型的设置项。
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

#: 项目 project.json 里所有引用模型的字段。
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

#: 引用模型的设置字段（全局与项目）→ 所引模型的媒体类型。写入时据此校验模型的媒体类型，读出引用时据此对应条目。
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
