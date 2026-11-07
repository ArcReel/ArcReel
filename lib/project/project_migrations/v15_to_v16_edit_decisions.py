"""v15→v16 migration: move editing decisions out of script entries.

This step consists of independent substeps called in order by ``migrate_v15_to_v16``; changes folded
into this step within the same release add a substep.

**Move transitions to the editing timeline (ADR 0090)**

- Remove ``transition_to_next`` from all entries in bound scripts; discard non-hard-cut values without
  migrating them anywhere.
- Current presentation-model bases no longer record transitions. Remove transitions from persisted
  presentation-model files (``presentations/``) and replace their bases with current ones. Rewrite
  manifest entries to the current basis only when they exactly match the old basis recorded in the file.
  The old basis includes script transitions, so entries outdated only because transitions differ from
  the file become current; entries outdated due to other inputs differ from the file record and remain
  unchanged. Subtitle bases never recorded transitions and remain unchanged. Presentation models whose
  files do not match their own recorded old bases were already unclaimed and are not changed here.

Commit order is script → manifest → presentation-model files → ``project.json``. A mid-step crash
reruns the whole step, skipping already-changed scripts and files because they have no transitions.
Manifest rewriting precedes files: on rerun, unchanged files still yield the old bases, while rewritten
entries no longer match and are skipped. Presentation-model files derive from selected media and can
be rematerialized on read; no separate backup is made.

**Make narration delivery a project setting (ADR 0089)**

- Projects with manifest-registered narration audio and at least one registered selected audio-version
  record carrying complete TTS settings are classified as TTS voiceover. ``project.json`` receives a TTS
  snapshot of the model, voice, and speed used by the record with the latest ``created_at``.
- Other projects are classified as post-production voiceover, preserving existing audio-backend, voice,
  and speed fields.
- Only ``project.json`` is written, with this step's final write; the runner handles backups. Old audio
  whose selected-version records lack TTS settings was already unregistered; classification does not
  change its treatment.

**Stop using narration duration as an input to video currency**

- Plan target states under both the old and new rules. Rewrite only video entries that were current
  before the change and whose targets changed; already-outdated entries remain. Plan the pre-change
  target state before any writes in this step, also serving as read-only preflight. Video bases exclude
  transitions, so planning before the transition substep does not affect the result.
- Selected versions additionally record the new currency-duration basis; actual paid tiers and execution
  request digests remain unchanged. Persist version records before the manifest, and the manifest before
  the project version. Reruns after interruption still recognize old entries and rewritten version records.

**Register uploaded storyboard images and videos by uploaded bytes (ADR 0062 revision)**

- For storyboard images and videos whose selected versions are manual uploads matching artifact bytes,
  register the post-change target basis determined only by uploaded bytes: backfill previously unregistered
  uploaded videos and uploaded storyboards whose entries were deleted; rewrite uploaded storyboards
  previously registered by generation inputs.
- Write only the manifest, before the project version; reruns skip entries already matching their targets.

**Separate episode IDs from airing order (ADR 0096)**

- Write into ``project.json`` the project high-water mark (``lib.episode.episode_ids``), taking the maximum
  episode ID in the ledger, names still carrying episode IDs in the project directory (scripts, draft
  directories, source snapshots, ``E{N}`` prefixes in media names, presentation and subtitle directories),
  the artifact manifest, and task/call-record query results injected by the runner. Existing ledger order
  is already ascending by episode ID and becomes airing order without reordering.
- The next-episode outline uses the immediately following episode in airing order, providing its title
  when planning data is absent (``episode_outline_context``). This feeds script-plan bases: as with video
  currency, plan before/after targets and rewrite only script-plan entries current before the change whose
  targets changed. Script plans have no version records; only the manifest changes.

**Explicitly register episode sources and whole-source files in the project (ADRs 0031, 0097)**

- Backfill episode-source origins in ledger entries: a source range means a cut episode. Without a source
  range but with ``source/episode_N.txt``, another source in ``source/`` means a cut episode under the old
  splitting workflow; only episode files means an episode with its own source. Neither means no source.
- Episodes with only ``source/episode_N.txt`` and no ledger entry are registered with their own source,
  using N as the episode ID and appended to airing order in ascending N order.
- Backfill the whole-source file list using the original candidate rules (directly under ``source/``,
  .txt / .md without dot / underscore prefixes, excluding episode files) and filename order. Derive the
  planning start from the ledger instead, removing ``planning_cursor``.
- Backfill normalized text snapshots for files with registered cut episodes. Do not backfill when file
  contents differ from the recorded source fingerprint; the planning entry point still blocks mismatches.
  Write snapshots before ``project.json``, overwriting them identically on rerun.

**Record source kinds with source files (ADR 0036)**

- For drama projects, backfill the original project-level ``source_kind`` (novel when absent or invalid)
  on every whole-source list entry and every episode with its own source; preserve valid existing records.
  Other content modes simply remove the project-level field.
- The project-level kind previously entered only drama storyboard-image-to-video script-plan bases.
  Pre-change targets use the backfilled kinds, so these bases remain unchanged. Reference-to-video script
  plans previously ignored kind and drafted as novels; those entries in screenplay projects read as
  outdated afterward. Frozen output provenance is not rewritten.

**Retire the asset inventory (ADR 0092)**

- From ``project.json``, remove ``workflow.asset_inventory``, removing ``workflow`` entirely if now empty.

**Align project-list completion with production status**

- Write into ``project.json`` the field ``source_remaining``: whether whole-source text remains unplanned according
  to the backfilled whole-source list and ledger. Commands writing the ledger and source registrations
  (``ProjectManager``) maintain it afterward. The project list uses it to determine completion without
  reading source text.

Except for uploaded-artifact backfills, this step only rewrites existing entries without adding or
removing them. Migration results use skipped items from the complete post-rewrite target state and
actual manifest counts. This does not resolve earlier skip reasons; the runner merges skipped items
from earlier chain steps or existing migration reports."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import assert_artifact_target_state_plan_unchanged
from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactKind,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifacts.artifact_planner import ArtifactTargetStatePlan, TargetStatePlanner
from lib.artifacts.artifact_version_provenance import (
    VIDEO_CURRENCY_DURATION_FIELD,
    parse_typed_audio_settings,
    parse_typed_media_version_target,
)
from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.version_manager import selected_manual_upload_snapshot
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.artifacts.visual_artifact_provenance import (
    build_uploaded_storyboard_basis,
    build_uploaded_video_basis,
    visual_file_digest,
)
from lib.episode.episode_ids import episode_ids_on_disk, raise_episode_id_high_water
from lib.episode.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    SOURCE_TEXT_SUFFIXES,
    discover_episode_files,
    has_downstream_products,
    is_derived_episode_name,
    normalize_source_text,
    parse_positive_episode_num,
    parse_source_range,
)
from lib.episode.episode_paths import episode_script_relpath
from lib.episode.episode_sources import (
    SOURCE_ORIGIN_FIELD,
    SOURCE_ORIGINS,
    WHOLE_SOURCE_FILES_KEY,
    SourceOrigin,
    cut_episode_source_files,
    episode_source_origin,
    record_source_remaining,
    sync_source_snapshots,
)
from lib.episode.source_kinds import DEFAULT_SOURCE_KIND, SOURCE_KIND_FIELD, is_source_kind, source_kind_applies
from lib.infra.json_io import atomic_write_json
from lib.infra.path_safety import try_safe_join
from lib.project.project_migration_report import ArtifactBackfillOutcome
from lib.project.project_migrations.backups import ensure_versioned_backup
from lib.project.project_schema import parse_project_schema_version
from lib.script.script_skeleton import SKELETONS
from lib.speech.narration_config import (
    NARRATION_DELIVERY_FIELD,
    POST_PRODUCTION,
    TTS_SPEED_FIELD,
    USE_TTS,
    TtsSynthesisSettings,
    tts_snapshot_fields,
)
from lib.speech.speech_artifact_provenance import (
    RenditionVariant,
    SelectedMediaEvidence,
    build_legacy_transition_presentation_basis,
    build_presentation_basis,
    build_video_duration_basis,
)
from lib.speech.speech_presentation import presentation_artifact_paths

TARGET_SCHEMA_VERSION = 16

#: Look up the highest episode ID in task/call records by project name (0 if absent). The database is outside project directories, injected by application assembly.
RecordedEpisodeIds = Callable[[str], int]

#: Retired script-entry transition field name. Historical fact fixed within this step.
_TRANSITION_FIELD = "transition_to_next"


def _load_object(path: Path) -> dict[str, Any] | None:
    """Read a JSON object; return None if missing, corrupt, or not an object (no repair in this step)."""

    try:
        parsed = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# Substep: decouple video currency from the narration lower bound
# ---------------------------------------------------------------------------


def _plan_before_rewrite(project_dir: Path, view: Mapping[str, Any]) -> ArtifactTargetStatePlan:
    """Pre-change target state, planned before any writes in this step as read-only preflight: corrupt inputs leave the project directory untouched.

    ``view`` projects the old project onto new field locations: current code reads episode-source origins,
    whole-source lists, and source-file kinds at their new locations."""

    project_bytes = (project_dir / "project.json").read_bytes()
    plan = TargetStatePlanner(
        project_dir,
        project_bytes=json.dumps(view).encode(),
        allow_stale_formal_targets=True,
        legacy_audio_entries=ProjectArtifactManifestAdapter(project_dir).snapshot_entries(),
    ).plan()
    assert_artifact_target_state_plan_unchanged(project_dir, plan, expected_project_bytes=project_bytes)
    return plan


def _plan_after_rewrite(project_dir: Path, migrated: Mapping[str, Any]) -> ArtifactTargetStatePlan:
    project_bytes = (project_dir / "project.json").read_bytes()
    after = TargetStatePlanner(
        project_dir, project_bytes=json.dumps(migrated).encode(), allow_stale_formal_targets=True
    ).plan()
    assert_artifact_target_state_plan_unchanged(project_dir, after, expected_project_bytes=project_bytes)
    return after


def _rebase_video_duration_entries(
    project_dir: Path, before: ArtifactTargetStatePlan, after: ArtifactTargetStatePlan
) -> None:
    adapter = ProjectArtifactManifestAdapter(project_dir)
    stored = adapter.snapshot_entries()
    versions_path = project_dir / "versions" / "versions.json"
    versions = _load_object(versions_path) or {}
    replacements: dict[ArtifactKey, ArtifactManifestEntry] = {}
    changed = False
    for key, target in after.entries.items():
        if (
            key.kind is not ArtifactKind.EPISODE_VIDEO
            or key not in before.entries
            or stored.get(key) != before.entries[key]
            or before.entries[key] == target
        ):
            continue
        resource_id = str(key.components[-1])
        resource_type = "reference_videos" if target.artifact_path.startswith("reference_videos/") else "videos"
        record = _selected_version_record(versions, resource_type, resource_id)
        if record is None:
            continue
        try:
            frozen = parse_typed_media_version_target(resource_type, record)
            facts = VideoArtifactCurrencyFacts.from_dict(record.get("artifact_video_currency"))
        except (TypeError, ValueError):
            continue
        if frozen.basis.digest == target.basis_digest:
            replacements[key] = target
            continue
        if frozen.basis.digest != stored[key].basis_digest:
            continue
        for tier in facts.duration_tiers:
            basis = compose_video_artifact_basis(
                visual=facts.visual_basis, speech=facts.speech_basis, duration=build_video_duration_basis(tier)
            )
            if basis.digest == target.basis_digest:
                record[VIDEO_CURRENCY_DURATION_FIELD] = tier
                replacements[key] = target
                changed = True
                break
    if not replacements:
        return
    if changed:
        ensure_versioned_backup(versions_path, TARGET_SCHEMA_VERSION - 1)
        atomic_write_json(versions_path, versions)
    ensure_versioned_backup(project_dir / MANIFEST_FILENAME, TARGET_SCHEMA_VERSION - 1)
    if not adapter.replace_entries_if_matches_atomically(
        expected={key: stored[key] for key in replacements}, replacements=replacements
    ):
        raise RuntimeError("artifact manifest changed while rebasing video duration entries")


# ---------------------------------------------------------------------------
# Substep: register uploaded storyboard images and videos by uploaded bytes
# ---------------------------------------------------------------------------

#: Uploadable artifact kinds whose selected uploaded versions determine their bases, and corresponding version resource types.
_UPLOAD_RESOURCE_TYPES: Mapping[ArtifactKind, tuple[str, ...]] = {
    ArtifactKind.EPISODE_STORYBOARD: ("storyboards",),
    ArtifactKind.EPISODE_VIDEO: ("videos", "reference_videos"),
}


def _uploaded_basis_digest(project_dir: Path, kind: ArtifactKind, artifact_path: str) -> str | None:
    path = try_safe_join(project_dir, artifact_path)
    if path is None or not path.is_file():
        return None
    content_digest = visual_file_digest(path)
    if kind is ArtifactKind.EPISODE_STORYBOARD:
        return build_uploaded_storyboard_basis(content_digest=content_digest).digest
    return build_uploaded_video_basis(content_digest=content_digest).digest


def _register_uploaded_media(project_dir: Path, migrated: Mapping[str, Any]) -> None:
    project_bytes = (project_dir / "project.json").read_bytes()
    target = TargetStatePlanner(project_dir, project_bytes=json.dumps(migrated).encode()).plan()
    assert_artifact_target_state_plan_unchanged(project_dir, target, expected_project_bytes=project_bytes)
    versions = _load_object(project_dir / "versions" / "versions.json") or {}
    adapter = ProjectArtifactManifestAdapter(project_dir)
    stored = adapter.snapshot_entries()
    replacements: dict[ArtifactKey, ArtifactManifestEntry] = {}
    for key, entry in target.entries.items():
        resource_types = _UPLOAD_RESOURCE_TYPES.get(key.kind)
        if resource_types is None or stored.get(key) == entry:
            continue
        resource_id = str(key.components[-1])
        if not any(
            selected_manual_upload_snapshot(_history(versions, resource_type, resource_id), resource_type)
            for resource_type in resource_types
        ):
            continue
        if _uploaded_basis_digest(project_dir, key.kind, entry.artifact_path) == entry.basis_digest:
            replacements[key] = entry
    if not replacements:
        return
    ensure_versioned_backup(project_dir / MANIFEST_FILENAME, TARGET_SCHEMA_VERSION - 1)
    if not adapter.replace_entries_if_matches_atomically(
        expected={key: stored.get(key) for key in replacements}, replacements=replacements
    ):
        raise RuntimeError("artifact manifest changed while registering uploaded media")


def _history(versions: Mapping[str, Any], resource_type: str, resource_id: str) -> object:
    bucket = versions.get(resource_type)
    return bucket.get(resource_id) if isinstance(bucket, Mapping) else None


# ---------------------------------------------------------------------------
# Substep: separate episode IDs from airing order
# ---------------------------------------------------------------------------


def _with_episode_id_high_water(
    project_dir: Path, project: Mapping[str, Any], recorded_episode_ids: RecordedEpisodeIds | None
) -> dict[str, Any]:
    migrated = dict(project)
    manifest_ids = [
        key.episode_number
        for key in ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
        if key.episode_number is not None
    ]
    recorded = recorded_episode_ids(project_dir.name) if recorded_episode_ids is not None else 0
    raise_episode_id_high_water(migrated, *episode_ids_on_disk(project_dir), *manifest_ids, recorded)
    return migrated


def _rebase_script_plan_entries(
    project_dir: Path, before: ArtifactTargetStatePlan, after: ArtifactTargetStatePlan
) -> None:
    adapter = ProjectArtifactManifestAdapter(project_dir)
    stored = adapter.snapshot_entries()
    replacements = {
        key: target
        for key, target in after.entries.items()
        if key.kind is ArtifactKind.EPISODE_SCRIPT_PLAN
        and key in before.entries
        and stored.get(key) == before.entries[key]
        and before.entries[key] != target
    }
    if not replacements:
        return
    ensure_versioned_backup(project_dir / MANIFEST_FILENAME, TARGET_SCHEMA_VERSION - 1)
    if not adapter.replace_entries_if_matches_atomically(
        expected={key: stored[key] for key in replacements}, replacements=replacements
    ):
        raise RuntimeError("artifact manifest changed while rebasing script plan entries")


# ---------------------------------------------------------------------------
# Substep: explicitly register episode and whole-source files
# ---------------------------------------------------------------------------


def _legacy_whole_source_paths(project_dir: Path) -> list[Path]:
    """Whole-source files under the old rules: regular .txt / .md files directly under ``source/``, without dot / underscore prefixes, excluding episode files, sorted by filename."""
    source_dir = project_dir / "source"
    if source_dir.is_symlink() or source_dir.is_junction() or not source_dir.is_dir():
        return []
    return [
        path
        for path in sorted(source_dir.iterdir())
        if not path.is_symlink()
        and path.is_file()
        and not path.name.startswith((".", "_"))
        and path.suffix.lower() in SOURCE_TEXT_SUFFIXES
        and not is_derived_episode_name(path.name)
    ]


def _legacy_origin(entry: Mapping[str, Any], episode_files: Mapping[int, Path], has_whole_source: bool) -> str:
    if parse_source_range(entry) is not None:
        return SourceOrigin.WHOLE_SOURCE.value
    episode = parse_positive_episode_num(entry.get("episode"))
    if episode is None or episode not in episode_files:
        return SourceOrigin.NONE.value
    return SourceOrigin.WHOLE_SOURCE.value if has_whole_source else SourceOrigin.OWN.value


def _with_explicit_episode_sources(
    project_dir: Path, project: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Backfill episode-source origins and whole-source lists, removing the planning cursor; return the migrated project and normalized full texts for pending snapshots."""
    migrated = dict(project)
    whole_source = _legacy_whole_source_paths(project_dir)
    episode_files = discover_episode_files(project_dir)

    raw_episodes = project.get("episodes")
    if isinstance(raw_episodes, list):
        episodes: list[Any] = []
        known: set[int] = set()
        for raw in raw_episodes:
            if not isinstance(raw, Mapping):
                episodes.append(raw)
                continue
            entry = dict(raw)
            if entry.get(SOURCE_ORIGIN_FIELD) not in SOURCE_ORIGINS:
                entry[SOURCE_ORIGIN_FIELD] = _legacy_origin(entry, episode_files, bool(whole_source))
            episode = parse_positive_episode_num(entry.get("episode"))
            if episode is not None:
                known.add(episode)
            episodes.append(entry)
        for episode in sorted(set(episode_files) - known):
            orphan: dict[str, Any] = {
                "episode": episode,
                "title": "",
                "script_file": episode_script_relpath(episode),
                SOURCE_ORIGIN_FIELD: SourceOrigin.OWN.value,
            }
            orphan["ledger_status"] = "consumed" if has_downstream_products(project_dir, episode, orphan) else "planned"
            episodes.append(orphan)
        migrated["episodes"] = episodes

    if not isinstance(migrated.get(WHOLE_SOURCE_FILES_KEY), list):
        migrated[WHOLE_SOURCE_FILES_KEY] = [{"source_file": f"source/{path.name}"} for path in whole_source]
    migrated.pop("planning_cursor", None)

    recorded = project.get(SOURCE_FINGERPRINTS_KEY)
    recorded_fingerprints = recorded if isinstance(recorded, Mapping) else {}
    snapshot_texts: dict[str, str] = {}
    for rel in cut_episode_source_files(migrated):
        path = project_dir / rel
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = normalize_source_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        fingerprint = recorded_fingerprints.get(rel)
        if isinstance(fingerprint, str) and fingerprint != hashlib.sha256(text.encode("utf-8")).hexdigest():
            continue
        snapshot_texts[rel] = text
    return migrated, snapshot_texts


