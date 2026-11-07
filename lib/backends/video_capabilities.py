"""Effective video input capabilities, jointly constrained by entries and call channels (docs/adr/0104).

Input modes (text-to-video, first/last frames, reference audio) and counts (images, audio segments,
audio total duration) are dimensions on both sides. Entries state model acceptance/counts distributed
with official synchronization; channels state implemented paths/request capacity, declared in built-in
channels/endpoints (see video backends' ``video_capabilities``).

- Boolean support intersects both sides. Missing entry first/last-frame facts retain conservative
  fallback: first frame follows channels, last frame disabled. Missing text-to-video follows channels;
  missing reference audio disables it, matching unmatched-model fallbacks.
- Numeric values take minima (:func:`lib.backends.input_limits.effective_input_limit`). Unlisted entries
  leave channel constraints; neither declared means unlisted, without clipping, upstream rejecting excess.
  ``0`` only means refused input; channel ``0`` means the path is unimplemented.

Results remain :class:`VideoCapabilities`, with image/audio counts stored via :meth:`InputLimit.to_json`
(``None`` unlisted, ``0`` refused, otherwise maximum). :func:`reference_image_limit` and
:func:`reference_audio_count_limit` restore :class:`InputLimit`. Candidates, execution validation,
orchestration clipping, and settings all share this result.

Capability overrides replace whole effective dimensions after both sides evaluate. See
``lib.backends.backend_assembly.specs`` for user-added built-in-provider models and
:mod:`lib.custom_provider.capabilities` for custom providers. ComfyUI derives only from node bindings,
ignoring entries and bypassing this module.

Request shapes such as prompt lengths, audio-track forms, and first-frame ratios remain channel-only declarations, unchanged.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from arcreel_catalog_core import VideoFacts
from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoCapabilities
from lib.backends.input_limits import InputLimit, effective_input_limit

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry

#: Unmatched models leave all entry facts unlisted; effective capabilities use conservative fallbacks (module docs).
UNRECORDED_VIDEO_FACTS = VideoFacts()


def entry_video_facts(entry: CatalogEntry | None) -> VideoFacts:
    """Entry video-model facts; unmatched/nonvideo entries leave all facts unlisted."""
    if entry is not None and isinstance(entry.facts, VideoFacts):
        return entry.facts
    return UNRECORDED_VIDEO_FACTS


def reference_image_limit(caps: VideoCapabilities) -> InputLimit[int]:
    """Reference-image limit from channel-declared or effective video capabilities."""
    return InputLimit.from_json(caps.max_reference_images)


def reference_audio_count_limit(caps: VideoCapabilities) -> InputLimit[int]:
    """Reference-audio segment limit, refusing input when audio mode is ``NONE``."""
    if caps.reference_audio_mode == ReferenceAudioMode.NONE:
        return InputLimit.unsupported()
    return InputLimit.from_json(caps.max_reference_audio_count)


def effective_video_capabilities(channel: VideoCapabilities, facts: VideoFacts) -> VideoCapabilities:
    """Video capabilities after evaluating channel declarations and entry facts; see module documentation.

    ``channel`` may lack request context or resolve by request tiers, such as Kling last frames enabled
    only in pro. Both evaluate against entries under the same rules.
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
        # Without reference-audio support, total duration is inapplicable, None; with support, None means neither side declared totals.
        max_reference_audio_total_seconds=total_seconds.maximum,
    )
