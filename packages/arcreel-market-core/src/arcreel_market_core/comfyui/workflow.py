"""API-format workflow reading primitives: node fields and link detection.

ComfyUI Export (API) produces a node-ID-to-node object. Every node ``inputs`` field is either a
literal or an upstream link. JSON has no distinguishing tag, so infer by shape: a link is a length-2
array ``[upstream_node_id, output_index]``. When a literal itself is an array, the export wraps it as ``{"__value__": [...]}``,
which unwraps to a literal and is no longer tested as a link.

Node bindings can target only literal fields: upstream nodes produce link-field values at runtime and overwrite any populated value.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Wrapper key for array literals: always treat wrapped fields as literals.
VALUE_WRAPPER_KEY = "__value__"

#: Link shape: ``[upstream_node_id, output_index]``.
_LINK_LENGTH = 2


def has_any_node(document: Mapping[str, Any]) -> bool:
    """Whether this object contains at least one ComfyUI node.

    Nodes are identified by ``class_type``: every API-format value is a node, while endpoint-definition top-level sections (``meta`` /
    ``submit`` / ``poll`` …) are not. One node suffices: malformed nodes should be reported individually by the workflow
    schema rather than route the entire payload elsewhere.
    """
    return any(isinstance(node, Mapping) and "class_type" in node for node in document.values())


def node_inputs(node: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read node ``inputs``. Nodes without parameterizable fields return an empty map."""
    inputs = node.get("inputs")
    return inputs if isinstance(inputs, Mapping) else {}


def is_link(raw: object) -> bool:
    """Whether this field's value is a link.

    Check wrapping before shape: ``{"__value__": ["a", "b"]}`` is an explicitly declared array literal, not a link.
    """
    if isinstance(raw, Mapping) and set(raw) == {VALUE_WRAPPER_KEY}:
        return False
    return isinstance(raw, list) and len(raw) == _LINK_LENGTH
