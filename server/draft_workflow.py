"""Host-independent draft workflow implementation."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.json_schema import SkipJsonSchema

from lib.artifacts.artifact_manifest import ArtifactBasis
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.resolver import ConfigResolver
from lib.episode.episode_ids import episode_title
from lib.episode.episode_paths import SCRIPT_PLAN_FILENAMES, episode_drafts_dir, episode_script_filename
from lib.generation.video_request_facts import VideoRequestFactsError, VideoRequestFactsFailure
from lib.infra.async_thread import run_sync_transaction
from lib.infra.json_io import atomic_write_json, load_json_or_none
from lib.project.project_activity import record_project_activity
from lib.project.project_manager import ProjectManager, ScriptWriteConflict
from lib.script import script_review
from lib.script.draft_quarantine import (
    DOC_TYPE_TO_QUARANTINE_KIND,
    DRAFT_OWNER_AGENT,
    FORMAL_EDIT_META_KEY,
    PROMOTE_TOOL_NAME,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    QUARANTINE_KIND_TO_DOC_TYPE,
    QuarantinedDraft,
    announce_draft_change,
    clear_quarantine,
    draft_owner,
    draft_payload,
    draft_revision,
    quarantine_and_report,
    quarantine_envelope,
    quarantine_exists,
    quarantine_path,
    read_quarantine,
    write_quarantine,
)
from lib.script.draft_violation import DraftViolation, schema_violations
from lib.script.plan_new_assets import NEW_ASSETS_FIELD, dedupe_new_assets, planning_project, with_new_assets
from lib.script.planning_draft_trash import discard_quarantine
from lib.script.script_generator import ScriptGenerator
from lib.script.script_models import (
    NarrationScriptPlanDraft,
    build_drama_normalized_script_model,
    build_reference_units_script_plan_model,
)
from lib.speech.speech_composition import admit_script_unit
from server.text_generation import (
    SOFT_VIOLATION_NOTE_QUARANTINED,
    ReferenceSplitCaps,
    SoftViolation,
    build_reference_units_from_flat,
    collect_drama_violations,
    collect_narration_violations,
    collect_reference_flat_violations,
    commit_single_script_plan,
    coverage_source_scope,
    drama_script_plan_result_text,
    fetch_reference_split_caps,
    fetch_storyboard_durations,
    load_novel_source,
    load_script_plan_source_with_basis,
    narration_script_plan_path,
    narration_script_plan_result_text,
    plan_item_ids,
    reference_result_text,
    reference_soft_violations,
    reissue_plan_item_ids,
    render_soft_violation_lines,
    render_soft_violation_section,
    uses_reference_video_units,
    video_facts_failure_text,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DraftContext:
    project_name: str
    data_root: Path
    pm: ProjectManager
    config_resolver: ConfigResolver | None = None

    @property
    def project_path(self) -> Path:
        return self.pm.get_project_path(self.project_name)

    async def aproject_path(self) -> Path:
        """Async ``project_path`` entry point: locating the directory calls stat, so offload to a thread."""
        return await asyncio.to_thread(self.pm.get_project_path, self.project_name)


DraftDocType = Literal[
    "drama_script_plan", "narration_script_plan", "reference_script_plan", "reference_prompt_authoring"
]
PositiveEpisode = Annotated[int, Field(strict=True, ge=1)]
#: Shared ``episode_id`` tool-argument description. Episode IDs are internal; address users by title and playback position.
EPISODE_ID_DESCRIPTION = "Episode ID: an internal episode identifier from project details episodes[].episode or the production plan episode_id, not its position in playback order"


_DRAFT_REVISION_DESCRIPTION = "Revision returned by open_draft / the preceding patch_draft; sha256-v1: prefixed and bare hexadecimal forms are equivalent"


class _DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: PositiveEpisode = Field(description=EPISODE_ID_DESCRIPTION)
    doc_type: DraftDocType = Field(
        description=(
            "Draft document: drama_script_plan / narration_script_plan / reference_script_plan are the content modes' script_plan documents; reference_prompt_authoring is the prompt-authoring draft for a formal reference-to-video script"
        )
    )


class DraftLocator(_DraftRequest):
    source: str | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "Optional novel source path, used for reassessment only when first creating a draft from formal script_plan; defaults to this episode's derived source/episode_N.txt"
        ),
    )


class PatchDraftRequest(_DraftRequest):
    """``accept_formal_revision`` and ``source`` distinguish omission from explicit null by presence: omission means no acceptance / no change."""

    content: dict[str, Any] = Field(
        description="Complete replacement draft body; intermediate states may fail business validation"
    )
    base_revision: str = Field(description=_DRAFT_REVISION_DESCRIPTION)
    accept_formal_revision: str | None = Field(
        default=None,
        description=(
            "After merging concurrent formal-document changes, explicitly accept formal_revision returned by open_draft (sha256-v1: prefixed and bare hexadecimal forms are equivalent). If the formal document no longer exists, explicitly supply null. Omission means no acceptance"
        ),
    )
    source: str | None = Field(
        default=None,
        description="Optional source range, supplied only to correct the reassessment range of a script_plan draft; null clears the range, omission leaves it unchanged",
    )


class PromoteDraftRequest(_DraftRequest):
    base_revision: str = Field(description=_DRAFT_REVISION_DESCRIPTION)


class DiscardDraftRequest(_DraftRequest):
    base_revision: str = Field(description=_DRAFT_REVISION_DESCRIPTION)


class DraftWorkflowError(Exception):
    """``draft_refreshed`` means violations blocked promotion and the draft now contains a violation report for current values.

    ``failure`` indicates video-request facts blocked reassessment (configuration problems such as insufficient model capabilities or missing tiers, not violations in the draft itself). ``code`` is that problem's code; Web and Agent present the real reason using the code, parameters and recovery guidance.

    ``notice_key`` indicates a violation with a user-facing wording key and ``notice_params`` (for example, a formal script unit failing preflight before promotion). ``code`` stays unchanged, Web localizes the reason by that key, and ``detail`` remains Agent-facing."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        draft_refreshed: bool = False,
        failure: VideoRequestFactsFailure | None = None,
        notice_key: str | None = None,
        notice_params: dict[str, Any] | None = None,
    ):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.draft_refreshed = draft_refreshed
        self.failure = failure
        self.notice_key = notice_key
        self.notice_params = notice_params or {}

    @classmethod
    def video_facts(cls, failure: VideoRequestFactsFailure) -> DraftWorkflowError:
        return cls(failure.code, video_facts_failure_text(failure), failure=failure)


class ReferenceDraftRevalidation(NamedTuple):
    """Read-time reassessment result for a script_plan draft.

    ``schema_failed`` explicitly distinguishes two phases: True means the draft fails even its production-time schema (``flat_units`` must be empty; callers can only present ``draft.content`` unchanged). When False, ``flat_units`` is the coerced flat output and promotion is allowed if ``violations`` is empty. These need different handling (unchanged versus coerced), so do not infer the phase from whether ``flat_units`` is empty.

    ``soft_violations`` contains structured soft violations (audio downgrades, unreferenced scenes), separate from ``violations``: soft violations neither block promotion nor enter repair-draft violation entries. Combining them would eventually make some nonempty-list guard turn downgrade hints into hard violations. Empty on schema failure: without coercing the body, there is no per-unit assessment.

    ``new_assets`` contains this episode's coerced and deduplicated additions, written back to the draft or persisted with ``flat_units``."""

    violations: list[DraftViolation]
    flat_units: list[dict[str, Any]]
    caps: ReferenceSplitCaps
    schema_failed: bool
    basis: ArtifactBasis | None
    soft_violations: list[SoftViolation]
    new_assets: list[dict[str, Any]]

    def flat_content(self) -> dict[str, Any]:
        """Coerced flat draft content."""
        return with_new_assets({"units": self.flat_units}, self.new_assets)


