"""Map reference admission and generation-input rejection to server contracts: route domain exceptions and per-target batch problems.

Entry admission lives in :mod:`lib.references.reference_admission`; execution-time input rejection comes from
:mod:`lib.artifacts.generation_input`. This module only translates outcomes into response shapes, avoiding duplicated
decision logic across single submissions, whole-batch admission and execution-time rejection."""

from __future__ import annotations

from collections.abc import Iterable

from lib.artifacts.generation_input import (
    ASSET_DESCRIPTION_REQUIRED_CODE,
    DERIVATIVE_DESCRIPTION_REQUIRED_CODE,
    DERIVATIVE_OWNER_SHEET_MISSING_CODE,
    PROMPT_PENDING_CODE,
    InputGap,
    InputRefused,
)
from lib.generation.generation_result import GenerationAction, GenerationProblem
from lib.infra.api_errors import BadRequestError
from lib.references.reference_admission import (
    SHEET_MISSING_CODE,
    UNREGISTERED_REFERENCE_CODE,
    ReferenceAdmission,
    admit_storyboard_items,
)
from lib.references.reference_catalog import build_reference_catalog

#: Parameter names identifying semantic-gap objects in their respective messages.
_SUBJECT_PARAMS: dict[str, str] = {
    PROMPT_PENDING_CODE: "segment_id",
    ASSET_DESCRIPTION_REQUIRED_CODE: "name",
    DERIVATIVE_DESCRIPTION_REQUIRED_CODE: "name",
    DERIVATIVE_OWNER_SHEET_MISSING_CODE: "name",
}


def _gap_text(gap: InputGap) -> str:
    if gap.code == UNREGISTERED_REFERENCE_CODE or gap.asset_type is None:
        return gap.name or ""
    return f"{gap.asset_type}: {gap.name}"


def input_refusal_error(refused: InputRefused) -> BadRequestError:
    """The sole translation of input rejection: use the first gap code as the error code and include all gaps in details.

    ``missing_text`` lists all reference gaps in assembly order for one-pass repair. ``gaps`` represents the same gaps
    with machine-readable code/type/name, persisted with task failure reasons for per-item Agent handling."""

    first = refused.reasons[0]
    params: dict[str, object] = {
        "missing_text": ", ".join(_gap_text(gap) for gap in refused.reasons if gap.code not in _SUBJECT_PARAMS),
        "gaps": [{"code": gap.code, "asset_type": gap.asset_type, "name": gap.name} for gap in refused.reasons],
    }
    if (subject := _SUBJECT_PARAMS.get(first.code)) is not None:
        params[subject] = first.name or ""
    return BadRequestError(first.code, **params)


def require_admitted_storyboard_references(project: dict, items: Iterable[object]) -> None:
    """Preflight storyboard-route generation: reject unregistered references or missing character/scene/prop images.

    Single storyboard-image, grid-image and image-to-video submissions share this function; reference-video equivalents
    live in :class:`lib.script.reference_video.request_projection.ReferenceUnitRequestProjector`. Previously these references
    were silently dropped, so users paid for missing-character images or videos without proper character references
    without knowing which name caused the problem.

    Report axes separately: unregistered references require registration/name changes; missing images require generation.
    One combined hint cannot identify these different actions. For multiple inputs report every gap together, avoiding
    repeated submissions just to discover all gaps."""

    admission = admit_storyboard_items(build_reference_catalog(project), items)
    if admission.unregistered:
        raise BadRequestError(UNREGISTERED_REFERENCE_CODE, missing_text=admission.unregistered_text())
    if admission.without_sheet:
        raise BadRequestError(SHEET_MISSING_CODE, missing_text=admission.without_sheet_text())


def reference_admission_problems(admission: ReferenceAdmission, *, unit_id: str) -> tuple[GenerationProblem, ...]:
    """Promote reference-admission gaps into the per-target generation-problem contract.

    Share decisions with single-submission ``require_admitted_storyboard_references``, but never short-circuit a batch:
    per-target outcomes expose all needed repairs at once instead of repair/resubmit cycles revealing one gap at a time."""

    problems: list[GenerationProblem] = []
    if admission.unregistered:
        problems.append(
            GenerationProblem(
                code=UNREGISTERED_REFERENCE_CODE,
                detail=f"引用了未登记的资产名: {admission.unregistered_text()}",
                action=GenerationAction.GENERATE_DEPENDENCY,
                params={"unit_id": unit_id, "missing_text": admission.unregistered_text()},
            )
        )
    if admission.without_sheet:
        problems.append(
            GenerationProblem(
                code=SHEET_MISSING_CODE,
                detail=f"引用的资产没有资产图: {admission.without_sheet_text()}",
                action=GenerationAction.GENERATE_DEPENDENCY,
                params={"unit_id": unit_id, "missing_text": admission.without_sheet_text()},
            )
        )
    return tuple(problems)
