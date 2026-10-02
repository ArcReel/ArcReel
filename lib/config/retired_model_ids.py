"""已退役模型引用的配置迁移映射。"""

from __future__ import annotations

#: registry 键兼作配置引用。只迁移已确认被供应商更名的精确 provider/model 引用，
#: 不把其他 preview 模型或相似字符串纳入映射。
RETIRED_TEXT_MODEL_REFERENCES: dict[str, str] = {
    "gemini-aistudio/gemini-3.1-flash-lite-preview": "gemini-aistudio/gemini-3.1-flash-lite",
    "gemini-vertex/gemini-3.1-flash-lite-preview": "gemini-vertex/gemini-3.1-flash-lite",
}

#: 全局 system_settings 与项目 project.json 同名的文本 backend 字段。
TEXT_BACKEND_SETTING_KEYS: tuple[str, ...] = (
    "default_text_backend",
    "text_backend_simple",
    "text_backend_complex",
)


def migrate_retired_text_model_reference(value: str) -> str:
    """把精确的退役文本模型引用迁到现行 registry 键；其余值原样返回。"""

    return RETIRED_TEXT_MODEL_REFERENCES.get(value, value)
