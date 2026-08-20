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

from lib.profile_frontmatter import parse_profile_metadata
from lib.profile_manifest import VALID_CONTENT_MODES

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / "agent_runtime_profile" / ".claude" / "skills"
DASHBOARD_TS = "frontend/src/i18n/{locale}/dashboard.ts"
LOCALES = ("zh", "en", "vi")

_SKILL_KEY_RE = re.compile(r"""['"](skill_name_[a-z0-9_]+)['"]\s*:""")


def _is_user_invocable(skill_md: Path) -> bool:
    return parse_profile_metadata(skill_md).user_invocable


def _find_skill_md(skill_dir: Path) -> Path | None:
    """优先 SKILL.md；否则任一 SKILL.<mode>.md 变体。

    双变体同时存在时，要求所有变体的 user-invocable 状态一致——否则若一份
    user-invocable=true 另一份 false，前端只会显示其中一份的翻译 key，CI 校验
    会漏掉这种漂移。校验失败直接 raise，让回归用例显式 fail。
    """
    common = skill_dir / "SKILL.md"
    if common.is_file():
        return common
    variants = [skill_dir / f"SKILL.{mode}.md" for mode in sorted(VALID_CONTENT_MODES)]
    existing = [v for v in variants if v.is_file()]
    if not existing:
        return None
    states = {v.name: _is_user_invocable(v) for v in existing}
    if len(set(states.values())) > 1:
        raise AssertionError(
            f"skill {skill_dir.name} 各 mode 变体的 user-invocable 不一致: {states}; "
            "请保证所有 SKILL.<mode>.md frontmatter 的 user-invocable 字段相同"
        )
    return existing[0]


def _user_invocable_skill_ids() -> set[str]:
    if not SKILLS_ROOT.is_dir():
        return set()
    ids: set[str] = set()
    for skill_dir in sorted(SKILLS_ROOT.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = _find_skill_md(skill_dir)
        if skill_md is None:
            continue
        if _is_user_invocable(skill_md):
            ids.add(skill_dir.name.replace("-", "_"))
    return ids


def _load_skill_name_keys(locale: str) -> set[str]:
    path = REPO_ROOT / DASHBOARD_TS.format(locale=locale)
    text = path.read_text(encoding="utf-8")
    return set(_SKILL_KEY_RE.findall(text))


def test_user_invocable_uses_shared_yaml_boolean_parser(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "---\nname: hidden\ndescription: Hidden skill\nuser-invocable: off\n---\n",
        encoding="utf-8",
    )

    assert _is_user_invocable(skill) is False


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
