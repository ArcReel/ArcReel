"""Upgrade the frozen narration delivery chain while preserving pre-migration currency."""

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasisDescriptor,
    ArtifactKind,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_planner import TargetStatePlanner
from lib.infra.json_io import atomic_write_bytes, atomic_write_json
from lib.project.project_migrations.backups import ensure_versioned_backup
from lib.project.project_migrations.legacy_audio import V1_AUDIO_RULES, read_v1_audio_target
from lib.project.project_trash import trash_dir
from lib.speech.narration_config import TTS_GROUP_FIELDS, project_tts_settings
from lib.speech.narration_delivery import build_narration_audio_basis_from_canonical_text
from lib.speech.speech_presentation import materialize_speech_presentation

_KINDS = {
    ArtifactKind.EPISODE_AUDIO,
    ArtifactKind.EPISODE_SUBTITLE,
    ArtifactKind.EPISODE_PRESENTATION,
    ArtifactKind.EPISODE_FINAL_CUT,
    ArtifactKind.EPISODE_JIANYING_DRAFT,
}


def _upgrade_audio_metadata(metadata: dict, prompt: Any) -> tuple[str, ArtifactBasisDescriptor] | None:
    try:
        old = read_v1_audio_target({**metadata, "prompt": prompt})
    except (TypeError, ValueError):
        return None
    new = ArtifactBasisDescriptor.from_basis(build_narration_audio_basis_from_canonical_text(prompt))
    metadata.update(artifact_audio_basis=new.to_dict(), tts_basis_digest=new.digest, tts_speed=old.settings.speed)
    return old.basis.digest, new


def _upgrade_deleted_audio_versions(root: Path) -> None:
    trash = trash_dir(root)
    if trash.parent.is_symlink() or trash.is_symlink():
        raise ValueError("项目回收站路径不能是符号链接")
    for path in trash.glob("*/record.json"):
        if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
            continue
        try:
            record = json.loads(path.read_bytes())
        except ValueError:
            continue
        obj = record.get("object") if isinstance(record, dict) else None
        if not isinstance(obj, dict) or obj.get("kind") != "version" or obj.get("resource_type") != "audio":
            continue
        metadata = obj.get("metadata")
        if isinstance(metadata, dict) and _upgrade_audio_metadata(metadata, obj.get("prompt")) is not None:
            ensure_versioned_backup(path, 20)
            atomic_write_json(path, record)


def _upgrade_records(history: dict) -> dict[str, ArtifactBasisDescriptor]:
    rewritten = {}
    audio = history.get("audio")
    if not isinstance(audio, dict):
        return rewritten
    for resource in audio.values():
        records = resource.get("versions") if isinstance(resource, dict) else None
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            if upgraded := _upgrade_audio_metadata(record, record.get("prompt")):
                old_digest, new = upgraded
                rewritten[old_digest] = new
    return rewritten


def _presentation_updates(
    planner: TargetStatePlanner, rewritten: dict[str, ArtifactBasisDescriptor]
) -> dict[Path, dict]:
    updates = {}
    for subtitle_path, presentation_path, metadata, frozen, preparation in planner.proven_narrated_presentations():
        assert frozen.narration_audio is not None
        media = frozen.narration_audio.media
        basis = rewritten.get(media.evidence.basis.digest)
        if basis is None:
            continue
        upgraded = materialize_speech_presentation(
            preparation,
            variant=frozen.variant,
            video=frozen.video.media,
            narration_audio=replace(media, evidence=replace(media.evidence, basis=basis)),
            provider_audio_enabled=frozen.video.audio_enabled,
            subtitle_sentences_prepared=True,
        )
        updates[planner.project_dir / subtitle_path] = upgraded.subtitle_artifact_dict()
        updates[planner.project_dir / presentation_path] = {**metadata, **upgraded.to_dict()}
    return updates


_RECOVERY_FILE = ".audio-delivery-v20.pending.json"
_BACKUP_PATH = re.compile(
    r"(?P<source>versions/versions\.json|\.arcreel_artifacts\.json|(?:subtitles|presentations)/episode_[0-9]+/[A-Za-z0-9_-]+\.use_tts\.json)\.bak\.v20-[0-9]+"
)


def _restore_preflight_inputs(root: Path) -> None:
    marker = root / _RECOVERY_FILE
    if not marker.exists():
        return
    if marker.is_symlink():
        raise ValueError("invalid audio migration recovery file")
    for relative in json.loads(marker.read_bytes()):
        match = _BACKUP_PATH.fullmatch(relative)
        if not match:
            raise ValueError("invalid audio migration backup path")
        backup = root / relative
        path = root / match["source"]
        if backup.resolve() != backup or path.resolve() != path:
            raise ValueError("invalid audio migration backup path")
        original = backup.read_bytes()
        if path.read_bytes() != original:
            atomic_write_bytes(path, original)


def upgrade_audio_delivery(root: Path, project: dict[str, Any]) -> None:
    root = root.resolve()
    source = root / "versions/versions.json"
    _restore_preflight_inputs(root)
    _upgrade_deleted_audio_versions(root)
    if source.is_file() and source.resolve() == source:
        history = json.loads(source.read_bytes())
        rewritten = _upgrade_records(history)
        if rewritten:
            _rebase_delivery(root, project, source, history, rewritten)
    if project_tts_settings(project) is None:
        for key in TTS_GROUP_FIELDS:
            project.pop(key, None)


def _rebase_delivery(
    root: Path, project: dict, source: Path, history: dict, rewritten: dict[str, ArtifactBasisDescriptor]
) -> None:
    project_bytes = json.dumps({**project, "schema_version": 21}).encode()
    before = TargetStatePlanner(root, audio_rules=V1_AUDIO_RULES)
    adapter = ProjectArtifactManifestAdapter(root)
    stored = adapter.snapshot_entries()
    targets = before.project_registered_entries([key for key in stored if key.kind in _KINDS])
    current = {key: stored[key] for key, target in targets.items() if stored[key] == target}
    updates = {source: history, **_presentation_updates(before, rewritten)}
    backups = []
    for path in (*updates, root / MANIFEST_FILENAME):
        backup = ensure_versioned_backup(path, 20)
        if backup is not None:
            backups.append(backup.relative_to(root).as_posix())
    marker = root / _RECOVERY_FILE
    atomic_write_json(marker, backups)
    for path, data in updates.items():
        atomic_write_json(path, data)
    after = TargetStatePlanner(root, project_bytes=project_bytes)
    replacements = {}
    for key, target in after.project_registered_entries(current.keys()).items():
        entry = current[key]
        if target != entry and target.artifact_path == entry.artifact_path:
            replacements[key] = replace(entry, basis_digest=target.basis_digest)
    if replacements and not adapter.replace_entries_if_matches_atomically(
        expected={k: current[k] for k in replacements}, replacements=replacements
    ):
        raise RuntimeError("artifact manifest changed while upgrading narration")
    marker.unlink()  # hard-delete: infra -- 已完成迁移的重试标记
