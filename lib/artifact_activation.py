"""Eager Artifact Manifest target-state planning and activation.

The schema migration and archive-import boundary both call this module.  It is
the only place that reconstructs a complete manifest from canonical project
state; ordinary readers never repair or infer entries on first access.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from lib import script_review
from lib.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasis,
    ArtifactComparison,
    ArtifactKey,
    ArtifactManifest,
    ArtifactManifestAdapter,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.artifact_provenance import build_ad_episode_script_basis, build_episode_script_basis, build_step1_basis
from lib.artifact_version_provenance import parse_typed_audio_settings, parse_typed_media_version_target
from lib.asset_types import ASSET_SPECS, asset_name_comparison_key
from lib.grid.layout import grid_aspect_ratio_for
from lib.grid.models import GridGeneration
from lib.json_io import atomic_write_json
from lib.media_artifact_currency import build_current_audio_artifact_basis, build_current_video_artifact_basis
from lib.resource_paths import resource_relative_path
from lib.script_editor import resolve_items
from lib.storyboard_sequence import get_storyboard_items
from lib.version_manager import VersionManager
from lib.visual_artifact_provenance import (
    GridStoryboardVisual,
    VisualReference,
    build_asset_sheet_visual_basis,
    build_grid_composite_visual_basis,
    build_grid_member_storyboard_visual_basis,
    build_storyboard_image_visual_basis,
)

TARGET_SCHEMA_VERSION = 8
_GRID_RECORD_RE = re.compile(r"grid_[0-9a-f]{12}\.json\Z")


@dataclass(frozen=True, slots=True)
class ArtifactTargetStatePlan:
    """Immutable preflight result consumed by the activation commit."""

    entries: Mapping[ArtifactKey, ArtifactManifestEntry]
    project: Mapping[str, Any]
    project_bytes: bytes
    dependency_bytes: Mapping[Path, bytes]
    script_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _EpisodeBinding:
    episode: int
    script_file: str


@dataclass(frozen=True, slots=True)
class _EpisodeState:
    episode: int
    script_file: str
    script_path: Path
    script: dict[str, Any]
    items: tuple[dict[str, Any], ...]
    id_field: str
    kind: str


@dataclass(frozen=True, slots=True)
class _FormalStep1State:
    artifact_path: str
    content: object


class _Planner:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir.resolve(strict=True)
        self.adapter = ProjectArtifactManifestAdapter(self.project_dir)
        self.project_path = self.project_dir / "project.json"
        self.project_bytes = self._read_required_control_file("project.json", "project.json")
        project = self._parse_json(self.project_bytes, "project.json")
        if not isinstance(project, dict):
            raise ValueError("project.json must contain an object")
        self.project = cast(dict[str, Any], project)
        self.dependencies: dict[Path, bytes] = {}
        self.script_paths: list[Path] = []
        self.bindings: list[_EpisodeBinding] = []
        self.episodes: list[_EpisodeState] = []
        self._bindings_loaded = False
        self._episodes_loaded = False
        self.entries: dict[ArtifactKey, ArtifactManifestEntry] = {}
        self._versions: dict[str, Any] | None = None
        self._activation_mode = False

    def plan(self) -> ArtifactTargetStatePlan:
        schema = self.project.get("schema_version")
        if type(schema) is not int or schema not in {TARGET_SCHEMA_VERSION - 1, TARGET_SCHEMA_VERSION}:
            raise ValueError(f"artifact activation requires schema 7 or 8, got {schema!r}")

        self._activation_mode = True
        # Parsing the existing sidecar is part of preflight.  A corrupt manifest
        # is a real migration error, not permission to overwrite unknown state.
        self.adapter.get_entry(ArtifactKey.episode_script(1))
        self._load_episodes()
        self._plan_assets()
        self._plan_structured_content()
        self._plan_grids()
        self._plan_storyboards()
        self._plan_typed_media()
        return ArtifactTargetStatePlan(
            entries=dict(self.entries),
            project=dict(self.project),
            project_bytes=self.project_bytes,
            dependency_bytes=dict(self.dependencies),
            script_paths=tuple(self.script_paths),
        )

    def resolve_key(self, key: ArtifactKey) -> ArtifactManifestEntry | None:
        """Resolve one post-commit target through the same canonical planner."""

        schema = self.project.get("schema_version")
        if schema != TARGET_SCHEMA_VERSION:
            raise RuntimeError("Artifact Manifest is not activated for this project schema")
        kind = key.kind.value
        if kind == "asset-sheet":
            self._plan_assets()
        elif kind == "episode-step1":
            self._load_episode_bindings()
            episode_number = cast(int, key.components[0])
            binding = next((candidate for candidate in self.bindings if candidate.episode == episode_number), None)
            if binding is not None:
                self._plan_one_step1(binding)
        elif kind == "episode-script":
            self._load_episodes()
            self._plan_structured_content()
        elif kind == "episode-grid":
            self._load_episodes()
            self._plan_grids()
        elif kind == "episode-storyboard":
            self._load_episodes()
            self._plan_grids()
            self._plan_storyboards()
        elif kind in {"episode-video", "episode-audio"}:
            self._load_episodes()
            self._plan_typed_media()
        return self.entries.get(key)

    def _load_episode_bindings(self) -> None:
        if self._bindings_loaded:
            return
        raw_episodes = self.project.get("episodes")
        if raw_episodes is None:
            raw_episodes = []
        if not isinstance(raw_episodes, list):
            raise ValueError("project episodes must be an array")
        seen_episodes: set[int] = set()
        seen_scripts: set[str] = set()
        for index, raw in enumerate(raw_episodes):
            if not isinstance(raw, Mapping):
                raise ValueError(f"project episode {index} must be an object")
            episode = raw.get("episode")
            script_file = raw.get("script_file")
            if type(episode) is not int or episode < 1 or not isinstance(script_file, str) or not script_file:
                raise ValueError(f"project episode {index} has an invalid binding")
            normalized = _normalize_script_binding(script_file)
            if episode in seen_episodes or normalized in seen_scripts:
                raise ValueError("project episode bindings must be unique")
            seen_episodes.add(episode)
            seen_scripts.add(normalized)
            self.bindings.append(_EpisodeBinding(episode=episode, script_file=normalized))
        self._bindings_loaded = True

    def _load_episodes(self) -> None:
        if self._episodes_loaded:
            return
        self._load_episode_bindings()
        for binding in self.bindings:
            observation = self.adapter.inspect_artifact(binding.script_file)
            if observation.blocker is not None:
                raise ArtifactManifestError(observation.blocker.detail)
            if not observation.present:
                continue
            raw_script = self._read_dependency(binding.script_file, "episode script")
            parsed = self._parse_json(raw_script, f"episode script {binding.script_file}")
            if not isinstance(parsed, dict):
                raise ValueError(f"episode script {binding.script_file} must contain an object")
            script = cast(dict[str, Any], parsed)
            if script.get("episode") != binding.episode:
                raise ValueError(f"episode script {binding.script_file} does not match its project binding")
            items, id_field, kind = resolve_items(script)
            seen_ids: set[str] = set()
            typed_items: list[dict[str, Any]] = []
            for item_index, item in enumerate(items):
                if not isinstance(item, dict):
                    raise ValueError(f"episode script {binding.script_file} item {item_index} must be an object")
                resource_id = item.get(id_field)
                if not isinstance(resource_id, str) or not resource_id:
                    raise ValueError(f"episode script {binding.script_file} item {item_index} has no identity")
                if resource_id in seen_ids:
                    raise ValueError(
                        f"episode script {binding.script_file} has duplicate resource identity {resource_id!r}"
                    )
                seen_ids.add(resource_id)
                typed_items.append(item)
            script_path = self.project_dir / binding.script_file
            self.script_paths.append(script_path)
            self.episodes.append(
                _EpisodeState(
                    episode=binding.episode,
                    script_file=binding.script_file,
                    script_path=script_path,
                    script=script,
                    items=tuple(typed_items),
                    id_field=id_field,
                    kind=kind,
                )
            )
        self._episodes_loaded = True

    def _plan_assets(self) -> None:
        style = self.project.get("style", "")
        style_description = self.project.get("style_description", "")
        if not isinstance(style, str) or not isinstance(style_description, str):
            raise ValueError("project visual style fields must be strings")
        for asset_type, spec in ASSET_SPECS.items():
            bucket = self.project.get(spec.bucket_key, {})
            if not isinstance(bucket, Mapping):
                raise ValueError(f"project asset bucket {spec.bucket_key} must be an object")
            normalized_names: set[str] = set()
            for raw_name, raw_entry in bucket.items():
                if not isinstance(raw_name, str) or not isinstance(raw_entry, Mapping):
                    raise ValueError(f"project asset bucket {spec.bucket_key} is malformed")
                name = asset_name_comparison_key(raw_name)
                if not name or name in normalized_names:
                    raise ValueError("project asset identities must be unique after normalization")
                normalized_names.add(name)
                artifact_path = raw_entry.get(spec.sheet_field)
                if not isinstance(artifact_path, str) or not artifact_path:
                    continue
                description = raw_entry.get("description")
                if not isinstance(description, str) or not description.strip():
                    continue
                references = self._asset_sheet_references(asset_type, name, raw_entry)
                if references is None:
                    continue
                try:
                    basis = build_asset_sheet_visual_basis(
                        asset_type=asset_type,
                        asset_id=name,
                        description=description,
                        style=style,
                        style_description=style_description,
                        aspect_ratio="16:9",
                        references=references,
                    )
                except (OSError, TypeError, ValueError):
                    continue
                self._add_if_present(ArtifactKey.asset_sheet(asset_type, name), artifact_path, basis)

    def _asset_sheet_references(
        self,
        asset_type: str,
        asset_id: str,
        entry: Mapping[str, Any],
    ) -> tuple[VisualReference, ...] | None:
        raw_paths: list[tuple[str, str]] = []
        if asset_type == "character":
            value = entry.get("reference_image")
            if value not in (None, "") and not isinstance(value, str):
                return None
            if isinstance(value, str) and value:
                raw_paths.append((value, "original"))
        elif asset_type == "product":
            values = entry.get("reference_images", [])
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                return None
            raw_paths.extend((value, "original") for value in values if value)
        references: list[VisualReference] = []
        for relative_path, kind in raw_paths:
            path = self._safe_present_path(relative_path)
            if path is None:
                return None
            references.append(
                VisualReference(
                    path=path,
                    role="source",
                    logical_type=asset_type,
                    logical_id=asset_id,
                    kind=kind,
                )
            )
        return tuple(references)

    def _plan_structured_content(self) -> None:
        if self.project.get("content_mode") == "ad":
            for episode in self.episodes:
                try:
                    script_basis = build_ad_episode_script_basis(episode.episode, project=self.project)
                except (TypeError, ValueError):
                    continue
                self._add_if_present(
                    ArtifactKey.episode_script(episode.episode),
                    episode.script_file,
                    script_basis,
                )
            return
        if self.project.get("content_mode") not in {"narration", "drama"}:
            return
        step1_by_episode = {
            binding.episode: step1 for binding in self.bindings if (step1 := self._plan_one_step1(binding)) is not None
        }
        for episode in self.episodes:
            step1 = step1_by_episode.get(episode.episode)
            if step1 is None:
                continue
            try:
                script_basis = build_episode_script_basis(step1.content, project=self.project)
            except (TypeError, ValueError):
                continue
            self._add_if_present(
                ArtifactKey.episode_script(episode.episode),
                episode.script_file,
                script_basis,
            )

    def _plan_one_step1(self, binding: _EpisodeBinding) -> _FormalStep1State | None:
        if self.project.get("content_mode") not in {"narration", "drama"}:
            return None
        step1_path = script_review.step1_path(self.project_dir, self.project, binding.episode)
        if step1_path is None:
            return None
        step1_rel = step1_path.relative_to(self.project_dir).as_posix()
        observation = self.adapter.inspect_artifact(step1_rel)
        if observation.blocker is not None or not observation.present:
            return None
        step1_raw = self._read_dependency(step1_rel, "formal step1")
        step1_content = self._parse_json(step1_raw, f"formal step1 {step1_rel}")
        source_rel = f"source/episode_{binding.episode}.txt"
        source_observation = self.adapter.inspect_artifact(source_rel)
        if source_observation.blocker is None and source_observation.present:
            source_raw = self._read_dependency(source_rel, "episode source")
            try:
                source_content = source_raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"episode source {source_rel} is not UTF-8") from exc
            try:
                step1_basis = build_step1_basis(source_content, project=self.project)
            except (TypeError, ValueError):
                pass
            else:
                self._add_if_present(ArtifactKey.episode_step1(binding.episode), step1_rel, step1_basis)
        return _FormalStep1State(artifact_path=step1_rel, content=step1_content)

    def _plan_storyboards(self) -> None:
        if self.project.get("generation_mode") != "storyboard":
            return
        style = self.project.get("style", "")
        aspect_ratio = self.project.get("aspect_ratio") or "9:16"
        if not isinstance(style, str) or not isinstance(aspect_ratio, str):
            raise ValueError("project storyboard style and aspect ratio must be strings")
        for episode in self.episodes:
            storyboard_items, id_field, char_field, scene_field, prop_field = get_storyboard_items(episode.script)
            grid_members = self._grid_members_by_resource(episode.episode)
            for index, item in enumerate(storyboard_items):
                resource_id = str(item[id_field])
                assets = item.get("generated_assets")
                if not isinstance(assets, Mapping) or item.get("needs_replan") is True:
                    continue
                artifact_path = assets.get("storyboard_image")
                if not isinstance(artifact_path, str) or not artifact_path:
                    continue
                grid_target = grid_members.get(resource_id)
                if assets.get("grid_id") is not None or assets.get("grid_cell_index") is not None:
                    if grid_target is not None:
                        key, basis = grid_target
                        self._add_if_present(key, artifact_path, basis)
                    continue
                references = self._storyboard_references(
                    item,
                    char_field=char_field,
                    scene_field=scene_field,
                    prop_field=prop_field,
                )
                if references is None:
                    continue
                if index and not item.get("segment_break"):
                    previous_item = storyboard_items[index - 1]
                    previous_id = str(previous_item.get(id_field) or "")
                    previous_assets = previous_item.get("generated_assets")
                    previous_rel = (
                        previous_assets.get("storyboard_image") if isinstance(previous_assets, Mapping) else None
                    )
                    if previous_rel not in (None, "") and not isinstance(previous_rel, str):
                        continue
                    if isinstance(previous_rel, str) and previous_rel:
                        previous_path = self._safe_present_path(previous_rel)
                        if previous_path is None:
                            continue
                        references.append(
                            VisualReference(
                                path=previous_path,
                                role="previous_storyboard",
                                logical_type="storyboard",
                                logical_id=previous_id,
                            )
                        )
                try:
                    basis = build_storyboard_image_visual_basis(
                        resource_id=resource_id,
                        image_prompt=item.get("image_prompt"),
                        style=style,
                        aspect_ratio=aspect_ratio,
                        references=references,
                    )
                except (OSError, TypeError, ValueError):
                    continue
                self._add_if_present(ArtifactKey.episode_storyboard(episode.episode, resource_id), artifact_path, basis)

    def _storyboard_references(
        self,
        item: Mapping[str, Any],
        *,
        char_field: str | None,
        scene_field: str,
        prop_field: str,
    ) -> list[VisualReference] | None:
        references: list[VisualReference] = []
        seen_paths: set[str] = set()
        valid = True

        def append_asset(asset_type: str, name: object, *, include_originals: bool = False) -> None:
            nonlocal valid
            if not isinstance(name, str):
                valid = False
                return
            spec = ASSET_SPECS[asset_type]
            bucket = self.project.get(spec.bucket_key)
            if not isinstance(bucket, Mapping):
                valid = False
                return
            entry = next(
                (
                    candidate
                    for raw_name, candidate in bucket.items()
                    if isinstance(raw_name, str)
                    and asset_name_comparison_key(raw_name) == asset_name_comparison_key(name)
                    and isinstance(candidate, Mapping)
                ),
                None,
            )
            if not isinstance(entry, Mapping):
                valid = False
                return
            paths: list[tuple[object, str]] = [(entry.get(spec.sheet_field), "sheet")]
            if include_originals:
                originals = entry.get("reference_images", [])
                if not isinstance(originals, list):
                    valid = False
                    return
                paths.extend((value, "original") for value in originals)
            for raw_path, variant in paths:
                if raw_path in (None, ""):
                    continue
                if not isinstance(raw_path, str):
                    valid = False
                    return
                if raw_path in seen_paths:
                    continue
                path = self._safe_present_path(raw_path)
                if path is None:
                    valid = False
                    return
                seen_paths.add(raw_path)
                references.append(
                    VisualReference(
                        path=path,
                        role="asset_sheet" if variant == "sheet" else "source",
                        logical_type=asset_type,
                        logical_id=name,
                        kind=variant,
                    )
                )

        products = item.get("products_in_shot", [])
        if isinstance(products, Sequence) and not isinstance(products, (str, bytes)):
            for name in products:
                append_asset("product", name, include_originals=True)
        else:
            valid = False
        for asset_type, field in (("character", char_field), ("scene", scene_field), ("prop", prop_field)):
            values = item.get(field, []) if field is not None else []
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                for name in values:
                    append_asset(asset_type, name)
            else:
                valid = False
        return references if valid else None

    def _plan_grids(self) -> None:
        for grid in self._load_grid_records():
            episode = next(
                (
                    candidate
                    for candidate in self.episodes
                    if candidate.episode == grid.episode
                    and candidate.script_file == _normalize_script_binding(grid.script_file)
                ),
                None,
            )
            if episode is None or grid.status != "completed" or not grid.grid_image_path:
                continue
            if grid.grid_image_path != resource_relative_path("grids", grid.id):
                continue
            members = self._grid_visual_members(grid, episode)
            references = self._grid_references(grid)
            if members is None or references is None:
                continue
            member_ratio = grid.video_aspect_ratio or self.project.get("aspect_ratio") or "9:16"
            if not isinstance(member_ratio, str):
                continue
            try:
                basis = build_grid_composite_visual_basis(
                    group_id=grid.id,
                    members=members,
                    rows=grid.rows,
                    columns=grid.cols,
                    style=str(self.project.get("style") or ""),
                    grid_aspect_ratio=grid_aspect_ratio_for(grid.rows, grid.cols, member_ratio),
                    references=references,
                )
            except (OSError, TypeError, ValueError):
                continue
            self._add_if_present(ArtifactKey.episode_grid(grid.episode, grid.id), grid.grid_image_path, basis)

    def _grid_members_by_resource(
        self,
        episode_number: int,
    ) -> dict[str, tuple[ArtifactKey, ArtifactBasis]]:
        result: dict[str, tuple[ArtifactKey, ArtifactBasis]] = {}
        for grid in self._load_grid_records():
            if grid.episode != episode_number or not grid.split_at or not grid.grid_image_path:
                continue
            episode = next(
                (
                    candidate
                    for candidate in self.episodes
                    if candidate.episode == grid.episode
                    and candidate.script_file == _normalize_script_binding(grid.script_file)
                ),
                None,
            )
            if episode is None:
                continue
            members = self._grid_visual_members(grid, episode)
            references = self._grid_references(grid)
            composite_path = self._safe_present_path(grid.grid_image_path)
            if members is None or references is None or composite_path is None:
                continue
            member_ratio = grid.video_aspect_ratio or self.project.get("aspect_ratio") or "9:16"
            if not isinstance(member_ratio, str):
                continue
            by_id = {str(item[episode.id_field]): item for item in episode.items}
            for frame in grid.frame_chain:
                resource_id = frame.next_scene_id
                if frame.frame_type not in {"first", "transition"} or not resource_id or frame.index >= len(members):
                    continue
                item = by_id.get(resource_id)
                if item is None:
                    continue
                assets = item.get("generated_assets")
                if (
                    not isinstance(assets, Mapping)
                    or assets.get("grid_id") != grid.id
                    or assets.get("grid_cell_index") != frame.index
                    or item.get("needs_replan") is True
                ):
                    continue
                try:
                    basis = build_grid_member_storyboard_visual_basis(
                        group_id=grid.id,
                        members=members,
                        cell_index=frame.index,
                        composite_image=composite_path,
                        rows=grid.rows,
                        columns=grid.cols,
                        style=str(self.project.get("style") or ""),
                        member_aspect_ratio=member_ratio,
                        references=references,
                    )
                except (OSError, TypeError, ValueError):
                    continue
                result[resource_id] = (
                    ArtifactKey.episode_storyboard(grid.episode, resource_id),
                    basis,
                )
        return result

    def _grid_visual_members(
        self,
        grid: GridGeneration,
        episode: _EpisodeState,
    ) -> tuple[GridStoryboardVisual, ...] | None:
        by_id = {str(item[episode.id_field]): item for item in episode.items}
        if len(set(grid.scene_ids)) != len(grid.scene_ids):
            return None
        members: list[GridStoryboardVisual] = []
        for resource_id in grid.scene_ids:
            item = by_id.get(resource_id)
            if item is None:
                return None
            try:
                members.append(
                    GridStoryboardVisual(
                        resource_id=resource_id,
                        image_prompt=item.get("image_prompt"),
                        video_prompt=item.get("video_prompt"),
                    )
                )
            except (TypeError, ValueError):
                return None
        return tuple(members)

    def _grid_references(self, grid: GridGeneration) -> tuple[VisualReference, ...] | None:
        references: list[VisualReference] = []
        for raw in grid.reference_images or []:
            path = self._safe_present_path(raw.path)
            if path is None:
                return None
            try:
                references.append(
                    VisualReference(
                        path=path,
                        role="asset_sheet",
                        logical_type=raw.ref_type,
                        logical_id=raw.name,
                        kind="sheet",
                    )
                )
            except (TypeError, ValueError):
                return None
        return tuple(references)

    def _load_grid_records(self) -> tuple[GridGeneration, ...]:
        cached = getattr(self, "_grids", None)
        if cached is not None:
            return cast(tuple[GridGeneration, ...], cached)
        grids_dir = self.project_dir / "grids"
        if not grids_dir.exists():
            grids: tuple[GridGeneration, ...] = ()
            self._grids = grids
            return grids
        if grids_dir.is_symlink() or not grids_dir.is_dir():
            raise ValueError("grids control directory is not a safe directory")
        loaded: list[GridGeneration] = []
        for path in sorted(grids_dir.iterdir()):
            if not _GRID_RECORD_RE.fullmatch(path.name):
                continue
            rel = path.relative_to(self.project_dir).as_posix()
            raw = self._read_dependency(rel, "grid record")
            parsed = self._parse_json(raw, f"grid record {rel}")
            if not isinstance(parsed, dict):
                raise ValueError(f"grid record {rel} must contain an object")
            try:
                grid = GridGeneration.from_dict(parsed)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"grid record {rel} is malformed") from exc
            if f"{grid.id}.json" != path.name:
                raise ValueError(f"grid record {rel} does not match its filename")
            loaded.append(grid)
        result = tuple(loaded)
        self._grids = result
        return result

    def _plan_typed_media(self) -> None:
        versions = self._load_versions()
        for episode in self.episodes:
            for item in episode.items:
                if item.get("needs_replan") is True:
                    continue
                resource_id = str(item[episode.id_field])
                assets = item.get("generated_assets")
                if not isinstance(assets, Mapping):
                    continue
                audio_path = assets.get("narration_audio")
                if isinstance(audio_path, str) and audio_path:
                    self._plan_one_typed_media(
                        versions,
                        episode=episode,
                        item=item,
                        resource_id=resource_id,
                        resource_type="audio",
                        artifact_path=audio_path,
                        key=ArtifactKey.episode_audio(episode.episode, resource_id),
                    )
                video_path = assets.get("video_clip")
                if isinstance(video_path, str) and video_path:
                    resource_type = "reference_videos" if episode.kind == "video_units" else "videos"
                    self._plan_one_typed_media(
                        versions,
                        episode=episode,
                        item=item,
                        resource_id=resource_id,
                        resource_type=resource_type,
                        artifact_path=video_path,
                        key=ArtifactKey.episode_video(episode.episode, resource_id),
                    )

    def _plan_one_typed_media(
        self,
        versions: Mapping[str, Any],
        *,
        episode: _EpisodeState,
        item: Mapping[str, Any],
        resource_id: str,
        resource_type: str,
        artifact_path: str,
        key: ArtifactKey,
    ) -> None:
        if artifact_path != resource_relative_path(resource_type, resource_id):
            return
        resource_bucket = versions.get(resource_type)
        resource = resource_bucket.get(resource_id) if isinstance(resource_bucket, Mapping) else None
        if not isinstance(resource, Mapping):
            return
        selected_version = resource.get("current_version")
        records = resource.get("versions")
        if type(selected_version) is not int or not isinstance(records, list):
            return
        selected = [
            record for record in records if isinstance(record, Mapping) and record.get("version") == selected_version
        ]
        if len(selected) != 1:
            return
        record = selected[0]
        try:
            target = parse_typed_media_version_target(resource_type, record)
        except (TypeError, ValueError):
            return
        if target.episode != episode.episode or _normalize_script_binding(target.script_file) != episode.script_file:
            return
        snapshot_rel = record.get("file")
        if not isinstance(snapshot_rel, str):
            return
        artifact = self._safe_present_path(artifact_path)
        snapshot = self._safe_present_path(snapshot_rel)
        if artifact is None or snapshot is None:
            return
        try:
            if artifact.read_bytes() != snapshot.read_bytes():
                return
        except OSError:
            return
        try:
            if resource_type == "audio":
                current_basis = build_current_audio_artifact_basis(
                    item=item,
                    skeleton_kind=episode.kind,
                    version_record=record,
                )
            else:
                current_basis = build_current_video_artifact_basis(
                    project_path=self.project_dir,
                    project=self.project,
                    script=episode.script,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    versions=VersionManager(self.project_dir),
                    version_metadata=record,
                    current_tts_settings=self._selected_audio_settings(versions, episode, resource_id),
                    resolve_audio_manifest_entry=self.entries.get if self._activation_mode else None,
                )
        except (KeyError, OSError, TypeError, ValueError):
            return
        if current_basis is None or (self._activation_mode and current_basis != target.basis):
            return
        self.entries[key] = ArtifactManifestEntry(
            artifact_path=artifact_path,
            basis_digest=current_basis.digest,
        )

    @staticmethod
    def _selected_audio_settings(
        versions: Mapping[str, Any],
        episode: _EpisodeState,
        resource_id: str,
    ):
        bucket = versions.get("audio")
        resource = bucket.get(resource_id) if isinstance(bucket, Mapping) else None
        if not isinstance(resource, Mapping):
            return None
        selected_version = resource.get("current_version")
        records = resource.get("versions")
        if type(selected_version) is not int or not isinstance(records, list):
            return None
        selected = [
            record for record in records if isinstance(record, Mapping) and record.get("version") == selected_version
        ]
        if len(selected) != 1:
            return None
        record = selected[0]
        try:
            target = parse_typed_media_version_target("audio", record)
            settings = parse_typed_audio_settings(record)
        except (TypeError, ValueError):
            return None
        if target.episode != episode.episode or _normalize_script_binding(target.script_file) != episode.script_file:
            return None
        return settings

    def _load_versions(self) -> Mapping[str, Any]:
        if self._versions is not None:
            return self._versions
        relative = "versions/versions.json"
        observation = self.adapter.inspect_artifact(relative)
        if observation.blocker is not None:
            raise ArtifactManifestError(observation.blocker.detail)
        if not observation.present:
            self._versions = {}
            return self._versions
        raw = self._read_dependency(relative, "version metadata")
        parsed = self._parse_json(raw, "version metadata")
        if not isinstance(parsed, dict):
            raise ValueError("version metadata must contain an object")
        self._versions = parsed
        return parsed

    def _add_if_present(self, key: ArtifactKey, artifact_path: str, basis: ArtifactBasis) -> None:
        observation = self.adapter.inspect_artifact(artifact_path)
        if observation.blocker is not None or not observation.present:
            return
        entry = ArtifactManifestEntry(
            artifact_path=observation.artifact_path,
            basis_digest=basis.digest,
        )
        existing = self.entries.get(key)
        if existing is not None and existing != entry:
            raise ValueError(f"multiple canonical targets claim artifact key {key.encode()}")
        self.entries[key] = entry

    def _safe_present_path(self, relative_path: str) -> Path | None:
        observation = self.adapter.inspect_artifact(relative_path)
        if observation.blocker is not None or not observation.present:
            return None
        return self.project_dir.joinpath(*Path(observation.artifact_path).parts)

    def _read_required_control_file(self, relative_path: str, label: str) -> bytes:
        observation = self.adapter.inspect_artifact(relative_path)
        if observation.blocker is not None:
            raise ArtifactManifestError(observation.blocker.detail)
        if not observation.present:
            raise ValueError(f"{label} is missing")
        path = self.project_dir.joinpath(*Path(observation.artifact_path).parts)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot read {label}") from exc

    def _read_dependency(self, relative_path: str, label: str) -> bytes:
        observation = self.adapter.inspect_artifact(relative_path)
        if observation.blocker is not None:
            raise ArtifactManifestError(observation.blocker.detail)
        if not observation.present:
            raise ValueError(f"{label} is missing: {relative_path}")
        path = self.project_dir.joinpath(*Path(observation.artifact_path).parts)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot read {label}: {relative_path}") from exc
        self.dependencies[path] = raw
        return raw

    @staticmethod
    def _parse_json(raw: bytes, label: str) -> object:
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"{label} is not valid UTF-8 JSON") from exc


def plan_artifact_target_state(project_dir: Path) -> ArtifactTargetStatePlan:
    """Perform the complete read-only activation preflight."""

    return _Planner(project_dir).plan()


def activate_artifact_target_state(project_dir: Path, *, bump_schema: bool) -> bool:
    """Commit one complete target state, optionally advancing schema last."""

    plan = plan_artifact_target_state(project_dir)
    current_schema = plan.project.get("schema_version")
    if bump_schema and current_schema != TARGET_SCHEMA_VERSION - 1:
        raise ValueError("schema bump requires a v7 project")
    if not bump_schema and current_schema != TARGET_SCHEMA_VERSION:
        raise ValueError("schema-preserving activation requires a v8 project")

    _assert_preflight_unchanged(project_dir, plan)
    if bump_schema:
        _backup_activation_inputs(project_dir, plan)
        _assert_preflight_unchanged(project_dir, plan)
    changed = ProjectArtifactManifestAdapter(project_dir).replace_entries_atomically(plan.entries)
    if bump_schema:
        _assert_project_unchanged(project_dir, plan.project_bytes)
        _commit_schema_version(project_dir, plan.project)
        return True
    return changed


def ensure_imported_artifact_target_state(project_dir: Path) -> bool:
    """Eagerly materialize the v8 sidecar at the archive staging boundary."""

    raw = (project_dir / "project.json").read_bytes()
    try:
        project = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("project.json is not valid UTF-8 JSON") from exc
    if not isinstance(project, Mapping) or project.get("schema_version") != TARGET_SCHEMA_VERSION:
        raise ValueError("archive activation requires a schema-v8 project")
    return activate_artifact_target_state(project_dir, bump_schema=False)


def resolve_current_artifact_target(project_dir: Path, key: ArtifactKey) -> ArtifactManifestEntry | None:
    """Resolve one formal post-commit target without repairing any other key."""

    return _Planner(project_dir).resolve_key(key)


class ArtifactCurrencyResolver:
    """Side-effect-free runtime comparison against canonical target state."""

    def __init__(self, project_dir: Path) -> None:
        self._planner = _Planner(project_dir)
        if self._planner.project.get("schema_version") != TARGET_SCHEMA_VERSION:
            raise RuntimeError("Artifact Manifest is not activated for this project schema")
        # Validate the sidecar once even when a workflow phase has no artifacts
        # to compare.  A corrupt active manifest is a blocker, never an empty
        # target state or permission to fall back to filesystem existence.
        self._planner.adapter.get_entry(ArtifactKey.episode_script(1))
        self._manifest = ArtifactManifest(self._planner.adapter)

    def compare(self, key: ArtifactKey, *, artifact_path: str) -> ArtifactComparison:
        expected = self._planner.resolve_key(key)
        return self._manifest.compare_entry(key, artifact_path=artifact_path, expected=expected)


def active_artifact_currency_resolver(
    project_dir: Path,
    project: Mapping[str, Any],
) -> ArtifactCurrencyResolver | None:
    """Return the active resolver, preserving legacy selection before schema 8."""

    return ArtifactCurrencyResolver(project_dir) if project.get("schema_version") == TARGET_SCHEMA_VERSION else None


def artifact_is_usable(
    resolver: ArtifactCurrencyResolver | None,
    key: ArtifactKey | None,
    artifact_path: object,
) -> bool:
    """Classify selection eligibility without treating stale artifacts as missing.

    Before activation, callers retain their historical metadata-pointer behavior.
    Once active, only Manifest current/stale entries are usable; a blocked
    comparison fails loud so a damaged sidecar cannot trigger paid regeneration.
    """

    if not isinstance(artifact_path, str) or not artifact_path:
        return False
    if resolver is None:
        return True
    if key is None:
        raise ValueError("an ArtifactKey is required for active currency")
    comparison = resolver.compare(key, artifact_path=artifact_path)
    if comparison.status is ArtifactStatus.BLOCKED:
        assert comparison.blocker is not None
        raise ArtifactManifestError(comparison.blocker.detail)
    return comparison.status in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}


def register_current_artifact(
    project_dir: Path,
    key: ArtifactKey,
    *,
    adapter: ArtifactManifestAdapter | None = None,
) -> bool:
    """Register a just-committed formal artifact through the shared resolver."""

    entry = resolve_current_artifact_target(project_dir, key)
    if entry is None:
        raise ValueError(f"formal artifact target is not provable: {key.encode()}")
    storage = adapter or ProjectArtifactManifestAdapter(project_dir)
    return ArtifactManifest(storage).register_entry_transactionally(key, entry)


def register_current_artifact_if_provable(
    project_dir: Path,
    key: ArtifactKey,
    *,
    adapter: ArtifactManifestAdapter | None = None,
) -> bool:
    """Refresh a write-time claim, removing it when provenance is unprovable."""

    storage = adapter or ProjectArtifactManifestAdapter(project_dir)
    manifest = ArtifactManifest(storage)
    entry = resolve_current_artifact_target(project_dir, key)
    if entry is None:
        return manifest.forget_entry_transactionally(key)
    return manifest.register_entry_transactionally(key, entry)


def artifact_key_for_resource(
    project_dir: Path,
    *,
    resource_type: str,
    resource_id: str,
    script_file: str | None = None,
) -> ArtifactKey:
    """Map a formal write target to its typed manifest identity."""

    for asset_type, spec in ASSET_SPECS.items():
        if resource_type == spec.bucket_key:
            return ArtifactKey.asset_sheet(asset_type, resource_id)
    planner = _Planner(project_dir)
    if planner.project.get("schema_version") != TARGET_SCHEMA_VERSION:
        raise RuntimeError("Artifact Manifest is not activated for this project schema")
    planner._load_episodes()
    if resource_type == "grids":
        grid = next((candidate for candidate in planner._load_grid_records() if candidate.id == resource_id), None)
        if grid is None:
            raise KeyError(resource_id)
        return ArtifactKey.episode_grid(grid.episode, resource_id)
    if script_file is None and resource_type == "storyboards":
        matches = [
            candidate
            for candidate in planner.episodes
            if any(str(item.get(candidate.id_field)) == resource_id for item in candidate.items)
        ]
        if len(matches) != 1:
            raise ValueError("storyboard identity does not resolve to exactly one episode binding")
        episode = matches[0]
    else:
        if script_file is None:
            raise ValueError(f"script_file is required for {resource_type}")
        normalized = _normalize_script_binding(script_file)
        episode = next((candidate for candidate in planner.episodes if candidate.script_file == normalized), None)
    if episode is None:
        raise ValueError("formal resource no longer matches an episode script binding")
    if resource_type == "storyboards":
        return ArtifactKey.episode_storyboard(episode.episode, resource_id)
    if resource_type in {"videos", "reference_videos"}:
        return ArtifactKey.episode_video(episode.episode, resource_id)
    if resource_type == "audio":
        return ArtifactKey.episode_audio(episode.episode, resource_id)
    raise ValueError(f"unsupported formal artifact resource type: {resource_type}")


def register_current_resource_artifact(
    project_dir: Path,
    *,
    resource_type: str,
    resource_id: str,
    script_file: str | None = None,
) -> bool:
    """Register a successful formal commit when its target basis is provable."""

    if not _artifact_manifest_is_active(project_dir):
        return False

    key = artifact_key_for_resource(
        project_dir,
        resource_type=resource_type,
        resource_id=resource_id,
        script_file=script_file,
    )
    return register_current_artifact_if_provable(project_dir, key)


def forget_current_resource_artifact(
    project_dir: Path,
    *,
    resource_type: str,
    resource_id: str,
    script_file: str | None = None,
) -> bool:
    """Remove a currency claim after an unprovable formal replacement."""

    if not _artifact_manifest_is_active(project_dir):
        return False

    key = artifact_key_for_resource(
        project_dir,
        resource_type=resource_type,
        resource_id=resource_id,
        script_file=script_file,
    )
    return ArtifactManifest(ProjectArtifactManifestAdapter(project_dir)).forget_entry_transactionally(key)


def _artifact_manifest_is_active(project_dir: Path) -> bool:
    """Return whether runtime write-through is enabled by the schema gate."""

    project_path = project_dir / "project.json"
    try:
        raw = project_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    project = json.loads(raw)
    if not isinstance(project, Mapping):
        raise ValueError("project.json must contain an object")
    return project.get("schema_version") == TARGET_SCHEMA_VERSION


def _assert_preflight_unchanged(project_dir: Path, plan: ArtifactTargetStatePlan) -> None:
    _assert_project_unchanged(project_dir, plan.project_bytes)
    for path, expected in plan.dependency_bytes.items():
        try:
            current = path.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"artifact activation dependency changed after preflight: {path}") from exc
        if current != expected:
            raise RuntimeError(f"artifact activation dependency changed after preflight: {path}")


def _assert_project_unchanged(project_dir: Path, expected: bytes) -> None:
    try:
        current = (project_dir / "project.json").read_bytes()
    except OSError as exc:
        raise RuntimeError("project.json changed after artifact activation preflight") from exc
    if current != expected:
        raise RuntimeError("project.json changed after artifact activation preflight")


def _backup_activation_inputs(project_dir: Path, plan: ArtifactTargetStatePlan) -> None:
    candidates = [project_dir / "project.json", *plan.script_paths]
    manifest = project_dir / MANIFEST_FILENAME
    if manifest.exists():
        candidates.append(manifest)
    stamp = time.time_ns()
    for source in candidates:
        pattern = f"{source.name}.bak.v7-*"
        if any(source.parent.glob(pattern)):
            continue
        backup = source.with_name(f"{source.name}.bak.v7-{stamp}")
        shutil.copy2(source, backup)


def _commit_schema_version(project_dir: Path, project: Mapping[str, Any]) -> None:
    updated = dict(project)
    updated["schema_version"] = TARGET_SCHEMA_VERSION
    atomic_write_json(project_dir / "project.json", updated)


def _normalize_script_binding(value: str) -> str:
    normalized = value.replace("\\", "/")
    if normalized.startswith("scripts/"):
        normalized = normalized.removeprefix("scripts/")
    if not normalized or "/" in normalized or normalized in {".", ".."}:
        raise ValueError(f"invalid episode script binding: {value!r}")
    return f"scripts/{normalized}"


__all__ = [
    "ArtifactCurrencyResolver",
    "ArtifactTargetStatePlan",
    "TARGET_SCHEMA_VERSION",
    "activate_artifact_target_state",
    "active_artifact_currency_resolver",
    "artifact_is_usable",
    "artifact_key_for_resource",
    "ensure_imported_artifact_target_state",
    "forget_current_resource_artifact",
    "plan_artifact_target_state",
    "register_current_artifact",
    "register_current_artifact_if_provable",
    "register_current_resource_artifact",
    "resolve_current_artifact_target",
]