async def revalidate_reference_script_plan_draft(
    project_path: Path,
    project: dict[str, Any],
    episode: int,
    draft: QuarantinedDraft,
    *,
    config_resolver: ConfigResolver | None = None,
) -> ReferenceDraftRevalidation:
    """Fully reassess a script_plan draft with its production-time validators; read only, without disk writes or draft cleanup.

    Use the same function as splitting (``collect_reference_flat_violations``), never a simplified copy: promotion, content-confirmation read-time reassessment and production must share code, or divergence such as admitting here but rejecting on the next generation will return. Resolve capabilities and source anew against current values: users may change model configuration or source while a draft exists.

    Independent of ``DraftContext`` (callers pass ``project_path`` / ``project`` rather than deriving them from ctx): content-confirmation read-time reassessment (``server/services/project/script_review.py``) has only ``ProjectManager``, not the Agent tool ctx. Share this function without each site loading project again; each caller loads it once.

    Raise ``ValueError`` for missing ``meta.source`` (damaged draft that cannot be reassessed)."""
    # meta.source records the source range at production time. A missing key means damaged meta: never silently reparse using the default source.
    # If another source file was specified originally, reassessing a different range would admit source anchors copied from elsewhere.
    if "source" not in draft.meta:
        raise ValueError(
            f"草稿 {draft.path} 的 meta.source 缺失（产出时记录的源文范围）；"
            "请恢复该字段（指定源文时为其相对路径，按本集派生源文产出时为 null）后重试"
        )
    # Source text may span hundreds of KB; synchronous disk reads directly inside this async function would occupy
    # the event loop. Promotion tools use a separate session thread and are insensitive, but content-confirmation read-time reassessment
    # runs this same code in a request coroutine. Offload to a thread to avoid slowing other concurrent requests.
    novel_text, _prompt_inputs, script_plan_basis = await asyncio.to_thread(
        load_script_plan_source_with_basis,
        project_path,
        draft.meta["source"],
        project,
        episode,
        "reference_video",
    )
    split_caps = await fetch_reference_split_caps(project, config_resolver=config_resolver)

    # Edited drafts must first satisfy their production-time schema: response_schema and _parse_script_plan_json enforce duration
    # enums and nonempty fields on splitting. Omitting this layer on promotion would let a non-tier duration_seconds or its deletion
    # (coerced to zero seconds) reach formal files, precisely the formal-content contamination this mechanism prevents. Schema violations here
    # have no backend to retry because the Agent wrote the content, so return a report for further editing as well.
    #
    # Outer-shape failures (missing / non-array / empty units) and per-unit field violations share one reporting path: both can occur
    # while the Agent edits a draft. Refreshing reports only for the latter would eject the former from the edit-and-promote loop.
    raw_units = draft.content.get("units")
    schema = build_reference_units_script_plan_model(split_caps.durations)
    violations: list[DraftViolation] = []
    flat_units: list[dict[str, Any]] = []
    new_assets: list[dict[str, Any]] = []
    if not isinstance(raw_units, list) or not raw_units:
        logger.debug("草稿 content.units 形状非法: %s", type(raw_units).__name__)
        violations = [
            DraftViolation(
                "草稿的 content.units 必须是非空的 unit 对象数组",
                code="schema_invalid",
            )
        ]
    else:
        try:
            flat = schema.model_validate(
                {"units": raw_units, NEW_ASSETS_FIELD: draft.content.get(NEW_ASSETS_FIELD, [])}
            ).model_dump()
        except ValidationError as exc:
            violations = schema_violations(exc, draft.content, "units")
        else:
            flat_units = flat["units"]
            new_assets = dedupe_new_assets(flat[NEW_ASSETS_FIELD])
    if violations:
        return ReferenceDraftRevalidation(
            violations, [], split_caps, schema_failed=True, basis=script_plan_basis, soft_violations=[], new_assets=[]
        )

    source_language = project.get("source_language")
    violations = await asyncio.to_thread(
        collect_reference_flat_violations,
        flat_units,
        project,
        project_path=project_path,
        episode=episode,
        novel_text=novel_text,
        caps=split_caps,
        source_language=source_language,
        new_assets=new_assets,
    )
    # Compute soft and hard violations in the same reassessment, including when hard violations block promotion, and include both in its report.
    # Otherwise every repair round hides downgrade hints until the final successful promotion exposes them for the first time.
    soft_violations = reference_soft_violations(
        [str(flat["text"]) for flat in flat_units],
        planning_project(project, new_assets),
        episode=episode,
        voice=split_caps.voice,
    )
    return ReferenceDraftRevalidation(
        violations,
        flat_units,
        split_caps,
        schema_failed=False,
        basis=script_plan_basis,
        soft_violations=soft_violations,
        new_assets=new_assets,
    )


def _project_relative(path: Path, project_path: Path) -> str:
    """Receipt paths are project-relative to avoid exposing server filesystem layout; use only the filename outside the project directory."""
    try:
        return path.relative_to(project_path).as_posix()
    except ValueError:
        return path.name


def _commit_reference_script_plan(
    project_path: Path,
    episode: int,
    content: dict[str, Any],
    expected_fingerprint: Any,
    basis: ArtifactBasis | None,
    before_commit: Callable[[], None] | None = None,
) -> None:
    if before_commit is not None:
        before_commit()
    script_review.write_script_plan(
        project_path,
        episode,
        content,
        expected_fingerprint=expected_fingerprint,
        basis=basis,
    )
    clear_quarantine(project_path, episode, QUARANTINE_KIND_SCRIPT_PLAN)


def _script_plan_confirmed_detail(episode: int) -> str:
    return (
        f"❌ 集（id={episode}）的脚本规划已确认，确认后只读，不能再改。"
        "内容修改请在正式脚本上进行（patch_episode_script，或请用户在时间线上修改）；"
        "要整集重做，请重跑 generate_script_plan，新的脚本规划会让该集回到待确认。"
    )


def _open_script_plan_draft(
    project_path: Path,
    episode: int,
    script_plan_path: Path,
    kind: str,
    source: str | None,
    to_draft_shape: Callable[[dict[str, Any]], dict[str, Any] | None],
    missing_detail: str,
) -> None:
    with script_review.formal_script_plan_lock(project_path, episode, script_plan_path):
        if quarantine_exists(project_path, episode, kind):
            return
        data = load_json_or_none(script_plan_path)
        content = to_draft_shape(data) if isinstance(data, dict) else None
        if content is None:
            raise DraftWorkflowError("draft_source_missing", missing_detail)
        write_quarantine(
            project_path,
            episode,
            kind,
            content=content,
            violations=[],
            meta={
                "source": source or None,
                "base_fingerprint": script_review.content_fingerprint_of_data(data),
                FORMAL_EDIT_META_KEY: True,
            },
        )


def _validate_open_source(project_path: Path, episode: int, source: str) -> None:
    load_novel_source(project_path, source, episode=episode)


def _rewrite_invalid_draft(
    project_path: Path,
    episode: int,
    kind: str,
    content: dict[str, Any],
    violations: list[DraftViolation],
    meta: dict[str, Any],
) -> str:
    return quarantine_and_report(
        project_path,
        episode,
        kind,
        content=content,
        violations=violations,
        meta=meta,
    )


def _soft_violation_section(revalidation: ReferenceDraftRevalidation) -> str:
    """Soft-violation section from reassessment, appended to reports when violations block promotion.

    Share ``render_soft_violation_section`` with promotion / splitting receipts, changing only the handling explanation: the draft still exists and these hints are not violations to repair. Keeping the other explanation would make the Agent repeatedly rewrite downgrade hints as though violations remained unfixed."""
    return render_soft_violation_section(
        render_soft_violation_lines(revalidation.soft_violations), note=SOFT_VIOLATION_NOTE_QUARANTINED
    )


