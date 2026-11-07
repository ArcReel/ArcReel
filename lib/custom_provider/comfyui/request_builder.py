"""Actual workflow construction: deep-copy templates, bind values, convert sizes/frames, resolve seeds, edit by image counts.

Generation requests require four conversions: project ratios/resolution tiers to pixels; durations to frames;
seeds random/preserved by binding policy; fewer references than slots require deleting excess loaders/downstream nodes
rather than repeating last images. All depend on inputs only, without config/DB/network;
callers upload first, supplying server-recognized references.

Never mutate templates: ``definition["workflow"]`` alone is authoritative, deep-copied per submission.
Return ``workflow_sha256`` too; identical inputs construct identical digests, enabling preview/
actual submission comparisons.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from random import Random
from typing import Any

from arcreel_market_core.aspect_size import (
    DEFAULT_SHORT_EDGE,
    IMAGE_TIER_SHORT_EDGE,
    VIDEO_TIER_SHORT_EDGE,
    aspect_size,
)
from arcreel_market_core.aspect_size import resolution_to_short_edge as short_edge_of_resolution
from arcreel_market_core.comfyui.bindings import align_frames, step_of
from arcreel_market_core.comfyui.bindings import bound_fps as _bound_fps
from arcreel_market_core.comfyui.bindings import int_literal_of as _int_literal
from arcreel_market_core.comfyui.bindings import literal_of as _literal
from arcreel_market_core.comfyui.bindings import positive_number as _positive_number
from arcreel_market_core.comfyui.bindings import targets_of as _targets
from arcreel_market_core.comfyui.workflow import is_link, node_inputs
from lib.prompts.prompt_utils import append_avoid_text, split_avoid_lines

from .capabilities import keeps_its_own_frame_count, size_is_fixed
from .failures import IMAGE_DROP_UNSUPPORTED, ComfyuiError
from .inference_rules import InferenceRules, MergeNode, load_inference_rules

logger = logging.getLogger(__name__)

#: Exclusive random-seed upper bound; ComfyUI samplers accept unsigned 32-bit seed inputs.
SEED_UPPER_BOUND = 2**32

#: Default seed policy, identical to schema ``default``.
_DEFAULT_SEED_POLICY = "random"


@dataclass(frozen=True)
class MediaInputs:
    """Actual asset references from upload responses: ``subfolder/name``, or ``name``
    without directories, composed by callers after uploads.

    ``None`` versus empty sequences concerns this request's absence, not endpoint support, determined by binding tristates.
    """

    start_image: str | None = None
    end_image: str | None = None
    reference_images: Sequence[str] = ()


@dataclass(frozen=True)
class BuiltWorkflow:
    """Construction results: actual workflows/digests/three conversion outcomes.

    ``width`` / ``height`` / ``frames`` / ``seed`` = ``None`` means unbound dimensions preserving workflow
    literals. Previews explain conversions through these fields; ``dropped_nodes`` identifies graph deletions,
    returned with workflows rather than logged only.

    ``negative_prompt`` is extracted exclusion text, not final negative-node values, which also retain
    author literals; unbound negative inputs receive nothing at all.
    """

    workflow: dict[str, Any]
    workflow_sha256: str
    seed: int | None = None
    width: int | None = None
    height: int | None = None
    frames: int | None = None
    negative_prompt: str = ""
    dropped_nodes: tuple[str, ...] = ()


def build_workflow(
    definition: Mapping[str, Any],
    *,
    prompt: str,
    aspect_ratio: str,
    resolution: str | None = None,
    duration_seconds: float | None = None,
    media: MediaInputs | None = None,
    seed: int | None = None,
    rng: Random | None = None,
) -> BuiltWorkflow:
    """Build actual workflows from ComfyUI definitions/generation requests.

    ``rng`` injects random seeds, production default module :class:`~random.Random`; request ``seed``
    (image requests can specify) overrides random, never ``keep`` policies: author-preserved literals
    must not silently change through request seeds.

    Edit graphs before writing values: deletions reshape graphs; filling by node id/field first
    would merely write into nodes about to disappear.
    """
    workflow = deepcopy(dict(definition["workflow"]))
    bindings: Mapping[str, Any] = definition.get("bindings") or {}
    media_type = "image" if definition.get("media_type") == "image" else "video"

    dropped = _apply_media(workflow, bindings, media or MediaInputs(), load_inference_rules(media_type))

    body, avoid_text = split_avoid_lines(prompt)
    _write_all(workflow, _targets(bindings.get("prompt")), body)
    _write_negative_prompt(workflow, bindings.get("negative_prompt"), avoid_text)
    width, height = _write_size(workflow, bindings, aspect_ratio=aspect_ratio, resolution=resolution, media=media_type)
    # Preserve authored lengths lacking native-duration tiers: endpoints declare durations undriven by
    # ArcReel through empty tiers/readonly UI; planning borrows seconds, never user-selected.
    driven = None if keeps_its_own_frame_count(definition) else duration_seconds
    frames = _write_frames(workflow, bindings, duration_seconds=driven)
    actual_seed = _write_seed(workflow, bindings, requested=seed, rng=rng or Random())

    return BuiltWorkflow(
        workflow=workflow,
        workflow_sha256=workflow_sha256(workflow),
        seed=actual_seed,
        width=width,
        height=height,
        frames=frames,
        negative_prompt=avoid_text,
        dropped_nodes=dropped,
    )


def workflow_sha256(workflow: Mapping[str, Any]) -> str:
    """Workflow content digests.

    SHA-256 canonical JSON with sorted keys/no extra whitespace/non-ASCII preserved makes identical
    contents independent of dict insertion order, comparable across previews/submissions/repeated builds.
    """
    canonical = json.dumps(workflow, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Value-writing primitives.


def _mutable_inputs(node: Any) -> dict[str, Any]:
    """Writable node ``inputs``, creating empty mappings when ``inputs`` is missing/malformed."""
    if not isinstance(node, dict):
        return {}
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        inputs = {}
        node["inputs"] = inputs
    return inputs


def _write_one(workflow: dict[str, Any], target: Mapping[str, Any], value: object) -> None:
    """Write targets, skipping deleted nodes without recreating them."""
    node_id = str(target["node"])
    name = target.get("input")
    if node_id not in workflow or not isinstance(name, str):
        logger.debug("跳过填值：节点 %s 的 %r 已不在实发 workflow 中", node_id, name)
        return
    _mutable_inputs(workflow[node_id])[name] = value


def _write_all(workflow: dict[str, Any], targets: Sequence[Mapping[str, Any]], value: object) -> None:
    """Write identical values to every semantic-key target.

    Accept normalized entries, never renormalize: callers already have :func:`_targets` results. Raw-value
    signatures would create ambiguity, potentially rejecting normalized inputs as malformed
    and discarding whole batches.
    """
    for target in targets:
        _write_one(workflow, target, value)


# Negative prompts.


def _write_negative_prompt(workflow: dict[str, Any], targets: object, avoid_text: str) -> None:
    """Append body-extracted exclusions after literal negative-node values.

    Empty/missing bindings write nothing: extraction already removed exclusions from bodies, and no negative
    input exists. Reinserting would describe unwanted entities as desired subjects to models.
    """
    for target in _targets(targets):
        literal = _literal(workflow, target)
        _write_one(workflow, target, append_avoid_text(literal if isinstance(literal, str) else "", avoid_text))


# Size.


def _write_size(
    workflow: dict[str, Any],
    bindings: Mapping[str, Any],
    *,
    aspect_ratio: str,
    resolution: str | None,
    media: str,
) -> tuple[int | None, int | None]:
    """Derive ratio/resolution dimensions, align down per entry steps, then write.

    ``round_to`` is LCM of every width/height step; :func:`~arcreel_market_core.aspect_size.aspect_size` dimensions
    divide by each naturally, preserving exact ratios. Still align down again per entry before writes:
    constraints belong to workflow inputs, independent of coincidental ``round_to`` selection.

    Without selected resolutions, short edges use minimum literal workflow dimensions, author-tuned native tiers;
    ratios still follow projects. Unreadable integer literals use shared cross-backend fallback short edges.

    UI/construction share :func:`~lib.custom_provider.comfyui.capabilities.size_is_fixed`:
    UI disables selectors, construction decides writes. One-sided bindings mean fixed because
    writing derived sizes on one side while preserving other literals yields neither native nor
    selected ratios.
    """
    if size_is_fixed(bindings):
        return None, None
    width_targets = _targets(bindings.get("width"))
    height_targets = _targets(bindings.get("height"))

    round_to = math.lcm(*[step_of(target) for target in (*width_targets, *height_targets)])
    tier_map = IMAGE_TIER_SHORT_EDGE if media == "image" else VIDEO_TIER_SHORT_EDGE
    if resolution and resolution.strip():
        short_edge = short_edge_of_resolution(resolution, tier_map=tier_map)
    else:
        short_edge = _literal_short_edge(workflow, width_targets, height_targets)

    width, height = aspect_size(aspect_ratio, short_edge, round_to=round_to)
    written_width = _write_stepped(workflow, width_targets, width)
    written_height = _write_stepped(workflow, height_targets, height)
    return written_width, written_height


def _literal_short_edge(
    workflow: Mapping[str, Any],
    width_targets: Sequence[Mapping[str, Any]],
    height_targets: Sequence[Mapping[str, Any]],
) -> int:
    literals = [
        value
        for target in (*width_targets, *height_targets)
        if (value := _int_literal(workflow, target)) is not None and value > 0
    ]
    if not literals:
        logger.info("分辨率未选且 workflow 字面宽高读不出整数，短边取兜底 %d", DEFAULT_SHORT_EDGE)
        return DEFAULT_SHORT_EDGE
    return min(literals)


def _write_stepped(workflow: dict[str, Any], targets: Sequence[Mapping[str, Any]], value: int) -> int | None:
    """Align derived dimensions down per entry steps, write, and return final written values."""
    written: int | None = None
    for target in targets:
        written = _align_down(value, step_of(target))
        _write_one(workflow, target, written)
    return written


def _align_down(value: int, step: int) -> int:
    """Align down to ``step`` multiples, minimum one ``step`` because zero dimensions always fail submission."""
    return max(step, value - value % step)


# Frames.


def _write_frames(
    workflow: dict[str, Any], bindings: Mapping[str, Any], *, duration_seconds: float | None
) -> int | None:
    """Convert duration/fps to frames and write.

    ``frames = round(时长 × 帧率) + 1``: video models count first frames + intervals, so 1s @ 24fps
    means 25 frames, not 24. Prefer readonly ``fps`` binding literals, actual workflow rates;
    otherwise manual ``frames`` entry constants. Without either, leave literals rather than guess fps
    and risk incorrect lengths.

    Frame steps mean ``frames ≡ 1 (mod step)``, such as 4n+1/8n+1; align down with minimum ``1 + step``.

    ``duration_seconds`` = ``None`` means requests do not drive this dimension, supplied by callers
    when endpoints lack tiers; preserve literal frame counts.
    """
    targets = _targets(bindings.get("frames"))
    if not targets or duration_seconds is None:
        return None

    bound_fps = _bound_fps(workflow, bindings)
    written: int | None = None
    for target in targets:
        fps = bound_fps if bound_fps is not None else _positive_number(target.get("fps"))
        if fps is None:
            logger.info("帧数未写：既无 fps 只读绑定，条目也未手填帧率")
            continue
        step = step_of(target)
        written = align_frames(round(duration_seconds * fps) + 1, step)
        _write_one(workflow, target, written)
    return written


# Seeds.


def _write_seed(
    workflow: dict[str, Any], bindings: Mapping[str, Any], *, requested: int | None, rng: Random
) -> int | None:
    """Write per-entry seed policies, returning actually effective seeds.

    ``random`` entries share one value across samplers, commonly MoE pairs, preserving reproducibility.
    ``keep`` remains byte-unchanged, even with request seeds: author-preserved literals
    are established workflow behavior.

    Return effective, not generated seeds: all-``keep`` returns literals, recording actual rerun
    numbers in version metadata.
    """
    targets = _targets(bindings.get("seed"))
    if not targets:
        return None
    rolling = [target for target in targets if target.get("policy", _DEFAULT_SEED_POLICY) == _DEFAULT_SEED_POLICY]
    if not rolling:
        return next((value for target in targets if (value := _int_literal(workflow, target)) is not None), None)

    value = requested if requested is not None else rng.randrange(SEED_UPPER_BOUND)
    for target in rolling:
        _write_one(workflow, target, value)
    return value


# Graph edits.


def _apply_media(
    workflow: dict[str, Any], bindings: Mapping[str, Any], media: MediaInputs, rules: InferenceRules
) -> tuple[str, ...]:
    """Fill supplied assets and delete unsupplied loaders/downstream nodes.

    Bound-but-unsupplied first/last/reference inputs require graph edits: otherwise loaders read previous images from
    workflow literal filenames, generating with first frames users never selected.
    """
    doomed: list[str] = []
    for key, value in (("start_image", media.start_image), ("end_image", media.end_image)):
        targets = _targets(bindings.get(key))
        if value is not None:
            _write_all(workflow, targets, value)
        else:
            doomed.extend(str(target["node"]) for target in targets)
    doomed.extend(
        _apply_reference_images(workflow, _targets(bindings.get("reference_images")), media.reference_images, rules)
    )
    if not doomed:
        return ()
    return _drop_nodes(workflow, doomed, output_nodes=_output_nodes(bindings), rules=rules)


def _apply_reference_images(
    workflow: dict[str, Any],
    targets: Sequence[Mapping[str, Any]],
    values: Sequence[str],
    rules: InferenceRules,
) -> list[str]:
    """Fill references by count, returning loader nodes to delete.

    Delete excess slots rather than repeat last images, which doubles subject emphasis. Require known
    ``consumer`` destinations saved with bindings, adjustable only for optional inputs/pairwise merges.
    Missing ``consumer`` or unknown destination types fall back to repeating last images:
    prefer compositional bias over guessing away required inputs and causing submission
    ``node_errors``. Inference already warned via identical predicates at binding saves. With zero images,
    nothing can repeat, so preserve loader literals.
    """
    filled = min(len(targets), len(values))
    for index in range(filled):
        _write_one(workflow, targets[index], values[index])
    spare = targets[filled:]
    if not spare:
        return []
    if any(not _adjustable(target, rules) for target in spare):
        logger.info("参考图格子 %d 个、本次 %d 张，但有格子的 consumer 改不动图，改图跳过", len(targets), len(values))
        if values:
            _write_all(workflow, spare, values[-1])
        return []
    return [str(target["node"]) for target in spare]


def _adjustable(target: Mapping[str, Any], rules: InferenceRules) -> bool:
    """Whether reference slots permit graph adjustment when counts shrink."""
    consumer = target.get("consumer")
    if not isinstance(consumer, Mapping):
        return False
    class_type = consumer.get("class_type")
    input_name = consumer.get("input")
    if not isinstance(class_type, str) or not isinstance(input_name, str):
        return False
    return rules.adjustable_input(class_type, input_name)


def _output_nodes(bindings: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(str(target["node"]) for target in _targets(bindings.get("output")))


def _drop_nodes(
    workflow: dict[str, Any], seeds: Sequence[str], *, output_nodes: frozenset[str], rules: InferenceRules
) -> tuple[str, ...]:
    """Delete nodes, cascading forward and pruning upstream feeding deleted nodes only.

    Rule tables classify forward handling: detach optional inputs, bypass pairwise merges,
    otherwise delete/cascade whole nodes. Reaching output nodes fails because outputs depend on those images;
    broken submissions would merely cause remote errors/waits.
    """
    deleted: list[str] = []
    orphan_candidates: set[str] = set()
    pending = list(seeds)
    while pending:
        node_id = pending.pop()
        if node_id not in workflow:
            continue
        if node_id in output_nodes:
            raise ComfyuiError(IMAGE_DROP_UNSUPPORTED, node=node_id)
        orphan_candidates.update(_link_sources(workflow[node_id]))
        del workflow[node_id]
        deleted.append(node_id)
        pending.extend(_detach_consumers(workflow, node_id, output_nodes, deleted, orphan_candidates, rules))
    _prune_orphans(workflow, orphan_candidates, pinned=output_nodes, deleted=deleted)
    return tuple(deleted)


def _detach_consumers(
    workflow: dict[str, Any],
    node_id: str,
    output_nodes: frozenset[str],
    deleted: list[str],
    orphan_candidates: set[str],
    rules: InferenceRules,
) -> list[str]:
    """Handle downstream references to deleted nodes, returning required cascading deletions."""
    doomed: list[str] = []
    for consumer_id in list(workflow):
        consumer = workflow.get(consumer_id)
        if consumer is None:
            continue
        inputs = _mutable_inputs(consumer)
        names = [name for name, raw in inputs.items() if _refers_to(raw, node_id)]
        if not names:
            continue
        class_type = str(consumer.get("class_type") or "")
        merge = rules.merge_node(class_type)
        for name in names:
            if rules.is_optional_input(class_type, name):
                del inputs[name]
            elif merge is not None and name in merge.inputs and consumer_id not in output_nodes:
                if _bypass_merge(workflow, consumer_id, merge, name, deleted, orphan_candidates):
                    break
                doomed.append(consumer_id)
            else:
                doomed.append(consumer_id)
    return doomed


def _bypass_merge(
    workflow: dict[str, Any],
    merge_id: str,
    merge: MergeNode,
    dropped: str,
    deleted: list[str],
    orphan_candidates: set[str],
) -> bool:
    """Remove pairwise merges, reconnecting downstream to surviving branches.

    Surviving branches must link live nodes. Otherwise delete/cascade merges too: literals have no upstream to reconnect;
    when both inputs share deleted loaders, purported survivors are already deleted, leaving dangling links
    in still-submitted workflows.

    Add no nodes, only rewrite downstream references.
    """
    inputs = _mutable_inputs(workflow[merge_id])
    first, second = merge.inputs
    survivor = _as_link(inputs.get(second if dropped == first else first))
    if survivor is None or str(survivor[0]) not in workflow:
        return False
    for other_id in list(workflow):
        if other_id == merge_id:
            continue
        other_inputs = _mutable_inputs(workflow[other_id])
        for name, raw in list(other_inputs.items()):
            if _refers_to(raw, merge_id):
                other_inputs[name] = list(survivor)
    orphan_candidates.update(_link_sources(workflow[merge_id]))
    del workflow[merge_id]
    deleted.append(merge_id)
    return True


def _prune_orphans(
    workflow: dict[str, Any], candidates: set[str], *, pinned: frozenset[str], deleted: list[str]
) -> None:
    """Prune upstream feeding deleted nodes only.

    Candidates come only from deleted-node links, never all nodes lacking downstream consumers; previews naturally lack them,
    so whole scans would rewrite user workflows. Bound carriers receive no special protection: after all consumers vanish,
    they are dead and ComfyUI never executes their filled values.
    """
    pending = set(candidates)
    while pending:
        node_id = pending.pop()
        if node_id not in workflow or node_id in pinned or _has_consumer(workflow, node_id):
            continue
        pending.update(_link_sources(workflow[node_id]))
        del workflow[node_id]
        deleted.append(node_id)


def _has_consumer(workflow: Mapping[str, Any], node_id: str) -> bool:
    return any(
        _refers_to(raw, node_id)
        for other_id, other in workflow.items()
        if other_id != node_id
        for raw in node_inputs(other).values()
    )


def _as_link(raw: object) -> list[Any] | None:
    """Return actual links, otherwise ``None``; literal-array wrappers are not links."""
    return raw if isinstance(raw, list) and is_link(raw) else None


def _refers_to(raw: object, node_id: str) -> bool:
    """Whether fields link to ``node_id``."""
    link = _as_link(raw)
    return link is not None and str(link[0]) == node_id


def _link_sources(node: Any) -> set[str]:
    """All upstream node ids from node link inputs."""
    return {str(link[0]) for raw in node_inputs(node).values() if (link := _as_link(raw)) is not None}
