"""预设 Anthropic 兼容供应商目录。

每条 PresetProvider 提供 messages_url + discovery_url + 「获取 API Key」链接，
让用户在 UI 上选 chip 即填好 URL。`default_model` 仅作为输入框 placeholder
提示，不再自动预填到表单。

新增 entries 在此文件添加；前端 ICON_LOADERS 通过 icon_key 与 @lobehub/icons 对齐。
"""

from __future__ import annotations

from dataclasses import dataclass

CUSTOM_SENTINEL_ID = "__custom__"


@dataclass(frozen=True)
class PresetProvider:
    id: str
    display_name: str
    icon_key: str  # @lobehub/icons 子组件名 (如 "DeepSeek")
    messages_url: str
    discovery_url: str | None
    default_model: str
    suggested_models: tuple[str, ...]
    docs_url: str | None
    api_key_url: str | None
    notes_i18n_key: str | None
    api_key_pattern: str | None
    is_recommended: bool


PRESET_PROVIDERS: dict[str, PresetProvider] = {
    "anthropic-official": PresetProvider(
        id="anthropic-official",
        display_name="Anthropic Official",
        icon_key="Anthropic",
        messages_url="https://api.anthropic.com",
        discovery_url="https://api.anthropic.com",
        default_model="",
        suggested_models=(),
        docs_url=None,
        api_key_url="https://platform.claude.com/",
        notes_i18n_key=None,
        api_key_pattern=r"^sk-ant-[A-Za-z0-9_-]+$",
        is_recommended=False,
    ),
    "arcreel": PresetProvider(
        id="arcreel",
        display_name="ArcReel API",
        icon_key="ArcReel",
        messages_url="https://api.arc-reel.com",
        discovery_url="https://api.arc-reel.com",
        default_model="gpt-5.5",
        suggested_models=(),
        docs_url=None,
        api_key_url="https://api.arc-reel.com/",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=True,
    ),
    "glm-cn": PresetProvider(
        id="glm-cn",
        display_name="Zhipu GLM (中国)",
        icon_key="Zhipu",
        messages_url="https://open.bigmodel.cn/api/anthropic",
        discovery_url="https://open.bigmodel.cn/api/anthropic",
        default_model="glm-5.1",
        suggested_models=(),
        docs_url=None,
        api_key_url="https://www.bigmodel.cn/glm-coding?ic=92O3DUV7NS",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=False,
    ),
    "glm-intl": PresetProvider(
        id="glm-intl",
        display_name="Zhipu GLM (Global)",
        icon_key="Zhipu",
        messages_url="https://api.z.ai/api/anthropic",
        discovery_url="https://api.z.ai/api/anthropic",
        default_model="glm-5.1",
        suggested_models=(),
        docs_url=None,
        api_key_url="https://z.ai/subscribe?ic=3TIZJG5I0I",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=False,
    ),
    "xiaomi-mimo": PresetProvider(
        id="xiaomi-mimo",
        display_name="Xiaomi MiMo",
        icon_key="XiaomiMiMo",
        messages_url="https://api.xiaomimimo.com/anthropic",
        discovery_url=None,
        default_model="mimo-v2.5-pro",
        suggested_models=(),
        docs_url=None,
        api_key_url="https://platform.xiaomimimo.com?ref=9JF5V2",
        notes_i18n_key="preset_notes_xiaomi_mimo",
        api_key_pattern=None,
        is_recommended=False,
    ),
    "deepseek": PresetProvider(
        id="deepseek",
        display_name="DeepSeek",
        icon_key="DeepSeek",
        messages_url="https://api.deepseek.com/anthropic",
        discovery_url="https://api.deepseek.com",
        default_model="deepseek-v4-pro",
        suggested_models=(),
        docs_url=None,
        api_key_url="https://platform.deepseek.com/",
        notes_i18n_key="preset_notes_deepseek",
        api_key_pattern=r"^sk-[A-Za-z0-9]+$",
        is_recommended=False,
    ),
    "minimax-cn": PresetProvider(
        id="minimax-cn",
        display_name="MiniMax (中国)",
        icon_key="Minimax",
        messages_url="https://api.minimaxi.com/anthropic",
        discovery_url="https://api.minimaxi.com/anthropic",
        default_model="MiniMax-M2.7",
        suggested_models=(),
        docs_url=None,
        api_key_url="https://platform.minimaxi.com/subscribe/coding-plan",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=False,
    ),
    "minimax-intl": PresetProvider(
        id="minimax-intl",
        display_name="MiniMax (Global)",
        icon_key="Minimax",
        messages_url="https://api.minimax.io/anthropic",
        discovery_url="https://api.minimax.io/anthropic",
        default_model="MiniMax-M2.7",
        suggested_models=(),
        docs_url=None,
        api_key_url="https://platform.minimax.io/subscribe/coding-plan",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=False,
    ),
    "kimi": PresetProvider(
        id="kimi",
        display_name="Kimi For Coding",
        icon_key="Kimi",
        messages_url="https://api.kimi.com/coding",
        discovery_url="https://api.kimi.com/coding",
        default_model="",
        suggested_models=(),
        docs_url=None,
        api_key_url="https://www.kimi.com/coding/docs/",
        notes_i18n_key=None,
        api_key_pattern=r"^sk-[A-Za-z0-9]+$",
        is_recommended=False,
    ),
}


# 显示顺序：显式定义,Anthropic Official 永远第一,ArcReel 第二,其余按区域归组
PRESET_ORDER: tuple[str, ...] = (
    "anthropic-official",
    "arcreel",
    "deepseek",
    "kimi",
    "xiaomi-mimo",
    "glm-cn",
    "glm-intl",
    "minimax-cn",
    "minimax-intl",
)


def get_preset(preset_id: str) -> PresetProvider | None:
    return PRESET_PROVIDERS.get(preset_id)


def list_presets() -> list[PresetProvider]:
    return [PRESET_PROVIDERS[k] for k in PRESET_ORDER]