async def _promote_reference_script_plan(
    ctx: DraftContext,
    episode: int,
    draft: QuarantinedDraft,
    *,
    before_commit: Callable[[], None] | None = None,
) -> str:
    """Fully reassess a script_plan draft with its production-time validators; on success promote to formal script_plan and clear the draft.

    Return promotion receipt text through the same outlet as splitting (``reference_result_text``), appending soft violations after persisted statistics. Successful promotion is the first time the Agent receives downgrade hints on the main draft-repair loop, so success cannot return only a promoted boolean."""
    project_path = await ctx.aproject_path()
    project = await asyncio.to_thread(ctx.pm.load_project, ctx.project_name)
    try:
        revalidation = await revalidate_reference_script_plan_draft(
            project_path,
            project,
            episode,
            draft,
            config_resolver=ctx.config_resolver,
        )
    except VideoRequestFactsError as exc:
        raise DraftWorkflowError.video_facts(exc.failure) from exc
    except ValueError as exc:
        raise DraftWorkflowError("draft_invalid", f"❌ {exc}") from exc
    violations, flat_units, split_caps = revalidation.violations, revalidation.flat_units, revalidation.caps
    if revalidation.schema_failed:
        # Schema violations: write back the Agent's unchanged original content without coercion; coercing malformed fields would reshape its draft
        # so the report no longer matches what the Agent wrote.
        report = await run_sync_transaction(
            _rewrite_invalid_draft,
            project_path,
            episode,
            QUARANTINE_KIND_SCRIPT_PLAN,
            draft.content,
            violations,
            draft.meta,
        )
        raise DraftWorkflowError("draft_invalid", report + _soft_violation_section(revalidation), draft_refreshed=True)
    if violations:
        report = await run_sync_transaction(
            _rewrite_invalid_draft,
            project_path,
            episode,
            QUARANTINE_KIND_SCRIPT_PLAN,
            revalidation.flat_content(),
            violations,
            draft.meta,
        )
        raise DraftWorkflowError("draft_invalid", report + _soft_violation_section(revalidation), draft_refreshed=True)

    # Only the server issues IDs, allocating without reuse: retain IDs issued by position in the on-disk formal plan (unchanged promotion stays verbatim).
    # Allocate new IDs to extra units; locate downgrade hints in the receipt by the IDs actually persisted.
    formal_ids = await asyncio.to_thread(
        plan_item_ids, script_review.official_reference_script_plan_path(project_path, episode)
    )
    unit_ids = formal_ids[: len(flat_units)]
    if len(flat_units) > len(unit_ids):
        unit_ids += await run_sync_transaction(
            ctx.pm.reserve_item_ids, ctx.project_name, episode, "video_units", len(flat_units) - len(unit_ids)
        )
    units = build_reference_units_from_flat(
        flat_units,
        planning_project(project, revalidation.new_assets),
        episode=episode,
        max_refs=split_caps.max_refs,
        unit_ids=unit_ids,
    )
    # Persist only through lib.script.script_review.write_script_plan_locked: locking, baseline comparison and prompt_authoring draft cleanup
    # live there alone. Use the base_fingerprint recorded in meta at checkout / draft production: if another writer (Web save or splitting)
    # changed the formal file after draft production, abort promotion and return a conflict report for the Agent to merge,
    # never silently overwriting others' changes. Drafts lacking base_fingerprint promote without a baseline.
    expected = draft.meta.get("base_fingerprint", script_review.UNCHECKED_FINGERPRINT)
    try:
        await run_sync_transaction(
            _commit_reference_script_plan,
            project_path,
            episode,
            with_new_assets({"units": units}, revalidation.new_assets),
            expected,
            revalidation.basis,
            before_commit,
        )
    except script_review.ScriptPlanWriteConflict as conflict:
        raise DraftWorkflowError(
            "formal_revision_conflict",
            _render_script_plan_conflict_report(
                episode,
                draft,
                conflict,
                to_draft_shape=_reference_script_plan_draft_shape,
                field_hint="content.units",
            ),
        ) from conflict
    return reference_result_text(
        script_review.official_reference_script_plan_path(project_path, episode).relative_to(project_path),
        units,
        render_soft_violation_lines(
            [replace(soft, item_id=unit_ids[soft.item_index]) for soft in revalidation.soft_violations]
        ),
        action="晋升",
    )


def _render_script_plan_conflict_report(
    episode: int,
    draft: QuarantinedDraft,
    conflict: script_review.ScriptPlanWriteConflict,
    *,
    to_draft_shape: Callable[[dict[str, Any]], dict[str, Any] | None],
    field_hint: str,
) -> str:
    """Render a structured Agent report for optimistic-concurrency conflicts during promotion: latest content plus merge guidance.

    Enable merging in place: include current disk content converted to draft-layer shape (matching draft ``content`` for entry-by-entry comparison), and instruct explicit acceptance of the merged formal revision through ``patch_draft`` before retrying promotion.

    Variants supply ``to_draft_shape`` and ``field_hint``: draft shapes and editable fields differ. Including latest content in the wrong shape is more misleading than omitting it."""
    latest_content = to_draft_shape(conflict.current_content) if conflict.current_content is not None else None
    if latest_content is not None:
        latest = json.dumps(latest_content, ensure_ascii=False, indent=2)
        latest_block = f"当前正式 script_plan 的最新内容（与草稿 content 同形）：\n{latest}"
    else:
        latest_block = "当前正式文件不存在或不是合法的 script_plan JSON，无法附上最新内容；请自行读取该文件确认。"
    # Render fingerprints as JSON literals: if the formal file was deleted, the current value is null. Writing "None" would make the Agent
    # accept an incorrect baseline; every promotion would mismatch and return the same report, making the conflict impossible to resolve.
    actual_literal = json.dumps(script_review.published_revision(conflict.actual))
    doc_type = QUARANTINE_KIND_TO_DOC_TYPE[draft.kind]
    return (
        "❌ 晋升中止（并发冲突）：正式 script_plan 在本草稿产出后已被其他写入方（如 Web 端保存）修改，"
        "直接晋升会覆盖对方的修改，本次未写盘、草稿仍在场。\n"
        f"草稿基线指纹: {json.dumps(script_review.published_revision(conflict.expected))}；"
        f"盘上现值指纹: {actual_literal}\n\n"
        f"{latest_block}\n\n"
        f"处置：调用 open_draft 读取当前草稿与 formal_revision，对照上方最新内容合并 {field_hint}；"
        "再调用 patch_draft 提交完整 content，并把 formal_revision 作为 accept_formal_revision；"
        "该值为 null 时同样显式传入 null；"
        f'最后调用 {PROMOTE_TOOL_NAME}({{"episode_id": {episode}, "doc_type": "{doc_type}", '
        '"base_revision": "<patch_draft 返回的新 revision>"}) 重新晋升。'
    )


def _render_prompt_authoring_conflict_report(
    episode: int,
    draft: QuarantinedDraft,
    conflict: ScriptWriteConflict,
) -> str:
    latest = (
        json.dumps(conflict.current_content, ensure_ascii=False, indent=2)
        if conflict.current_content is not None
        else "null"
    )
    return (
        "❌ 晋升中止（并发冲突）：正式剧本在本草稿产出后已被修改，本次未写盘、草稿仍在场。\n"
        f"草稿基线指纹: {json.dumps(script_review.published_revision(conflict.expected))}；"
        f"盘上现值指纹: {json.dumps(script_review.published_revision(conflict.actual))}\n\n"
        f"当前正式剧本的最新内容：\n{latest}\n\n"
        "处置：调用 open_draft 读取当前草稿与 formal_revision，合并最新正式内容；"
        "再调用 patch_draft 提交完整 content，并把 formal_revision 作为 accept_formal_revision；"
        "该值为 null 时同样显式传入 null；"
        f'最后调用 {PROMOTE_TOOL_NAME}({{"episode_id": {episode}, "doc_type": "reference_prompt_authoring", '
        '"base_revision": "<patch_draft 返回的新 revision>"}) 重新晋升。'
    )


