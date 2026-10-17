"""退役模型映射：设置与项目里引用的、已被供应商改名的模型改写为后继模型。

只收已确认改名、且有后继模型的精确 ``provider/model`` 引用。已下架而没有后继的模型不在这里：
这类引用原样保留，运行时构造 backend 时报「该模型不在供应商的模型清单中」，由用户重新选择
（``lib.catalog.errors.ModelNotInCatalogError``）。
"""

from __future__ import annotations

#: 退役模型引用 → 后继模型引用，覆盖所有媒体类型。不把其他 preview 模型或相似字符串纳入映射。
RETIRED_MODEL_REFERENCES: dict[str, str] = {
    # 文本
    "gemini-aistudio/gemini-3.1-flash-lite-preview": "gemini-aistudio/gemini-3.1-flash-lite",
    "gemini-vertex/gemini-3.1-flash-lite-preview": "gemini-vertex/gemini-3.1-flash-lite",
    # 图片：Gemini 图片模型正式发布，AI Studio 与 Vertex 都改用不带 -preview 的名字
    "gemini-aistudio/gemini-3-pro-image-preview": "gemini-aistudio/gemini-3-pro-image",
    "gemini-aistudio/gemini-3.1-flash-image-preview": "gemini-aistudio/gemini-3.1-flash-image",
    "gemini-vertex/gemini-3-pro-image-preview": "gemini-vertex/gemini-3-pro-image",
    "gemini-vertex/gemini-3.1-flash-image-preview": "gemini-vertex/gemini-3.1-flash-image",
}

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


def migrate_retired_model_reference(value: str) -> str:
    """把精确的退役模型引用改写为后继模型引用；其余值原样返回。"""

    return RETIRED_MODEL_REFERENCES.get(value, value)
