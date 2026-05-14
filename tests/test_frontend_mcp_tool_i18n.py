"""Cross-check that every backend ArcReel MCP tool has a frontend display name.

The single source of truth is :data:`ARCREEL_MCP_TOOL_IDS` in
``server/agent_runtime/sdk_tools/__init__.py``. The frontend renders each
``mcp__arcreel__<id>`` tool chip by looking up ``tool_name_<id>`` in the
``dashboard`` i18n namespace; if a backend tool ships without a corresponding
``tool_name_<id>`` key in zh/en/vi, the chip falls back to the raw upper-cased
tool name. This test fails CI in that case so the gap is caught at PR time.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_TS = "frontend/src/i18n/{locale}/dashboard.ts"
LOCALES = ("zh", "en", "vi")

# Match either single- or double-quoted keys: 'tool_name_foo' or "tool_name_foo".
_KEY_RE = re.compile(r"""['"](tool_name_[a-z0-9_]+)['"]\s*:""")


def _load_tool_name_keys(locale: str) -> set[str]:
    path = REPO_ROOT / DASHBOARD_TS.format(locale=locale)
    text = path.read_text(encoding="utf-8")
    return set(_KEY_RE.findall(text))


@pytest.mark.parametrize("locale", LOCALES)
def test_every_backend_tool_has_frontend_display_name(locale: str) -> None:
    keys = _load_tool_name_keys(locale)
    expected = {f"tool_name_{tid}" for tid in ARCREEL_MCP_TOOL_IDS}
    missing = expected - keys
    assert not missing, (
        f"frontend/src/i18n/{locale}/dashboard.ts 缺少 MCP tool 显示名翻译: {sorted(missing)}。"
        f" 单一真相源在 server/agent_runtime/sdk_tools/__init__.py 的 ARCREEL_MCP_TOOL_IDS。"
    )


def test_no_orphan_tool_name_keys_in_any_locale() -> None:
    """Frontend tool_name_* keys 必须都对应 backend tool id —— 防止过时翻译堆积。"""
    expected = {f"tool_name_{tid}" for tid in ARCREEL_MCP_TOOL_IDS}
    for locale in LOCALES:
        keys = _load_tool_name_keys(locale)
        orphans = keys - expected
        assert not orphans, (
            f"frontend/src/i18n/{locale}/dashboard.ts 存在与 backend 不匹配的 tool_name_* key: "
            f"{sorted(orphans)}。请删除或更新 backend ARCREEL_MCP_TOOL_IDS。"
        )
