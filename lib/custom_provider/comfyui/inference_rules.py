"""节点绑定推断的规则表：随包数据文件的读入与形状化。

推断要认的东西——input 名别名、原生节点白名单、步长、可选入口、合并节点、外部约定节点族、
产物候选顺序、音轨判定——全是 ComfyUI 生态的节点知识，不是 ArcReel 的逻辑。它们随节点包的
迭代长期漂移，因此放在 ``inference_rules.<media_type>.json`` 里，改一行 JSON 不动一行 Python。

两种媒体类型各一份：图像端点没有首尾帧与时间轴，产物候选顺序也不同，合成一份就得在每条规则上
再挂一个「这条只对视频有效」的开关。共用的几节（常量节点、合并节点、外部节点族、种子闸门）在
两份里逐字相同，由测试守住不漂移。

文件在读入时即过 ``inference_rules.schema.json``：它是随包发布的数据，写坏了该在启动时炸，而
不是变成某个语义键悄悄推断不出来。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

RULES_SCHEMA_PATH = Path(__file__).parent / "inference_rules.schema.json"

#: 媒体类型 → 该媒体类型的规则文件。
RULES_PATHS: Mapping[str, Path] = {
    "image": Path(__file__).parent / "inference_rules.image.json",
    "video": Path(__file__).parent / "inference_rules.video.json",
}


@dataclass(frozen=True)
class SemanticKeyRules:
    """认出一个语义键的字段级线索。"""

    #: 该语义键的 input 名别名。
    input_names: tuple[str, ...]
    #: 承载该语义键的原生节点白名单。
    class_types: frozenset[str]
    #: 条件规则：某个 input 名只在这些节点上算本键。
    input_name_class_types: Mapping[str, frozenset[str]]
    #: 标题里的次级线索，大小写不敏感子串匹配。
    title_keywords: tuple[str, ...]

    def accepts(self, input_name: str, class_type: str) -> bool:
        """这个 ``(input 名, 承载节点)`` 组合算不算本键的线索。"""
        if input_name not in self.input_names:
            return False
        limited = self.input_name_class_types.get(input_name)
        return limited is None or class_type in limited

    def title_hit(self, title: str) -> bool:
        lowered = title.lower()
        return any(keyword in lowered for keyword in self.title_keywords)


@dataclass(frozen=True)
class NodeInput:
    """一个节点上的一个入口。"""

    class_type: str
    input: str


@dataclass(frozen=True)
class ConsumerPort:
    """读图节点的输出接到这个入口时，它承载的就是对应的语义键。"""

    input: str
    #: 限定这个入口名只在这些节点上算数；空集即不限定。
    class_types: frozenset[str]

    def matches(self, input_name: str, class_type: str) -> bool:
        if input_name != self.input:
            return False
        return not self.class_types or class_type in self.class_types


@dataclass(frozen=True)
class OutputCandidate:
    """产物节点候选。"""

    class_type: str
    #: 该入口取 false 时此节点只预览不落盘。
    requires_true: str | None


@dataclass(frozen=True)
class StepRules:
    """某个语义键的步长预填。"""

    default: int
    by_class_type: Mapping[str, int]

    def of(self, class_type: str) -> int:
        return self.by_class_type.get(class_type, self.default)


@dataclass(frozen=True)
class MergeNode:
    """两两合并节点：少一张图时对该级做 bypass。"""

    class_type: str
    inputs: tuple[str, ...]


@dataclass(frozen=True)
class ExternalFamily:
    """外部参数化约定的节点族。只按前缀识别，不做完整兼容。"""

    name: str
    prefix: str
    #: 参数名写在哪个 input 上；写在标题上时为 ``None``。
    parameter_input: str | None
    #: 参数名是否取自 ``_meta.title``。
    parameter_from_title: bool
    #: 承载参数名本身、不作为可绑落点的字段。
    ignored_inputs: frozenset[str]

    def parameter_of(self, inputs: Mapping[str, Any], title: str) -> str | None:
        """读出这个节点声明的参数名。"""
        if self.parameter_from_title:
            return title.strip() or None
        raw = inputs.get(self.parameter_input) if self.parameter_input else None
        return raw.strip() or None if isinstance(raw, str) else None


@dataclass(frozen=True)
class AudioTrackSource:
    """音轨判定：这个入口有连线即成片带音轨。"""

    class_type: str
    audio_input: str
    #: 音频入口不在产物节点自身上时，先沿这个入口走到上游的成片节点。
    through_input: str | None
    through_class_types: frozenset[str]


@dataclass(frozen=True)
class SeedGate:
    """种子闸门：闸门字段不取 ``enabled_value`` 的采样器不真正吃种子。"""

    class_type: str
    input: str
    enabled_value: str


@dataclass(frozen=True)
class ManualOnlyNode:
    """已知推断不出、只能手动绑的节点形态。"""

    class_type: str
    binding_keys: tuple[str, ...]
    #: 这个节点上收图、但收的不是参考图的入口。
    blocked_inputs: frozenset[str]


@dataclass(frozen=True)
class InferenceRules:
    """一种媒体类型的全部推断规则。"""

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
    optional_inputs: tuple[NodeInput, ...]
    merge_nodes: tuple[MergeNode, ...]
    external_families: tuple[ExternalFamily, ...]
    audio_track_sources: tuple[AudioTrackSource, ...]
    seed_gates: tuple[SeedGate, ...]
    batch_size_inputs: tuple[NodeInput, ...]
    manual_only_class_types: tuple[ManualOnlyNode, ...]

    def port_of(self, binding_key: str) -> str | None:
        """语义键对应的采样器条件端口名。"""
        for port, key in self.sampler_ports.items():
            if key == binding_key:
                return port
        return None

    def tier_of(self, binding_key: str, class_type: str) -> int:
        """承载节点在该语义键的优先级分档里的位置：靠前的档位得分更高，档外为 0。"""
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
        """这个节点是读图节点吗；是则回它承载文件名的入口。"""
        for loader in self.image_loaders:
            if loader.class_type == class_type:
                return loader.input
        return None

    def is_optional_input(self, class_type: str, input_name: str) -> bool:
        return any(item.class_type == class_type and item.input == input_name for item in self.optional_inputs)

    def merge_node(self, class_type: str) -> MergeNode | None:
        for node in self.merge_nodes:
            if node.class_type == class_type:
                return node
        return None

    def seed_gate(self, class_type: str) -> SeedGate | None:
        for gate in self.seed_gates:
            if gate.class_type == class_type:
                return gate
        return None

    def blocks_image(self, class_type: str, input_name: str) -> bool:
        """这个入口收的图不是参考图：控制视频、掩码一类，流向它的读图节点不进图像类候选。"""
        return any(
            node.class_type == class_type and input_name in node.blocked_inputs for node in self.manual_only_class_types
        )

    def output_rank(self, class_type: str) -> int:
        """产物候选的优先级：靠前的候选分更高，不在候选表里为 0。"""
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
    """读入并缓存规则表的 schema。对外公开，供测试与文档站取同一份契约。"""
    return json.loads(RULES_SCHEMA_PATH.read_text(encoding="utf-8"))


@cache
def load_inference_rules(media_type: str) -> InferenceRules:
    """读入某种媒体类型的规则表。未登记的媒体类型是调用方的错，直接 ``KeyError``。"""
    document = json.loads(RULES_PATHS[media_type].read_text(encoding="utf-8"))
    _validator().validate(document)
    return _rules_from(document)


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
            key: tuple(ConsumerPort(str(port["input"]), frozenset(_strings(port.get("class_types")))) for port in ports)
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
        optional_inputs=_node_inputs(document.get("optional_inputs")),
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


def _items(raw: object) -> Iterable[Any]:
    """只认外层是不是数组；元素的形状由 schema 在读入时保证。"""
    return raw if isinstance(raw, list) else ()


def _mapping(raw: object) -> Mapping[str, Any]:
    return raw if isinstance(raw, Mapping) else {}


def _strings(raw: object) -> tuple[str, ...]:
    return tuple(str(item) for item in raw) if isinstance(raw, list) else ()


def _optional_str(raw: object) -> str | None:
    return str(raw) if isinstance(raw, str) else None