# ---------------------------------------------------------------------------
# Substep: record source kinds with source files
# ---------------------------------------------------------------------------


def _with_source_kinds(project: Mapping[str, Any]) -> dict[str, Any]:
    """Backfill whole-source and own-source episode kinds from the project kind in drama projects; remove the project-level field."""
    migrated = dict(project)
    legacy = migrated.pop(SOURCE_KIND_FIELD, None)
    if not source_kind_applies(migrated):
        return migrated
    kind = legacy if is_source_kind(legacy) else DEFAULT_SOURCE_KIND

    def _with_kind(item: Mapping[str, Any]) -> dict[str, Any]:
        updated = dict(item)
        if not is_source_kind(updated.get(SOURCE_KIND_FIELD)):
            updated[SOURCE_KIND_FIELD] = kind
        return updated

    raw_files = migrated.get(WHOLE_SOURCE_FILES_KEY)
    if isinstance(raw_files, list):
        migrated[WHOLE_SOURCE_FILES_KEY] = [
            _with_kind(item) if isinstance(item, Mapping) else item for item in raw_files
        ]
    raw_episodes = migrated.get("episodes")
    if isinstance(raw_episodes, list):
        migrated["episodes"] = [
            _with_kind(entry)
            if isinstance(entry, Mapping) and episode_source_origin(entry) is SourceOrigin.OWN
            else entry
            for entry in raw_episodes
        ]
    return migrated


