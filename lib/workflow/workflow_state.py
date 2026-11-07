"""Authoritative workflow status for ArcReel projects.

Status computation is read-only, without persistence."""

from __future__ import annotations

import json
import logging
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lib.artifacts.artifact_activation import ArtifactComparer, ArtifactCurrencyResolver, RegisteredArtifactResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestError, ArtifactStatus
from lib.edit_timeline.errors import EditTimelineError
from lib.edit_timeline.store import EditTimelineStore
from lib.episode.episode_ledger import (
    SourceDoc,
    normalize_source_text,
    parse_positive_episode_num,
)
from lib.episode.episode_paths import episode_source_relpath
from lib.episode.episode_replan import replan_candidate
from lib.episode.episode_sources import (
    legacy_cut_episode_ids,
    source_fingerprints_diverged,
    source_remaining,
    stored_source_remaining,
    whole_source_files,
)
from lib.infra.content_digest import prefixed_canonical_json_digest
from lib.project.asset_derivatives import derivative_artifact_key, derivative_table, split_derivative_artifact_id
from lib.project.asset_types import ASSET_SPECS, asset_name_comparison_key
from lib.project.data_validator import DataValidator
from lib.project.episode_asset_references import episode_referenced_assets
from lib.project.project_manager import ProjectManager, is_reference_video_project
from lib.project.project_migration_failure import (
    MIGRATION_FAILURE_CODE,
    MIGRATION_FAILURE_FILENAME,
    MigrationFailureRecord,
    load_migration_verdict,
)
from lib.project.project_migration_report import MigrationReport, visible_migration_report
from lib.project.source_revision import SourceRevisionResult, SourceScope, compute_source_revision
from lib.references.reference_admission import admit_references, admit_storyboard_items
from lib.references.reference_catalog import build_reference_catalog
from lib.script import script_review
from lib.script.draft_quarantine import (
    QUARANTINE_KIND_PROMPT_AUTHORING,
    quarantine_exists,
    quarantine_path,
    read_quarantine,
)
from lib.script.reference_video.text_parser import derive_references_from_text
from lib.script.script_models import get_generated_assets, script_duration_total
from lib.script.script_skeleton import SKELETONS, STORYBOARD_ITEM_ID_PATTERN, ensure_route_skeleton, resolve_kind_items
from lib.speech.narration_config import USE_TTS, project_narration_delivery
from lib.workflow.operation_admission import (
    AdmissionReason,
    AdmissionState,
    OperationAdmission,
    ad_inputs_present,
    admit_ad_script,
    admit_author_prompts,
    admit_edit_timeline,
    admit_plan_episodes,
    admit_script_plan,
    episode_source_present,
    pending_authoring_entry_ids,
    whole_source_present,
)
from lib.workflow.workflow_rules import workflow_rule

logger = logging.getLogger(__name__)


class WorkflowRequestError(ValueError):
    """Caller-supplied query parameters are invalid.

    Contrast persisted corruption (script skeletons, content_mode / generation_mode combinations, etc.),
    also raised through the ``ValueError`` family but attributable to server data, not this request.
    Consumers distinguish 400 / invalid_request from server-failure reporting accordingly, avoiding
    misdirecting troubleshooting toward callers."""


class WorkflowProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_mode: str
    generation_mode: str
    grid_storyboard: bool


class WorkflowTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode: int
    script: str
    script_filename: str
    source: str


class WorkflowBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    path: str
    reason: str


class WorkflowActionType(StrEnum):
    """Closed set of ``WorkflowNextAction.type`` values.

    Combines three sources: this module's next actions/alternatives, additional actions injected during
    ``lib.workflow.workflow_plan`` projection, and ``lib.generation.generation_result.GenerationAction``
    returned unchanged by rejected batch admission. Consumers (frontend unions, controlled profile action
    tables, action translations) derive from this enum. New members automatically enter all coverage checks
    without manually maintaining another list."""

    # Suggested next actions and alternatives produced by this module
    NONE = "none"
    COLLECT_PROJECT_INPUT = "collect_project_input"
    CREATE_EPISODE = "create_episode"
    DRAFT_SELLING_POINTS = "draft_selling_points"
    PLAN_EPISODES = "plan_episodes"
    RESET_EPISODE_PLANNING = "reset_episode_planning"
    RESOLVE_DRAFT = "resolve_draft"
    PREPARE_SCRIPT_PLAN = "prepare_script_plan"
    START_BLANK_SCRIPT = "start_blank_script"
    PROVIDE_EPISODE_SOURCE = "provide_episode_source"
    CONFIRM_SCRIPT_PLAN = "confirm_script_plan"
    GENERATE_SCRIPT = "generate_script"
    ADD_SCRIPT_ITEMS = "add_script_items"
    AUTHOR_PROMPTS = "author_prompts"
    GENERATE_ASSET_SHEETS = "generate_asset_sheets"
    GENERATE_STORYBOARDS = "generate_storyboards"
    GENERATE_GRID = "generate_grid"
    REPAIR_VIDEO_UNITS = "repair_video_units"
    GENERATE_VIDEOS = "generate_videos"
    CREATE_EDIT_TIMELINE = "create_edit_timeline"

    # Data-upgrade failures report only this action at every stage.
    RETRY_PROJECT_MIGRATION = "retry_project_migration"

    # Actions injected during ``build_workflow_plan`` projection
    PATCH_EPISODE_SCRIPT = "patch_episode_script"

    # ``GenerationAction`` closed set; batch-admission rejection/task failure returns it unchanged as next_action.
    RETRY = "retry"
    FIX_INPUT = "fix_input"
    GENERATE_DEPENDENCY = "generate_dependency"
    WAIT_FOR_TASK = "wait_for_task"
    REPLAN_UNIT = "replan_unit"
    CONFIRM_REQUEST_DURATION = "confirm_request_duration"
    CONFIGURE_PROVIDER = "configure_provider"
    REPAIR_ARTIFACT_STATE = "repair_artifact_state"
    RETRY_ARTIFACT_DOWNLOAD = "retry_artifact_download"


class WorkflowNextAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: WorkflowActionType
    args: dict[str, Any] = Field(default_factory=dict)
    requested_ids: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    reason: str


class WorkflowOperation(BaseModel):
    """Current structural admission for one AI operation, sharing entry-point predicates (``lib.workflow.operation_admission``)."""

    model_config = ConfigDict(extra="forbid")

    state: AdmissionState
    reason: AdmissionReason | None = None


class WorkflowDraft(BaseModel):
    """One draft present on the target episode; ``needs_repair`` distinguishes quarantined drafts from Agent-editable drafts."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    path: str
    needs_repair: bool


class WorkflowContent(BaseModel):
    """Current content state without deciding which step should come next.

    Episode fields have values only with a target episode. ``episode_plan_stale`` means episode planning
    is stale and script planning awaits rebuilding: state it here without adding it to suggested next actions.

    ``episode_complete`` indicates target-episode completion (``is_episode_complete``); completed episodes
    may still have within-episode suggestions, e.g. pending authoring or missing sheets. ``project_complete``
    appears only in queries without a specified episode: every episode complete, none awaiting replanning,
    and no remaining whole-source text."""

    model_config = ConfigDict(extra="forbid")

    episode_count: int
    whole_source: Literal["present", "absent", "not_applicable"]
    source_remaining: bool
    ad_inputs: Literal["present", "absent", "not_applicable"]
    products_without_selling_points: list[str] = Field(default_factory=list)
    episode_source: Literal["present", "absent", "not_applicable"] | None = None
    episode_plan_stale: bool = False
    expected_stale_script_plan_revision: str | None = None
    drafts: list[WorkflowDraft] = Field(default_factory=list)
    formal_script: Literal["present", "absent", "invalid"] | None = None
    script_item_count: int | None = None
    pending_authoring_ids: list[str] = Field(default_factory=list)
    needs_replan_ids: list[str] = Field(default_factory=list)
    #: Referenced characters/scenes/props (including derivatives) without sheets, matching generation admission (ADR 0073).
    referenced_assets_without_sheet: list[str] = Field(default_factory=list)
    #: Referenced names absent from registration; generation rejects them.
    unregistered_references: list[str] = Field(default_factory=list)
    #: Referenced assets (including derivatives) with outdated sheets; informational, excluded from next suggestions.
    referenced_asset_sheets_stale: list[str] = Field(default_factory=list)
    #: Referenced assets (including derivatives) lacking descriptions and unable to generate sheets; informational, excluded from next suggestions.
    referenced_assets_without_description: list[str] = Field(default_factory=list)
    episode_complete: bool = False
    project_complete: bool = False


class WorkflowStatus(BaseModel):
    """Shared response model serialized unchanged by REST and MCP adapters.

    Production status states three things (``docs/adr/0091``): ``content`` and ``artifacts`` describe current
    content, ``operations`` describes each AI operation's availability/reason, and ``next_action`` suggests
    the next step, with branch alternatives in ``next_alternatives``. ``blockers`` means only whole-project
    unavailability; content-data problems enter ``issues`` as information without blocking other content."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    project_revision: str
    source_revision: str | None
    project: WorkflowProject
    target: WorkflowTarget | None
    blockers: list[WorkflowBlocker]
    issues: list[WorkflowBlocker] = Field(default_factory=list)
    content: WorkflowContent | None
    operations: dict[str, WorkflowOperation] = Field(default_factory=dict)
    gates: dict[str, dict[str, Any]]
    artifacts: dict[str, dict[str, Any]]
    next_action: WorkflowNextAction
    next_alternatives: list[WorkflowNextAction] = Field(default_factory=list)
    migration_report: MigrationReport | None = None
    """What the last migration adjusted or could not carry over, supplied only until user acknowledgment; informational only, without affecting state or blocking."""


