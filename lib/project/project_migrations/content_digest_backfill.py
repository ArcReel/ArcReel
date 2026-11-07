"""v17→v18 content-fingerprint backfill for existing video/narration artifacts (``docs/adr/0062``).

From v18, registrations store formal-file facts (digest, size, nanosecond mtime), while history stores
snapshot digests. Hash registered formal files and selected snapshots once; only matching pairs update
both history/manifests. Unreadable/mismatched/missing selected snapshots stay unchanged, preserving prior decisions.

Run after gap backfill: planners just hashed the same files, so stat identities hit process digest caches
without rereading. Size/mtime come from the same opened descriptor used for hashing."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from lib.artifacts.artifact_manifest import (
    CONTENT_FACT_ARTIFACT_KINDS,
    ArtifactContentFacts,
    ArtifactKey,
    ArtifactKind,
    ArtifactManifestEntry,
    ArtifactObservation,
    ProjectArtifactManifestAdapter,
    same_entry_records,
)
from lib.artifacts.version_manager import VERSION_CONTENT_DIGEST, VersionManager
from lib.infra.json_io import atomic_write_bytes, atomic_write_json
from lib.project.resource_paths import resource_relative_path


def backfill_content_digests(project_dir: Path) -> None:
    """Backfill registered video/narration fingerprints. Callers hold metadata locks and already backed up manifests/history.

    Write history before committing manifests; failed manifest commits restore original history bytes, letting retries restart."""

    project_dir = Path(project_dir)
    versions_path = project_dir / "versions" / "versions.json"
    if not versions_path.is_file():
        return
    versions_bytes = versions_path.read_bytes()
    versions = json.loads(versions_bytes)
    if not isinstance(versions, dict):
        raise ValueError("versions/versions.json must contain an object")
    adapter = ProjectArtifactManifestAdapter(project_dir)
    stored = adapter.snapshot_entries()
    proven: dict[ArtifactKey, ArtifactManifestEntry] = {}
    amended = False
    for key, entry in stored.items():
        if key.kind not in CONTENT_FACT_ARTIFACT_KINDS:
            continue
        resource_type = _resource_type(key, entry.artifact_path)
        if resource_type is None:
            continue
        record = _selected_record(versions, resource_type, str(key.components[1]))
        snapshot_path = record.get("file") if record is not None else None
        if record is None or not VersionManager.is_managed_snapshot_path(resource_type, snapshot_path):
            continue
        formal = _read_content(adapter, entry.artifact_path)
        snapshot = _read_content(adapter, str(snapshot_path))
        if formal is None or snapshot is None:
            continue
        formal_stat = formal.file_stat
        assert formal_stat is not None
        digest = cast(str, formal.content_digest)
        if digest != snapshot.content_digest:
            continue
        amended = amended or record.get(VERSION_CONTENT_DIGEST) != digest
        record[VERSION_CONTENT_DIGEST] = digest
        proven[key] = replace(
            entry,
            content=ArtifactContentFacts(digest=digest, size=formal_stat.st_size, mtime_ns=formal_stat.st_mtime_ns),
        )
    if not proven:
        return
    if amended:
        atomic_write_json(versions_path, versions)
    try:
        expected = {key: stored[key] for key in proven}
        # No write may mean an interrupted previous run already stored identical facts; only actual manifest changes count as conflicts.
        if not adapter.replace_entries_if_matches_atomically(
            expected=expected, replacements=proven
        ) and not same_entry_records({key: adapter.get_entry(key) for key in proven}, proven):
            raise RuntimeError("artifact manifest changed while backfilling content digests")
    except BaseException:
        if amended:
            atomic_write_bytes(versions_path, versions_bytes)
        raise


def _resource_type(key: ArtifactKey, artifact_path: str) -> str | None:
    """History resource type: narration is audio; canonical video paths distinguish storyboard/reference video."""

    resource_id = str(key.components[1])
    candidates = ("audio",) if key.kind is ArtifactKind.EPISODE_AUDIO else ("videos", "reference_videos")
    return next((name for name in candidates if resource_relative_path(name, resource_id) == artifact_path), None)


def _selected_record(versions: dict[str, Any], resource_type: str, resource_id: str) -> dict[str, Any] | None:
    bucket = versions.get(resource_type)
    resource = bucket.get(resource_id) if isinstance(bucket, dict) else None
    if not isinstance(resource, dict):
        return None
    selected_version = resource.get("current_version")
    records = resource.get("versions")
    if type(selected_version) is not int or not isinstance(records, list):
        return None
    selected = [record for record in records if isinstance(record, dict) and record.get("version") == selected_version]
    return selected[0] if len(selected) == 1 else None


def _read_content(adapter: ProjectArtifactManifestAdapter, artifact_path: str) -> ArtifactObservation | None:
    observation = adapter.inspect_artifact_content(artifact_path)
    if observation.blocker is not None or not observation.present or observation.content_digest is None:
        return None
    return observation


__all__ = ["backfill_content_digests"]
