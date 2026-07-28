"""Cross-check that every backend task_type has a frontend display name.

``task_type`` has no centralized backend enum: it's a plain ``String`` column
(``lib/db/models/task.py``) populated either by fixed literal call sites
(``server/routers/generate.py``, ``server/routers/grids.py``,
``server/routers/reference_videos.py``, ``server/agent_runtime/sdk_tools/enqueue_*.py``)
or dynamically from :data:`ASSET_SPECS` keys (``lib/asset_types.py``) via
``server/agent_runtime/sdk_tools/enqueue_assets.py``. The frontend Task HUD
(``frontend/src/components/task-hud/TaskHud.tsx``) renders each task's type by
looking up ``task_type_<type>`` in the ``dashboard`` i18n namespace; if a
backend task_type ships without a corresponding ``task_type_<type>`` key in
zh/en/vi, the label falls back to the raw task_type string. This test fails CI
in that case so the gap is caught at PR time.

The fixed literals below were enumerated by grepping every ``task_type="..."``
and ``task_type=asset_type``/``task_type=spec.task_type`` call site in
``server/`` and ``lib/`` (2026-07-17) — not from memory. Any new fixed literal
task_type introduced by future backend changes must be added here alongside
its zh/en/vi translations.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lib.asset_types import ASSET_SPECS

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_TS = "frontend/src/i18n/{locale}/dashboard.ts"
LOCALES = ("zh", "en", "vi")

FIXED_TASK_TYPES = frozenset(
    {
        "image_edit",
        "storyboard",
        "video",
        "reference_video",
        "tts",
        "grid",
    }
)

ALL_TASK_TYPES = FIXED_TASK_TYPES | frozenset(ASSET_SPECS.keys())

_KEY_RE = re.compile(r"""['"](task_type_[a-z0-9_]+)['"]\s*:""")


def _load_task_type_keys(locale: str) -> set[str]:
    path = REPO_ROOT / DASHBOARD_TS.format(locale=locale)
    text = path.read_text(encoding="utf-8")
    return set(_KEY_RE.findall(text))


@pytest.mark.parametrize("locale", LOCALES)
def test_every_task_type_has_frontend_display_name(locale: str) -> None:
    keys = _load_task_type_keys(locale)
    expected = {f"task_type_{t}" for t in ALL_TASK_TYPES}
    missing = expected - keys
    assert not missing, (
        f"frontend/src/i18n/{locale}/dashboard.ts 缺少 task_type 显示名翻译: {sorted(missing)}。"
        f" 固定字面量见本文件 FIXED_TASK_TYPES，动态部分单一真相源在 lib/asset_types.ASSET_SPECS。"
    )


def test_no_orphan_task_type_keys_in_any_locale() -> None:
    """Frontend task_type_* keys 必须都对应已知 task_type —— 防止过时翻译堆积。"""
    expected = {f"task_type_{t}" for t in ALL_TASK_TYPES}
    for locale in LOCALES:
        keys = _load_task_type_keys(locale)
        orphans = keys - expected
        assert not orphans, (
            f"frontend/src/i18n/{locale}/dashboard.ts 存在与已知 task_type 不匹配的 task_type_* key: "
            f"{sorted(orphans)}。请删除或更新本文件的 FIXED_TASK_TYPES。"
        )
