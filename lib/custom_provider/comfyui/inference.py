"""Binding inference: identify candidates per workflow semantic key, returning scores/hit signals.

ComfyUI has no official parameterization conventions, only ``inputs`` / ``class_type`` / ``_meta.title``.
Prompts/outputs require external inference. Two ecosystem references infer anew on every submission,
making tasks irreproducible and hiding ambiguity. ArcReel centralizes **candidates** only, returning scores/signals
for user confirmation and explicit persistence before bindings become authoritative (``docs/adr/0082``).

**Scoring** removes competing primary-key assumptions between input aliases/class allowlists: both contribute
signals, highest totals win per semantic key. Seven ranked levels each outweigh all lower levels combined,
so stronger evidence dominates any weaker combination. Three secondary signals, carrier tiers/output-chain
membership/positive-negative title substrings, break ties only, never outranking any primary level.

Multiple nodes per key are normal, such as base/refiner prompts or MoE samplers. Unique highest scores auto-select,
ties remain unresolved, no candidates mean not found. References differ:
multiple candidates are ordered slots, not competitors; select all tied candidates, list order defining reference indices.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from arcreel_market_core.comfyui.bindings import BINDING_KEYS_BY_MEDIA_TYPE, REQUIRED_BINDING_KEYS
from arcreel_market_core.comfyui.graph import (
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
from arcreel_market_core.comfyui.workflow import is_link, node_inputs
from arcreel_market_core.validation_messages import ValidationMessage
from lib.infra.validation_messages import default_translate

from .inference_rules import ConsumerPort, InferenceRules, load_inference_rules

#: Inference-message prefixes for signals/notes, sharing diagnostic ``val_ce_`` namespaces.
MESSAGE_KEY_PREFIX = "val_ce_infer_"

#: Case-insensitive title prefixes, ending at first whitespace.
TITLE_MARKER_PREFIX = "arcreel:"

#: Readonly semantics: read literal workflow values, never write.
READ_ONLY_BINDING_KEYS = frozenset({"fps"})

#: Ordered-slot semantic keys whose ties are not ambiguity.
LIST_BINDING_KEYS = frozenset({"reference_images"})

#: Semantic keys storing ``consumer`` node/input destinations.
CONSUMER_BINDING_KEYS = frozenset({"reference_images"})

#: Image-value semantics target loader filename fields only, distinct from
#: :data:`.bindings.IMAGE_BINDING_KEYS`, allowed image-endpoint semantics.
IMAGE_VALUED_KEYS = frozenset({"start_image", "end_image", "reference_images"})

_NUMERIC_PREFIX = re.compile(r"^(\d+)")


class BindingSignal(StrEnum):
    """Candidate-hit signals: seven primary levels followed by three secondary tie-breakers."""

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


#: Primary weights each exceed all lower weights combined, including secondary maxima,
#: preventing weaker accumulation from outranking stronger signals.
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

#: Weakest primary weight must exceed all secondary weights combined, preserving tie-breaking only.
WEAKEST_GRADED_WEIGHT = SIGNAL_WEIGHTS[BindingSignal.LINK_TRACE]


class InferenceNote(StrEnum):
    """Nonblocking inference notes explaining behavior users need to know."""

    REFERENCE_CONSUMER_UNKNOWN = "reference_consumer_unknown"
    BATCH_SIZE_ABOVE_ONE = "batch_size_above_one"
    MANUAL_ONLY_NODE = "manual_only_node"
    COMPUTED_SOURCE = "computed_source"
    REMATCHED = "rematched"
    BINDING_LOST = "binding_lost"
    TARGET_TAKEN = "target_taken"


class BindingState(StrEnum):
    """Inference states per semantic key."""

    #: Unique highest scores, already selected.
    AUTO_SELECTED = "auto_selected"
    #: Tied highest scores, none auto-selected; sort all candidates for users.
    AMBIGUOUS = "ambiguous"
    #: No candidates; users may manually choose any ``(node, input)``.
    NOT_FOUND = "not_found"
    #: Existing empty lists mean confirmed unsupported, never reinfer.
    UNSUPPORTED = "unsupported"
    #: Lost saved entries trigger reinference/confirmation before save.
    NEEDS_CONFIRMATION = "needs_confirmation"


class MatchOrigin(StrEnum):
    """Candidate origins."""

    INFERRED = "inferred"
    #: Same node id/type reuses saved entries unchanged.
    KEPT = "kept"
    #: Changed ids rematch unique graph-wide type/title.
    REMATCHED = "rematched"


@dataclass(frozen=True)
class SignalHit:
    """Hit signals with weights."""

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
            "message": self.message.render(translate or default_translate),
        }


@dataclass(frozen=True)
class Note:
    """One note."""

    code: InferenceNote
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> ValidationMessage:
        return ValidationMessage(f"{MESSAGE_KEY_PREFIX}note_{self.code.value}", self.params)

    def to_payload(self, translate: Callable[..., str] | None = None) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message.render(translate or default_translate)}


@dataclass(frozen=True)
class Candidate:
    """Persistable targets plus selection evidence."""

    target: Mapping[str, Any]
    signals: tuple[SignalHit, ...]
    selected: bool
    origin: MatchOrigin
    #: Upstream artifact-chain lengths, ``None`` for other keys; choose deepest among outputs.
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
    """Inference results for one semantic key."""

    state: BindingState
    candidates: tuple[Candidate, ...]
    notes: tuple[Note, ...] = ()

    @property
    def selected_targets(self) -> tuple[Mapping[str, Any], ...]:
        """Entries directly persistable into ``bindings``; empty unless automatically recognized."""
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
    """Complete workflow inference results."""

    media_type: str
    keys: Mapping[str, KeyInference]
    notes: tuple[Note, ...] = ()

    @property
    def savable(self) -> bool:
        """Whether saving inference as-is passes validation.

        Three rules: no ambiguous/unconfirmed keys, requiring selection or explicit unsupported empties; prompts/outputs
        must resolve; no shared writable fields between keys. Same hard gates as validators.
        """
        blocked = {BindingState.AMBIGUOUS, BindingState.NEEDS_CONFIRMATION}
        if any(result.state in blocked for result in self.keys.values()):
            return False
        landings = [
            landing
            for result in self.keys.values()
            for target in result.selected_targets
            if (landing := _target_landing(target)) is not None
        ]
        if len(landings) != len(set(landings)):
            return False
        return all(self.keys.get(key, _MISSING).selected_targets for key in REQUIRED_BINDING_KEYS)

    def to_payload(self, translate: Callable[..., str] | None = None) -> dict[str, Any]:
        return {
            "media_type": self.media_type,
            "savable": self.savable,
            "bindings": {key: result.to_payload(translate) for key, result in self.keys.items()},
            "notes": [note.to_payload(translate) for note in self.notes],
        }


#: Placeholder for absent semantics: empty ``selected_targets`` means unbound.
_MISSING = KeyInference(BindingState.NOT_FOUND, ())


def infer_bindings(definition: Mapping[str, Any]) -> BindingInference:
    """Infer ComfyUI bindings, respecting three existing-binding states.

    Missing keys mean never inferred, running full inference; empty lists mean user-confirmed unsupported, never reinferred;
    nonempty saved bindings rematch/reuse/migrate individually, rerunning key inference only for lost entries.
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
    _resolve_target_collisions(results)
    return BindingInference(media_type, results, engine.workflow_notes())