# ---------------------------------------------------------------------------
# Substep: move transitions out of scripts
# ---------------------------------------------------------------------------


def _bound_scripts(project_dir: Path, project: Mapping[str, Any]) -> list[Path]:
    raw_episodes = project.get("episodes")
    scripts: list[Path] = []
    for entry in raw_episodes if isinstance(raw_episodes, list) else []:
        script_file = entry.get("script_file") if isinstance(entry, Mapping) else None
        if not isinstance(script_file, str) or not script_file:
            continue
        path = try_safe_join(project_dir, script_file)
        if path is not None and path.is_file() and path not in scripts:
            scripts.append(path)
    return scripts


def _drop_script_transitions(project_dir: Path, project: Mapping[str, Any]) -> None:
    for path in _bound_scripts(project_dir, project):
        script = _load_object(path)
        if script is None:
            continue
        changed = False
        for skeleton in SKELETONS:
            items = script.get(skeleton)
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and _TRANSITION_FIELD in item:
                    del item[_TRANSITION_FIELD]
                    changed = True
        if changed:
            ensure_versioned_backup(path, TARGET_SCHEMA_VERSION - 1)
            atomic_write_json(path, script)


@dataclass(frozen=True, slots=True)
class _PresentationRewrite:
    path: Path
    key: ArtifactKey
    #: Old basis entry recorded before the file rewrite and current basis entry after it.
    legacy: ArtifactManifestEntry
    current: ArtifactManifestEntry
    content: dict[str, Any]