#: Issue code for unreadable timeline directories or unparseable timeline files.
INVALID_EDIT_TIMELINES_CODE = "invalid_edit_timelines"
#: ``next_action`` reason when an episode is complete with no other suggestions; next step is ``none``.
EPISODE_COMPLETE_REASON = "episode has an edit timeline"
#: ``next_action`` reason when all scoped episodes are complete and source text fully arranged.
ALL_EPISODES_COMPLETE_REASON = "every episode has an edit timeline"


def workflow_finished(status: WorkflowStatus) -> bool:
    """Suggested next actions have ended: episode queries mean completion with no other within-episode suggestions; project queries mean all complete.

    This does not define episode completion: completed episodes may still have suggestions such as pending
    authoring. Use ``content.episode_complete`` for their completion."""

    return status.next_action.type is WorkflowActionType.NONE and status.next_action.reason in {
        EPISODE_COMPLETE_REASON,
        ALL_EPISODES_COMPLETE_REASON,
    }


#: Per-episode script artifact-derived state: usable formal script means generated, only script_plan means segmented.
EpisodeScriptStatus = Literal["none", "segmented", "generated"]

#: Broad-view coarse episode progress derived from artifact counts.
EpisodeProductionStatus = Literal["draft", "scripted", "in_production", "completed"]
#: Summary currency rules: ``verified`` compares canonical states per artifact; ``registered`` checks manifest registration/presence only.
ProjectSummaryCurrency = Literal["verified", "registered"]


class ArtifactCount(BaseModel):
    """Artifact-group counts: available = current ∪ stale, with stale counted separately.

    Do not subtract stale from available: artifacts older than current content remain usable (ADR 0062).
    Staleness suggests deciding whether to regenerate, not a gap."""

    model_config = ConfigDict(extra="forbid")

    total: int
    available: int
    stale: int

    @classmethod
    def zero(cls) -> ArtifactCount:
        return cls(total=0, available=0, stale=0)

    @classmethod
    def of(cls, collection: Mapping[str, Any], *, total: int) -> ArtifactCount:
        stale = len(collection["stale_ids"])
        return cls(total=total, available=len(collection["current_ids"]) + stale, stale=stale)


def is_episode_complete(videos: ArtifactCount, *, has_edit_timeline: bool) -> bool:
    """Episode completion: all videos usable (current ∪ stale) and at least one editing timeline.

    Sole episode-completion criterion shared by episode progress, project cards, production-status
    ``episode_complete``, and cross-episode selection. Storyboards, pending authoring, units awaiting
    replanning, and asset sheets do not participate; they are only within-episode next suggestions."""

    return videos.total > 0 and videos.available >= videos.total and has_edit_timeline


class EpisodeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode: int
    script_status: EpisodeScriptStatus
    status: EpisodeProductionStatus
    # Episode content size: storyboard-image-to-video reports shots, reference-to-video reports video units,
    # consistent across three content modes; compute script-entry counts on read, never persist.
    item_count: int
    duration_seconds: int
    storyboards: ArtifactCount
    videos: ArtifactCount


class EpisodesSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    scripted: int
    in_production: int
    completed: int


