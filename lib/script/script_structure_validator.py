"""Pure script-structure validation.

Centralize dict well-formedness: take a dict and return `ValidationResult`, without disk or project-state
dependencies. Central persistence, tests, and future writers share this implementation.

Validate structural Pydantic models (`lib.script.script_models`), not filesystem-aware `DataValidator`,
which reads disk and rejects legitimate incomplete drafts lacking images. Models encode every structural
constraint (required fields, `duration_seconds` bounds, IDs, prompt shapes, reference shots↔duration consistency).
This validator selects models and converts Pydantic `ValidationError` into `ValidationResult`, never
duplicating constraints. Model changes directly change validation as the single source of truth.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

# pydantic does not re-export ErrorDetails; depend directly on pydantic-core.
from pydantic_core import ErrorDetails

from arcreel_market_core.validation_messages import ValidationMessage
from lib.infra.validation_messages import ValidationResult
from lib.script.script_models import (
    AdEpisodeScript,
    DramaEpisodeScript,
    NarrationEpisodeScript,
    ReferenceVideoScript,
)
from lib.script.script_skeleton import resolve_script_kind


class ScriptStructureValidationError(ValueError):
    """Invalid script structure; carries `ValidationResult` for routers to produce localized 4xx responses."""

    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        super().__init__("; ".join(result.errors) or "script structure invalid")


_KIND_MODEL: dict[str, type[BaseModel]] = {
    "video_units": ReferenceVideoScript,
    "scenes": DramaEpisodeScript,
    "segments": NarrationEpisodeScript,
    "shots": AdEpisodeScript,
}


def _select_model(script: dict[str, Any]) -> type[BaseModel]:
    """Select script models through `script_skeleton.resolve_script_kind` forensic resolution, shared with
    editing and persistence metadata recalculation. Keep the kind→model mapping (`_KIND_MODEL`)
    local rather than placing models in the registry.

    **Data shape first** (see `resolve_script_kind`): reference only when video_units is the sole array,
    otherwise content_mode → segments/scenes (empty drama scenes still use DramaEpisodeScript).
    ``generation_mode`` never participates: generation callers (server/media_tools/videos.py, etc.) dispatch
    by generation_mode themselves; this function chooses only **structural-validation** Pydantic models.
    """
    return _KIND_MODEL[resolve_script_kind(script)]


def _format_error(err: ErrorDetails) -> str:
    loc = ".".join(str(part) for part in err.get("loc", ()))
    msg = err.get("msg", "")
    return f"{loc}: {msg}" if loc else str(msg)


def validate_script_structure(script: dict[str, Any]) -> ValidationResult:
    """Validate script dict structure, returning `ValidationResult`.

    Pure: no disk reads, file-reference checks, or cross-project.json character/scene-name consistency
    (those belong to archive-layer `DataValidator.validate_project_tree`).
    """
    model = _select_model(script)
    try:
        model.model_validate(script)
    except ValidationError as exc:
        # Pydantic errors are final text without translatable structure; include through the same message carrier's literal channel.
        return ValidationResult(
            valid=False,
            error_messages=[ValidationMessage.literal(_format_error(e)) for e in exc.errors()],
        )
    return ValidationResult(valid=True)
