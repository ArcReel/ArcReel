"""Service layer for script_plan→prompt_authoring Web content confirmation: read confirmation state, edit structured intermediate content, and confirm.

Pure gate logic (applicability / fingerprints / derived state) lives in ``lib.script.script_review``.
This layer adds ProjectManager persistence (confirmation fingerprint in project.json
``episodes[i].script_plan_review``), Pydantic structural validation, and content persistence.

Confirmation mechanically converts the entire script plan to a formal script (all entries pending
authoring), persisted together with confirmation records. Existing formal scripts are overwritten only
with explicit caller approval. The Agent's ``generate_episode_script`` tool performs prompt authoring
(script visual generation), reads only the formal script, and does not pass through the content-confirmation gate."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from lib.config.resolver import ConfigResolver
from lib.episode.episode_target_duration import project_episode_target_duration
from lib.generation.video_request_facts import (
    VideoRequestFactsError,
    VideoRequestFactsFailure,
    reference_migration_durations,
)
from lib.infra.async_thread import run_sync_transaction
from lib.infra.json_io import load_json_or_none
from lib.project.project_manager import ProjectManager, ScriptWriteConflict
from lib.script import script_review
from lib.script.draft_quarantine import (
    DRAFT_OWNER_AGENT,
    DRAFT_OWNER_USER,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_TO_DOC_TYPE,
    draft_owner,
    draft_revision,
    quarantine_path,
    read_quarantine,
    violation_entries,
)
from lib.script.plan_new_assets import NEW_ASSETS_FIELD, NewAssetsError, UnregisteredReferencesError, resolve_new_assets
from lib.script.script_generator import ScriptGenerator, UnitDurationOffTier
from lib.script.script_models import DramaNormalizedScript, NarrationScriptPlanDraft, ReferenceScriptPlanDraft
from lib.speech.speech_composition import SpeechAdmission, SpeechAdmissionError, admit_script_unit
from server.services.tasks.video_caps import (
    reference_request_facts_lookup,
    reference_unit_capabilities,
)
from server.text_generation import fetch_reference_split_caps, reference_soft_violations, soft_violation_entries

logger = logging.getLogger(__name__)

#: Structured script_plan intermediate-validation models (by ``script_review.script_plan_kind`` variant). Validate saved edits with these:
#: drama uses content-layer DramaNormalizedScript (utterances / source_text / scene_description);
#: narration uses NarrationScriptPlanDraft (structured novel_text storyboards); reference_video uses ReferenceScriptPlanDraft
#: (units → body + planned duration).
_SCRIPT_PLAN_CONTENT_MODEL: dict[str, type[BaseModel]] = {
    "drama": DramaNormalizedScript,
    "narration": NarrationScriptPlanDraft,
    "reference_video": ReferenceScriptPlanDraft,
}


class ScriptReviewError(Exception):
    """Domain error for gate operations. Routers map ``code`` to HTTP status and i18n key; ``message`` contains technical details."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        admission: SpeechAdmission | None = None,
        overwrite: dict[str, Any] | None = None,
        script_filename: str | None = None,
        problem: VideoRequestFactsFailure | None = None,
        params: Mapping[str, object] | None = None,
    ):
        super().__init__(message or code)
        self.code = code
        self.message = message
        self.admission = admission
        #: ``video_request_facts`` carries a video-request-fact failure; routers return its problem code and parameters to users.
        self.problem = problem
        #: ``overwrite_required`` carries the formal script to overwrite (``FormalScriptOverwrite.to_dict()``).
        self.overwrite = overwrite
        #: ``foreign_formal_script`` carries the filename occupying this episode's canonical path.
        self.script_filename = script_filename
        #: ``duration_off_tier`` carries off-tier unit UI positions, current duration, and selectable tiers for router wording.
        self.params: dict[str, object] = dict(params or {})


