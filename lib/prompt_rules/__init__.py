"""Prompt 规则单一真相源。

仅保留 `episode_pacing`：drama 短剧的节奏建议（开篇钩子 / 中段冲突 / 末镜定格）
是体裁特征，与具体视觉风格 / backend 无关，需要在 builder 与 subagent .md 间共享。

历史上还有 `visual_dynamic` / `asset_anti_break` / `asset_layout` 三个模块——
这些被认为属于"prompt 写作指导"而非可独立维护的规则常量，已下沉到
`lib/prompt_builders.py` 与 `lib/prompt_builders_script.py` 内部。

灰度开关 `ARCREEL_PROMPT_RULES_V2`（默认 on）仅控制是否注入节奏 section，
便于线上回滚到无节奏注入的 baseline。
"""

import os


def is_v2_enabled() -> bool:
    return os.environ.get("ARCREEL_PROMPT_RULES_V2", "on").strip().lower() != "off"


__all__ = ["is_v2_enabled"]
