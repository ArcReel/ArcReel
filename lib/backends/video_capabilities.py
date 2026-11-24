from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields
from types import EllipsisType
from typing import TYPE_CHECKING

from arcreel_catalog_core import VideoFacts
from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoAudioMode, VideoCapabilities, VideoRoute
from lib.backends.input_limits import InputLimit
from lib.backends.providers import PROVIDER_KLING, video_audio_always_billed
from lib.capabilities.kernel import AudioProtocol, CapabilitySnapshot, Endpoint, RequestConditions, resolve

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry

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


def billed_audio_for_request(
    provider: str | None, caps: VideoCapabilities | None, bucket: VideoRoute, requested: bool
) -> bool | None:
    if video_audio_always_billed(provider):
        return True
    return caps.audio_track_for_route(bucket).output_audio(requested) if caps is not None else None


def audio_protocol(mode: VideoAudioMode) -> AudioProtocol:
    return {
        VideoAudioMode.CONTROLLABLE: AudioProtocol.SWITCH,
        VideoAudioMode.ALWAYS_ON: AudioProtocol.NO_SWITCH,
        VideoAudioMode.ALWAYS_OFF: AudioProtocol.SILENT,
    }[mode]


def video_endpoint(channel: VideoCapabilities) -> Endpoint:
    slots = {k for k in ("text_to_video", "first_frame", "last_frame") if getattr(channel, k)}
    if channel.max_reference_images != 0:
        slots.add("reference_images")
    if channel.reference_audio_mode == ReferenceAudioMode.DIRECT:
        slots.add("reference_audio")
    defaults = {
        k: getattr(channel, k)
        for k in (
            "max_reference_images",
            "max_reference_audio_count",
            "max_reference_audio_total_seconds",
            "reference_audio_per_image",
            "max_prompt_chars",
            "first_frame_ratio_adaptive_only",
        )
    }
    return Endpoint(
        "video",
        frozenset(slots),
        defaults=defaults,
        audio=audio_protocol(channel.audio_track),
        reference_audio=audio_protocol(channel.reference_route_audio_track)
        if channel.reference_route_audio_track
        else None,
    )


def resolve_video_model(
    endpoint: Endpoint,
    facts: VideoFacts,
    overrides: object = None,
    *,
    model_id: str = "",
    provider_id: str = "",
    always_bill_audio: bool = False,
    default_resolution: str | None = None,
    official: bool = False,
    request: RequestConditions | None = None,
) -> CapabilitySnapshot:
    values = {f.name: getattr(facts, f.name) for f in fields(facts)}
    constraints = facts.duration_constraints
    values["duration_constraints"] = {
        "by_resolution": constraints.by_resolution,
        "with_reference_images": constraints.with_reference_images,
        "with_end_frame": constraints.with_end_frame,
    }
    legacy_primary_always_on = (
        provider_id == PROVIDER_KLING
        and isinstance(overrides, Mapping)
        and overrides.get("audio_track") == VideoAudioMode.ALWAYS_ON
    )
    return resolve(
        endpoint,
        facts=values,
        overrides=overrides,
        model_id=model_id,
        provider_id=provider_id,
        request=request,
        official=official,
        always_bill_audio=always_bill_audio,
        default_resolution=default_resolution,
        legacy_primary_always_on=legacy_primary_always_on,
    )


def projected_video_capabilities(snapshot: CapabilitySnapshot, *, base: bool = False) -> VideoCapabilities:
    values = snapshot.base if base else snapshot.effective

    primary = VideoAudioMode(snapshot.audio_track("i2v", base=base))
    reference = VideoAudioMode(snapshot.audio_track("r2v", base=base))
    explicit_reference = snapshot.endpoint.reference_audio is not None or (
        not base and "reference_route_has_audio_track" in snapshot.overrides
    )
    total_seconds = values["max_reference_audio_total_seconds"]
    return VideoCapabilities(
        text_to_video=values["text_to_video"],
        first_frame=values["first_frame"],
        last_frame=values["last_frame"],
        max_reference_images=values["max_reference_images"],
        max_prompt_chars=values.get("max_prompt_chars"),
        reference_audio_mode=ReferenceAudioMode(values["reference_audio_mode"]),
        max_reference_audio_count=values["max_reference_audio_count"],
        max_reference_audio_total_seconds=float(total_seconds) if total_seconds is not None else None,
        reference_audio_per_image=values.get("reference_audio_per_image", False),
        first_frame_ratio_adaptive_only=values.get("first_frame_ratio_adaptive_only", False),
        audio_track=primary,
        reference_route_audio_track=reference if explicit_reference or reference != primary else None,
    )


class VideoEndpointCapabilities:
    @classmethod
    def describe_video_endpoint(cls, model: str, entry: CatalogEntry | None = None) -> Endpoint:
        raise NotImplementedError

    @classmethod
    def video_capabilities_for_model(
        cls, model: str, entry: CatalogEntry | EllipsisType | None = ...
    ) -> VideoCapabilities:
        description = (
            cls.describe_video_endpoint(model)
            if isinstance(entry, EllipsisType)
            else cls.describe_video_endpoint(model, entry)
        )
        return channel_projection(description)


def channel_projection(endpoint: Endpoint, request: RequestConditions | None = None) -> VideoCapabilities:
    return projected_video_capabilities(
        resolve(endpoint, facts={"last_frame": True, "reference_audio": True}, request=request)
    )


def effective_video_capabilities(
    channel: VideoCapabilities, facts: VideoFacts, overrides: object = None
) -> VideoCapabilities:
    return projected_video_capabilities(resolve_video_model(video_endpoint(channel), facts, overrides))