def _media_evidence(raw: object) -> SelectedMediaEvidence:
    if not isinstance(raw, Mapping):
        raise ValueError("presentation media must be an object")
    content_digest = raw.get("content_digest")
    duration = raw.get("actual_duration_seconds")
    if not isinstance(content_digest, str) or not isinstance(duration, int | float):
        raise ValueError("presentation media evidence is malformed")
    return SelectedMediaEvidence(
        basis=ArtifactBasisDescriptor.from_dict(raw.get("basis")),
        content_digest=content_digest,
        actual_duration_seconds=duration,
    )


def _rendition_variant(raw: object) -> RenditionVariant | None:
    if raw == "post_production":
        return "post_production"
    if raw == "use_tts":
        return "use_tts"
    return None


def _legacy_presentation(project_dir: Path, path: Path) -> _PresentationRewrite | None:
    """Compute a rewrite for a presentation-model file with transitions; return None if transitions are absent or it does not match its own recorded old basis."""

    presentation = _load_object(path)
    if presentation is None or _TRANSITION_FIELD not in presentation:
        return None
    transition = presentation[_TRANSITION_FIELD]
    episode = presentation.get("episode")
    unit_id = presentation.get("unit_id")
    variant = _rendition_variant(presentation.get("variant"))
    video = presentation.get("video")
    raw_audio = presentation.get("narration_audio")
    if type(episode) is not int or not isinstance(unit_id, str) or variant is None or not isinstance(video, Mapping):
        return None
    provider_audio_enabled = video.get("audio_enabled")
    if not isinstance(provider_audio_enabled, bool):
        return None
    try:
        artifact_path = path.relative_to(project_dir).as_posix()
        if presentation_artifact_paths(episode, unit_id, variant)[1] != artifact_path:
            return None
        current = build_presentation_basis(
            variant=variant,
            video=_media_evidence(video),
            subtitle=ArtifactBasisDescriptor.from_dict(presentation.get("subtitle_basis")),
            narration_audio=_media_evidence(raw_audio) if raw_audio is not None else None,
            provider_audio_enabled=provider_audio_enabled,
        )
        legacy = build_legacy_transition_presentation_basis(current, transition)
    except (TypeError, ValueError):
        return None
    if presentation.get("presentation_basis") != ArtifactBasisDescriptor.from_basis(legacy).to_dict():
        return None
    content = {key: value for key, value in presentation.items() if key != _TRANSITION_FIELD}
    content["presentation_basis"] = ArtifactBasisDescriptor.from_basis(current).to_dict()
    return _PresentationRewrite(
        path=path,
        key=ArtifactKey.episode_presentation(episode, unit_id, variant),
        legacy=ArtifactManifestEntry(artifact_path=artifact_path, basis_digest=legacy.digest),
        current=ArtifactManifestEntry(artifact_path=artifact_path, basis_digest=current.digest),
        content=content,
    )


