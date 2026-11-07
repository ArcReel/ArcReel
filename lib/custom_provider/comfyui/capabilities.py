"""Derive ComfyUI capabilities/parameter constraints from node bindings.

Definitions lack ``capabilities`` (``docs/adr/0082``): workflows themselves are models,
with bindings providing complete model facts. Duplicate declarations would conflict. Derive two shared UI/execution conclusions:

* **Capability bits**: first/last/reference-max/text-only video map directly to three image bindings; audio comes from
  connected ``audio`` inputs on artifact chains. Four bits are pure functions independent of model names, which cannot change
  workflow abilities. Only interpret bindings here; boxing into :class:`VideoCapabilities` /
  :class:`ImageCapability` belongs to ``lib/custom_provider/endpoints.py`` because forbidden runtime dependencies
  (``pyproject.toml``) prevent this package reaching those indirectly connected backend types.
* **Parameter constraints**: driving size/frame dimensions requires corresponding bindings. Disable/clarify unsupported
  UI selectors rather than accepting user choices then silently discarding them.

Read definitions only, without DB/network or definition writes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from arcreel_market_core.comfyui.bindings import (
    align_frames,
    bound_fps,
    int_literal_of,
    positive_number,
    step_of,
    targets_of,
)
from arcreel_market_core.comfyui.graph import class_type_of, link_of
from arcreel_market_core.comfyui.workflow import is_link, node_inputs

from .inference_rules import AudioTrackSource, load_inference_rules

#: Three image-input semantic keys; all empty means workflows accept no image assets.
_IMAGE_BINDING_KEYS = ("start_image", "end_image", "reference_images")

#: Audio conclusions exactly match :class:`arcreel_market_core.video_backend_contract.VideoAudioMode` values.
#: Never ``controllable``: ComfyUI has no request audio switches.
AudioTrack = Literal["always_on", "always_off"]


@dataclass(frozen=True)
class BindingCapabilities:
    """Complete binding-derived workflow capabilities, named after ``VideoCapabilities`` fields.

    ``last_frame`` means support, not requirement: workflows still produce without supplied last frames,
    whose loaders construction removes using reference-image graph-edit rules.

    Exclude three audio-reference dimensions/``max_prompt_chars``: bindings lack keys, making declarations invented;
    projections retain ``VideoCapabilities`` defaults.
    """

    text_to_video: bool
    first_frame: bool
    last_frame: bool
    max_reference_images: int
    audio_track: AudioTrack


def derive_video_capabilities(definition: Mapping[str, Any]) -> BindingCapabilities:
    """Pure binding-to-video-capability derivation without model names/DB."""
    bindings: Mapping[str, Any] = definition["bindings"]
    workflow: Mapping[str, Any] = definition["workflow"]
    has_image_input = any(targets_of(bindings.get(key)) for key in _IMAGE_BINDING_KEYS)
    return BindingCapabilities(
        text_to_video=not has_image_input,
        first_frame=bool(targets_of(bindings.get("start_image"))),
        last_frame=bool(targets_of(bindings.get("end_image"))),
        max_reference_images=len(targets_of(bindings.get("reference_images"))),
        audio_track=derive_audio_track(workflow, bindings),
    )


def derive_audio_track(workflow: Mapping[str, Any], bindings: Mapping[str, Any]) -> AudioTrack:
    """Audio form: connected artifact-chain ``audio`` means ``always_on``, otherwise ``always_off``.

    Never ``controllable``: authors wire audio in workflows, not request parameters;
    no ArcReel switch exists, so claiming control silently loses user intent.

    ``audio`` lives on output nodes (``VHS_VideoCombine``) or one upstream video node
    (``SaveVideo`` ← ``CreateVideo``), both cataloged in ``inference_rules.video.json``
    ``audio_track_sources``. Unknown shapes use ``always_off``: silence exposes an empty audio control
    for user verification; always-on would guarantee unreadable graphs.
    """
    targets = targets_of(bindings.get("output"))
    if not targets:
        return "always_off"
    node = workflow.get(str(targets[0]["node"]))
    if not isinstance(node, Mapping):
        return "always_off"
    for source in load_inference_rules("video").audio_track_sources:
        if class_type_of(node) != source.class_type:
            continue
        carrier = _audio_carrier(workflow, node, source)
        if carrier is None:
            return "always_off"
        return "always_on" if is_link(node_inputs(carrier).get(source.audio_input)) else "always_off"
    return "always_off"


def _audio_carrier(
    workflow: Mapping[str, Any], node: Mapping[str, Any], source: AudioTrackSource
) -> Mapping[str, Any] | None:
    """Nodes with ``audio``: output nodes or video carriers one hop upstream through ``through``.

    Upstream types must match ``through_class_types``: other nodes at ``SaveVideo.video`` do not match
    cataloged output shapes, leaving audio unknown.
    """
    if source.through_input is None:
        return node
    link = link_of(node_inputs(node).get(source.through_input))
    if link is None:
        return None
    upstream = workflow.get(link[0])
    if not isinstance(upstream, Mapping) or class_type_of(upstream) not in source.through_class_types:
        return None
    return upstream


def takes_reference_images(definition: Mapping[str, Any]) -> bool:
    """Whether workflows have reference slots, solely determining image capabilities.

    None means T2I only, present means I2I only, mutually exclusive (``docs/adr/0082``): withholding images from slotted
    workflows either deletes loaders or submits empty images, never T2I support. Strict buckets without fallback
    (``docs/adr/0015``) declare only actually executable routes.
    """
    return bool(targets_of(definition["bindings"].get("reference_images")))


# Parameter constraints.


def size_is_fixed(bindings: Mapping[str, Any]) -> bool:
    """Whether ArcReel cannot drive size because both dimensions are not bound.

    Require both, not either: one-sided binding writes only that dimension, leaving fixed counterparts
    and producing neither native nor selected ratios. Worse than no bindings because users believe chosen ratios applied.
    Any missing side makes whole size fixed, with disabled/explained resolution selectors.
    """
    return not (targets_of(bindings.get("width")) and targets_of(bindings.get("height")))


def native_short_edge(definition: Mapping[str, Any]) -> int | None:
    """Smaller literal workflow dimensions, native short edges; ``None`` when indeterminate.

    UI resolution placeholders can say workflow-native 480p, showing unset-tier outcomes rather than ambiguous defaults.
    Every input on each side must agree on literals: multiple bindings with 1024×576 versus 512×512
    consumers have no single native tier; choosing smaller endorses one route. Any linked/nonliteral input
    also prevents reporting, because writes still affect it and round-trip consistency is unknown.

    Fixed sizes likewise lack values: ``_write_size`` returns before literal-short-edge selection,
    so reported tiers would correspond to no behavior. ``size_fixed`` supplies placeholders instead.

    Construction without selected tiers still uses minimum literals through ``request_builder._literal_short_edge``:
    it must write dimensions, and graph-specific minima beat global fallback constants. UI instead determines
    whether native is a truthful label, requiring stricter predicates.
    """
    workflow: Mapping[str, Any] = definition["workflow"]
    bindings: Mapping[str, Any] = definition["bindings"]
    if size_is_fixed(bindings):
        return None
    edges: list[int] = []
    for key in ("width", "height"):
        targets = targets_of(bindings.get(key))
        literals = [
            value for target in targets if (value := int_literal_of(workflow, target)) is not None and value > 0
        ]
        if len(literals) != len(targets) or len(set(literals)) != 1:
            return None
        edges.append(literals[0])
    return min(edges)


def duration_is_fixed(bindings: Mapping[str, Any]) -> bool:
    """ArcReel cannot drive duration: unbound ``frames`` leaves workflow literal frame counts only."""
    return not targets_of(bindings.get("frames"))


def frame_rate_is_missing(definition: Mapping[str, Any]) -> bool:
    """Bound ``frames`` without fps sources: no readonly ``fps`` binding, and some frame inputs lack manual fps.

    Mutually exclusive with :func:`duration_is_fixed`, where ``frames`` is unbound, and readable-fps cases
    lacking exactly round-trippable integer-second tiers. All yield empty :func:`default_supported_durations`,
    but only this branch lacks fps itself, letting UI identify missing sources rather than claim inherently fixed durations.
    """
    bindings: Mapping[str, Any] = definition["bindings"]
    workflow: Mapping[str, Any] = definition["workflow"]
    targets = targets_of(bindings.get("frames"))
    if not targets or bound_fps(workflow, bindings) is not None:
        return False
    return any(positive_number(target.get("fps")) is None for target in targets)


def native_duration(definition: Mapping[str, Any]) -> int | None:
    """Workflow-native seconds, rounded.

    ``round((字面 frames − 1) / fps)`` inverts construction ``frames = round(时长 × 帧率) + 1``:
    ComfyUI counts first frames plus intervals. Share conversions or selecting defaults
    immediately changes frame counts.

    Either missing source means ``None``: read literals only through ``frames`` bindings, otherwise no pointer identifies
    frame inputs. Read fps from readonly ``fps`` bindings, falling back to manual ``frames`` entry
    constants without them. Never guess: guessed defaults producing different lengths are worse
    than explicit not-driven-by-ArcReel controls.

    Return ``None`` when selection would alter workflows: 81 frames @ 24fps becomes 3s, resubmitted as
    73 frames; disagreeing multiple bindings likewise change other inputs. Native labels must preserve original graphs,
    requiring every binding to round-trip its own literal with fps/steps through shared
    :func:`~.bindings.align_frames`. Any linked/invalid frame literal prevents reporting because construction still writes it,
    with unknowable round-trip outcomes.
    """
    bindings: Mapping[str, Any] = definition["bindings"]
    workflow: Mapping[str, Any] = definition["workflow"]
    frames_targets = targets_of(bindings.get("frames"))
    literals = [
        (target, value)
        for target in frames_targets
        if (value := int_literal_of(workflow, target)) is not None and value > 1
    ]
    if not literals or len(literals) != len(frames_targets):
        return None
    bound = bound_fps(workflow, bindings)

    def fps_of(target: Mapping[str, Any]) -> float | None:
        return bound if bound is not None else positive_number(target.get("fps"))

    first_fps = fps_of(literals[0][0])
    if first_fps is None:
        return None
    seconds = round((literals[0][1] - 1) / first_fps)
    if seconds <= 0:
        return None
    for target, value in literals:
        fps = fps_of(target)
        if fps is None or align_frames(round(seconds * fps) + 1, step_of(target)) != value:
            return None
    return seconds


def keeps_its_own_frame_count(definition: Mapping[str, Any]) -> bool:
    """Every frame input has its own literal >1, but cannot form one native duration.

    Such workflows declare undriven duration: empty ``endpoint_durations``, disabled UI,
    planning borrows default seconds. Construction must not rewrite with borrowed values: 81 frames @ 24fps
    becomes 97 at 4s, contradicting undriven claims. Both layers must agree.

    Placeholder ``length`` =1 has no native length to preserve, so requests drive frames normally.
    """
    bindings: Mapping[str, Any] = definition["bindings"]
    workflow: Mapping[str, Any] = definition["workflow"]
    targets = targets_of(bindings.get("frames"))
    if not targets:
        return False
    literals = [value for target in targets if (value := int_literal_of(workflow, target)) is not None and value > 1]
    return len(literals) == len(targets) and native_duration(definition) is None


def default_supported_durations(definition: Mapping[str, Any]) -> list[int]:
    """Default ComfyUI-video ``supported_durations`` includes workflow-native duration only.

    Never use ``duration_presets.infer_supported_durations`` name heuristics: arbitrary user model names
    such as ``my-wan-workflow`` match ``[4, 8]`` without relating to workflow lengths.

    Uninferable native durations yield empty sets, including unbound ``frames`` without readable literals and missing fps.
    Empty is valid here (``lib/config/resolver.py`` ``kind: comfyui`` branches):
    ArcReel does not drive duration; UI disables controls, submissions omit duration,
    leaving workflows their own lengths.
    """
    native = native_duration(definition)
    return [] if native is None or native <= 0 else [native]
