"""script_generator.py - Script generator

Read structured script-plan intermediate files and invoke text-generation backends to produce final JSON scripts.
"""

import asyncio
import copy
import functools
import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional, cast

from pydantic import BaseModel, TypeAdapter, ValidationError

from lib.artifacts.artifact_activation import (
    ArtifactInputClaim,
    active_artifact_currency_resolver,
    assert_current_artifact_input_claims_usable,
    resolve_usable_artifact_input_claim,
)
from lib.artifacts.artifact_manifest import ArtifactBasisDescriptor, ArtifactKey
from lib.artifacts.artifact_provenance import (
    build_ad_episode_script_basis,
    project_ad_episode_script_inputs,
)
from lib.backends.providers import CallPurpose
from lib.backends.text_backends.base import DEFAULT_MAX_OUTPUT_TOKENS, TextGenerationRequest, TextTaskType
from lib.backends.text_generator import TextGenerator
from lib.config.resolver import ConfigResolver, VideoGenerationType, video_bucket_for_generation_mode
from lib.db import async_session_factory
from lib.episode.episode_ids import episode_title
from lib.episode.episode_paths import (
    REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME,
    SCRIPT_PLAN_FILENAMES,
    SCRIPT_PLAN_LEGACY_FILENAMES,
    episode_drafts_dir,
)
from lib.generation.video_request_facts import (
    CONFIGURED_VIDEO_IDENTITY,
    VideoRequestFacts,
    VideoRequestFactsError,
    VideoRequestFactsFailure,
    VideoRoute,
    evaluate_video_request_facts,
    planning_durations,
    reference_migration_durations,
    require_video_request_facts,
)
from lib.i18n import _ as translate
from lib.infra.async_thread import run_sync_transaction
from lib.infra.content_digest import sha256_file
from lib.infra.text_utils import strip_json_code_fences
from lib.project.asset_types import asset_name_comparison_key
from lib.project.item_id_history import issue_landing_item_ids
from lib.project.project_manager import ProjectManager, ScriptWriteConflict
from lib.prompts.prompt_builders_ad import (
    build_ad_prompt,
    build_ad_reference_prompt,
    build_ad_reference_prompt_authoring_prompt,
    build_ad_shot_prompt_authoring_prompt,
)
from lib.prompts.prompt_builders_reference import build_reference_video_prompt
from lib.prompts.prompt_builders_script import (
    build_drama_prompt,
    build_narration_prompt,
    render_drama_content_for_prompt_authoring,
)
from lib.script.draft_quarantine import (
    PROMOTE_TOOL_NAME,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    clear_quarantine,
    draft_revision,
    quarantine_and_report,
    quarantine_path,
    read_quarantine,
)
from lib.script.draft_violation import locate_violations, locate_violations_by_id, schema_violations
from lib.script.item_excerpt import find_item
from lib.script.plan_new_assets import (
    NEW_ASSETS_FIELD,
    NewAssetResolution,
    NewAssetsError,
    UnregisteredReferencesError,
    planning_project,
    resolve_new_assets,
    unregistered_references,
)
from lib.script.prompt_authoring_scope import (
    PromptAuthoringSelection,
    PromptOverwrite,
    PromptOverwriteRequired,
    select_prompt_authoring,
)
from lib.script.reference_video.draft_validation import (
    DraftViolation,
    DraftViolations,
    assert_dialogue_preserved,
    dialogue_speakers,
    dialogue_spoken_seconds,
    validate_dialogue_load,
    validate_unit_text,
    violation_items,
)
from lib.script.reference_video.duration_slots import resolve_duration_slot
from lib.script.reference_video.request_projection import ReferenceUnitHydration
from lib.script.reference_video.unit_capabilities import hydrate_reference_units
from lib.script.script_document import (
    build_materialized_script,
    episode_ledger_entry,
    finish_script_document,
    ledger_outline,
    prepare_script_entries,
)
from lib.script.script_models import (
    AD_TARGET_DURATION_DRIFT_THRESHOLD,
    AdEpisodeScript,
    AdReferenceFlatScript,
    AdVisualScript,
    DramaEpisodeScript,
    DramaSceneContent,
    DramaVisualScript,
    NarrationEpisodeScript,
    NarrationScriptPlanDraft,
    NarrationVisualEpisodeScript,
    ReferencePromptAuthoringFlatScript,
    ReferenceScriptPlanDraft,
    ReferenceVideoScript,
    build_episode_script_model,
    script_duration_total,
    with_new_assets_field,
)
from lib.script.script_plan_entries import ScriptPlanKind, entry_id_field, plan_variant
from lib.script.script_review import (
    FormalScriptOverwrite,
    ScriptPlanWriteConflict,
    content_fingerprint,
    content_fingerprint_of_data,
    fingerprint_of_revision,
    formal_script_filename,
    formal_script_overwrite,
    formal_script_plan_lock,
    migrate_script_plan_draft_in_place,
    script_plan_path,
)
from lib.script.script_skeleton import resolve_declared_kind, resolve_kind_items, rewrite_episode_prefix
from lib.script.script_structure_validator import validate_script_structure
from lib.speech.speech_composition import require_script_unit_admitted
from lib.speech.speech_rate import project_speech_rate_override

logger = logging.getLogger(__name__)


class _UnsetExpectedFingerprint:
    pass


_UNSET_EXPECTED_FINGERPRINT = _UnsetExpectedFingerprint()

# Normalize drama script_plan durations like nonstrict DramaSceneContent.duration_seconds,
# keeping validation/persistence consistent for "4" / 4.0. Obtain defaults from field declarations, never duplicate literals.
_DURATION_ADAPTER = TypeAdapter(int)
_DRAMA_DEFAULT_DURATION = DramaSceneContent.model_fields["duration_seconds"].default

#: Dry-run reply without authoring targets: real execution makes no model call, so no prompt exists to preview.
_NO_ENTRY_TO_AUTHOR_NOTE = (
    "本次没有要编写的条目：运行时不会调用文本模型；要覆盖已有视觉层，请用 entry_ids 点名条目并传 rewrite=true。"
)

#: ad reference-body admission: these speech-ownership issues set needs_replan without blocking persistence.
_AD_UNIT_REPLAN_CODES = frozenset({"mixed_speech", "empty_speaker", "parse_failed"})

# Visual LLM responses by skeleton kind → validation model. Reference units separately rewrite structure-preserving bodies.
_VISUAL_RESPONSE_SCHEMA: dict[str, type[BaseModel]] = {
    "segments": NarrationVisualEpisodeScript,
    "scenes": DramaVisualScript,
    "shots": AdVisualScript,
}

# Quality thresholds catch only extremely short samples; complete normal descriptions should far exceed them.
_QUALITY_PROBE_SCENE_MIN_LEN = 40
_QUALITY_PROBE_ACTION_MIN_LEN = 25
_QUALITY_PROBE_UNIT_TEXT_MIN_LEN = 15

# Skeleton kind → response model. Model classes are higher-level dependencies outside the narrow SKELETONS table.
# Keys correspond exactly to SKELETONS; exhaustive assertions fail individually when a fifth skeleton is added.
_KIND_PARSE_SCHEMA: dict[str, type[BaseModel]] = {
    "segments": NarrationEpisodeScript,
    "scenes": DramaEpisodeScript,
    "shots": AdEpisodeScript,
    "video_units": ReferenceVideoScript,
}


class PromptAuthoringTargetError(ValueError):
    """Invalid prompt-authoring target: no formal episode script, or ``entry_ids`` names items absent from it."""


