"""Structural admission for AI operations: one predicate per operation, shared by production status and operation entry points (``docs/adr/0091``).

Admission belongs to AI operations, not content or pipeline positions. Predicates check only their
own operation inputs. UI disabled reasons, Agent rejection reasons, and suggested-next-step evidence
share the same conclusion, using a stable closed set of reason codes.

Compute **structural admission** only: read persisted project-directory content without quotes,
provider queries, or video-request facts. **Submission admission** requiring external facts still runs
at preview/submission (batch admission: ``docs/adr/0061``). Generation reference admission remains
``docs/adr/0073``, handled by ``lib.references.reference_admission``.

Predicates take facts, not paths. Shared fact functions here retrieve each fact consistently for
production status and operation entry points reading disk, rather than duplicating source-presence criteria."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from lib.episode.episode_ledger import SourceDoc
from lib.episode.episode_paths import episode_source_path
from lib.episode.episode_sources import SourceOrigin, episode_source_origin
from lib.script.script_models import PENDING_AUTHORING_FIELD
from lib.script.script_skeleton import SKELETONS


class AdmissionState(StrEnum):
    """Current admission conclusion for one AI operation."""

    ADMITTED = "admitted"
    REFUSED = "refused"
    #: Operation inapplicable to this project type (e.g. ad/short-film has no episode planning), not missing inputs.
    NOT_APPLICABLE = "not_applicable"


class AdmissionReason(StrEnum):
    """Closed set of inability reason codes; UI localizes by code, Agents paraphrase by code."""

    NOT_APPLICABLE = "operation_not_applicable"
    WHOLE_SOURCE_MISSING = "whole_source_missing"
    REPLAN_CANDIDATE_PENDING = "replan_candidate_pending"
    EPISODE_SOURCE_MISSING = "episode_source_missing"
    FORMAL_SCRIPT_MISSING = "formal_script_missing"
    FORMAL_SCRIPT_EXISTS = "formal_script_exists"
    NO_PENDING_AUTHORING = "no_pending_authoring"
    PROMPT_AUTHORING_DRAFT_PENDING = "prompt_authoring_draft_pending"
    AD_INPUTS_MISSING = "ad_brief_and_products_missing"
    NO_AVAILABLE_VIDEO = "no_available_video"


class OperationAdmission(BaseModel):
    """Structural admission for one AI operation: ``reason`` empty when allowed, required in the other two states."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: AdmissionState
    reason: AdmissionReason | None = None

    @property
    def admitted(self) -> bool:
        return self.state is AdmissionState.ADMITTED


ADMITTED = OperationAdmission(state=AdmissionState.ADMITTED)
NOT_APPLICABLE = OperationAdmission(state=AdmissionState.NOT_APPLICABLE, reason=AdmissionReason.NOT_APPLICABLE)


def _refused(reason: AdmissionReason) -> OperationAdmission:
    return OperationAdmission(state=AdmissionState.REFUSED, reason=reason)


# ---------------------------------------------------------------------------
# Fact functions: sole shared definitions used by production status and operation entry points
# ---------------------------------------------------------------------------


def whole_source_present(docs: Iterable[SourceDoc]) -> bool:
    """Whole-source text available for episode planning: registered whole-source files contain non-whitespace text.

    ``docs`` comes from :func:`lib.episode.episode_sources.discover_sources`; unregistered files do not count."""
    return any(doc.text.strip() for doc in docs)


def episode_source_present(project_path: Path, episode: int, entry: Mapping[str, Any] | None) -> bool:
    """Episode has non-whitespace source: ledger records a cut/own-source episode and ``source/episode_N.txt`` is non-whitespace.

    Episodes absent from the ledger (``entry`` None) or recorded as sourceless do not count even if a
    same-name file happens to exist in ``source/``."""
    if entry is None or episode_source_origin(entry) is SourceOrigin.NONE:
        return False
    path = episode_source_path(project_path, episode)
    if path.is_symlink() or not path.is_file():
        return False
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeDecodeError):
        return False


