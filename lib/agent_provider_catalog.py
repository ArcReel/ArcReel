"""预设 Anthropic 兼容供应商目录 (cc-switch 风格)。

每条 PresetProvider 提供「开箱即用」的 messages_url + discovery_url + 推荐模型，
让用户在 UI 上选 chip 即填好 URL。新增 entries 在此文件添加；前端 ICON_LOADERS
通过 icon_key 与 @lobehub/icons 对齐。
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
    api_key_url: str | None  # 「获取 API Key」链接
    notes_i18n_key: str | None
    api_key_pattern: str | None  # 前端轻量校验
    is_recommended: bool


PRESET_PROVIDERS: dict[str, PresetProvider] = {
    "anthropic-official": PresetProvider(
        id="anthropic-official",
        display_name="Anthropic Official",
        icon_key="Anthropic",
        messages_url="https://api.anthropic.com",
        discovery_url="https://api.anthropic.com",
        default_model="claude-3-5-sonnet-20241022",
        suggested_models=(
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-7-sonnet-latest",
        ),
        docs_url="https://docs.anthropic.com",
        api_key_url="https://console.anthropic.com/settings/keys",
        notes_i18n_key=None,
        api_key_pattern=r"^sk-ant-[A-Za-z0-9_-]+$",
        is_recommended=True,
    ),
    "deepseek": PresetProvider(
        id="deepseek",
        display_name="DeepSeek",
        icon_key="DeepSeek",
        messages_url="https://api.deepseek.com/anthropic",
        discovery_url="https://api.deepseek.com",
        default_model="deepseek-chat",
        suggested_models=("deepseek-chat", "deepseek-reasoner"),
        docs_url="https://api-docs.deepseek.com/",
        api_key_url="https://platform.deepseek.com/api_keys",
        notes_i18n_key="preset_notes_deepseek",
        api_key_pattern=r"^sk-[A-Za-z0-9]+$",
        is_recommended=True,
    ),
    "kimi": PresetProvider(
        id="kimi",
        display_name="Kimi (Moonshot)",
        icon_key="Moonshot",
        messages_url="https://api.moonshot.cn/anthropic",
        discovery_url="https://api.moonshot.cn",
        default_model="moonshot-v1-32k",
        suggested_models=("moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"),
        docs_url="https://platform.moonshot.cn/docs",
        api_key_url="https://platform.moonshot.cn/console/api-keys",
        notes_i18n_key=None,
        api_key_pattern=r"^sk-[A-Za-z0-9]+$",
        is_recommended=True,
    ),
    "glm": PresetProvider(
        id="glm",
        display_name="Zhipu GLM",
        icon_key="Zhipu",
        messages_url="https://open.bigmodel.cn/api/anthropic",
        discovery_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4-plus",
        suggested_models=("glm-4-plus", "glm-4-air", "glm-4-flash"),
        docs_url="https://open.bigmodel.cn/dev/api",
        api_key_url="https://open.bigmodel.cn/usercenter/apikeys",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=True,
    ),
    "minimax-intl": PresetProvider(
        id="minimax-intl",
        display_name="MiniMax (Global)",
        icon_key="Minimax",
        messages_url="https://api.minimax.io/anthropic",
        discovery_url="https://api.minimax.io",
        default_model="MiniMax-M1",
        suggested_models=("MiniMax-M1",),
        docs_url="https://www.minimax.io/platform/document",
        api_key_url="https://www.minimax.io/user-center/basic-information/interface-key",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=False,
    ),
    "minimax-cn": PresetProvider(
        id="minimax-cn",
        display_name="MiniMax (中国)",
        icon_key="Minimax",
        messages_url="https://api.minimaxi.com/anthropic",
        discovery_url="https://api.minimaxi.com",
        default_model="MiniMax-M1",
        suggested_models=("MiniMax-M1",),
        docs_url="https://platform.minimaxi.com/document",
        api_key_url="https://platform.minimaxi.com/user-center/basic-information/interface-key",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=False,
    ),
    "hunyuan": PresetProvider(
        id="hunyuan",
        display_name="Tencent Hunyuan",
        icon_key="Hunyuan",
        messages_url="https://api.hunyuan.cloud.tencent.com/anthropic",
        discovery_url="https://api.hunyuan.cloud.tencent.com",
        default_model="hunyuan-turbo",
        suggested_models=("hunyuan-turbo", "hunyuan-pro", "hunyuan-lite"),
        docs_url="https://cloud.tencent.com/document/product/1729",
        api_key_url="https://console.cloud.tencent.com/hunyuan/api-key",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=False,
    ),
    "lkeap": PresetProvider(
        id="lkeap",
        display_name="Tencent LKEAP (Coding)",
        icon_key="TencentCloud",
        messages_url="https://api.lkeap.cloud.tencent.com/coding/anthropic",
        discovery_url="https://api.lkeap.cloud.tencent.com",
        default_model="deepseek-v3",
        suggested_models=("deepseek-v3", "deepseek-r1"),
        docs_url="https://cloud.tencent.com/document/product/1772",
        api_key_url="https://console.cloud.tencent.com/lkeap/api",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=False,
    ),
    "ark-coding": PresetProvider(
        id="ark-coding",
        display_name="火山方舟 (Coding)",
        icon_key="Volcengine",
        messages_url="https://ark.cn-beijing.volces.com/api/coding",
        discovery_url="https://ark.cn-beijing.volces.com",
        default_model="doubao-seed-1.6",
        suggested_models=("doubao-seed-1.6", "doubao-1.5-pro-32k"),
        docs_url="https://www.volcengine.com/docs/82379",
        api_key_url="https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
        notes_i18n_key="preset_notes_ark_coding",
        api_key_pattern=None,
        is_recommended=False,
    ),
    "bailian": PresetProvider(
        id="bailian",
        display_name="阿里百炼 (DashScope)",
        icon_key="Qwen",
        messages_url="https://dashscope.aliyuncs.com/apps/anthropic",
        discovery_url=None,  # 无公开 list 端点
        default_model="qwen-max",
        suggested_models=("qwen-max", "qwen-plus", "qwen-turbo"),
        docs_url="https://help.aliyun.com/zh/dashscope/",
        api_key_url="https://bailian.console.aliyun.com/?apiKey=1",
        notes_i18n_key="preset_notes_bailian",
        api_key_pattern=None,
        is_recommended=False,
    ),
    "xiaomi-mimo": PresetProvider(
        id="xiaomi-mimo",
        display_name="Xiaomi MiMo",
        icon_key="XiaomiMiMo",
        messages_url="https://api.xiaomimimo.com/anthropic",
        discovery_url=None,  # 未公开 /v1/models
        default_model="mimo-v2-pro",
        suggested_models=("mimo-v2-pro", "mimo-v2-flash"),
        docs_url="https://www.xiaomi.com/mimo",
        api_key_url=None,
        notes_i18n_key="preset_notes_xiaomi_mimo",
        api_key_pattern=None,
        is_recommended=False,
    ),
}


# 显示顺序：推荐项优先；同推荐内按字母序
PRESET_ORDER: tuple[str, ...] = tuple(
    sorted(PRESET_PROVIDERS.keys(), key=lambda k: (not PRESET_PROVIDERS[k].is_recommended, k))
)


def get_preset(preset_id: str) -> PresetProvider | None:
    return PRESET_PROVIDERS.get(preset_id)


def list_presets() -> list[PresetProvider]:
    return [PRESET_PROVIDERS[k] for k in PRESET_ORDER]
