"""BGM 轨的摆放：纯函数，时间一律是整数微秒。读取投影、成片与剪映草稿共用这份摆放。

BGM 片段按绝对起点摆放，放完截取区间即止；同一时刻只有一首（由批量编辑保证）。超出时间线末尾的部分截断，
截断处固定淡出 :data:`BGM_CUTOFF_FADE_MICROSECONDS`，不取片段自身的淡出时长；完全落在末尾之后的片段不出声。
淡入淡出加起来超过摆放后的时长时：截断的片段先保住截断处的淡出，再缩短淡入；未截断的片段两者按比例缩短。
剪辑视图预览直接取读取结果里摆放后的起止与淡入淡出，不另算一遍。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lib.bgm.library import BgmSource
from lib.edit_timeline.model import BgmClip

BGM_CUTOFF_FADE_MICROSECONDS = 1_000_000
"""超出时间线末尾被截断的 BGM 片段，在截断处淡出这么久。"""


@dataclass(frozen=True, slots=True)
class PlacedBgm:
    """时间线上实际出声的一段 BGM：``volume`` 是片段音量，还没乘上 BGM 的响度增益。"""

    clip_id: str
    bgm_id: str
    start_us: int
    source_in_us: int
    duration_us: int
    volume: float
    fade_in_us: int
    fade_out_us: int
    truncated: bool

    @property
    def end_us(self) -> int:
        return self.start_us + self.duration_us


def bgm_clip_length_us(clip: BgmClip) -> int:
    return clip.out_us - clip.in_us


def place_bgm(bgm: Sequence[BgmClip], duration_us: int) -> tuple[PlacedBgm, ...]:
    """按起点排列 BGM 片段，截到时间线末尾；截成空的丢弃。"""
    placed: list[PlacedBgm] = []
    for clip in sorted(bgm, key=lambda item: item.start_us):
        natural_end = clip.start_us + bgm_clip_length_us(clip)
        end = min(natural_end, duration_us)
        if end <= clip.start_us:
            continue
        length = end - clip.start_us
        truncated = end < natural_end
        fade_in, fade_out = _fit_fades(clip, length, truncated=truncated)
        placed.append(
            PlacedBgm(
                clip_id=clip.id,
                bgm_id=clip.bgm_id,
                start_us=clip.start_us,
                source_in_us=clip.in_us,
                duration_us=length,
                volume=clip.volume,
                fade_in_us=fade_in,
                fade_out_us=fade_out,
                truncated=truncated,
            )
        )
    return tuple(placed)


def _fit_fades(clip: BgmClip, length: int, *, truncated: bool) -> tuple[int, int]:
    if truncated:
        fade_out = min(BGM_CUTOFF_FADE_MICROSECONDS, length)
        return min(clip.fade_in_us, length - fade_out), fade_out
    total = clip.fade_in_us + clip.fade_out_us
    if total <= length:
        return clip.fade_in_us, clip.fade_out_us
    return clip.fade_in_us * length // total, clip.fade_out_us * length // total


def bgm_ids(bgm: Sequence[BgmClip]) -> tuple[str, ...]:
    """BGM 轨引用的 BGM，按首次出现的顺序。"""
    return tuple(dict.fromkeys(clip.bgm_id for clip in bgm))


def bgm_sources_input(bgm: Sequence[BgmClip], sources: Mapping[str, BgmSource]) -> dict[str, object]:
    """生成依据里 BGM 的部分：每首被引用 BGM 的内容指纹与静态增益；取不到的记为不可用。"""
    return {
        bgm_id: source.to_input() if (source := sources.get(bgm_id)) is not None else {"unavailable": True}
        for bgm_id in bgm_ids(bgm)
    }


__all__ = [
    "BGM_CUTOFF_FADE_MICROSECONDS",
    "PlacedBgm",
    "bgm_clip_length_us",
    "bgm_ids",
    "bgm_sources_input",
    "place_bgm",
]