class ProjectSummary(BaseModel):
    """Broad-view project projection: available asset counts, episode summaries, and per-episode progress.

    Same source as ``WorkflowStatus`` at different granularity: status answers what to do next; this answers
    how many episodes dozens of projects finished and how many artifacts are usable. Project progress is
    episode progress. Read only project metadata, episode scripts, manifest, and timeline filenames, excluding
    source bodies and source revisions (sha256), or listing N projects would read N novels. Do not parse
    timelines; production status alone reports corrupt files as issues.

    Only source text determines unplanned-source availability. Read the ledger-writing commands' conclusion
    in ``project.json`` (``source_remaining``), matching production status's continue-planning criterion.
    External source edits before ledger updates may temporarily disagree between these views. Artifact
    rules themselves agree: available/stale both come from the same manifest.

    Two currency rules (``ProjectSummaryCurrency``): ``verified`` compares each artifact with canonical
    state, distinguishing current/stale; ``registered`` checks only manifest registration and file presence,
    never producing stale through comparison. The former serves individual details/episode cards, the latter
    project lists without reading artifact content for N projects. Reference-to-video replan shells
    (``needs_replan``) count stale under both: a script-entry marker, not an artifact-comparison result."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    needs_repair: bool
    repair_reason: str | None
    #: Sheet counts by ``ASSET_SPECS`` asset-type keys; new types automatically enter projection.
    assets: dict[str, ArtifactCount]
    episodes_summary: EpisodesSummary
    episodes: list[EpisodeSummary]
    #: Remaining unplanned whole-source text prevents project completion even if all episodes are complete; next action continues planning.
    source_remaining: bool


class EpisodeNextStep(BaseModel):
    """Suggested next step for one ledger episode in project-level episode lists; matches episode-query ``next_action``."""

    model_config = ConfigDict(extra="forbid")

    episode: int
    #: Episode plan invalidated by source replanning: next step ``none``, awaiting rebuild.
    plan_stale: bool
    next_action: WorkflowNextAction


@dataclass(frozen=True)
class _SharedWorkflowFacts:
    source: SourceRevisionResult | None
    planning_sources: tuple[SourceDoc, ...]
    source_remaining: bool
    sheets: dict[str, dict[str, Any]]
    episodes: list[tuple[int, dict[str, Any]]]
    currency: ArtifactCurrencyResolver | None
    blockers: tuple[WorkflowBlocker, ...]
    issues: tuple[WorkflowBlocker, ...]
    whole_source: bool


def _project_revision(project: Mapping[str, Any]) -> str:
    return prefixed_canonical_json_digest(dict(project))


def _action(
    action_type: WorkflowActionType,
    reason: str,
    *,
    args: dict[str, Any] | None = None,
    ids: list[str] | None = None,
    requires_confirmation: bool = False,
) -> WorkflowNextAction:
    return WorkflowNextAction(
        type=action_type,
        args=args or {},
        requested_ids=ids or [],
        requires_confirmation=requires_confirmation,
        reason=reason,
    )


def planning_docs(project: Mapping[str, Any], source: SourceRevisionResult | None) -> tuple[SourceDoc, ...]:
    """Convert sources read for revision computation into normalized whole-source text in ledger coordinates and project-registered file order.

    Read sources only once per status query: ``compute_source_revision`` already loaded every source into
    memory; derive normalized full text for episode arrangement from that read without rereading disk."""

    if source is None or source.blockers:
        return ()
    by_rel = {unicodedata.normalize("NFC", f"source/{document.name}"): document for document in source.documents}
    docs: list[SourceDoc] = []
    for rel in whole_source_files(project):
        document = by_rel.get(unicodedata.normalize("NFC", rel))
        if document is not None:
            docs.append(SourceDoc(rel_path=rel, text=normalize_source_text(document.text)))
    return tuple(docs)


def _empty_collection() -> dict[str, list[str]]:
    return {"current_ids": [], "missing_ids": [], "stale_ids": []}


def _not_applicable_collection() -> dict[str, Any]:
    return {"state": "not_applicable", **_empty_collection()}


def _episode_production_status(
    script_status: EpisodeScriptStatus,
    storyboards: ArtifactCount,
    videos: ArtifactCount,
    *,
    has_edit_timeline: bool,
) -> EpisodeProductionStatus:
    """Completion uses ``is_episode_complete``; any usable storyboard/video means production underway."""

    if script_status != "generated":
        return "draft"
    if is_episode_complete(videos, has_edit_timeline=has_edit_timeline):
        return "completed"
    if storyboards.available + videos.available:
        return "in_production"
    return "scripted"


def _asset_bucket_total(project: Mapping[str, Any], bucket_key: str) -> int:
    bucket = project.get(bucket_key)
    return len(bucket) if isinstance(bucket, Mapping) else 0


def _episodes_summary(episodes: list[EpisodeSummary]) -> EpisodesSummary:
    return EpisodesSummary(
        total=len(episodes),
        scripted=sum(1 for episode in episodes if episode.script_status == "generated"),
        in_production=sum(1 for episode in episodes if episode.status == "in_production"),
        completed=sum(1 for episode in episodes if episode.status == "completed"),
    )


class WorkflowStateService:
    """Calculate the first unmet workflow condition from durable project facts."""

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

    @staticmethod
    def _artifact_state(
        resolver: ArtifactComparer,
        key: ArtifactKey,
        artifact_path: str,
        issues: list[WorkflowBlocker],
    ) -> str:
        try:
            comparison = resolver.compare(key, artifact_path=artifact_path)
        except (ArtifactManifestError, OSError, RuntimeError, TypeError, ValueError) as exc:
            issues.append(
                WorkflowBlocker(
                    code="artifact_currency_unavailable",
                    path=artifact_path,
                    reason=str(exc),
                )
            )
            return ArtifactStatus.BLOCKED.value
        if comparison.status is ArtifactStatus.BLOCKED:
            assert comparison.blocker is not None
            issues.append(
                WorkflowBlocker(
                    code=comparison.blocker.code,
                    path=comparison.blocker.path,
                    reason=comparison.blocker.detail,
                )
            )
        return comparison.status.value

    @classmethod
    def _classify_artifact(
        cls,
        collection: dict[str, Any],
        *,
        resolver: ArtifactComparer,
        key: ArtifactKey,
        artifact_path: str,
        resource_id: str,
        issues: list[WorkflowBlocker],
    ) -> None:
        state = cls._artifact_state(resolver, key, artifact_path, issues)
        if state == ArtifactStatus.BLOCKED.value:
            collection["state"] = "blocked"
        else:
            collection[f"{state}_ids"].append(resource_id)

    @staticmethod
    def _source_revision(
        project_path: Path, project: dict[str, Any], mode: str, issues: list[WorkflowBlocker]
    ) -> SourceRevisionResult | None:
        if mode == "ad":
            return None
        source = compute_source_revision(project_path, project, SourceScope(kind="all"))
        issues.extend(WorkflowBlocker(code=item.code, path=item.path, reason=item.reason) for item in source.blockers)
        return source

    def _asset_sheets(
        self,
        project_path: Path,
        project: dict[str, Any],
        issues: list[WorkflowBlocker],
        resolver: ArtifactComparer | None,
    ) -> dict[str, dict[str, Any]]:
        collections: dict[str, dict[str, Any]] = {}
        for asset_type, spec in ASSET_SPECS.items():
            collection: dict[str, Any] = _empty_collection()
            bucket = project.get(spec.bucket_key, {})
            if not isinstance(bucket, Mapping):
                issues.append(
                    WorkflowBlocker(
                        code="invalid_asset_bucket",
                        path=spec.bucket_key,
                        reason=f"{spec.bucket_key} must be an object",
                    )
                )
                collection["state"] = "blocked"
                collections[asset_type] = collection
                continue
            for name, item in bucket.items():
                if not isinstance(name, str) or not isinstance(item, Mapping):
                    issues.append(
                        WorkflowBlocker(
                            code="invalid_asset_entry",
                            path=f"{spec.bucket_key}.{name}",
                            reason="asset entries must be named objects",
                        )
                    )
                    collection["state"] = "blocked"
                    collection["current_ids"] = []
                    collection["missing_ids"] = []
                    break
                path = item.get(spec.sheet_field)
                if resolver is not None and isinstance(path, str) and path:
                    self._classify_artifact(
                        collection,
                        resolver=resolver,
                        key=ArtifactKey.asset_sheet(asset_type, asset_name_comparison_key(name)),
                        artifact_path=path,
                        resource_id=name,
                        issues=issues,
                    )
                else:
                    collection["missing_ids"].append(name)
            collections[asset_type] = collection
        return collections

    @staticmethod
    def _episodes(project: dict[str, Any], issues: list[WorkflowBlocker]) -> list[tuple[int, dict[str, Any]]]:
        raw = project.get("episodes")
        if not isinstance(raw, list):
            issues.append(
                WorkflowBlocker(code="invalid_episode_ledger", path="episodes", reason="episodes must be an array")
            )
            return []
        parsed: list[tuple[int, dict[str, Any]]] = []
        seen: set[int] = set()
        for index, entry in enumerate(raw):
            if not isinstance(entry, dict):
                issues.append(
                    WorkflowBlocker(
                        code="invalid_episode_entry",
                        path=f"episodes[{index}]",
                        reason="episode entry must be an object",
                    )
                )
                continue
            number = parse_positive_episode_num(entry.get("episode"))
            if number is None or number in seen:
                issues.append(
                    WorkflowBlocker(
                        code="invalid_episode_number",
                        path=f"episodes[{index}].episode",
                        reason="episode number must be a unique positive integer",
                    )
                )
                continue
            seen.add(number)
            ledger_status = entry.get("ledger_status")
            if ledger_status is not None and not isinstance(ledger_status, str):
                issues.append(
                    WorkflowBlocker(
                        code="invalid_ledger_status",
                        path=f"episodes[{index}].ledger_status",
                        reason="ledger_status must be a string",
                    )
                )
                continue
            parsed.append((number, entry))
        return parsed

    def _load_script_artifacts(
        self,
        project_path: Path,
        project_name: str,
        project: dict[str, Any],
        target: WorkflowTarget,
        issues: list[WorkflowBlocker],
        resolver: ArtifactCurrencyResolver | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
        path = target.script
        state = ArtifactStatus.CURRENT.value
        if resolver is not None:
            state = self._artifact_state(resolver, ArtifactKey.episode_script(target.episode), path, issues)
            if state not in {ArtifactStatus.CURRENT.value, ArtifactStatus.STALE.value}:
                return {"state": state, "path": path}, [], None
        try:
            script: Any = self.pm.load_script_readonly(project_name, path)
        except FileNotFoundError:
            return {"state": "missing", "path": path}, [], None
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            issues.append(WorkflowBlocker(code="invalid_script", path=path, reason=str(exc)))
            return {"state": "blocked", "path": path}, [], None
        if not isinstance(script, dict):
            issues.append(WorkflowBlocker(code="invalid_script", path=path, reason="script must be an object"))
            return {"state": "blocked", "path": path}, [], None
        script_episode = script.get("episode")
        if script_episode != target.episode or isinstance(script_episode, bool):
            issues.append(
                WorkflowBlocker(
                    code="script_episode_mismatch",
                    path=f"{path}.episode",
                    reason=f"script episode must equal target episode {target.episode}",
                )
            )
            return {"state": "blocked", "path": path}, [], None
        try:
            kind = ensure_route_skeleton(script, project.get("content_mode"), project.get("generation_mode"))
        except ValueError as exc:
            issues.append(WorkflowBlocker(code="invalid_project_mode", path="content_mode", reason=str(exc)))
            return {"state": "blocked", "path": path}, [], None
        raw_items, id_field, _kind = resolve_kind_items(script, kind=kind)
        # Empty formal scripts are valid (docs/adr/0091); nonempty requirements belong only to AI-generation output acceptance.
        if not isinstance(raw_items, list) or not all(isinstance(item, dict) for item in raw_items):
            issues.append(
                WorkflowBlocker(
                    code="invalid_script_collection",
                    path=f"{path}.{kind}",
                    reason=f"{kind} must be an array of objects",
                )
            )
            return {"state": "blocked", "path": path}, [], kind
        seen_ids: set[str] = set()
        for index, item in enumerate(raw_items):
            resource_id = item.get(id_field)
            if not isinstance(resource_id, str) or not resource_id:
                issues.append(
                    WorkflowBlocker(
                        code="invalid_script_id",
                        path=f"{path}.{kind}[{index}].{id_field}",
                        reason=f"{id_field} must be a non-empty string",
                    )
                )
                return {"state": "blocked", "path": path}, [], kind
            if kind != "video_units" and STORYBOARD_ITEM_ID_PATTERN.fullmatch(resource_id) is None:
                issues.append(
                    WorkflowBlocker(
                        code="invalid_script_id",
                        path=f"{path}.{kind}[{index}].{id_field}",
                        reason=f"invalid {id_field}: {resource_id}",
                    )
                )
                return {"state": "blocked", "path": path}, [], kind
            if resource_id in seen_ids:
                issues.append(
                    WorkflowBlocker(
                        code="duplicate_script_id",
                        path=f"{path}.{kind}[{index}].{id_field}",
                        reason=f"duplicate {id_field}: {resource_id}",
                    )
                )
                return {"state": "blocked", "path": path}, [], kind
            seen_ids.add(resource_id)
            duration = item.get("duration_seconds")
            duration_max = 300 if kind == "video_units" else 60
            replan_shell = (
                kind == "video_units"
                and item.get("needs_replan") is True
                and not str(item.get("text") or "").strip()
                and duration == 0
            )
            if (
                duration is not None
                and not replan_shell
                and (isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= duration_max)
            ):
                issues.append(
                    WorkflowBlocker(
                        code="invalid_script_structure",
                        path=f"{path}.{kind}[{index}].duration_seconds",
                        reason=f"duration_seconds must be an integer between 1 and {duration_max}",
                    )
                )
                return {"state": "blocked", "path": path}, [], kind
        validation = DataValidator(str(self.pm.projects_dir)).validate_episode_payload(
            project_path,
            project,
            script,
            validate_artifacts=False,
        )
        if not validation.valid:
            issues.append(
                WorkflowBlocker(
                    code="invalid_script_structure",
                    path=path,
                    reason="; ".join(validation.errors),
                )
            )
            return {"state": "blocked", "path": path}, [], kind
        return {"state": state, "path": path}, raw_items, kind

    @classmethod
    def _media_collection(
        cls,
        project_path: Path,
        items: list[dict[str, Any]],
        kind: str | None,
        field: str,
        *,
        episode: int,
        resolver: ArtifactComparer | None,
        issues: list[WorkflowBlocker],
    ) -> dict[str, Any]:
        collection: dict[str, Any] = _empty_collection()
        if kind is None:
            return collection
        id_field = SKELETONS[kind].id_field
        for item in items:
            resource_id = item.get(id_field)
            if not isinstance(resource_id, str) or not resource_id:
                continue
            if kind == "video_units" and item.get("needs_replan") is True:
                collection["stale_ids"].append(resource_id)
                continue
            artifact_path = get_generated_assets(item).get(field)
            if resolver is not None and isinstance(artifact_path, str) and artifact_path:
                key = (
                    ArtifactKey.episode_storyboard(episode, resource_id)
                    if field == "storyboard_image"
                    else ArtifactKey.episode_video(episode, resource_id)
                    if field == "video_clip"
                    else ArtifactKey.episode_audio(episode, resource_id)
                )
                cls._classify_artifact(
                    collection,
                    resolver=resolver,
                    key=key,
                    artifact_path=artifact_path,
                    resource_id=resource_id,
                    issues=issues,
                )
            else:
                collection["missing_ids"].append(resource_id)
        return collection

    def get_status(self, project_name: str, episode: int | None = None) -> WorkflowStatus:
        project_path = self.pm.get_project_path(project_name)
        try:
            project: Any = self.pm.load_project(project_name)
            if not isinstance(project, dict):
                raise ValueError("project.json must be an object")
        except (OSError, ValueError) as exc:
            failure = load_migration_verdict(project_path)
            if failure is not None:
                return self._migration_blocked_status({}, failure)
            return WorkflowStatus(
                project_revision="",
                source_revision=None,
                project=WorkflowProject(content_mode="unknown", generation_mode="unknown", grid_storyboard=False),
                target=None,
                blockers=[WorkflowBlocker(code="project_data_unavailable", path="project.json", reason=str(exc))],
                content=None,
                gates={},
                artifacts={},
                next_action=_action(WorkflowActionType.NONE, "project data cannot be read"),
            )
        failure = load_migration_verdict(project_path)
        if failure is not None:
            return self._migration_blocked_status(project, failure)
        shared = self._shared_facts(project_path, project)
        status = self._get_status(project_name, project, project_path, episode, shared)
        status.migration_report = visible_migration_report(project_path)
        return status

    def get_episode_next_steps(self, project_name: str) -> list[EpisodeNextStep]:
        """Give each episode's next step in ledger order.

        Whole-project unavailability (migration failure, unreadable project data, blockers) yields no episode
        next steps: return an empty list. Project-level production status explains why."""

        project_path = self.pm.get_project_path(project_name)
        try:
            project: Any = self.pm.load_project(project_name)
        except FileNotFoundError:
            raise
        except (OSError, ValueError):
            return []
        if not isinstance(project, dict) or load_migration_verdict(project_path) is not None:
            return []
        shared = self._shared_facts(project_path, project)
        if shared.blockers:
            return []
        steps: list[EpisodeNextStep] = []
        for pair in shared.episodes:
            status = self._episode_status(project_name, project, project_path, shared, pair)
            steps.append(
                EpisodeNextStep(
                    episode=pair[0],
                    plan_stale=status.content is not None and status.content.episode_plan_stale,
                    next_action=status.next_action,
                )
            )
        return steps

    def get_project_summary(
        self,
        project_name: str,
        *,
        preloaded_scripts: Mapping[str, dict[str, Any]] | None = None,
        currency: ProjectSummaryCurrency = "verified",
    ) -> ProjectSummary:
        """Broad-view project projection (``ProjectSummary``).

        ``preloaded_scripts`` keys use original ``episodes[].script_file`` values; hits reuse caller reads
        (project lists feed the same scripts to cover resolution), reading scripts once per episode per list request.

        ``currency`` chooses rules: ``verified`` compares canonical states per artifact, rebuilding every basis
        (reading manifests, hashing storyboard/reference images); ``registered`` loads the manifest once,
        checking only registration/presence without reading contents (presence probes one byte). Lists use
        the latter, other views the former."""

        project = self.pm.load_project(project_name)
        project_path = self.pm.get_project_path(project_name)
        failure = load_migration_verdict(project_path)
        if failure is not None:
            return self._migration_blocked_summary(project, self._episodes(project, []), failure)
        episodes = self._episodes(project, [])
        try:
            resolver: ArtifactComparer | None = (
                RegisteredArtifactResolver(project_path, project)
                if currency == "registered"
                else ArtifactCurrencyResolver(project_path)
            )
        except (ArtifactManifestError, OSError, RuntimeError, TypeError, ValueError):
            # Unreadable manifests never fall back to file existence as artifact presence: report zero usable here,
            # matching workbench artifact_currency_unavailable blocking.
            resolver = None
        assets = self._asset_counts(project_path, project, resolver)
        episode_summaries = [
            self._episode_summary(
                project_name,
                project,
                project_path,
                number,
                entry,
                resolver=resolver,
                preloaded_scripts=preloaded_scripts,
            )
            for number, entry in episodes
        ]
        return ProjectSummary(
            needs_repair=False,
            repair_reason=None,
            assets=assets,
            episodes_summary=_episodes_summary(episode_summaries),
            episodes=episode_summaries,
            source_remaining=stored_source_remaining(project),
        )

    def _asset_counts(
        self,
        project_path: Path,
        project: dict[str, Any],
        resolver: ArtifactComparer | None,
    ) -> dict[str, ArtifactCount]:
        sheets = self._asset_sheets(project_path, project, [], resolver)
        return {
            asset_type: ArtifactCount.of(
                sheets.get(asset_type, _empty_collection()),
                total=_asset_bucket_total(project, spec.bucket_key),
            )
            for asset_type, spec in ASSET_SPECS.items()
        }

    def _episode_summary(
        self,
        project_name: str,
        project: dict[str, Any],
        project_path: Path,
        number: int,
        entry: dict[str, Any],
        *,
        resolver: ArtifactComparer | None,
        preloaded_scripts: Mapping[str, dict[str, Any]] | None,
    ) -> EpisodeSummary:
        script_status = self._episode_script_status(project, project_path, number, entry, resolver)
        items: list[dict[str, Any]] = []
        kind: str | None = None
        if script_status == "generated":
            script = self._summary_script(project_name, entry, preloaded_scripts)
            if script is not None:
                try:
                    kind = ensure_route_skeleton(script, project.get("content_mode"), project.get("generation_mode"))
                except ValueError:
                    kind = None
                if kind is not None:
                    raw_items, _id_field, _kind = resolve_kind_items(script, kind=kind)
                    items = (
                        [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
                    )
        storyboards = (
            ArtifactCount.of(
                self._media_collection(
                    project_path,
                    items,
                    kind,
                    "storyboard_image",
                    episode=number,
                    resolver=resolver,
                    issues=[],
                ),
                total=len(items),
            )
            if project.get("generation_mode") == "storyboard"
            else ArtifactCount.zero()
        )
        videos = ArtifactCount.of(
            self._media_collection(
                project_path,
                items,
                kind,
                "video_clip",
                episode=number,
                resolver=resolver,
                issues=[],
            ),
            total=len(items),
        )
        return EpisodeSummary(
            episode=number,
            script_status=script_status,
            status=_episode_production_status(
                script_status,
                storyboards,
                videos,
                has_edit_timeline=EditTimelineStore(self.pm, project_name).has_documents(number),
            ),
            item_count=len(items),
            duration_seconds=script_duration_total(kind, items) if kind is not None else 0,
            storyboards=storyboards,
            videos=videos,
        )

    def _summary_script(
        self,
        project_name: str,
        entry: dict[str, Any],
        preloaded_scripts: Mapping[str, dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        script_file = entry.get("script_file")
        if not isinstance(script_file, str) or not script_file:
            return None
        if preloaded_scripts is not None and script_file in preloaded_scripts:
            return preloaded_scripts[script_file]
        try:
            return self.pm.load_script_readonly(project_name, script_file)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            # Listing projects survives one corrupt episode script, reporting zero artifacts for it; workbench production-status
            # queries report corruption as invalid_script blocking.
            return None

    def _episode_script_status(
        self,
        project: dict[str, Any],
        project_path: Path,
        number: int,
        entry: dict[str, Any],
        resolver: ArtifactComparer | None,
    ) -> EpisodeScriptStatus:
        """Derive episode script progress from script_plan/formal-script artifact states.

        Stale episode plans without rebuilt script plans return none because downstream work must be redone
        (source ranges invalidated by replanning). Share ``_stale_episode_plan`` with the top bar; after rebuilding,
        derive normal artifact states."""

        if project.get("content_mode") != "ad" and self._stale_episode_plan(project_path, project, number, entry)[0]:
            return "none"
        script_file = entry.get("script_file")
        if resolver is not None and isinstance(script_file, str) and script_file:
            state = self._artifact_state(resolver, ArtifactKey.episode_script(number), script_file, [])
            if state in {ArtifactStatus.CURRENT.value, ArtifactStatus.STALE.value}:
                return "generated"
        script_plan = script_review.script_plan_path(project_path, project, number)
        if script_plan is None:
            return "none"
        if script_review.script_plan_quarantined(project_path, project, number):
            # A present draft already means segmented: failed first splitting never wrote formal files. Reporting none would
            # route users back to source review, hiding draft details/repair entry points.
            return "segmented"
        if resolver is None:
            return "none"
        state = self._artifact_state(
            resolver, ArtifactKey.episode_script_plan(number), script_plan.relative_to(project_path).as_posix(), []
        )
        return "segmented" if state in {ArtifactStatus.CURRENT.value, ArtifactStatus.STALE.value} else "none"

    @classmethod
    def _migration_blocked_summary(
        cls,
        project: dict[str, Any],
        episodes: list[tuple[int, dict[str, Any]]],
        failure: MigrationFailureRecord,
    ) -> ProjectSummary:
        """Still list upgrade-failed projects, reporting zero usable artifacts.

        The manifest is the sole artifact authority but unreadable for unupgraded data. Counting usable
        artifacts would require reverting to file-existence rules this design retires. Users still see projects,
        episode counts, and repair reasons; read-only entry points remain open."""

        summaries = [
            EpisodeSummary(
                episode=number,
                script_status="none",
                status="draft",
                item_count=0,
                duration_seconds=0,
                storyboards=ArtifactCount.zero(),
                videos=ArtifactCount.zero(),
            )
            for number, _entry in episodes
        ]
        return ProjectSummary(
            needs_repair=True,
            repair_reason=failure.reason,
            assets={
                asset_type: ArtifactCount(
                    total=_asset_bucket_total(project, spec.bucket_key),
                    available=0,
                    stale=0,
                )
                for asset_type, spec in ASSET_SPECS.items()
            },
            episodes_summary=_episodes_summary(summaries),
            episodes=summaries,
            source_remaining=stored_source_remaining(project),
        )

    def _shared_facts(self, project_path: Path, project: dict[str, Any]) -> _SharedWorkflowFacts:
        """Facts shared across episodes within one query.

        Block only whole-project unavailability: unreadable content/generation modes (no content can be judged)
        or unreadable manifests. Other data problems enter ``issues`` as current-state information without
        blocking other content/operations (``docs/adr/0091``)."""
        mode = project.get("content_mode")
        generation_mode = project.get("generation_mode")
        blockers: list[WorkflowBlocker] = []
        issues: list[WorkflowBlocker] = []
        if not isinstance(mode, str) or mode not in {"narration", "drama", "ad"}:
            blockers.append(
                WorkflowBlocker(code="invalid_content_mode", path="content_mode", reason="unsupported mode")
            )
        if not isinstance(generation_mode, str) or generation_mode not in {"storyboard", "reference_video"}:
            blockers.append(
                WorkflowBlocker(code="invalid_generation_mode", path="generation_mode", reason="unsupported route")
            )
        grid_storyboard = project.get("grid_storyboard")
        if grid_storyboard is not None and not isinstance(grid_storyboard, bool):
            issues.append(
                WorkflowBlocker(
                    code="invalid_grid_storyboard",
                    path="grid_storyboard",
                    reason="grid_storyboard must be a boolean",
                )
            )
        if mode == "ad":
            target_duration = project.get("target_duration")
            if not isinstance(target_duration, int) or isinstance(target_duration, bool) or target_duration <= 0:
                issues.append(
                    WorkflowBlocker(
                        code="invalid_target_duration",
                        path="target_duration",
                        reason="ad target_duration must be a positive integer",
                    )
                )
            if grid_storyboard is True:
                issues.append(
                    WorkflowBlocker(
                        code="invalid_grid_storyboard",
                        path="grid_storyboard",
                        reason="ad workflow does not support grid storyboards",
                    )
                )
        # ``get_status`` refuses an unmigrated project before reaching here, so the
        # only way to arrive without a resolver is a damaged sidecar — a blocker,
        # never permission to classify artifacts by filesystem existence instead.
        currency: ArtifactCurrencyResolver | None = None
        try:
            currency = ArtifactCurrencyResolver(project_path)
        except (ArtifactManifestError, OSError, RuntimeError, TypeError, ValueError) as exc:
            blockers.append(
                WorkflowBlocker(
                    code="artifact_currency_unavailable",
                    path=".arcreel_artifacts.json",
                    reason=str(exc),
                )
            )
        asset_validation = DataValidator(str(self.pm.projects_dir)).validate_asset_definitions(project)
        if not asset_validation.valid:
            issues.append(
                WorkflowBlocker(
                    code="invalid_asset_definitions",
                    path="project.json",
                    reason="; ".join(asset_validation.errors),
                )
            )
        source = self._source_revision(project_path, project, str(mode), issues)
        planning_sources = planning_docs(project, source) if mode != "ad" else ()
        sheets = self._asset_sheets(project_path, project, issues, currency)
        episodes = self._episodes(project, issues)
        return _SharedWorkflowFacts(
            source=source,
            planning_sources=planning_sources,
            source_remaining=source_remaining(project, list(planning_sources)),
            sheets=sheets,
            episodes=episodes,
            currency=currency,
            blockers=tuple(blockers),
            issues=tuple(issues),
            whole_source=whole_source_present(planning_sources),
        )

    def _get_status(
        self,
        project_name: str,
        project: dict[str, Any],
        project_path: Path,
        episode: int | None,
        shared: _SharedWorkflowFacts,
    ) -> WorkflowStatus:
        mode = project.get("content_mode")
        if episode is not None and (isinstance(episode, bool) or episode < 1):
            raise WorkflowRequestError("episode must be a positive integer")
        if mode == "ad" and episode not in {None, 1}:
            raise WorkflowRequestError("ad workflow only has episode 1")
        if shared.blockers:
            return self._response(
                project,
                shared,
                target=None,
                content=None,
                operations={},
                gates={},
                artifacts=self._base_artifacts(project, shared),
                next_action=_action(WorkflowActionType.NONE, "workflow is blocked"),
            )
        if mode == "ad":
            selected = next((pair for pair in shared.episodes if pair[0] == 1), (1, {}))
            return self._episode_status(project_name, project, project_path, shared, selected)
        if episode is None:
            return self._next_episode_status(project_name, project, project_path, shared)
        selected = next((pair for pair in shared.episodes if pair[0] == episode), None)
        if selected is not None:
            return self._episode_status(project_name, project, project_path, shared, selected)
        status = self._next_episode_status(project_name, project, project_path, shared)
        status.issues.append(
            WorkflowBlocker(
                code="episode_unavailable",
                path=f"episodes.{episode}",
                reason="requested episode is not in the episode ledger",
            )
        )
        return status

    def _next_episode_status(
        self,
        project_name: str,
        project: dict[str, Any],
        project_path: Path,
        shared: _SharedWorkflowFacts,
    ) -> WorkflowStatus:
        """Without a specified episode, choose the first incomplete, non-stale-planned episode in ledger order.

        When all other episodes are complete, return to project level: continue planning if whole-source text
        remains uncut; otherwise stop at the first stale episode with state only, no action; otherwise all complete."""
        if not shared.episodes:
            return self._project_status(project, shared)
        first: WorkflowStatus | None = None
        first_stale: WorkflowStatus | None = None
        any_complete = False
        for pair in shared.episodes:
            status = self._episode_status(project_name, project, project_path, shared, pair)
            first = first or status
            if status.content is not None and status.content.episode_plan_stale:
                first_stale = first_stale or status
                continue
            if status.content is None or not status.content.episode_complete:
                return status
            any_complete = True
        assert first is not None
        if shared.source_remaining:
            next_action = self._planning_action(project, shared, "source text remains unplanned")
        elif first_stale is not None:
            reason = "remaining episodes await replanning" if any_complete else "every episode awaits replanning"
            return first_stale.model_copy(
                update={"next_action": _action(WorkflowActionType.NONE, reason), "next_alternatives": []}
            )
        else:
            content = first.content.model_copy(update={"project_complete": True}) if first.content else None
            return first.model_copy(
                update={
                    "content": content,
                    "next_action": _action(WorkflowActionType.NONE, ALL_EPISODES_COMPLETE_REASON),
                    "next_alternatives": [],
                }
            )
        return first.model_copy(update={"next_action": next_action, "next_alternatives": []})

    def _planning_action(
        self, project: dict[str, Any], shared: _SharedWorkflowFacts, reason: str
    ) -> WorkflowNextAction:
        """Continue-planning action; switch to reset from beginning when the planner rejects continuation (cut episodes lack positions or sources changed)."""
        if legacy_cut_episode_ids(project):
            return _action(
                WorkflowActionType.RESET_EPISODE_PLANNING,
                "episode ledger lacks source range records",
            )
        if source_fingerprints_diverged(project, list(shared.planning_sources)):
            return _action(
                WorkflowActionType.RESET_EPISODE_PLANNING,
                "source files changed after episode planning",
            )
        return _action(WorkflowActionType.PLAN_EPISODES, reason)

    @staticmethod
    def _project_content(project: Mapping[str, Any], shared: _SharedWorkflowFacts) -> WorkflowContent:
        is_ad = project.get("content_mode") == "ad"
        products = project.get("products")
        return WorkflowContent(
            episode_count=len(shared.episodes),
            whole_source="not_applicable" if is_ad else ("present" if shared.whole_source else "absent"),
            source_remaining=shared.source_remaining,
            ad_inputs=("present" if ad_inputs_present(project) else "absent") if is_ad else "not_applicable",
            products_without_selling_points=(
                [
                    name
                    for name, item in products.items()
                    if isinstance(item, Mapping) and not item.get("selling_points")
                ]
                if is_ad and isinstance(products, Mapping)
                else []
            ),
        )

    @staticmethod
    def _base_artifacts(project: Mapping[str, Any], shared: _SharedWorkflowFacts) -> dict[str, dict[str, Any]]:
        is_ad = project.get("content_mode") == "ad"
        return {
            "asset_sheets": shared.sheets,
            "script_plan": {"state": "not_applicable" if is_ad else "missing"},
            "script": {"state": "missing"},
            "storyboards": _empty_collection(),
            "videos": _empty_collection(),
            "audio": _empty_collection(),
        }

    def _project_status(self, project: dict[str, Any], shared: _SharedWorkflowFacts) -> WorkflowStatus:
        """Episodeless project: AI episode planning with whole-source text, otherwise source upload; either can switch to creating one episode."""
        if shared.whole_source:
            next_action = self._planning_action(project, shared, "episode ledger has no episodes")
        else:
            next_action = _action(WorkflowActionType.COLLECT_PROJECT_INPUT, "no episodes and no source text yet")
        return self._response(
            project,
            shared,
            target=None,
            content=self._project_content(project, shared),
            operations={
                WorkflowActionType.PLAN_EPISODES: admit_plan_episodes(
                    project.get("content_mode"),
                    whole_source=shared.whole_source,
                    replan_pending=replan_candidate(project) is not None,
                )
            },
            gates={},
            artifacts=self._base_artifacts(project, shared),
            next_action=next_action,
            next_alternatives=[_action(WorkflowActionType.CREATE_EPISODE, "an episode can also be created by hand")],
        )

    def _edit_timeline_ids(
        self,
        project_name: str,
        episode: int,
        artifacts: dict[str, dict[str, Any]],
        issues: list[WorkflowBlocker],
    ) -> list[str] | None:
        """Editing-step completion: at least one timeline in the episode. Also write results into ``artifacts``.

        Record an issue and return None if directory reads fail or any timeline file cannot be parsed."""

        try:
            documents = EditTimelineStore(self.pm, project_name).list_documents(episode, strict=True)
        except (OSError, EditTimelineError) as exc:
            issues.append(
                WorkflowBlocker(
                    code=INVALID_EDIT_TIMELINES_CODE, path=f"edit_timelines/episode_{episode}", reason=str(exc)
                )
            )
            artifacts["edit_timelines"] = {"timeline_ids": []}
            return None
        ids = [document.id for document in documents]
        artifacts["edit_timelines"] = {"timeline_ids": ids}
        return ids

    @staticmethod
    def _episode_target(number: int, entry: Mapping[str, Any], issues: list[WorkflowBlocker]) -> WorkflowTarget | None:
        script_path = entry.get("script_file")
        if not isinstance(script_path, str) or not script_path:
            issues.append(
                WorkflowBlocker(
                    code="invalid_script_binding",
                    path=f"episodes.{number}.script_file",
                    reason="script_file must be a non-empty string",
                )
            )
            return None
        script_filename = ProjectManager.normalize_script_filename(script_path)
        if "/" in script_filename or "\\" in script_filename:
            issues.append(
                WorkflowBlocker(
                    code="invalid_script_path",
                    path=f"episodes.{number}.script_file",
                    reason="script_file must resolve to a bare filename under scripts/",
                )
            )
            return None
        return WorkflowTarget(
            episode=number,
            script=script_path,
            script_filename=script_filename,
            source=episode_source_relpath(number),
        )

    @staticmethod
    def _episode_drafts(project_path: Path, project: dict[str, Any], number: int) -> list[WorkflowDraft]:
        """Drafts present for the target episode: script-plan drafts and reference-to-video prompt-authoring drafts."""
        kinds: list[str] = []
        script_plan_kind = script_review.script_plan_quarantine_kind(project)
        if script_plan_kind is not None:
            kinds.append(script_plan_kind)
        if is_reference_video_project(project):
            kinds.append(QUARANTINE_KIND_PROMPT_AUTHORING)
        drafts: list[WorkflowDraft] = []
        for kind in kinds:
            if not quarantine_exists(project_path, number, kind):
                continue
            draft = read_quarantine(project_path, number, kind)
            drafts.append(
                WorkflowDraft(
                    kind=kind,
                    path=quarantine_path(project_path, number, kind).relative_to(project_path).as_posix(),
                    # Drafts with unreadable envelopes also require repair before disposition.
                    needs_repair=draft is None or bool(draft.violations),
                )
            )
        return drafts

    @staticmethod
    def _stale_episode_plan(
        project_path: Path, project: dict[str, Any], number: int, entry: Mapping[str, Any]
    ) -> tuple[bool, str | None]:
        """Stale episode plan without rebuilt script plan: return ``(是否 stale, 重建基线 revision)``.

        After rebuilding (formal script_plan differs from the stale one, or rebuilding explicitly recorded
        complete), state content normally."""
        if entry.get("ledger_status") != "stale":
            return False, None
        if script_review.STALE_SCRIPT_PLAN_REVISION_FIELD not in entry:
            return True, None
        stale_revision = entry.get(script_review.STALE_SCRIPT_PLAN_REVISION_FIELD)
        rebuilt_revision = entry.get(script_review.STALE_SCRIPT_PLAN_REBUILT_REVISION_FIELD)
        path = script_review.script_plan_path(project_path, project, number)
        live_revision = script_review.content_fingerprint(path) if path is not None else None
        if live_revision is None or (live_revision == stale_revision and rebuilt_revision != live_revision):
            return True, script_review.published_revision(stale_revision) if isinstance(stale_revision, str) else None
        return False, None

    def _script_plan_artifact(
        self,
        project_path: Path,
        project: dict[str, Any],
        number: int,
        currency: ArtifactCurrencyResolver | None,
        issues: list[WorkflowBlocker],
    ) -> dict[str, Any]:
        path = script_review.script_plan_path(project_path, project, number)
        revision = script_review.content_fingerprint(path) if path is not None else None
        state = ArtifactStatus.CURRENT.value if revision is not None else ArtifactStatus.MISSING.value
        if currency is not None and path is not None:
            state = self._artifact_state(
                currency,
                ArtifactKey.episode_script_plan(number),
                path.relative_to(project_path).as_posix(),
                issues,
            )
        return {
            "state": state,
            "path": str(path.relative_to(project_path)) if path is not None else None,
            "revision": script_review.published_revision(revision),
        }

    @staticmethod
    def _reference_admission(project: dict[str, Any], items: list[dict[str, Any]], kind: str | None) -> Any:
        """Episode reference admission matches generation (ADR 0073): derive body-unit references from bodies; storyboard entries use reference fields."""
        catalog = build_reference_catalog(project)
        if kind != "video_units":
            return admit_storyboard_items(catalog, items)
        references: list[tuple[str, str]] = []
        unregistered: list[str] = []
        for item in items:
            text = item.get("text")
            if not isinstance(text, str) or not text:
                continue
            resources, missing = derive_references_from_text(text, project)
            references.extend((resource.type, resource.name) for resource in resources)
            unregistered.extend(missing)
        return admit_references(catalog, references=references, unregistered=unregistered)

    def _referenced_sheet_facts(
        self,
        project: dict[str, Any],
        items: list[dict[str, Any]],
        kind: str | None,
        shared: _SharedWorkflowFacts,
        issues: list[WorkflowBlocker],
    ) -> tuple[list[str], list[str], list[str]]:
        """Referenced sheet state for this episode: (pending and generatable, outdated, missing description).

        Pending uses sheet-batch episode-scope rules: exclude missing descriptions, and derivatives whose bases
        lack usable sheets and are not in the same batch."""
        missing: list[str] = []
        stale: list[str] = []
        without_description: list[str] = []
        accepted_sheets: set[tuple[str, str]] = set()
        for asset in sorted(
            episode_referenced_assets(project, ({kind: items} if kind else None)),
            key=lambda asset: (asset.owner is not None, asset.asset_type, asset.name),
        ):
            spec = ASSET_SPECS[asset.asset_type]
            owner = project[spec.bucket_key][asset.owner or asset.name]
            entry = (
                derivative_table(owner)[split_derivative_artifact_id(asset.name)[1]]
                if asset.owner is not None
                else owner
            )
            description = entry.get("description")
            if not isinstance(description, str) or not description.strip():
                without_description.append(asset.name)
                continue
            path = entry.get(spec.sheet_field)
            sheet_state = (
                self._artifact_state(
                    shared.currency,
                    (
                        derivative_artifact_key(
                            *map(asset_name_comparison_key, split_derivative_artifact_id(asset.name))
                        )
                        if asset.owner is not None
                        else ArtifactKey.asset_sheet(asset.asset_type, asset_name_comparison_key(asset.name))
                    ),
                    path,
                    issues,
                )
                if shared.currency is not None and isinstance(path, str) and path
                else ArtifactStatus.MISSING.value
            )
            if sheet_state == ArtifactStatus.STALE.value:
                stale.append(asset.name)
            if sheet_state != ArtifactStatus.MISSING.value:
                continue
            if asset.owner is not None:
                owner_sheets = shared.sheets[asset.asset_type]
                if (asset.asset_type, asset.owner) not in accepted_sheets and asset.owner not in (
                    owner_sheets["current_ids"] + owner_sheets["stale_ids"]
                ):
                    continue
            missing.append(asset.name)
            accepted_sheets.add((asset.asset_type, asset.name))
        return missing, stale, without_description

    def _episode_status(
        self,
        project_name: str,
        project: dict[str, Any],
        project_path: Path,
        shared: _SharedWorkflowFacts,
        selected: tuple[int, dict[str, Any]],
    ) -> WorkflowStatus:
        """Episode content state, AI-operation admission, and next suggestions (order in ``docs/adr/0091`` and Spec)."""
        number, entry = selected
        mode = project.get("content_mode")
        is_ad = mode == "ad"
        generation_mode = project.get("generation_mode")
        grid = project.get("grid_storyboard") is True and generation_mode == "storyboard"
        issues = list(shared.issues)
        artifacts = self._base_artifacts(project, shared)
        gates: dict[str, dict[str, Any]] = {
            "script_plan_review": {"state": "not_applicable" if is_ad else "pending", "revision": None}
        }
        content = self._project_content(project, shared)
        operations: dict[str, OperationAdmission] = {
            WorkflowActionType.PLAN_EPISODES: admit_plan_episodes(
                mode, whole_source=shared.whole_source, replan_pending=replan_candidate(project) is not None
            ),
        }

        def respond(
            target: WorkflowTarget | None,
            next_action: WorkflowNextAction,
            alternatives: list[WorkflowNextAction] | None = None,
        ) -> WorkflowStatus:
            return self._response(
                project,
                shared,
                target=target,
                content=content,
                operations=operations,
                gates=gates,
                artifacts=artifacts,
                next_action=next_action,
                next_alternatives=alternatives or [],
                issues=issues,
            )

        target = self._episode_target(number, entry, issues)
        if target is None:
            return respond(None, _action(WorkflowActionType.NONE, "episode script binding is invalid"))

        episode_source = not is_ad and episode_source_present(project_path, number, entry)
        content.episode_source = "not_applicable" if is_ad else ("present" if episode_source else "absent")
        content.drafts = self._episode_drafts(project_path, project, number)
        stale, stale_revision = (
            (False, None) if is_ad else self._stale_episode_plan(project_path, project, number, entry)
        )
        content.episode_plan_stale = stale
        content.expected_stale_script_plan_revision = stale_revision

        script_plan_state: str | None = None
        review_pending = False
        if not is_ad:
            artifacts["script_plan"] = self._script_plan_artifact(
                project_path, project, number, shared.currency, issues
            )
            if script_review.script_plan_quarantined(project_path, project, number):
                artifacts["script_plan"]["state"] = "blocked"
            elif stale:
                artifacts["script_plan"]["state"] = "stale"
            script_plan_state = artifacts["script_plan"]["state"]
            review = script_review.review_status(project_path, project, number)
            gates["script_plan_review"] = {
                "state": "confirmed" if review == "confirmed" else "pending",
                "revision": artifacts["script_plan"].get("revision"),
            }
            # When existing formal scripts are used, unconfirmed rerun plans are informational pending-confirmation only, not next steps;
            # stale episode plans make old scripts unusable, so rebuilt plans still require confirmation.
            formal_in_use = entry.get("ledger_status") != "stale" and script_review.prompt_authoring_generated(
                project_path, project, number
            )
            review_pending = (
                script_plan_state in {ArtifactStatus.CURRENT.value, ArtifactStatus.STALE.value}
                and review != "confirmed"
                and not formal_in_use
            )

        script_artifact, items, kind = self._load_script_artifacts(
            project_path, project_name, project, target, issues, shared.currency
        )
        artifacts["script"] = script_artifact
        formal_present = script_artifact["state"] in {ArtifactStatus.CURRENT.value, ArtifactStatus.STALE.value}
        content.formal_script = (
            "present" if formal_present else "invalid" if script_artifact["state"] == "blocked" else "absent"
        )
        pending_ids: list[str] = []
        replan_ids: list[str] = []
        without_sheet: list[str] = []
        missing_sheets: list[str] = []
        if formal_present:
            content.script_item_count = len(items)
            pending_ids = pending_authoring_entry_ids(items, kind)
            id_field = SKELETONS[kind].id_field if kind in SKELETONS else None
            replan_ids = [
                str(item.get(id_field))
                for item in items
                if kind == "video_units" and id_field is not None and item.get("needs_replan") is True
            ]
            admission = self._reference_admission(project, items, kind)
            without_sheet = [name for _asset_type, name in admission.without_sheet]
            content.referenced_assets_without_sheet = without_sheet
            content.unregistered_references = list(admission.unregistered)
            missing_sheets, content.referenced_asset_sheets_stale, content.referenced_assets_without_description = (
                self._referenced_sheet_facts(project, items, kind, shared, issues)
            )
            artifacts["storyboards"] = (
                self._media_collection(
                    project_path,
                    items,
                    kind,
                    "storyboard_image",
                    episode=number,
                    resolver=shared.currency,
                    issues=issues,
                )
                if generation_mode == "storyboard"
                else _not_applicable_collection()
            )
            artifacts["videos"] = self._media_collection(
                project_path,
                items,
                kind,
                "video_clip",
                episode=number,
                resolver=shared.currency,
                issues=issues,
            )
            # Narration audio is informational only, excluded from next actions: missing TTS does not block editing; users explicitly
            # request TTS (generate_narration_audio). Post-production-voiceover projects need no TTS and report no gap. Unreadable audio
            # state appears only in the collection state, not issues.
            artifacts["audio"] = (
                self._media_collection(
                    project_path,
                    items,
                    kind,
                    "narration_audio",
                    episode=number,
                    resolver=shared.currency,
                    issues=[],
                )
                if mode == "narration"
                and generation_mode == "storyboard"
                and project_narration_delivery(project) == USE_TTS
                else _not_applicable_collection()
            )
        elif not is_ad:
            artifacts["storyboards"] = (
                _empty_collection() if generation_mode == "storyboard" else _not_applicable_collection()
            )
        content.pending_authoring_ids = pending_ids
        content.needs_replan_ids = replan_ids
        # Timelines describe only their own content type: incomplete videos do not affect listing existing timelines.
        timeline_ids = self._edit_timeline_ids(project_name, number, artifacts, issues) if formal_present else []
        videos = artifacts["videos"]
        operations[WorkflowActionType.CREATE_EDIT_TIMELINE] = admit_edit_timeline(
            available_videos=len(videos.get("current_ids", [])) + len(videos.get("stale_ids", []))
        )
        content.episode_complete = (
            formal_present
            and videos.get("state") != "blocked"
            and is_episode_complete(ArtifactCount.of(videos, total=len(items)), has_edit_timeline=bool(timeline_ids))
        )

        operations[WorkflowActionType.PREPARE_SCRIPT_PLAN] = admit_script_plan(mode, episode_source=episode_source)
        operations[WorkflowActionType.GENERATE_SCRIPT] = admit_ad_script(
            mode,
            formal_script=formal_present,
            ad_inputs=ad_inputs_present(project),
        )
        operations[WorkflowActionType.AUTHOR_PROMPTS] = admit_author_prompts(
            formal_script=formal_present,
            pending_ids=pending_ids,
            draft_pending=any(draft.kind == QUARANTINE_KIND_PROMPT_AUTHORING for draft in content.drafts),
        )

        episode_args = {"episode_id": number}
        blank = _action(WorkflowActionType.START_BLANK_SCRIPT, "write the formal script by hand", args=episode_args)
        if stale:
            return respond(
                target,
                _action(WorkflowActionType.NONE, "the episode plan is stale; its script plan awaits a rebuild"),
            )
        if content.drafts:
            draft = content.drafts[0]
            return respond(
                target,
                _action(
                    WorkflowActionType.RESOLVE_DRAFT,
                    "a draft awaits repair" if draft.needs_repair else "an editable draft awaits completion",
                    args={**episode_args, "draft_kind": draft.kind, "needs_repair": draft.needs_repair},
                ),
            )
        if content.formal_script == "invalid":
            return respond(target, _action(WorkflowActionType.NONE, "the formal script is invalid"))
        if not formal_present:
            if is_ad:
                if operations[WorkflowActionType.GENERATE_SCRIPT].admitted:
                    return respond(
                        target,
                        _action(WorkflowActionType.GENERATE_SCRIPT, "the ad has no script yet", args=episode_args),
                        [blank],
                    )
                return respond(
                    target,
                    _action(WorkflowActionType.COLLECT_PROJECT_INPUT, "the ad needs a creative brief or products"),
                    [blank],
                )
            if script_plan_state == ArtifactStatus.MISSING.value:
                if episode_source:
                    preprocessor = workflow_rule(str(mode), str(generation_mode)).preprocessor
                    return respond(
                        target,
                        _action(
                            WorkflowActionType.PREPARE_SCRIPT_PLAN,
                            "the episode has neither a script plan nor a formal script",
                            args={**episode_args, "preprocessor": preprocessor},
                        ),
                        [blank],
                    )
                return respond(
                    target,
                    _action(
                        WorkflowActionType.START_BLANK_SCRIPT,
                        "the episode has no episode source to plan from",
                        args=episode_args,
                    ),
                    [
                        _action(
                            WorkflowActionType.PROVIDE_EPISODE_SOURCE,
                            "episode source enables AI script planning",
                            args=episode_args,
                        )
                    ],
                )
            if script_plan_state == ArtifactStatus.BLOCKED.value:
                return respond(target, _action(WorkflowActionType.NONE, "formal script_plan currency is blocked"))
            return respond(
                target,
                _action(
                    WorkflowActionType.CONFIRM_SCRIPT_PLAN,
                    "formal script_plan awaits content review"
                    if review_pending
                    else "confirming the script_plan materializes the missing final script",
                    args=episode_args,
                    requires_confirmation=True,
                ),
            )
        if review_pending:
            return respond(
                target,
                _action(
                    WorkflowActionType.CONFIRM_SCRIPT_PLAN,
                    "formal script_plan awaits content review",
                    args=episode_args,
                    requires_confirmation=True,
                ),
            )
        if not items:
            return respond(
                target,
                _action(WorkflowActionType.ADD_SCRIPT_ITEMS, "the formal script is empty", args=episode_args),
            )
        if pending_ids:
            return respond(
                target,
                _action(
                    WorkflowActionType.AUTHOR_PROMPTS,
                    "script entries still need prompts",
                    args=episode_args,
                    ids=pending_ids,
                ),
            )
        if replan_ids:
            return respond(
                target,
                _action(
                    WorkflowActionType.REPAIR_VIDEO_UNITS,
                    "video units need replanning before generation",
                    args=episode_args,
                    ids=replan_ids,
                ),
            )
        if missing_sheets:
            return respond(
                target,
                _action(
                    WorkflowActionType.GENERATE_ASSET_SHEETS,
                    "assets referenced by this episode need sheets",
                    args=episode_args,
                    ids=missing_sheets,
                ),
            )
        if artifacts["storyboards"].get("state") == "blocked" or artifacts["videos"].get("state") == "blocked":
            return respond(target, _action(WorkflowActionType.NONE, "media artifact currency is blocked"))
        if generation_mode == "storyboard" and artifacts["storyboards"]["missing_ids"]:
            return respond(
                target,
                _action(
                    WorkflowActionType.GENERATE_GRID if grid else WorkflowActionType.GENERATE_STORYBOARDS,
                    "storyboard images are missing",
                    args=episode_args,
                    ids=artifacts["storyboards"]["missing_ids"],
                ),
            )
        if artifacts["videos"]["missing_ids"]:
            return respond(
                target,
                _action(
                    WorkflowActionType.GENERATE_VIDEOS,
                    "video clips are missing",
                    args=episode_args,
                    ids=artifacts["videos"]["missing_ids"],
                ),
            )
        if timeline_ids is None:
            return respond(target, _action(WorkflowActionType.NONE, "edit timelines cannot be read"))
        if not timeline_ids:
            return respond(
                target,
                _action(WorkflowActionType.CREATE_EDIT_TIMELINE, "episode has no edit timeline", args=episode_args),
            )
        return respond(target, _action(WorkflowActionType.NONE, EPISODE_COMPLETE_REASON))

    @staticmethod
    def _response(
        project: dict[str, Any],
        shared: _SharedWorkflowFacts,
        *,
        target: WorkflowTarget | None,
        content: WorkflowContent | None,
        operations: Mapping[str, OperationAdmission],
        gates: dict[str, dict[str, Any]],
        artifacts: dict[str, dict[str, Any]],
        next_action: WorkflowNextAction,
        next_alternatives: list[WorkflowNextAction] | None = None,
        issues: list[WorkflowBlocker] | None = None,
    ) -> WorkflowStatus:
        source = shared.source
        return WorkflowStatus(
            project_revision=_project_revision(project),
            source_revision=source.revision if source is not None else None,
            project=WorkflowProject(
                content_mode=str(project.get("content_mode")),
                generation_mode=str(project.get("generation_mode")),
                grid_storyboard=project.get("grid_storyboard") is True,
            ),
            target=target,
            blockers=list(shared.blockers),
            issues=list(shared.issues) if issues is None else issues,
            content=content,
            operations={
                str(operation): WorkflowOperation(state=admission.state, reason=admission.reason)
                for operation, admission in operations.items()
            },
            gates=gates,
            artifacts=artifacts,
            next_action=next_action,
            next_alternatives=next_alternatives or [],
        )

    @classmethod
    def _migration_blocked_status(cls, project: Mapping[str, Any], failure: MigrationFailureRecord) -> WorkflowStatus:
        """Report the failure instead of a state derived from unmigrated data.

        Deriving the real state would mean walking the very inputs the migration
        already refused, so a second failure would replace the explanation the
        user needs with a 500. The project stays readable through the project
        and script endpoints; only the production status short-circuits.
        """

        return WorkflowStatus(
            project_revision=_project_revision(project),
            source_revision=None,
            project=WorkflowProject(
                content_mode=str(project.get("content_mode")),
                generation_mode=str(project.get("generation_mode")),
                grid_storyboard=project.get("grid_storyboard") is True,
            ),
            target=None,
            blockers=[migration_blocker(failure)],
            content=None,
            gates={},
            artifacts={
                "asset_sheets": {},
                "script_plan": {"state": "missing"},
                "script": {"state": "missing"},
                "storyboards": _empty_collection(),
                "videos": _empty_collection(),
                "audio": _empty_collection(),
            },
            next_action=migration_next_action(failure),
        )


def migration_blocker(failure: MigrationFailureRecord) -> WorkflowBlocker:
    """The one blocker a project whose migration failed reports everywhere."""

    return WorkflowBlocker(code=MIGRATION_FAILURE_CODE, path=MIGRATION_FAILURE_FILENAME, reason=failure.reason)


def migration_next_action(failure: MigrationFailureRecord) -> WorkflowNextAction:
    """Repair the reported inputs, then rerun the chain — the only way forward."""

    return _action(
        WorkflowActionType.RETRY_PROJECT_MIGRATION,
        failure.reason,
        args={"details": [detail.model_dump(mode="json") for detail in failure.details]},
    )


__all__ = [
    "ALL_EPISODES_COMPLETE_REASON",
    "EPISODE_COMPLETE_REASON",
    "INVALID_EDIT_TIMELINES_CODE",
    "ArtifactCount",
    "EpisodeNextStep",
    "EpisodeSummary",
    "EpisodesSummary",
    "ProjectSummary",
    "WorkflowActionType",
    "WorkflowBlocker",
    "WorkflowNextAction",
    "WorkflowProject",
    "WorkflowRequestError",
    "WorkflowStateService",
    "WorkflowStatus",
    "WorkflowTarget",
    "is_episode_complete",
    "migration_blocker",
    "migration_next_action",
    "planning_docs",
    "workflow_finished",
]