def _target_landing(target: Mapping[str, Any]) -> tuple[str, str] | None:
    """Writable target fields; readonly targets never write, node-level output targets lack fields."""
    if target.get("direction") == "read":
        return None
    name = target.get("input")
    return (str(target["node"]), name) if isinstance(name, str) else None


def _resolve_target_collisions(results: dict[str, KeyInference]) -> None:
    """One field cannot receive two semantic keys.

    Node-level strong title signals may satisfy both: node 7 marked
    ``ARCREEL:prompt`` puts ``prompt`` / ``negative_prompt`` on ``text``. Actual construction writes positive prompts,
    then appends Avoid to the same field, making supposedly negative prompts positive. Keep higher-scored keys,
    remove losing candidates/recompute states, often truthfully not-found.

    Ties keep neither, leaving user decisions. Saved bindings outweigh inferred candidates;
    when both saved, preserve them as definition inconsistency caught by save validators.

    Each yielding round recollects claims: losing keys may choose second candidates already claimed elsewhere.
    One-pass handling would miss secondary collisions and send two keys to one field. Every round
    removes candidates from finite totals, guaranteeing termination.
    """
    while _yield_one_collision(results):
        pass


def _claimants_by_landing(results: Mapping[str, KeyInference]) -> dict[tuple[str, str], list[tuple[str, int]]]:
    """Landings → claiming ``(语义键, 分数)``; auto-selected keys only, unresolved keys claim nothing."""
    claims: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for key, result in results.items():
        if result.state is not BindingState.AUTO_SELECTED:
            continue
        for candidate in result.candidates:
            landing = _target_landing(candidate.target) if candidate.selected else None
            if landing is not None:
                claims.setdefault(landing, []).append((key, candidate.score))
    return claims


