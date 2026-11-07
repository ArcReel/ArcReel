r"""v14→v15: formal scripts become sole episode content truth; retire plan-item fingerprints/incremental merging (ADR 0080).

Adopt existing projects into the new mechanism:

- **Episode bindings**: ``script_file`` must equal canonical ``scripts/episode_N.json`` verbatim.
  :mod:`lib.project.project_migrations.legacy_shapes` already normalizes old noncanonical bindings/
  duplicate episode IDs. Reject remaining cases before writes with episode/binding verdicts. Missing bindings use canonical paths.
- **Fingerprint fields**: remove item ``script_plan_entry_revision`` and metadata ``script_plan_revision``.
- **Confirmed episodes without formal scripts**: convert whole confirmed plans into formal scripts,
  all items pending authoring, sharing ``lib.script.script_document.build_materialized_script`` with
  confirmation. Unprojectable/invalid episodes remain unconverted and enter reports.
- **Existing formal scripts**: mark segments/scenes/shots pending when both visual sides are empty;
  backfill drama ``scene_description`` and reference-unit ``source_text`` from plans, leaving absent source empty.
- **Grandfathered episodes** with formal scripts/plans but no confirmation: record current plan fingerprints
  as confirmation baselines so reruns require review. Exclude ledger-stale episodes, whose first
  postreplanning confirmation must establish baselines.
- **Script registrations**: v3 script basis excludes plans. Rebase previously current registrations
  (v2 current-plan digests or plan-free digests) to v3; preserve already-stale entries. Register newly
  converted and previously unregistered present scripts under v3.

Ad has no plans, so only advance schema. Commit scripts → manifests → ``project.json``; interruption
reruns skip already-target scripts/manifests, with the schema version in ``project.json`` committed last."""

from __future__ import annotations

import contextlib
import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_planner import ArtifactTargetStatePlan, TargetStatePlanner
from lib.artifacts.artifact_provenance import SCRIPT_PLAN_BASIS_INPUT_KEY, project_episode_script_prompt_inputs
from lib.artifacts.formal_write import project_metadata_lock
from lib.episode.episode_paths import episode_script_relpath
from lib.infra.json_io import atomic_write_json
from lib.infra.path_safety import try_safe_join
from lib.project.project_manager import is_episode_number
from lib.project.project_migration_failure import ProjectMigrationError
from lib.project.project_migration_report import ArtifactBackfillOutcome, MigrationSkippedArtifact
from lib.project.project_migrations.backups import ensure_versioned_backup
from lib.project.project_schema import parse_project_schema_version
from lib.script.script_document import build_materialized_script
from lib.script.script_models import PENDING_AUTHORING_FIELD
from lib.script.script_plan_entries import ScriptPlanKind, entry_id_field, plan_entries_from_document, plan_variant
from lib.script.script_review import (
    REVIEW_FIELD,
    content_fingerprint,
    script_plan_kind,
    script_plan_path,
    stored_review,
)
from lib.script.script_skeleton import SKELETONS, rewrite_episode_prefix
from lib.script.script_structure_validator import validate_script_structure

TARGET_SCHEMA_VERSION = 15

#: Two retired script fingerprint fields, historical facts frozen in this step.
_ENTRY_REVISION_FIELD = "script_plan_entry_revision"
_SCRIPT_REVISION_FIELD = "script_plan_revision"

#: Storyboard visual fields; reference-unit visuals are bodies, not empty-driven pending markers.
_STORYBOARD_KINDS = ("segments", "scenes", "shots")
_VISUAL_FIELDS = ("image_prompt", "video_prompt")

#: Historical script bases: v2 inputs plans, plan-free versions include episode numbers only.
_LEGACY_SCRIPT_BASIS_KIND = "structured-content/episode-script"
_LEGACY_PLANLESS_SCRIPT_BASIS_KIND = "structured-content/episode-script-without-plan"


@dataclass
class _EpisodeWork:
    episode: int
    #: On-disk project-relative script paths, always canonical for episodes.
    script_rel: str
    #: Rewritten scripts; None means no rewrite needed.
    script: dict[str, Any] | None = None
    #: Newly materialized formal script for this episode.
    materialized: bool = False


@dataclass
class _Plan:
    project: dict[str, Any]
    works: list[_EpisodeWork] = field(default_factory=list)
    #: Episode → script path for manifest rebase plus premigration plan content (None without plans).
    registrations: dict[int, tuple[str, object | None]] = field(default_factory=dict)
    skipped: list[MigrationSkippedArtifact] = field(default_factory=list)


def _visual_empty(value: object) -> bool:
    return not (value.strip() if isinstance(value, str) else value)


