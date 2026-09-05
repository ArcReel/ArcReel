"""Typed provenance for media version records written before it existed.

A selected video version record that predates typed provenance carries only
``version / file / prompt / created_at / duration_seconds``. The Artifact
Manifest planner and the presentation read model both refuse such a record,
so the paid video is invisible to them. This module projects the missing
facts from the project as it stands at migration time — the same projection
the currency comparison uses — and writes them onto the record, stamped with
``provenance_backfilled_at`` so a reader can tell a projected record from one
frozen at generation time.

Audio records are not backfilled: their basis includes the TTS settings the
audio was synthesised with, which no legacy record carries.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.artifact_manifest import ArtifactKey
from lib.artifact_version_provenance import parse_typed_media_version_target
from lib.content_digest import canonical_json_digest
from lib.json_io import atomic_write_json, load_json
from lib.media_artifact_currency import VideoExecutionShape, project_video_basis_components
from lib.path_safety import try_safe_join
from lib.project_manager import ProjectManager
from lib.project_migration_report import MigrationSkippedArtifact
from lib.reference_video.execution_checkpoint import CHECKPOINT_SCHEMA_VERSION
from lib.resource_paths import resource_relative_path
from lib.script_editor import ScriptEditError, resolve_items
from lib.version_manager import VersionManager
from lib.video_artifact_facts import VideoArtifactCurrencyFacts

PROVENANCE_BACKFILLED_AT_FIELD = "provenance_backfilled_at"
"""Set on a version record whose typed provenance was projected by a migration."""

_VIDEO_RESOURCE_TYPES = {"videos": "segments", "reference_videos": "video_units"}


@dataclass(frozen=True, slots=True)
class LegacyProvenanceBackfill:
    """Which selected records were amended, and which pointers were left alone."""

    amended: tuple[tuple[str, str], ...]
    """``(resource_type, resource_id)`` of every record that received typed provenance."""
    skipped: tuple[MigrationSkippedArtifact, ...]


def backfill_legacy_media_provenance(project_dir: Path) -> LegacyProvenanceBackfill:
    """Amend every selected legacy video record the bound scripts still point at.

    Writes ``versions/versions.json`` once, atomically, and only when at least
    one record was amended. Records that already parse as typed are untouched.
    """

    project_dir = Path(project_dir)
    project = load_json(project_dir / "project.json")
    if not isinstance(project, dict):
        raise ValueError("project.json must contain an object")
    versions_path = project_dir / "versions" / "versions.json"
    versions_data: Any = load_json(versions_path) if versions_path.is_file() else {}
    if not isinstance(versions_data, dict):
        raise ValueError("versions/versions.json must contain an object")
    version_manager = VersionManager(project_dir)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    amended: list[tuple[str, str]] = []
    skipped: list[MigrationSkippedArtifact] = []
    for episode, script_file, script in _bound_scripts(project_dir, project):
        try:
            items, id_field, kind = resolve_items(script)
        except ScriptEditError:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            assets = item.get("generated_assets")
            if not isinstance(assets, Mapping):
                continue
            resource_id = str(item.get(id_field))
            if item.get("needs_replan") is True:
                skipped.extend(_replan_skips(episode, resource_id, assets))
                continue
            audio_path = assets.get("narration_audio")
            if isinstance(audio_path, str) and audio_path:
                record = _selected_record(versions_data, "audio", resource_id)
                if record is not None and not _is_typed("audio", record):
                    skipped.append(
                        MigrationSkippedArtifact(
                            kind=ArtifactKey.episode_audio(episode, resource_id).kind.value,
                            episode=episode,
                            resource_id=resource_id,
                            artifact_path=audio_path,
                            reason="legacy audio version has no TTS settings to project a basis from",
                        )
                    )
            video_path = assets.get("video_clip")
            if not isinstance(video_path, str) or not video_path:
                continue
            resource_type = "reference_videos" if kind == "video_units" else "videos"
            record = _selected_record(versions_data, resource_type, resource_id)
            if record is None or _is_typed(resource_type, record):
                continue
            try:
                amendment = _project_video_provenance(
                    project_dir=project_dir,
                    project=project,
                    item=item,
                    skeleton_kind=kind,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    episode=episode,
                    script_file=script_file,
                    record=record,
                    versions=version_manager,
                )
            except (KeyError, OSError, TypeError, ValueError) as exc:
                skipped.append(
                    MigrationSkippedArtifact(
                        kind=ArtifactKey.episode_video(episode, resource_id).kind.value,
                        episode=episode,
                        resource_id=resource_id,
                        artifact_path=video_path,
                        reason=f"typed provenance cannot be projected: {exc}",
                    )
                )
                continue
            record.update(amendment)
            record[PROVENANCE_BACKFILLED_AT_FIELD] = stamp
            amended.append((resource_type, resource_id))

    if amended:
        atomic_write_json(versions_path, versions_data)
    return LegacyProvenanceBackfill(amended=tuple(amended), skipped=tuple(skipped))


def _replan_skips(episode: int, resource_id: str, assets: Mapping[str, Any]) -> list[MigrationSkippedArtifact]:
    """Media hanging off a unit marked ``needs_replan`` is not formal; say so instead of dropping it."""

    skips: list[MigrationSkippedArtifact] = []
    for field, key in (
        ("narration_audio", ArtifactKey.episode_audio(episode, resource_id)),
        ("video_clip", ArtifactKey.episode_video(episode, resource_id)),
    ):
        path = assets.get(field)
        if isinstance(path, str) and path:
            skips.append(
                MigrationSkippedArtifact(
                    kind=key.kind.value,
                    episode=episode,
                    resource_id=resource_id,
                    artifact_path=path,
                    reason="script unit is marked needs_replan; its media is not a formal artifact",
                )
            )
    return skips


def _bound_scripts(project_dir: Path, project: dict[str, Any]) -> list[tuple[int, str, dict[str, Any]]]:
    episodes = project.get("episodes")
    bound: list[tuple[int, str, dict[str, Any]]] = []
    for entry in episodes if isinstance(episodes, list) else []:
        if not isinstance(entry, Mapping):
            continue
        episode = entry.get("episode")
        script_file = entry.get("script_file")
        if type(episode) is not int or episode < 1 or not isinstance(script_file, str) or not script_file:
            continue
        script_path = try_safe_join(project_dir, script_file, require_file=True)
        if script_path is None:
            continue
        try:
            script = load_json(script_path)
        except (OSError, ValueError):
            continue
        if not isinstance(script, dict):
            continue
        bound.append((episode, ProjectManager.normalize_script_filename(script_file), script))
    return bound


def _selected_record(versions_data: dict[str, Any], resource_type: str, resource_id: str) -> dict[str, Any] | None:
    bucket = versions_data.get(resource_type)
    resource = bucket.get(resource_id) if isinstance(bucket, dict) else None
    if not isinstance(resource, dict):
        return None
    selected_version = resource.get("current_version")
    records = resource.get("versions")
    if type(selected_version) is not int or not isinstance(records, list):
        return None
    selected = [record for record in records if isinstance(record, dict) and record.get("version") == selected_version]
    return selected[0] if len(selected) == 1 else None


def _is_typed(resource_type: str, record: Mapping[str, Any]) -> bool:
    try:
        parse_typed_media_version_target(resource_type, record)
    except (TypeError, ValueError):
        return False
    return True


def _request_duration(item: Mapping[str, Any], project: Mapping[str, Any], record: Mapping[str, Any]) -> int:
    for candidate in (item.get("duration_seconds"), project.get("default_duration"), record.get("duration_seconds")):
        if isinstance(candidate, str) and candidate.strip().isdigit():
            candidate = int(candidate)
        if type(candidate) is int and candidate > 0:
            return candidate
    raise ValueError("no positive request duration on the script item, the project or the version record")


def _project_video_provenance(
    *,
    project_dir: Path,
    project: dict[str, Any],
    item: dict[str, Any],
    skeleton_kind: str,
    resource_type: str,
    resource_id: str,
    episode: int,
    script_file: str,
    record: Mapping[str, Any],
    versions: VersionManager,
) -> dict[str, Any]:
    snapshot_rel = record.get("file")
    if not VersionManager.is_managed_snapshot_path(resource_type, snapshot_rel):
        raise ValueError("selected version snapshot is not a managed history path")
    current_path = project_dir / resource_relative_path(resource_type, resource_id)
    if not current_path.is_file():
        raise ValueError("current video file is missing")
    request_duration = _request_duration(item, project, record)
    shape = VideoExecutionShape(
        voice_style_speakers=(),
        reference_audio_speakers=(),
        reference_image_limit=None,
        duration_tiers=(request_duration,),
    )
    components = project_video_basis_components(
        project_path=project_dir,
        project=project,
        item=item,
        skeleton_kind=skeleton_kind,
        resource_type=resource_type,
        resource_id=resource_id,
        episode=episode,
        shape=shape,
        versions=versions,
        version_metadata=record,
    )
    facts = VideoArtifactCurrencyFacts(
        episode=episode,
        request_duration_seconds=request_duration,
        visual_basis=components.visual,
        speech_basis=components.speech,
        duration_basis=components.duration,
        video_basis=components.compose(),
        voice_style_speakers=(),
        duration_tiers=(request_duration,),
        reference_image_limit=None,
        parent_version=0,
    )
    facts_dict = facts.to_dict()
    return {
        "execution_checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "execution_duration_seconds": request_duration,
        "execution_request_digest": canonical_json_digest(facts_dict, allow_nan=False),
        "execution_script_file": script_file,
        "execution_provider_media": [],
        # 旧记录没有记下供应商音轨开关，按项目当前设置投影，与其余字段同一口径。
        "execution_generate_audio": project.get("video_generate_audio") is True,
        "artifact_video_currency": facts_dict,
    }


__all__ = [
    "PROVENANCE_BACKFILLED_AT_FIELD",
    "LegacyProvenanceBackfill",
    "backfill_legacy_media_provenance",
]
