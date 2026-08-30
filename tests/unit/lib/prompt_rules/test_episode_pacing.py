"""``render_pacing_section`` 按 content_mode 交回 ``.claude/references/`` 下对应的规则文件，测试不抄文案措辞。"""

from pathlib import Path

import pytest

from lib.prompt_rules.episode_pacing import PACING_RULE_FILES, render_pacing_section


@pytest.mark.parametrize("content_mode", sorted(PACING_RULE_FILES))
def test_rules_come_from_the_profile_directory_at_call_time(
    content_mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """规则正文住在 agent profile 里，换 profile 目录即换文本——不在导入时定死。"""
    references = tmp_path / ".claude" / "references"
    references.mkdir(parents=True)
    (references / PACING_RULE_FILES[content_mode]).write_text("替身节奏规则", encoding="utf-8")
    monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(tmp_path))

    assert render_pacing_section(content_mode) == "替身节奏规则"


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError, match="unknown content_mode"):
        render_pacing_section("unknown")