def _load_object(path: Path) -> dict[str, Any] | None:
    r"""Read JSON objects; None for missing/corrupt/nonobjects, without repair here."""

    try:
        parsed = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_canonical_binding(raw_binding: str, episode: int) -> bool:
    r"""Whether bindings equal episode canonical ``scripts/episode_N.json`` verbatim.

    No normalization: reject bare ``episode_1.json``, ``./`` prefixes, backslash
    ``scripts\episode_1.json``, nested ``scripts/archive/custom.json``, or wrong numbering
    ``scripts/episode_01.json``. All backend writers use canonical paths, mandatory since v15;
    this step is stricter than target-state planning."""

    return raw_binding == episode_script_relpath(episode)


def _strip_revisions(script: dict[str, Any]) -> None:
    metadata = script.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop(_SCRIPT_REVISION_FIELD, None)
    for skeleton in SKELETONS:
        items = script.get(skeleton)
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                item.pop(_ENTRY_REVISION_FIELD, None)


def _plan_items_by_id(kind: ScriptPlanKind, plan_document: object, episode: int) -> dict[str, dict[str, object]]:
    entries = plan_entries_from_document(kind, plan_document)
    id_field = entry_id_field(kind)
    by_id: dict[str, dict[str, object]] = {}
    for entry in entries:
        entry_id = rewrite_episode_prefix(entry.get(id_field), episode)
        if isinstance(entry_id, str) and entry_id and entry_id not in by_id:
            by_id[entry_id] = entry
    return by_id


def _fill_from_plan(
    kind: ScriptPlanKind | None, script: dict[str, Any], plan_document: object | None, episode: int
) -> None:
    r"""For existing scripts, mark empty visuals and backfill drama visual foundations/reference-unit source text."""

    if kind is None:
        return
    variant = plan_variant(kind)
    items = script.get(variant.skeleton_kind)
    if not isinstance(items, list):
        return
    id_field = entry_id_field(kind)
    plan_items = _plan_items_by_id(kind, plan_document, episode) if plan_document is not None else {}
    for item in items:
        if not isinstance(item, dict):
            continue
        plan_item = plan_items.get(str(item.get(id_field)))
        if variant.skeleton_kind in _STORYBOARD_KINDS and all(_visual_empty(item.get(f)) for f in _VISUAL_FIELDS):
            item[PENDING_AUTHORING_FIELD] = True
        if kind == "drama" and plan_item is not None and not item.get("scene_description"):
            description = plan_item.get("scene_description")
            if isinstance(description, str) and description.strip():
                item["scene_description"] = description
        if kind == "reference_video" and not item.get("source_text"):
            source_text = plan_item.get("source_text") if plan_item is not None else None
            item["source_text"] = source_text if isinstance(source_text, str) else ""


def _materialize(
    project: dict[str, Any], kind: ScriptPlanKind, plan_document: object, episode: int
) -> tuple[dict[str, Any] | None, str | None]:
    r"""Project whole formal scripts from confirmed plans; (None, reason) when impossible."""

    entries = plan_entries_from_document(kind, plan_document)
    if not entries:
        return None, "confirmed script_plan has no readable entries"
    id_field = entry_id_field(kind)
    ids = [rewrite_episode_prefix(entry.get(id_field), episode) for entry in entries]
    if any(not isinstance(entry_id, str) or not entry_id for entry_id in ids) or len(set(ids)) != len(ids):
        return None, "confirmed script_plan entry ids are missing or duplicated"
    raw_title = plan_document.get("title") if isinstance(plan_document, Mapping) else None
    title = raw_title if kind == "drama" and isinstance(raw_title, str) and raw_title.strip() else None
    script = build_materialized_script(
        project,
        episode,
        plan_kind=kind,
        plan_entries=entries,
        title=title,
    )
    if not validate_script_structure(script).valid:
        return None, "materialized script from the confirmed script_plan fails structure validation"
    # Match ``ProjectManager.save_script`` default status filling.
    script["metadata"].setdefault("status", "draft")
    _stamp_confirmation_time(script["metadata"], stored_review(project, episode).get("confirmed_at"))
    return script, None


def _stamp_confirmation_time(metadata: dict[str, Any], confirmed_at: object) -> None:
    r"""Use confirmation time for materialized script creation/update timestamps.

    Projection initially stamps migration time; ``updated_at`` feeds project activity and would push
    projects to listing tops. Remove both timestamps if confirmation time is unreadable, falling back to other activity sources."""
    try:
        valid = isinstance(confirmed_at, str) and datetime.fromisoformat(confirmed_at).tzinfo is not None
    except ValueError:
        valid = False
    if valid:
        metadata["created_at"] = confirmed_at
        metadata["updated_at"] = confirmed_at
    else:
        metadata.pop("created_at", None)
        metadata.pop("updated_at", None)