def ad_inputs_present(project: Mapping[str, Any]) -> bool:
    """At least one of creative inspiration or products is provided for an ad/short film."""
    brief = project.get("brief")
    products = project.get("products")
    return (isinstance(brief, str) and bool(brief.strip())) or (isinstance(products, Mapping) and bool(products))


def pending_authoring_entry_ids(items: Sequence[Any], kind: str | None) -> list[str]:
    """IDs marked pending authoring in formal scripts, in script order; prompt authoring fills all four skeletons."""
    if kind not in SKELETONS:
        return []
    id_field = SKELETONS[kind].id_field
    return [
        str(item[id_field])
        for item in items
        if isinstance(item, Mapping)
        and isinstance(item.get(id_field), str)
        and item[id_field]
        and item.get(PENDING_AUTHORING_FIELD) is True
    ]


# ---------------------------------------------------------------------------
# Admission predicates: one per AI operation
# ---------------------------------------------------------------------------


def admit_plan_episodes(
    content_mode: object, *, whole_source: bool, replan_pending: bool = False
) -> OperationAdmission:
    """AI episode planning: whole-source text exists and no unresolved replan candidate remains."""
    if content_mode == "ad":
        return NOT_APPLICABLE
    if not whole_source:
        return _refused(AdmissionReason.WHOLE_SOURCE_MISSING)
    return _refused(AdmissionReason.REPLAN_CANDIDATE_PENDING) if replan_pending else ADMITTED


def admit_script_plan(content_mode: object, *, episode_source: bool) -> OperationAdmission:
    """AI script planning: this episode has episode-source text."""
    if content_mode == "ad":
        return NOT_APPLICABLE
    return ADMITTED if episode_source else _refused(AdmissionReason.EPISODE_SOURCE_MISSING)


def admit_author_prompts(
    *,
    formal_script: bool,
    pending_ids: Sequence[str],
    draft_pending: bool,
    explicit_ids: Sequence[str] = (),
) -> OperationAdmission:
    """Prompt authoring: formal scripts have pending entries; explicitly targeted gap-filling/rewriting ignores pending markers.

    Handle existing authoring drafts first, since new authoring results have nowhere to land."""
    if draft_pending:
        return _refused(AdmissionReason.PROMPT_AUTHORING_DRAFT_PENDING)
    if not formal_script:
        return _refused(AdmissionReason.FORMAL_SCRIPT_MISSING)
    if pending_ids or explicit_ids:
        return ADMITTED
    return _refused(AdmissionReason.NO_PENDING_AUTHORING)


def admit_ad_script(
    content_mode: object, *, formal_script: bool, ad_inputs: bool, regenerate: bool = False
) -> OperationAdmission:
    """AI ad/short-film script generation: at least one inspiration/product input; existing formal scripts require explicit whole-script redo.

    Report missing inputs before existing formal scripts: ``formal_script_exists`` guarantees complete
    inputs for whole-script redo."""
    if content_mode != "ad":
        return NOT_APPLICABLE
    if not ad_inputs:
        return _refused(AdmissionReason.AD_INPUTS_MISSING)
    if formal_script and not regenerate:
        return _refused(AdmissionReason.FORMAL_SCRIPT_EXISTS)
    return ADMITTED


def admit_edit_timeline(*, available_videos: int) -> OperationAdmission:
    """Editing (create timeline / ask Agent to edit): at least one usable video (current or stale) in this episode."""
    return ADMITTED if available_videos > 0 else _refused(AdmissionReason.NO_AVAILABLE_VIDEO)


__all__ = [
    "ADMITTED",
    "NOT_APPLICABLE",
    "AdmissionReason",
    "AdmissionState",
    "OperationAdmission",
    "ad_inputs_present",
    "admit_ad_script",
    "admit_author_prompts",
    "admit_edit_timeline",
    "admit_plan_episodes",
    "admit_script_plan",
    "episode_source_present",
    "pending_authoring_entry_ids",
    "whole_source_present",
]
