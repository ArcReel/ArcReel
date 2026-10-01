"""字幕样式基线：剪映草稿的字幕样式，成片烧入的字幕按它换算。

白字、粗体、黑色描边与阴影；竖屏字号 12、行宽占画布宽度 82%、纵向位置 −0.75，横屏字号 8、行宽 60%、
纵向位置 −0.8。剪辑视图预览按同一组数值换算字幕外观（frontend/src/components/canvas/edit/preview-tracks.ts
``subtitleLayout``），改动时一并修改。
"""

from __future__ import annotations

from dataclasses import dataclass

JIANYING_FONT_PX_PER_SIZE = 5
"""剪映字号 1 在短边 1080 的画布上约合 5 像素字身。"""


@dataclass(frozen=True, slots=True)
class SubtitleStyleBaseline:
    """``size`` 是剪映字号；``max_line_width`` 是行宽占画布宽度的比例；``transform_y`` 是剪映的纵向位置，
    以半个画布高为单位，−1 为底边、0 为居中。"""

    size: float
    max_line_width: float
    transform_y: float


def subtitle_style_baseline(width: int, height: int) -> SubtitleStyleBaseline:
    """按画布横竖取字幕样式基线。"""
    if height > width:
        return SubtitleStyleBaseline(size=12.0, max_line_width=0.82, transform_y=-0.75)
    return SubtitleStyleBaseline(size=8.0, max_line_width=0.6, transform_y=-0.8)


__all__ = ["JIANYING_FONT_PX_PER_SIZE", "SubtitleStyleBaseline", "subtitle_style_baseline"]