def _rebase_presentations(project_dir: Path) -> None:
    rewrites = [
        rewrite
        for path in sorted((project_dir / "presentations").glob("episode_*/*.json"))
        if path.is_file() and (rewrite := _legacy_presentation(project_dir, path)) is not None
    ]
    if not rewrites:
        return
    adapter = ProjectArtifactManifestAdapter(project_dir)
    stored = adapter.snapshot_entries()
    expected = {rewrite.key: rewrite.legacy for rewrite in rewrites if stored.get(rewrite.key) == rewrite.legacy}
    if expected:
        ensure_versioned_backup(project_dir / MANIFEST_FILENAME, TARGET_SCHEMA_VERSION - 1)
        replacements = {rewrite.key: rewrite.current for rewrite in rewrites if rewrite.key in expected}
        if not adapter.replace_entries_if_matches_atomically(expected=expected, replacements=replacements):
            raise RuntimeError("artifact manifest changed while rebasing presentation entries")
    for rewrite in rewrites:
        atomic_write_json(rewrite.path, rewrite.content)


def _move_transitions_out_of_scripts(project_dir: Path, project: Mapping[str, Any]) -> None:
    _drop_script_transitions(project_dir, project)
    _rebase_presentations(project_dir)


# ---------------------------------------------------------------------------
# Substep: make narration delivery a project setting
# ---------------------------------------------------------------------------


