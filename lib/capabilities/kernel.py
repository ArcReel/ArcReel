"""供应商模型的协议硬边界、能力覆盖与单次求值。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal

from lib.capabilities.limits import InputLimit, LimitState
from lib.capabilities.override_schema import (
    INPUT_SLOT,
    LEGACY_AUDIO_KEYS,
    OVERRIDE_KEYS,
    VIDEO_CHANNEL_FLAG_KEYS,
    is_legacy_audio_mode,
    normalized_value,
    same_override_value,
    valid_value,
)

type Values = Mapping[str, Any]
type Media = Literal["text", "image", "video"]
type Route = Literal["i2v", "r2v"]


class AudioProtocol(StrEnum):
    SWITCH = "switch"
    NO_SWITCH = "no_switch"
    SILENT = "forced_silent"


def _restrict_audio(base: AudioProtocol, restriction: AudioProtocol) -> AudioProtocol:
    if AudioProtocol.SILENT in (base, restriction):
        return AudioProtocol.SILENT
    if AudioProtocol.NO_SWITCH in (base, restriction):
        return AudioProtocol.NO_SWITCH
    return AudioProtocol.SWITCH


@dataclass(frozen=True)
class AudioDecision:
    requested: bool
    switch: bool | None
    output: bool
    billed: bool


@dataclass(frozen=True)
class FixedDuration:
    reason: Literal["no_frames", "missing_fps", "no_whole_seconds"]


@dataclass(frozen=True)
class EndpointDurations:
    seconds: tuple[int, ...]

    def __post_init__(self):
        if not self.seconds or not all(type(n) is int and n > 0 for n in self.seconds):
            raise ValueError("endpoint durations")
        object.__setattr__(self, "seconds", tuple(self.seconds))


@dataclass(frozen=True)
class DurationCorrection:
    by_subpath: Mapping[str, tuple[int, ...]]
    fallback: int
    preferred: int | None = None

    def __post_init__(self):
        object.__setattr__(
            self, "by_subpath", MappingProxyType({key: tuple(seconds) for key, seconds in self.by_subpath.items()})
        )

    def correct(self, subpath: str, requested: int | None) -> int:
        allowed = self.by_subpath.get(subpath)
        if not allowed:
            return requested or self.fallback
        if requested is None:
            if self.preferred is not None and self.preferred in allowed:
                return self.preferred
            return self.fallback if self.fallback in allowed else allowed[0]
        return min(allowed, key=lambda value: abs(value - requested))


@dataclass(frozen=True)
class ModelDurations:
    correction: DurationCorrection | None = None


type DurationMode = FixedDuration | EndpointDurations | ModelDurations


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, StrEnum):
        return value.value
    if value is None or type(value) in (str, bool, int, float):
        return value
    raise ValueError(f"invalid capability value: {type(value).__name__}")


type OverrideRejectionReason = Literal[
    "official",
    "not_applicable",
    "invalid_value",
    "image_unsupported",
    "last_frame_unsupported",
    "reference_audio_unsupported",
    "audio_pair_incoherent",
]


class InvalidOverrides(ValueError):
    def __init__(self, keys: set[str], reasons: Mapping[str, OverrideRejectionReason] | None = None):
        self.keys = set(keys)
        self.reasons: dict[str, OverrideRejectionReason] = dict(reasons or {})
        super().__init__(", ".join(sorted(keys)))


class IncompatibleInputsError(ValueError):
    pass


@dataclass(frozen=True)
class RequestConditions:
    bucket: Route
    resolution: str | None = None
    service_tier: str = "default"
    inputs: frozenset[str] = frozenset()

    def __post_init__(self):
        object.__setattr__(self, "resolution", self.resolution.lower() if self.resolution else None)
        object.__setattr__(self, "service_tier", (self.service_tier or "default").lower())
        object.__setattr__(self, "inputs", frozenset(self.inputs))


@dataclass(frozen=True)
class PathRestriction:
    """满足所有非空条件时，收窄协议可传输的槽位或音轨。"""

    bucket: Route | None = None
    resolutions: frozenset[str | None] = frozenset()
    tiers: frozenset[str] = frozenset()
    except_resolutions: frozenset[str] = frozenset()
    except_tiers: frozenset[str] = frozenset()
    required_inputs: frozenset[str] = frozenset()
    disabled_slots: frozenset[str] = frozenset()
    audio: AudioProtocol | None = None

    def __post_init__(self):
        for name in ("resolutions", "tiers", "except_resolutions", "except_tiers", "required_inputs", "disabled_slots"):
            object.__setattr__(self, name, frozenset(getattr(self, name)))

    def matches(self, request: RequestConditions) -> bool:
        return (
            (self.bucket is None or request.bucket == self.bucket)
            and (not self.resolutions or request.resolution in self.resolutions)
            and (not self.tiers or request.service_tier in self.tiers)
            and request.resolution not in self.except_resolutions
            and request.service_tier not in self.except_tiers
            and self.required_inputs <= request.inputs
        )


@dataclass(frozen=True)
class Endpoint:
    """槽位与硬上限描述所有可达请求，paths 按本次请求收窄。defaults 只参与软默认合成。"""

    media: Media
    slots: frozenset[str] = frozenset()
    limits: Mapping[str, int | float] = field(default_factory=dict)
    defaults: Values = field(default_factory=dict)
    audio: AudioProtocol = AudioProtocol.NO_SWITCH
    reference_audio: AudioProtocol | None = None
    duration: DurationMode = ModelDurations()
    exclusive_inputs: tuple[frozenset[str], ...] = ()
    paths: tuple[PathRestriction, ...] = ()
    context_free_disabled_slots: frozenset[str] = frozenset()
    binding_owned: bool = False

    def __post_init__(self):
        if set(self.defaults) - (OVERRIDE_KEYS[self.media] | {"qualities"} | VIDEO_CHANNEL_FLAG_KEYS):
            raise ValueError("unknown endpoint capability")
        if any(
            not k.startswith("max_") or k not in OVERRIDE_KEYS[self.media] or not valid_value(k, v)
            for k, v in self.limits.items()
        ):
            raise ValueError("invalid endpoint limit")
        object.__setattr__(self, "context_free_disabled_slots", frozenset(self.context_free_disabled_slots))
        slots = frozenset(self.slots)
        if self.media == "image":
            slots = slots - {"reference_images"}
            if "image_to_image" in slots:
                slots |= {"reference_images"}
        object.__setattr__(self, "slots", slots)
        object.__setattr__(self, "paths", tuple(self.paths))
        object.__setattr__(self, "exclusive_inputs", tuple(frozenset(group) for group in self.exclusive_inputs))
        object.__setattr__(self, "limits", _freeze(self.limits))
        object.__setattr__(self, "defaults", _freeze(self.defaults))

    def protocol(self, route: Route) -> AudioProtocol:
        return (self.reference_audio or self.audio) if route == "r2v" else self.audio

    @property
    def editable(self) -> frozenset[str]:
        keys = OVERRIDE_KEYS[self.media]
        if self.binding_owned or not isinstance(self.duration, ModelDurations):
            return frozenset({"durations"}) if isinstance(self.duration, EndpointDurations) else frozenset()
        return frozenset(k for k in keys if self._supported(k))

    def _supported(self, key: str) -> bool:
        if key in LEGACY_AUDIO_KEYS.values():
            route = "i2v" if key == "has_audio_track" else "r2v"
            return self.protocol(route) != AudioProtocol.SILENT
        if key in {"text_to_image", "image_to_image", "first_frame", "last_frame", "text_to_video", *INPUT_SLOT}:
            return INPUT_SLOT.get(key, key) in self.slots
        return True

    def validate(self, raw: Values, *, facts: Values | None = None, official: bool = False) -> dict[str, Any]:
        if official and raw:
            raise InvalidOverrides(set(raw), dict.fromkeys(raw, "official"))
        return self._overrides(raw, {} if self.binding_owned else facts or {}, strict=True)

    def filter_overrides(self, raw: object, facts: Values) -> dict[str, Any]:
        return self._overrides(raw, facts, strict=False) if isinstance(raw, Mapping) else {}

    def _overrides(self, raw: Values, facts: Values, *, strict: bool) -> dict[str, Any]:
        result: dict[str, Any] = {}
        invalid: set[str] = set()
        reasons: dict[str, OverrideRejectionReason] = {}

        def reject(key: str, reason: OverrideRejectionReason) -> None:
            invalid.add(key)
            reasons.setdefault(key, reason)

        for original, value in raw.items():
            key = LEGACY_AUDIO_KEYS.get(original, original)
            if original in LEGACY_AUDIO_KEYS and is_legacy_audio_mode(value):
                route: Route = "i2v" if original == "audio_track" else "r2v"
                if strict and value == "controllable" and self.protocol(route) != AudioProtocol.SWITCH:
                    reject(key, "not_applicable")
                    continue
                value = value != "always_off"
            elif original in LEGACY_AUDIO_KEYS:
                reject(key, "invalid_value")
                continue
            silent_false = (
                key in LEGACY_AUDIO_KEYS.values()
                and value is False
                and not self.binding_owned
                and isinstance(self.duration, ModelDurations)
            )
            if key not in OVERRIDE_KEYS[self.media]:
                reject(key, "not_applicable")
                continue
            if not valid_value(key, value):
                reject(key, "invalid_value")
                continue
            if key not in self.editable and not silent_false:
                if not self.binding_owned and key in ("text_to_image", "image_to_image") and value is True:
                    reject(key, "image_unsupported")
                elif not self.binding_owned and key == "last_frame" and value is True:
                    reject(key, "last_frame_unsupported")
                elif not self.binding_owned and key == "reference_audio_mode" and value == "direct":
                    reject(key, "reference_audio_unsupported")
                else:
                    reject(key, "not_applicable")
                continue
            if (key in result and result[key] != value) or (
                key in self.limits and (value is None or value > self.limits[key])
            ):
                reject(key, "not_applicable")
                continue
            result[key] = normalized_value(key, value)
        for key in invalid:
            result.pop(key, None)
        values = _compose(self, facts, result, self.slots)
        if (
            values.get("reference_audio_mode") == "direct"
            and values.get("max_reference_audio_count") == 0
            and (strict or result.get("reference_audio_mode") == "direct")
        ):
            for key in ("reference_audio_mode", "max_reference_audio_count"):
                if key in result:
                    reject(key, "audio_pair_incoherent")
                    del result[key]
            if "reference_audio_mode" in invalid:
                reject("max_reference_audio_count", "audio_pair_incoherent")
        if strict and invalid:
            raise InvalidOverrides(invalid, reasons)
        return result


TEXT_ENDPOINT = Endpoint("text")


def _compose(endpoint: Endpoint, facts: Values, overrides: Values, slots: frozenset[str]) -> dict[str, Any]:
    values = dict(endpoint.defaults)
    values.update({k: v for k, v in facts.items() if k in OVERRIDE_KEYS[endpoint.media] and v is not None})
    values.update(overrides)
    flags = {"video": ("first_frame", "last_frame", "text_to_video"), "image": ("text_to_image", "image_to_image")}
    for key in flags.get(endpoint.media, ()):
        values[key] = key in slots and values.get(key, endpoint.binding_owned or key != "last_frame")
    for key in OVERRIDE_KEYS[endpoint.media]:
        if not key.startswith("max_"):
            continue
        if key in INPUT_SLOT and INPUT_SLOT[key] not in slots:
            values[key] = 0
            continue
        bounds = [overrides[key]] if key in overrides else [facts.get(key), endpoint.defaults.get(key)]
        values[key] = min((v for v in [*bounds, endpoint.limits.get(key)] if v is not None), default=None)
    if endpoint.media == "video":
        if "reference_audio" not in slots:
            values["reference_audio_mode"] = "none"
        else:
            values["reference_audio_mode"] = overrides.get(
                "reference_audio_mode", "direct" if facts.get("reference_audio") or endpoint.binding_owned else "none"
            )
        if not facts.get("reference_audio") and not endpoint.binding_owned:
            if "max_reference_audio_count" not in overrides:
                values["max_reference_audio_count"] = 0
            if "max_reference_audio_total_seconds" not in overrides:
                values["max_reference_audio_total_seconds"] = None
        if "reference_audio" not in slots:
            values["max_reference_audio_total_seconds"] = None
        if isinstance(endpoint.duration, FixedDuration):
            values["durations"] = ()
        elif isinstance(endpoint.duration, EndpointDurations):
            values["durations"] = overrides.get("durations", endpoint.duration.seconds)
    return values


@dataclass(frozen=True)
class CapabilitySnapshot:
    endpoint: Endpoint
    base: Values
    overrides: Values
    effective: Values
    editable: frozenset[str]
    reference_images: InputLimit[int]
    primary_protocol: AudioProtocol
    reference_protocol: AudioProtocol
    default_resolution: str | None
    model_facts: Values
    official: bool
    provider_id: str
    model_id: str
    always_bill_audio: bool
    legacy_primary_always_on: bool

    def override_dict(self) -> dict[str, Any]:
        def unpack(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {k: unpack(v) for k, v in value.items()}
            if isinstance(value, tuple):
                return [unpack(v) for v in value]
            return value

        return {k: unpack(v) for k, v in self.overrides.items()}

    def validate_overrides(self, raw: Values, *, carried: Values | None = None) -> dict[str, Any]:
        try:
            return self.endpoint.validate(raw, facts=self.model_facts, official=self.official)
        except InvalidOverrides as exc:
            if not carried or "official" in exc.reasons.values():
                raise
            inherited = dict(carried)
            for legacy, key in LEGACY_AUDIO_KEYS.items():
                if is_legacy_audio_mode(inherited.get(legacy)):
                    inherited.setdefault(key, inherited[legacy] != "always_off")
            keep = dict(raw)
            paired = {key for legacy, key in LEGACY_AUDIO_KEYS.items() if legacy in raw and key in raw}
            incoherent_explicit = "audio_pair_incoherent" in exc.reasons.values() and any(
                k in raw and (k not in inherited or not same_override_value(k, inherited[k], raw[k]))
                for k in ("reference_audio_mode", "max_reference_audio_count")
            )
            for key, value in raw.items():
                canonical = LEGACY_AUDIO_KEYS.get(key, key)
                if canonical in paired:
                    continue
                if incoherent_explicit and exc.reasons.get(canonical) == "audio_pair_incoherent":
                    continue
                if canonical in exc.keys and key in inherited and same_override_value(key, inherited[key], value):
                    del keep[key]
            return self.endpoint.validate(keep, facts=self.model_facts, official=self.official)

    @property
    def media(self) -> Media:
        return self.endpoint.media

    @property
    def duration(self) -> DurationMode:
        return self.endpoint.duration

    def audio_track(self, route: Route, *, base: bool = False) -> Literal["controllable", "always_on", "always_off"]:
        decision = self.audio(route, requested=True, base=base)
        if not decision.output:
            return "always_off"
        return "controllable" if decision.switch is not None else "always_on"

    def for_request(self, request: RequestConditions) -> CapabilitySnapshot:
        return resolve(
            self.endpoint,
            facts=self.model_facts,
            overrides=self.overrides,
            request=request,
            official=self.official,
            default_resolution=self.default_resolution,
            provider_id=self.provider_id,
            model_id=self.model_id,
            always_bill_audio=self.always_bill_audio,
            legacy_primary_always_on=self.legacy_primary_always_on,
        )

    def __post_init__(self):
        for name in ("base", "overrides", "effective", "model_facts"):
            object.__setattr__(self, name, _freeze(getattr(self, name)))
        object.__setattr__(self, "editable", frozenset(self.editable))

    def with_remote_reference_limit(self, remote_limit: int | None) -> InputLimit[int]:
        if remote_limit is None or not self.reference_images.accepts_input:
            return self.reference_images
        if self.reference_images.state == LimitState.UNRECORDED or self.reference_images.maximum is None:
            return InputLimit.at_most(remote_limit)
        return InputLimit.at_most(min(self.reference_images.maximum, remote_limit))

    def audio(self, route: Route, *, requested: bool, base: bool = False) -> AudioDecision:
        protocol = self.reference_protocol if route == "r2v" else self.primary_protocol
        if route == "i2v" and not base and self.legacy_primary_always_on:
            protocol = AudioProtocol.NO_SWITCH
        values = self.base if base else self.effective
        has_audio = values.get("has_audio_track", True)
        if route == "r2v":
            has_audio = values.get("reference_route_has_audio_track", has_audio)
        switch = requested if protocol == AudioProtocol.SWITCH else None
        output = protocol != AudioProtocol.SILENT and has_audio and switch is not False
        return AudioDecision(requested, switch, output, self.always_bill_audio or output)


def video_bucket_unmet(first_frame: bool, reference_images: InputLimit[int]) -> dict[str, tuple[str, ...]]:
    result = {}
    if not first_frame:
        result["i2v"] = ("first_frame",)
    if not reference_images.accepts_input:
        result["r2v"] = ("max_reference_images",)
    return result


def bucket_fit(media: Media, snapshot: CapabilitySnapshot | None) -> dict[str, tuple[str, ...]]:
    """返回未满足的桶及维度。空元组表示无法判定。"""
    dimensions = {
        "image": {"t2i": "text_to_image", "i2i": "image_to_image"},
        "video": {"i2v": "first_frame", "r2v": "max_reference_images"},
    }.get(media, {})
    if snapshot is None:
        return dict.fromkeys(dimensions, ())
    if media == "video":
        return video_bucket_unmet(snapshot.effective["first_frame"], snapshot.reference_images)
    result = {}
    for bucket, key in dimensions.items():
        if bucket in ("r2v", "i2i"):
            if not snapshot.reference_images.accepts_input:
                missing_slot = bucket == "i2i" and snapshot.reference_images.state == "unsupported"
                result[bucket] = ("image_to_image" if missing_slot else "max_reference_images",)
        elif not snapshot.effective[key]:
            result[bucket] = (key,)
    return result


def resolve(
    endpoint: Endpoint,
    *,
    facts: Values | None = None,
    overrides: object = None,
    request: RequestConditions | None = None,
    official: bool = False,
    default_resolution: str | None = None,
    provider_id: str = "",
    model_id: str = "",
    always_bill_audio: bool = False,
    legacy_primary_always_on: bool = False,
) -> CapabilitySnapshot:
    allowed_facts = OVERRIDE_KEYS[endpoint.media]
    if endpoint.media == "image":
        allowed_facts |= {"qualities", "resolutions"}
    elif endpoint.media == "video":
        allowed_facts |= {"reference_audio"}
    facts = {k: v for k, v in (facts or {}).items() if k in allowed_facts}
    if endpoint.binding_owned:
        facts = {}
    applied = {} if official else endpoint.filter_overrides(overrides, facts)
    slots = endpoint.slots if request else endpoint.slots - endpoint.context_free_disabled_slots
    primary, reference = endpoint.audio, endpoint.protocol("r2v")
    if request:
        if any(group <= request.inputs for group in endpoint.exclusive_inputs):
            raise IncompatibleInputsError("incompatible input combination")
        for path in endpoint.paths:
            if path.matches(request):
                slots = slots - path.disabled_slots
                if path.audio is not None and request.bucket == "r2v":
                    reference = _restrict_audio(reference, path.audio)
                elif path.audio is not None:
                    primary = _restrict_audio(primary, path.audio)
    base = _compose(endpoint, facts, {}, slots)
    if request is None:
        slots |= endpoint.context_free_disabled_slots.intersection(applied)
    values = _compose(endpoint, facts, applied, slots)
    legacy_primary_always_on = (
        legacy_primary_always_on and endpoint.audio == AudioProtocol.SWITCH and applied.get("has_audio_track") is True
    )
    for projection in (base, values):
        if endpoint.media == "video":
            primary_has_audio = projection.get("has_audio_track", True)
            reference_has_audio = projection.get("reference_route_has_audio_track", primary_has_audio)
            primary_available = primary != AudioProtocol.SILENT or (projection is values and legacy_primary_always_on)
            projection["has_audio_track"] = primary_available and primary_has_audio
            projection["reference_route_has_audio_track"] = reference != AudioProtocol.SILENT and reference_has_audio
            if projection.get("max_reference_audio_count") == 0:
                projection["reference_audio_mode"] = "none"
        elif endpoint.media == "image":
            if not projection["image_to_image"]:
                projection["max_reference_images"] = 0
            projection["resolutions"] = facts.get("resolutions", ())
            projection["qualities"] = facts.get("qualities", ()) if "explicit_quality" in slots else ()
    supports_images = "reference_images" in slots and values.get("image_to_image", True)
    limit = InputLimit.from_json(values.get("max_reference_images")) if supports_images else InputLimit.unsupported()
    if endpoint.media == "image":
        for projection in (base, values):
            projection["image_to_image"] = projection["image_to_image"] and projection["max_reference_images"] != 0
    return CapabilitySnapshot(
        endpoint=endpoint,
        base=base,
        overrides=applied,
        effective=values,
        editable=frozenset() if official else endpoint.editable,
        reference_images=limit,
        primary_protocol=primary,
        reference_protocol=reference,
        default_resolution=default_resolution,
        model_facts=facts,
        official=official,
        provider_id=provider_id,
        model_id=model_id,
        always_bill_audio=always_bill_audio,
        legacy_primary_always_on=legacy_primary_always_on,
    )