def _yield_one_collision(results: dict[str, KeyInference]) -> bool:
    """First yieldable conflict in document order yields lower scores; ``False`` if none.

    Skip unyieldable saved/saved conflicts and continue, otherwise reporting unchanged conflicts would
    prevent outer-loop termination.
    """
    for landing, claimants in sorted(_claimants_by_landing(results).items()):
        if len({key for key, _score in claimants}) < 2:
            continue
        best = max(score for _key, score in claimants)
        leaders = [key for key, score in claimants if score == best]
        winner = leaders[0] if len(leaders) == 1 else None
        losers = [key for key, _score in claimants if key != winner and not _from_saved_bindings(results[key])]
        if not losers:
            continue
        for key in losers:
            results[key] = _yield_landing(key, results[key], landing, claimants)
        return True
    return False


def _from_saved_bindings(result: KeyInference) -> bool:
    """Candidates derived from saved bindings; inference cannot displace user-confirmed targets."""
    return any(hit.signal is BindingSignal.MANUAL_BINDING for c in result.candidates for hit in c.signals)


def _yield_landing(
    key: str, result: KeyInference, landing: tuple[str, str], claimants: Sequence[tuple[str, int]]
) -> KeyInference:
    """Remove one landing's candidates for a key and recompute states from survivors."""
    others = sorted({other for other, _score in claimants if other != key})
    note = Note(
        InferenceNote.TARGET_TAKEN,
        {"binding_key": key, "node": landing[0], "input": landing[1], "others": " / ".join(others)},
    )
    remaining = [c for c in result.candidates if _target_landing(c.target) != landing]
    return _decide(key, remaining, (*result.notes, note))


