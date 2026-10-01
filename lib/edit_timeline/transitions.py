"""转场词表到两个输出端的对照：每一项对应一个剪映非 VIP 预设与一个随包 ffmpeg ``xfade`` 效果。

重叠型转场在切点两侧借用相邻片段的源素材做交叉过渡；非重叠型（闪黑、闪白）不借素材，只在切点两侧
分别淡出、淡入到 ``fade_color``。两端的视觉一致性由人工 QA 逐项核对，对不上的项从词表剔除。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from lib.edit_timeline.model import TransitionType


@dataclass(frozen=True, slots=True)
class TransitionPreset:
    """一种转场在剪映草稿与成片里的具体做法。

    ``jianying`` 是 pyJianYingDraft ``TransitionType`` 的成员名；``xfade`` 是 ffmpeg ``xfade`` 的
    ``transition`` 取值；``fade_color`` 只在非重叠型上给出，成片用它做淡出、淡入。
    """

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
