"""Import routing: pasted JSON definitions versus API/UI workflows.

Users commonly have exported workflows, not endpoint definitions. ComfyUI Export creates UI
``nodes`` / ``links`` canvas archives without ``class_type``, impossible to submit;
Export (API) creates submit-ready node tables. Neither carries ``kind``; distinguish shapes
rather than forcing users to infer wrong menu choices from field diagnostics.

Wrap API workflows as ``kind: comfyui`` with empty bindings: wrapping enters definition
flows only; binding inference is separate, with no resolved semantics yet.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from arcreel_market_core.comfyui.validator import CURRENT_SCHEMA_VERSION
from arcreel_market_core.comfyui.workflow import has_any_node
from arcreel_market_core.definition_diagnostics import (
    ROOT_PATH,
    DefinitionDiagnostics,
    DefinitionErrorCode,
    DefinitionIssue,
)
from arcreel_market_core.endpoint_definition.kinds import COMFYUI_KIND

#: Auto-wrapper ``meta`` placeholders: raw workflows lack authors/names; users change before saving.
PLACEHOLDER_META_NAME = "ComfyUI workflow"
PLACEHOLDER_META_AUTHOR = "unknown"
PLACEHOLDER_META_VERSION = "1.0.0"


class ImportShape(StrEnum):
    """Imported payload shapes."""

    #: Definitions with ``kind`` validate according to their own kind.
    ENDPOINT_DEFINITION = "endpoint_definition"
    #: ComfyUI Export (API) node tables auto-wrap as endpoint definitions.
    COMFYUI_API_WORKFLOW = "comfyui_api_workflow"
    #: ComfyUI Export canvas archives cannot submit; reject, directing users to Export (API).
    COMFYUI_UI_WORKFLOW = "comfyui_ui_workflow"


def route_import_payload(document: object, *, media_type: str) -> tuple[ImportShape, Any]:
    """Identify payload shapes, wrapping API workflows into definitions.

    Nonobjects follow definition validation, already reporting definitions-must-be-objects; saying not-workflow
    would split one root type error into two. UI requires array ``nodes`` plus ``links``; API
    keys are node ids with object values, never arrays, so shapes cannot collide.

    Objects without nodes also follow definitions: omitted ``kind`` should report missing kind,
    not treat sections as nodes and produce secondary ``workflow.<节名>`` errors.
    """
    if not isinstance(document, Mapping) or "kind" in document:
        return ImportShape.ENDPOINT_DEFINITION, document
    if isinstance(document.get("nodes"), list) and "links" in document:
        return ImportShape.COMFYUI_UI_WORKFLOW, document
    if not has_any_node(document):
        return ImportShape.ENDPOINT_DEFINITION, document
    return ImportShape.COMFYUI_API_WORKFLOW, wrap_api_workflow(document, media_type=media_type)


def ui_workflow_refusal() -> DefinitionDiagnostics:
    """Structured UI-format refusal, outside definition validators, identifying the correct export menu."""
    return DefinitionDiagnostics(errors=(DefinitionIssue(ROOT_PATH, DefinitionErrorCode.COMFYUI_UI_FORMAT_WORKFLOW),))


def wrap_api_workflow(workflow: Mapping[str, Any], *, media_type: str) -> dict[str, Any]:
    """Wrap raw API workflows as ComfyUI definitions.

    ``bindings`` is empty objects, not per-key empty lists: lists explicitly mean unsupported, while here
    nothing was inferred. Resulting definitions fail required prompt/output bindings, accurately reflecting their state.
    """
    return {
        "kind": COMFYUI_KIND,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "meta": {
            "name": PLACEHOLDER_META_NAME,
            "author": PLACEHOLDER_META_AUTHOR,
            "version": PLACEHOLDER_META_VERSION,
        },
        "media_type": media_type,
        "workflow": dict(workflow),
        "bindings": {},
    }
