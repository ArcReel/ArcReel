"""视频调用通道按名字族认定模型的通道能力（docs/adr/0104）。

通道事实（输入模式、参考图与参考音频上限、提示词长度上限、音轨形态）由各调用通道按模型名声明。拿来比对的
名字取模型所对应条目的官方模型名与模型别名，对应不上条目时取 API 模型名（docs/adr/0105）。各通道按自己的
名字族规则逐个试这些名字，第一个认得的名字决定能力；一个都认不得时统一回落为
:data:`UNRECOGNIZED_MODEL_CAPABILITIES`：只支持文生视频与首帧，不开尾帧，参考图与参考音频上限为 0。

内置供应商清单里的 API 模型名就是所引条目的官方模型名或模型别名，按名字现查得到的正是清单所引的条目；
自定义供应商的模型按同一规则现查。
"""

from __future__ import annotations

from collections.abc import Callable

from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoCapabilities
from lib.catalog import query as catalog

#: 调用通道认不得的模型统一取这组输入模式与上限。音轨形态、提示词长度上限这类与输入模式无关的通道事实
#: 不在统一之列：调用通道对认不得的模型照样按自己的请求构造声明（如不下发音轨开关时声明恒无声），
#: 用 ``dataclasses.replace`` 在这份基础上改。
UNRECOGNIZED_MODEL_CAPABILITIES = VideoCapabilities(
    text_to_video=True,
    first_frame=True,
    last_frame=False,
    max_reference_images=0,
    reference_audio_mode=ReferenceAudioMode.NONE,
    max_reference_audio_count=0,
)


def capability_names(model: str) -> tuple[str, ...]:
    """按名字族认定通道能力时拿来比对的名字：对应条目的官方模型名与模型别名，对应不上条目时为 API 模型名。"""
    entry = catalog.find_entry(model, media_type="video")
    if entry is None:
        return (model,)
    return (entry.name, *entry.aliases)


def match_by_names[T](model: str, match: Callable[[str], T | None]) -> T | None:
    """依次用 :func:`capability_names` 的名字调 ``match``，返回第一个非 ``None`` 的结果；都认不得时为 ``None``。"""
    for name in capability_names(model):
        found = match(name)
        if found is not None:
            return found
    return None


def capabilities_by_names(
    model: str,
    match: Callable[[str], VideoCapabilities | None],
    *,
    fallback: VideoCapabilities = UNRECOGNIZED_MODEL_CAPABILITIES,
) -> VideoCapabilities:
    """调用通道对模型声明的视频能力：按名字族认得时取 ``match`` 的结果，认不得时取 ``fallback``。

    ``fallback`` 只在 :data:`UNRECOGNIZED_MODEL_CAPABILITIES` 的基础上改输入模式以外的通道事实。
    """
    found = match_by_names(model, match)
    return found if found is not None else fallback