def _selected_version_record(
    versions: Mapping[str, Any], resource_type: str, resource_id: str
) -> dict[str, Any] | None:
    bucket = versions.get(resource_type)
    resource = bucket.get(resource_id) if isinstance(bucket, Mapping) else None
    if not isinstance(resource, Mapping):
        return None
    selected_version = resource.get("current_version")
    records = resource.get("versions")
    if type(selected_version) is not int or not isinstance(records, list):
        return None
    selected = [record for record in records if isinstance(record, dict) and record.get("version") == selected_version]
    return selected[0] if len(selected) == 1 else None


def _latest_registered_tts_settings(project_dir: Path) -> TtsSynthesisSettings | None:
    """TTS settings used by the manifest-registered narration selected-version record with the latest ``created_at``."""

    audio_keys = [
        key
        for key in ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
        if key.kind is ArtifactKind.EPISODE_AUDIO
    ]
    if not audio_keys:
        return None
    versions = _load_object(project_dir / "versions" / "versions.json") or {}
    candidates: list[tuple[str, int, str, TtsSynthesisSettings]] = []
    for key in audio_keys:
        episode, resource_id = key.components
        if type(episode) is not int or not isinstance(resource_id, str):
            continue
        record = _selected_version_record(versions, "audio", resource_id)
        if record is None:
            continue
        try:
            target = parse_typed_media_version_target("audio", record)
            settings = parse_typed_audio_settings(record)
        except (TypeError, ValueError):
            continue
        if target.episode == episode:
            candidates.append((target.created_at or "", episode, resource_id, settings))
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[:3])[3]


