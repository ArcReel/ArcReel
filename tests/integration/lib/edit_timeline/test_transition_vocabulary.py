"""转场词表：每一项都能在 pyJianYingDraft 的非 VIP 转场枚举与随包 ffmpeg 的 ``xfade`` 效果里找到。"""

from __future__ import annotations

import re
import subprocess

from pyJianYingDraft import TransitionType as JianyingTransition

from lib.edit_timeline.model import TransitionType
from lib.edit_timeline.transitions import TRANSITION_PRESETS
from lib.infra.ffmpeg import ffmpeg_executable


def _xfade_effects() -> set[str]:
    help_text = subprocess.run(
        [ffmpeg_executable(), "-hide_banner", "-h", "filter=xfade"], capture_output=True, text=True, check=True
    ).stdout
    return set(re.findall(r"^\s+(\w+)\s+-?\d+\s+\.\.FV", help_text, flags=re.MULTILINE))


def test_every_transition_maps_to_a_free_jianying_preset_and_a_bundled_xfade_effect() -> None:
    xfade = _xfade_effects()
    jianying = {member.name: member.value for member in JianyingTransition}

    assert set(TRANSITION_PRESETS) == set(TransitionType)
    for transition_type, preset in TRANSITION_PRESETS.items():
        assert preset.jianying in jianying, transition_type
        assert not jianying[preset.jianying].is_vip, transition_type
        assert preset.xfade in xfade, transition_type


def test_only_fade_through_black_and_white_skip_borrowing_source_frames() -> None:
    assert {transition for transition, preset in TRANSITION_PRESETS.items() if not preset.overlaps} == {
        TransitionType.FADE_BLACK,
        TransitionType.FADE_WHITE,
    }
