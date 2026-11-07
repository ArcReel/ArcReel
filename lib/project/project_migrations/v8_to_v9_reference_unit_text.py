r"""v8→v9: consolidate video units into one body + planned duration.

Previously bodies persisted as ``shots[]`` with derived ``references[]``, machine splits without
creator value (ADR 0064). Remove both:

- Join ``shots[*].text`` in array order with newlines into ``text``. Inject no segment prefixes;
  existing literal ``镜头N：`` becomes ordinary body text, preserved verbatim. First adopt old shot
  durations at unit level (``lib.script.reference_video.duration_migration``), previously lazy in loading,
  because migration removes ``shots`` entirely.
- Remove ``references[]``; resolve body ``@[名称]`` images at execution in first-mention order.
- Remove ``migration_requires_content_replan``, evidence from v6→v7 shot folding with no remaining
  target after shot removal. Preserve implied ``needs_replan`` so problem units still block generation and require replanning.

Apply to scripts and script_plan drafts, prior forms of the same body that review gates otherwise
cannot read. Preserve unreadable/unconvertible drafts unchanged without rejecting projects (ADR 0098).
Likewise mark unconvertible/model-invalid script units for runtime replanning without whole-project rejection.

Preflight all files/read-only payloads before backups, then atomically replace individually and commit
``project.json`` last. This step owns its ``project.json`` backup at that boundary (runner
``_MIGRATORS_WITH_OWNED_BACKUP``), avoiding runner backup writes before preflight. Corrupt scripts
leave all project bytes untouched; runners record repair-needed verdicts."""

from __future__ import annotations

import copy
import logging
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from lib.episode.episode_paths import episode_drafts_dir
from lib.infra.json_io import atomic_write_json, load_json
from lib.infra.path_safety import safe_join
from lib.project.project_migration_failure import ProjectMigrationError
from lib.project.project_migrations.backups import ensure_versioned_backup
from lib.script.reference_video.duration_migration import migrate_unit_durations
from lib.script.script_models import ReferenceScriptPlanUnit, ReferenceVideoUnit

logger = logging.getLogger(__name__)

_TARGET_VERSION = 9

#: Historical reference-plan draft names in lookup order. Projects starting below v8 were renamed to ``script_plan_*`` during
#: v7→v8; those starting at v8 skipped that step and retain v8 persisted
#: ``step1_*`` names. Freeze both historical facts here, independent of current naming.
_REFERENCE_UNITS_FILENAMES = ("script_plan_reference_units.json", "step1_reference_units.json")


def _reference_units_draft_path(project_dir: Path, episode: int) -> Path:
    r"""Find episode on-disk plan drafts; return preferred names if both absent, leaving readers to handle missing files."""

    drafts_dir = episode_drafts_dir(project_dir, episode)
    for name in _REFERENCE_UNITS_FILENAMES:
        candidate = drafts_dir / name
        if candidate.exists():
            return candidate
    return drafts_dir / _REFERENCE_UNITS_FILENAMES[0]


def _unit_text(unit: dict[str, Any], location: str) -> str:
    r"""Join ``shots[*].text`` in order into bodies; return already-body units unchanged."""

    if "shots" not in unit:
        text = unit.get("text")
        if not isinstance(text, str):
            raise ValueError(f"{location} 既没有 shots 也没有字符串 text")
        return text
    shots = unit.get("shots")
    if not isinstance(shots, list):
        raise ValueError(f"{location}.shots 必须是数组")
    parts: list[str] = []
    for index, shot in enumerate(shots):
        if not isinstance(shot, dict):
            raise ValueError(f"{location}.shots[{index}] 必须是对象")
        text = shot.get("text")
        if text is not None and not isinstance(text, str):
            raise ValueError(f"{location}.shots[{index}].text 必须是字符串")
        parts.append(text or "")
    return "\n".join(parts)


def _convert_unit(unit: object, location: str) -> dict[str, Any]:
    r"""Mechanically adopt shot durations, join bodies, and remove ``shots`` / ``references``."""

    if not isinstance(unit, dict):
        raise ValueError(f"{location} 必须是对象")
    migrated = copy.deepcopy(unit)
    # Durations once lived on shots[*].duration and migrated lazily on loading; persisted data may still have only shot durations.
    # shots is removed below, making this the last chance to read durations; adopt them before joining bodies.
    migrate_unit_durations([migrated])
    text = _unit_text(migrated, location)
    migrated.pop("shots", None)
    migrated.pop("references", None)
    # Legacy markers served shot structures only; explicitly preserve implied needs_replan next.
    if migrated.pop("migration_requires_content_replan", None) is True:
        migrated["needs_replan"] = True
    migrated["text"] = text
    return migrated


def _without_transition(unit: dict[str, Any]) -> dict[str, Any]:
    # Transitions remain until v15→v16, but current self-check models exclude them.
    return {key: value for key, value in unit.items() if key != "transition_to_next"}