def _narration_delivery_fields(project_dir: Path, project: Mapping[str, Any]) -> dict[str, Any]:
    """Migrated ``project.json``: write narration delivery and replace TTS-project settings with the TTS snapshot."""

    settings = _latest_registered_tts_settings(project_dir)
    if settings is None:
        return {**project, NARRATION_DELIVERY_FIELD: POST_PRODUCTION}
    migrated = {**project, NARRATION_DELIVERY_FIELD: USE_TTS, **tts_snapshot_fields(settings)}
    if settings.speed is None:
        del migrated[TTS_SPEED_FIELD]
    return migrated


# ---------------------------------------------------------------------------


def _without_asset_inventory_marker(project: Mapping[str, Any]) -> dict[str, Any]:
    """Remove the retired asset-inventory marker; remove ``workflow`` if now empty, preserving other shapes unchanged."""

    migrated = dict(project)
    workflow = migrated.get("workflow")
    if isinstance(workflow, Mapping) and "asset_inventory" in workflow:
        remaining = {key: value for key, value in workflow.items() if key != "asset_inventory"}
        if remaining:
            migrated["workflow"] = remaining
        else:
            migrated.pop("workflow")
    return migrated


# ---------------------------------------------------------------------------
# Substep: align project-list completion with production status
# ---------------------------------------------------------------------------