def _require_changed_speech_admitted(kind: str, previous: object, candidate: object) -> None:
    """Reject only new or edited speech content, leaving legacy mixed metadata editable."""

    if kind == "drama":
        root, skeleton, id_field, speech_fields = "scenes", "scenes", "scene_id", ("utterances",)
    elif kind == "reference_video":
        root, skeleton, id_field, speech_fields = "units", "video_units", "unit_id", ("text",)
    else:
        return
    if not isinstance(candidate, dict) or not isinstance(candidate.get(root), list):
        return
    previous_units = previous.get(root) if isinstance(previous, dict) else None
    previous_by_id = {
        unit.get(id_field): unit
        for unit in previous_units or []
        if isinstance(unit, dict) and isinstance(unit.get(id_field), str)
    }
    for unit in candidate[root]:
        if not isinstance(unit, dict) or not isinstance(unit.get(id_field), str):
            continue
        old = previous_by_id.get(unit[id_field])
        speech_changed = old is None or any(old.get(field) != unit.get(field) for field in speech_fields)
        if not speech_changed:
            if isinstance(old, dict) and old.get("needs_replan") is True:
                unit["needs_replan"] = True
            continue
        admission = admit_script_unit(skeleton, unit, ignore_marker=True)
        if not admission.allowed:
            raise ScriptReviewError("speech_admission", admission=admission)
        unit.pop("needs_replan", None)