def _rematch(key: str, saved: Sequence[Any], workflow: Mapping[str, Any], engine: _Engine) -> KeyInference:
    """Match saved bindings to new workflows, reusing/migrating/losing individually.

    Three decreasing reliability levels: same node id/type/title reuses unchanged; unique whole-graph type/title migrates
    to new ids and marks rematched; otherwise lost. Lost keys rerun wholly, requiring confirmation: reimport never silently
    saves, exposing reused/reidentified targets.
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
        _refresh_consumer(target, key, node_id, engine)
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
    # Lost entries prevent any auto-selection, requiring review even for reused survivors.
    pending = tuple(
        Candidate(c.target, c.signals, selected=False, origin=c.origin, depth=c.depth)
        for c in sorted(merged, key=lambda c: (-c.score, _node_order(c.node)))
    )
    return KeyInference(BindingState.NEEDS_CONFIRMATION, pending, (*notes, *fresh.notes))


def _relocate(entry: Mapping[str, Any], workflow: Mapping[str, Any]) -> tuple[str, MatchOrigin] | None:
    """Existing-entry locations in new workflows."""
    class_type = str(entry.get("class_type", ""))
    input_name = entry.get("input")
    node = workflow.get(str(entry.get("node")))
    if (
        isinstance(node, Mapping)
        and class_type_of(node) == class_type
        and _same_title(entry, node)
        and _writable(node, input_name)
    ):
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


def _same_title(entry: Mapping[str, Any], node: Mapping[str, Any]) -> bool:
    """Whether saved titles equal same-id node titles in new graphs.

    Same-type/input nodes, commonly positive/negative ``CLIPTextEncode``, may swap ids during exports. Type/writability alone
    would match old ids and silently swap values. Both titles absent, including missing entire
    ``_meta``, cannot verify and count equal; absent saved ``title`` likewise, because optional
    schema fields do not establish whether titles existed originally.
    """
    if "title" not in entry:
        return True
    return str(entry["title"]) == node_title(node)


def _refresh_consumer(target: dict[str, Any], key: str, node_id: str, engine: _Engine) -> None:
    """Reinfer nested ``consumer`` from live graphs after relocation.

    ``consumer`` is inference-only without user controls, unlike preserved ``step`` / ``policy``.
    Reexported loaders/consumers often renumber together; stale values would point to nonexistent nodes.
    Remove uninferable keys, meaning unknown image destinations just like never inferred ``consumer``.
    """
    if key not in CONSUMER_BINDING_KEYS:
        return
    consumer = engine.consumer_of(key, node_id)
    if consumer is None:
        target.pop("consumer", None)
    else:
        target["consumer"] = _consumer_entry(consumer)


def _consumer_entry(consumer: Consumer) -> dict[str, Any]:
    """One ``consumer`` record, four persisted ``bindings`` fields."""
    return {
        "node": consumer.node,
        "input": consumer.input,
        "class_type": consumer.class_type,
        "title": consumer.title,
    }


def _writable(node: Mapping[str, Any], input_name: object) -> bool:
    """Target fields still exist as literals; node-level artifacts lack fields.

    Check key presence: ``"text": null`` exists as literal, matching validators.
    """
    if not isinstance(input_name, str):
        return True
    inputs = node_inputs(node)
    return input_name in inputs and not is_link(inputs[input_name])


class _Engine:
    """Inference per workflow, building indexes once for all keys."""

    def __init__(self, workflow: Mapping[str, Any], rules: InferenceRules) -> None:
        self.workflow = workflow
        self.rules = rules
        self.consumers = consumers_by_node(workflow)
        self.markers = _title_markers(workflow, rules)
        self.polarity = _polarity_by_node(workflow, rules)
        self.output_chain = self._output_chain()

    # Public entries.

    def infer_key(self, key: str) -> KeyInference:
        if key == "output":
            return self._select("output", self._output_candidates(), ())
        collected, notes = self._candidates_for(key)
        return self._select(key, collected, notes)

    def workflow_notes(self) -> tuple[Note, ...]:
        """Workflow-wide notes independent of semantic keys."""
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

    # Candidate generation.

    def _candidates_for(self, key: str) -> tuple[list[Candidate], tuple[Note, ...]]:
        builder = _Collector()
        notes: list[Note] = []
        marked = self.markers.get(key, ())
        if marked:
            # Semantic title markers suppress other signals for that key, considering marked nodes only.
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
        """Node-level markers use input names for secondary decisions on multiparameter nodes."""
        node = self.workflow[node_id]
        if key == "output":
            yield self._target(key, node_id, None, node_id)
            return
        loader = self.rules.loader_input(class_type_of(node)) if key in IMAGE_VALUED_KEYS else None
        if loader is not None:
            # Loaders include upload UI fields; image semantics target filenames only.
            yield self._target(key, node_id, loader, node_id, consumer=self._marked_consumer(key, node_id))
            return
        rules = self.rules.semantic_keys.get(key)
        literal = [name for name, raw in node_inputs(node).items() if not is_link(raw)]
        named = [name for name in literal if rules is not None and rules.accepts(name, class_type_of(node))]
        for name in named or literal:
            yield self._target(key, node_id, name, node_id)

    def _marked_consumer(self, key: str, node_id: str) -> Consumer | None:
        """Marked references still record consumer nodes/inputs, enabling graph adjustment for fewer images."""
        ports = self.rules.consumer_ports.get(key, ())
        return self._matching_consumer(node_id, ports) if ports else None

    def _external_candidates(self, key: str, builder: _Collector) -> None:
        """External node conventions recognized by class_type prefixes, reading their parameter-name carriers."""
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
        """Multihop conditioning backtrace for positive/negative prompts only.

        Start any nodes with ``positive`` / ``negative``, not samplers alone: model-conditioning nodes
        have both and reach the same texts, sometimes uniquely identifying
        polarity in community workflows. Signal descriptions therefore say nodes, never samplers.
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
        """Input alias matches, stronger on allowlisted nodes."""
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
                    # Text already assigned opposite polarity by ports cannot compete here.
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
        """Infer semantics from downstream consumer input names; sole workable route for image semantics."""
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
        """Loader filename fields are image semantics' sole writable targets.

        First/last/reference roles depend on consumer nodes/ports: ``image`` on
        ``LoadImage`` means filenames, ``LTXVImgToVideo`` first frames, scaling nodes intermediate links.
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
            if key == "reference_images" and not self.rules.adjustable_input(consumer.class_type, consumer.input):
                notes.append(
                    Note(
                        InferenceNote.REFERENCE_CONSUMER_UNKNOWN,
                        {"node": consumer.node, "class_type": consumer.class_type, "input": consumer.input},
                    )
                )

    def _downstream(self, node_id: str) -> Iterator[Consumer]:
        """Walk downstream image streams, yielding inputs nearest-first.

        Scaling/variable-pass-through nodes often intervene, so direct consumers alone miss matches;
        visit each node at most once, terminating cycles too.
        """
        seen = {node_id}
        queue = [node_id]
        while queue:
            for consumer in self.consumers.get(queue.pop(0), ()):
                yield consumer
                if consumer.node not in seen:
                    seen.add(consumer.node)
                    queue.append(consumer.node)

    def consumer_of(self, key: str, node_id: str) -> Consumer | None:
        """Where this loader's image stream lands as this semantic key."""
        return self._matching_consumer(node_id, self.rules.consumer_ports.get(key, ()))

    def _matching_consumer(self, node_id: str, ports: Sequence[ConsumerPort]) -> Consumer | None:
        """First stream consumers matching these inputs."""
        return next(
            (
                consumer
                for consumer in self._downstream(node_id)
                if any(port.matches(consumer.input, consumer.class_type) for port in ports)
            ),
            None,
        )

    def _feeds_blocked_input(self, node_id: str) -> bool:
        """Whether image streams ultimately feed nonreference inputs.

        VACE first/last frames merge into control-video batches, first hitting merge ``image1`` / ``image2``:
        elsewhere these name reference slots, so direct-only checks would misidentify frames as references.
        """
        return any(
            self.rules.blocks_image(consumer.class_type, consumer.input) for consumer in self._downstream(node_id)
        )

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
                # Preview-only nodes cannot yield final artifacts.
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
        """All final-artifact-chain nodes; empty while output remains ambiguous."""
        candidates = self._output_candidates()
        if not candidates:
            return frozenset()
        best = _pick(candidates, "output")
        if len(best) != 1:
            return frozenset()
        node = candidates[best[0]].node
        return ancestors(self.workflow, node) | {node}

    # Assembly.

    def _target(
        self, key: str, node_id: str, input_name: str | None, carrier: str, *, consumer: Consumer | None = None
    ) -> Mapping[str, Any]:
        """Assemble persistable ``bindings`` entries from landings.

        ``carrier`` is the inference carrier: linked values relocate targets to constants,
        while constraints such as steps belong to carriers.
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
        # Only reference entries declare consumer (schema writeTarget additionalProperties:false);
        # adding to other keys would make inferred definitions unsavable. Match _refresh_consumer.
        if consumer is not None and key in CONSUMER_BINDING_KEYS:
            target["consumer"] = _consumer_entry(consumer)
        return target

    def _gated_out(self, node: Mapping[str, Any]) -> bool:
        """Whether gates disable whole nodes from alias scanning.

        MoE low-noise/refiner ``add_noise`` disable fixes seeds at 0; gates classify nodes,
        not semantic keys, though seeds are currently their only alias-recognizable fields.
        """
        gate = self.rules.seed_gate(class_type_of(node))
        if gate is None:
            return False
        return unwrap_value(node_inputs(node).get(gate.input)) != gate.enabled_value

    def _select(self, key: str, candidates: list[Candidate], notes: tuple[Note, ...]) -> KeyInference:
        return _decide(key, [self._with_chain(candidate) for candidate in candidates], notes)

    def _with_chain(self, candidate: Candidate) -> Candidate:
        if candidate.node not in self.output_chain:
            return candidate
        hit = SignalHit(BindingSignal.OUTPUT_CHAIN, _weight(BindingSignal.OUTPUT_CHAIN))
        return Candidate(
            candidate.target, (*candidate.signals, hit), candidate.selected, candidate.origin, candidate.depth
        )


class _Collector:
    """Merge signals into one candidate per ``(node, input)`` landing."""

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
    """Select by scores, none without unique winners except reference ties defining slot indices."""
    if not candidates:
        return ()
    best = max(candidate.score for candidate in candidates)
    tied = [index for index, candidate in enumerate(candidates) if candidate.score == best]
    if key in LIST_BINDING_KEYS:
        return tuple(sorted(tied, key=lambda index: _node_order(candidates[index].node)))
    if len(tied) == 1:
        return (tied[0],)
    if key == "output":
        # Second-stage outputs have deeper upstream chains; similar depth means parallel branches for user selection.
        deepest = max(candidates[index].depth or 0 for index in tied)
        at_depth = [index for index in tied if (candidates[index].depth or 0) == deepest]
        return (at_depth[0],) if len(at_depth) == 1 else ()
    return ()


def _title_markers(workflow: Mapping[str, Any], rules: InferenceRules) -> Mapping[str, tuple[str, ...]]:
    """``_meta.title`` ``ARCREEL:<key>`` markers: keys → marked nodes sorted by node ids."""
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
    """Text fields assigned polarities through conditioning backtrace, excluded from opposite candidates.

    Workflows without negative paths, zeroed positives/no CFG, commonly misidentify positive text as
    negative because both alias vocabularies include ``text``; conditioning ports disambiguate.
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
    """Trace conditioning ports upstream through pass-through nodes, stopping at first nonpass-through.

    Model-conditioning nodes before samplers encode polarities in output indices (0 positive,
    1 negative), switching port names during traversal. Zeroing nodes are absent from pass-through tables,
    correctly stopping negative-derived-from-positive-zeroing as no negative text.
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
    """Normalize user-chosen external parameter names against semantic keys."""
    return re.sub(r"[^a-z0-9]+", "_", parameter.strip().lower()).strip("_")


def _decide(key: str, scored: Sequence[Candidate], notes: tuple[Note, ...]) -> KeyInference:
    """Sort scores into selections/states; complete scores make repeated candidate sets deterministic."""
    if not scored:
        return KeyInference(BindingState.NOT_FOUND, (), notes)
    ranked = sorted(scored, key=lambda candidate: (-candidate.score, _node_order(candidate.node)))
    chosen = set(_pick(ranked, key))
    selected = tuple(
        Candidate(c.target, c.signals, index in chosen, c.origin, c.depth) for index, c in enumerate(ranked)
    )
    state = BindingState.AUTO_SELECTED if chosen else BindingState.AMBIGUOUS
    return KeyInference(state, selected, notes)


def _node_order(node_id: str) -> tuple[int, str]:
    """Node-id sorting: numeric strings by value, others literal."""
    match = _NUMERIC_PREFIX.match(node_id)
    return (int(match.group(1)), node_id) if match else (2**31, node_id)