def _flatten_reference_script_plan_units(units: list[Any]) -> list[dict[str, Any]]:
    """Structured units in formal script_plan → flat draft units (inverse of ``build_reference_units_from_flat``).

    Exclude ``unit_id`` from drafts: mechanically derived from array positions, it would invite drift in the Agent-editable layer.

    Do not fail loudly on malformed on-disk units: carry missing or mistyped fields through **unchanged** (fill missing values with None / empty strings), letting promotion-side schema reassessment report each to the Agent. Preserve originals rather than normalize them to valid values: rewriting ``8.0`` to ``0`` shows a duration the Agent never wrote, so an out-of-tier report no longer matches disk. Retain the original so it can see the error. Likewise retain non-dict units as empty placeholders at their array positions for per-unit invalid-structure reports. Skipping them shortens the array; if all remaining units validate, promotion would silently overwrite the formal file and lose that unit."""
    flat: list[dict[str, Any]] = []
    for unit in units:
        if not isinstance(unit, dict):
            flat.append({"duration_seconds": None, "source_text": "", "text": ""})
            continue
        text = unit.get("text")
        flat.append(
            {
                "duration_seconds": unit.get("duration_seconds"),
                "source_text": unit.get("source_text", ""),
                "text": text if isinstance(text, str) else "",
            }
        )
    return flat


def _new_assets_of(content: dict[str, Any]) -> dict[str, Any]:
    """Carry episode additions from formal script_plan unchanged into drafts; do not add the key if absent."""
    return {NEW_ASSETS_FIELD: content[NEW_ASSETS_FIELD]} if NEW_ASSETS_FIELD in content else {}


def _reference_script_plan_draft_shape(content: dict[str, Any]) -> dict[str, Any] | None:
    """Formal reference script_plan content → flat draft shape; return None for invalid script_plan."""
    units = content.get("units")
    if not isinstance(units, list) or not units:
        return None
    return {"units": _flatten_reference_script_plan_units(units), **_new_assets_of(content)}


def _drama_script_plan_draft_shape(content: dict[str, Any]) -> dict[str, Any] | None:
    """Formal drama script_plan content → editable draft-layer storyboards; return None for invalid script_plan.

    Strip only ``needs_replan``: mechanically derived by speech admission, it would invite drift if editable; promotion derives it again from current ``content``. Carry other fields unchanged, including ``scene_id``: the prompt_authoring visual-layer alignment anchor. Promotion-side schema must report malformed values individually, without filling them here. Likewise retain non-dict storyboard entries unchanged: skipping them shortens the array; if all remaining storyboards validate, promotion would silently overwrite the formal file and lose that storyboard."""
    scenes = content.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return None
    flat: list[Any] = [
        {k: v for k, v in scene.items() if k != "needs_replan"} if isinstance(scene, dict) else scene
        for scene in scenes
    ]
    return {"title": content.get("title", ""), "scenes": flat, **_new_assets_of(content)}


class SingleScriptPlanDraftRevalidation(NamedTuple):
    """Read-time script_plan draft reassessment for the two routes with a single draft slot (drama / narration).

    ``schema_failed`` explicitly distinguishes two phases: True means failure of even the production-time schema (``content`` must be an empty dict; callers can only present ``draft.content`` unchanged). When False, ``content`` is the schema-coerced draft shape (drama ``{title, scenes}``, narration ``{segments}``) and promotion is allowed if ``violations`` is empty. Handling differs (unchanged versus coerced), so never infer phase from whether ``content`` is empty.

    Both routes share one type because fields and semantics are identical; separate types would duplicate the same normalization branch in ``revalidate_script_plan_draft``. Reference-to-video uses ``ReferenceDraftRevalidation`` because flat units and returned tiers have a different shape."""

    violations: list[DraftViolation]
    content: dict[str, Any]
    schema_failed: bool
    basis: ArtifactBasis | None


async def revalidate_drama_script_plan_draft(
    project_path: Path,
    project: dict[str, Any],
    episode: int,
    draft: QuarantinedDraft,
    *,
    config_resolver: ConfigResolver | None = None,
) -> SingleScriptPlanDraftRevalidation:
    """Fully reassess a drama script_plan draft with production-time validators; read only, without disk writes or draft cleanup.

    Use the production validator (``DramaNormalizedScript`` built from current capability tiers), not a copy: tiers change with project configuration, so formerly valid seconds may no longer fit. Admitting old enums would persist durations the provider rejects. Derive ``needs_replan`` again from current values, matching generation.

    Like the other two routes, independent of ``DraftContext``: promotion and content-confirmation read-time reassessment share this function; the latter has only ``ProjectManager``, not Agent tool ctx.

    Raise ``ValueError`` for unreadable source (``meta.source`` points to a missing / renamed path)."""
    # Source text may span hundreds of KB; offload synchronous disk reads to a thread. Read-time content-confirmation reassessment runs
    # in request coroutines; direct reads would occupy the event loop and slow concurrent requests. Match the other two routes.
    _novel_text, _prompt_inputs, script_plan_basis = await asyncio.to_thread(
        load_script_plan_source_with_basis,
        project_path,
        draft.meta.get("source"),
        project,
        episode,
        "drama",
    )
    _default_duration, supported_durations = await fetch_storyboard_durations(project, config_resolver=config_resolver)
    schema = build_drama_normalized_script_model(supported_durations)
    try:
        content = schema.model_validate(draft.content).model_dump()
    except ValidationError as exc:
        return SingleScriptPlanDraftRevalidation(
            schema_violations(exc, draft.content, "scenes", "scene_id"),
            {},
            schema_failed=True,
            basis=script_plan_basis,
        )

    raw_scenes = content.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        violation = DraftViolation("草稿的 content.scenes 必须是非空的分镜对象数组", code="schema_invalid")
        return SingleScriptPlanDraftRevalidation([violation], {}, schema_failed=True, basis=script_plan_basis)
    for scene in raw_scenes:
        admission = admit_script_unit("scenes", scene, ignore_marker=True)
        if admission.allowed:
            scene.pop("needs_replan", None)
        else:
            scene["needs_replan"] = True
    content = with_new_assets(content, content[NEW_ASSETS_FIELD])
    violations = collect_drama_violations(raw_scenes, project=project, new_assets=content.get(NEW_ASSETS_FIELD))
    return SingleScriptPlanDraftRevalidation(violations, content, schema_failed=False, basis=script_plan_basis)


async def _promote_drama_script_plan(
    ctx: DraftContext,
    episode: int,
    draft: QuarantinedDraft,
) -> str:
    """Fully reassess drama script_plan with production-time validators; on success promote to formal script_plan and clear the draft.

    Return promotion receipt text through the same outlet as production (``drama_script_plan_result_text``)."""
    project_path = await ctx.aproject_path()
    project = await asyncio.to_thread(ctx.pm.load_project, ctx.project_name)
    try:
        revalidation = await revalidate_drama_script_plan_draft(
            project_path,
            project,
            episode,
            draft,
            config_resolver=ctx.config_resolver,
        )
    except VideoRequestFactsError as exc:
        raise DraftWorkflowError.video_facts(exc.failure) from exc
    except ValueError as exc:
        raise DraftWorkflowError("draft_invalid", f"❌ {exc}") from exc

    if revalidation.violations:
        # On schema violations, write back the Agent's unchanged original content without coercion: reshaping malformed fields would make its draft
        # disagree with what it wrote when following the report. For schema-valid content, write back the coerced result instead.
        report = await run_sync_transaction(
            _rewrite_invalid_draft,
            project_path,
            episode,
            QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
            draft.content if revalidation.schema_failed else revalidation.content,
            revalidation.violations,
            draft.meta,
        )
        raise DraftWorkflowError("draft_invalid", report, draft_refreshed=True)

    content = revalidation.content
    script_plan_basis = revalidation.basis
    # Use the base_fingerprint recorded in meta at checkout: if another writer (Web save or normalization rerun) changed the formal file
    # after draft production, abort promotion and return a conflict report for the Agent to merge, never silently overwriting changes.
    expected = draft.meta.get("base_fingerprint", script_review.UNCHECKED_FINGERPRINT)
    script_plan_path = episode_drafts_dir(project_path, episode) / SCRIPT_PLAN_FILENAMES["drama"]
    await reissue_plan_item_ids(
        ctx.pm, ctx.project_name, episode, content, keep=await asyncio.to_thread(plan_item_ids, script_plan_path)
    )
    try:
        await run_sync_transaction(
            commit_single_script_plan,
            project_path,
            episode,
            script_plan_path,
            QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
            content,
            expected,
            script_plan_basis,
        )
    except script_review.ScriptPlanWriteConflict as conflict:
        raise DraftWorkflowError(
            "formal_revision_conflict",
            _render_script_plan_conflict_report(
                episode,
                draft,
                conflict,
                to_draft_shape=_drama_script_plan_draft_shape,
                field_hint="content.scenes",
            ),
        ) from conflict
    raw_scenes = content.get("scenes")
    return drama_script_plan_result_text(
        script_plan_path.relative_to(project_path),
        raw_scenes if isinstance(raw_scenes, list) else [],
        action="晋升",
    )


