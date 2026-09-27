"""Cross-check that every backend task_type has a frontend display name.

``task_type`` has no centralized backend enum: it's a plain ``String`` column
(``lib/db/models/task.py``) populated either by fixed literal call sites
(``server/routers/generate.py``, ``server/routers/grids.py``,
``server/routers/reference_videos.py``, ``server/tool_runtime.py``,
``server/media_tools/*.py``) or dynamically from
:data:`ASSET_SPECS` keys (``lib/project/asset_types.py``) and
:data:`DERIVATIVE_TASK_TYPE` (``lib/project/asset_derivatives.py``).

Two frontend surfaces look up ``task_type_<type>`` and fall back to the raw
task_type string when the key is absent:

- the ``dashboard`` namespace: the usage cancellation preview
  (``components/usage/CancelConfirmDialog.tsx``) lists any queued task, and the
  prompt-template list names generation-task triggers with the same keys;
- the ``workflow`` namespace: the workflow panel's task chips
  (``components/workflow/TaskChips.tsx``) show the tasks
  ``server/services/project/workflow_planner.py`` attaches to a step — text
  tasks, asset sheets, storyboard / grid / tts / video / reference_video.

The fixed literals below were enumerated by grepping every ``task_type="..."``
and ``task_type=<variable>`` call site in ``server/`` and ``lib/`` — not from
memory. Any new fixed literal task_type introduced by future backend changes
must be added here alongside its zh/en/vi translations.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lib.project.asset_derivatives import DERIVATIVE_TASK_TYPE
from lib.project.asset_types import ASSET_SPECS

REPO_ROOT = Path(__file__).resolve().parents[2]
VOCAB_TS = "frontend/src/i18n/{locale}/{namespace}.ts"
LOCALES = ("zh", "en", "vi")

TEXT_TASK_TYPES = frozenset(
    {
        "text_episode_plan",
        "text_episode_script",
        "text_drama_script_plan",
        "text_narration_script_plan",
        "text_reference_script_plan",
    }
)

UNIT_TASK_TYPES = frozenset({"storyboard", "grid", "tts", "video", "reference_video"})

FIXED_TASK_TYPES = TEXT_TASK_TYPES | UNIT_TASK_TYPES | {"image_edit", "voice_sample", DERIVATIVE_TASK_TYPE}

ALL_TASK_TYPES = FIXED_TASK_TYPES | frozenset(ASSET_SPECS.keys())

#: 工作流面板只展示规划器挂到步骤上的任务：文本任务、资产图与单元级生成任务。
WORKFLOW_TASK_TYPES = TEXT_TASK_TYPES | UNIT_TASK_TYPES | frozenset(ASSET_SPECS.keys())

EXPECTED_BY_NAMESPACE = {
    "dashboard": {f"task_type_{t}" for t in ALL_TASK_TYPES},
    "workflow": {f"task_type_{t}" for t in WORKFLOW_TASK_TYPES},
}

_KEY_RE = re.compile(r"""['"](task_type_[a-z0-9_]+)['"]\s*:""")


def _load_task_type_keys(locale: str, namespace: str) -> set[str]:
    path = REPO_ROOT / VOCAB_TS.format(locale=locale, namespace=namespace)
    text = path.read_text(encoding="utf-8")
    return set(_KEY_RE.findall(text))


@pytest.mark.parametrize("namespace", sorted(EXPECTED_BY_NAMESPACE))
@pytest.mark.parametrize("locale", LOCALES)
def test_every_task_type_has_frontend_display_name(locale: str, namespace: str) -> None:
    keys = _load_task_type_keys(locale, namespace)
    missing = EXPECTED_BY_NAMESPACE[namespace] - keys
    assert not missing, (
        f"frontend/src/i18n/{locale}/{namespace}.ts 缺少 task_type 显示名翻译: {sorted(missing)}。"
        f" 固定字面量见本文件 FIXED_TASK_TYPES，动态部分单一真相源在 lib/project/asset_types.ASSET_SPECS。"
    )


@pytest.mark.parametrize("namespace", sorted(EXPECTED_BY_NAMESPACE))
def test_no_orphan_task_type_keys_in_any_locale(namespace: str) -> None:
    """Frontend task_type_* keys 必须都对应该命名空间会展示的 task_type —— 防止过时翻译堆积。"""
    for locale in LOCALES:
        keys = _load_task_type_keys(locale, namespace)
        orphans = keys - EXPECTED_BY_NAMESPACE[namespace]
        assert not orphans, (
            f"frontend/src/i18n/{locale}/{namespace}.ts 存在与已知 task_type 不匹配的 task_type_* key: "
            f"{sorted(orphans)}。请删除或更新本文件的 FIXED_TASK_TYPES。"
        )
