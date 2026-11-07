"""ComfyUI endpoint-definition validation: the dispatch target for ``kind: comfyui``.

Two gates: ``schema.json`` governs structure (field sets, target-tuple shapes, and which semantic keys accept optional
entry keys); this module governs semantics: prompt and artifact bindings are required, semantic keys are whitelisted
by media type, every target's node and field really exist in the workflow and are not links, and credentials enter
only through ``auth``. Both layers produce :class:`DefinitionIssue` using the same diagnostic codes as declarative definitions.

Pure logic: no database access, requests, or environment reads.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from arcreel_market_core.auth_section import API_KEY_VARIABLE, PLACEHOLDER, check_auth_section
from arcreel_market_core.definition_diagnostics import (
    DefinitionDiagnostics,
    DefinitionErrorCode,
    DefinitionIssue,
    join_path,
)
from arcreel_market_core.definition_schema_errors import most_specific, translate_schema_error

from .bindings import BINDING_KEYS_BY_MEDIA_TYPE, REQUIRED_BINDING_KEYS, fps_literals, positive_number, targets_of
from .graph import ancestors, link_of
from .workflow import is_link, node_inputs

SCHEMA_PATH = Path(__file__).parent / "schema.json"

#: Version of the ComfyUI definition format itself, independent of the declarative-definition version line.
CURRENT_SCHEMA_VERSION = "1.0.0"

#: Fields present in declarative definitions but absent from ComfyUI definitions, mapped to their destinations. These are easy to write when following declarative shapes;
#: a generic unknown-field message does not explain their absence.
REMOVED_FIELD_REASONS: Mapping[str, str] = {
    "capabilities": "val_ce_removed_reason_comfyui_capabilities",
}


@cache
def load_schema() -> dict[str, Any]:
    """Read and cache ComfyUI definition ``schema.json``. Public so the documentation site and frontend share this contract."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@cache
def _schema_validator() -> Draft202012Validator:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_comfyui_definition(document: Mapping[str, Any]) -> DefinitionDiagnostics:
    """The two ComfyUI-definition gates.

    Do not run semantic validation after structural errors: binding/workflow cross-checks assume valid shapes on both
    sides. Continuing with incomplete structures would produce misleading secondary errors.
    """
    structural = structural_diagnostics(document)
    if structural.errors:
        return structural
    auth = check_auth_section(document.get("auth") or {}, variable_issues=_auth_variable_issues)
    return DefinitionDiagnostics(
        errors=(*_semantic_issues(document), *auth.errors, *_reserved_auth_query_issues(document)),
        warnings=auth.warnings,
    )


#: Query parameters required by artifact retrieval (``GET /view``). If credential query keys match them, request assembly
#: overwrites credentials with artifact parameters: submit and poll succeed, but downloads lack credentials and the proxy likely returns 401.
_VIEW_RESERVED_QUERY = frozenset({"filename", "subfolder", "type"})


