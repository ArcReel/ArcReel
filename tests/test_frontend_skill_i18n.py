"""Cross-check that every user-invocable agent skill has a frontend display name.

The single source of truth is the set of ``SKILL.md`` files under
``agent_runtime_profile/.claude/skills/`` whose YAML frontmatter declares
``user-invocable: true`` (the default when the field is absent). The frontend
renders each skill chip via i18n ``dashboard:skill_name_<id>`` where the id is
the skill directory name with ``-`` replaced by ``_``.

If a backend skill ships without a corresponding translation in zh/en/vi, the
chip falls back to ``/skill-name`` (raw id). This test fails CI in that case
so the gap is caught at PR time.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / "agent_runtime_profile" / ".claude" / "skills"
DASHBOARD_TS = "frontend/src/i18n/{locale}/dashboard.ts"
LOCALES = ("zh", "en", "vi")

_SKILL_KEY_RE = re.compile(r"""['"](skill_name_[a-z0-9_]+)['"]\s*:""")
_USER_INVOCABLE_RE = re.compile(r"^\s*user-invocable\s*:\s*(\S+)", re.MULTILINE)


def _is_user_invocable(skill_md: Path) -> bool:
    text = skill_md.read_text(encoding="utf-8", errors="ignore")
    if not text.startswith("---"):
        return True
    parts = text.split("---", 2)
    if len(parts) < 3:
        return True
    match = _USER_INVOCABLE_RE.search(parts[1])
    if not match:
        return True  # Default when field absent
    # YAML 允许 ``user-invocable: "false"`` / ``'false'`` —— 解析时要去掉引号
    # 再判断，否则带引号的 false 会被误判为 truthy。
    raw = match.group(1).strip().strip("'\"").lower()
    return raw not in {"false", "no", "0"}


def _user_invocable_skill_ids() -> set[str]:
    if not SKILLS_ROOT.is_dir():
        return set()
    ids: set[str] = set()
    for skill_dir in sorted(SKILLS_ROOT.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        if _is_user_invocable(skill_md):
            ids.add(skill_dir.name.replace("-", "_"))
    return ids


def _load_skill_name_keys(locale: str) -> set[str]:
    path = REPO_ROOT / DASHBOARD_TS.format(locale=locale)
    text = path.read_text(encoding="utf-8")
    return set(_SKILL_KEY_RE.findall(text))


@pytest.mark.parametrize("locale", LOCALES)
def test_every_user_invocable_skill_has_frontend_display_name(locale: str) -> None:
    skill_ids = _user_invocable_skill_ids()
    assert skill_ids, "未发现任何 user-invocable skill；检查 agent_runtime_profile/.claude/skills/"

    keys = _load_skill_name_keys(locale)
    expected = {f"skill_name_{sid}" for sid in skill_ids}
    missing = expected - keys
    assert not missing, (
        f"frontend/src/i18n/{locale}/dashboard.ts 缺少 skill 显示名翻译: {sorted(missing)}。"
        f" 单一真相源在 agent_runtime_profile/.claude/skills/*/SKILL.md（user-invocable: true）。"
    )


def test_no_orphan_skill_name_keys_in_any_locale() -> None:
    """Frontend skill_name_* keys 必须都对应 user-invocable SKILL.md —— 防止过时翻译堆积。"""
    skill_ids = _user_invocable_skill_ids()
    expected = {f"skill_name_{sid}" for sid in skill_ids}
    for locale in LOCALES:
        keys = _load_skill_name_keys(locale)
        orphans = keys - expected
        assert not orphans, (
            f"frontend/src/i18n/{locale}/dashboard.ts 存在与 SKILL.md 不匹配的 skill_name_* key: "
            f"{sorted(orphans)}。请删除或恢复对应 SKILL.md 的 user-invocable 状态。"
        )
