"""Short-drama standards pack renderer.

This module composes the Phase 1 standards pack from stable rule constants.  It
is intentionally read-only/prompt-facing; deterministic enforcement belongs to
`lib.short_drama_qa` in a later goal.
"""

from lib.prompt_rules.creative_quality import CREATIVE_QUALITY_RULES
from lib.prompt_rules.seedance_policy import SEEDANCE_PROMPT_POLICY
from lib.prompt_rules.storyboard_continuity import STORYBOARD_CONTINUITY_RULES

_SHORT_DRAMA_HEADER = "短剧 / Seedance 制作标准（Phase 1）"


def render_short_drama_standards_section(content_mode: str) -> str:
    """Render shared production standards for script prompt builders.

    `content_mode` is used only for the mode note; the standards themselves stay
    provider-neutral and do not add a new model/vendor path.
    """
    if content_mode not in {"drama", "narration"}:
        raise ValueError(f"unknown content_mode: {content_mode!r}")
    mode_note = (
        "drama 模式：step1 固定场景边界 / 资产 / 口播，step2 只补视觉层。"
        if content_mode == "drama"
        else "narration 模式：step1 固定逐字原文 / 资产 / 时长，step2 只补视觉层。"
    )
    return "\n\n".join(
        [
            _SHORT_DRAMA_HEADER,
            mode_note,
            SEEDANCE_PROMPT_POLICY,
            STORYBOARD_CONTINUITY_RULES,
            CREATIVE_QUALITY_RULES,
        ]
    )


__all__ = [
    "CREATIVE_QUALITY_RULES",
    "SEEDANCE_PROMPT_POLICY",
    "STORYBOARD_CONTINUITY_RULES",
    "render_short_drama_standards_section",
]