def _reserved_auth_query_issues(document: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    """``auth.query`` entries using parameter names reserved by the artifact-download route.

    They cannot share keys: ComfyUI needs these three parameters to return a file, and replacing them changes the
    download target. Reject at save time rather than let users encounter 401 after generation has completed.
    """
    query: Mapping[str, Any] = (document.get("auth") or {}).get("query") or {}
    for name in query:
        if str(name) in _VIEW_RESERVED_QUERY:
            yield DefinitionIssue(
                join_path(join_path("auth", "query"), str(name)),
                DefinitionErrorCode.AUTH_QUERY_RESERVED,
                {"param": str(name)},
            )


def structural_diagnostics(document: object) -> DefinitionDiagnostics:
    """Run only structure checks: field sets, target-tuple shapes, and optional entry keys allowed for each semantic key.

    Node-binding inference must run on structurally valid definitions whose bindings do not yet match. When importing
    a changed workflow again, most nodes targeted by existing entries may no longer exist; this is exactly the input
    rematching handles, not a reason to reject it. Semantic validation there would only report inevitably true errors.
    """
    return DefinitionDiagnostics(errors=tuple(_structural_issues(document)))


def _structural_issues(document: Any) -> Iterator[DefinitionIssue]:
    for error in _schema_validator().iter_errors(document):
        yield from translate_schema_error(most_specific(error), removed_fields=REMOVED_FIELD_REASONS)


def _semantic_issues(document: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    bindings: Mapping[str, Any] = document["bindings"]
    workflow: Mapping[str, Any] = document["workflow"]
    media_type = str(document["media_type"])
    yield from _required_binding_issues(bindings)
    yield from _media_type_issues(bindings, media_type)
    yield from _target_issues(bindings, workflow, media_type)
    yield from _collision_issues(bindings, media_type)
    yield from _fps_issues(bindings, workflow, media_type)
    yield from _api_key_outside_auth_issues(document)


def _required_binding_issues(bindings: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    """Prompt and artifact must already be bound: neither empty lists nor absent keys suffice."""
    for key in REQUIRED_BINDING_KEYS:
        if not bindings.get(key):
            yield DefinitionIssue(
                join_path("bindings", key), DefinitionErrorCode.COMFYUI_BINDING_REQUIRED, {"binding_key": key}
            )


def _media_type_issues(bindings: Mapping[str, Any], media_type: str) -> Iterator[DefinitionIssue]:
    """Whitelist semantic keys by media type: image endpoints have no first/last frames, frame counts, or frame rates."""
    allowed = BINDING_KEYS_BY_MEDIA_TYPE[media_type]
    for key in bindings:
        if key not in allowed:
            yield DefinitionIssue(
                join_path("bindings", key),
                DefinitionErrorCode.COMFYUI_BINDING_KEY_NOT_ALLOWED,
                {"binding_key": key, "media_type": media_type, "allowed": " / ".join(sorted(allowed))},
            )


def _target_issues(
    bindings: Mapping[str, Any], workflow: Mapping[str, Any], media_type: str
) -> Iterator[DefinitionIssue]:
    """Every target must land on a real, nonlink workflow field.

    Do not check targets individually for out-of-scope semantic keys: their entries will not be populated anyway.
    More node-location errors would bury the real diagnostic that this key does not exist on image endpoints.
    """
    allowed = BINDING_KEYS_BY_MEDIA_TYPE[media_type]
    for key, targets in bindings.items():
        if key not in allowed:
            continue
        for index, target in enumerate(targets):
            yield from _one_target_issues(join_path(join_path("bindings", key), index), target, workflow)


def _one_target_issues(path: str, target: Mapping[str, Any], workflow: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    node_id = str(target["node"])
    node = workflow.get(node_id)
    if node is None:
        yield DefinitionIssue(join_path(path, "node"), DefinitionErrorCode.COMFYUI_NODE_NOT_FOUND, {"node": node_id})
        return
    yield from _class_type_issues(path, target, node)
    name = target.get("input")
    if name is None:
        yield from _consumer_issues(path, target, workflow)
        return
    inputs = node_inputs(node)
    input_path = join_path(path, "input")
    if name not in inputs:
        yield DefinitionIssue(
            input_path, DefinitionErrorCode.COMFYUI_INPUT_NOT_FOUND, {"node": node_id, "input": str(name)}
        )
        return
    if is_link(inputs[name]):
        yield DefinitionIssue(
            input_path, DefinitionErrorCode.COMFYUI_INPUT_IS_LINK, {"node": node_id, "input": str(name)}
        )
        return
    yield from _consumer_issues(path, target, workflow)


def _consumer_issues(path: str, target: Mapping[str, Any], workflow: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    """A reference-image entry's nested ``consumer`` must also match the workflow.

    It records which input of which node receives this slot's image. Actual request construction uses it to decide
    whether to modify the graph or repeat the final image when image count decreases. Node presence, input presence,
    and unchanged type must all match the live graph, and that input must actually be fed by this slot.

    Checking only the first three is insufficient. Definitions can be shared and manually edited: an existing, truly
    optional input unrelated to the slot passes those three checks. Construction then incorrectly decides the graph
    is modifiable and deletes nodes through the required-input branch, potentially hitting
    ``comfyui_image_drop_unsupported`` when the correct response was to repeat the final image.
    """
    consumer = target.get("consumer")
    if not isinstance(consumer, Mapping):
        return
    consumer_path = join_path(path, "consumer")
    node_id = str(consumer["node"])
    node = workflow.get(node_id)
    if node is None:
        yield DefinitionIssue(
            join_path(consumer_path, "node"), DefinitionErrorCode.COMFYUI_NODE_NOT_FOUND, {"node": node_id}
        )
        return
    name = str(consumer["input"])
    inputs = node_inputs(node)
    if name not in inputs:
        yield DefinitionIssue(
            join_path(consumer_path, "input"),
            DefinitionErrorCode.COMFYUI_INPUT_NOT_FOUND,
            {"node": node_id, "input": name},
        )
    elif not _feeds(workflow, str(target["node"]), inputs[name]):
        yield DefinitionIssue(
            join_path(consumer_path, "input"),
            DefinitionErrorCode.COMFYUI_CONSUMER_NOT_FED,
            {"node": str(target["node"]), "consumer": node_id, "input": name},
        )
    yield from _class_type_issues(consumer_path, consumer, node)


def _class_type_issues(path: str, record: Mapping[str, Any], node: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    """An entry's recorded ``class_type`` must match its target node's current type.

    This is not decoration: rematching uses it for identity. If a node remains but its type differs, rematching
    rejects that location and searches the whole graph for another node uniquely matching type plus title, or marks
    the entry lost. A wrong type can silently move bindings to a node the user never selected. The same applies to
    reference-image ``consumer``: construction uses its type to consult optional inputs and decide graph modification.
    """
    recorded = record.get("class_type")
    actual = node.get("class_type")
    if isinstance(recorded, str) and isinstance(actual, str) and actual != recorded:
        yield DefinitionIssue(
            join_path(path, "class_type"),
            DefinitionErrorCode.COMFYUI_CLASS_TYPE_MISMATCH,
            {"node": str(record["node"]), "class_type": recorded, "actual": actual},
        )


def _feeds(workflow: Mapping[str, Any], node_id: str, raw: object) -> bool:
    """Whether this input's link can reach this node by traversing upstream.

    The inferred consumer need not be direct: the graph may pass through relay nodes before reaching the image input.
    Therefore check reachability rather than compare only the two ends of one edge.
    """
    link = link_of(raw)
    if link is None:
        return False
    return link[0] == node_id or node_id in ancestors(workflow, link[0])


def _collision_issues(bindings: Mapping[str, Any], media_type: str) -> Iterator[DefinitionIssue]:
    """A field can be the write location of only one semantic key.

    Construction populates by semantic key; when two keys target one field, the later write overwrites the earlier.
    If ``prompt`` and ``negative_prompt`` share one ``text`` input, the user's negative prompt is actually the positive
    one, causing incorrect images without an obvious cause. The same applies to two entries of one key sharing a
    location: the second reference image overwrites the first.

    Read-only targets occupy no write location (they only read); node-level artifact targets have no field to occupy.

    Assign ownership by definition order: the first key retains the location and the later entry gets the duplicate
    diagnostic, located where the user can identify it.
    """
    allowed = BINDING_KEYS_BY_MEDIA_TYPE[media_type]
    owners: dict[tuple[str, str], str] = {}
    for key in bindings:
        if key not in allowed:
            continue
        for index, target in enumerate(bindings[key]):
            landing = _write_landing(target)
            if landing is None:
                continue
            if landing in owners:
                path = join_path(join_path(join_path("bindings", key), index), "input")
                yield DefinitionIssue(
                    path,
                    DefinitionErrorCode.COMFYUI_TARGET_COLLISION,
                    {"node": landing[0], "input": landing[1], "binding_key": key, "owner": owners[landing]},
                )
                continue
            owners[landing] = key


def _fps_issues(bindings: Mapping[str, Any], workflow: Mapping[str, Any], media_type: str) -> Iterator[DefinitionIssue]:
    """Frame rate has one source of truth: literals read from multiple read-only ``fps`` bindings must agree.

    ``frames`` conversion (``round(duration × fps) + 1``) applies one frame rate to all frame-count targets. If two
    read-only bindings produce different literals, the definition cannot identify the workflow's frame rate.
    Construction chooses the first, calculating another branch's frame count with the wrong rate; output becomes
    longer or shorter than selected without an error. The same applies when an ``fps`` binding and a manually entered
    constant on a ``frames`` entry separately express the same value. Bindings yielding no literal do not participate:
    missing nodes or fields are reported separately by target validation.

    Image endpoints lack these two semantic keys (``BINDING_KEYS_BY_MEDIA_TYPE``) and do not enter this check.
    """
    if "fps" not in BINDING_KEYS_BY_MEDIA_TYPE[media_type]:
        return
    literals = fps_literals(workflow, bindings)
    manual = [value for target in targets_of(bindings.get("frames")) if (value := positive_number(target.get("fps")))]
    distinct = sorted({*literals, *manual})
    if len(distinct) > 1:
        yield DefinitionIssue(
            join_path("bindings", "fps"),
            DefinitionErrorCode.COMFYUI_FPS_CONFLICT,
            {"values": " / ".join(_format_fps(value) for value in distinct)},
        )


def _format_fps(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def _write_landing(target: Mapping[str, Any]) -> tuple[str, str] | None:
    """The field this target writes. Read-only and node-level artifact targets have no write location."""
    if target.get("direction") == "read":
        return None
    name = target.get("input")
    return (str(target["node"]), name) if isinstance(name, str) else None


def _auth_variable_issues(path: str, name: str) -> list[DefinitionIssue]:
    """ComfyUI ``auth`` accepts only ``api_key``: no source exists for other variables.

    Declarative auth can reference base variables such as ``base_url``; ComfyUI clients lack that template context.
    Template evaluation beyond credentials does not exist on this side.
    """
    return [DefinitionIssue(path, DefinitionErrorCode.UNDECLARED_VARIABLE, {"name": name})]


def _api_key_outside_auth_issues(document: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    """Credentials enter only through ``auth``.

    The workflow is embedded unchanged and is not template-rendered on submission. Writing ``{{api_key}}`` inside
    it has no effect and distributes real credentials with exported files.
    """
    for path in _api_key_outside_auth(document):
        yield DefinitionIssue(path, DefinitionErrorCode.API_KEY_OUTSIDE_AUTH)


def _api_key_outside_auth(document: Mapping[str, Any]) -> Iterator[str]:
    for field, value in document.items():
        if field == "auth":
            continue
        yield from _api_key_references(field, value)


def _api_key_references(path: str, value: object) -> Iterator[str]:
    if isinstance(value, str):
        if API_KEY_VARIABLE in PLACEHOLDER.findall(value):
            yield path
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield from _api_key_references(join_path(path, str(key)), child)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _api_key_references(join_path(path, index), child)
