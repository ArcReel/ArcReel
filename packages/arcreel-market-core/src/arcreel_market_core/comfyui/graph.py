"""Graph primitives for API-format workflows: link parsing, upstream/downstream traversal, dependency depth, and constant tracing.

Node-binding inference reads graph shape rather than individual literal fields: whether ``LoadImage.image`` is a first frame or
reference image depends on which node input consumes its output; which of two ``SaveVideo`` nodes is the final artifact depends on whose upstream
is deeper. These judgments require traversing edges; :mod:`.workflow` recognizes only individual field shapes, so edges are handled separately here.

API-format node IDs are object keys, with edges written on consumers: ``"positive": ["6", 0]`` means this node's positive
input receives node 6 output 0. Reverse edges (who consumes my output) are absent from exports; this module builds them in one graph scan.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from .workflow import VALUE_WRAPPER_KEY, is_link, node_inputs

#: One link: ``(upstream_node_id, upstream_output_index)``.
type LinkRef = tuple[str, int]


@dataclass(frozen=True)
class Consumer:
    """A downstream input connected to a node output."""

    node: str
    input: str
    class_type: str
    title: str
    #: Index of the connected upstream output. Model-conditioning nodes use 0 for positive and 1 for negative.
    slot: int


def node_title(node: Mapping[str, Any]) -> str:
    """Node ``_meta.title``, defaulting to an empty string because the entire section may be absent from exports."""
    meta = node.get("_meta")
    if not isinstance(meta, Mapping):
        return ""
    title = meta.get("title")
    return title if isinstance(title, str) else ""


def class_type_of(node: Mapping[str, Any]) -> str:
    raw = node.get("class_type")
    return raw if isinstance(raw, str) else ""


def unwrap_value(raw: object) -> object:
    """Remove the ``{"__value__": [...]}`` array-literal wrapper; return other values unchanged."""
    if isinstance(raw, Mapping) and set(raw) == {VALUE_WRAPPER_KEY}:
        return raw[VALUE_WRAPPER_KEY]
    return raw


def link_of(raw: object) -> LinkRef | None:
    """Upstream link connected to this field, or ``None`` for a literal."""
    if not is_link(raw):
        return None
    pair: Any = raw
    return str(pair[0]), int(pair[1]) if isinstance(pair[1], int) else 0


def consumers_by_node(workflow: Mapping[str, Any]) -> Mapping[str, tuple[Consumer, ...]]:
    """Reverse edges: upstream node ID to all downstream inputs connected to its outputs."""
    found: dict[str, list[Consumer]] = {}
    for node_id, node in workflow.items():
        if not isinstance(node, Mapping):
            continue
        for name, raw in node_inputs(node).items():
            link = link_of(raw)
            if link is None:
                continue
            found.setdefault(link[0], []).append(
                Consumer(node_id, name, class_type_of(node), node_title(node), link[1])
            )
    return {node_id: tuple(items) for node_id, items in found.items()}


def upstream_of(workflow: Mapping[str, Any], node_id: str) -> Iterator[tuple[str, LinkRef]]:
    """All upstream edges of a node: ``(this_node_input_name, upstream_link)``."""
    node = workflow.get(node_id)
    if not isinstance(node, Mapping):
        return
    for name, raw in node_inputs(node).items():
        link = link_of(raw)
        if link is not None:
            yield name, link


def ancestors(workflow: Mapping[str, Any], node_id: str) -> frozenset[str]:
    """All upstream nodes, excluding this node itself. Used to identify the final-artifact path."""
    seen: set[str] = set()
    stack = [node_id]
    while stack:
        current = stack.pop()
        for _, (parent, _slot) in upstream_of(workflow, current):
            if parent not in seen and parent != node_id:
                seen.add(parent)
                stack.append(parent)
    return frozenset(seen)


def dependency_depth(workflow: Mapping[str, Any], node_id: str) -> int:
    """Length of this node's longest upstream path. In two-stage workflows, second-stage artifacts are strictly deeper than first-stage artifacts.

    Valid workflows have no cycles (ComfyUI's executor requires a DAG), but imports are user files; converge on cycles by
    not traversing backward again: assign 0 to nodes on back edges and do not revisit them.

    Use an explicit stack rather than recursion: inference receives complete user-imported workflows with unbounded node counts and path lengths. A sufficiently
    long valid path would exceed recursion with ``RecursionError``, producing a 500 for an otherwise valid workflow.
    """
    memo: dict[str, int] = {}
    on_path: set[str] = set()
    stack: list[tuple[str, bool]] = [(node_id, False)]
    while stack:
        current, expanded = stack.pop()
        if expanded:
            on_path.discard(current)
            parents = [parent for _, (parent, _slot) in upstream_of(workflow, current)]
            # A parent not yet memoized can only be a back edge: it remains on the current path, so count it as 0.
            memo[current] = 1 + max(memo.get(parent, 0) for parent in parents) if parents else 0
            continue
        if current in memo:
            continue
        on_path.add(current)
        stack.append((current, True))
        for _, (parent, _slot) in upstream_of(workflow, current):
            if parent not in memo and parent not in on_path:
                stack.append((parent, False))
    return memo[node_id]


@dataclass(frozen=True)
class Resolution:
    """Result of tracing a link to a literal.

    ``node`` / ``input`` identify the actual literal location: the original field itself if literal, or the upstream
    constant node reached through links. All three values are empty when tracing stops at a computation node: no literal exists there, and a binding cannot write it.
    """

    node: str | None
    input: str | None
    value: object | None

    @property
    def found(self) -> bool:
        return self.node is not None


#: Result when upstream tracing cannot find a stopping point.
UNRESOLVED = Resolution(None, None, None)


def resolve_literal(
    workflow: Mapping[str, Any], node_id: str, input_name: str, *, constants: Mapping[str, str]
) -> Resolution:
    """Read an input's literal value; for links, trace upstream to a constant node.

    ``constants`` maps constant-node class_type to the input holding its value. Official templates place duration and frame rate in
    ``PrimitiveInt`` / ``PrimitiveFloat`` before linking into conditioning nodes; binding the constant node is
    equivalent to binding a literal field. Computation nodes such as ``ComfyMathExpression`` provide no writable location; leave that semantic binding for the user to specify manually.
    """
    node = workflow.get(node_id)
    if not isinstance(node, Mapping):
        return UNRESOLVED
    inputs = node_inputs(node)
    # Check key presence rather than whether the value is None: ``"text": null`` is an existing, writable literal field,
    # distinct from a node having no such input at all.
    if input_name not in inputs:
        return UNRESOLVED
    raw = inputs[input_name]
    link = link_of(raw)
    if link is None:
        return Resolution(node_id, input_name, unwrap_value(raw))
    seen: set[str] = {node_id}
    current = link[0]
    while current not in seen:
        seen.add(current)
        upstream = workflow.get(current)
        if not isinstance(upstream, Mapping):
            return UNRESOLVED
        constant_input = constants.get(class_type_of(upstream))
        if constant_input is None:
            return UNRESOLVED
        upstream_inputs = node_inputs(upstream)
        if constant_input not in upstream_inputs:
            return UNRESOLVED
        value = upstream_inputs[constant_input]
        next_link = link_of(value)
        if next_link is None:
            return Resolution(current, constant_input, unwrap_value(value))
        current = next_link[0]
    return UNRESOLVED
