"""视频的生效输入能力：模型条目与调用通道两侧共同约束（docs/adr/0104）。

视频的输入模式（文生视频、首帧、尾帧、参考音频）与数量上限（参考图、参考音频段数、参考音频总时长）在两侧各是
一个维度：模型条目说模型接受什么、能接受多少（模型事实，随官方同步下发），调用通道说这条通道实现了什么、一次能
承载多少（通道事实，内置调用通道与调用端点在代码里声明，见各视频后端的 ``video_capabilities``）。

- 是否支持类取两侧都满足的部分。条目未收录首帧、尾帧时沿用保守回落：首帧跟随通道，尾帧不开；未收录文生视频
  时跟随通道，未收录参考音频时不开，与对应不上条目的模型的回落一致。
- 数值类取两侧中较小的那个（:func:`lib.backends.input_limits.effective_input_limit`）。条目未收录时只受通道约束，
  两侧都没有声明时为「未收录」，执行时不裁剪，超出部分由上游拒绝。``0`` 只表示不接受；调用通道声明 ``0`` 即
  没有实现这条路径。

结果仍是一份 :class:`VideoCapabilities`，参考图与参考音频段数上限按 :meth:`InputLimit.to_json` 的形状存
（``None`` 未收录、``0`` 不接受、其余为上限），读取时经 :func:`reference_image_limit` 与
:func:`reference_audio_count_limit` 还原为 :class:`InputLimit`。任务类型桶的候选、执行时的校验、编排层的裁剪与
设置页都读这一个结果。

能力覆盖整维替换生效值，在两侧求值之后生效（内置供应商下用户添加的模型见
``lib.backends.backend_assembly.specs``，自定义供应商见 :mod:`lib.custom_provider.capabilities`）。ComfyUI 端点的
能力只来自节点绑定，不读条目，不经过本模块。

提示词长度上限、音轨形态、首帧比例约束这些请求形态仍只由调用通道声明，原样保留。
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from arcreel_catalog_core import VideoFacts
from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoCapabilities
from lib.backends.input_limits import InputLimit, effective_input_limit

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry

#: 对应不上条目的模型：模型事实一侧全部未收录，生效能力按保守回落取（见模块文档）。
UNRECORDED_VIDEO_FACTS = VideoFacts()


def entry_video_facts(entry: CatalogEntry | None) -> VideoFacts:
    """条目上的视频模型事实；对应不上条目或条目不是视频时全部未收录。"""
    if entry is not None and isinstance(entry.facts, VideoFacts):
        return entry.facts
    return UNRECORDED_VIDEO_FACTS


def reference_image_limit(caps: VideoCapabilities) -> InputLimit[int]:
    """一份视频能力（调用通道的声明或生效能力）的参考图上限。"""
    return InputLimit.from_json(caps.max_reference_images)


def reference_audio_count_limit(caps: VideoCapabilities) -> InputLimit[int]:
    """参考音频段数上限；参考音频模式为 ``NONE`` 时不接受。"""
    if caps.reference_audio_mode == ReferenceAudioMode.NONE:
        return InputLimit.unsupported()
    return InputLimit.from_json(caps.max_reference_audio_count)


def effective_video_capabilities(channel: VideoCapabilities, facts: VideoFacts) -> VideoCapabilities:
    """调用通道的声明与模型条目的事实两侧求值后的视频能力（见模块文档）。

    ``channel`` 可以是无请求上下文的声明，也可以是按请求档位解析的声明（如可灵在 pro 档才放开尾帧），两者按同一
    规则与条目求值。
    """
    images = effective_input_limit(
        path_supported=channel.max_reference_images != 0,
        declared=(facts.max_reference_images, channel.max_reference_images),
    )
    audio_path = channel.reference_audio_mode != ReferenceAudioMode.NONE and facts.reference_audio is True
    audio_count = effective_input_limit(
        path_supported=audio_path,
        declared=(facts.max_reference_audio_count, channel.max_reference_audio_count),
    )
    audio_open = audio_count.accepts_input
    total_seconds = effective_input_limit(
        path_supported=audio_open,
        declared=(facts.max_reference_audio_total_seconds, channel.max_reference_audio_total_seconds),
    )
    return replace(
        channel,
        text_to_video=channel.text_to_video and facts.text_to_video is not False,
        first_frame=channel.first_frame and facts.first_frame is not False,
        last_frame=channel.last_frame and facts.last_frame is True,
        max_reference_images=images.to_json(),
        reference_audio_mode=channel.reference_audio_mode if audio_open else ReferenceAudioMode.NONE,
        max_reference_audio_count=audio_count.to_json(),
        # 不接受参考音频时总时长无从谈起，为 None；接受时 None 即两侧都没有声明总时长。
        max_reference_audio_total_seconds=total_seconds.maximum,
    )