class PromptAuthoringUnitRejected(ValueError):
    """Current formal-script units awaiting authoring failed preflight before invoking the text model.

    This is an input problem: authoring preserves dialogue verbatim, so retrying unchanged input inevitably
    fails again. Edit the unit on the timeline instead. ``code`` and ``params`` identify task failures for
    localized UI messages. ``params["position"]`` is the unit's 1-based episode video-unit position, used
    by the UI to identify units; Agent-facing messages locate them by unit ID.
    """

    def __init__(self, message: str, *, code: str, params: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.params = params


#: Formal-unit preflight problem codes → promotion copy keys. Authoring reports the same preflight as task failure;
#: draft promotion instead explains draft nonadoption, requiring separate copy.
_FORMAL_UNIT_PROMOTION_KEYS: dict[str, str] = {
    "prompt_authoring_dialogue_overload": "draft_formal_unit_dialogue_overload",
    "prompt_authoring_duration_off_tier": "draft_formal_unit_duration_off_tier",
    "prompt_authoring_unit_invalid": "draft_formal_unit_invalid",
}


class UnitDurationOffTier(ValueError):
    """A unit awaiting authoring has a duration outside its reference bucket's effective tiers, usually narrowed
    by model, resolution, or reference-image state changes.

    ``unit_id`` / ``duration_seconds`` / ``tiers`` let callers build user messages by their conventions;
    messages here address Agents. ``position`` is the unit's 1-based UI position within evaluated content,
    or None for formal scripts, whose callers locate units themselves.
    """

    def __init__(
        self, message: str, *, unit_id: str, duration_seconds: int, tiers: list[int], position: int | None = None
    ) -> None:
        super().__init__(message)
        self.unit_id = unit_id
        self.duration_seconds = duration_seconds
        self.tiers = tiers
        self.position = position


class AdScriptRejected(ValueError):
    """Whole ad/short-film output violates constraints: no formal writes, asset registration, or repair drafts.

    ``problems`` details violations for task-failure display. Users may add instructions and regenerate.
    """

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("；".join(self.problems))


class AdScriptOverwriteRequired(ValueError):
    """Whole-script regeneration would replace existing formal content without caller acceptance; carries the loss inventory."""

    def __init__(self, overwrite: FormalScriptOverwrite) -> None:
        self.overwrite = overwrite
        super().__init__("整份重做会替换已有的正式脚本，需要用户确认覆盖")


@dataclass(frozen=True, slots=True)
class PromptAuthoringTargets:
    """Targets for one prompt-authoring call: formal-script snapshot and items needing visual layers, in script order."""

    kind: str
    id_field: str
    script: dict[str, Any]
    entries: tuple[dict[str, Any], ...]
    selection: PromptAuthoringSelection

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(str(entry[self.id_field]) for entry in self.entries)

    def fields_of(self, entry_id: str) -> tuple[str, ...]:
        """Visual-layer fields written back for this item in this operation."""
        return next(entry.fields for entry in self.selection.entries if entry.entry_id == entry_id)

    @property
    def items(self) -> list[Any]:
        """All formal-script items for this skeleton, supplying context around rendered prompts."""
        return cast(list[Any], self.script[self.kind])


@dataclass(frozen=True, slots=True)
class ScriptPlanMaterializationReceipt:
    """Receipt for content confirmation's complete mechanical conversion from script plan to formal script."""

    episode: int
    script_filename: str
    #: All fresh formal-script item ids, pending authoring, in planning order.
    entry_ids: tuple[str, ...]
    #: Prior formal ids absent from the new script, in prior script order.
    removed: tuple[str, ...]


class ScriptPlanNotFoundError(FileNotFoundError):
    """Episode script-plan file missing, including cases with only prestructured legacy split tables; all three route loaders raise this type.

    Remains a ``FileNotFoundError`` subclass, preserving missing-file handlers. Callers distinguishing
    missing plans from missing projects/other files catch this subtype.
    """


@dataclass(frozen=True)
class PlanningVideoFacts:
    """Video-request facts read during planning: read-side evaluation results per task-type bucket on this route.

    Storyboard routes use only the project generation-mode bucket. Reference-to-video evaluates both r2v
    with available images and i2v without them; each unit reads its bucket after availability-based selection.
    Preserve failure objects until checkpoints require success, then raise :class:`VideoRequestFactsError`
    with the same problem-code family as preflight/execution.
    """

    by_bucket: Mapping[VideoGenerationType, VideoRequestFacts | VideoRequestFactsFailure]

    def result(self, generation_type: VideoGenerationType) -> VideoRequestFacts | VideoRequestFactsFailure:
        return self.by_bucket[generation_type]

    def require(self, generation_type: VideoGenerationType) -> VideoRequestFacts:
        """Successful bucket facts; raise :class:`VideoRequestFactsError` if resolution fails."""
        return require_video_request_facts(self.result(generation_type))

    def planning_durations(self, generation_type: VideoGenerationType) -> list[int]:
        """Available planning duration tiers for this bucket, using planning tiers for fixed endpoints; raise on resolution failure."""
        return planning_durations(self.require(generation_type))


def _require_overwrite_acknowledged(
    targets: PromptAuthoringTargets, baseline: str | None, overwrite_revision: str | None
) -> None:
    """When explicit rewrites replace existing visual content, overwrite tokens must match the formal fingerprint captured with targets."""
    overwritten = targets.selection.overwritten
    if overwritten and (overwrite_revision is None or fingerprint_of_revision(overwrite_revision) != baseline):
        raise PromptOverwriteRequired(PromptOverwrite(fingerprint=baseline, entries=overwritten))


class ScriptGenerator:
    """Script generator

    Prompt authoring fills pending items in formal scripts. Content confirmation mechanically converts
    script plans to formal scripts. ad generates whole scripts when no formal script exists.
    """

    # Class-level default also gives instances bypassing __init__ None; resolution lazily falls back to production ConfigResolver.
    config_resolver: ConfigResolver | None = None

    def __init__(
        self,
        project_path: str | Path,
        generator: Optional["TextGenerator"] = None,
        *,
        config_resolver: ConfigResolver | None = None,
    ):
        """Initialize the generator.

        Args:
            project_path: Project-directory path, such as projects/test0205.
            generator: Optional TextGenerator instance. None supports only build_prompt() dry runs.
            config_resolver: Capability resolver; by default, resolution constructs ConfigResolver connected to production DB.
        """
        self.project_path = Path(project_path)
        self.generator = generator
        self.config_resolver = config_resolver
        self._script_plan_fingerprint: str | None = None
        #: Loader-read top-level planning ``new_assets``, used to rewrite references/register during materialization.
        self._script_plan_new_assets: object = None
        self._artifact_basis: ArtifactBasisDescriptor | None = None
        self._script_plan_input_claim: ArtifactInputClaim | None = None

        # Load project.json.
        self.project_json = self._load_project_json()
        self.content_mode = self.project_json.get("content_mode", "narration")

    @property
    def generation_mode(self) -> str | None:
        """Project generation mode, a top-level project.json field: fixed at creation, immutable afterward, independent of episode number."""
        return self.project_json.get("generation_mode")

    def _episode_entry(self, episode: int) -> dict:
        """Find project.json episodes entries by episode number, returning an empty dict when absent."""
        return episode_ledger_entry(self.project_json, episode)

    @staticmethod
    def _entry_outline(entry: dict) -> dict:
        """Normalize ledger-entry outline fields to dicts; missing/malformed values become empty dicts."""
        return ledger_outline(entry)

    @classmethod
    async def create(
        cls,
        project_path: str | Path,
        *,
        config_resolver: ConfigResolver | None = None,
    ) -> "ScriptGenerator":
        """Async factory loading provider configuration from DB to construct TextGenerator automatically."""
        project_name = Path(project_path).name
        generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name, purpose=CallPurpose.SCRIPT_GENERATION)
        return await asyncio.to_thread(
            cls,
            project_path,
            generator,
            config_resolver=config_resolver,
        )

    def _formal_script_baseline(self, episode: int, output_filename: str | None) -> tuple[str, str | None]:
        """Formal episode filename and baseline fingerprint, None when the file does not exist."""
        filename = output_filename or formal_script_filename(self.project_path, self.project_json, episode)
        return filename, content_fingerprint(self.project_path / "scripts" / filename)

    def _issue_landing_item_ids(self, script_data: dict[str, Any]) -> None:
        """Issue server-side ids before persisting generated items (``item_id_history.issue_landing_item_ids``); call in a thread."""
        pm = ProjectManager.for_project_dir(self.project_path)
        issue_landing_item_ids(self.project_path, pm.load_project(self.project_path.name), script_data)

    def _save_script(self, script_data: dict[str, Any], filename: str, **kwargs: Any) -> Path:
        """Save through the unified script-write entry; ProjectManager construction creates directories, so call in a thread."""
        pm = ProjectManager.for_project_dir(self.project_path)
        return pm.save_script(self.project_path.name, script_data, filename, **kwargs)

    def _load_prompt_authoring_targets(
        self,
        episode: int,
        filename: str,
        entry_ids: Iterable[str] | None,
        *,
        rewrite: bool = False,
    ) -> PromptAuthoringTargets | None:
        """Read formal scripts and select authoring items; None without a formal script.

        Empty ``entry_ids`` scopes all pending-authoring items; nonempty values scope the named items.
        Any id absent from formal scripts raises ``PromptAuthoringTargetError``. Within scope select missing
        fields or explicit rewrites (see ``select_prompt_authoring``). Never read script plans.
        """
        resolver = active_artifact_currency_resolver(self.project_path, self.project_json)
        if (
            resolver.resolve_usable_entry(ArtifactKey.episode_script(episode), artifact_path=f"scripts/{filename}")
            is None
        ):
            return None
        pm = ProjectManager.for_project_dir(self.project_path)
        try:
            script = pm.load_script_readonly(self.project_path.name, filename)
        except FileNotFoundError:
            return None
        kind = resolve_declared_kind(self.content_mode, self.generation_mode)
        raw_items, id_field, _kind = resolve_kind_items(script, kind=kind)
        if not isinstance(raw_items, list):
            raise ValueError(f"集（id={episode}）正式脚本的 {kind} 不是条目数组，无法编写提示词")
        items = [item for item in cast(list[Any], raw_items) if isinstance(item, dict) and id_field in item]
        requested = tuple(dict.fromkeys(entry_ids or ()))
        known = {str(item[id_field]) for item in items}
        unknown = [entry_id for entry_id in requested if entry_id not in known]
        if unknown:
            raise PromptAuthoringTargetError(f"entry_ids 不在集（id={episode}）正式脚本内: {unknown}")
        selection = select_prompt_authoring(items, kind=kind, id_field=id_field, entry_ids=requested, rewrite=rewrite)
        selected = {entry.entry_id for entry in selection.entries}
        entries = tuple(item for item in items if str(item[id_field]) in selected)
        return PromptAuthoringTargets(kind=kind, id_field=id_field, script=script, entries=entries, selection=selection)

    async def generate(
        self,
        episode: int,
        output_filename: str | None = None,
        *,
        instructions: str | None = None,
        entry_ids: Iterable[str] | None = None,
        rewrite: bool = False,
        overwrite_revision: str | None = None,
        rewritten_entry_ids: list[str] | None = None,
        skipped_entry_ids: list[str] | None = None,
        before_quarantine_commit: Callable[[], None] | None = None,
        regenerate: bool = False,
        registered_assets: list[dict[str, str]] | None = None,
    ) -> Path:
        """Author visual layers in formal scripts; generate whole ad scripts when absent or explicitly regenerated.

        Input is formal-script content, never script plans. Targeted items replace visual layers only
        (reference-to-video units rewrite bodies), clearing pending-authoring flags while preserving content
        and user fields. Other items remain byte-for-byte unchanged. Without authoring targets, invoke no
        text model and write nothing.

        Args:
            episode: Episode number.
            output_filename: Output filename, default episode_{episode}.json. Unified writes always go to
                project scripts/, so this accepts filenames only, never directories.
            instructions: Verbatim additional user instructions. If nonempty, append a neutral additional-instructions
                section to the prompt; body wording determines adherence strength, consistently across all modes.
            entry_ids: Authoring scope, all pending items when empty. Unknown ids fail without writes.
                Naming items only scopes selection: completion still skips items with complete visual layers.
            rewrite: Explicitly rewrite all scoped visual layers; False fills missing visual fields only.
            overwrite_revision: Accepted formal-script fingerprint from the loss inventory's ``revision``.
                If explicit rewrites replace existing visuals, missing/mismatched values raise
                ``PromptOverwriteRequired`` before model calls.
            rewritten_entry_ids: Optional collector, filled in place with authored ids in script order when
                non-None for caller receipts. Remains empty during whole-ad generation.
            skipped_entry_ids: Optional collector, filled with scoped ids skipped because their visual layers are complete.
            regenerate: ad only: replace the entire existing formal script. Existing scripts require
                ``overwrite_revision`` matching the loss inventory's ``revision``; otherwise raise
                ``AdScriptOverwriteRequired`` before model calls.
            registered_assets: Optional collector, filled in place with newly registered assets/derivatives
                during whole-ad generation (``{"type", "name"}``, derivatives named ``本体/衍生``).

        Returns:
            Formal script JSON path.
        """
        if self.generator is None:
            raise RuntimeError("TextGenerator 未初始化，请使用 ScriptGenerator.create() 工厂方法")

        # Enforce filename-only, no-directories docstring contract. Write-entry _safe_subpath rejects absolute paths
        # and traversal but not subdirectories: `subdir/x.json` remains inside scripts/ after realpath,
        # writing scripts/subdir/x.json instead of the flat layout. Fail fast at the public API entry,
        # enforcing the contract before completing the entire generation workflow.
        # Explicitly reject `\\`: POSIX Path treats it differently from Windows; defend across platforms.
        # Explicitly reject empty "": Path("").name == "" equals output_filename and passes both earlier checks;
        # letting empty filenames reach save_script crashes only during writes. Fail early to keep failure timing consistent.
        if output_filename is not None and (
            not output_filename or Path(output_filename).name != output_filename or "\\" in output_filename
        ):
            raise ValueError(f"output_filename 只接受纯文件名，不允许目录或路径分隔符: {output_filename!r}")

        self._script_plan_fingerprint = None
        self._artifact_basis = None
        self._script_plan_input_claim = None
        gen_mode = self.generation_mode
        # Capture baseline before reading formal scripts: snapshots and expected_fingerprint precede the same instant;
        # concurrent saves between them cause write conflicts rather than being overwritten by this writeback.
        filename, formal_baseline = await asyncio.to_thread(self._formal_script_baseline, episode, output_filename)
        if regenerate:
            if self.content_mode != "ad":
                raise PromptAuthoringTargetError("整份重做只适用于广告/短片；其他创作类型请重跑脚本规划并重新确认")
            if entry_ids or rewrite:
                raise PromptAuthoringTargetError("整份重做不能与 entry_ids / rewrite 同时使用")
            overwrite = await asyncio.to_thread(formal_script_overwrite, self.project_path, self.project_json, episode)
            if overwrite is not None and fingerprint_of_revision(overwrite_revision) != overwrite.fingerprint:
                raise AdScriptOverwriteRequired(overwrite)
            prompt, schema = await self._compose_ad(episode, gen_mode, instructions)
            return await self._generate_and_save(
                prompt,
                schema,
                episode,
                output_filename,
                previous=overwrite,
                registered_assets=registered_assets,
            )
        targets = await asyncio.to_thread(
            functools.partial(self._load_prompt_authoring_targets, episode, filename, entry_ids, rewrite=rewrite)
        )
        if targets is None:
            if self.content_mode != "ad":
                raise PromptAuthoringTargetError(
                    f"集（id={episode}）尚无正式脚本：请先完成脚本规划并在 Web 端完成内容确认，确认即生成正式脚本"
                )
            if entry_ids:
                raise PromptAuthoringTargetError(f"集（id={episode}）尚无正式脚本，entry_ids 无从对应")
            # Both ad modes generate in one action without script_plan; reference-to-video directly emits self-contained video_units.
            prompt, schema = await self._compose_ad(episode, gen_mode, instructions)
            return await self._generate_and_save(
                prompt,
                schema,
                episode,
                output_filename,
                registered_assets=registered_assets,
            )

        _require_overwrite_acknowledged(targets, formal_baseline, overwrite_revision)
        if rewritten_entry_ids is not None:
            rewritten_entry_ids[:] = targets.ids
        if skipped_entry_ids is not None:
            skipped_entry_ids[:] = targets.selection.skipped
        output_path = self.project_path / "scripts" / filename
        if not targets.entries:
            logger.info("第 %d 集没有待编写条目，未调用文本模型", episode)
            return output_path

        if targets.kind == "video_units":
            if self.content_mode == "ad":
                return await self._author_ad_reference_units(
                    episode,
                    filename,
                    targets,
                    formal_baseline=formal_baseline,
                    instructions=instructions,
                )
            return await self._author_reference_units(
                episode,
                filename,
                targets,
                formal_baseline=formal_baseline,
                instructions=instructions,
                before_quarantine_commit=before_quarantine_commit,
            )

        if targets.kind == "scenes":
            for scene in targets.entries:
                require_script_unit_admitted("scenes", scene, ignore_marker=True)
        logger.info(
            "正在为第 %d 集编写提示词（%s，%d/%d 个条目）...",
            episode,
            targets.kind,
            len(targets.entries),
            len(targets.items),
        )
        result = await self._generate_text(
            TextGenerationRequest(
                prompt=await self._build_visual_prompt(episode, targets, instructions),
                response_schema=_VISUAL_RESPONSE_SCHEMA[targets.kind],
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            )
        )
        authored = self._merge_visual_layer(targets, self._parse_visual_layer(result.text, targets), episode)
        script_data = self._authored_script(episode, targets, authored)
        saved_path = await run_sync_transaction(
            self._save_script,
            script_data,
            filename,
            validate=True,
            expected_fingerprint=formal_baseline,
        )
        self._quality_probe(script_data, episode)
        logger.info("剧本已保存至 %s", saved_path)
        return saved_path

    async def _build_visual_prompt(
        self, episode: int, targets: PromptAuthoringTargets, instructions: str | None
    ) -> str:
        """Visual-layer prompts for storyboard-family segments / scenes / shots, using their formal-script content fields."""
        entries = list(targets.entries)
        if targets.kind == "scenes":
            return self._build_drama_prompt_authoring_prompt(entries, episode, instructions)
        if targets.kind == "shots":
            return self._build_ad_shot_prompt_authoring_prompt(episode, targets, instructions)
        return build_narration_prompt(
            project_overview=self.project_json.get("overview", {}),
            style=self.project_json.get("style", ""),
            style_description=self.project_json.get("style_description", ""),
            characters=self._project_bucket("characters"),
            scenes=self._project_bucket("scenes"),
            props=self._project_bucket("props"),
            script_plan_segments=entries,
            aspect_ratio=self._resolve_aspect_ratio(),
            episode=episode,
            # Output uses project source_language, matching formal-script content fields, as drama does.
            target_language=self.project_json.get("source_language") or "中文",
            instructions=instructions,
        )

    def _project_bucket(self, key: str) -> dict:
        bucket = self.project_json.get(key)
        return bucket if isinstance(bucket, dict) else {}

    def _parse_visual_layer(self, response_text: str, targets: PromptAuthoringTargets) -> list[dict]:
        """Strictly validate visual responses by skeleton kind; return per-item visual dicts (id + image_prompt + video_prompt)."""
        text = strip_json_code_fences(response_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"提示词编写视觉层 JSON 解析失败: {e}") from e
        try:
            validated = _VISUAL_RESPONSE_SCHEMA[targets.kind].model_validate(data)
        except ValidationError as e:
            raise ValueError(f"提示词编写视觉层结构校验失败: {e}") from e
        return cast(list[dict], validated.model_dump()[targets.kind])

    @staticmethod
    def _merge_visual_layer(targets: PromptAuthoringTargets, visual_items: list[dict], episode: int) -> list[dict]:
        """Merge visuals into formal-script items by id, writing only selected visual fields and preserving every other field.

        Visual responses must correspond one-to-one to authoring targets: missing, extra, or duplicate items
        fail loudly, preventing misalignment and omissions.
        """
        id_field = targets.id_field
        visual_by_id: dict[str, dict] = {}
        for item in visual_items:
            entry_id = str(item[id_field])
            if entry_id in visual_by_id:
                raise ValueError(f"集（id={episode}）视觉层 {id_field} 重复: {entry_id}")
            visual_by_id[entry_id] = item
        missing = [entry_id for entry_id in targets.ids if entry_id not in visual_by_id]
        if missing:
            raise ValueError(f"集（id={episode}）视觉层缺少本次编写的条目: {missing}")
        extra = sorted(set(visual_by_id) - set(targets.ids))
        if extra:
            raise ValueError(f"集（id={episode}）视觉层含本次编写范围之外的 {id_field}: {extra}")
        return [
            {
                **entry,
                **{
                    field: visual_by_id[str(entry[id_field])][field]
                    for field in targets.fields_of(str(entry[id_field]))
                },
            }
            for entry in targets.entries
        ]

    def _authored_script(
        self,
        episode: int,
        targets: PromptAuthoringTargets,
        authored: list[dict],
        *,
        reference_unit_durations: dict[str, int] | None = None,
        facts: PlanningVideoFacts | None = None,
    ) -> dict[str, Any]:
        """Replace authored items by id within the formal snapshot; return the entire script for persistence.

        Item metadata (clearing pending authoring, reassessing needs_replan, reference-unit tier validation)
        affects authored items only. Script fields retain snapshot values; metadata refreshes only
        ``updated_at`` and ``generator``.
        """
        finished = self._add_metadata(
            {targets.kind: authored},
            episode,
            reference_unit_durations=reference_unit_durations,
            facts=facts,
        )[targets.kind]
        # _add_metadata rewrites episode id prefixes; formal ids are writeback anchors and never change during authoring.
        replacements: dict[str, dict] = {}
        for entry_id, item in zip(targets.ids, finished, strict=True):
            item[targets.id_field] = entry_id
            replacements[entry_id] = item
        script = dict(targets.script)
        script[targets.kind] = [
            replacements.get(str(item.get(targets.id_field)), item) if isinstance(item, dict) else item
            for item in targets.items
        ]
        raw_metadata = script.get("metadata")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
        metadata["updated_at"] = datetime.now(UTC).isoformat()
        metadata["generator"] = self.generator.model if self.generator else "unknown"
        script["metadata"] = metadata
        return script

    async def _load_plan_entries_for_materialization(self, episode: int) -> tuple[ScriptPlanKind, list[dict]]:
        """Read plans by project route and assert materialization admission; return (variant, items).

        Each route loader validates structure, guards repair drafts, and freezes planning fingerprints /
        input registrations. Duration tiers and speech admission use current video capabilities.
        Reference-to-video bodies skip machine preflight: units persist pending authoring, which later rewrites
        and validates their bodies.
        """
        gen_mode = self.generation_mode
        if self.content_mode == "ad":
            raise ValueError("广告/短片项目没有脚本规划步骤，不适用内容确认转换")
        if gen_mode == "reference_video":
            facts = await self._fetch_video_request_facts()
            units = await run_sync_transaction(
                self._load_reference_script_plan, episode, self._reference_migration_durations(facts)
            )
            await asyncio.to_thread(functools.partial(self._assert_reference_script_plan_durations, units, facts=facts))
            return "reference_video", units
        if self.content_mode != "narration":
            content = await asyncio.to_thread(self._load_drama_script_plan_content, episode)
            raw_scenes = content.get("scenes")
            scenes: list = raw_scenes if isinstance(raw_scenes, list) else []
            for scene in scenes:
                require_script_unit_admitted("scenes", scene)
            await self._assert_drama_script_plan_durations(scenes)
            return "drama", scenes
        supported = self._storyboard_planning_durations(await self._fetch_video_request_facts())
        return "narration", await asyncio.to_thread(self._load_narration_script_plan, episode, supported)

    async def materialize_script_plan(
        self,
        episode: int,
        *,
        expected_plan_revision: str,
        expected_script_fingerprint: str | None,
        project_update: Callable[[dict[str, Any]], None],
    ) -> ScriptPlanMaterializationReceipt:
        """Convert entire script plans to formal scripts on confirmation, replacing all prior items without reuse.

        Issue fresh ids at persistence, allocating without reuse (``docs/adr/0090``).

        See ``lib.script.script_document.build_materialized_script`` for projection. ``expected_plan_revision``
        is the confirmed planning fingerprint; mismatched loaded plans raise ``ScriptPlanWriteConflict``.
        ``expected_script_fingerprint`` captures accepted overwrite content, None without scripts;
        mismatched writes raise ``ScriptWriteConflict``. Unregister old items' artifacts with this write,
        moving version histories/current media to project trash. ``project_update`` modifies project.json
        in the same write transaction, persisting confirmation and formal scripts together.
        """
        self._script_plan_fingerprint = None
        self._script_plan_new_assets = None
        self._artifact_basis = None
        self._script_plan_input_claim = None
        plan_kind, plan_entries = await self._load_plan_entries_for_materialization(episode)
        # Loaders froze the read planning fingerprint inside await; static narrowing cannot see that assignment.
        loaded_revision = cast(str | None, self._script_plan_fingerprint)
        if loaded_revision != expected_plan_revision:
            raise ScriptPlanWriteConflict(expected=expected_plan_revision, actual=loaded_revision, current_content=None)
        # Rewrite episode-new references by handling decisions and register alongside formal scripts/confirmations in one write.
        new_assets = resolve_new_assets(self.project_json, self._script_plan_new_assets)
        plan_entries = new_assets.rewrite_entries(plan_entries)
        registered_project = copy.deepcopy(self.project_json)
        new_assets.apply_to_project(registered_project)
        unregistered = unregistered_references(registered_project, plan_entries, id_field=entry_id_field(plan_kind))
        if unregistered:
            raise UnregisteredReferencesError(unregistered)

        def update_project(project: dict[str, Any]) -> None:
            new_assets.apply_to_project(project)
            project_update(project)

        filename = await asyncio.to_thread(formal_script_filename, self.project_path, self.project_json, episode)
        # Episode titles come from ledgers: fill but ignore drama model titles to avoid freezing derived names or replacing user titles.
        script_data = build_materialized_script(
            self.project_json, episode, plan_kind=plan_kind, plan_entries=plan_entries, title=None
        )
        items_key = plan_variant(plan_kind).skeleton_kind
        id_field = entry_id_field(plan_kind)
        previous = await asyncio.to_thread(formal_script_overwrite, self.project_path, self.project_json, episode)
        previous_ids = tuple(entry.entry_id for entry in previous.entries) if previous is not None else ()
        plan_path = script_plan_path(self.project_path, self.project_json, episode)
        if plan_path is None:
            raise FileNotFoundError(f"集（id={episode}）不适用脚本规划")
        claim = self._script_plan_input_claim

        def _commit() -> None:
            # Recheck planning fingerprints under lock before writes: saves, reruns, or promotion after loading must not
            # materialize stale plans or record already replaced fingerprints as confirmed.
            with formal_script_plan_lock(self.project_path, episode, plan_path):
                current_revision = content_fingerprint(plan_path)
                if current_revision != expected_plan_revision:
                    raise ScriptPlanWriteConflict(
                        expected=expected_plan_revision, actual=current_revision, current_content=None
                    )
                if claim is not None:
                    assert_current_artifact_input_claims_usable(self.project_path, (claim,))
                # Plans provide content only: issue fresh formal identities and trash all prior items.
                self._issue_landing_item_ids(script_data)
                self._save_script(
                    script_data,
                    filename,
                    validate=True,
                    expected_fingerprint=expected_script_fingerprint,
                    project_update=update_project,
                )

        await run_sync_transaction(_commit)
        entry_ids = tuple(str(item[id_field]) for item in script_data[items_key])
        removed = tuple(entry_id for entry_id in previous_ids if entry_id not in entry_ids)
        logger.info(
            "第 %d 集已在内容确认时按脚本规划整份转为正式剧本（%d 条，移出旧条目 %d 条）",
            episode,
            len(entry_ids),
            len(removed),
        )
        return ScriptPlanMaterializationReceipt(
            episode=episode, script_filename=filename, entry_ids=entry_ids, removed=removed
        )

    async def _assert_drama_script_plan_durations(self, content_scenes: list) -> None:
        """Validate finalized drama storyboard durations against current capabilities; fail loudly outside them.

        Match narration (``_load_narration_script_plan``) and reference_video (``_load_reference_script_plan``):
        drama durations are finalized in script_plan; prompt_authoring generates visuals only and passes them
        unchanged, while prepersistence static validation requires only positive integers. Without this check,
        planning at one resolution followed by stricter resolution authoring stores out-of-tier durations
        until video enqueueing rejects them.

        Use **final-schema normalization** (``TypeAdapter(int)``, matching ``DramaSceneContent``'s nonstrict
        ``int`` field), not ``isinstance(..., int)``. The latter skips ``"4"`` and ``4.0``, although persistence
        normalizes them to 4, creating a bypass. Missing keys and explicit ``null`` both use declared defaults:
        omission still requires validation because persistence fills exactly those defaults. Failed normalization
        (such as ``"abc"``) is left to unified prepersistence static validation to fail loudly.
        """
        supported = self._storyboard_planning_durations(await self._fetch_video_request_facts())
        allowed = {int(d) for d in supported}
        seen: set[int] = set()
        for scene in content_scenes:
            if not isinstance(scene, dict):
                continue
            raw = scene.get("duration_seconds")
            try:
                seen.add(_DURATION_ADAPTER.validate_python(_DRAMA_DEFAULT_DURATION if raw is None else raw))
            except ValidationError:
                continue
        bad = sorted(seen - allowed)
        if bad:
            raise ValueError(
                f"script_plan 已定分镜时长非法（不在 {sorted(allowed)} 内）: {bad}；"
                "当前分辨率与型号下这些时长不可用，请调用 generate_script_plan 按当前能力规范化"
            )

    def _build_drama_prompt_authoring_prompt(
        self, content_scenes: list, episode: int, instructions: str | None = None
    ) -> str:
        """Build drama visual prompt_authoring prompts from script_plan content, requesting only image_prompt / video_prompt."""
        characters = self.project_json.get("characters")
        characters = characters if isinstance(characters, dict) else {}
        scenes = self.project_json.get("scenes")
        scenes = scenes if isinstance(scenes, dict) else {}
        props = self.project_json.get("props")
        props = props if isinstance(props, dict) else {}
        return build_drama_prompt(
            project_overview=self.project_json.get("overview", {}),
            style=self.project_json.get("style", ""),
            style_description=self.project_json.get("style_description", ""),
            scenes_content=render_drama_content_for_prompt_authoring(content_scenes),
            episode=episode,
            aspect_ratio=self._resolve_aspect_ratio(),
            # Output uses project source_language like normalized script_plan, avoiding content/visual language splits in non-Chinese projects.
            target_language=self.project_json.get("source_language") or "中文",
            characters=characters,
            scenes=scenes,
            props=props,
            instructions=instructions,
        )

    async def _generate_and_save(
        self,
        prompt: str,
        schema: type,
        episode: int,
        output_filename: str | None,
        *,
        previous: FormalScriptOverwrite | None = None,
        registered_assets: list[dict[str, str]] | None = None,
    ) -> Path:
        """Whole-ad generation tail: TextBackend → parse/validate → realize new assets → metadata → unified script write.

        Invalid outputs (parse failures, malformed structure, unresolved references/new-asset decisions, empty
        items) raise ``AdScriptRejected`` without formal writes, registrations, or drafts. ``needs_replan``
        units still persist normally. ``previous`` is accepted prior formal content: use its fingerprint as
        write baseline and unregister its old artifacts with the write. Server-issued ids allocate without reuse.
        """
        assert self.generator is not None  # Already checked at generate() entry.
        if previous is None:
            filename, formal_baseline = await asyncio.to_thread(self._formal_script_baseline, episode, output_filename)
        else:
            filename = output_filename or await asyncio.to_thread(
                formal_script_filename, self.project_path, self.project_json, episode
            )
            formal_baseline = previous.fingerprint
        logger.info("正在生成第 %d 集剧本...", episode)
        result = await self._generate_text(
            TextGenerationRequest(
                prompt=prompt,
                response_schema=schema,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            )
        )
        response_text = result.text
        if self.generation_mode == "reference_video":
            script_data, new_assets = self._parse_ad_reference_response(response_text, episode)
        else:
            script_data, new_assets = self._parse_ad_storyboard_response(response_text, episode)
        # Empty formal scripts are valid for manual authors, but whole AI generation must produce items; require nonempty only here.
        items, id_field, _kind = resolve_kind_items(script_data)
        if not isinstance(items, list) or not items:
            raise AdScriptRejected([f"第 {episode} 集的 AI 生成脚本没有任何条目"])
        registered_project = self._settle_ad_new_assets(script_data, new_assets, id_field)
        # Speech-ownership problems set needs_replan without blocking persistence, evaluated after reference rewrites.
        script_data = self._add_metadata(script_data, episode)
        structure = validate_script_structure(script_data)
        if not structure.valid:
            raise AdScriptRejected(structure.errors)
        # Bases include asset tables: freeze projects after new registration so persisted formal scripts are current.
        self._freeze_ad_artifact_basis(episode, registered_project)

        # Save through the unified entry: whole generation has no prior content, so validate strictly, equivalent to former
        # response_schema Pydantic checks, while retaining metadata recomputation, locking, filename↔episode consistency,
        # and project.json synchronization. Eliminate bare json.dump bypasses; _write_script_unlocked is the sole script writer.
        # Register new assets and formal scripts in the same write transaction.
        # Model ids are placeholders: servers issue previously unused episode ids for all generated items and trash entire prior items.
        def _issue_and_save() -> Path:
            self._issue_landing_item_ids(script_data)
            return self._save_script(
                script_data,
                filename,
                validate=True,
                artifact_basis=self._artifact_basis,
                expected_fingerprint=formal_baseline,
                project_update=new_assets.apply_to_project,
            )

        output_path = await run_sync_transaction(_issue_and_save)
        if registered_assets is not None:
            registered_assets[:] = new_assets.registered()
        self._quality_probe(script_data, episode)
        logger.info("剧本已保存至 %s", output_path)
        return output_path

    def _settle_ad_new_assets(
        self, script_data: dict[str, Any], new_assets: NewAssetResolution, id_field: str
    ) -> dict[str, Any]:
        """Rewrite script references by handling decisions; return a project view with new assets registered.

        Rewritten references must resolve to existing/new assets or derivatives. Products cannot be newly
        registered here: ``products_in_shot`` recognizes existing products only. Unresolved references raise
        ``AdScriptRejected``.
        """
        items, _id_field, kind = resolve_kind_items(script_data)
        if not all(isinstance(item, dict) for item in items):
            raise AdScriptRejected([f"{kind} 里有不是对象的条目"])
        entries = new_assets.rewrite_entries(items)
        script_data[kind] = entries
        registered_project = copy.deepcopy(self.project_json)
        new_assets.apply_to_project(registered_project)
        problems = [
            f"{reference.item_id} 引用了未登记、也不在本次新增资产里的名字：{'、'.join(reference.names)}"
            for reference in unregistered_references(registered_project, entries, id_field=id_field)
        ]
        products = registered_project.get("products")
        known_products = (
            {asset_name_comparison_key(str(name)) for name in products} if isinstance(products, Mapping) else set()
        )
        for entry in entries:
            named = entry.get("products_in_shot")
            unknown = [
                name
                for name in (named if isinstance(named, list) else ())
                if isinstance(name, str) and asset_name_comparison_key(name) not in known_products
            ]
            if unknown:
                problems.append(f"{entry.get(id_field)} 引用了未登记的商品：{'、'.join(unknown)}")
        if problems:
            raise AdScriptRejected(problems)
        return registered_project

    async def _compose_ad(self, episode: int, gen_mode: str | None, instructions: str | None) -> tuple[str, type]:
        """Build ad (prompt, response_schema), shared by generate/build_prompt.

        Reference routes skip provider capability queries because unit orchestration durations are not
        quantized by provider tiers. Storyboard routes resolve supported_durations once, sharing the source
        between prompt duration lists and schema enums.
        """
        if gen_mode == "reference_video":
            supported = None
            schema: type = AdReferenceFlatScript
        else:
            supported = self._storyboard_planning_durations(await self._fetch_video_request_facts())
            schema = with_new_assets_field(build_episode_script_model("ad", supported))
        return self._build_ad_prompt(episode, gen_mode, supported, instructions), schema

    def _build_ad_prompt(
        self, episode: int, gen_mode: str | None, supported: list[int] | None, instructions: str | None
    ) -> str:
        """Build ad/short-film prompts from briefs, product information, and approved allocation tables, without script_plan files.

        Storyboard routes include supported_durations as per-shot duration enums. Reference-to-video outputs
        uniform-reference-syntax video units directly; the eight-part framework guides content planning only,
        without persistence.
        """
        direct_inputs = project_ad_episode_script_inputs(episode, project=self.project_json)
        # Asset descriptions/aliases only help models identify characters/list new assets, not artifact bases; description edits never expire scripts.
        common = {
            **self._ad_prompt_common(episode, instructions),
            "target_duration": direct_inputs["target_duration"],
            "asset_registry": {
                key: bucket if isinstance(bucket := self.project_json.get(key), dict) else {}
                for key in ("characters", "scenes", "props")
            },
        }
        if gen_mode == "reference_video":
            return build_ad_reference_prompt(**common)
        return build_ad_prompt(
            **common,
            generation_mode=gen_mode,
            supported_durations=supported,
            speech_rate_override=cast(float | None, direct_inputs["speech_rate_override"]),
        )

    def prompt_overwrite(
        self,
        episode: int,
        *,
        entry_ids: Iterable[str] | None = None,
        rewrite: bool = False,
        overwrite_revision: str | None = None,
    ) -> None:
        """Preflight overwrites before submission, sharing ``generate`` selection and raising ``PromptOverwriteRequired`` for unaccepted replacement.

        Named ids absent from formal scripts also raise ``PromptAuthoringTargetError``. Skip checks without formal scripts.
        """
        filename = formal_script_filename(self.project_path, self.project_json, episode)
        baseline = content_fingerprint(self.project_path / "scripts" / filename)
        targets = self._load_prompt_authoring_targets(episode, filename, entry_ids, rewrite=rewrite)
        if targets is not None:
            _require_overwrite_acknowledged(targets, baseline, overwrite_revision)

    async def build_prompt(
        self,
        episode: int,
        *,
        instructions: str | None = None,
        entry_ids: Iterable[str] | None = None,
        rewrite: bool = False,
    ) -> str:
        """Build prompts for dry runs.

        Share `generate()` selection: whole-generation ad prompts without formal scripts, otherwise only
        targeted items (``entry_ids`` or all pending items). Without targets, state that fact instead of
        rendering empty prompts that would never be sent. Inject ``instructions`` exactly as `generate()` does.
        Dry runs always use episode-bound formal scripts; without persistence there is no ``output_filename``.
        """

        def load_targets() -> PromptAuthoringTargets | None:
            filename = formal_script_filename(self.project_path, self.project_json, episode)
            return self._load_prompt_authoring_targets(episode, filename, entry_ids, rewrite=rewrite)

        targets = await asyncio.to_thread(load_targets)
        if targets is None:
            if self.content_mode != "ad":
                raise PromptAuthoringTargetError(
                    f"集（id={episode}）尚无正式脚本：请先完成脚本规划并在 Web 端完成内容确认，确认即生成正式脚本"
                )
            if entry_ids:
                raise PromptAuthoringTargetError(f"集（id={episode}）尚无正式脚本，entry_ids 无从对应")
            prompt, _schema = await self._compose_ad(episode, self.generation_mode, instructions)
            return prompt
        if not targets.entries:
            return _NO_ENTRY_TO_AUTHOR_NOTE
        if targets.kind != "video_units":
            return await self._build_visual_prompt(episode, targets, instructions)
        if self.content_mode == "ad":
            return self._build_ad_reference_prompt_authoring_prompt(episode, targets, instructions)
        facts = await self._fetch_video_request_facts()
        return self._build_reference_prompt_authoring_prompt(episode, targets, facts, instructions)

    @property
    def _storyboard_bucket(self) -> VideoGenerationType:
        """Task-type bucket for storyboard narration / drama / ad routes, selected by project generation mode."""
        return video_bucket_for_generation_mode(self.generation_mode)

    async def _fetch_video_request_facts(self) -> PlanningVideoFacts:
        """Evaluate read-side video facts by route buckets, sharing narrowed results with preflight/execution.

        Resolve identity from current configuration through bucket capability gates. Preserve unresolved
        buckets' failure objects without raising or falling back to project-declared identities/registry:
        durations/reference counts from alternate sources would still fail the same execution gate. Problem
        codes and repair instructions are more useful than first writing inevitably failing scripts.
        Reference-to-video evaluates r2v and i2v; units read their buckets after image-availability selection.
        """
        resolver = self.config_resolver or ConfigResolver(async_session_factory)
        route: VideoRoute
        buckets: tuple[VideoGenerationType, ...]
        if self.generation_mode == "reference_video":
            route, buckets = "reference_video", ("r2v", "i2v")
        else:
            route, buckets = "storyboard", (self._storyboard_bucket,)
        by_bucket: dict[VideoGenerationType, VideoRequestFacts | VideoRequestFactsFailure] = {}
        for bucket in buckets:
            by_bucket[bucket] = await evaluate_video_request_facts(
                self.project_json,
                route=route,
                generation_type=bucket,
                identity=CONFIGURED_VIDEO_IDENTITY,
                resolver=resolver,
            )
        return PlanningVideoFacts(by_bucket=by_bucket)

    def _storyboard_planning_durations(self, facts: PlanningVideoFacts) -> list[int]:
        """Storyboard planning tiers, narrowed by resolution before passing to prompts/dynamic schemas.

        ``supported_durations`` contains the model's full durations without resolution-duration coupling.
        Without narrowing, 1080p Veo plans produce 4/6-second shots rejected only at video enqueueing,
        after users lose a unified correction entry. Unresolvable facts raise :class:`VideoRequestFactsError`.
        """
        return facts.planning_durations(self._storyboard_bucket)

    def _unit_duration_off_every_tier(
        self, duration: int, *, facts: PlanningVideoFacts, generation_type: VideoGenerationType
    ) -> list[int] | None:
        """Return this unit's bucket tiers only when duration fails both image/no-image buckets; None if either allows it.

        prompt_authoring can add/remove `@[名称]` mentions, so durations failing one bucket may still land
        validly. Early refusal would block successful generation. Failure in both is independent of images,
        caused by model/resolution configuration changes, and can be stopped before paid calls.
        Unresolvable buckets count as failures.
        """
        for candidate in ("r2v", "i2v"):
            if self._unit_duration_off_tier(duration, facts=facts, generation_type=candidate) is None:
                return None
        return facts.planning_durations(generation_type)

    def _unit_duration_off_tier(
        self, duration: int, *, facts: PlanningVideoFacts, generation_type: VideoGenerationType
    ) -> list[int] | None:
        """Return effective bucket tiers for out-of-tier durations, otherwise None.

        Calculate per unit: available reference images select r2v, absent images i2v. Unresolvable bucket
        facts yield empty tiers.
        """
        result = facts.result(generation_type)
        if not isinstance(result, VideoRequestFacts):
            return []
        tiers = planning_durations(result)
        return None if resolve_duration_slot(duration, tiers).seconds == duration else tiers

    def _hydrate_reference_units(self, units: Sequence[dict]) -> tuple[ReferenceUnitHydration, ...]:
        """Hydrate declared unit references with execution's file-existence/manifest-ownership checks to determine current buckets.

        Share confirmation-panel and batch-admission predicates: body ``@`` references with missing or
        unowned images select i2v.
        """
        return hydrate_reference_units(self.project_json, self.project_path, units)

    @staticmethod
    def _reference_migration_durations(facts: PlanningVideoFacts) -> list[int] | None:
        """Structural-adoption duration set: union of declared r2v/i2v sets; None if either fails, allowing structural adoption only."""
        return reference_migration_durations(facts.result("r2v"), facts.result("i2v"))

    def _resolve_aspect_ratio(self) -> str:
        """Resolve project aspect_ratio compatibly; narration / ad default to portrait, matching ad creation-wizard defaults."""
        if "aspect_ratio" in self.project_json and isinstance(self.project_json["aspect_ratio"], str):
            return self.project_json["aspect_ratio"]
        return "9:16" if self.content_mode in ("narration", "ad") else "16:9"

    @staticmethod
    def _resolve_max_refs(facts: PlanningVideoFacts) -> int | None:
        """Per-request reference-image limit for units with usable images (r2v), from that bucket's video facts.

        None means unlisted: neither entry nor channel declares limits, so prompts omit hard count constraints
        and execution skips clipping. Zero means unsupported images, models excluded from r2v; likewise skip
        clipping and let execution reject. Unresolvable r2v facts count as undeclared: units with images already
        block at checkpoints for missing facts, without introducing another source here.
        """
        result = facts.result("r2v")
        return result.max_reference_images if isinstance(result, VideoRequestFacts) else None

    def _load_project_json(self) -> dict:
        """Load project.json."""
        path = self.project_path / "project.json"
        if not path.exists():
            raise FileNotFoundError(f"未找到 project.json: {path}")

        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _freeze_script_plan_input_claim(self, episode: int, script_plan_path: Path, *, content_digest: str) -> None:
        """Bind parsed script_plan bytes to their formal identity for provider admission."""

        artifact_path = script_plan_path.relative_to(self.project_path).as_posix()
        claim = resolve_usable_artifact_input_claim(
            resolver=active_artifact_currency_resolver(self.project_path, self.project_json),
            key=ArtifactKey.episode_script_plan(episode),
            artifact_path=artifact_path,
            content_digest=content_digest,
        )
        if claim is None:
            raise ValueError(f"formal script_plan artifact is not registered: {artifact_path}")
        self._script_plan_input_claim = claim

    async def _generate_text(self, request: TextGenerationRequest) -> Any:
        """Call the paid text provider after one shared formal-input recheck."""

        assert self.generator is not None  # Already checked at generate() entry.
        if self._script_plan_input_claim is not None:
            await asyncio.to_thread(
                assert_current_artifact_input_claims_usable,
                self.project_path,
                (self._script_plan_input_claim,),
            )
        return await self.generator.generate(request, project_name=self.project_path.name)

    def _freeze_ad_artifact_basis(self, episode: int, project: Mapping[str, Any]) -> None:
        """Freeze the ad-specific canonical basis from the project the generated script is written against."""
        basis = build_ad_episode_script_basis(episode, project=project)
        self._artifact_basis = ArtifactBasisDescriptor.from_basis(basis)

    def _load_script_plan(self, episode: int) -> str:
        """Load raw structured intermediate script-plan text for drama-shaped two-stage routes.

        Each mode has exactly one expected file. Missing files fail explicitly with expected paths, never
        silently falling back to another mode's intermediates and generating from the wrong mode's content.
        Serve drama-shaped structured content only; narration uses ``_load_narration_script_plan`` and
        reference_video ``_load_reference_script_plan``.
        """
        drafts_path = episode_drafts_dir(self.project_path, episode)
        # Select registered structured filenames by content_mode, falling back to drama for dirty values.
        script_plan_path = drafts_path / SCRIPT_PLAN_FILENAMES.get(self.content_mode, SCRIPT_PLAN_FILENAMES["drama"])

        if not script_plan_path.exists():
            raise ScriptPlanNotFoundError(
                f"未找到脚本规划中间文件: {script_plan_path}；content_mode={self.content_mode} 期望该文件，请先完成本集脚本规划"
            )

        raw = script_plan_path.read_bytes()
        text = raw.decode("utf-8")
        self._freeze_script_plan_input_claim(episode, script_plan_path, content_digest=hashlib.sha256(raw).hexdigest())
        return text

    def _load_reference_script_plan(
        self,
        episode: int,
        supported_durations: list[int] | None,
    ) -> list[dict]:
        """Load/validate reference_video structured intermediate ``script_plan_reference_units.json``.

        Return unit dicts (unit_id / text / duration_seconds / source_text) for complete formal materialization
        on confirmation. Validate structure (``ReferenceScriptPlanDraft``), nonempty units, and unique unit_id.
        ``supported_durations`` serves legacy structural adoption only; subsequent per-unit facts check
        duration admission. Legacy-only ``script_plan_reference_units.md`` explicitly asks to rerun splitting,
        without md→json migration because it predates structured intermediates, matching narration's decision.
        """
        drafts_path = episode_drafts_dir(self.project_path, episode)
        script_plan_json = drafts_path / REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME
        # Never materialize while repair drafts exist: formal files still hold prior content or are absent, so using them
        # silently substitutes old content for unresolved output. Confirmation services already refuse; repeat the guard for direct calls.
        quarantine = quarantine_path(self.project_path, episode, QUARANTINE_KIND_SCRIPT_PLAN)
        if quarantine.exists():
            raise ValueError(
                f"集（id={episode}）有待修复草稿（{quarantine}），脚本规划转换已中止；"
                f"请先修改该草稿并经 {PROMOTE_TOOL_NAME} 晋升为正式 script_plan"
            )
        if not script_plan_json.exists():
            legacy_md = drafts_path / REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME
            if legacy_md.exists():
                raise ScriptPlanNotFoundError(
                    f"仅找到结构化前的旧拆分表 {legacy_md}，未找到 {script_plan_json}；"
                    f"请调用 generate_script_plan 产出结构化 {REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME}"
                )
            raise ScriptPlanNotFoundError(
                f"未找到脚本规划中间文件: {script_plan_json}；generation_mode=reference_video 期望该文件，"
                "请先完成 video_unit 拆分"
            )

        pm = ProjectManager.for_project_dir(self.project_path)
        # Share the per-path lock with server.services.project.script_review / save_content:
        # migration read-modify-write excludes Web saves and resplitting writes.
        prompt_authoring_path = quarantine_path(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        with pm.file_lock(prompt_authoring_path), pm.file_lock(script_plan_json):
            try:
                raw = json.loads(script_plan_json.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise ValueError(f"script_plan_reference_units.json 解析失败: {e}") from e

            # Adopt legacy per-shot durations into unit-level fields once and persist; subsequent loads do not repeat it.
            # Use structural tiers when i2v facts exist; never borrow r2v to change imageless-unit durations when i2v is unknown.
            # Duration adoption shifts planning fingerprints; confirmed-baseline materialization refuses unreviewed durations instead of persisting them.
            migrate_script_plan_draft_in_place(
                self.project_path,
                raw,
                episode=episode,
                update_project=lambda mutate: pm.update_project(self.project_path.name, mutate),
                supported_durations=supported_durations,
            )
            self._script_plan_fingerprint = content_fingerprint_of_data(raw)
            self._script_plan_new_assets = raw.get(NEW_ASSETS_FIELD) if isinstance(raw, dict) else None
            self._freeze_script_plan_input_claim(
                episode,
                script_plan_json,
                content_digest=sha256_file(script_plan_json),
            )

        try:
            draft = ReferenceScriptPlanDraft.model_validate(raw)
        except ValidationError as e:
            raise ValueError(f"script_plan_reference_units.json 结构校验失败: {e}") from e

        units = [u.model_dump() for u in draft.units]
        if not units:
            raise ValueError("script_plan_reference_units.json units 为空")

        ids = [u["unit_id"] for u in units]
        dupes = sorted(uid for uid, count in Counter(ids).items() if count > 1)
        if dupes:
            raise ValueError(f"script_plan_reference_units.json unit_id 重复: {dupes}")

        # _add_metadata rewrites E\d+ prefixes to the current episode; distinct original ids may collide afterward
        # (E1U01 and E2U01 both become E2U01 at episode=2). Fail loudly before silently persisting duplicate ids.
        # Match _load_narration_script_plan / _load_drama_script_plan_content.
        rewritten_ids = [str(rewrite_episode_prefix(uid, episode)) for uid in ids]
        rewritten_dupes = sorted(uid for uid, count in Counter(rewritten_ids).items() if count > 1)
        if rewritten_dupes:
            raise ValueError(
                f"script_plan_reference_units.json unit_id 改写到 episode={episode} 后重复: {rewritten_dupes}"
            )

        return units

    def _load_narration_script_plan(self, episode: int, supported_durations: list[int]) -> list[dict]:
        """Load/validate narration structured intermediate ``script_plan_segments.json``.

        Return storyboard dicts with verbatim ``novel_text``, durations, ``segment_break``, and other content
        fields for prompt rendering/visual merging; novel_text passes through without LLM rewriting.
        Validate structure, unique segment_id, and ``duration_seconds`` ∈ ``supported_durations``. Move duration
        constraints from former prompt_authoring schema enums to script_plan because authoring no longer
        emits them. Legacy-only ``script_plan_segments.md`` explicitly asks to rerun splitting; no md→json
        migration because it predates structured intermediates and contains no manual edits.
        """
        # Never author while repair drafts exist: formal files still hold prior content or are absent, and authoring them
        # silently substitutes old content for pending output. Confirmation already blocks tool entries by this rule;
        # guard direct scripts/tests/future entries here, matching the other two routes.
        quarantine = quarantine_path(self.project_path, episode, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)
        if quarantine.exists():
            raise ValueError(
                f"集（id={episode}） script_plan 有草稿待处置（{quarantine}），脚本规划转换已中止；"
                f"请先修改该草稿并经 {PROMOTE_TOOL_NAME} 晋升为正式 script_plan"
            )
        drafts_path = episode_drafts_dir(self.project_path, episode)
        narration_json = SCRIPT_PLAN_FILENAMES["narration"]
        script_plan_json = drafts_path / narration_json
        if not script_plan_json.exists():
            legacy_md = drafts_path / SCRIPT_PLAN_LEGACY_FILENAMES["narration"][0]
            if legacy_md.exists():
                raise ScriptPlanNotFoundError(
                    f"仅找到结构化前的旧拆分表 {legacy_md}，未找到 {script_plan_json}；"
                    f"请调用 generate_script_plan 产出结构化 {narration_json}"
                )
            raise ScriptPlanNotFoundError(
                f"未找到脚本规划中间文件: {script_plan_json}；content_mode=narration 期望该文件，请先完成分镜拆分"
            )

        raw_bytes = script_plan_json.read_bytes()
        try:
            raw = json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"script_plan_segments.json 解析失败: {e}") from e
        self._script_plan_fingerprint = content_fingerprint_of_data(raw)
        self._script_plan_new_assets = raw.get(NEW_ASSETS_FIELD) if isinstance(raw, dict) else None
        self._freeze_script_plan_input_claim(
            episode,
            script_plan_json,
            content_digest=hashlib.sha256(raw_bytes).hexdigest(),
        )

        try:
            draft = NarrationScriptPlanDraft.model_validate(raw)
        except ValidationError as e:
            raise ValueError(f"script_plan_segments.json 结构校验失败: {e}") from e

        segments = [s.model_dump() for s in draft.segments]
        if not segments:
            raise ValueError("script_plan_segments.json segments 为空")

        ids = [s["segment_id"] for s in segments]
        dupes = sorted(sid for sid, count in Counter(ids).items() if count > 1)
        if dupes:
            raise ValueError(f"script_plan_segments.json segment_id 重复: {dupes}")

        # _add_metadata rewrites E\d+ prefixes to the current episode; distinct original ids may collide afterward
        # (E1S02_1 and E2S02_1 become E2S02_1 at episode=2). Fail loudly before silently persisting duplicate ids.
        rewritten_ids = [str(rewrite_episode_prefix(sid, episode)) for sid in ids]
        rewritten_dupes = sorted(sid for sid, count in Counter(rewritten_ids).items() if count > 1)
        if rewritten_dupes:
            raise ValueError(f"script_plan_segments.json segment_id 改写到 episode={episode} 后重复: {rewritten_dupes}")

        allowed = {int(d) for d in supported_durations}
        bad = sorted({s["duration_seconds"] for s in segments if s["duration_seconds"] not in allowed})
        if bad:
            raise ValueError(f"script_plan_segments.json duration_seconds 非法（不在 {sorted(allowed)} 内）: {bad}")

        return segments

    def _load_drama_script_plan_content(self, episode: int) -> dict:
        """Load/parse drama structured script_plan (``script_plan_normalized_script.json``).

        Return ``{title, scenes: [...]}`` dicts. Missing files raise FileNotFoundError through _load_script_plan.
        Invalid JSON, nonobject roots, nonempty-list violations in scenes, nonobject scene items, invalid
        nonempty-string scene_id, or duplicate ids after rewriting episode numbers raise ValueError.
        save_script validates internal fields (utterances / source_text, etc.) after visual merging. This
        outer-shape guard must fail fast for scenes/scene_id: otherwise broken plans silently save empty
        scripts, collisions surface only in artifact filenames/keys, or rendering/merging raises internal
        exceptions rather than explicit script_plan validation errors.
        """
        # Never author while repair drafts exist: formal files still hold prior content or are absent; using them
        # silently substitutes old content for unresolved output. Confirmation blocks tool entries already, but scripts/tests
        # bypass them; repeat the guard here, matching reference-to-video.
        quarantine = quarantine_path(self.project_path, episode, QUARANTINE_KIND_DRAMA_SCRIPT_PLAN)
        if quarantine.exists():
            raise ValueError(
                f"集（id={episode}）有待修复草稿（{quarantine}），脚本规划转换已中止；"
                f"请先修改该草稿并经 {PROMOTE_TOOL_NAME} 晋升为正式 script_plan"
            )
        raw = self._load_script_plan(episode)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"脚本规划内容文件不是合法 JSON（drama script_plan 应为结构化内容）: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("脚本规划内容文件结构异常：顶层应为对象 {title, scenes}")
        self._script_plan_fingerprint = content_fingerprint_of_data(data)
        self._script_plan_new_assets = data.get(NEW_ASSETS_FIELD)
        scenes = data.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("脚本规划内容文件结构异常：scenes 必须是非空的分镜对象数组")
        scene_ids: list[str] = []
        for idx, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                raise ValueError(f"脚本规划内容文件结构异常：scenes[{idx}] 必须是分镜对象")
            scene_id = scene.get("scene_id")
            if not isinstance(scene_id, str) or not scene_id:
                raise ValueError(f"脚本规划内容文件结构异常：scenes[{idx}].scene_id 必须是非空字符串")
            scene_ids.append(scene_id)
        # _add_metadata rewrites E\d+ prefixes to current episodes; distinct original ids may collide afterward
        # (E1S02_1 and E2S02_1 become E2S02_1 at episode=2). Fail before silent duplicate-id persistence
        # or downstream artifact-filename/asset-key collisions, matching _load_narration_script_plan.
        rewritten_ids = [str(rewrite_episode_prefix(sid, episode)) for sid in scene_ids]
        rewritten_dupes = sorted(sid for sid, count in Counter(rewritten_ids).items() if count > 1)
        if rewritten_dupes:
            raise ValueError(f"脚本规划内容文件 scene_id 改写到 episode={episode} 后重复: {rewritten_dupes}")
        return data

    def _assert_reference_script_plan_durations(
        self, script_plan_units: list[dict], *, facts: PlanningVideoFacts
    ) -> None:
        """Before materialization, verify confirmed unit durations still fit current tiers; later authoring rewrites/validates bodies.

        Available images determine buckets now: absent references, missing images, or unowned images select
        i2v. Require that bucket's facts. Out-of-tier units raise ``UnitDurationOffTier``, named by planning
        position like the confirmation panel's video unit N, without internal unit IDs.
        """
        hydrations = self._hydrate_reference_units(script_plan_units)
        for position, (unit, hydration) in enumerate(zip(script_plan_units, hydrations, strict=True), start=1):
            bucket = hydration.hydrated_generation_type
            facts.require(bucket)
            duration = int(unit["duration_seconds"])
            off_tiers = self._unit_duration_off_every_tier(duration, facts=facts, generation_type=bucket)
            if off_tiers is not None:
                tiers = sorted(set(off_tiers))
                raise UnitDurationOffTier(
                    f"视频单元 {position} 已确认时长 {duration}s 不在当前生效档位 {tiers} 内；"
                    "通常是模型或分辨率配置变化让档位收窄导致，请调整配置回原档位，"
                    "或在脚本规划里把该单元时长改到档位内（也可重新拆分该集 script_plan）后重新完成内容确认",
                    unit_id=str(unit["unit_id"]),
                    duration_seconds=duration,
                    tiers=tiers,
                    position=position,
                )

    def _assert_reference_units_authorable(self, units: list[dict], *, facts: PlanningVideoFacts) -> None:
        """Complete paid-call preflight of targeted unit values: effective duration tiers and machine-valid bodies.

        Generation and promotion before repair-draft revalidation share this code: users may edit timeline
        units while drafts exist. Divergent rules would permit promotion but reject next authoring, or vice versa.
        """
        for unit, hydration in zip(units, self._hydrate_reference_units(units), strict=True):
            bucket = hydration.hydrated_generation_type
            facts.require(bucket)
            duration = int(unit["duration_seconds"])
            # Stop inevitably failing durations before paid calls; stopping in _add_metadata is after TextBackend charges.
            off_tiers = self._unit_duration_off_every_tier(duration, facts=facts, generation_type=bucket)
            if off_tiers is not None:
                tiers = sorted(set(off_tiers))
                raise UnitDurationOffTier(
                    f"unit {unit['unit_id']} 时长 {duration}s 不在当前生效档位 {tiers} 内；"
                    "通常是模型或分辨率配置变化让档位收窄导致，请调整配置回原档位，或在时间线上把该单元时长改到档位内",
                    unit_id=str(unit["unit_id"]),
                    duration_seconds=duration,
                    tiers=tiers,
                )
        self._assert_reference_unit_text_valid(units, max_refs=self._resolve_max_refs(facts))

    def _unit_rejection(
        self, targets: PromptAuthoringTargets, exc: DraftViolation
    ) -> PromptAuthoringUnitRejected | None:
        """Convert formal-unit preflight violations to task problem codes: speech overload includes estimated seconds; others identify only units.

        Return None when violations cannot locate formal-script units.
        """
        first = violation_items(exc)[0]
        found = find_item(targets.script, first.item_id) if first.item_id else None
        if found is None:
            return None
        position, unit = found
        params: dict[str, Any] = {"unit_id": first.item_id, "position": position}
        if first.code == "dialogue_overload":
            spoken = dialogue_spoken_seconds(
                str(unit.get("text") or ""),
                self.project_json.get("source_language"),
                project_speech_rate_override(self.project_json),
            )
            params.update(spoken_seconds=f"{spoken:.1f}", duration_seconds=int(unit["duration_seconds"]))
            return PromptAuthoringUnitRejected(str(exc), code="prompt_authoring_dialogue_overload", params=params)
        return PromptAuthoringUnitRejected(str(exc), code="prompt_authoring_unit_invalid", params=params)

    @staticmethod
    def _duration_rejection(
        targets: PromptAuthoringTargets, exc: UnitDurationOffTier
    ) -> PromptAuthoringUnitRejected | None:
        """Turn out-of-tier durations into input-repair requests by UI position, with current duration/available tiers; None if unlocatable."""
        found = find_item(targets.script, exc.unit_id)
        if found is None:
            return None
        params: dict[str, Any] = {
            "unit_id": exc.unit_id,
            "position": found[0],
            "duration_seconds": exc.duration_seconds,
            "tiers": " / ".join(str(tier) for tier in exc.tiers),
        }
        return PromptAuthoringUnitRejected(str(exc), code="prompt_authoring_duration_off_tier", params=params)

    def _formal_unit_promotion_violation(
        self, targets: PromptAuthoringTargets, exc: DraftViolation | UnitDurationOffTier
    ) -> DraftViolation:
        """Formal-unit preflight failure before promotion: still ``DraftViolation`` and ``draft_invalid``, identifying units by UI position.

        Reuse authoring's preflight codes/parameters (``_unit_rejection`` / ``_duration_rejection``) with draft
        promotion copy keys. Presentation localizes by ``code`` and ``params``. Agent messages likewise name
        video unit N without internal IDs. Preserve messages unchanged when formal units cannot be located.
        """
        rejection = (
            self._duration_rejection(targets, exc)
            if isinstance(exc, UnitDurationOffTier)
            else self._unit_rejection(targets, exc)
        )
        if rejection is None:
            return exc if isinstance(exc, DraftViolation) else DraftViolation(str(exc), code="duration_off_tier")
        params = {key: value for key, value in rejection.params.items() if key != "unit_id"}
        unit_id = str(rejection.params["unit_id"])
        code = _FORMAL_UNIT_PROMOTION_KEYS[rejection.code]
        message = translate(code, **params)
        if isinstance(exc, DraftViolation) and rejection.code == "prompt_authoring_unit_invalid":
            # Append body-violation details after replacing unit ids with UI positions so Agents know repairs; omit unlocatable internal-ID items.
            label = f"视频单元 {params['position']}"
            reasons = [
                f"{label}{str(item)[len(item.label) :]}"
                for item in violation_items(exc)
                if item.item_id == unit_id and item.label and str(item).startswith(item.label)
            ]
            message += "".join(f"\n   - {reason}" for reason in reasons if unit_id not in reason)
        return DraftViolation(message, code=code, params=params, item_id=unit_id)

    def _assert_reference_unit_text_valid(self, units: list[dict], *, max_refs: int | None) -> None:
        """Preflight formal unit bodies under strict machine-output rules, directing violation locations/repairs to the timeline.

        Share ``validate_unit_text`` with ``_merge_reference_visual`` so script_plan cannot pass where authoring
        necessarily fails. Check only, without taking derived results: execution derives images from bodies;
        only bodies persist.

        Recheck dialogue load here too. Splitting checked only original output; shortening units or adding
        dialogue during confirmation bypasses that check. Authoring preserves dialogue verbatim, with no later
        load check, so omitting revalidation would persist units whose dialogue cannot fit.
        """
        source_language = self.project_json.get("source_language")
        speech_rate_override = project_speech_rate_override(self.project_json)
        hint = "这段正文来自正式脚本，提示词编写会逐字保留其中的台词，请先在时间线上修正该单元的正文或时长"
        for unit in units:
            label = f"正式脚本 的 unit {unit['unit_id']}"
            text = str(unit.get("text") or "")
            try:
                validate_unit_text(
                    label,
                    text,
                    self.project_json,
                    unit_id=str(unit["unit_id"]),
                    max_refs=max_refs,
                    tolerated_speakers=dialogue_speakers(text),
                )
                validate_dialogue_load(
                    label, text, int(unit["duration_seconds"]), source_language, speech_rate_override
                )
            except DraftViolation as exc:
                enriched = [
                    DraftViolation(
                        f"{item}；{hint}",
                        code=item.code,
                        label=label,
                        line=item.line,
                        locations=item.locations,
                        reason=item.reason,
                        action=item.action,
                        item_id=str(unit["unit_id"]),
                    )
                    for item in violation_items(exc)
                ]
                if len(enriched) == 1:
                    raise enriched[0] from exc
                raise DraftViolations(enriched) from exc

    def _build_reference_prompt_authoring_prompt(
        self, episode: int, targets: PromptAuthoringTargets, facts: PlanningVideoFacts, instructions: str | None
    ) -> str:
        return build_reference_video_prompt(
            project_overview=self.project_json.get("overview", {}),
            style=self.project_json.get("style", ""),
            style_description=self.project_json.get("style_description", ""),
            characters=self._project_bucket("characters"),
            scenes=self._project_bucket("scenes"),
            props=self._project_bucket("props"),
            script_plan_units=list(targets.entries),
            max_refs=self._resolve_max_refs(facts),
            aspect_ratio=self._resolve_aspect_ratio(),
            episode=episode,
            target_language=self.project_json.get("source_language") or "中文",
            instructions=instructions,
        )

    @staticmethod
    def _unit_durations(targets: PromptAuthoringTargets) -> dict[str, int]:
        """Durations of targeted units: authoring never changes them, so tier checks compare final bodies against these values."""
        return {str(unit["unit_id"]): int(unit["duration_seconds"]) for unit in targets.entries}

    async def _author_reference_units(
        self,
        episode: int,
        filename: str,
        targets: PromptAuthoringTargets,
        *,
        formal_baseline: str | None,
        instructions: str | None,
        before_quarantine_commit: Callable[[], None] | None,
    ) -> Path:
        """Reference-to-video authoring rewrites only selected unit bodies; others remain verbatim.

        LLMs emit reference-syntax bodies only, matching selected units' count/order. Retain invalid output
        with itemized reports in repair drafts, revalidating on promotion before formal writes. Resampling
        wastes money without convergence; unchanged models likely repeat the same mistakes on the same bodies.
        """
        facts = await self._fetch_video_request_facts()
        units = list(targets.entries)

        def preflight() -> str | None:
            try:
                self._assert_reference_units_authorable(units, facts=facts)
            except DraftViolation as exc:
                rejection = self._unit_rejection(targets, exc)
                if rejection is None:
                    raise
                raise rejection from exc
            except UnitDurationOffTier as exc:
                rejection = self._duration_rejection(targets, exc)
                if rejection is None:
                    raise
                raise rejection from exc
            return self._reference_prompt_authoring_draft_revision(episode)

        if await asyncio.to_thread(preflight) is not None:
            raise DraftViolation(
                "reference prompt_authoring 草稿待处置；正式生成已中止，请先晋升或丢弃现有草稿",
                code="draft_revision_conflict",
            )
        max_refs = self._resolve_max_refs(facts)
        logger.info("正在为第 %d 集编写提示词（video_units，%d/%d 个单元）...", episode, len(units), len(targets.items))
        result = await self._generate_text(
            TextGenerationRequest(
                prompt=self._build_reference_prompt_authoring_prompt(episode, targets, facts, instructions),
                response_schema=ReferencePromptAuthoringFlatScript,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            )
        )
        response_text = result.text

        async def quarantine(exc: DraftViolation) -> DraftViolation:
            return await run_sync_transaction(
                self._quarantine_reference_prompt_authoring,
                episode,
                response_text,
                exc,
                unit_ids=targets.ids,
                base_fingerprint=formal_baseline,
                expected_draft_revision=None,
                before_commit=before_quarantine_commit,
            )

        # Include _add_metadata in draft protection: it recalculates tiers from final bodies. Adding/removing
        # `@` references may make units out-of-tier only after merging; uncaught paid output would otherwise exist only in memory.
        def author() -> dict[str, Any]:
            authored = self._merge_reference_visual(units, response_text, episode, max_refs=max_refs)
            return self._authored_script(
                episode, targets, authored, reference_unit_durations=self._unit_durations(targets), facts=facts
            )

        try:
            script_data = await asyncio.to_thread(author)
        except DraftViolation as exc:
            raise await quarantine(exc) from exc
        try:
            output_path = await run_sync_transaction(
                self._save_reference_prompt_authoring_if_draft_unchanged,
                episode,
                None,
                script_data,
                filename,
                formal_baseline,
            )
        except ScriptWriteConflict as exc:
            raise await quarantine(
                DraftViolation(
                    "正式剧本在模型生成期间已变化；本次生成结果已保留为 prompt_authoring 草稿，请合并最新正式内容后再晋升",
                    code="formal_revision_conflict",
                )
            ) from exc
        self._quality_probe(script_data, episode)
        logger.info("剧本已保存至 %s", output_path)
        return output_path

    def _parse_unit_texts(self, response_text: str, episode: int) -> ReferencePromptAuthoringFlatScript:
        text = strip_json_code_fences(response_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}") from e
        # Missing/blank title fallback precedes validation: titles are not written back, so omitting whole fields in
        # nonconstrained decoding must not waste paid expansion, matching _parse_response fallback.
        if isinstance(data, dict):
            raw_title = data.get("title")
            if not (isinstance(raw_title, str) and raw_title.strip()):
                data["title"] = episode_title(self.project_json, episode)
        try:
            return ReferencePromptAuthoringFlatScript.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"prompt_authoring 提示词编写结构校验失败: {e}") from e

    def _merge_reference_visual(
        self,
        units: list[dict],
        response_text: str,
        episode: int,
        *,
        max_refs: int | None,
    ) -> list[dict]:
        """Merge reference authoring: LLM emits reference-syntax bodies, positioned into targeted units; retain all other fields.

        Enforce structural diffs here: unit count/order and normative dialogue lines must match existing
        bodies verbatim; authoring may only expand visual descriptions. Changes fail loudly with
        ``DraftViolation``, never silently pass. Dialogue/visual mismatches require timeline body edits,
        not autonomous authoring dialogue rewrites.

        Gather all per-unit failures and raise ``DraftViolations`` once so callers persist whole output and
        reports in repair drafts. Raising individually would require another paid expansion after every unit repair.
        """
        flat = self._parse_unit_texts(response_text, episode)
        if len(flat.units) != len(units):
            raise DraftViolation(
                f"prompt_authoring 产出的 unit 数（{len(flat.units)}）与待编写单元数（{len(units)}）不一致；"
                "prompt_authoring 只做提示词编写，不得合并、拆分或增删 unit",
                code="unit_count_changed",
            )

        authored: list[dict] = []
        violations: list[DraftViolation] = []
        for index, (unit, flat_unit) in enumerate(zip(units, flat.units, strict=True)):
            label = f"unit {unit['unit_id']}"
            # Collect all failing units, not just the first, so Agents see all repairs in one round.
            # Within each unit, stop at first failure: later checks depend on the same unparseable-body problem.
            try:
                validate_unit_text(
                    label,
                    flat_unit.text,
                    self.project_json,
                    max_refs=max_refs,
                    tolerated_speakers=dialogue_speakers(str(unit.get("text") or "")),
                )
                assert_dialogue_preserved(label, str(unit.get("text") or ""), flat_unit.text)
            except DraftViolation as exc:
                items = violation_items(exc)
                locate_violations(items, item_index=index, item_id=str(unit["unit_id"]))
                violations.extend(items)
                continue
            authored.append({**unit, "text": flat_unit.text})

        if violations:
            raise DraftViolations(violations)
        return authored

    async def _author_ad_reference_units(
        self,
        episode: int,
        filename: str,
        targets: PromptAuthoringTargets,
        *,
        formal_baseline: str | None,
        instructions: str | None,
    ) -> Path:
        """Author ad/short-film reference-unit bodies from briefs, products, and neighboring units.

        Retain current durations, unquantized by provider tiers for ad orchestration, and existing dialogue
        verbatim. Like whole-ad generation, invalid output errors directly without repair drafts.
        """
        logger.info(
            "正在为第 %d 集编写提示词（ad video_units，%d/%d 个单元）...",
            episode,
            len(targets.entries),
            len(targets.items),
        )
        result = await self._generate_text(
            TextGenerationRequest(
                prompt=self._build_ad_reference_prompt_authoring_prompt(episode, targets, instructions),
                response_schema=ReferencePromptAuthoringFlatScript,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            )
        )
        flat = self._parse_unit_texts(result.text, episode)
        if len(flat.units) != len(targets.entries):
            raise ValueError(
                f"提示词编写产出的 unit 数（{len(flat.units)}）与待编写单元数（{len(targets.entries)}）不一致"
            )
        authored: list[dict] = []
        problems: list[str] = []
        for unit, flat_unit in zip(targets.entries, flat.units, strict=True):
            label = f"unit {unit['unit_id']}"
            try:
                assert_dialogue_preserved(label, str(unit.get("text") or ""), flat_unit.text)
                validate_unit_text(label, flat_unit.text, self.project_json, max_refs=None)
            except DraftViolation as exc:
                # Match whole-ad admission: speech-ownership problems set needs_replan without blocking writes.
                blocking = [item for item in violation_items(exc) if item.code not in _AD_UNIT_REPLAN_CODES]
                if blocking:
                    problems.extend(str(item) for item in blocking)
                    continue
            authored.append({**unit, "text": flat_unit.text})
        if problems:
            raise ValueError("提示词编写产出的单元正文不合规：" + "；".join(problems))
        script_data = self._authored_script(episode, targets, authored)
        output_path = await run_sync_transaction(
            self._save_script,
            script_data,
            filename,
            validate=True,
            expected_fingerprint=formal_baseline,
        )
        self._quality_probe(script_data, episode)
        logger.info("剧本已保存至 %s", output_path)
        return output_path

    def _ad_prompt_common(self, episode: int, instructions: str | None) -> dict[str, Any]:
        """Persistent input slots shared by whole-ad and authoring prompts, from the same source as artifact bases."""
        direct_inputs = project_ad_episode_script_inputs(episode, project=self.project_json)
        return {
            "project_overview": cast(dict[str, Any], direct_inputs["overview"]),
            "style": direct_inputs["style"],
            "style_description": direct_inputs["style_description"],
            "characters": cast(dict[str, Any], direct_inputs["characters"]),
            "scenes": cast(dict[str, Any], direct_inputs["scenes"]),
            "props": cast(dict[str, Any], direct_inputs["props"]),
            "products": cast(dict[str, Any], direct_inputs["products"]),
            "brief": direct_inputs["brief"],
            "episode": direct_inputs["episode"],
            "aspect_ratio": direct_inputs["aspect_ratio"],
            "target_language": direct_inputs["target_language"],
            "instructions": instructions,
        }

    def _build_ad_shot_prompt_authoring_prompt(
        self, episode: int, targets: PromptAuthoringTargets, instructions: str | None
    ) -> str:
        return build_ad_shot_prompt_authoring_prompt(
            **self._ad_prompt_common(episode, instructions),
            shots=[item for item in targets.items if isinstance(item, dict)],
            target_ids=targets.ids,
        )

    def _build_ad_reference_prompt_authoring_prompt(
        self, episode: int, targets: PromptAuthoringTargets, instructions: str | None
    ) -> str:
        return build_ad_reference_prompt_authoring_prompt(
            **self._ad_prompt_common(episode, instructions),
            units=[item for item in targets.items if isinstance(item, dict)],
            target_ids=targets.ids,
        )

    def _prompt_authoring_flat_content(self, response_text: str, episode: int) -> dict:
        """Restore authoring responses to repair-draft flat shape ``{title, units: [{text}]}``.

        Match ``_merge_reference_visual`` parsing step by step: strip fences, title fallback, schema checks.
        Repair drafts must contain schema-valid content violations only, or Agent-edited body shapes diverge
        from merging's expected shapes.
        """
        return self._parse_unit_texts(response_text, episode).model_dump()

    def _quarantine_reference_prompt_authoring(
        self,
        episode: int,
        response_text: str,
        exc: DraftViolation,
        *,
        unit_ids: Sequence[str],
        base_fingerprint: str | _UnsetExpectedFingerprint | None = _UNSET_EXPECTED_FINGERPRINT,
        expected_draft_revision: str | None,
        before_commit: Callable[[], None] | None = None,
    ) -> DraftViolation:
        """Persist invalid authoring output/reports as repair drafts; return the report-carrying violation for callers to raise.

        ``meta.unit_ids`` records corresponding units in script order; promotion retrieves the same formal units.

        Return rather than raise: callers use ``raise ... from exc`` to preserve original chains. Raising here
        would point tracebacks to this function instead of merging logic.
        """
        draft_path = quarantine_path(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        formal_path = (
            self.project_path / "scripts" / formal_script_filename(self.project_path, self.project_json, episode)
        )
        pm = ProjectManager.for_project_dir(self.project_path)
        with pm.file_lock(draft_path), pm.file_lock(formal_path):
            current = read_quarantine(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
            actual_draft_revision = draft_revision(current) if current is not None else None
            if actual_draft_revision != expected_draft_revision:
                return DraftViolation(
                    "reference prompt_authoring 草稿在模型生成期间已变化；本次生成结果未覆盖并发编辑，请先处置现有草稿",
                    code="draft_revision_conflict",
                )
            if before_commit is not None:
                before_commit()
            violations = violation_items(exc)
            locate_violations_by_id(violations, unit_ids)
            report = quarantine_and_report(
                self.project_path,
                episode,
                QUARANTINE_KIND_PROMPT_AUTHORING,
                content=self._prompt_authoring_flat_content(response_text, episode),
                violations=violations,
                meta={
                    "base_fingerprint": (
                        content_fingerprint(formal_path)
                        if isinstance(base_fingerprint, _UnsetExpectedFingerprint)
                        else base_fingerprint
                    ),
                    "unit_ids": list(unit_ids),
                },
            )
        return DraftViolation(report, code="quarantined")

    def _reference_prompt_authoring_draft_revision(self, episode: int) -> str | None:
        path = quarantine_path(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        with ProjectManager.for_project_dir(self.project_path).file_lock(path):
            draft = read_quarantine(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
            return draft_revision(draft) if draft is not None else None

    def _save_reference_prompt_authoring_if_draft_unchanged(
        self,
        episode: int,
        expected_draft_revision: str | None,
        script_data: dict[str, Any],
        filename: str,
        formal_baseline: str | None,
    ) -> Path:
        draft_path = quarantine_path(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        pm = ProjectManager.for_project_dir(self.project_path)
        with pm.file_lock(draft_path):
            current = read_quarantine(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
            actual_draft_revision = draft_revision(current) if current is not None else None
            if actual_draft_revision != expected_draft_revision:
                raise DraftViolation(
                    "reference prompt_authoring 草稿在模型生成期间已变化；本次生成结果未覆盖并发编辑，请先处置现有草稿",
                    code="draft_revision_conflict",
                )
            return pm.save_script(
                self.project_path.name,
                script_data,
                filename,
                validate=True,
                expected_fingerprint=formal_baseline,
            )

    def _promote_reference_prompt_authoring_draft_sync(
        self,
        episode: int,
        facts: PlanningVideoFacts,
        output_filename: str | None = None,
        *,
        expected_fingerprint: str | _UnsetExpectedFingerprint | None = _UNSET_EXPECTED_FINGERPRINT,
        _prompt_authoring_lock_held: bool = False,
    ) -> Path:
        draft_path = quarantine_path(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        pm = ProjectManager.for_project_dir(self.project_path)
        prompt_authoring_lock = nullcontext() if _prompt_authoring_lock_held else pm.file_lock(draft_path)
        with prompt_authoring_lock:
            return self._promote_reference_prompt_authoring_draft_locked_sync(
                episode,
                facts,
                output_filename,
                expected_fingerprint=expected_fingerprint,
            )

    def _promote_reference_prompt_authoring_draft_locked_sync(
        self,
        episode: int,
        facts: PlanningVideoFacts,
        output_filename: str | None = None,
        *,
        expected_fingerprint: str | _UnsetExpectedFingerprint | None = _UNSET_EXPECTED_FINGERPRINT,
    ) -> Path:
        draft = read_quarantine(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        if draft is None:
            draft_path = quarantine_path(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
            raise FileNotFoundError(
                f"集（id={episode}）没有可晋升的 prompt_authoring 待修复草稿"
                f"（{draft_path.relative_to(self.project_path).as_posix()} 缺失或内容不是合法信封）"
            )

        filename = output_filename or formal_script_filename(self.project_path, self.project_json, episode)
        raw_unit_ids = draft.meta.get("unit_ids")
        unit_ids = (
            [unit_id for unit_id in raw_unit_ids if isinstance(unit_id, str)]
            if isinstance(raw_unit_ids, list)
            else None
        )
        targets = self._load_prompt_authoring_targets(episode, filename, unit_ids, rewrite=True)
        if targets is None:
            raise FileNotFoundError(f"集（id={episode}）尚无正式脚本，无法晋升 prompt_authoring 待修复草稿")
        if unit_ids is None:
            # Drafts without recorded units came from whole-script authoring; revalidate all formal units and report count mismatches faithfully.
            targets = replace(targets, entries=tuple(item for item in targets.items if isinstance(item, dict)))
        units = list(targets.entries)
        # Share generation preflight: users may edit timeline units while drafts exist; recheck to stop dialogue exceeding shortened
        # durations or unregistered @[名称] references from persisting through promotion.
        try:
            self._assert_reference_units_authorable(units, facts=facts)
        except (DraftViolation, UnitDurationOffTier) as exc:
            raise self._formal_unit_promotion_violation(targets, exc) from exc
        max_refs = self._resolve_max_refs(facts)
        try:
            authored = self._merge_reference_visual(units, json.dumps(draft.content), episode, max_refs=max_refs)
            if unit_ids is None:
                # Unchanged bodies in whole drafts do not count as authored: write only changed units and retain others,
                # including pending-authoring flags, from formal scripts. Still merge/revalidate every unit and report violations normally.
                changed = [
                    (unit, merged)
                    for unit, merged in zip(units, authored, strict=True)
                    if merged["text"] != unit.get("text")
                ]
                targets = replace(targets, entries=tuple(unit for unit, _ in changed))
                authored = [merged for _, merged in changed]
            # Include _add_metadata via _authored_script: final-body tier recomputation detects added/removed
            # `@` references only after merging. Outside try, these errors would again leave promotion
            # failing without refreshing draft reports.
            script_data = self._authored_script(
                episode, targets, authored, reference_unit_durations=self._unit_durations(targets), facts=facts
            )
        except DraftViolation as exc:
            violations = violation_items(exc)
            locate_violations_by_id(violations, [str(unit.get("unit_id")) for unit in units])
            raise DraftViolation(
                quarantine_and_report(
                    self.project_path,
                    episode,
                    QUARANTINE_KIND_PROMPT_AUTHORING,
                    content=draft.content,
                    violations=violations,
                    meta=draft.meta,
                ),
                code="quarantined",
            ) from exc
        except ValueError as exc:
            # Schema failures also only return reports (DraftViolation subclasses ValueError and must be checked first):
            # Agents authored this content, without a backend to retry, matching script_plan promotion schema_invalid.
            violations = (
                schema_violations(exc.__cause__, draft.content, "units")
                if isinstance(exc.__cause__, ValidationError)
                else [
                    DraftViolation(
                        f"待修复草稿的 content 不符合 prompt_authoring 产出结构：{exc}", code="schema_invalid"
                    )
                ]
            )
            for violation in violations:
                if violation.item_index is not None and violation.item_index < len(units):
                    violation.item_id = str(units[violation.item_index]["unit_id"])
            raise DraftViolation(
                quarantine_and_report(
                    self.project_path,
                    episode,
                    QUARANTINE_KIND_PROMPT_AUTHORING,
                    content=draft.content,
                    violations=violations,
                    meta=draft.meta,
                ),
                code="quarantined",
            ) from exc

        pm = ProjectManager.for_project_dir(self.project_path)
        if isinstance(expected_fingerprint, _UnsetExpectedFingerprint):
            if "base_fingerprint" not in draft.meta:
                raise DraftViolation(
                    "reference prompt_authoring 草稿缺少生成时的正式剧本基线 base_fingerprint；无法安全晋升",
                    code="formal_revision_missing",
                )
            resolved_expected_fingerprint = cast(str | None, draft.meta["base_fingerprint"])
        else:
            resolved_expected_fingerprint = expected_fingerprint
        output_path = pm.save_script(
            self.project_path.name,
            script_data,
            filename,
            validate=True,
            expected_fingerprint=resolved_expected_fingerprint,
        )
        # Clear drafts only after successful writes: failed persistence leaves drafts retryable, never losing both copies.
        clear_quarantine(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        self._quality_probe(script_data, episode)
        return output_path

    async def promote_reference_prompt_authoring_draft(
        self,
        episode: int,
        output_filename: str | None = None,
        *,
        expected_fingerprint: str | _UnsetExpectedFingerprint | None = _UNSET_EXPECTED_FINGERPRINT,
        _prompt_authoring_lock_held: bool = False,
    ) -> Path:
        """Fully revalidate repair drafts with the generation validator; promote passing results and clear drafts.

        Use ``_merge_reference_visual`` itself, never a simplified copy. Promotion and generation must share
        code to prevent promotion-pass/next-generation-reject divergence. Reread corresponding formal units:
        users may have edited timelines while drafts existed, so structural diffs compare current values.

        Remaining violations refresh stored report snapshots before raising ``DraftViolation``; retain drafts
        for further repairs without a repair-round limit.
        """
        facts = await self._fetch_video_request_facts()
        return await run_sync_transaction(
            self._promote_reference_prompt_authoring_draft_sync,
            episode,
            facts,
            output_filename,
            expected_fingerprint=expected_fingerprint,
            _prompt_authoring_lock_held=_prompt_authoring_lock_held,
        )

    def _parse_response(self, response_text: str, episode: int) -> dict:
        """Parse and validate TextBackend responses.

        Args:
            response_text: JSON text returned by the API.
            episode: Episode number.

        Returns:
            Validated script data dictionary.
        """
        # Strip possible Markdown wrappers.
        text = strip_json_code_fences(response_text)

        # Parse JSON.
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"JSON 顶层须是对象，实际是 {type(data).__name__}")

        # Missing/blank title fallback: nonconstrained decoders may omit entire fields. Titles are display-only and
        # user-editable, not worth failing whole episodes; match _merge_narration_visual fallback.
        title = data.get("title")
        if not (isinstance(title, str) and title.strip()):
            data["title"] = episode_title(self.project_json, episode)

        # Canonical resolution selects skeletons: storyboard i2v by creation type, reference-to-video always video_units.
        # Keep kind→model mappings local because higher-level model dependencies stay outside narrow SKELETONS tables.
        kind = resolve_declared_kind(self.content_mode, self.generation_mode)
        schema = _KIND_PARSE_SCHEMA[kind]
        try:
            parsed = schema.model_validate(data).model_dump()
        except ValidationError as e:
            logger.warning("数据验证警告: %s", e)
            # Return raw data, allowing partial schema nonconformance.
            return data
        # Script models do not accept episode-new assets; callers extract/resolve them from returned output.
        if NEW_ASSETS_FIELD in data:
            parsed[NEW_ASSETS_FIELD] = data[NEW_ASSETS_FIELD]
        return parsed

    def _resolve_ad_new_assets(self, raw_items: object) -> NewAssetResolution:
        """Resolve new assets from whole-script generation against current projects; unresolved handling decisions violate constraints."""
        try:
            return resolve_new_assets(self.project_json, raw_items)
        except NewAssetsError as exc:
            raise AdScriptRejected([problem.message for problem in exc.problems]) from exc

    def _parse_ad_storyboard_response(self, response_text: str, episode: int) -> tuple[dict, NewAssetResolution]:
        """Parse whole ad/short-film storyboard output: script plus new-asset handling decisions.

        Take episode titles from episode ledgers, possibly empty; ignore model titles.
        """
        try:
            data = self._parse_response(response_text, episode)
        except ValueError as exc:
            raise AdScriptRejected([str(exc)]) from exc
        data["title"] = episode_title(self.project_json, episode)
        raw_new_assets = data.pop(NEW_ASSETS_FIELD, None)
        return data, self._resolve_ad_new_assets(raw_new_assets)

    def _parse_ad_reference_response(self, response_text: str, episode: int) -> tuple[dict, NewAssetResolution]:
        """Mechanically lift flat ad/short-film reference output to self-contained ``video_units`` and resolve new assets.

        Body ``@[名称]`` references must be registered or new here; validate against project views overlaid
        with new items. ``needs_replan`` carries speech-ownership issues; gather/reject all other violations.
        Use episode-ledger titles, possibly empty, never model-provided titles.
        """
        text = strip_json_code_fences(response_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AdScriptRejected([f"广告参考剧本 JSON 解析失败: {exc}"]) from exc
        try:
            flat = AdReferenceFlatScript.model_validate(data)
        except ValidationError as exc:
            raise AdScriptRejected([f"广告参考剧本结构校验失败: {exc}"]) from exc
        raw_new_assets = [item.model_dump() for item in flat.new_assets]
        planning = planning_project(self.project_json, raw_new_assets)
        problems: list[str] = []
        for ordinal, source in enumerate(flat.units, start=1):
            try:
                validate_unit_text(f"unit E{episode}U{ordinal}", source.text, planning, max_refs=None)
            except DraftViolation as exc:
                problems.extend(str(item) for item in violation_items(exc) if item.code not in _AD_UNIT_REPLAN_CODES)
        new_assets = self._resolve_ad_new_assets(raw_new_assets)
        if problems:
            raise AdScriptRejected(problems)

        units: list[dict] = []
        for ordinal, source in enumerate(flat.units, start=1):
            unit: dict = {
                "unit_id": f"E{episode}U{ordinal}",
                "text": source.text,
                "duration_seconds": source.duration_seconds,
                "note": None,
                "generated_assets": {},
            }
            units.append(unit)

        script = ReferenceVideoScript.model_validate(
            {
                "title": episode_title(self.project_json, episode),
                "content_mode": "ad",
                "video_units": units,
            }
        ).model_dump()
        return script, new_assets

    def _add_metadata(
        self,
        script_data: dict,
        episode: int,
        *,
        reference_unit_durations: dict[str, int] | None = None,
        facts: PlanningVideoFacts | None = None,
    ) -> dict:
        """Add script metadata.

        Args:
            script_data: Script data.
            episode: Episode number.
            reference_unit_durations: Mechanically replace LLM reference_video durations by rewritten unit_id
                with original confirmed script_plan values, before tier mapping. Recompute tiers per unit below;
                see construction comments in ``generate``.
            facts: Video-request facts for per-unit effective tiers; required with ``reference_unit_durations``,
                never skipping tier checks.

        Returns:
            Script data with metadata.
        """
        gen_mode = self.generation_mode
        rewritten_output_ids = prepare_script_entries(script_data, project=self.project_json, episode=episode)

        if reference_unit_durations is not None:
            # Override durations only when unit_id sets exactly match script_plan. Missing confirmed units or unfamiliar
            # output ids reveal disconnection from the planning base. Duration replacement cannot hide this deeper
            # problem; share fail-loud policy with storyboard visual writes in _merge_visual_layer.
            dupes = sorted(uid for uid, count in Counter(rewritten_output_ids).items() if count > 1)
            if dupes:
                raise ValueError(f"reference_video 输出 unit_id 重复: {dupes}")
            missing = sorted(set(reference_unit_durations) - set(rewritten_output_ids))
            if missing:
                raise ValueError(f"reference_video 输出缺少 script_plan 已确认的 unit_id: {missing}")
            unknown = sorted(set(rewritten_output_ids) - set(reference_unit_durations))
            if unknown:
                raise ValueError(f"reference_video 输出包含 script_plan 之外的未知 unit_id: {unknown}")

            raw_rewrite_items, id_field, _kind = resolve_kind_items(
                script_data, kind=resolve_declared_kind(self.content_mode, gen_mode)
            )
            authored = [
                s
                for s in (raw_rewrite_items if isinstance(raw_rewrite_items, list) else [])
                if isinstance(s, dict) and id_field in s
            ]
            if facts is None:
                raise ValueError("reference_video 取档校验需要视频请求事实")
            # Determine tiers from final persisted unit bodies, not splitting-time state. LLM-authored
            # `@[名称]` references may differ from script_plan. Current available images select buckets,
            # matching confirmation/execution predicates and reading that bucket's video-request facts.
            for s, hydration in zip(authored, self._hydrate_reference_units(authored), strict=True):
                target_duration = reference_unit_durations[s[id_field]]
                bucket = hydration.hydrated_generation_type
                try:
                    facts.require(bucket)
                except VideoRequestFactsError as exc:
                    # Video-model configuration failures, not body violations: problem codes express causes/repairs and localize Web views.
                    params: dict[str, Any] = exc.params
                    raise DraftViolation(
                        f"unit {s[id_field]}：{translate(exc.code, **params)}（{exc.failure.summary()}）",
                        code=exc.code,
                        label=f"unit {s[id_field]}",
                        item_id=str(s[id_field]),
                        params=params,
                    ) from exc
                unit_tiers = self._unit_duration_off_tier(target_duration, facts=facts, generation_type=bucket)
                if unit_tiers is not None:
                    # Effective tiers excluding confirmed durations must not silently remap them: user-reviewed durations/costs
                    # cannot be replaced with unreviewed persisted values. Raise content violations instead of bare ValueError
                    # so reference routes preserve paid output as repair drafts. Usually authoring added/removed unit `@` references;
                    # repairing those body references can fix drafts, without discarding and resampling.
                    raise DraftViolation(
                        f"unit {s[id_field]} 已确认时长 {target_duration}s 不在当前生效档位 "
                        f"{sorted(set(unit_tiers))} 内；通常是该次生成给该 unit 新增/去掉了引用"
                        "导致，请调整该 unit 正文里的 `@` 引用使其回到该档位；若引用本就该是这样，"
                        "说明模型能力已变化，需要重新拆分该集 script_plan",
                        code="duration_off_tier",
                        label=f"unit {s[id_field]}",
                        item_id=str(s[id_field]),
                    )
                if s.get("duration_seconds") != target_duration:
                    logger.warning(
                        "unit %s 时长与 script_plan 确认值不一致（LLM 输出 %s，已按 script_plan 确认值 %s 覆盖）",
                        s[id_field],
                        s.get("duration_seconds"),
                        target_duration,
                    )
                s["duration_seconds"] = target_duration
        return finish_script_document(
            script_data,
            project=self.project_json,
            episode=episode,
            generator=self.generator.model if self.generator else "unknown",
        )

    def _quality_probe(self, script_data: dict, episode: int) -> None:
        """Lightweight postpersistence quality probe: logging only, never blocking or retrying.

        Count extremely short scene/action/shot text samples below character thresholds to locate short-content
        risks. Thresholds catch obvious anomalies only; complete normal descriptions should far exceed them.
        Outer try/except protects generate: when _parse_response returns raw dicts after validation failures
        with invalid nested types (such as string image_prompt), probes only warn, never block generation.
        """
        try:
            short_ids: list[str] = []

            # Canonical skeleton resolution uses resolve_kind_items for arrays/id fields, matching _add_metadata
            # id rewrites. Short video_units samples come from bodies, unlike flat narration/drama/ad
            # image_prompt/video_prompt probes; explicitly branch by kind here rather than skeleton dispatch.
            kind = resolve_declared_kind(self.content_mode, self.generation_mode)
            raw_items, id_key, _kind = resolve_kind_items(script_data, kind=kind)
            # Degraded raw dicts may contain nonlist arrays; `... or []` misses truthy scalars.
            # isinstance guards prevent `for` iteration crashes; outer try/except would swallow errors but incorrectly skip whole probes.
            items = raw_items if isinstance(raw_items, list) else []
            if kind == "video_units":
                for u in items:
                    if not isinstance(u, dict):
                        continue
                    uid = str(u.get(id_key) or "?")
                    if len(str(u.get("text") or "")) < _QUALITY_PROBE_UNIT_TEXT_MIN_LEN:
                        short_ids.append(uid)
            else:
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    iid = str(item.get(id_key) or "?")
                    img_p = item.get("image_prompt")
                    vid_p = item.get("video_prompt")
                    img_p = img_p if isinstance(img_p, dict) else {}
                    vid_p = vid_p if isinstance(vid_p, dict) else {}
                    scene = str(img_p.get("scene") or "")
                    action = str(vid_p.get("action") or "")
                    if len(scene) < _QUALITY_PROBE_SCENE_MIN_LEN or len(action) < _QUALITY_PROBE_ACTION_MIN_LEN:
                        short_ids.append(iid)

            if short_ids:
                logger.warning(
                    "episode %d quality probe: short=%s",
                    episode,
                    sorted(set(short_ids)),
                )

            # Narration novel_text now passes through script_plan without authoring regeneration, structurally eliminating
            # expansion drift and any LLM secret-expansion window. Remove novel_text drift probes accordingly.

            # Observe ad total duration near target_duration; provider-duration enum quantization makes exact
            # matches unrealistic. WARN only, without blocking, retries, or frontend delivery.
            if self.content_mode == "ad":
                target = self.project_json.get("target_duration")
                if isinstance(target, int) and not isinstance(target, bool) and target > 0:
                    total = script_duration_total(kind, items)
                    delta_ratio = abs(total - target) / target
                    if delta_ratio > AD_TARGET_DURATION_DRIFT_THRESHOLD:
                        logger.warning(
                            "episode %d target_duration drift: target=%d actual=%d delta=%.1f%%",
                            episode,
                            target,
                            total,
                            delta_ratio * 100,
                        )
        except Exception as exc:
            logger.warning("episode %d quality probe skipped due to unexpected data shape: %s", episode, exc)