def _preflight(project_dir: Path, project: dict[str, Any]) -> _Plan:
    r"""Read-only computation of all script rewrites/conversions and project.json changes, without persistence."""

    migrated = copy.deepcopy(project)
    plan = _Plan(project=migrated)
    kind = script_plan_kind(migrated)
    raw_episodes = migrated.get("episodes")
    episodes: list[Any] = raw_episodes if isinstance(raw_episodes, list) else []
    seen_episodes: set[int] = set()
    for entry in episodes:
        if not isinstance(entry, dict):
            continue
        entry_episode = entry.get("episode")
        if not is_episode_number(entry_episode):
            continue
        if entry_episode in seen_episodes:
            # Duplicate IDs make ledger ownership ambiguous. Earlier normalization already deduplicates;
            # reject remaining cases during read-only preflight, recording IDs/bindings.
            raise ProjectMigrationError(
                "ledger has more than one entry for this episode",
                episode=entry_episode,
                file=entry.get("script_file") if isinstance(entry.get("script_file"), str) else "project.json",
            )
        seen_episodes.add(entry_episode)
        raw_binding = entry.get("script_file")
        if isinstance(raw_binding, str) and raw_binding and not _is_canonical_binding(raw_binding, entry_episode):
            # Earlier normalization canonicalized bindings; reject remaining noncanonical ones with episode/binding verdicts.
            raise ProjectMigrationError(
                "script binding is not this episode's canonical script", episode=entry_episode, file=raw_binding
            )
    confirmed_at = datetime.now(UTC).isoformat()
    for entry in episodes:
        if not isinstance(entry, dict):
            continue
        episode = entry.get("episode")
        if not is_episode_number(episode):
            continue
        canonical = episode_script_relpath(episode)
        binding_path = try_safe_join(project_dir, canonical)
        plan_path = script_plan_path(project_dir, migrated, episode) if kind is not None else None
        plan_document = _load_object(plan_path) if plan_path is not None else None
        # Nonobject plans have no convertible/backfillable items and provide no confirmation baseline.
        plan_fingerprint = (
            content_fingerprint(plan_path) if plan_path is not None and plan_document is not None else None
        )

        if binding_path is not None and binding_path.is_file():
            script = _load_object(binding_path)
            if script is None:
                continue
            original = copy.deepcopy(script)
            _strip_revisions(script)
            if migrated.get("content_mode") != "ad":
                _fill_from_plan(kind, script, plan_document, episode)
            work = _EpisodeWork(episode=episode, script_rel=canonical, script=script if script != original else None)
            plan.works.append(work)
            plan.registrations[episode] = (canonical, plan_document)
            if (
                plan_fingerprint is not None
                and entry.get("ledger_status") != "stale"
                and stored_review(migrated, episode).get("fingerprint") is None
            ):
                entry[REVIEW_FIELD] = {"fingerprint": plan_fingerprint, "confirmed_at": confirmed_at}
            continue

        if kind is None or plan_document is None or plan_fingerprint is None:
            continue
        if stored_review(migrated, episode).get("fingerprint") != plan_fingerprint:
            continue
        script, reason = _materialize(migrated, kind, plan_document, episode)
        if script is None:
            _skip_script(plan, episode, canonical, reason or "confirmed script_plan could not be materialized")
            continue
        entry["title"] = script.get("title", "")
        entry["script_file"] = canonical
        plan.works.append(_EpisodeWork(episode=episode, script_rel=canonical, script=script, materialized=True))
        plan.registrations[episode] = (canonical, plan_document)
    return plan


def _skip_script(plan: _Plan, episode: int, artifact_path: str, reason: str) -> None:
    r"""Record confirmed episodes unable to materialize in migration reports."""

    plan.skipped.append(
        MigrationSkippedArtifact(
            kind=ArtifactKey.episode_script(episode).kind.value,
            episode=episode,
            resource_id=str(episode),
            artifact_path=artifact_path,
            reason=reason,
        )
    )