def _migrate_unit(unit: object, location: str, model: type[BaseModel]) -> dict[str, Any]:
    r"""Convert units and self-check target models.

    Validate units only: unrelated legacy script dirt (e.g. absent title) must not mark whole projects repair-needed."""

    migrated = _convert_unit(unit, location)
    model.model_validate(_without_transition(migrated))
    return migrated


def _migrate_script_unit(unit: object, location: str) -> dict[str, Any]:
    r"""Convert script units; mark unconvertible/model-invalid units for replanning without rejecting projects.

    Retain originals if unconvertible, converted results if model-invalid. Runtime replanning handles both (ADR 0098)."""

    if not isinstance(unit, dict):
        raise ValueError(f"{location} 必须是对象")
    try:
        migrated = _convert_unit(unit, location)
    except ValueError as exc:
        logger.warning("%s 无法转换，原样保留并标为待重新规划：%s", location, exc)
        kept = copy.deepcopy(unit)
        kept["needs_replan"] = True
        return kept
    try:
        ReferenceVideoUnit.model_validate(_without_transition(migrated))
    except ValidationError as exc:
        logger.warning("%s 不符合当前单元模型，标为待重新规划：%s", location, exc)
        migrated["needs_replan"] = True
        text = migrated.get("text")
        if isinstance(text, str) and not text.strip():
            # Bodyless units become replanning shells with duration 0.
            migrated["duration_seconds"] = 0
    return migrated


def migrate_reference_script(payload: dict[str, Any], *, location: str) -> dict[str, Any]:
    r"""Pure reference-script conversion; validate/return existing v9 scripts unchanged."""

    units = payload.get("video_units")
    if not isinstance(units, list):
        raise ValueError(f"{location}.video_units 必须是数组")
    migrated = copy.deepcopy(payload)
    migrated["video_units"] = [
        _migrate_script_unit(unit, f"{location}.video_units[{index}]") for index, unit in enumerate(units)
    ]
    return migrated


def migrate_reference_script_plan_draft(payload: dict[str, Any], *, location: str) -> dict[str, Any]:
    r"""Pure plan-draft conversion; validate/return existing v9 drafts unchanged."""

    units = payload.get("units")
    if not isinstance(units, list):
        raise ValueError(f"{location}.units 必须是数组")
    migrated = copy.deepcopy(payload)
    migrated["units"] = [
        _migrate_unit(unit, f"{location}.units[{index}]", ReferenceScriptPlanUnit) for index, unit in enumerate(units)
    ]
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


@contextmanager
def _located(episode: int, file: str) -> Generator[None]:
    r"""Attach location facts to preflight structural violations as migration errors.

    Repair-needed verdicts navigate users/Agents by ``(episode, file)``, unlocatable from message text alone."""

    try:
        yield
    except ProjectMigrationError:
        raise
    except ValueError as exc:
        raise ProjectMigrationError(str(exc), episode=episode, file=file) from exc


def migrate_v8_to_v9(project_dir: Path) -> None:
    r"""Single startup/archive entry point through ``migrate_project_dir``."""

    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return
    project = load_json(project_file)
    if not isinstance(project, dict):
        raise ValueError("project.json 必须是对象")
    if int(project.get("schema_version") or 0) >= _TARGET_VERSION:
        return

    plans: list[tuple[Path, dict[str, Any]]] = []
    if project.get("generation_mode") == "reference_video":
        # Read-only loop; corrupt files precede backups/business writes.
        for script_path, episode in _episode_entries(project_dir, project):
            label = script_path.name
            with _located(episode, label):
                payload = _readable_file(script_path, f"剧本 {label}")
                if payload is not None:
                    plans.append((script_path, migrate_reference_script(payload, location=f"剧本 {label}")))

            draft_path = _reference_units_draft_path(project_dir, episode)
            draft_label = f"第 {episode} 集 {draft_path.name}"
            try:
                draft = _readable_file(draft_path, draft_label)
                if draft is not None:
                    plans.append((draft_path, migrate_reference_script_plan_draft(draft, location=draft_label)))
            except (OSError, ValueError) as exc:
                # Preserve corrupt/unopenable plan drafts unchanged without rejecting projects (ADR 0098 narrows ADR 0064).
                logger.warning("第 %s 集脚本规划草稿无法迁移，原样保留：%s（%s）", episode, draft_path, exc)

    # Back up only after all preflights succeed, completing every backup including project.json before replacements.
    for path, _payload in [*plans, (project_file, project)]:
        ensure_versioned_backup(path, 8)
    for path, payload in plans:
        atomic_write_json(path, payload)

    migrated_project = copy.deepcopy(project)
    migrated_project["schema_version"] = _TARGET_VERSION
    atomic_write_json(project_file, migrated_project)


__all__ = ["migrate_reference_script", "migrate_reference_script_plan_draft", "migrate_v8_to_v9"]
