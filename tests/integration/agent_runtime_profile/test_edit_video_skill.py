"""edit-video skill：出片交付的 look 纪律——先看图核对画面、读交付检查结论，再向创作者汇报。"""

from __future__ import annotations

from lib import PROJECT_ROOT

SKILL = PROJECT_ROOT / "agent_runtime_profile" / ".claude" / "skills" / "edit-video" / "SKILL.md"


def test_final_cut_delivery_requires_looking_at_the_contact_sheet_first() -> None:
    section = SKILL.read_text(encoding="utf-8").split("## 出片", 1)[1]

    assert "联系表" in section
    assert "交付检查结论" in section
    # 看图与读交付结论都在给出下载链接与验收结果之前。
    assert section.index("先看图") < section.index("再给出下载链接")