def _legacy_script_bases(project: Mapping[str, Any], episode: int, plan_document: object | None) -> list[str]:
    r"""Two possible current premigration script digests: v2 from current plans, or plan-free basis."""

    digests = [
        ArtifactBasis.build(
            _LEGACY_PLANLESS_SCRIPT_BASIS_KIND,
            kind_version=1,
            inputs={"episode": episode},
        ).digest
    ]
    if plan_document is not None:
        with contextlib.suppress(TypeError, ValueError):
            digests.append(
                ArtifactBasis.build(
                    _LEGACY_SCRIPT_BASIS_KIND,
                    kind_version=2,
                    inputs={
                        "content_mode": project.get("content_mode"),
                        "generation_mode": project.get("generation_mode"),
                        SCRIPT_PLAN_BASIS_INPUT_KEY: plan_document,
                        "prompt_context": project_episode_script_prompt_inputs(project),
                    },
                ).digest
            )
    return digests


def _rewrite_manifest_entries(
    project_dir: Path,
    before_project: Mapping[str, Any],
    target: ArtifactTargetStatePlan,
    plan: _Plan,
) -> int:
    r"""Rebase script registrations to v3.

    Rewrite previously current/unregistered entries, preserving already-stale registrations. Use complete
    target-state planning without current-schema validation, supporting later chain versions. Return rewrite count."""

    adapter = ProjectArtifactManifestAdapter(project_dir)
    snapshot = adapter.snapshot_entries()
    expected: dict[ArtifactKey, ArtifactManifestEntry | None] = {}
    replacements: dict[ArtifactKey, ArtifactManifestEntry | None] = {}
    rebase_scripts = before_project.get("content_mode") != "ad"
    for episode, (_script_rel, plan_document) in sorted(plan.registrations.items()) if rebase_scripts else []:
        key = ArtifactKey.episode_script(episode)
        entry = target.entries.get(key)
        if entry is None:
            continue
        stored = snapshot.get(key)
        if stored == entry:
            continue
        if stored is not None and (
            stored.artifact_path != entry.artifact_path
            or stored.basis_digest not in _legacy_script_bases(before_project, episode, plan_document)
        ):
            continue
        expected[key] = stored
        replacements[key] = entry
    if replacements:
        ensure_versioned_backup(project_dir / MANIFEST_FILENAME, TARGET_SCHEMA_VERSION - 1)
        if not adapter.replace_entries_if_matches_atomically(expected=expected, replacements=replacements):
            raise RuntimeError("artifact manifest changed while rewriting episode script entries")
    return len(replacements)


def migrate_v14_to_v15(project_dir: Path) -> ArtifactBackfillOutcome | None:
    r"""File-level v14→v15 migration."""

    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return None
    project_bytes = project_file.read_bytes()
    project = json.loads(project_bytes)
    if not isinstance(project, dict):
        raise ValueError("project.json 必须是对象")
    if parse_project_schema_version(project) >= TARGET_SCHEMA_VERSION:
        return None

    plan = _preflight(project_dir, project)
    plan.project["schema_version"] = TARGET_SCHEMA_VERSION

    ensure_versioned_backup(project_file, TARGET_SCHEMA_VERSION - 1)
    for work in plan.works:
        if work.script is not None and not work.materialized:
            ensure_versioned_backup(project_dir / work.script_rel, TARGET_SCHEMA_VERSION - 1)
    for work in plan.works:
        if work.script is not None:
            path = try_safe_join(project_dir, work.script_rel)
            if path is None:
                raise ValueError(f"第 {work.episode} 集剧本路径越界: {work.script_rel}")
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, work.script)

    after_bytes = json.dumps(plan.project, ensure_ascii=False).encode("utf-8")
    reported = bool(plan.skipped)
    if not plan.registrations or project.get("content_mode") == "ad":
        outcome = _outcome(project_dir, _target_plan(project_dir, after_bytes), plan) if reported else None
        atomic_write_json(project_file, plan.project)
        return outcome
    with project_metadata_lock(project_dir):
        target = _target_plan(project_dir, after_bytes)
        rewritten = _rewrite_manifest_entries(project_dir, project, target, plan)
        outcome = _outcome(project_dir, target, plan) if rewritten or reported else None
        atomic_write_json(project_file, plan.project)
    return outcome


def _target_plan(project_dir: Path, after_bytes: bytes) -> ArtifactTargetStatePlan:
    return TargetStatePlanner(project_dir, project_bytes=after_bytes).plan()


def _outcome(project_dir: Path, target: ArtifactTargetStatePlan, plan: _Plan) -> ArtifactBackfillOutcome:
    r"""Migration report: runners retain only the last full manifest view, so merge complete target-planning skips."""

    return ArtifactBackfillOutcome.from_entries(
        ProjectArtifactManifestAdapter(project_dir).snapshot_entries(),
        plan.skipped,
        target.skipped,
    )


__all__ = ["TARGET_SCHEMA_VERSION", "migrate_v14_to_v15"]