async def _open_drama_script_plan_for_edit(
    ctx: DraftContext,
    episode: int,
    source: str | None,
) -> None:
    """Check out this episode's formal drama script_plan as a draft (formal file unchanged); return Agent editing guidance.

    Match reference-to-video: draft-existence check, formal-file read and draft write all share one per-path locked critical section. Separating them outside the lock lets two concurrent requests both see no draft and then write one, silently allowing the later writer to overwrite the earlier content and meta."""
    project_path = await ctx.aproject_path()
    # Validate source before writing a draft: once persisted, meta.source supplies the basis for promotion artifacts. If its path is
    # missing, renamed or mistyped, promotion repeatedly fails while the existing draft prevents reopening to correct source, trapping the Agent
    # in a state it cannot edit out of. On validation failure, write nothing: invalid arguments leave no persistent side effects.
    if source is not None:
        try:
            await asyncio.to_thread(_validate_open_source, project_path, episode, source)
        except ValueError as exc:
            raise DraftWorkflowError("draft_open_failed", f"❌ {exc}") from exc

    script_plan_path = episode_drafts_dir(project_path, episode) / SCRIPT_PLAN_FILENAMES["drama"]
    await run_sync_transaction(
        _open_script_plan_draft,
        project_path,
        episode,
        script_plan_path,
        QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
        source,
        _drama_script_plan_draft_shape,
        f"❌ 集（id={episode}）没有可编辑的正式 script_plan（{script_plan_path.relative_to(project_path).as_posix()} 不存在、不是合法 JSON，"
        "或 scenes 不是非空数组）；首次生成请调用 generate_script_plan",
    )


async def revalidate_narration_script_plan_draft(
    project_path: Path,
    project: dict[str, Any],
    episode: int,
    draft: QuarantinedDraft,
    *,
    config_resolver: ConfigResolver | None = None,
) -> SingleScriptPlanDraftRevalidation:
    """Fully reassess narration script_plan with production-time validators; read only, without disk writes or draft cleanup.

    Use the same function as splitting (``collect_narration_violations``), never a simplified copy: promotion, content-confirmation read-time reassessment and production must share code, or admitting here but rejecting on the next generation would recur. Resolve capability tiers and source anew against current values: users may change configuration or source during quarantine.

    Like ``revalidate_reference_script_plan_draft``, independent of ``DraftContext``: content-confirmation read-time reassessment has only ``ProjectManager``, not Agent tool ctx. Both sites share this function without each loading project again.

    Raise ``ValueError`` for missing ``meta.source`` (damaged draft that cannot be reassessed)."""
    # meta.source records the source range at production time. A missing key means damaged meta: never silently reparse the default source.
    # If another source file was originally specified, assessing coverage against a different range asks a different question than production did.
    if "source" not in draft.meta:
        raise ValueError(
            f"草稿 {draft.path} 的 meta.source 缺失（产出时记录的源文范围）；"
            "请恢复该字段（指定源文时为其相对路径，按本集派生源文产出时为 null）后重试"
        )
    # Source text may span hundreds of KB; synchronous disk reads directly inside this async function would occupy the event
    # loop. Promotion tools use a separate session thread and are insensitive, but content-confirmation read-time reassessment runs in request
    # coroutines through the same code. Offload to a thread to avoid slowing other concurrent requests.
    novel_text, _prompt_inputs, script_plan_basis = await asyncio.to_thread(
        load_script_plan_source_with_basis,
        project_path,
        draft.meta["source"],
        project,
        episode,
        "narration",
    )
    _default_duration, supported_durations = await fetch_storyboard_durations(project, config_resolver=config_resolver)

    # Edited drafts first satisfy their production-time schema: response_schema and _parse_script_plan_json enforce fields and types
    # on splitting. Without this layer on promotion, a string duration_seconds or wholly deleted novel_text could enter formal files,
    # precisely the formal-content contamination this mechanism prevents. Outer-shape failures (missing / non-array /
    # empty segments) share the per-storyboard field-violation report path: both occur when the Agent edits drafts.
    raw_segments = draft.content.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        logger.debug("草稿 content.segments 形状非法: %s", type(raw_segments).__name__)
        violation = DraftViolation("草稿的 content.segments 必须是非空的分镜对象数组", code="schema_invalid")
        return SingleScriptPlanDraftRevalidation([violation], {}, schema_failed=True, basis=script_plan_basis)
    try:
        content = NarrationScriptPlanDraft.model_validate(draft.content).model_dump()
    except ValidationError as exc:
        return SingleScriptPlanDraftRevalidation(
            schema_violations(exc, draft.content, "segments", "segment_id"),
            {},
            schema_failed=True,
            basis=script_plan_basis,
        )
    content = with_new_assets(content, content[NEW_ASSETS_FIELD])

    violations = collect_narration_violations(
        content["segments"],
        episode=episode,
        supported_durations=supported_durations,
        project=project,
        new_assets=content.get(NEW_ASSETS_FIELD),
        novel_text=novel_text,
        # The reassessment source range comes from the draft's own meta.source: drafts opened without an explicit source
        # record null (this episode's derived source). If its formal script_plan originally used another source file, an unchanged,
        # freshly opened draft can fail coverage. Include both the range and its correction in the message so the Agent can recover:
        # while a draft exists it cannot be reopened, so patch_draft must update the source range.
        source_scope=(
            f"{coverage_source_scope(cast(str | None, draft.meta['source']), episode=episode)}"
            "，取自草稿的源文范围；若该范围与产出本集正式 script_plan 时不同，"
            "请调用 patch_draft，用 source 传入当初那个源文件的相对路径后重试"
        ),
    )
    return SingleScriptPlanDraftRevalidation(violations, content, schema_failed=False, basis=script_plan_basis)


def _narration_script_plan_draft_shape(content: dict[str, Any]) -> dict[str, Any] | None:
    """Formal narration script_plan content → draft-layer storyboards; return None for invalid script_plan.

    No mechanically derived fields need stripping (``segment_id`` is a model-authored alignment anchor, not position-derived). Draft and persisted layers share shape; retain only ``segments`` and episode additions. Carry storyboard entries unchanged, including non-dicts: skipping them shortens the array; if remaining entries validate, promotion would silently overwrite the formal file and lose that segment."""
    segments = content.get("segments")
    if not isinstance(segments, list) or not segments:
        return None
    return {"segments": list(segments), **_new_assets_of(content)}


