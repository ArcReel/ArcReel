"""ComfyUI node-binding inference API.

ComfyUI does not declare which workflow nodes receive prompts or produce final output, so infer externally.
Inference runs entirely on the server and returns **candidates only**, each with a score and matched signals.
The frontend renders them; persist only after user confirmation (``docs/adr/0082``). Inference and reimport
rematching share one endpoint: rematching supplies the workflow plus existing bindings, differing from first import
only in whether existing bindings are empty.

Request bodies match import: ``kind`` identifies endpoint definitions; otherwise accept raw ComfyUI API workflows
and wrap them according to ``media_type``. Admission checks structure only: changed workflows often lack nodes
referenced by existing bindings, precisely the input rematching must handle."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body
from pydantic import BaseModel

from arcreel_market_core.comfyui.validator import structural_diagnostics
from lib.custom_provider.comfyui.import_shapes import ImportShape, route_import_payload, ui_workflow_refusal
from lib.custom_provider.comfyui.inference import infer_bindings
from lib.infra.api_errors import UnprocessableError
from server.i18n import Translator

router = APIRouter(prefix="/comfyui", tags=["Custom Endpoints"])

#: Raw request payload, matching import; avoid ``dict`` so nonobject inputs receive structured diagnostics too.
InferBody = Annotated[Any, Body()]

#: For pasted raw workflows, the importer specifies image versus video output.
ImportMediaType = Literal["image", "video"]


class SignalPayload(BaseModel):
    """One matched signal: stable name, score contribution and user-facing explanation."""

    signal: str
    weight: int
    message: str


class NotePayload(BaseModel):
    """An advisory hint that does not block saving but explains how a particular shape will behave."""

    code: str
    message: str


class CandidatePayload(BaseModel):
    """A candidate entry writable directly into ``bindings``, with reasons for its selection."""

    target: dict[str, Any]
    score: int
    signals: list[SignalPayload]
    selected: bool
    origin: str
    # Upstream chain length for output candidates, explaining their selection; null for other semantic keys.
    depth: int | None = None


class KeyInferencePayload(BaseModel):
    """Inference results for one semantic key."""

    state: str
    candidates: list[CandidatePayload]
    notes: list[NotePayload]


class InferResponse(BaseModel):
    media_type: str
    # Whether this result can persist directly: ambiguous/unconfirmed keys or unresolved prompts/outputs prevent validation.
    savable: bool
    bindings: dict[str, KeyInferencePayload]
    notes: list[NotePayload]
    # How this payload was interpreted.
    import_shape: ImportShape
    # Wrapped raw API workflow; null for existing definitions, which clients retain unchanged.
    wrapped_definition: dict[str, Any] | None = None


@router.post("/infer")
async def infer_endpoint_bindings(
    body: InferBody,
    _t: Translator,
    media_type: ImportMediaType = "video",
) -> InferResponse:
    """Infer workflow node-binding candidates, also rematching reimports when existing bindings are supplied.

    The server retains no state and persists no results. Clients render the binding editor and save through endpoint
    creation or full replacement only after user confirmation. ``media_type`` affects only raw workflows; endpoint
    definitions already contain it."""
    shape, definition = route_import_payload(body, media_type=media_type)
    if shape is ImportShape.COMFYUI_UI_WORKFLOW:
        raise UnprocessableError("custom_endpoint_definition_invalid").with_diagnostic(
            ui_workflow_refusal().to_payload(_t)
        )
    diagnostics = structural_diagnostics(definition)
    if diagnostics.errors or not isinstance(definition, dict):
        raise UnprocessableError("custom_endpoint_definition_invalid").with_diagnostic(diagnostics.to_payload(_t))
    payload = infer_bindings(definition).to_payload(_t)
    return InferResponse(
        **payload,
        import_shape=shape,
        wrapped_definition=definition if shape is ImportShape.COMFYUI_API_WORKFLOW else None,
    )
