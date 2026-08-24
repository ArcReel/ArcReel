"""v10→v11: give legacy reference-video units a non-empty keyframe plan."""

from __future__ import annotations

import copy
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from lib.json_io import atomic_write_json, load_json
from lib.path_safety import safe_join
from lib.project_migration_failure import ProjectMigrationError
from lib.reference_video.keyframes import DEFAULT_ENTRY_KEYFRAME_DESCRIPTION, keyframe_id, keyframe_mention
from lib.script_models import ReferenceStep1Unit, ReferenceVideoUnit
from lib.script_review import REFERENCE_VIDEO_STEP1_FILENAME, episode_drafts_dir

_TARGET_VERSION = 11


def _seed_formal_unit(unit: object) -> object:
    if not isinstance(unit, dict):
        return unit
    migrated = copy.deepcopy(unit)
    unit_id = migrated.get("unit_id")
    text = migrated.get("text")
    if not isinstance(unit_id, str) or not unit_id or not isinstance(text, str) or not text.strip():
        ReferenceVideoUnit.model_validate(migrated)
        return migrated
    keyframes = migrated.get("keyframes")
    if isinstance(keyframes, list) and keyframes:
        ReferenceVideoUnit.model_validate(migrated)
        return migrated
    stable_id = keyframe_id(unit_id, 1)
    migrated["keyframes"] = [
        {
            "keyframe_id": stable_id,
            "description": DEFAULT_ENTRY_KEYFRAME_DESCRIPTION,
            "image_path": None,
        }
    ]
    mention = keyframe_mention(stable_id)
    if mention not in text:
        migrated["text"] = f"{mention} {text}".strip()
    ReferenceVideoUnit.model_validate(migrated)
    return migrated


def _seed_step1_unit(unit: object) -> object:
    if not isinstance(unit, dict):
        return unit
    migrated = copy.deepcopy(unit)
    plan = migrated.get("keyframe_plan")
    if not isinstance(plan, list) or not plan:
        migrated["keyframe_plan"] = [DEFAULT_ENTRY_KEYFRAME_DESCRIPTION]
    ReferenceStep1Unit.model_validate(migrated)
    return migrated


def _episode_entries(project_dir: Path, project: dict[str, Any]) -> list[tuple[Path, int]]:
    episodes = project.get("episodes")
    if not isinstance(episodes, list):
        raise ValueError("project.episodes 必须是数组")
    result: list[tuple[Path, int]] = []
    seen: set[Path] = set()
    for index, entry in enumerate(episodes):
        if not isinstance(entry, dict):
            raise ValueError(f"project.episodes[{index}] 必须是对象")
        episode = entry.get("episode")
        script_file = entry.get("script_file")
        if not isinstance(episode, int) or isinstance(episode, bool) or episode <= 0:
            raise ValueError(f"project.episodes[{index}].episode 必须是正整数")
        if not isinstance(script_file, str) or not script_file:
            raise ValueError(f"project.episodes[{index}].script_file 必须是非空字符串")
        path = safe_join(project_dir, script_file)
        if path in seen:
            raise ValueError(f"多个 episode 指向同一剧本文件: {script_file}")
        seen.add(path)
        result.append((path, episode))
    return result


def _readable_file(path: Path, label: str) -> dict[str, Any] | None:
    if path.is_symlink():
        raise ValueError(f"{label} 不是普通文件")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"{label} 不是普通文件")
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} 必须是对象")
    return payload


def _migrate_script_payload(payload: dict[str, Any], *, location: str) -> dict[str, Any]:
    units = payload.get("video_units")
    if not isinstance(units, list):
        raise ValueError(f"{location}.video_units 必须是数组")
    migrated = copy.deepcopy(payload)
    migrated["video_units"] = [_seed_formal_unit(unit) for unit in units]
    return migrated


def _migrate_step1_payload(payload: dict[str, Any], *, location: str) -> dict[str, Any]:
    units = payload.get("units")
    if not isinstance(units, list):
        raise ValueError(f"{location}.units 必须是数组")
    migrated = copy.deepcopy(payload)
    migrated["units"] = [_seed_step1_unit(unit) for unit in units]
    return migrated


def _ensure_backup(path: Path) -> None:
    if any(path.parent.glob(f"{path.name}.bak.v10-*")):
        return
    shutil.copy2(path, path.with_name(f"{path.name}.bak.v10-{time.time_ns()}"))


@contextmanager
def _located(episode: int, file: str) -> Iterator[None]:
    try:
        yield
    except ProjectMigrationError:
        raise
    except (TypeError, ValueError) as exc:
        raise ProjectMigrationError(str(exc), episode=episode, file=file) from exc


def migrate_v10_to_v11(project_dir: Path) -> None:
    """Seed addressable legacy keyframes without generating or confirming images."""

    project_file = Path(project_dir) / "project.json"
    if not project_file.is_file():
        return
    project = load_json(project_file)
    if not isinstance(project, dict):
        raise ValueError("project.json must contain an object")
    if int(project.get("schema_version") or 0) >= _TARGET_VERSION:
        return

    plans: list[tuple[Path, dict[str, Any]]] = []
    if project.get("generation_mode") == "reference_video":
        # Read and validate the complete migration set before creating backups
        # or modifying any business file.
        for script_path, episode in _episode_entries(project_dir, project):
            with _located(episode, script_path.name):
                script = _readable_file(script_path, f"剧本 {script_path.name}")
                if script is not None:
                    plans.append(
                        (
                            script_path,
                            _migrate_script_payload(script, location=f"剧本 {script_path.name}"),
                        )
                    )

            draft_path = episode_drafts_dir(project_dir, episode) / REFERENCE_VIDEO_STEP1_FILENAME
            with _located(episode, REFERENCE_VIDEO_STEP1_FILENAME):
                draft = _readable_file(draft_path, f"第 {episode} 集 {REFERENCE_VIDEO_STEP1_FILENAME}")
                if draft is not None:
                    plans.append(
                        (
                            draft_path,
                            _migrate_step1_payload(
                                draft,
                                location=f"第 {episode} 集 {REFERENCE_VIDEO_STEP1_FILENAME}",
                            ),
                        )
                    )

    for path, _payload in [*plans, (project_file, project)]:
        _ensure_backup(path)
    for path, payload in plans:
        atomic_write_json(path, payload)

    migrated_project = copy.deepcopy(project)
    migrated_project["schema_version"] = _TARGET_VERSION
    atomic_write_json(project_file, migrated_project)


__all__ = ["migrate_v10_to_v11"]