class ScriptReviewService:
    """Encapsulate script_plan→prompt_authoring content-confirmation reads/writes. Routers and tests operate the gate here, without direct file / project.json access."""

    def __init__(self, pm: ProjectManager, *, config_resolver: ConfigResolver | None = None):
        self.pm = pm
        self.config_resolver = config_resolver

    def _resolve_script_plan_model(self, project: dict[str, Any], episode: int) -> tuple[str, type[BaseModel]]:
        """Episode script_plan variant and structural-validation model; raise not_applicable when the gate does not apply (no structured script_plan).

        ``script_review.script_plan_kind`` is the sole source for variant selection (project generation mode
        prioritizes reference_video across content_mode). Select the Pydantic model from it and return the
        variant name for caller dispatch."""
        kind = script_review.script_plan_kind(project)
        if kind is None:
            raise ScriptReviewError("not_applicable")
        return kind, _SCRIPT_PLAN_CONTENT_MODEL[kind]

    def _require_episode(self, project_name: str, project: dict[str, Any], episode: int) -> dict[str, Any]:
        """When the gate applies, require registration in project.json ``episodes[]`` and return the project.

        Use the same write prerequisite as ``confirm``: prevent ``get_state`` reporting unregistered episodes
        as no_script_plan and ``save_content`` writing orphan script_plan files that can never link to
        project.json. Only explicit registration adds episodes to the ledger; same-named episode files in ``source/`` do not count."""
        if script_review.find_episode(project, episode) is None:
            raise ScriptReviewError("episode_not_found")
        return project

    async def _resolve_supported_durations(self, project: dict[str, Any]) -> list[int] | None:
        """Full duration set for structural adoption: reference routes use the union declared by r2v and i2v video request facts.

        Return None if either bucket cannot resolve. Read-time migration only clamps to structural ranges,
        never rewrites user seconds using the other bucket's tiers; the gate panel also offers no selectable
        tiers. Subsequent per-unit fact validation blocks tier drift.

        Callers must await this method **before** acquiring ``self.pm.file_lock``: it is a blocking file lock,
        and holding it across await also blocks other event-loop coroutines outside the lock."""
        if script_review.script_plan_kind(project) != "reference_video":
            return None
        request_facts = reference_request_facts_lookup(project, self.config_resolver)
        return reference_migration_durations(await request_facts("r2v"), await request_facts("i2v"))

    def _read_script_plan_migrated(
        self,
        project_name: str,
        project: dict[str, Any],
        episode: int,
        path: Path,
        project_path: Path,
        supported_durations: list[int] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Read structured script_plan and apply one-time reference-to-video draft duration migration; return ``(内容, 最新 project)``.

        Drafts are the sole content source for all three gate entry points (state / save / confirm). Adopted
        ``ReferenceScriptPlanUnit`` requires unit-level ``duration_seconds`` and rejects extra fields. Without
        read-time migration of existing drafts, saving and confirmation fail structural validation, leaving users
        unable to edit or confirm in the gate. Episode-script migration lives in ``ProjectManager.load_script``;
        draft migration is here, sharing the same migrator.

        Callers resolve ``supported_durations`` through ``_resolve_supported_durations`` and pass it in
        (this function runs under lock and cannot await), sharing prompt_authoring generation's source.
        Migration is idempotent and one-time, so the first path determines persisted results. If criteria differ,
        content confirmation running first could persist structurally valid but off-tier seconds, then
        prompt_authoring's enum schema rejects them, leaving the gate unable to reveal or fix the issue.
        None (no configured project video model) falls back to structural-range clamping: missing configuration
        must not block draft loading; execution-tier selection still guards drift.

        Callers must hold ``self.pm.file_lock(path)``; reference_video additionally requires the prompt_authoring
        draft lock first. Shared lock order: downstream draft → formal script_plan. This function only reads,
        modifies, and writes, acquiring no locks itself to avoid same-thread deadlock on a lock already held
        by callers such as ``confirm``."""
        content = _read_json(path)
        if content is None or script_review.script_plan_kind(project) != "reference_video":
            return content, project

        updated, _warnings = script_review.migrate_script_plan_draft_in_place(
            project_path,
            content,
            episode=episode,
            update_project=lambda mutate: self.pm.update_project(project_name, mutate),
            supported_durations=supported_durations,
        )
        return content, updated or project

    async def get_state(self, project_name: str, episode: int) -> dict[str, Any]:
        """Return episode content-confirmation state + structured intermediate content for Web rendering.

        ``content`` is parsed structured script_plan (drama: {title, scenes[]}; narration: {segments[]};
        reference_video: {units[]}), or None if the gate does not apply or script_plan is missing/corrupted.

        ``supported_durations`` is the full structural tier set for reference-route read-time migration.
        Unknown i2v means None; do not rewrite seconds using r2v tiers. ``get_reference_duration_tiers``
        provides selectable UI tiers by bucket.

        Resolve tiers before disk reads/writes, then offload all remaining synchronous I/O to a thread:
        ``file_lock`` blocks and cannot span await, while tier resolution awaits video-capability queries."""
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        supported_durations = await self._resolve_supported_durations(project)
        state = await run_sync_transaction(self._get_state_sync, project_name, project, episode, supported_durations)
        state["soft_violations"] = []
        content = state["content"]
        if script_review.script_plan_kind(project) == "reference_video" and isinstance(content, dict):
            try:
                caps = await fetch_reference_split_caps(project, config_resolver=self.config_resolver)
            except VideoRequestFactsError:
                return state
            units = content.get("units") or []
            texts = [str(unit.get("text") or "") if isinstance(unit, dict) else "" for unit in units]
            unit_ids = [
                unit_id for unit in units if isinstance(unit, dict) and isinstance(unit_id := unit.get("unit_id"), str)
            ]
            state["soft_violations"] = soft_violation_entries(
                reference_soft_violations(
                    texts,
                    project,
                    episode=episode,
                    voice=caps.voice,
                    # Formal script_plan units carry their persisted IDs; locate notices with those IDs.
                    unit_ids=unit_ids if len(unit_ids) == len(units) else None,
                )
            )
        return state

    def _get_state_sync(
        self,
        project_name: str,
        project: dict[str, Any],
        episode: int,
        supported_durations: list[int] | None,
    ) -> dict[str, Any]:
        """Synchronous ``get_state`` body: episode self-healing, read-time migration, and state/fingerprint derivation in one thread.

        ``get_state`` preloads ``project`` and ``supported_durations``. Migration does not alter video-model
        configuration, so tiers describe the same facts before/after migration and need not be recomputed under lock."""
        project_path = self.pm.get_project_path(project_name)
        path = script_review.script_plan_path(project_path, project, episode)
        if path is not None:
            # Require episode registration only when the gate applies (drama / narration, not reference_video);
            # not_applicable (ad / reference_video) is independent of episode existence and returns unchanged.
            project = self._require_episode(project_name, project, episode)
        content: dict[str, Any] | None = None
        fingerprint: str | None = None
        if path is not None:
            # Migration may persist script_plan and confirmation records. Derive fingerprints/state after migration under the same lock,
            # sharing one persisted content basis; outside-lock reads could describe different content after concurrent save_content.
            prompt_authoring_lock = (
                self.pm.file_lock(quarantine_path(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING))
                if script_review.script_plan_kind(project) == "reference_video"
                else nullcontext()
            )
            with prompt_authoring_lock, self.pm.file_lock(path):
                content, project = self._read_script_plan_migrated(
                    project_name, project, episode, path, project_path, supported_durations
                )
                fingerprint = script_review.content_fingerprint(path)
                status = script_review.review_status(project_path, project, episode)
        else:
            status = script_review.review_status(project_path, project, episode)
        return {
            "episode": episode,
            "content_mode": project.get("content_mode"),
            "status": status,
            "fingerprint": fingerprint,
            "confirmed_at": script_review.stored_review(project, episode).get("confirmed_at"),
            "content": content,
            "supported_durations": supported_durations,
            # Project-level target episode duration in seconds, None when unset. Review panels compare episode total / target;
            # return with state rather than requiring another project request, so total and target come from one read.
            "episode_target_duration": project_episode_target_duration(project),
            # Formal script overwritten on confirmation (None if absent): confirmation pages render danger and consequences from this.
            "script_overwrite": _overwrite_dict(project_path, project, episode) if path is not None else None,
        }

    async def get_quarantine_info(self, project_name: str, episode: int) -> dict[str, Any] | None:
        """Presentation view of this episode's script_plan draft for confirmation-page display/manual repair; None if inapplicable or absent.

        Fields: ``doc_type``, ``revision`` (concurrency token for manual save/discard, None for malformed
        envelopes), ``editable_by`` (``user`` for repair-needed drafts, ``agent`` for Agent-editable drafts),
        ``content`` (verbatim draft body for creator edits; None for Agent-editable drafts, whose content is
        hidden), ``violations``, ``soft_violations`` (per-entry fallback notices), and ``formal_exists``
        (whether a formal script plan remains after discard).

        Recompute violations fully on read using production-time validators (``revalidate_script_plan_draft``
        dispatches by kind to the variant revalidator, the same code as promotion). Do not trust the previous
        ``violations`` snapshot: source/model configuration may change while drafts exist, and reports must
        reflect current values. If damaged meta prevents recalculation, report inability itself as a violation
        instead of reverting to old snapshots. Agent-editable drafts belong to the Agent and are not revalidated.

        This method and ``get_state`` read independently; routers combine results in one request. Offload
        synchronous project/draft reads with ``asyncio.to_thread`` to avoid occupying the event loop for large sources."""
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        quarantine_kind = script_review.script_plan_quarantine_kind(project)
        if quarantine_kind is None:
            return None

        def _locate() -> tuple[Path, Path | None, bool]:
            project_path = self.pm.get_project_path(project_name)
            quarantine_path = script_review.script_plan_quarantine_path(project_path, project, episode)
            if quarantine_path is None or not quarantine_path.exists():
                return project_path, None, False
            formal_path = script_review.script_plan_path(project_path, project, episode)
            return project_path, quarantine_path, formal_path is not None and formal_path.exists()

        project_path, quarantine_path, formal_exists = await asyncio.to_thread(_locate)
        if quarantine_path is None:
            return None
        view: dict[str, Any] = {
            "doc_type": QUARANTINE_KIND_TO_DOC_TYPE[quarantine_kind],
            "revision": None,
            "editable_by": DRAFT_OWNER_USER,
            "content": None,
            "violations": [],
            "soft_violations": [],
            "formal_exists": formal_exists,
        }
        draft = await asyncio.to_thread(read_quarantine, project_path, episode, quarantine_kind)
        if draft is None:
            if not await asyncio.to_thread(quarantine_path.exists):
                # Between existence check and read, Agent promotion/resplitting removed the file (formal content written, pending draft cleared).
                # This is not envelope corruption: the read crossed the clearing instant. Treat as no draft
                # rather than falsely reporting corruption.
                return None
            # File exists but envelope is malformed (invalid JSON / nonobject top level / nonobject content). `read_quarantine`
            # returns None as no draft under its own reading contract (confirm existence checks still block),
            # but presentation must not accept this blindly or panels look clean while the draft still blocks confirmation.
            view["violations"] = [_unreadable_violation("草稿文件已损坏或格式不符，无法解析")]
            return view
        view["revision"] = draft_revision(draft)
        view["editable_by"] = draft_owner(draft)
        if view["editable_by"] == DRAFT_OWNER_AGENT:
            return view
        view["content"] = draft.content
        # Lazy import avoids module-level cycles: draft_workflow reuses this service's locked-write capability.
        from server.draft_workflow import revalidate_script_plan_draft

        try:
            revalidation = await revalidate_script_plan_draft(
                project_path,
                project,
                episode,
                draft,
                config_resolver=self.config_resolver,
            )
        except VideoRequestFactsError as exc:
            # Unresolved video request facts are configuration problems, not corrupted drafts. Report one unlocated violation
            # with fact problem codes/parameters; routers compose message from the corresponding wording key and parameters.
            view["violations"] = [
                {
                    "code": exc.code,
                    "label": "",
                    "message": "",
                    "line": None,
                    "params": exc.params,
                }
            ]
            return view
        except ValueError as exc:
            # Damaged drafts, e.g. missing meta.source: report recalculation failure itself as an episode-level violation. Exception text contains
            # draft.path; log only, never return to callers, because user-facing messages must not contain internal paths.
            logger.warning("草稿重算失败 project=%s episode=%s：%s", project_name, episode, exc)
            view["violations"] = [_unreadable_violation("草稿的产出上下文缺失或损坏，无法重新校验")]
            return view
        view["violations"] = violation_entries(revalidation.violations)
        view["soft_violations"] = soft_violation_entries(list(revalidation.soft_violations))
        return view

    async def get_reference_duration_tiers(
        self, project_name: str, episode: int, units: Sequence[dict[str, Any]] = ()
    ) -> dict | None:
        """Return effective reference-route tiers with reference images and per-unit bucket conclusions.

        ``units`` are this episode's script-plan units; the ``units`` key maps ``unit_id`` to the bucket selected
        by available reference images, its tiers, and splits in declared references, for panel tier selection.
        Return None for non-reference routes or unresolved r2v request facts, leaving presentation read-only.
        Offload project-file reads to a thread to avoid blocking the request loop."""
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        if script_review.script_plan_kind(project) != "reference_video":
            return None
        request_facts = reference_request_facts_lookup(project, self.config_resolver)
        with_ref_facts = await request_facts("r2v")
        if isinstance(with_ref_facts, VideoRequestFactsFailure):
            return None
        project_path = await asyncio.to_thread(self.pm.get_project_path, project_name)
        unit_capabilities = await reference_unit_capabilities(
            project,
            project_path,
            units,
            request_facts=request_facts,
        )
        return {
            "with_references": list(with_ref_facts.allowed_durations),
            "units": unit_capabilities,
        }

    async def save_content(
        self, project_name: str, episode: int, content: object, base_fingerprint: str | None = None
    ) -> dict[str, Any]:
        """Validate/persist edited structured intermediate content (manual or Agent writeback), returning latest state awaiting confirmation again.

        Content changes shift fingerprints, so ``get_state`` returns to pending_review automatically: saving
        requires renewed confirmation. Confirmed plans are read-only; saving raises ``script_plan_confirmed``
        without writing. Rerunning script planning with new content permits saving again. Formal plans are
        also read-only while Agent-editable drafts exist; saving raises ``draft_agent_owned``.

        ``base_fingerprint`` is the editor's fingerprint from reading content (``get_state``). If supplied,
        compare under lock against disk; mismatch (another writer changed script_plan during editing) raises
        ``conflict`` without persistence. The later writer receives 409 to refresh/merge instead of silently
        overwriting earlier content. ``None`` skips comparison (direct calls without a baseline).

        Persistence is fully synchronous (no duration migration or tier table), offloaded as one block. The
        subsequent ``get_state`` resolves its own tiers, so save responses share the initial GET-loading source."""
        await run_sync_transaction(self._save_content_sync, project_name, episode, content, base_fingerprint)
        return await self.get_state(project_name, episode)

    def _save_content_sync(
        self, project_name: str, episode: int, content: object, base_fingerprint: str | None
    ) -> None:
        """Synchronous ``save_content`` body: structural validation, baseline comparison, persistence."""
        project = self.pm.load_project(project_name)
        project_path = self.pm.get_project_path(project_name)
        path = script_review.script_plan_path(project_path, project, episode)
        if path is None:
            raise ScriptReviewError("not_applicable")
        project = self._require_episode(project_name, project, episode)
        kind, model = self._resolve_script_plan_model(project, episode)
        try:
            validated = model.model_validate(content).model_dump()
        except ValidationError as exc:
            _require_changed_speech_admitted(kind, _read_json(path), content)
            raise ScriptReviewError("invalid_content", str(exc)) from exc
        if kind == "drama":
            for scene in validated["scenes"]:
                if scene.get("needs_replan") is not True:
                    scene.pop("needs_replan", None)
        if not validated.get(NEW_ASSETS_FIELD):
            validated.pop(NEW_ASSETS_FIELD, None)
        # Input None means caller has no baseline, skip comparison. Comparison-semantic None instead means the file was absent at baseline read;
        # convert once here, sharing expected across all three variants.
        expected = base_fingerprint if base_fingerprint is not None else script_review.UNCHECKED_FINGERPRINT
        try:
            if kind == "reference_video":
                # Single write exit: write_script_plan_locked centralizes locks, baseline checks, and prompt_authoring draft cleanup.
                prompt_authoring_path = quarantine_path(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
                with (
                    self.pm.file_lock(prompt_authoring_path),
                    script_review.script_plan_write_lock(project_path, episode),
                ):
                    self._reject_readonly_script_plan(project_name, project_path, episode)
                    _require_changed_speech_admitted(kind, _read_json(path), validated)
                    script_review.write_script_plan_locked(
                        project_path, episode, validated, expected_fingerprint=expected
                    )
            else:
                # Share the per-path lock with _read_script_plan_migrated, mutually excluding save and migration read-modify-write.
                # Baseline comparison uses assert_base_fingerprint as rv does, without divergent conflict criteria. Compare before
                # dialogue admission so stale saves receive conflict instead of unfixable admission advice.
                # Already compared here, so the persistence exit does not repeat it (default UNCHECKED).
                with script_review.formal_script_plan_lock(project_path, episode, path):
                    self._reject_readonly_script_plan(project_name, project_path, episode)
                    script_review.assert_base_fingerprint(path, expected)
                    _require_changed_speech_admitted(kind, _read_json(path), validated)
                    script_review.write_formal_script_plan_locked(project_path, episode, path, validated)
        except script_review.ScriptPlanWriteConflict as exc:
            raise ScriptReviewError("conflict", str(exc)) from exc

    def _reject_readonly_script_plan(self, project_name: str, project_path: Path, episode: int) -> None:
        """Under the formal-plan lock, reject saving read-only plans using current confirmation records and Agent draft ownership."""
        project = self.pm.load_project(project_name)
        if script_review.formal_script_plan_agent_owned(project_path, project, episode):
            raise ScriptReviewError("draft_agent_owned")
        if script_review.formal_script_plan_confirmed(project_path, project, episode):
            raise ScriptReviewError("script_plan_confirmed")

    async def confirm(
        self, project_name: str, episode: int, *, overwrite_revision: str | None = None
    ) -> dict[str, Any]:
        """Confirm episode script_plan: convert the entire plan into a formal script and record its confirmation fingerprint in one write, admitting prompt_authoring.

        Raise ScriptReviewError for missing script_plan / inapplicability / missing episode entry / invalid
        content structure / pending drafts / failed conversion admission; routers map to 4xx. With an existing
        formal script, ``overwrite_revision`` must match the overwrite manifest's ``revision`` (the exact version
        whose overwrite the caller approved). Missing/mismatched values raise ``overwrite_required`` with
        the current manifest, writing neither formal script nor confirmation record.

        Resolve tiers before locking (as in ``get_state``): confirmation also migrates existing drafts on read.
        Migration must use the same tier source as gate-panel presentation, or confirmation could persist
        seconds unavailable in the panel."""
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        supported_durations = await self._resolve_supported_durations(project)

        def _locate_overwrite() -> tuple[Path, script_review.FormalScriptOverwrite | None]:
            project_path = self.pm.get_project_path(project_name)
            return project_path, script_review.formal_script_overwrite(project_path, project, episode)

        project_path, overwrite = await asyncio.to_thread(_locate_overwrite)
        fingerprint = await run_sync_transaction(
            self._confirm_sync, project_name, project, episode, supported_durations
        )
        if overwrite is not None and script_review.fingerprint_of_revision(overwrite_revision) != overwrite.fingerprint:
            raise ScriptReviewError("overwrite_required", overwrite=overwrite.to_dict())
        await self._materialize(
            project_path,
            episode,
            plan_fingerprint=fingerprint,
            script_fingerprint=overwrite.fingerprint if overwrite is not None else None,
        )
        return await self.get_state(project_name, episode)

    async def _materialize(
        self, project_path: Path, episode: int, *, plan_fingerprint: str, script_fingerprint: str | None
    ) -> None:
        """Persist whole-episode conversion with confirmation records; reject as conflict if the plan or formal script changes during conversion."""
        confirmed_at = datetime.now(UTC).isoformat()

        def _record_confirmation(project: dict[str, Any]) -> None:
            if not script_review.apply_confirmation(project, episode, plan_fingerprint, confirmed_at):
                raise ScriptReviewError("episode_not_found")

        generator = await asyncio.to_thread(ScriptGenerator, project_path, config_resolver=self.config_resolver)
        try:
            await generator.materialize_script_plan(
                episode,
                expected_plan_revision=plan_fingerprint,
                expected_script_fingerprint=script_fingerprint,
                project_update=_record_confirmation,
            )
        except SpeechAdmissionError as exc:
            raise ScriptReviewError("speech_admission", admission=exc.admission) from exc
        except (ScriptWriteConflict, script_review.ScriptPlanWriteConflict) as exc:
            raise ScriptReviewError("conversion_conflict", str(exc)) from exc
        except VideoRequestFactsError as exc:
            # Unresolved video request facts (unconfigured model, missing/invalid tiers, empty narrowed set): return the fact problem code
            # and parameters to users, matching preflight and execution.
            raise ScriptReviewError("video_request_facts", exc.code, problem=exc.failure) from exc
        except script_review.ForeignFormalScriptError as exc:
            # Before the ValueError branch below: if binding is lost and another episode's script occupies the canonical path, reject before writing,
            # neither rebuilding that episode's script nor changing this episode's binding. Point guidance at the actionable location.
            raise ScriptReviewError("foreign_formal_script", str(exc), script_filename=exc.filename) from exc
        except NewAssetsError as exc:
            raise ScriptReviewError("invalid_new_assets", str(exc)) from exc
        except UnregisteredReferencesError as exc:
            raise ScriptReviewError("unregistered_references", str(exc)) from exc
        except UnitDurationOffTier as exc:
            # Before the ValueError branch below: when confirmed duration is excluded by narrowing, report current duration and selectable tiers by script-plan unit position.
            params = {
                "position": exc.position,
                "duration_seconds": exc.duration_seconds,
                "tiers": " / ".join(str(tier) for tier in exc.tiers),
            }
            raise ScriptReviewError("duration_off_tier", str(exc), params=params) from exc
        except (ValueError, FileNotFoundError) as exc:
            raise ScriptReviewError("conversion_refused", str(exc)) from exc

    def _confirm_sync(
        self,
        project_name: str,
        project: dict[str, Any],
        episode: int,
        supported_durations: list[int] | None,
    ) -> str:
        """Synchronous first half of ``confirm``: pending-draft checks, read-time migration, structural validation; return the content fingerprint for confirmation."""
        project_path = self.pm.get_project_path(project_name)
        path = script_review.script_plan_path(project_path, project, episode)
        if path is None:
            raise ScriptReviewError("not_applicable")
        project = self._require_episode(project_name, project, episode)
        # Reject confirmation while drafts exist: formal script_plan is still the previous version (or absent), so confirming would approve
        # content the user has not seen while newly produced violating content awaits Agent handling in a draft. Validation follows
        # generation's rules: promotion uses the production-time validators; here only check pending-draft presence.
        quarantine = script_review.script_plan_quarantine_path(project_path, project, episode)
        if quarantine is not None and quarantine.exists():
            raise ScriptReviewError("quarantined", f"script_plan 草稿待处置: {quarantine}")
        # Migrate existing draft durations before validation: Agent/direct calls may confirm without get_state. One per-path
        # lock covers the whole read-modify-write (including the reference_video branch's write below), preventing intervening
        # save_content or migration writes; _read_script_plan_migrated requires the caller to hold the lock.
        kind, model = self._resolve_script_plan_model(project, episode)
        prompt_authoring_lock = (
            self.pm.file_lock(quarantine_path(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING))
            if kind == "reference_video"
            else nullcontext()
        )
        with prompt_authoring_lock, self.pm.file_lock(path):
            content, project = self._read_script_plan_migrated(
                project_name, project, episode, path, project_path, supported_durations
            )
            if content is None and not path.exists():
                raise ScriptReviewError("no_script_plan")
            # Validate script_plan structure with its variant model before confirming. None content (invalid JSON / nonobject) is also
            # rejected here by model_validate as invalid_content; file presence alone cannot admit it.
            try:
                validated = model.model_validate(content)
            except ValidationError as exc:
                raise ScriptReviewError("invalid_content", str(exc)) from exc
            try:
                resolve_new_assets(project, validated.model_dump().get(NEW_ASSETS_FIELD))
            except NewAssetsError as exc:
                raise ScriptReviewError("invalid_new_assets", str(exc)) from exc

            marked_shape = {
                "drama": ("scenes", "scenes"),
                "reference_video": ("units", "video_units"),
            }.get(kind)
            if marked_shape is not None:
                root, skeleton = marked_shape
                for unit in validated.model_dump()[root]:
                    if unit.get("needs_replan") is not True:
                        continue
                    admission = admit_script_unit(skeleton, unit)
                    raise ScriptReviewError("speech_admission", admission=admission)

            if kind == "reference_video":
                # Fingerprint the object just persisted directly, keeping confirmation records consistent with actual written content.
                dumped = validated.model_dump()
                for unit in dumped["units"]:
                    admission = admit_script_unit("video_units", unit)
                    if not admission.allowed:
                        raise ScriptReviewError("speech_admission", admission=admission)
                # Single persistence exit (already holding the same per-path lock; do not wrap script_plan_write_lock again).
                # No concurrency window exists in this critical-section read-modify-write, so skip baseline comparison. Real content changes
                # invalidate prompt_authoring draft bases, and the exit cleans them up accordingly.
                script_review.write_script_plan_locked(project_path, episode, dumped)
                fingerprint = script_review.content_fingerprint_of_data(dumped)
            else:
                # Derive fingerprints from validated content without rereading disk: confirmation records must match content validated here.
                fingerprint = script_review.content_fingerprint_of_data(content)
        return fingerprint


def _overwrite_dict(project_path: Path, project: Mapping[str, Any], episode: int) -> dict[str, Any] | None:
    overwrite = script_review.formal_script_overwrite(project_path, project, episode)
    return overwrite.to_dict() if overwrite is not None else None


def _unreadable_violation(message: str) -> dict[str, Any]:
    """Episode-level violation when draft recalculation is impossible; routers localize using the ``quarantine_unreadable`` message key."""
    return {"code": "quarantine_unreadable", "label": "", "message": message, "line": None}


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read/parse a structured script_plan file; return None for missing / invalid JSON / nonobjects (fingerprint-derived state handles fallback separately).

    Reuse ``lib.infra.json_io.load_json_or_none`` for tolerant reading (OSError / JSON / encoding errors →
    None), consistently with other project JSON reads. Add a top-level-object guard here, returning None for nonobjects too."""
    data = load_json_or_none(path)
    return data if isinstance(data, dict) else None
