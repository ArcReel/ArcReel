"""Map transition vocabulary to both outputs: each entry maps to a non-VIP Jianying preset and a bundled ffmpeg ``xfade`` effect.

Overlapping transitions borrow source media from adjacent clips across the cut. Nonoverlapping ones
(fade black/white) borrow none, fading out/in to ``fade_color`` on either side. Manual QA verifies visual
consistency between outputs per entry; remove mismatches from the vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from lib.edit_timeline.model import TransitionType


@dataclass(frozen=True, slots=True)
class TransitionPreset:
    """Concrete transition implementations in Jianying drafts and final videos.

    ``jianying`` is a pyJianYingDraft ``TransitionType`` member name; ``xfade`` is ffmpeg ``xfade``'s
    ``transition`` value; ``fade_color`` appears only for nonoverlapping transitions, used for final-video fade-out/in."""

    jianying: str
    xfade: str
    fade_color: str | None = None

    @property
    def overlaps(self) -> bool:
        return self.fade_color is None


TRANSITION_PRESETS: MappingProxyType[TransitionType, TransitionPreset] = MappingProxyType(
    {
        TransitionType.DISSOLVE: TransitionPreset("叠化", "fade"),
        TransitionType.FADE_BLACK: TransitionPreset("闪黑", "fadeblack", fade_color="black"),
        TransitionType.FADE_WHITE: TransitionPreset("闪白", "fadewhite", fade_color="white"),
        TransitionType.PUSH_LEFT: TransitionPreset("向左", "slideleft"),
        TransitionType.PUSH_RIGHT: TransitionPreset("向右", "slideright"),
        TransitionType.PUSH_UP: TransitionPreset("向上", "slideup"),
        TransitionType.PUSH_DOWN: TransitionPreset("向下", "slidedown"),
        TransitionType.WIPE_LEFT: TransitionPreset("向左擦除", "wipeleft"),
        TransitionType.WIPE_RIGHT: TransitionPreset("向右擦除", "wiperight"),
        TransitionType.WIPE_UP: TransitionPreset("向上擦除", "wipeup"),
        TransitionType.WIPE_DOWN: TransitionPreset("向下擦除", "wipedown"),
        TransitionType.CIRCLE: TransitionPreset("圆形遮罩", "circleopen"),
        TransitionType.CURTAIN_HORIZONTAL: TransitionPreset("横向拉幕", "horzopen"),
        TransitionType.CURTAIN_VERTICAL: TransitionPreset("竖向拉幕", "vertopen"),
        TransitionType.MOSAIC: TransitionPreset("马赛克", "pixelize"),
        TransitionType.BLUR: TransitionPreset("模糊", "hblur"),
        TransitionType.RADIAL: TransitionPreset("放射", "radial"),
        TransitionType.GRADIENT_WIPE: TransitionPreset("渐变擦除", "smoothleft"),
        TransitionType.SQUEEZE: TransitionPreset("压缩", "squeezeh"),
    }
)


def transition_preset(transition_type: TransitionType) -> TransitionPreset:
    return TRANSITION_PRESETS[transition_type]


__all__ = ["TRANSITION_PRESETS", "TransitionPreset", "transition_preset"]
