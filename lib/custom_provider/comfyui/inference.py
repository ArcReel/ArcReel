"""节点绑定推断：从一份 workflow 里认出每个语义键的候选，并给出分数与命中信号。

ComfyUI 没有任何官方参数化约定，导出物每个节点只有 ``inputs`` / ``class_type`` / ``_meta.title``，
哪个节点承接提示词、哪个产出成片全靠外部推断。生态里两家参考实现都在每次提交时现推，任务因此
不可复现、歧义无处交给用户。ArcReel 把推断收在一处、只产出**候选**：分数与信号说明一并回给
前端，用户确认后显式落盘才算节点绑定（``docs/adr/0082``）。

**打分制**消掉了「按 input 名匹配」与「按 class_type 白名单匹配」的主键之争：两种线索都只是
信号，各自计一份分，同一个语义键上取最高分。信号自高到低分七级，每一级的权重都大于它以下全部
级别之和，因此高级信号一旦命中就压得住任何数量的低级信号；三个次级信号（承载节点档位、是否落
在产物链路上、标题里的正负子串）权重更小，只在同分时决出先后，压不过任何一级。

同一个语义键落多个节点是常态（base + refiner 两段的提示词、MoE 的双采样器），故候选的选中是
按分而不是按数量：唯一最高分即「自动识别」，并列不自动选，零候选标「未找到」。参考图是例外——
它的多个候选是有序的格子而不是互相竞争的答案，同分全选，列表顺序即参考图序号。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from lib.validation_messages import ValidationMessage

from .bindings import BINDING_KEYS_BY_MEDIA_TYPE, REQUIRED_BINDING_KEYS
from .graph import (
    Consumer,
    ancestors,
    class_type_of,
    consumers_by_node,
    dependency_depth,
    link_of,
    node_title,
    resolve_literal,
    unwrap_value,
)
from .inference_rules import ConsumerPort, InferenceRules, load_inference_rules
from .workflow import is_link, node_inputs

#: 推断消息的键前缀：信号说明与提示各有一条，与诊断码共用 ``val_ce_`` 命名空间。
MESSAGE_KEY_PREFIX = "val_ce_infer_"

#: 标题约定的前缀，大小写不敏感，截到首个空白。
TITLE_MARKER_PREFIX = "arcreel:"

#: 只读的语义键：ArcReel 从 workflow 读出它的字面值，不写回。
READ_ONLY_BINDING_KEYS = frozenset({"fps"})

#: 候选即有序格子、并列不算歧义的语义键。
LIST_BINDING_KEYS = frozenset({"reference_images"})

#: 值本身是一张图的语义键：它们的落点只能是读图节点承载文件名的那个字段。与
#: :data:`.bindings.IMAGE_BINDING_KEYS`（图像端点允许的语义键）不是一回事。
IMAGE_VALUED_KEYS = frozenset({"start_image", "end_image", "reference_images"})

_NUMERIC_PREFIX = re.compile(r"^(\d+)")


class BindingSignal(StrEnum):
    """一条候选上命中的信号。前七项是分级信号，其后三项是只决同分先后的次级信号。"""

    MANUAL_BINDING = "manual_binding"
    TITLE_MARKER = "title_marker"
    EXTERNAL_NODE_FAMILY = "external_node_family"
    SAMPLER_PORT_TRACE = "sampler_port_trace"
    ALIAS_WITH_CLASS_TYPE = "alias_with_class_type"
    ALIAS_ONLY = "alias_only"
    LINK_TRACE = "link_trace"
    CLASS_TYPE_TIER = "class_type_tier"
    OUTPUT_CHAIN = "output_chain"
    TITLE_POLARITY = "title_polarity"


#: 分级信号的权重。每一级都大于它以下全部级别之和（含次级信号的上限），高级信号因此不可能被
#: 一堆低级信号累加压过。
SIGNAL_WEIGHTS: Mapping[BindingSignal, int] = {
    BindingSignal.MANUAL_BINDING: 2048,
    BindingSignal.TITLE_MARKER: 1024,
    BindingSignal.EXTERNAL_NODE_FAMILY: 256,
    BindingSignal.SAMPLER_PORT_TRACE: 128,
    BindingSignal.ALIAS_WITH_CLASS_TYPE: 64,
    BindingSignal.ALIAS_ONLY: 32,
    BindingSignal.LINK_TRACE: 16,
    BindingSignal.OUTPUT_CHAIN: 4,
    BindingSignal.TITLE_POLARITY: 1,
}

#: 最弱的分级信号：次级信号的权重之和必须小于它，否则次级信号就不再「只决同分先后」。
WEAKEST_GRADED_WEIGHT = SIGNAL_WEIGHTS[BindingSignal.LINK_TRACE]


class InferenceNote(StrEnum):
    """推断给出的提示：不拦保存，但有它用户才知道某个形态会怎么表现。"""

    REFERENCE_CONSUMER_UNKNOWN = "reference_consumer_unknown"
    BATCH_SIZE_ABOVE_ONE = "batch_size_above_one"
    MANUAL_ONLY_NODE = "manual_only_node"
    COMPUTED_SOURCE = "computed_source"
    REMATCHED = "rematched"
    BINDING_LOST = "binding_lost"


class BindingState(StrEnum):
    """一个语义键的推断结果状态。"""

    #: 唯一最高分，已按它选好。
    AUTO_SELECTED = "auto_selected"
    #: 最高分并列，不自动选，全部候选按分排序交用户挑。
    AMBIGUOUS = "ambiguous"
    #: 一个候选也没有，允许用户从全部 ``(node, input)`` 手选。
    NOT_FOUND = "not_found"
    #: 定义里该键是空列表：用户确认过这份 workflow 不支持这项语义，不再推断。
    UNSUPPORTED = "unsupported"
    #: 既有条目有丢失的，该键重跑了推断，保存前必须让用户确认。
    NEEDS_CONFIRMATION = "needs_confirmation"


class MatchOrigin(StrEnum):
    """候选是怎么来的。"""

    INFERRED = "inferred"
    #: 既有条目的节点与类型都没变，原样沿用。
    KEPT = "kept"
    #: 节点 id 变了，按类型加标题在全图唯一匹配到新 id。
    REMATCHED = "rematched"


@dataclass(frozen=True)
class SignalHit:
    """一条命中的信号与它的分量。"""

    signal: BindingSignal
    weight: int
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> ValidationMessage:
        return ValidationMessage(f"{MESSAGE_KEY_PREFIX}{self.signal.value}", self.params)

    def to_payload(self, translate: Callable[..., str] | None = None) -> dict[str, Any]:
        return {
            "signal": self.signal.value,
            "weight": self.weight,
            "message": self.message.render(translate),
        }


@dataclass(frozen=True)
class Note:
    """一条提示。"""

    code: InferenceNote
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> ValidationMessage:
        return ValidationMessage(f"{MESSAGE_KEY_PREFIX}note_{self.code.value}", self.params)

    def to_payload(self, translate: Callable[..., str] | None = None) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message.render(translate)}


@dataclass(frozen=True)
class Candidate:
    """一条候选：一个可落盘的目标，加上它凭什么被选中。"""

    target: Mapping[str, Any]
    signals: tuple[SignalHit, ...]
    selected: bool
    origin: MatchOrigin
    #: 产物候选的上游链长度；其余语义键为 ``None``。多个产物时按它取最靠下游的一个。
    depth: int | None = None

    @property
    def score(self) -> int:
        return sum(hit.weight for hit in self.signals)

    @property
    def node(self) -> str:
        return str(self.target["node"])

    @property
    def input(self) -> str | None:
        raw = self.target.get("input")
        return str(raw) if isinstance(raw, str) else None

    def to_payload(self, translate: Callable[..., str] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "target": dict(self.target),
            "score": self.score,
            "signals": [hit.to_payload(translate) for hit in self.signals],
            "selected": self.selected,
            "origin": self.origin.value,
        }
        if self.depth is not None:
            payload["depth"] = self.depth
        return payload


@dataclass(frozen=True)
class KeyInference:
    """一个语义键的推断结果。"""

    state: BindingState
    candidates: tuple[Candidate, ...]
    notes: tuple[Note, ...] = ()

    @property
    def selected_targets(self) -> tuple[Mapping[str, Any], ...]:
        """可直接写进 ``bindings`` 的条目；状态不是「自动识别」时为空。"""
        if self.state is not BindingState.AUTO_SELECTED:
            return ()
        return tuple(candidate.target for candidate in self.candidates if candidate.selected)

    def to_payload(self, translate: Callable[..., str] | None = None) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "candidates": [candidate.to_payload(translate) for candidate in self.candidates],
            "notes": [note.to_payload(translate) for note in self.notes],
        }


@dataclass(frozen=True)
class BindingInference:
    """一份 workflow 的完整推断结果。"""

    media_type: str
    keys: Mapping[str, KeyInference]
    notes: tuple[Note, ...] = ()

    @property
    def savable(self) -> bool:
        """照这份结果直接落盘能不能过校验。

        两条：任何键处于歧义或待确认都不行——用户要么选一个，要么把它清空为显式不支持；提示词
        与产物必须有着落，这两项是校验器的硬闸门。
        """
        blocked = {BindingState.AMBIGUOUS, BindingState.NEEDS_CONFIRMATION}
        if any(result.state in blocked for result in self.keys.values()):
            return False
        return all(self.keys.get(key, _MISSING).selected_targets for key in REQUIRED_BINDING_KEYS)

    def to_payload(self, translate: Callable[..., str] | None = None) -> dict[str, Any]:
        return {
            "media_type": self.media_type,
            "savable": self.savable,
            "bindings": {key: result.to_payload(translate) for key, result in self.keys.items()},
            "notes": [note.to_payload(translate) for note in self.notes],
        }


#: 某个语义键根本没出现在结果里时的占位：它的 ``selected_targets`` 为空，与「没绑上」同义。
_MISSING = KeyInference(BindingState.NOT_FOUND, ())


def infer_bindings(definition: Mapping[str, Any]) -> BindingInference:
    """对一份 ComfyUI 端点定义跑推断；定义里已有的节点绑定按三态参与。

    键缺失即从未推断过，跑全套推断；空列表是用户确认过的「此 workflow 不支持」，不再推断；
    非空列表是已保存的节点绑定，走重匹配，逐条沿用或迁移，有丢失的条目才重跑该键的推断。
    """
    media_type = str(definition["media_type"])
    workflow: Mapping[str, Any] = definition["workflow"]
    existing: Mapping[str, Any] = definition.get("bindings") or {}
    engine = _Engine(workflow, load_inference_rules(media_type))
    results: dict[str, KeyInference] = {}
    for key in sorted(BINDING_KEYS_BY_MEDIA_TYPE[media_type]):
        saved = existing.get(key)
        if isinstance(saved, list) and not saved:
            results[key] = KeyInference(BindingState.UNSUPPORTED, ())
        elif isinstance(saved, list):
            results[key] = _rematch(key, saved, workflow, engine)
        else:
            results[key] = engine.infer_key(key)
    return BindingInference(media_type, results, engine.workflow_notes())


def _rematch(key: str, saved: Sequence[Any], workflow: Mapping[str, Any], engine: _Engine) -> KeyInference:
    """已保存的节点绑定对上新 workflow：逐条沿用、迁移或判丢。

    三级按可靠度递减：节点 id 与类型都没变即原样沿用；类型加标题在全图唯一时迁移到新 id 并标
    「已重匹配」；再不成该条目就丢了。丢了条目的语义键整键重跑推断并标「需确认」——重导入不静默
    保存，用户要看见哪些沿用、哪些重新识别。
    """
    kept: list[Candidate] = []
    migrated: list[str] = []
    lost: list[str] = []
    for entry in saved:
        if not isinstance(entry, Mapping):
            continue
        landing = _relocate(entry, workflow)
        if landing is None:
            lost.append(str(entry.get("node")))
            continue
        node_id, origin = landing
        node = workflow[node_id]
        target = {**entry, "node": node_id, "class_type": class_type_of(node), "title": node_title(node)}
        kept.append(
            Candidate(
                target,
                (SignalHit(BindingSignal.MANUAL_BINDING, _weight(BindingSignal.MANUAL_BINDING)),),
                selected=True,
                origin=origin,
            )
        )
        if origin is MatchOrigin.REMATCHED:
            migrated.append(node_id)
    notes: list[Note] = []
    if migrated:
        notes.append(Note(InferenceNote.REMATCHED, {"binding_key": key, "nodes": " / ".join(migrated)}))
    if not lost:
        return KeyInference(BindingState.AUTO_SELECTED, tuple(kept), tuple(notes))
    notes.append(Note(InferenceNote.BINDING_LOST, {"binding_key": key, "nodes": " / ".join(lost)}))
    fresh = engine.infer_key(key)
    landed = {(candidate.node, candidate.input) for candidate in kept}
    merged = [*kept, *(c for c in fresh.candidates if (c.node, c.input) not in landed)]
    # 有条目丢失时一个也不自动选：沿用下来的那几条同样要在用户眼前过一遍。
    pending = tuple(
        Candidate(c.target, c.signals, selected=False, origin=c.origin, depth=c.depth)
        for c in sorted(merged, key=lambda c: (-c.score, _node_order(c.node)))
    )
    return KeyInference(BindingState.NEEDS_CONFIRMATION, pending, (*notes, *fresh.notes))


def _relocate(entry: Mapping[str, Any], workflow: Mapping[str, Any]) -> tuple[str, MatchOrigin] | None:
    """一个既有条目在新 workflow 里的落点。"""
    class_type = str(entry.get("class_type", ""))
    input_name = entry.get("input")
    node = workflow.get(str(entry.get("node")))
    if isinstance(node, Mapping) and class_type_of(node) == class_type and _writable(node, input_name):
        return str(entry["node"]), MatchOrigin.KEPT
    title = str(entry.get("title", ""))
    matches = [
        node_id
        for node_id, candidate in _nodes(workflow)
        if class_type_of(candidate) == class_type
        and node_title(candidate) == title
        and _writable(candidate, input_name)
    ]
    return (matches[0], MatchOrigin.REMATCHED) if len(matches) == 1 else None


def _writable(node: Mapping[str, Any], input_name: object) -> bool:
    """条目指的那个字段还在，且仍是字面值。产物条目是节点级的，没有字段要查。"""
    if not isinstance(input_name, str):
        return True
    raw = node_inputs(node).get(input_name)
    return raw is not None and not is_link(raw)


class _Engine:
    """一份 workflow 上的推断：索引建一次，各语义键复用。"""

    def __init__(self, workflow: Mapping[str, Any], rules: InferenceRules) -> None:
        self.workflow = workflow
        self.rules = rules
        self.consumers = consumers_by_node(workflow)
        self.markers = _title_markers(workflow, rules)
        self.polarity = _polarity_by_node(workflow, rules)
        self.output_chain = self._output_chain()

    # -- 对外入口 ---------------------------------------------------------

    def infer_key(self, key: str) -> KeyInference:
        if key == "output":
            return self._select("output", self._output_candidates(), ())
        collected, notes = self._candidates_for(key)
        return self._select(key, collected, notes)

    def workflow_notes(self) -> tuple[Note, ...]:
        """与某个语义键无关、整份 workflow 层面的提示。"""
        notes: list[Note] = []
        for node_id, node in _nodes(self.workflow):
            class_type = class_type_of(node)
            for item in self.rules.batch_size_inputs:
                raw = node_inputs(node).get(item.input)
                if item.class_type == class_type and isinstance(raw, int) and not isinstance(raw, bool) and raw > 1:
                    notes.append(
                        Note(
                            InferenceNote.BATCH_SIZE_ABOVE_ONE,
                            {"node": node_id, "class_type": class_type, "input": item.input, "value": raw},
                        )
                    )
            notes.extend(
                Note(
                    InferenceNote.MANUAL_ONLY_NODE,
                    {"node": node_id, "class_type": class_type, "binding_keys": " / ".join(manual.binding_keys)},
                )
                for manual in self.rules.manual_only_class_types
                if manual.class_type == class_type
            )
        return tuple(notes)

    # -- 候选生成 ---------------------------------------------------------

    def _candidates_for(self, key: str) -> tuple[list[Candidate], tuple[Note, ...]]:
        builder = _Collector()
        notes: list[Note] = []
        marked = self.markers.get(key, ())
        if marked:
            # 标题标记按语义键抑制推断：这个键上只认打了标记的节点，其余信号一律不再参与。
            for node_id in marked:
                for target in self._marked_targets(key, node_id):
                    builder.add(target, SignalHit(BindingSignal.TITLE_MARKER, _weight(BindingSignal.TITLE_MARKER)))
            return builder.done(), ()
        self._external_candidates(key, builder)
        self._sampler_port_candidates(key, builder)
        self._alias_candidates(key, builder, notes)
        self._link_trace_candidates(key, builder, notes)
        return builder.done(), tuple(notes)

    def _marked_targets(self, key: str, node_id: str) -> Iterator[Mapping[str, Any]]:
        """标记是节点级的：节点有多个可参数化字段时按 input 名做二级判定。"""
        node = self.workflow[node_id]
        if key == "output":
            yield self._target(key, node_id, None, node_id)
            return
        loader = self.rules.loader_input(class_type_of(node)) if key in IMAGE_VALUED_KEYS else None
        if loader is not None:
            # 读图节点还带着 upload 一类的界面字段，图像类语义的落点只有承载文件名的那一个。
            yield self._target(key, node_id, loader, node_id, consumer=self._marked_consumer(key, node_id))
            return
        rules = self.rules.semantic_keys.get(key)
        literal = [name for name, raw in node_inputs(node).items() if not is_link(raw)]
        named = [name for name in literal if rules is not None and rules.accepts(name, class_type_of(node))]
        for name in named or literal:
            yield self._target(key, node_id, name, node_id)

    def _marked_consumer(self, key: str, node_id: str) -> Consumer | None:
        """被标记的参考图节点仍要记下它接到谁的哪个入口，张数变少时才改得动图。"""
        ports = self.rules.consumer_ports.get(key, ())
        return self._matching_consumer(node_id, ports) if ports else None

    def _external_candidates(self, key: str, builder: _Collector) -> None:
        """外部约定节点族：只按 class_type 前缀认，参数名按各家的载体读。"""
        for node_id, node in _nodes(self.workflow):
            class_type = class_type_of(node)
            family = self.rules.family_of(class_type)
            if family is None:
                continue
            parameter = family.parameter_of(node_inputs(node), node_title(node))
            if parameter is None or _normalized(parameter) != key:
                continue
            for name, _value in _literal_inputs(node):
                if name in family.ignored_inputs:
                    continue
                builder.add(
                    self._target(key, node_id, name, node_id),
                    SignalHit(
                        BindingSignal.EXTERNAL_NODE_FAMILY,
                        _weight(BindingSignal.EXTERNAL_NODE_FAMILY),
                        {"family": family.name, "parameter": parameter},
                    ),
                )

    def _sampler_port_candidates(self, key: str, builder: _Collector) -> None:
        """条件端口的多跳反溯，只服务正负提示词。

        起点是任何带 ``positive`` / ``negative`` 端口的节点，不只是采样器：模型条件节点自己也
        带这两个端口，从它起步与从采样器起步反溯到的是同一个文本节点，而有些社区图里能指名
        极性的只有它。信号说明因此说「节点」而不说「采样器」。
        """
        port = self.rules.port_of(key)
        if port is None:
            return
        rules = self.rules.semantic_keys.get(key)
        if rules is None:
            return
        for node_id, _node in _nodes(self.workflow):
            for text_node in _trace_condition(self.workflow, self.rules, node_id, port):
                node = self.workflow[text_node]
                for name, _value in _literal_inputs(node):
                    if rules.accepts(name, class_type_of(node)):
                        builder.add(
                            self._target(key, text_node, name, text_node),
                            SignalHit(
                                BindingSignal.SAMPLER_PORT_TRACE,
                                _weight(BindingSignal.SAMPLER_PORT_TRACE),
                                {"node": node_id, "port": port},
                            ),
                        )

    def _alias_candidates(self, key: str, builder: _Collector, notes: list[Note]) -> None:
        """input 名别名表命中；落在白名单节点上时线索更强。"""
        rules = self.rules.semantic_keys.get(key)
        if rules is None:
            return
        for node_id, node in _nodes(self.workflow):
            class_type = class_type_of(node)
            if self._gated_out(node):
                continue
            for name in node_inputs(node):
                if not rules.accepts(name, class_type):
                    continue
                if self.polarity.get((node_id, name), key) != key:
                    # 已由条件端口判给另一极的文本字段，不再作为本键的候选。
                    continue
                landing = resolve_literal(self.workflow, node_id, name, constants=self.rules.constant_nodes)
                if landing.node is None or landing.input is None:
                    notes.append(
                        Note(InferenceNote.COMPUTED_SOURCE, {"node": node_id, "input": name, "binding_key": key})
                    )
                    continue
                hits = [SignalHit(BindingSignal.ALIAS_ONLY, _weight(BindingSignal.ALIAS_ONLY), {"input": name})]
                if class_type in rules.class_types:
                    hits.append(
                        SignalHit(
                            BindingSignal.ALIAS_WITH_CLASS_TYPE,
                            _weight(BindingSignal.ALIAS_WITH_CLASS_TYPE),
                            {"class_type": class_type},
                        )
                    )
                tier = self.rules.tier_of(key, class_type)
                if tier:
                    hits.append(SignalHit(BindingSignal.CLASS_TYPE_TIER, tier, {"class_type": class_type}))
                if rules.title_hit(node_title(node)):
                    hits.append(
                        SignalHit(
                            BindingSignal.TITLE_POLARITY,
                            _weight(BindingSignal.TITLE_POLARITY),
                            {"title": node_title(node)},
                        )
                    )
                builder.add(self._target(key, landing.node, landing.input, node_id), *hits)

    def _link_trace_candidates(self, key: str, builder: _Collector, notes: list[Note]) -> None:
        """顺输出找下游消费者的入口名反推。图像类语义只有这一条路走得通。"""
        if key in IMAGE_VALUED_KEYS:
            self._image_candidates(key, builder, notes)
            return
        rules = self.rules.semantic_keys.get(key)
        if rules is None:
            return
        for node_id, node in _nodes(self.workflow):
            source = self.rules.constant_nodes.get(class_type_of(node))
            if source is None or is_link(node_inputs(node).get(source)):
                continue
            for consumer in self.consumers.get(node_id, ()):
                if rules.accepts(consumer.input, consumer.class_type):
                    builder.add(
                        self._target(key, node_id, source, consumer.node),
                        SignalHit(
                            BindingSignal.LINK_TRACE,
                            _weight(BindingSignal.LINK_TRACE),
                            {"consumer": consumer.node, "input": consumer.input},
                        ),
                    )

    def _image_candidates(self, key: str, builder: _Collector, notes: list[Note]) -> None:
        """读图节点承载文件名的那个字段，是图像类语义唯一写得动的落点。

        它是首帧、尾帧还是参考图，取决于这张图流向了谁的哪个入口——``image`` 这个名字在
        ``LoadImage`` 上是文件名，在 ``LTXVImgToVideo`` 上是首帧，在缩放节点上只是中间连线。
        """
        ports = self.rules.consumer_ports.get(key, ())
        if not ports:
            return
        for node_id, node in _nodes(self.workflow):
            source = self.rules.loader_input(class_type_of(node))
            if source is None or is_link(node_inputs(node).get(source)):
                continue
            consumer = self._matching_consumer(node_id, ports)
            if consumer is None or self._feeds_blocked_input(consumer.node):
                continue
            builder.add(
                self._target(key, node_id, source, node_id, consumer=consumer),
                SignalHit(
                    BindingSignal.LINK_TRACE,
                    _weight(BindingSignal.LINK_TRACE),
                    {"consumer": consumer.node, "input": consumer.input},
                ),
            )
            if key == "reference_images" and not self._known_consumer(consumer):
                notes.append(
                    Note(
                        InferenceNote.REFERENCE_CONSUMER_UNKNOWN,
                        {"node": consumer.node, "class_type": consumer.class_type, "input": consumer.input},
                    )
                )

    def _downstream(self, node_id: str) -> Iterator[Consumer]:
        """顺着图往下游走，逐个交出接在这条图流上的入口，由近及远。

        中间常隔着缩放、变量传递一类只把图原样递下去的节点，只看直接消费者会漏判；每个节点最多
        走一次，图里有环也停得下来。
        """
        seen = {node_id}
        queue = [node_id]
        while queue:
            for consumer in self.consumers.get(queue.pop(0), ()):
                yield consumer
                if consumer.node not in seen:
                    seen.add(consumer.node)
                    queue.append(consumer.node)

    def _matching_consumer(self, node_id: str, ports: Sequence[ConsumerPort]) -> Consumer | None:
        """这条图流上第一个落在这些入口上的消费者。"""
        return next(
            (
                consumer
                for consumer in self._downstream(node_id)
                if any(port.matches(consumer.input, consumer.class_type) for port in ports)
            ),
            None,
        )

    def _feeds_blocked_input(self, node_id: str) -> bool:
        """这条图流最终喂进了收图但不收参考图的入口吗。

        首尾帧被拼成图像批次送进控制视频的形态（VACE）会先撞上合并节点的 ``image1`` / ``image2``——
        那两个入口名在别处正是参考图的格子，只看直接消费者会把首尾帧报成参考图。
        """
        return any(
            self.rules.blocks_image(consumer.class_type, consumer.input) for consumer in self._downstream(node_id)
        )

    def _known_consumer(self, consumer: Consumer) -> bool:
        """这个入口在张数变少时改得动吗：可选入口摘键、两两合并节点 bypass，其余都不认识。"""
        if self.rules.is_optional_input(consumer.class_type, consumer.input):
            return True
        merge = self.rules.merge_node(consumer.class_type)
        return merge is not None and consumer.input in merge.inputs

    def _output_candidates(self) -> list[Candidate]:
        builder = _Collector()
        marked = self.markers.get("output", ())
        for node_id, node in _nodes(self.workflow):
            class_type = class_type_of(node)
            if marked:
                if node_id in marked:
                    builder.add(
                        self._target("output", node_id, None, node_id),
                        SignalHit(BindingSignal.TITLE_MARKER, _weight(BindingSignal.TITLE_MARKER)),
                    )
                continue
            if class_type in self.rules.excluded_output_class_types:
                continue
            candidate = self.rules.output_candidate(class_type)
            if candidate is None:
                continue
            if (
                candidate.requires_true is not None
                and unwrap_value(node_inputs(node).get(candidate.requires_true)) is False
            ):
                # 只预览不落盘的节点取不到成片。
                continue
            builder.add(
                self._target("output", node_id, None, node_id),
                SignalHit(
                    BindingSignal.ALIAS_WITH_CLASS_TYPE,
                    _weight(BindingSignal.ALIAS_WITH_CLASS_TYPE),
                    {"class_type": class_type},
                ),
                SignalHit(
                    BindingSignal.CLASS_TYPE_TIER, self.rules.output_rank(class_type), {"class_type": class_type}
                ),
            )
        return [
            Candidate(c.target, c.signals, c.selected, c.origin, dependency_depth(self.workflow, c.node))
            for c in builder.done()
        ]

    def _output_chain(self) -> frozenset[str]:
        """最终产物那条链路上的全部节点；产物本身尚不唯一时为空集。"""
        candidates = self._output_candidates()
        if not candidates:
            return frozenset()
        best = _pick(candidates, "output")
        if len(best) != 1:
            return frozenset()
        node = candidates[best[0]].node
        return ancestors(self.workflow, node) | {node}

    # -- 组装 -------------------------------------------------------------

    def _target(
        self, key: str, node_id: str, input_name: str | None, carrier: str, *, consumer: Consumer | None = None
    ) -> Mapping[str, Any]:
        """把一个落点拼成可直接写进 ``bindings`` 的条目。

        ``carrier`` 是推断出这一项的承载节点：字段值是连线时落点会上溯到常量节点，而步长这类
        约束属于承载节点。
        """
        node = self.workflow[node_id]
        target: dict[str, Any] = {"node": node_id, "class_type": class_type_of(node), "title": node_title(node)}
        if input_name is not None:
            target["input"] = input_name
        carrier_class_type = class_type_of(self.workflow.get(carrier, node))
        step = self.rules.step_of(key, carrier_class_type)
        if step is not None:
            target["step"] = step
        if key in READ_ONLY_BINDING_KEYS:
            target["direction"] = "read"
        if key == "seed":
            target["policy"] = "random"
        if consumer is not None:
            target["consumer"] = {
                "node": consumer.node,
                "input": consumer.input,
                "class_type": consumer.class_type,
                "title": consumer.title,
            }
        return target

    def _gated_out(self, node: Mapping[str, Any]) -> bool:
        """这个节点被闸门关掉了吗：关掉的节点整个不参与别名扫描。

        MoE 的低噪档与 refiner 段的 ``add_noise`` 是 disable，种子恒为 0；闸门判的是节点而不是
        某个语义键，只是这类节点上能被别名认出的字段目前只有种子。
        """
        gate = self.rules.seed_gate(class_type_of(node))
        if gate is None:
            return False
        return unwrap_value(node_inputs(node).get(gate.input)) != gate.enabled_value

    def _select(self, key: str, candidates: list[Candidate], notes: tuple[Note, ...]) -> KeyInference:
        if not candidates:
            return KeyInference(BindingState.NOT_FOUND, (), notes)
        scored = [self._with_chain(candidate) for candidate in candidates]
        scored.sort(key=lambda candidate: (-candidate.score, _node_order(candidate.node)))
        chosen = set(_pick(scored, key))
        selected = tuple(
            Candidate(c.target, c.signals, index in chosen, c.origin, c.depth) for index, c in enumerate(scored)
        )
        state = BindingState.AUTO_SELECTED if chosen else BindingState.AMBIGUOUS
        return KeyInference(state, selected, notes)

    def _with_chain(self, candidate: Candidate) -> Candidate:
        if candidate.node not in self.output_chain:
            return candidate
        hit = SignalHit(BindingSignal.OUTPUT_CHAIN, _weight(BindingSignal.OUTPUT_CHAIN))
        return Candidate(
            candidate.target, (*candidate.signals, hit), candidate.selected, candidate.origin, candidate.depth
        )


class _Collector:
    """按落点归并候选：同一个 ``(node, input)`` 上的多条信号累加成一个候选。"""

    def __init__(self) -> None:
        self._targets: dict[tuple[str, str | None], Mapping[str, Any]] = {}
        self._signals: dict[tuple[str, str | None], list[SignalHit]] = {}

    def add(self, target: Mapping[str, Any], *hits: SignalHit) -> None:
        raw_input = target.get("input")
        landing = (str(target["node"]), str(raw_input) if isinstance(raw_input, str) else None)
        self._targets.setdefault(landing, target)
        collected = self._signals.setdefault(landing, [])
        present = {hit.signal for hit in collected}
        collected.extend(hit for hit in hits if hit.signal not in present)

    def done(self) -> list[Candidate]:
        return [
            Candidate(
                self._targets[landing],
                tuple(sorted(hits, key=lambda hit: -hit.weight)),
                selected=False,
                origin=MatchOrigin.INFERRED,
            )
            for landing, hits in self._signals.items()
        ]


def _pick(candidates: Sequence[Candidate], key: str) -> tuple[int, ...]:
    """按分选中；没有唯一赢家就一个也不选（参考图除外，它的并列是格子序号）。"""
    if not candidates:
        return ()
    best = max(candidate.score for candidate in candidates)
    tied = [index for index, candidate in enumerate(candidates) if candidate.score == best]
    if key in LIST_BINDING_KEYS:
        return tuple(sorted(tied, key=lambda index: _node_order(candidates[index].node)))
    if len(tied) == 1:
        return (tied[0],)
    if key == "output":
        # 两段式 workflow 的第二段产物上游更深；深度相当则是并行分支，交用户选。
        deepest = max(candidates[index].depth or 0 for index in tied)
        at_depth = [index for index in tied if (candidates[index].depth or 0) == deepest]
        return (at_depth[0],) if len(at_depth) == 1 else ()
    return ()


def _title_markers(workflow: Mapping[str, Any], rules: InferenceRules) -> Mapping[str, tuple[str, ...]]:
    """``_meta.title`` 上的 ``ARCREEL:<key>`` 标记：语义键 → 打了标记的节点，按节点 id 升序。"""
    found: dict[str, list[str]] = {}
    allowed = set(rules.semantic_keys) | {"output"} | set(rules.consumer_ports)
    for node_id, node in _nodes(workflow):
        title = node_title(node).strip()
        if not title.lower().startswith(TITLE_MARKER_PREFIX):
            continue
        words = title[len(TITLE_MARKER_PREFIX) :].split()
        key = words[0].lower() if words else ""
        if key in allowed:
            found.setdefault(key, []).append(node_id)
    return {key: tuple(sorted(nodes, key=_node_order)) for key, nodes in found.items()}


def _polarity_by_node(workflow: Mapping[str, Any], rules: InferenceRules) -> Mapping[tuple[str, str], str]:
    """条件端口反溯判给某一极的文本字段：另一极不再把它当候选。

    没有负向路径的 workflow（正向条件被清零、或整图无 CFG）最常见的误判就是把正向那个文本节点
    同时报成负向候选——两个键的别名表都收 ``text``，不靠端口这一层分不开。
    """
    assigned: dict[tuple[str, str], str] = {}
    for port, key in rules.sampler_ports.items():
        key_rules = rules.semantic_keys.get(key)
        if key_rules is None:
            continue
        for node_id, _node in _nodes(workflow):
            for text_node in _trace_condition(workflow, rules, node_id, port):
                node = workflow[text_node]
                for name, _value in _literal_inputs(node):
                    if key_rules.accepts(name, class_type_of(node)):
                        assigned[(text_node, name)] = key
    return assigned


def _trace_condition(workflow: Mapping[str, Any], rules: InferenceRules, node_id: str, port: str) -> Iterator[str]:
    """从某个节点的条件端口往上走，穿过透传节点，停在第一个不透传的节点上。

    条件在到达采样器前常经过模型条件节点：它们的输出序号定了透传的是哪一极（0 号 positive、
    1 号 negative），继续往上走时端口名随之切换。清零一类的节点不在透传表里，反溯停在那里——
    「负向条件是把正向清零得来的」正确的答案就是「这个 workflow 没有负向文本」。
    """
    link = link_of(node_inputs(workflow.get(node_id, {})).get(port))
    if link is None:
        return
    current, slot = link
    seen = {node_id}
    while current not in seen:
        seen.add(current)
        node = workflow.get(current)
        if not isinstance(node, Mapping):
            return
        if class_type_of(node) not in rules.conditioning_pass_through:
            yield current
            return
        carried = rules.conditioning_slots.get(slot)
        port = port if carried is None else (rules.port_of(carried) or port)
        link = link_of(node_inputs(node).get(port) or node_inputs(node).get("conditioning"))
        if link is None:
            return
        current, slot = link


def _nodes(workflow: Mapping[str, Any]) -> Iterator[tuple[str, Mapping[str, Any]]]:
    for node_id, node in workflow.items():
        if isinstance(node, Mapping):
            yield node_id, node


def _literal_inputs(node: Mapping[str, Any]) -> Iterator[tuple[str, object]]:
    for name, raw in node_inputs(node).items():
        if not is_link(raw):
            yield name, unwrap_value(raw)


def _weight(signal: BindingSignal) -> int:
    return SIGNAL_WEIGHTS[signal]


def _normalized(parameter: str) -> str:
    """外部约定里的参数名规范化后与语义键比对：各家的参数名由用户自己起。"""
    return re.sub(r"[^a-z0-9]+", "_", parameter.strip().lower()).strip("_")


def _node_order(node_id: str) -> tuple[int, str]:
    """节点 id 的排序：数字串按数值比，其余按字面。"""
    match = _NUMERIC_PREFIX.match(node_id)
    return (int(match.group(1)), node_id) if match else (2**31, node_id)
