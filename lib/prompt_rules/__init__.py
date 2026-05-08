"""Prompt 规则单一真相源。

各规则模块（episode_pacing / visual_dynamic / asset_anti_break / asset_layout）
分别导出常量与 helper，由 prompt_builders_script.py 与 generate_asset.py 按需消费。
所有 Python 端注入受 `ARCREEL_PROMPT_RULES_V2` 环境变量控制（默认 on）。
"""

import os


def is_v2_enabled() -> bool:
    return os.environ.get("ARCREEL_PROMPT_RULES_V2", "on").lower() != "off"


__all__ = ["is_v2_enabled"]
