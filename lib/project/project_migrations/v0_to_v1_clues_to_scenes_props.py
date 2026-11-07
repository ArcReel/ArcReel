"""v0→v1: split clues into scenes + props, remove importance, and cascade script JSON."""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path
from typing import Any

from lib.infra.json_io import atomic_write_json, load_json


def _split_clues(clues: dict[str, dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    scenes: dict[str, dict] = {}
    props: dict[str, dict] = {}
    for name, data in clues.items():
        clue_type = (data.get("type") or "prop").lower()
        new_item: dict[str, Any] = {
            "description": data.get("description", ""),
        }
        # Preserve generated-image paths under renamed fields.
        sheet = data.get("clue_sheet")
        if clue_type == "location":
            if sheet:
                new_item["scene_sheet"] = sheet.replace("clues/", "scenes/", 1)
            scenes[name] = new_item
        else:
            if sheet:
                new_item["prop_sheet"] = sheet.replace("clues/", "props/", 1)
            props[name] = new_item
    return scenes, props


def _relocate_clue_files(project_dir: Path, old_clues: dict[str, dict]) -> None:
    clues_dir = project_dir / "clues"
    if not clues_dir.exists():
        return
    scenes_dir = project_dir / "scenes"
    props_dir = project_dir / "props"
    scenes_dir.mkdir(exist_ok=True)
    props_dir.mkdir(exist_ok=True)

    for name, data in old_clues.items():
        clue_type = (data.get("type") or "prop").lower()
        target = scenes_dir if clue_type == "location" else props_dir
        for ext in ("png", "jpg", "jpeg", "webp"):
            src = clues_dir / f"{name}.{ext}"
            if src.exists():
                shutil.move(str(src), str(target / f"{name}.{ext}"))

    # Remove empty clues directories, retaining unknown remnants to avoid data loss;
    # never return on failure: versions/clues migration below is independent of directory removability
    # and must always continue, or advancing schema_version to 1 permanently loses version files.
    # Retain nonempty directories without treating them as migration failures.
    with contextlib.suppress(OSError):
        clues_dir.rmdir()  # hard-delete: infra -- Empty directories after migration relocation.

    # Route versions/clues by original clue types too.
    versions_clues = project_dir / "versions" / "clues"
    if versions_clues.exists():
        for name, data in old_clues.items():
            clue_type = (data.get("type") or "prop").lower()
            target_versions = project_dir / "versions" / ("scenes" if clue_type == "location" else "props")
            target_versions.mkdir(parents=True, exist_ok=True)
            for file in versions_clues.glob(f"{name}*"):
                shutil.move(str(file), str(target_versions / file.name))
        # Retain nonempty versions/clues containing unclassified files without failing migration.
        with contextlib.suppress(OSError):
            versions_clues.rmdir()  # hard-delete: infra -- Empty directories after migration relocation.


def _migrate_scripts(project_dir: Path, old_clues: dict[str, dict]) -> None:
    """Split clues[] in every script scene/segment into scenes[] + props[]."""
    scripts_dir = project_dir / "scripts"
    if not scripts_dir.exists():
        return

    def kind(clue_name: str) -> str:
        data = old_clues.get(clue_name, {})
        return "scene" if (data.get("type") or "prop").lower() == "location" else "prop"

    for sp in scripts_dir.glob("*.json"):
        try:
            data = load_json(sp)
        except Exception:
            continue
        if (data.get("schema_version") or 0) >= 1:
            continue

        # Legacy script clue references: clues / clues_in_segment / clues_in_scene.
        # All may coexist; merge before splitting into scenes/props.
        legacy_fields = ("clues", "clues_in_segment", "clues_in_scene")
        for bucket_key in ("scenes", "segments"):
            items = data.get(bucket_key) or []
            for item in items:
                merged: list[str] = []
                seen: set[str] = set()
                found_any = False
                for lf in legacy_fields:
                    if lf in item:
                        found_any = True
                        for nm in item.pop(lf) or []:
                            if nm not in seen:
                                seen.add(nm)
                                merged.append(nm)
                if not found_any:
                    continue
                scenes_list: list[str] = []
                props_list: list[str] = []
                for nm in merged:
                    (scenes_list if kind(nm) == "scene" else props_list).append(nm)
                item["scenes"] = scenes_list
                item["props"] = props_list

        data["schema_version"] = 1
        atomic_write_json(sp, data)


def _reconstruct_old_clues_from_v1(data: dict) -> dict[str, dict]:
    """Reconstruct old_clues from v1 for healing partially migrated projects.

    scene → type=location, prop → type=prop. Removed clue_sheet cannot be recovered, but
    _relocate_clue_files routes only by name/type and does not need it."""
    old: dict[str, dict] = {}
    for name in data.get("scenes") or {}:
        old[name] = {"type": "location"}
    for name in data.get("props") or {}:
        old[name] = {"type": "prop"}
    return old


def migrate_v0_to_v1(project_dir: Path) -> None:
    """Idempotent v0→v1 migration with partial-migration healing.

    Move files, cascade scripts, then advance schema_version. Crashes keep version 0 until final commit,
    so startup retries rather than skipping missing images as already upgraded."""
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    data = load_json(pj)
    # or 0: explicit null equals absent (v0); None >= 1 would raise TypeError.
    current_version = data.get("schema_version") or 0

    # Heal schema_version>=1 with residual clues/ by rerunning file/script migration.
    if current_version >= 1:
        clues_dir = project_dir / "clues"
        if clues_dir.is_dir() and any(clues_dir.iterdir()):
            old_clues = _reconstruct_old_clues_from_v1(data)
            _relocate_clue_files(project_dir, old_clues)
            _migrate_scripts(project_dir, old_clues)
        return

    old_clues: dict[str, dict] = data.get("clues") or {}

    # 1. Move files first; failures keep schema_version 0 for restart retries.
    _relocate_clue_files(project_dir, old_clues)

    # 2. Cascade scripts.
    _migrate_scripts(project_dir, old_clues)

    # 3. Atomically update project.json last, using schema_version as commit marker.
    # Rewrite scenes/props only with actual clues data; otherwise preserve existing fields,
    # avoiding clearing projects with missing schema_version, no clues, but existing scenes/props.
    if old_clues:
        scenes, props = _split_clues(old_clues)
        data["scenes"] = scenes
        data["props"] = props
    else:
        data.setdefault("scenes", {})
        data.setdefault("props", {})
    data.pop("clues", None)
    data["schema_version"] = 1
    atomic_write_json(pj, data)