def _with_source_remaining(project_dir: Path, project: Mapping[str, Any]) -> dict[str, Any]:
    """Record whether whole-source files on disk still contain unplanned text; call after backfilling whole-source lists and episode-source origins."""

    migrated = dict(project)
    record_source_remaining(project_dir, migrated)
    return migrated


def migrate_v15_to_v16(
    project_dir: Path, *, recorded_episode_ids: RecordedEpisodeIds | None = None
) -> ArtifactBackfillOutcome | None:
    """v15→v16 file-level migration."""

    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return None
    project = json.loads(project_file.read_bytes())
    if not isinstance(project, dict):
        raise ValueError("project.json 必须是对象")
    if parse_project_schema_version(project) >= TARGET_SCHEMA_VERSION:
        return None

    with project_metadata_lock(project_dir):
        before = _plan_before_rewrite(
            project_dir, _with_source_kinds(_with_explicit_episode_sources(project_dir, project)[0])
        )
        _move_transitions_out_of_scripts(project_dir, project)
        with_sources, snapshot_texts = _with_explicit_episode_sources(
            project_dir, _narration_delivery_fields(project_dir, project)
        )
        migrated_project = {
            **_with_source_remaining(
                project_dir,
                _without_asset_inventory_marker(
                    _with_episode_id_high_water(project_dir, _with_source_kinds(with_sources), recorded_episode_ids)
                ),
            ),
            "schema_version": TARGET_SCHEMA_VERSION,
        }
        after = _plan_after_rewrite(project_dir, migrated_project)
        _rebase_video_duration_entries(project_dir, before, after)
        _rebase_script_plan_entries(project_dir, before, after)
        _register_uploaded_media(project_dir, migrated_project)
        target = TargetStatePlanner(project_dir, project_bytes=json.dumps(migrated_project).encode()).plan()
        sync_source_snapshots(project_dir, migrated_project, snapshot_texts)
        atomic_write_json(project_file, migrated_project)
        return ArtifactBackfillOutcome.from_entries(
            ProjectArtifactManifestAdapter(project_dir).snapshot_entries(),
            target.skipped,
            preserve_previous_skips=True,
        )


__all__ = ["TARGET_SCHEMA_VERSION", "RecordedEpisodeIds", "migrate_v15_to_v16"]