async def _promote_narration_script_plan(
    ctx: DraftContext,
    episode: int,
    draft: QuarantinedDraft,
) -> str:
    """Fully reassess narration script_plan with production-time validators; on success promote to formal script_plan and clear the draft.

    Return promotion receipt text through the same outlet as splitting (``narration_script_plan_result_text``)."""
    project_path = await ctx.aproject_path()
    project = await asyncio.to_thread(ctx.pm.load_project, ctx.project_name)
    try:
        revalidation = await revalidate_narration_script_plan_draft(
            project_path,
            project,
            episode,
            draft,
            config_resolver=ctx.config_resolver,
        )
    except VideoRequestFactsError as exc:
        raise DraftWorkflowError.video_facts(exc.failure) from exc
    except ValueError as exc:
        raise DraftWorkflowError("draft_invalid", f"❌ {exc}") from exc

    # On schema violations, write back the Agent's unchanged original content without coercion: reshaping malformed fields would make its draft
    # disagree with what it wrote when following the report. For schema-valid content, write back the coerced result instead.
    if revalidation.violations:
        content = draft.content if revalidation.schema_failed else revalidation.content
        report = await run_sync_transaction(
            _rewrite_invalid_draft,
            project_path,
            episode,
            QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
            content,
            revalidation.violations,
            draft.meta,
        )
        raise DraftWorkflowError("draft_invalid", report, draft_refreshed=True)

    # Use the base_fingerprint recorded in meta at checkout / draft production: if another writer (Web save or splitting rerun) changes the formal file
    # afterward, abort promotion and return a conflict report for the Agent to merge, never silently overwriting others' changes.
    # Drafts lacking base_fingerprint promote without a baseline.
    expected = draft.meta.get("base_fingerprint", script_review.UNCHECKED_FINGERPRINT)
    script_plan_path = narration_script_plan_path(project_path, episode)
    await reissue_plan_item_ids(
        ctx.pm,
        ctx.project_name,
        episode,
        revalidation.content,
        keep=await asyncio.to_thread(plan_item_ids, script_plan_path),
    )
    try:
        await run_sync_transaction(
            commit_single_script_plan,
            project_path,
            episode,
            script_plan_path,
            QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
            revalidation.content,
            expected,
            revalidation.basis,
        )
    except script_review.ScriptPlanWriteConflict as conflict:
        conflict_report = _render_script_plan_conflict_report(
            episode,
            draft,
            conflict,
            to_draft_shape=_narration_script_plan_draft_shape,
            field_hint="content.segments",
        )
        raise DraftWorkflowError("formal_revision_conflict", conflict_report) from conflict
    raw_segments = revalidation.content.get("segments")
    return narration_script_plan_result_text(
        script_plan_path.relative_to(project_path),
        raw_segments if isinstance(raw_segments, list) else [],
        action="晋升",
    )


async def _open_narration_script_plan_for_edit(
    ctx: DraftContext,
    episode: int,
    source: str | None,
) -> None:
    """Check out this episode's formal narration script_plan as a draft (formal file unchanged); return Agent editing guidance.

    Match the other routes: draft-existence check, formal-file read and draft write all share one per-path locked critical section. Separating them outside the lock lets two concurrent requests both see no draft and then write one, silently allowing the later writer to overwrite the earlier content and meta."""
    project_path = await ctx.aproject_path()
    # Validate source before writing a draft: once persisted, meta.source supplies promotion's source-coverage reassessment. If its path is
    # missing, renamed or mistyped, promotion repeatedly fails while the existing draft prevents reopening to correct source, trapping the Agent
    # in a state it cannot edit out of. On validation failure, write nothing: invalid arguments leave no persistent side effects.
    if source is not None:
        try:
            await asyncio.to_thread(_validate_open_source, project_path, episode, source)
        except ValueError as exc:
            raise DraftWorkflowError("draft_open_failed", f"❌ {exc}") from exc

    script_plan_path = narration_script_plan_path(project_path, episode)
    await run_sync_transaction(
        _open_script_plan_draft,
        project_path,
        episode,
        script_plan_path,
        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
        source,
        _narration_script_plan_draft_shape,
        f"❌ 集（id={episode}）没有可编辑的正式 script_plan（{script_plan_path.relative_to(project_path).as_posix()} 不存在、不是合法 JSON，"
        "或 segments 不是非空数组）；首次生成请调用 generate_script_plan",
    )


class ScriptPlanDraftRevalidation(NamedTuple):
    """script_plan draft reassessment dispatched by kind, normalized for presentation.

    ``content`` is the draft body to present: schema-valid content has its coerced current shape (duration and similar values reassessed against current tiers); otherwise None tells callers to present the Agent-edited ``draft.content`` unchanged. Shape varies by route (reference-to-video units, drama title+scenes, narration segments); presentation renders its own route's cards.

    ``soft_violations`` contains entry-specific downgrade hints, produced only by reference-to-video; empty on schema failure."""

    violations: list[DraftViolation]
    content: dict[str, Any] | None
    soft_violations: tuple[SoftViolation, ...] = ()


class _SingleScriptPlanRevalidator(Protocol):
    """Revalidator call shape for the dispatch table: shared draft-location arguments, with capability resolvers injected by keyword."""

    def __call__(
        self,
        project_path: Path,
        project: dict[str, Any],
        episode: int,
        draft: QuarantinedDraft,
        *,
        config_resolver: ConfigResolver | None = None,
    ) -> Awaitable[SingleScriptPlanDraftRevalidation]: ...


_SINGLE_SCRIPT_PLAN_REVALIDATORS: dict[str, _SingleScriptPlanRevalidator] = {
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN: revalidate_drama_script_plan_draft,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN: revalidate_narration_script_plan_draft,
}


async def revalidate_script_plan_draft(
    project_path: Path,
    project: dict[str, Any],
    episode: int,
    draft: QuarantinedDraft,
    *,
    config_resolver: ConfigResolver | None = None,
) -> ScriptPlanDraftRevalidation:
    """Dispatch a script_plan draft to its route's revalidator and return a route-neutral result.

    Content-confirmation read-time reassessment dispatches by kind through this entry point: matching signatures and structurally corresponding results keep it independent of route internals and prevent missing a dispatch site when adding variants. Promoters still call their own revalidators directly because they need persistence fields such as basis and schema_failed that presentation does not use.

    Return reference-to-video soft violations with the result for entry-specific downgrade hints at presentation sites.

    Raise ``ValueError`` when ``draft.kind`` is not one of the three script_plan sources (for example, a mistakenly passed prompt_authoring draft)."""
    if draft.kind == QUARANTINE_KIND_SCRIPT_PLAN:
        reference = await revalidate_reference_script_plan_draft(
            project_path,
            project,
            episode,
            draft,
            config_resolver=config_resolver,
        )
        content = None if reference.schema_failed else reference.flat_content()
        return ScriptPlanDraftRevalidation(reference.violations, content, tuple(reference.soft_violations))
    revalidator = _SINGLE_SCRIPT_PLAN_REVALIDATORS.get(draft.kind)
    if revalidator is None:
        raise ValueError(f"不是 script_plan 草稿来源，无法重判: {draft.kind}")
    single = await revalidator(project_path, project, episode, draft, config_resolver=config_resolver)
    return ScriptPlanDraftRevalidation(single.violations, None if single.schema_failed else single.content)


async def _open_reference_script_plan_for_edit(
    ctx: DraftContext,
    episode: int,
    source: str | None,
) -> None:
    """Check out this episode's formal reference-to-video script_plan as a draft (formal file unchanged); return Agent editing guidance."""
    project_path = await ctx.aproject_path()
    # Validate source before writing a draft: once persisted, meta.source supplies promotion reassessment. If the path
    # is missing, renamed or mistyped, promotion repeatedly fails in load_novel_source while the existing draft
    # prevents reopening to correct source, trapping the Agent in a state it cannot edit out of. On validation failure, write nothing:
    # invalid arguments leave no persistent side effects.
    if source is not None:
        try:
            await asyncio.to_thread(_validate_open_source, project_path, episode, source)
        except ValueError as exc:
            raise DraftWorkflowError("draft_open_failed", f"❌ {exc}") from exc

    # Checking draft existence, reading the formal file and writing the draft must share one locked critical section. If done separately
    # outside the lock, two concurrent checkout requests for one episode could both see no draft and then both write one,
    # silently letting the later writer overwrite the earlier content and meta.source. The write critical section uses the same lock as Web saves
    # and migrations; reads also hold it to avoid checking out a half-written script_plan.
    script_plan_path = script_review.official_reference_script_plan_path(project_path, episode)
    await run_sync_transaction(
        _open_script_plan_draft,
        project_path,
        episode,
        script_plan_path,
        QUARANTINE_KIND_SCRIPT_PLAN,
        source,
        _reference_script_plan_draft_shape,
        f"❌ 集（id={episode}）没有可编辑的正式 script_plan（{script_plan_path.relative_to(project_path).as_posix()} 不存在、不是合法 JSON，"
        "或 units 不是非空数组）；首次生成请调用 generate_script_plan",
    )


