"""v6→v7: migrate ad/short-film reference-to-video into self-contained ``video_units``.

Produce current units (one ``text`` + planned duration, ADR 0064): concatenate old shot visuals in
order into bodies; derive references/speech ownership from body markers at read time, without
separate arrays. Later v8→v9 is therefore a no-op for these units. Retain transitions until v15→v16
removes them; omit transitions in current-model self-checks.

Resolve legacy ID collisions deterministically without rejecting projects (ADR 0098). Only first
``shot_id`` occurrences are addressable by old indexes; others become new problem units. Reissue
duplicate ``unit_id`` from the second occurrence and mark for replanning, leaving paid artifacts with
the first. Record reidentification as adjusted in reports. Validate units only: preserve units violating
current models unchanged, marked for runtime replanning."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from lib.infra.json_io import atomic_write_json, load_json
from lib.infra.path_safety import safe_join
from lib.project.asset_types import asset_name_comparison_key
from lib.project.project_migration_report import MigrationAdjustment
from lib.project.project_migrations.backups import ensure_versioned_backup
from lib.script.script_models import REFERENCE_UNIT_DURATION_RANGE, ReferenceVideoUnit
from lib.speech.speech_composition import SpeechComposition, SpeechProblemCode, adapt_video_unit

_REFERENCE_TYPES = (
    ("product", "products_in_shot"),
    ("character", "characters_in_shot"),
    ("scene", "scenes"),
    ("prop", "props"),
)
_TRANSITIONS = frozenset({"cut", "fade", "dissolve"})


def _positive_seconds(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _strings(value: object) -> Iterable[str]:
    """Yield nonempty text in legacy field order without inferring or rewriting meaning."""
    if isinstance(value, str):
        if value.strip():
            yield value
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key != "dialogue":
                yield from _strings(item)
        return
    if isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _shot_text(shot: dict[str, Any]) -> str:
    mentions: list[str] = []
    for _asset_type, field in _REFERENCE_TYPES:
        values = shot.get(field)
        if not isinstance(values, list):
            continue
        for raw_name in values:
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            mention = f"@[{asset_name_comparison_key(raw_name)}]"
            if mention not in mentions:
                mentions.append(mention)

    visual = [*_strings(shot.get("image_prompt")), *_strings(shot.get("video_prompt"))]
    first_line = "；".join([*mentions, *visual])
    lines = [first_line] if first_line else []

    video_prompt = shot.get("video_prompt")
    dialogue = video_prompt.get("dialogue") if isinstance(video_prompt, Mapping) else None
    if isinstance(dialogue, list):
        for entry in dialogue:
            if not isinstance(entry, Mapping):
                continue
            line = entry.get("line")
            speaker = entry.get("speaker")
            if not isinstance(line, str) or not line.strip():
                continue
            if isinstance(speaker, str) and speaker.strip():
                lines.append(f"@[{asset_name_comparison_key(speaker)}]：{{{line}}}")
            else:
                # Without usable legacy speakers, preserve source text without fabricating character ownership.
                lines.append(f"{{{line}}}")

    voiceover = shot.get("voiceover_text")
    if isinstance(voiceover, str) and voiceover.strip():
        lines.append(f"{{{voiceover}}}")
    return "\n".join(lines)


def _unit_transition(shots: list[dict[str, Any]]) -> str:
    """Preserve legacy export semantics: inter-unit transitions come from the last valid member shot."""
    if not shots:
        return "cut"
    value = shots[-1].get("transition_to_next")
    return value if isinstance(value, str) and value in _TRANSITIONS else "cut"


def _migration_note(*, unresolved_shot_ids: list[str], overlapping_shot_ids: list[str]) -> str | None:
    """Retain legacy member evidence unrepresentable in self-contained bodies as visible notes."""
    history: dict[str, list[str]] = {}
    if unresolved_shot_ids:
        history["unresolved_legacy_shot_ids"] = unresolved_shot_ids
    if overlapping_shot_ids:
        history["overlapping_legacy_shot_ids"] = overlapping_shot_ids
    if not history:
        return None
    return json.dumps(history, ensure_ascii=False, separators=(",", ":"))


def _unit_from_shots(
    *,
    unit_id: str,
    shots: list[dict[str, Any]],
    generated_assets: object,
    requires_replan: bool,
    note: str | None = None,
) -> dict[str, Any]:
    # Legacy unit boundaries and paid-artifact identities are inseparable; join shot visuals in order into one body.
    text = "\n".join(_shot_text(shot) for shot in shots)
    duration = sum(_positive_seconds(shot.get("duration_seconds")) for shot in shots)
    min_duration, max_duration = REFERENCE_UNIT_DURATION_RANGE
    invalid_duration = bool(text.strip()) and not min_duration <= duration <= max_duration
    if invalid_duration:
        # Markers block generation; structural clamps only retain readable/editable problem units without losing bodies.
        duration = min(max(duration, min_duration), max_duration)

    unit: dict[str, Any] = {
        "unit_id": unit_id,
        "text": text,
        "duration_seconds": duration,
        "transition_to_next": _unit_transition(shots),
        "note": note,
        # Paid artifacts, URIs, and legacy signatures are historical facts; preserve every key unchanged.
        "generated_assets": copy.deepcopy(generated_assets) if isinstance(generated_assets, dict) else {},
    }
    preparation = SpeechComposition.prepare(adapt_video_unit(unit))
    needs_replan = (
        requires_replan
        or invalid_duration
        or any(
            problem.code
            in {
                SpeechProblemCode.MIXED_SPEECH,
                SpeechProblemCode.PARSE_FAILED,
                SpeechProblemCode.EMPTY_SPEAKER,
            }
            for problem in preparation.problems
        )
    )
    if needs_replan:
        unit["needs_replan"] = True
    return unit


def _mark_invalid_units(units: list[Any]) -> None:
    """Self-check each unit with current models; preserve invalid units unchanged and mark replanning, without rejecting scripts.

    Do not validate whole scripts: unrelated legacy dirt (e.g. absent title) must not fail project migration."""
    for unit in units:
        if not isinstance(unit, dict):
            continue
        candidate = {key: value for key, value in unit.items() if key != "transition_to_next"}
        try:
            ReferenceVideoUnit.model_validate(candidate)
        except ValidationError:
            unit["needs_replan"] = True
            text = unit.get("text")
            if isinstance(text, str) and not text.strip():
                # Bodyless units become replanning shells with duration 0.
                unit["duration_seconds"] = 0


def migrate_ad_reference_script(
    payload: dict[str, Any], *, episode: int
) -> tuple[dict[str, Any], tuple[tuple[str, str], ...]]:
    """Pure legacy-ad conversion, returning scripts and changed (old ID, new ID) pairs.

    Return already-converted scripts unchanged for safe interrupted retries."""
    if "video_units" in payload and "shots" not in payload and "reference_units" not in payload:
        existing_units = payload.get("video_units")
        if not isinstance(existing_units, list):
            raise ValueError("video_units 必须是数组")
        migrated = copy.deepcopy(payload)
        _mark_invalid_units(migrated["video_units"])
        return migrated, ()

    raw_shots = payload.get("shots")
    if not isinstance(raw_shots, list):
        raise ValueError("广告/短片的参考生视频旧剧本缺少 shots 数组")
    shots: list[dict[str, Any]] = []
    shot_by_id: dict[str, dict[str, Any]] = {}
    for index, raw_shot in enumerate(raw_shots):
        if not isinstance(raw_shot, dict):
            raise ValueError(f"shots[{index}] 必须是对象")
        shot = copy.deepcopy(raw_shot)
        shots.append(shot)
        shot_id = shot.get("shot_id")
        # First duplicate shot_id wins; old indexes cannot reference others, which become separate problem units below.
        if isinstance(shot_id, str) and shot_id and shot_id not in shot_by_id:
            shot_by_id[shot_id] = shot
    renumbered: list[tuple[str, str]] = []

    raw_units = payload.get("reference_units")
    units: list[dict[str, Any]] = []
    if raw_units is None or raw_units == []:
        for ordinal, shot in enumerate(shots, start=1):
            units.append(
                _unit_from_shots(
                    unit_id=f"E{episode}U{ordinal}",
                    shots=[shot],
                    generated_assets={},
                    requires_replan=False,
                )
            )
    else:
        if not isinstance(raw_units, list):
            raise ValueError("reference_units 必须是数组或 null")
        covered_shots: set[int] = set()
        used_unit_ids: set[str] = set()
        membership_counts: dict[str, int] = {}
        prepared_units: list[tuple[dict[str, Any], str, list[str]]] = []
        duplicate_unit_indexes: list[int] = []
        for index, raw_unit in enumerate(raw_units):
            if not isinstance(raw_unit, dict):
                raise ValueError(f"reference_units[{index}] 必须是对象")
            unit_id = raw_unit.get("unit_id")
            if not isinstance(unit_id, str) or not unit_id:
                raise ValueError(f"reference_units[{index}].unit_id 必须是非空字符串")
            if unit_id in used_unit_ids:
                duplicate_unit_indexes.append(len(prepared_units))
            used_unit_ids.add(unit_id)
            shot_ids = raw_unit.get("shot_ids")
            if not isinstance(shot_ids, list):
                raise ValueError(f"reference_units[{index}].shot_ids 必须是数组")
            normalized_shot_ids: list[str] = []
            for member_index, shot_id in enumerate(shot_ids):
                if not isinstance(shot_id, str) or not shot_id:
                    raise ValueError(f"reference_units[{index}].shot_ids[{member_index}] 必须是非空字符串")
                normalized_shot_ids.append(shot_id)
                if shot_id in shot_by_id:
                    membership_counts[shot_id] = membership_counts.get(shot_id, 0) + 1
            prepared_units.append((raw_unit, unit_id, normalized_shot_ids))

        next_ordinal = 1

        def fresh_unit_id() -> str:
            nonlocal next_ordinal
            while f"E{episode}U{next_ordinal}" in used_unit_ids:
                next_ordinal += 1
            unit_id = f"E{episode}U{next_ordinal}"
            used_unit_ids.add(unit_id)
            next_ordinal += 1
            return unit_id

        for position, (raw_unit, unit_id, shot_ids) in enumerate(prepared_units):
            duplicate = position in duplicate_unit_indexes
            if duplicate:
                # First same-ID unit keeps paid artifacts; reissue this unit without artifacts and mark replanning.
                new_id = fresh_unit_id()
                renumbered.append((unit_id, new_id))
                unit_id = new_id
            members: list[dict[str, Any]] = []
            unresolved_shot_ids: list[str] = []
            overlapping_shot_ids: list[str] = []
            for shot_id in shot_ids:
                if shot_id in shot_by_id:
                    members.append(shot_by_id[shot_id])
                    covered_shots.add(id(shot_by_id[shot_id]))
                    if membership_counts[shot_id] > 1:
                        overlapping_shot_ids.append(shot_id)
                else:
                    unresolved_shot_ids.append(shot_id)
            units.append(
                _unit_from_shots(
                    unit_id=unit_id,
                    shots=members,
                    generated_assets={} if duplicate else raw_unit.get("generated_assets"),
                    requires_replan=duplicate
                    or not shot_ids
                    or bool(unresolved_shot_ids)
                    or bool(overlapping_shot_ids),
                    note=_migration_note(
                        unresolved_shot_ids=unresolved_shot_ids,
                        overlapping_shot_ids=overlapping_shot_ids,
                    ),
                )
            )

        # Nonempty old indexes may lag authoritative shots. Preserve provable indexed identities/paid artifacts,
        # then wrap uncovered shots individually as problem units, preventing generation before arrangement confirmation and avoiding lost shots content.
        for shot in shots:
            if id(shot) in covered_shots:
                continue
            unit_id = fresh_unit_id()
            shot_id = shot.get("shot_id")
            if isinstance(shot_id, str) and shot_id and shot_by_id.get(shot_id) is not shot:
                renumbered.append((shot_id, unit_id))
            units.append(
                _unit_from_shots(
                    unit_id=unit_id,
                    shots=[shot],
                    generated_assets={},
                    requires_replan=True,
                )
            )

    migrated = copy.deepcopy(payload)
    migrated.pop("shots", None)
    migrated.pop("reference_units", None)
    migrated["video_units"] = units
    _mark_invalid_units(units)
    migrated["duration_seconds"] = sum(_positive_seconds(unit.get("duration_seconds")) for unit in units)
    return migrated, tuple(renumbered)


def _script_paths(project_dir: Path, project: dict[str, Any]) -> list[tuple[Path, int]]:
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
        if path.is_symlink():
            raise ValueError(f"剧本文件不是普通文件: {script_file}")
        if not path.exists():
            continue
        if not path.is_file():
            raise ValueError(f"剧本文件不是普通文件: {script_file}")
        result.append((path, episode))
    return result


def migrate_v6_to_v7(project_dir: Path, *, record: list[MigrationAdjustment] | None = None) -> None:
    """Preflight all scripts, atomically replace files individually, then commit ``project.json`` version.

    Append changed IDs to ``record`` immediately after each script persists. If later steps fail, retries
    cannot rediscover changed IDs in converted scripts; incomplete reports must carry them forward."""
    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return
    project = load_json(project_file)
    if not isinstance(project, dict):
        raise ValueError("project.json 必须是对象")
    if int(project.get("schema_version") or 0) >= 7:
        return

    target_route = project.get("content_mode") == "ad" and project.get("generation_mode") == "reference_video"
    plans: list[tuple[Path, dict[str, Any], list[MigrationAdjustment]]] = []
    if target_route:
        # Entire loop is read-only; corrupt files precede any backup/business write.
        for path, episode in _script_paths(project_dir, project):
            payload = load_json(path)
            if not isinstance(payload, dict):
                raise ValueError(f"剧本必须是对象: {path.relative_to(project_dir)}")
            migrated, renumbered = migrate_ad_reference_script(payload, episode=episode)
            adjustments = [
                MigrationAdjustment(
                    code="item_id_reassigned",
                    episode=episode,
                    file=path.relative_to(project_dir).as_posix(),
                    params={"from": old_id, "to": new_id},
                )
                for old_id, new_id in renumbered
            ]
            plans.append((path, migrated, adjustments))

        # Back up only after all preflights succeed, completing all backups before replacements.
        for path, _payload, _adjustments in plans:
            ensure_versioned_backup(path, 6)
        for path, payload, adjustments in plans:
            atomic_write_json(path, payload)
            if record is not None:
                record.extend(adjustments)

    migrated_project = copy.deepcopy(project)
    migrated_project["schema_version"] = 7
    atomic_write_json(project_file, migrated_project)


__all__ = ["migrate_ad_reference_script", "migrate_v6_to_v7"]
