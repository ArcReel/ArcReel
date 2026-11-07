"""Binding-inference rule tables: load/shape bundled data.

Input aliases/native-node allowlists/steps/optional inputs/merge nodes/external conventions/output priorities/audio
are ComfyUI ecosystem knowledge, not ArcReel logic. They drift with node packages,
so live in ``inference_rules.<media_type>.json``, changing JSON without Python edits.

Separate media tables: images lack first/last frames/timelines and differ in output priorities; combined rules
would require video-only flags everywhere. Node-type facts (constants/optional inputs/merge nodes/external
families/seed gates) do not depend on image/video outputs and remain byte-identical across tables,
protected by tests.

Validate at load against ``inference_rules.schema.json`` and media-specific semantic-key vocabularies:
bundled invalid data must fail startup, never silently disable inference for particular keys.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from arcreel_market_core.comfyui.bindings import BINDING_KEYS_BY_MEDIA_TYPE

RULES_SCHEMA_PATH = Path(__file__).parent / "inference_rules.schema.json"

#: Media → rule files.
RULES_PATHS: Mapping[str, Path] = {
    "image": Path(__file__).parent / "inference_rules.image.json",
    "video": Path(__file__).parent / "inference_rules.video.json",
}


@dataclass(frozen=True)
class SemanticKeyRules:
    """Field-level clues identifying semantic keys."""

    #: Semantic-key input aliases.
    input_names: tuple[str, ...]
    #: Native carrier-node allowlists.
    class_types: frozenset[str]
    #: Conditional rules: input names count for this key only on these nodes.
    input_name_class_types: Mapping[str, frozenset[str]]
    #: Secondary title clues, case-insensitive substring matching.
    title_keywords: tuple[str, ...]

    def accepts(self, input_name: str, class_type: str) -> bool:
        """Whether ``(input 名, 承载节点)`` combinations provide clues for this key."""
        if input_name not in self.input_names:
            return False
        limited = self.input_name_class_types.get(input_name)
        return limited is None or class_type in limited

    def title_hit(self, title: str) -> bool:
        lowered = title.lower()
        return any(keyword in lowered for keyword in self.title_keywords)


@dataclass(frozen=True)
class NodeInput:
    """One node input."""

    class_type: str
    input: str


@dataclass(frozen=True)
class InputName:
    """Exact rule input names, or prefix families such as ComfyUI autogrow slots."""

    text: str
    #: When ``True``, ``text`` is a prefix; ``ref_images.ref_image_`` recognizes every slot from ``ref_images.ref_image_0`` onward.
    prefix: bool = False

    def matches(self, input_name: str) -> bool:
        return input_name.startswith(self.text) if self.prefix else input_name == self.text


@dataclass(frozen=True)
class OptionalInput:
    """Optional inputs: remove keys after upstream image-loader deletion, retaining executable nodes."""

    class_type: str
    input: InputName


@dataclass(frozen=True)
class ConsumerPort:
    """Image-loader outputs entering this port carry its corresponding semantic key."""

    input: InputName
    #: Restrict input names to nodes, unrestricted for empty sets.
    class_types: frozenset[str]

    def matches(self, input_name: str, class_type: str) -> bool:
        if not self.input.matches(input_name):
            return False
        return not self.class_types or class_type in self.class_types


@dataclass(frozen=True)
class OutputCandidate:
    """Artifact-node candidates."""

    class_type: str
    #: False input values make nodes preview-only without persistence.
    requires_true: str | None


@dataclass(frozen=True)
class StepRules:
    """Step prefills per semantic key."""

    default: int
    by_class_type: Mapping[str, int]

    def of(self, class_type: str) -> int:
        return self.by_class_type.get(class_type, self.default)


@dataclass(frozen=True)
class MergeNode:
    """Pairwise merge nodes: bypass this stage when images are missing."""

    class_type: str
    inputs: tuple[str, ...]


@dataclass(frozen=True)
class ExternalFamily:
    """External parameterized node conventions, prefix recognition only, without full compatibility."""

    name: str
    prefix: str
    #: Inputs carrying parameter names; ``None`` for names in titles.
    parameter_input: str | None
    #: Whether parameter names come from ``_meta.title``.
    parameter_from_title: bool
    #: Fields carrying parameter names themselves, never bindable targets.
    ignored_inputs: frozenset[str]

    def parameter_of(self, inputs: Mapping[str, Any], title: str) -> str | None:
        """Read node-declared parameter names."""
        if self.parameter_from_title:
            return title.strip() or None
        raw = inputs.get(self.parameter_input) if self.parameter_input else None
        return raw.strip() or None if isinstance(raw, str) else None


@dataclass(frozen=True)
class AudioTrackSource:
    """Audio detection: connected inputs mean audible outputs."""

    class_type: str
    audio_input: str
    #: If audio inputs live outside output nodes, follow this input upstream to completed-video nodes.
    through_input: str | None
    through_class_types: frozenset[str]


@dataclass(frozen=True)
class SeedGate:
    """Seed gates: samplers ignore seeds unless gate fields equal ``enabled_value``."""

    class_type: str
    input: str
    enabled_value: str


@dataclass(frozen=True)
class ManualOnlyNode:
    """Known uninferable shapes requiring manual bindings."""

    class_type: str
    binding_keys: tuple[str, ...]
    #: Inputs accepting images other than references.
    blocked_inputs: frozenset[str]


@dataclass(frozen=True)
class InferenceRules:
    """All rules for one media type."""

    media_type: str
    semantic_keys: Mapping[str, SemanticKeyRules]
    image_loaders: tuple[NodeInput, ...]
    consumer_ports: Mapping[str, tuple[ConsumerPort, ...]]
    sampler_ports: Mapping[str, str]
    conditioning_slots: Mapping[int, str]
    conditioning_pass_through: frozenset[str]
    output_candidates: tuple[OutputCandidate, ...]
    excluded_output_class_types: frozenset[str]
    class_type_tiers: Mapping[str, tuple[frozenset[str], ...]]
    steps: Mapping[str, StepRules]
    constant_nodes: Mapping[str, str]
    optional_inputs: tuple[OptionalInput, ...]
    merge_nodes: tuple[MergeNode, ...]
    external_families: tuple[ExternalFamily, ...]
    audio_track_sources: tuple[AudioTrackSource, ...]
    seed_gates: tuple[SeedGate, ...]
    batch_size_inputs: tuple[NodeInput, ...]
    manual_only_class_types: tuple[ManualOnlyNode, ...]

    def port_of(self, binding_key: str) -> str | None:
        """Sampler conditioning-port names for semantic keys."""
        for port, key in self.sampler_ports.items():
            if key == binding_key:
                return port
        return None

    def tier_of(self, binding_key: str, class_type: str) -> int:
        """Carrier-node priority tiers for semantic keys; earlier scores higher, outside tiers zero."""
        tiers = self.class_type_tiers.get(binding_key, ())
        for index, tier in enumerate(tiers):
            if class_type in tier:
                return len(tiers) - index
        return 0

    def step_of(self, binding_key: str, class_type: str) -> int | None:
        rules = self.steps.get(binding_key)
        return None if rules is None else rules.of(class_type)

    def family_of(self, class_type: str) -> ExternalFamily | None:
        for family in self.external_families:
            if class_type.startswith(family.prefix):
                return family
        return None

    def loader_input(self, class_type: str) -> str | None:
        """Whether nodes load images; if so, return filename inputs."""
        for loader in self.image_loaders:
            if loader.class_type == class_type:
                return loader.input
        return None

    def is_optional_input(self, class_type: str, input_name: str) -> bool:
        return any(item.class_type == class_type and item.input.matches(input_name) for item in self.optional_inputs)

    def merge_node(self, class_type: str) -> MergeNode | None:
        for node in self.merge_nodes:
            if node.class_type == class_type:
                return node
        return None

    def adjustable_input(self, class_type: str, input_name: str) -> bool:
        """Whether missing-image graphs can adjust inputs: optional keys remove, pairwise merges bypass, others unknown.

        Inference/execution share predicates: inference warns slots cannot adjust when counts shrink; construction decides graph edits
        versus repeating last images. Separate implementations would diverge warnings/actual handling.
        """
        if self.is_optional_input(class_type, input_name):
            return True
        merge = self.merge_node(class_type)
        return merge is not None and input_name in merge.inputs

    def seed_gate(self, class_type: str) -> SeedGate | None:
        for gate in self.seed_gates:
            if gate.class_type == class_type:
                return gate
        return None

    def blocks_image(self, class_type: str, input_name: str) -> bool:
        """Inputs accepting nonreference images such as control video/masks; their upstream loaders cannot be image candidates."""
        return any(
            node.class_type == class_type and input_name in node.blocked_inputs for node in self.manual_only_class_types
        )

    def output_rank(self, class_type: str) -> int:
        """Artifact candidate priorities: earlier scores higher, unlisted zero."""
        for index, candidate in enumerate(self.output_candidates):
            if candidate.class_type == class_type:
                return len(self.output_candidates) - index
        return 0

    def output_candidate(self, class_type: str) -> OutputCandidate | None:
        for candidate in self.output_candidates:
            if candidate.class_type == class_type:
                return candidate
        return None


@cache
def load_rules_schema() -> dict[str, Any]:
    """Load/cache public rule schemas, shared by tests/documentation sites."""
    return json.loads(RULES_SCHEMA_PATH.read_text(encoding="utf-8"))


@cache
def load_inference_rules(media_type: str) -> InferenceRules:
    """Load media rule tables; unregistered media are caller errors raising ``KeyError``."""
    document = json.loads(RULES_PATHS[media_type].read_text(encoding="utf-8"))
    _validator().validate(document)
    unknown = sorted(semantic_key_names(document) - set(BINDING_KEYS_BY_MEDIA_TYPE[media_type]))
    if unknown:
        raise ValueError(f"inference_rules.{media_type}.json 提到 {media_type} 端点没有的语义键：{', '.join(unknown)}")
    return _rules_from(document)


def semantic_key_names(document: Mapping[str, Any]) -> frozenset[str]:
    """All semantic keys mentioned by tables, in key or value positions.

    Shared schemas cannot enforce media-specific vocabularies: misspelled ``reference_images`` as ``reference_image``,
    or ``frames`` in image tables, still validates structurally but leaves rules unread and semantic inference silently absent.
    """
    keyed = {
        key
        for section in ("semantic_keys", "consumer_ports", "class_type_tiers", "steps")
        for key in _mapping(document.get(section))
    }
    valued = {
        str(key)
        for section in ("sampler_ports", "conditioning_slots")
        for key in _mapping(document.get(section)).values()
    }
    listed = {
        key for item in _items(document.get("manual_only_class_types")) for key in _strings(item.get("binding_keys"))
    }
    return frozenset(keyed | valued | listed)


@cache
def _validator() -> Draft202012Validator:
    schema = load_rules_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _rules_from(document: Mapping[str, Any]) -> InferenceRules:
    return InferenceRules(
        media_type=str(document["media_type"]),
        semantic_keys={
            key: SemanticKeyRules(
                input_names=_strings(rules.get("input_names")),
                class_types=frozenset(_strings(rules.get("class_types"))),
                input_name_class_types={
                    name: frozenset(_strings(values))
                    for name, values in _mapping(rules.get("input_name_class_types")).items()
                },
                title_keywords=_strings(rules.get("title_keywords")),
            )
            for key, rules in _mapping(document.get("semantic_keys")).items()
        },
        image_loaders=_node_inputs(document.get("image_loaders")),
        consumer_ports={
            key: tuple(ConsumerPort(_input_name(port), frozenset(_strings(port.get("class_types")))) for port in ports)
            for key, ports in _mapping(document.get("consumer_ports")).items()
        },
        sampler_ports={str(port): str(key) for port, key in _mapping(document.get("sampler_ports")).items()},
        conditioning_slots={int(slot): str(key) for slot, key in _mapping(document.get("conditioning_slots")).items()},
        conditioning_pass_through=frozenset(_strings(document.get("conditioning_pass_through"))),
        output_candidates=tuple(
            OutputCandidate(str(item["class_type"]), _optional_str(item.get("requires_true")))
            for item in _items(document.get("output_candidates"))
        ),
        excluded_output_class_types=frozenset(_strings(document.get("excluded_output_class_types"))),
        class_type_tiers={
            key: tuple(frozenset(_strings(tier)) for tier in tiers)
            for key, tiers in _mapping(document.get("class_type_tiers")).items()
        },
        steps={
            key: StepRules(
                default=int(rules["default"]),
                by_class_type={name: int(step) for name, step in _mapping(rules.get("by_class_type")).items()},
            )
            for key, rules in _mapping(document.get("steps")).items()
        },
        constant_nodes={str(name): str(field) for name, field in _mapping(document.get("constant_nodes")).items()},
        optional_inputs=tuple(
            OptionalInput(str(item["class_type"]), _input_name(item))
            for item in _items(document.get("optional_inputs"))
        ),
        merge_nodes=tuple(
            MergeNode(str(item["class_type"]), _strings(item.get("inputs")))
            for item in _items(document.get("merge_nodes"))
        ),
        external_families=tuple(_family_from(item) for item in _items(document.get("external_families"))),
        audio_track_sources=tuple(_audio_from(item) for item in _items(document.get("audio_track_sources"))),
        seed_gates=tuple(
            SeedGate(str(item["class_type"]), str(item["input"]), str(item["enabled_value"]))
            for item in _items(document.get("seed_gates"))
        ),
        batch_size_inputs=_node_inputs(document.get("batch_size_inputs")),
        manual_only_class_types=tuple(
            ManualOnlyNode(
                str(item["class_type"]),
                _strings(item.get("binding_keys")),
                frozenset(_strings(item.get("blocked_inputs"))),
            )
            for item in _items(document.get("manual_only_class_types"))
        ),
    )


def _family_from(item: Mapping[str, Any]) -> ExternalFamily:
    source = _mapping(item.get("parameter_from"))
    return ExternalFamily(
        name=str(item["name"]),
        prefix=str(item["prefix"]),
        parameter_input=_optional_str(source.get("input")),
        parameter_from_title=bool(source.get("title", False)),
        ignored_inputs=frozenset(_strings(item.get("ignored_inputs"))),
    )


def _audio_from(item: Mapping[str, Any]) -> AudioTrackSource:
    through = _mapping(item.get("through"))
    return AudioTrackSource(
        class_type=str(item["class_type"]),
        audio_input=str(item["audio_input"]),
        through_input=_optional_str(through.get("input")),
        through_class_types=frozenset(_strings(through.get("class_types"))),
    )


def _node_inputs(raw: object) -> tuple[NodeInput, ...]:
    return tuple(NodeInput(str(item["class_type"]), str(item["input"])) for item in _items(raw))


def _input_name(item: Mapping[str, Any]) -> InputName:
    """Exactly one ``input`` / ``input_prefix``, guaranteed by load-time schemas."""
    if "input_prefix" in item:
        return InputName(str(item["input_prefix"]), prefix=True)
    return InputName(str(item["input"]))


def _items(raw: object) -> Iterable[Any]:
    """Check outer arrays only; schemas guarantee element shapes at load."""
    return raw if isinstance(raw, list) else ()


def _mapping(raw: object) -> Mapping[str, Any]:
    return raw if isinstance(raw, Mapping) else {}


def _strings(raw: object) -> tuple[str, ...]:
    return tuple(str(item) for item in raw) if isinstance(raw, list) else ()


def _optional_str(raw: object) -> str | None:
    return str(raw) if isinstance(raw, str) else None