_SCRIPT_PLAN_EDIT_OPENERS: dict[
    str,
    Callable[[DraftContext, int, str | None], Awaitable[None]],
] = {
    QUARANTINE_KIND_SCRIPT_PLAN: _open_reference_script_plan_for_edit,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN: _open_drama_script_plan_for_edit,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN: _open_narration_script_plan_for_edit,
}

_SINGLE_SCRIPT_PLAN_PROMOTERS: dict[
    str,
    Callable[[DraftContext, int, QuarantinedDraft], Awaitable[str]],
] = {
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN: _promote_drama_script_plan,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN: _promote_narration_script_plan,
}


class DraftWorkflow:
    """Open, patch, promote, and discard drafts without host envelopes."""

    def __init__(self, ctx: DraftContext):
        self.ctx = ctx

    async def _kind(self, episode: int, doc_type: str, *, allow_stale_discard: bool = False) -> str:
        if isinstance(episode, bool) or episode < 1:
            raise DraftWorkflowError("invalid_request", "episode must be a positive integer")
        kind = DOC_TYPE_TO_QUARANTINE_KIND.get(doc_type)
        if kind is None:
            raise DraftWorkflowError("invalid_request", f"unsupported doc_type: {doc_type}")
        if allow_stale_discard:
            return kind
        project = await asyncio.to_thread(self.ctx.pm.load_project, self.ctx.project_name)
        active_script_plan = script_review.script_plan_quarantine_kind(project)
        compatible = kind == active_script_plan or (
            kind == QUARANTINE_KIND_PROMPT_AUTHORING and uses_reference_video_units(project)
        )
        if not compatible:
            raise DraftWorkflowError(
                "doc_type_not_applicable", f"doc_type {doc_type} does not match the project workflow"
            )
        return kind

    async def resolve_kind(self, episode: int, doc_type: str) -> str:
        """Resolve ``doc_type`` to a draft source and verify it applies to the current project workflow."""
        return await self._kind(episode, doc_type)

    def _reject_confirmed_script_plan_edit(self, episode: int, kind: str, draft: QuarantinedDraft | None) -> None:
        """Confirmed script planning is read only: reject checking out an editing copy, modifying one or promoting one.

        ``draft`` is None when opening a new editing copy; drafts from planning reruns lack editing markers and are unrestricted."""
        if kind == QUARANTINE_KIND_PROMPT_AUTHORING:
            return
        if draft is not None and draft.meta.get(FORMAL_EDIT_META_KEY) is not True:
            return
        project = self.ctx.pm.load_project(self.ctx.project_name)
        if script_review.formal_script_plan_confirmed(self.ctx.project_path, project, episode):
            raise DraftWorkflowError("script_plan_confirmed", _script_plan_confirmed_detail(episode))

    def _read_if_present(
        self,
        episode: int,
        kind: str,
    ) -> dict[str, Any] | None:
        draft = read_quarantine(self.ctx.project_path, episode, kind)
        if draft is not None:
            payload = draft_payload(draft)
            payload["formal_revision"] = script_review.published_revision(
                script_review.content_fingerprint(self._formal_path(episode, kind))
            )
            return payload
        if quarantine_exists(self.ctx.project_path, episode, kind):
            detail = f"集（id={episode}）{doc_type_for_kind(kind)} draft is not a valid JSON envelope"
            raise DraftWorkflowError("draft_not_found", detail)
        return None

    def _read(
        self,
        episode: int,
        kind: str,
    ) -> dict[str, Any]:
        payload = self._read_if_present(episode, kind)
        if payload is not None:
            return payload
        detail = f"集（id={episode}）has no {doc_type_for_kind(kind)} draft"
        raise DraftWorkflowError("draft_not_found", detail)

    def _draft_snapshot(
        self,
        episode: int,
        kind: str,
    ) -> tuple[QuarantinedDraft | None, str | None]:
        draft = read_quarantine(self.ctx.project_path, episode, kind)
        return draft, draft_revision(draft) if draft is not None else None

    def _quarantine_lock_target(self, episode: int, kind: str) -> tuple[Path, Path, ProjectManager]:
        """Project directory, draft path and locking ProjectManager: locating and constructing touch disk; offload together in one thread call."""
        project_path = self.ctx.project_path
        return project_path, quarantine_path(project_path, episode, kind), ProjectManager(self.ctx.data_root)

    def _formal_path(self, episode: int, kind: str) -> Path:
        if kind == QUARANTINE_KIND_PROMPT_AUTHORING:
            return self.ctx.project_path / "scripts" / episode_script_filename(episode)
        project = self.ctx.pm.load_project(self.ctx.project_name)
        path = script_review.script_plan_path(self.ctx.project_path, project, episode)
        if path is None:
            raise ValueError("project has no formal script_plan document")
        return path

    def _open_reference_prompt_authoring(
        self,
        episode: int,
        resolved: str,
    ) -> None:
        script = self.ctx.pm.load_script_readonly(
            self.ctx.project_name,
            episode_script_filename(episode),
        )
        units = script.get("video_units")
        if not isinstance(units, list) or not units:
            raise DraftWorkflowError("draft_source_missing", "formal reference prompt_authoring has no video_units")
        write_quarantine(
            self.ctx.project_path,
            episode,
            resolved,
            content={
                "title": script.get("title") or episode_title(self.ctx.pm.load_project(self.ctx.project_name), episode),
                "units": [{"text": unit.get("text", "")} for unit in units if isinstance(unit, dict)],
            },
            violations=[],
            meta={"base_fingerprint": script_review.content_fingerprint_of_data(script), FORMAL_EDIT_META_KEY: True},
        )

    async def open(
        self,
        episode: int,
        doc_type: str,
        source: str | None = None,
    ) -> dict[str, Any]:
        resolved = await self._kind(episode, doc_type)
        _, path, locks = await asyncio.to_thread(self._quarantine_lock_target, episode, resolved)

        try:
            async with locks.async_file_lock(path):
                existing = await asyncio.to_thread(self._read_if_present, episode, resolved)
                if existing is not None:
                    return existing
                await asyncio.to_thread(self._reject_confirmed_script_plan_edit, episode, resolved, None)
                if resolved == QUARANTINE_KIND_PROMPT_AUTHORING:
                    await run_sync_transaction(
                        self._open_reference_prompt_authoring,
                        episode,
                        resolved,
                    )
                else:
                    await _SCRIPT_PLAN_EDIT_OPENERS[resolved](self.ctx, episode, source)
                return await asyncio.to_thread(self._read, episode, resolved)
        except DraftWorkflowError:
            raise
        except Exception as exc:
            raise DraftWorkflowError("draft_open_failed", str(exc)) from exc

    def _patch_locked(
        self,
        episode: int,
        resolved: str,
        content: dict[str, Any],
        base_revision: str,
        accept_formal_revision: str | None,
        accepts_formal_revision: bool,
        source: str | None,
        updates_source: bool,
        before_commit: Callable[[], None] | None,
    ) -> dict[str, Any]:
        path = quarantine_path(self.ctx.project_path, episode, resolved)
        draft = read_quarantine(self.ctx.project_path, episode, resolved)
        if draft is None:
            return self._read(episode, resolved)
        actual_revision = draft_revision(draft)
        if script_review.fingerprint_of_revision(base_revision) != script_review.fingerprint_of_revision(
            actual_revision
        ):
            raise DraftWorkflowError(
                "revision_conflict",
                f"draft revision changed: expected {base_revision}, actual {actual_revision}",
            )
        self._reject_confirmed_script_plan_edit(episode, resolved, draft)
        meta = draft.meta
        if updates_source:
            if resolved == QUARANTINE_KIND_PROMPT_AUTHORING:
                raise DraftWorkflowError("invalid_request", "source is only valid for script_plan drafts")
            load_novel_source(self.ctx.project_path, source, episode=episode)
            meta = {**meta, "source": source}
        if accepts_formal_revision:
            actual_formal_revision = script_review.content_fingerprint(self._formal_path(episode, resolved))
            if script_review.fingerprint_of_revision(accept_formal_revision) != actual_formal_revision:
                raise DraftWorkflowError(
                    "formal_revision_conflict",
                    "formal document changed again: "
                    f"expected {accept_formal_revision}, "
                    f"actual {script_review.published_revision(actual_formal_revision)}",
                )
            meta = {**meta, "base_fingerprint": actual_formal_revision}
        if before_commit is not None:
            before_commit()
        atomic_write_json(
            path,
            quarantine_envelope(
                draft.kind,
                draft.episode,
                content=content,
                violations=draft.violations,
                meta=meta,
            ),
        )
        announce_draft_change(self.ctx.project_path, path)
        record_project_activity(self.ctx.project_path)
        receipt = self._read(episode, resolved)
        # Draft violations are the previous production / promotion assessment of the old body and become outdated after editing. Omit them from the receipt
        # so callers do not mistake an old report for this edit's result. Fully reassess current violations on promotion.
        receipt.pop("violations", None)
        return receipt

    async def patch(
        self,
        episode: int,
        doc_type: str,
        content: dict[str, Any],
        base_revision: str,
        accept_formal_revision: str | None = None,
        accepts_formal_revision: bool = False,
        source: str | None = None,
        updates_source: bool = False,
        *,
        before_commit: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        resolved = await self._kind(episode, doc_type)
        _, path, locks = await asyncio.to_thread(self._quarantine_lock_target, episode, resolved)
        async with locks.async_file_lock(path):
            return await run_sync_transaction(
                self._patch_locked,
                episode,
                resolved,
                content,
                base_revision,
                accept_formal_revision,
                accepts_formal_revision,
                source,
                updates_source,
                before_commit,
            )

    async def promote(
        self,
        episode: int,
        doc_type: str,
        base_revision: str,
        *,
        before_commit: Callable[[], None] | None = None,
        before_lock: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        resolved = await self._kind(episode, doc_type)
        project_path, path, locks = await asyncio.to_thread(self._quarantine_lock_target, episode, resolved)
        result_path: Path | None = None
        # Promotion receipts: each of the three script_plan variants uses its own promoter and shares its statistics section with production receipts
        # (reference-to-video also appends soft violations). Do not reconstruct them here. Prompt authoring has no corresponding production receipt:
        # it writes the formal script and production emits no receipt. Only its path can be reported, directly in the branch below.
        message: str
        if before_lock is not None:
            before_lock()
        async with locks.async_file_lock(path):
            draft, actual_revision = await asyncio.to_thread(
                self._draft_snapshot,
                episode,
                resolved,
            )
            if draft is None:
                return await asyncio.to_thread(self._read, episode, resolved)
            if script_review.fingerprint_of_revision(base_revision) != script_review.fingerprint_of_revision(
                actual_revision
            ):
                raise DraftWorkflowError(
                    "revision_conflict",
                    f"draft revision changed: expected {base_revision}, actual {actual_revision}",
                )
            await asyncio.to_thread(self._reject_confirmed_script_plan_edit, episode, resolved, draft)
            try:
                if resolved in _SINGLE_SCRIPT_PLAN_PROMOTERS:
                    message = await _SINGLE_SCRIPT_PLAN_PROMOTERS[resolved](self.ctx, episode, draft)
                elif resolved == QUARANTINE_KIND_SCRIPT_PLAN:
                    message = await _promote_reference_script_plan(
                        self.ctx,
                        episode,
                        draft,
                        before_commit=before_commit,
                    )
                else:
                    # Prompt authoring reads only formal scripts: unconfirmed rerun planning does not block promotion, matching the authoring entry point.
                    generator = await ScriptGenerator.create(
                        project_path,
                        config_resolver=self.ctx.config_resolver,
                    )
                    promote_kwargs = (
                        {"expected_fingerprint": draft.meta["base_fingerprint"]}
                        if "base_fingerprint" in draft.meta
                        else {}
                    )
                    result_path = await generator.promote_reference_prompt_authoring_draft(
                        episode,
                        _prompt_authoring_lock_held=True,
                        **promote_kwargs,
                    )
                    message = f"✅ 提示词编写晋升（正式剧本）已保存: {_project_relative(result_path, project_path)}"
            except (DraftWorkflowError, ModelNotInCatalogError):
                # Propagate ModelNotInCatalogError unchanged: callers use it to guide model reselection.
                raise
            except ScriptWriteConflict as exc:
                raise DraftWorkflowError(
                    "formal_revision_conflict", _render_prompt_authoring_conflict_report(episode, draft, exc)
                ) from exc
            except DraftViolation as exc:
                # If violations block prompt-authoring promotion, the report is already written back to the draft (code="quarantined"). Other violations come from formal scripts;
                # those with wording keys (formal units failing promotion preflight) explain reasons using the UI's unit terminology.
                refreshed = exc.code == "quarantined"
                notice = exc.params is not None and not refreshed
                raise DraftWorkflowError(
                    "draft_invalid",
                    str(exc),
                    draft_refreshed=refreshed,
                    notice_key=exc.code if notice else None,
                    notice_params=dict(exc.params or {}) if notice else None,
                ) from exc
            except Exception as exc:
                raise DraftWorkflowError("draft_promote_failed", str(exc)) from exc
        value: dict[str, Any] = {
            "episode": episode,
            "doc_type": doc_type,
            "promoted": True,
            "message": message,
        }
        if result_path is not None:
            value["path"] = _project_relative(result_path, project_path)
        return value

    async def save(
        self,
        episode: int,
        doc_type: str,
        content: dict[str, Any],
        base_revision: str,
    ) -> dict[str, Any]:
        """Creator manually repairs a repair draft: after writing the body, fully reassess by promotion rules and adopt as formal content if violations clear.

        On adoption return ``adopted=True``; if violations remain, refresh the report against current values and return ``adopted=False`` with the refreshed draft. Reject editable drafts, which the Agent owns. Failures outside reassessment (concurrency conflicts, unresolved video capabilities) still raise; the body is already written to the draft and is not lost."""
        resolved = await self._kind(episode, doc_type)
        current = await asyncio.to_thread(lambda: read_quarantine(self.ctx.project_path, episode, resolved))
        if current is not None and draft_owner(current) == DRAFT_OWNER_AGENT:
            raise DraftWorkflowError(
                "draft_agent_owned", f"集（id={episode}）{doc_type} draft is being edited by the agent"
            )
        patched = await self.patch(episode, doc_type, content, base_revision)
        try:
            promoted = await self.promote(episode, doc_type, patched["revision"])
        except DraftWorkflowError as exc:
            if not exc.draft_refreshed:
                raise
            refreshed = await asyncio.to_thread(self._read, episode, resolved)
            return {"episode": episode, "doc_type": doc_type, "adopted": False, "draft": refreshed}
        return {"episode": episode, "doc_type": doc_type, "adopted": True, "message": promoted["message"]}

    async def discard(
        self,
        episode: int,
        doc_type: str,
        base_revision: str,
    ) -> dict[str, Any]:
        resolved = await self._kind(episode, doc_type, allow_stale_discard=True)
        project_path, path, locks = await asyncio.to_thread(self._quarantine_lock_target, episode, resolved)
        async with locks.async_file_lock(path):
            draft, actual_revision = await asyncio.to_thread(
                self._draft_snapshot,
                episode,
                resolved,
            )
            if draft is not None and script_review.fingerprint_of_revision(
                base_revision
            ) != script_review.fingerprint_of_revision(actual_revision):
                raise DraftWorkflowError(
                    "revision_conflict",
                    f"draft revision changed: expected {base_revision}, actual {actual_revision}",
                )
            discarded = await run_sync_transaction(discard_quarantine, project_path, episode, resolved)
        return {"episode": episode, "doc_type": doc_type, "discarded": discarded}


def doc_type_for_kind(kind: str) -> str:
    return next(doc_type for doc_type, mapped in DOC_TYPE_TO_QUARANTINE_KIND.items() if mapped == kind)
