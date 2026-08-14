"""Eager Artifact Manifest target-state planning and activation.

The schema migration and archive-import boundary both call this module.  It is
the only place that reconstructs a complete manifest from canonical project
state; ordinary readers never repair or infer entries on first access.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from lib import script_review
from lib.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactComparison,
    ArtifactEntryRekeyPlan,
    ArtifactKey,
    ArtifactKind,
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
from lib.formal_write import project_metadata_lock
from lib.grid.layout import grid_aspect_ratio_for
from lib.grid.models import GridGeneration
from lib.json_io import atomic_write_bytes, atomic_write_json
from lib.media_artifact_currency import build_current_audio_artifact_basis, build_current_video_artifact_basis
from lib.narration_delivery import POST_PRODUCTION, USE_TTS
from lib.resource_paths import resource_relative_path
from lib.script_editor import resolve_items
from lib.speech_artifact_provenance import RenditionVariant, SelectedMediaEvidence, media_content_digest
from lib.speech_composition import (
    SpeechComposition,
    SpeechFieldLocation,
    SpeechInputUtterance,
    SpeechPreparation,
    SpeechUnitSnapshot,
    admit_script_unit,
)
from lib.speech_presentation import (
    PresentationMedia,
    materialize_speech_presentation,
    presentation_artifact_paths,
)
from lib.storyboard_sequence import StoryboardImageUnavailable, get_storyboard_items, resolve_storyboard_video_inputs
from lib.version_manager import VersionManager
from lib.visual_artifact_provenance import (
    GridStoryboardVisual,
    VisualReference,
    build_asset_sheet_visual_basis,
    build_grid_composite_visual_basis,
    build_grid_member_storyboard_visual_basis,
    build_storyboard_image_visual_basis,
    visual_file_digest,
)

TARGET_SCHEMA_VERSION = 8
_GRID_RECORD_RE = re.compile(r"grid_[0-9a-f]{12}\.json\Z")
_EPISODE_RESOURCE_KINDS = frozenset(
    {
        ArtifactKind.EPISODE_STORYBOARD,
        ArtifactKind.EPISODE_VIDEO,
        ArtifactKind.EPISODE_AUDIO,
        ArtifactKind.EPISODE_SUBTITLE,
        ArtifactKind.EPISODE_PRESENTATION,
    }
)
_FORMAL_IMAGE_KINDS = frozenset(
    {
        ArtifactKind.ASSET_SHEET,
        ArtifactKind.EPISODE_GRID,
        ArtifactKind.EPISODE_STORYBOARD,
    }
)


@dataclass(frozen=True, slots=True)
class ArtifactTargetStatePlan:
    """Immutable preflight result consumed by the activation commit."""

    entries: Mapping[ArtifactKey, ArtifactManifestEntry]
    project: Mapping[str, Any]
    project_bytes: bytes
    dependency_bytes: Mapping[Path, bytes]
    dependency_digests: Mapping[Path, str]
    script_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class ArtifactRegistrationReceipt:
    """A task-local current claim that can be rolled back if cancellation wins."""

    adapter: ProjectArtifactManifestAdapter | None
    key: ArtifactKey | None
    registered: ArtifactManifestEntry | None
    previous: ArtifactManifestEntry | None
    changed: bool = False

    def compensate_cancelled(self) -> None:
        if not self.changed or self.adapter is None or self.key is None or self.registered is None:
            return
        self.adapter.replace_entry_if_matches(
            self.key,
            expected=self.registered,
            replacement=self.previous,
        )


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


@dataclass(frozen=True, slots=True)
class _PersistedPresentationProof:
    frozen_subtitle_basis: ArtifactBasis
    frozen_presentation_basis: ArtifactBasis
    current_subtitle_basis: ArtifactBasis | None
    current_presentation_basis: ArtifactBasis | None


class _Planner:
    def __init__(
        self,
        project_dir: Path,
        *,
        episode_scope: int | None = None,
        project_bytes: bytes | None = None,
    ) -> None:
        self.project_dir = project_dir.resolve(strict=True)
        self.adapter = ProjectArtifactManifestAdapter(self.project_dir)
        self.project_path = self.project_dir / "project.json"
        if episode_scope is not None and (type(episode_scope) is not int or episode_scope < 1):
            raise ValueError("episode scope must be a positive integer or null")
        self.episode_scope = episode_scope
        self.project_bytes = (
            self._read_required_control_file("project.json", "project.json")
            if project_bytes is None
            else bytes(project_bytes)
        )
        project = self._parse_json(self.project_bytes, "project.json")
        if not isinstance(project, dict):
            raise ValueError("project.json must contain an object")
        self.project = cast(dict[str, Any], project)
        self.dependencies: dict[Path, bytes] = {}
        self.dependency_digests: dict[Path, str] = {}
        self.script_paths: list[Path] = []
        self.bindings: list[_EpisodeBinding] = []
        self.episodes: list[_EpisodeState] = []
        self._bindings_loaded = False
        self._episodes_loaded = False
        self.entries: dict[ArtifactKey, ArtifactManifestEntry] = {}
        self.bases: dict[ArtifactKey, ArtifactBasis] = {}
        self._path_owners: dict[str, ArtifactKey] = {}
        self._versions: dict[str, Any] | None = None
        self._activation_mode = False
        self._planned: set[str] = set()

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
        self._plan_persisted_presentations()
        return ArtifactTargetStatePlan(
            entries=dict(self.entries),
            project=dict(self.project),
            project_bytes=self.project_bytes,
            dependency_bytes=dict(self.dependencies),
            dependency_digests=dict(self.dependency_digests),
            script_paths=tuple(self.script_paths),
        )

    def resolve_key(self, key: ArtifactKey) -> ArtifactManifestEntry | None:
        """Resolve one post-commit target through the same canonical planner."""

        schema = self.project.get("schema_version")
        if schema != TARGET_SCHEMA_VERSION:
            raise RuntimeError("Artifact Manifest is not activated for this project schema")
        self._plan_key(key)
        return self.entries.get(key)

    def resolve_basis(self, key: ArtifactKey) -> ArtifactBasis | None:
        """Resolve one canonical basis without requiring its formal output yet."""

        schema = self.project.get("schema_version")
        if schema != TARGET_SCHEMA_VERSION:
            raise RuntimeError("Artifact Manifest is not activated for this project schema")
        self._plan_key(key)
        return self.bases.get(key)

    def _plan_key(self, key: ArtifactKey) -> None:
        """Run the canonical planning slice shared by target and basis resolution."""

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
        elif kind in {"episode-subtitle", "episode-presentation"}:
            self._load_episodes()
            self._plan_persisted_presentations()

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
            if self.episode_scope is not None and binding.episode != self.episode_scope:
                continue
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
        if "assets" in self._planned:
            return
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
        self._planned.add("assets")

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
                self._visual_reference(
                    path=path,
                    role="source",
                    logical_type=asset_type,
                    logical_id=asset_id,
                    kind=kind,
                )
            )
        return tuple(references)

    def _plan_structured_content(self) -> None:
        if "structured-content" in self._planned:
            return
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
            self._planned.add("structured-content")
            return
        if self.project.get("content_mode") not in {"narration", "drama"}:
            self._planned.add("structured-content")
            return
        step1_by_episode = {
            binding.episode: step1
            for binding in self.bindings
            if (self.episode_scope is None or binding.episode == self.episode_scope)
            and (step1 := self._plan_one_step1(binding)) is not None
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
        self._planned.add("structured-content")

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
        if "storyboards" in self._planned:
            return
        if self.project.get("generation_mode") != "storyboard":
            self._planned.add("storyboards")
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
                            self._visual_reference(
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
        self._planned.add("storyboards")

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
                    self._visual_reference(
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
        if "grids" in self._planned:
            return
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
        self._planned.add("grids")

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
                    composite_digest = self._track_dependency_digest(composite_path)
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
                        source_composite_digest=composite_digest,
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
                    self._visual_reference(
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
        if "typed-media" in self._planned:
            return
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
        self._planned.add("typed-media")

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
        if not VersionManager.is_managed_snapshot_path(resource_type, snapshot_rel):
            return
        artifact = self._safe_present_path(artifact_path)
        snapshot = self._safe_present_path(cast(str, snapshot_rel))
        if artifact is None or snapshot is None:
            return
        try:
            if artifact.samefile(snapshot):
                return
            artifact_digest = visual_file_digest(artifact)
            snapshot_digest = visual_file_digest(snapshot)
        except OSError:
            return
        if artifact_digest != snapshot_digest:
            return
        self._remember_dependency_digest(artifact, artifact_digest)
        self._remember_dependency_digest(snapshot, snapshot_digest)
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
                    allow_legacy_storyboard_same_name=False,
                )
        except (KeyError, OSError, TypeError, ValueError):
            return
        if current_basis is None or (self._activation_mode and current_basis != target.basis):
            return
        self.entries[key] = ArtifactManifestEntry(
            artifact_path=artifact_path,
            basis_digest=current_basis.digest,
        )

    def _plan_persisted_presentations(self) -> None:
        """Rebuild only complete, internally provable persisted presentation pairs."""

        if "persisted-presentations" in self._planned:
            return
        # Typed media must be planned first.  Persisted presentation files carry
        # observed duration/content evidence, but only a selected managed media
        # snapshot that independently proves its canonical formal file may anchor
        # that evidence.  This is not a same-name filesystem fallback.
        self._plan_typed_media()
        for episode in self.episodes:
            for item in episode.items:
                if item.get("needs_replan") is True:
                    continue
                resource_id = str(item[episode.id_field])
                for variant in (POST_PRODUCTION, USE_TTS):
                    subtitle_path, presentation_path = presentation_artifact_paths(
                        episode.episode,
                        resource_id,
                        variant,
                    )
                    subtitle = self._read_optional_json_artifact(subtitle_path)
                    presentation = self._read_optional_json_artifact(presentation_path)
                    if subtitle is None or presentation is None:
                        continue
                    proof = self._prove_persisted_presentation(
                        episode=episode,
                        item=item,
                        resource_id=resource_id,
                        variant=variant,
                        subtitle_path=subtitle_path,
                        presentation_path=presentation_path,
                        subtitle=subtitle,
                        presentation=presentation,
                    )
                    if proof is None:
                        continue
                    subtitle_basis = (
                        proof.frozen_subtitle_basis if self._activation_mode else proof.current_subtitle_basis
                    )
                    presentation_basis = (
                        proof.frozen_presentation_basis if self._activation_mode else proof.current_presentation_basis
                    )
                    if subtitle_basis is not None:
                        self.entries[ArtifactKey.episode_subtitle(episode.episode, resource_id, variant)] = (
                            ArtifactManifestEntry(
                                artifact_path=subtitle_path,
                                basis_digest=subtitle_basis.digest,
                            )
                        )
                    if presentation_basis is not None:
                        self.entries[ArtifactKey.episode_presentation(episode.episode, resource_id, variant)] = (
                            ArtifactManifestEntry(
                                artifact_path=presentation_path,
                                basis_digest=presentation_basis.digest,
                            )
                        )
        self._planned.add("persisted-presentations")

    def _prove_persisted_presentation(
        self,
        *,
        episode: _EpisodeState,
        item: Mapping[str, Any],
        resource_id: str,
        variant: RenditionVariant,
        subtitle_path: str,
        presentation_path: str,
        subtitle: Mapping[str, Any],
        presentation: Mapping[str, Any],
    ) -> _PersistedPresentationProof | None:
        """Validate a frozen typed presentation and derive its current basis."""

        resource_type = "reference_videos" if episode.kind == "video_units" else "videos"
        video_pair = self._presentation_media_pair(
            presentation.get("video"),
            episode=episode,
            resource_id=resource_id,
            resource_type=resource_type,
        )
        if video_pair is None:
            return None
        frozen_video, current_video = video_pair
        raw_audio = presentation.get("narration_audio")
        audio_pair = (
            self._presentation_media_pair(
                raw_audio,
                episode=episode,
                resource_id=resource_id,
                resource_type="audio",
            )
            if raw_audio is not None
            else None
        )
        if (variant == USE_TTS) != (audio_pair is not None):
            return None
        frozen_audio, current_audio = audio_pair if audio_pair is not None else (None, None)
        frozen_preparation = self._persisted_speech_preparation(resource_id, subtitle, presentation)
        raw_audio_enabled = presentation.get("video")
        provider_audio_enabled = (
            raw_audio_enabled.get("audio_enabled") if isinstance(raw_audio_enabled, Mapping) else None
        )
        if frozen_preparation is None or not isinstance(provider_audio_enabled, bool):
            return None
        transition = presentation.get("transition_to_next")
        if not isinstance(transition, str):
            return None
        try:
            frozen = materialize_speech_presentation(
                frozen_preparation,
                variant=variant,
                video=frozen_video,
                narration_audio=frozen_audio,
                provider_audio_enabled=provider_audio_enabled,
                transition_to_next=transition,
            )
        except (TypeError, ValueError):
            return None
        expected_presentation = {
            "episode": episode.episode,
            "resource_type": resource_type,
            "script_file": Path(episode.script_file).name,
            "transition_to_next": transition,
            "subtitle_artifact_path": subtitle_path,
            "presentation_artifact_path": presentation_path,
            "persisted": True,
            **frozen.to_dict(),
        }
        if dict(subtitle) != frozen.subtitle_artifact_dict() or dict(presentation) != expected_presentation:
            return None

        current_subtitle: ArtifactBasis | None = None
        current_presentation: ArtifactBasis | None = None
        admission = admit_script_unit(episode.kind, item)
        if admission.allowed:
            live_transition = item.get("transition_to_next")
            current_transition = live_transition if isinstance(live_transition, str) else "cut"
            try:
                current = materialize_speech_presentation(
                    admission.preparation,
                    variant=variant,
                    video=current_video,
                    narration_audio=current_audio,
                    provider_audio_enabled=provider_audio_enabled,
                    transition_to_next=current_transition,
                )
            except (TypeError, ValueError):
                pass
            else:
                current_subtitle = current.subtitle_basis
                current_presentation = current.presentation_basis
        return _PersistedPresentationProof(
            frozen_subtitle_basis=frozen.subtitle_basis,
            frozen_presentation_basis=frozen.presentation_basis,
            current_subtitle_basis=current_subtitle,
            current_presentation_basis=current_presentation,
        )

    def _presentation_media_pair(
        self,
        raw: object,
        *,
        episode: _EpisodeState,
        resource_id: str,
        resource_type: str,
    ) -> tuple[PresentationMedia, PresentationMedia] | None:
        """Prove one selected media snapshot and expose frozen/current currency."""

        if not isinstance(raw, Mapping) or raw.get("selection") != "current":
            return None
        key = (
            ArtifactKey.episode_audio(episode.episode, resource_id)
            if resource_type == "audio"
            else ArtifactKey.episode_video(episode.episode, resource_id)
        )
        planned = self.entries.get(key)
        if planned is None or planned.artifact_path != resource_relative_path(resource_type, resource_id):
            return None
        versions = self._load_versions()
        bucket = versions.get(resource_type)
        resource = bucket.get(resource_id) if isinstance(bucket, Mapping) else None
        selected_version = resource.get("current_version") if isinstance(resource, Mapping) else None
        records = resource.get("versions") if isinstance(resource, Mapping) else None
        if type(selected_version) is not int or not isinstance(records, list) or raw.get("version") != selected_version:
            return None
        selected = [
            record for record in records if isinstance(record, Mapping) and record.get("version") == selected_version
        ]
        if len(selected) != 1:
            return None
        record = selected[0]
        snapshot_path = record.get("file")
        if (
            not VersionManager.is_managed_snapshot_path(resource_type, snapshot_path)
            or raw.get("artifact_path") != snapshot_path
        ):
            return None
        try:
            target = parse_typed_media_version_target(resource_type, record)
            embedded_basis = ArtifactBasisDescriptor.from_dict(raw.get("basis"))
        except (TypeError, ValueError):
            return None
        if (
            target.episode != episode.episode
            or _normalize_script_binding(target.script_file) != episode.script_file
            or embedded_basis != target.basis
        ):
            return None
        snapshot = self._safe_present_path(cast(str, snapshot_path))
        artifact = self._safe_present_path(planned.artifact_path)
        if snapshot is None or artifact is None:
            return None
        try:
            if snapshot.samefile(artifact) or snapshot.read_bytes() != artifact.read_bytes():
                return None
            content_digest = media_content_digest(snapshot)
            evidence = SelectedMediaEvidence(
                basis=embedded_basis,
                content_digest=cast(str, raw.get("content_digest")),
                actual_duration_seconds=cast(float, raw.get("actual_duration_seconds")),
            )
        except (OSError, TypeError, ValueError):
            return None
        if evidence.content_digest != content_digest:
            return None
        frozen_currency = raw.get("currency")
        if frozen_currency not in {"current", "stale"}:
            return None
        frozen = PresentationMedia(
            artifact_path=cast(str, snapshot_path),
            version=selected_version,
            selection="current",
            currency=cast(Any, frozen_currency),
            evidence=evidence,
        )
        current = PresentationMedia(
            artifact_path=frozen.artifact_path,
            version=frozen.version,
            selection=frozen.selection,
            currency="current" if planned.basis_digest == target.basis.digest else "stale",
            evidence=evidence,
        )
        return frozen, current

    @staticmethod
    def _persisted_speech_preparation(
        resource_id: str,
        subtitle: Mapping[str, Any],
        presentation: Mapping[str, Any],
    ) -> SpeechPreparation | None:
        """Reconstruct only the speech facts actually frozen into subtitle cues."""

        raw_cues = subtitle.get("cues")
        if not isinstance(raw_cues, list):
            return None
        entries: list[SpeechInputUtterance] = []
        for index, cue in enumerate(raw_cues):
            if not isinstance(cue, Mapping):
                return None
            owner = cue.get("owner")
            text = cue.get("text")
            speaker = cue.get("speaker")
            if owner == "narrator":
                if speaker is not None:
                    return None
                speaker_required = False
            elif owner == "character":
                if not isinstance(speaker, str) or not speaker.strip():
                    return None
                speaker_required = True
            else:
                return None
            if not isinstance(text, str) or not text.strip():
                return None
            entries.append(
                SpeechInputUtterance(
                    text=text,
                    speaker=cast(str | None, speaker),
                    speaker_required=speaker_required,
                    location=SpeechFieldLocation(("cues", index)),
                )
            )
        preparation = SpeechComposition.prepare(SpeechUnitSnapshot(resource_id, tuple(entries)))
        mode = presentation.get("speech_mode")
        if preparation.problems or preparation.mode is None or preparation.mode.value != mode:
            return None
        return preparation

    def _read_optional_json_artifact(self, relative_path: str) -> Mapping[str, Any] | None:
        path = self._safe_present_path(relative_path)
        if path is None:
            return None
        try:
            raw = path.read_bytes()
            parsed = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return None
        if not isinstance(parsed, Mapping):
            return None
        self.dependencies[path] = raw
        return cast(Mapping[str, Any], parsed)

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
        existing_basis = self.bases.get(key)
        if existing_basis is not None and existing_basis != basis:
            raise ValueError(f"multiple canonical bases claim artifact key {key.encode()}")
        self.bases[key] = basis
        observation = self.adapter.inspect_artifact(artifact_path)
        if observation.blocker is not None or not observation.present:
            return
        owner = self._path_owners.get(observation.artifact_path)
        if owner is not None and owner != key:
            raise ValueError(
                "formal artifact path is claimed by multiple keys: "
                f"{observation.artifact_path} ({owner.encode()}, {key.encode()})"
            )
        self._path_owners[observation.artifact_path] = key
        if key.kind in _FORMAL_IMAGE_KINDS:
            self._track_dependency_digest(
                self.project_dir.joinpath(*Path(observation.artifact_path).parts),
            )
        entry = ArtifactManifestEntry(
            artifact_path=observation.artifact_path,
            basis_digest=basis.digest,
        )
        existing = self.entries.get(key)
        if existing is not None and existing != entry:
            raise ValueError(f"multiple canonical targets claim artifact key {key.encode()}")
        self.entries[key] = entry

    def _visual_reference(
        self,
        *,
        path: Path,
        role: str,
        logical_type: str | None = None,
        logical_id: str | None = None,
        kind: str | None = None,
    ) -> VisualReference:
        """Freeze visual evidence once and reuse it for the activation stability gate."""

        return VisualReference(
            path=path,
            role=role,
            logical_type=logical_type,
            logical_id=logical_id,
            kind=kind,
            content_digest=self._track_dependency_digest(path),
        )

    def _track_dependency_digest(self, path: Path) -> str:
        try:
            digest = visual_file_digest(path)
        except OSError as exc:
            raise ValueError(f"cannot read artifact activation dependency: {path}") from exc
        self._remember_dependency_digest(path, digest)
        return digest

    def _remember_dependency_digest(self, path: Path, digest: str) -> None:
        """Record an already-observed file digest for the final stability gate."""

        previous = self.dependency_digests.setdefault(path, digest)
        if previous != digest:
            raise RuntimeError(f"artifact activation dependency changed during preflight: {path}")

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
    adapter = ProjectArtifactManifestAdapter(project_dir)
    if bump_schema:
        with project_metadata_lock(project_dir):
            _assert_preflight_unchanged(project_dir, plan)
            previous_entries = adapter.snapshot_entries()
            changed = adapter.replace_entries_atomically(plan.entries)
            try:
                _assert_preflight_unchanged(project_dir, plan)
            except BaseException as original_error:
                if changed:
                    try:
                        restored = adapter.replace_snapshot_if_matches_atomically(
                            expected=plan.entries,
                            replacement=previous_entries,
                        )
                        if not restored and adapter.snapshot_entries() != previous_entries:
                            raise ArtifactManifestError(
                                "artifact manifest changed concurrently after activation commit"
                            )
                    except BaseException as rollback_error:
                        rollback_error.__cause__ = original_error
                        raise RuntimeError(
                            "artifact activation dependency drifted and Manifest rollback was incomplete"
                        ) from rollback_error
                raise
            _commit_schema_version(project_dir, plan.project)
            return True
    changed = adapter.replace_entries_atomically(plan.entries)
    return changed


def ensure_imported_artifact_target_state(
    project_dir: Path,
    *,
    preserved_entries: Mapping[ArtifactKey, ArtifactManifestEntry] | None = None,
) -> bool:
    """Eagerly materialize the v8 sidecar at the archive staging boundary.

    Official exports carry the complete source Manifest in their visible archive
    envelope.  Its digests are immutable generation evidence: validate every key
    and formal path against the imported canonical target plan, then restore that
    whole snapshot in one commit.  Legacy archives without the envelope retain
    the self-proving reconstruction path.
    """

    raw = (project_dir / "project.json").read_bytes()
    try:
        project = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("project.json is not valid UTF-8 JSON") from exc
    if not isinstance(project, Mapping) or project.get("schema_version") != TARGET_SCHEMA_VERSION:
        raise ValueError("archive activation requires a schema-v8 project")
    if preserved_entries is not None:
        plan = plan_artifact_target_state(project_dir)
        rebased = _rebase_preserved_artifact_entries(plan, preserved_entries)
        invalid = [key.encode() for key, archived in preserved_entries.items() if rebased[key] != archived]
        if invalid:
            raise ValueError(f"archive Artifact Manifest contains unprovable formal claims: {sorted(invalid)}")
        _assert_preflight_unchanged(project_dir, plan)
        return ProjectArtifactManifestAdapter(project_dir).replace_entries_atomically(dict(preserved_entries))
    return activate_artifact_target_state(project_dir, bump_schema=False)


def rebase_preserved_artifact_entries(
    project_dir: Path,
    preserved_entries: Mapping[ArtifactKey, ArtifactManifestEntry],
) -> Mapping[ArtifactKey, ArtifactManifestEntry]:
    """Move frozen claims onto the formal paths proven by one current target plan.

    Archive repair may normalize a pointer or materialize a selected version in
    its private snapshot.  The generation digest remains immutable evidence;
    only the formal path follows that repaired target state.  Missing target
    keys fail loud so an official export cannot emit an envelope that its own
    strict import boundary would reject.
    """

    plan = plan_artifact_target_state(project_dir)
    rebased = _rebase_preserved_artifact_entries(plan, preserved_entries)
    _assert_preflight_unchanged(project_dir, plan)
    return rebased


def _rebase_preserved_artifact_entries(
    plan: ArtifactTargetStatePlan,
    preserved_entries: Mapping[ArtifactKey, ArtifactManifestEntry],
) -> dict[ArtifactKey, ArtifactManifestEntry]:
    rebased: dict[ArtifactKey, ArtifactManifestEntry] = {}
    invalid: list[str] = []
    for key, archived in preserved_entries.items():
        current = plan.entries.get(key)
        if current is None:
            invalid.append(key.encode())
            continue
        rebased[key] = ArtifactManifestEntry(
            artifact_path=current.artifact_path,
            basis_digest=archived.basis_digest,
        )
    if invalid:
        raise ValueError(f"archive Artifact Manifest contains unprovable formal claims: {sorted(invalid)}")
    return rebased


def resolve_current_artifact_target(project_dir: Path, key: ArtifactKey) -> ArtifactManifestEntry | None:
    """Resolve one formal post-commit target without repairing any other key."""

    return _Planner(project_dir, episode_scope=_episode_scope_for_key(key)).resolve_key(key)


def reconcile_artifact_target_claims(
    project_dir: Path,
    keys: Sequence[ArtifactKey],
    *,
    adapter: ArtifactManifestAdapter | None = None,
) -> bool:
    """Forget claims whose canonical target disappeared or moved.

    Metadata edits may remove a formal target without touching its artifact
    bytes.  Resolve the selected claims against one frozen project snapshot,
    verify every dependency remains unchanged, then remove the invalid claims
    in one Manifest compare-and-swap.  Claims whose path is still canonical are
    deliberately retained: changed inputs make them stale, not unowned.
    """

    requested = tuple(dict.fromkeys(keys))
    if not requested or not _artifact_manifest_is_active(project_dir):
        return False

    storage = adapter or ProjectArtifactManifestAdapter(project_dir)
    manifest_snapshot = storage.snapshot_entries()
    claimed = {key: manifest_snapshot[key] for key in requested if key in manifest_snapshot}
    if not claimed:
        return False

    replacements, plan = _plan_artifact_claim_reconciliation(project_dir, claimed)
    if not replacements:
        return False
    _assert_preflight_unchanged(project_dir, plan)
    return register_artifact_entries_atomically(
        project_dir,
        replacements,
        expected_entries={key: claimed[key] for key in replacements},
        adapter=storage,
    )


def _plan_artifact_claim_reconciliation(
    project_dir: Path,
    claimed: Mapping[ArtifactKey, ArtifactManifestEntry],
) -> tuple[dict[ArtifactKey, None], ArtifactTargetStatePlan]:
    """Resolve claimed paths through one canonical dependency snapshot."""

    root_planner = _Planner(project_dir)
    planners: dict[int | None, _Planner] = {None: root_planner}
    dependency_bytes: dict[Path, bytes] = {}
    dependency_digests: dict[Path, str] = {}
    replacements: dict[ArtifactKey, None] = {}

    def _merge_dependency_snapshot[T](destination: dict[Path, T], source: Mapping[Path, T]) -> None:
        for path, value in source.items():
            previous = destination.setdefault(path, value)
            if previous != value:
                raise RuntimeError(f"artifact target dependency changed during reconciliation: {path}")

    for key, frozen_entry in claimed.items():
        scope = _episode_scope_for_key(key)
        planner = planners.get(scope)
        if planner is None:
            planner = _Planner(
                project_dir,
                episode_scope=scope,
                project_bytes=root_planner.project_bytes,
            )
            planners[scope] = planner
        target = planner.resolve_key(key)
        if target is None or target.artifact_path != frozen_entry.artifact_path:
            replacements[key] = None

    for planner in planners.values():
        _merge_dependency_snapshot(dependency_bytes, planner.dependencies)
        _merge_dependency_snapshot(dependency_digests, planner.dependency_digests)
    return (
        replacements,
        ArtifactTargetStatePlan(
            entries={},
            project=root_planner.project,
            project_bytes=root_planner.project_bytes,
            dependency_bytes=dependency_bytes,
            dependency_digests=dependency_digests,
            script_paths=(),
        ),
    )


def resolve_current_artifact_basis(project_dir: Path, key: ArtifactKey) -> ArtifactBasis | None:
    """Resolve canonical evidence for a formal write before its bytes are selected."""

    return _Planner(project_dir, episode_scope=_episode_scope_for_key(key)).resolve_basis(key)


def _episode_scope_for_key(key: ArtifactKey) -> int | None:
    """Return the one episode whose control files may affect ``key``."""

    if key.kind is ArtifactKind.ASSET_SHEET:
        return None
    episode = key.components[0]
    if type(episode) is not int:
        raise ValueError("episode artifact key has no positive episode identity")
    return episode


class ArtifactCurrencyResolver:
    """Side-effect-free runtime comparison against canonical target state."""

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = Path(project_dir)
        root_planner = _Planner(project_dir)
        if root_planner.project.get("schema_version") != TARGET_SCHEMA_VERSION:
            raise RuntimeError("Artifact Manifest is not activated for this project schema")
        # Validate the sidecar once even when a workflow phase has no artifacts
        # to compare.  A corrupt active manifest is a blocker, never an empty
        # target state or permission to fall back to filesystem existence.
        root_planner.adapter.get_entry(ArtifactKey.episode_script(1))
        self._project_bytes = root_planner.project_bytes
        self._planners: dict[int | None, _Planner] = {None: root_planner}
        self._manifest = ArtifactManifest(root_planner.adapter)

    def _planner_for(self, key: ArtifactKey) -> _Planner:
        scope = _episode_scope_for_key(key)
        planner = self._planners.get(scope)
        if planner is None:
            planner = _Planner(
                self._project_dir,
                episode_scope=scope,
                project_bytes=self._project_bytes,
            )
            self._planners[scope] = planner
        return planner

    def compare(self, key: ArtifactKey, *, artifact_path: str) -> ArtifactComparison:
        expected = self._planner_for(key).resolve_key(key)
        return self._manifest.compare_entry(key, artifact_path=artifact_path, expected=expected)


@dataclass(frozen=True, slots=True)
class ArtifactInputClaim:
    """One Manifest-backed formal artifact selected as a provider input."""

    key: ArtifactKey
    artifact_path: str


@dataclass(frozen=True, slots=True)
class EpisodeScriptInput:
    """A bound formal script and the identity frozen for provider admission."""

    episode: int
    claim: ArtifactInputClaim


def active_artifact_currency_resolver(
    project_dir: Path,
    project: Mapping[str, Any],
) -> ArtifactCurrencyResolver | None:
    """Return the active resolver, preserving legacy selection before schema 8."""

    return ArtifactCurrencyResolver(project_dir) if project.get("schema_version") == TARGET_SCHEMA_VERSION else None


def resolve_artifact_episode(
    *,
    project: Mapping[str, object],
    script: dict[str, Any],
    script_filename: str,
) -> int | None:
    """Resolve the Manifest episode identity while preserving legacy fallback.

    Active projects require a positive identity, but the canonical filename is
    valid evidence when the script omits its redundant top-level field. Before
    activation, ``None`` tells callers to retain their historical episode-1
    behavior without weakening the schema-8 gate.
    """

    from lib.project_manager import ProjectManager, resolve_episode_script_binding

    try:
        episode = ProjectManager.resolve_episode_from_script(script, script_filename)
        if episode < 1:
            raise ValueError("script episode must be a positive integer")
    except ValueError:
        if project.get("schema_version") == TARGET_SCHEMA_VERSION:
            raise
        return None
    if (
        project.get("schema_version") == TARGET_SCHEMA_VERSION
        and resolve_episode_script_binding(
            project,
            episode,
            script_filename,
            require_indexed=True,
        )
        is None
    ):
        raise ValueError(f"script {script_filename} is not bound to episode {episode} in project.json")
    return episode


def resolve_usable_episode_script_input(
    *,
    project_path: Path,
    project: Mapping[str, object],
    script: dict[str, Any],
    script_filename: str,
    legacy_episode_fallback: int | None = None,
) -> EpisodeScriptInput:
    """Resolve one bound episode script through the shared formal-input seam.

    Legacy projects still admit the script that the caller already loaded, while
    retaining its typed identity in case schema activation wins before provider
    submission. Callers with a historical noncanonical filename can supply their
    established legacy episode identity. Active projects require the exact bound
    script claim immediately and never use that fallback.
    """

    from lib.project_manager import ProjectManager

    episode = resolve_artifact_episode(
        project=project,
        script=script,
        script_filename=script_filename,
    )
    if episode is None:
        episode = (
            ProjectManager.resolve_episode_from_script(script, script_filename)
            if legacy_episode_fallback is None
            else legacy_episode_fallback
        )
    artifact_path = _normalize_script_binding(ProjectManager.normalize_script_filename(script_filename))
    claim = resolve_usable_artifact_input_claim(
        resolver=active_artifact_currency_resolver(project_path, project),
        key=ArtifactKey.episode_script(episode),
        artifact_path=artifact_path,
    )
    if claim is None:
        raise ValueError(f"episode script is not registered: {artifact_path}")
    return EpisodeScriptInput(episode=episode, claim=claim)


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


def artifact_input_is_usable(
    *,
    resolver: ArtifactCurrencyResolver | None,
    key: ArtifactKey,
    artifact_path: str,
    claims: list[ArtifactInputClaim] | None,
) -> bool:
    """Select one formal input and optionally retain its exact recheck evidence."""

    claim = resolve_usable_artifact_input_claim(
        resolver=resolver,
        key=key,
        artifact_path=artifact_path,
    )
    if claim is None:
        return False
    if claims is not None:
        claims.append(claim)
    return True


def resolve_usable_artifact_input_claim(
    *,
    resolver: ArtifactCurrencyResolver | None,
    key: ArtifactKey,
    artifact_path: str,
) -> ArtifactInputClaim | None:
    """Return recheck evidence for one usable formal input in either schema.

    Legacy selection still uses filesystem ownership, but retaining the logical
    key and exact path lets a provider-boundary check apply a Manifest that was
    activated while the task awaited provider configuration or staging.
    """

    if not artifact_is_usable(resolver, key, artifact_path):
        return None
    return ArtifactInputClaim(key=key, artifact_path=artifact_path)


def assert_artifact_input_claims_usable(
    project_path: Path,
    project: Mapping[str, Any],
    claims: Sequence[ArtifactInputClaim],
) -> None:
    """Recheck selected formal inputs immediately before provider submission."""

    if not claims:
        return
    resolver = active_artifact_currency_resolver(project_path, project)
    if resolver is None:
        raise RuntimeError("formal artifact input claims require an active Artifact Manifest")
    for claim in claims:
        if not artifact_is_usable(resolver, claim.key, claim.artifact_path):
            raise ValueError(f"formal artifact input is no longer registered: {claim.artifact_path}")


def assert_current_artifact_input_claims_usable(
    project_path: Path,
    claims: Sequence[ArtifactInputClaim],
) -> None:
    """Recheck frozen input identities against one current schema snapshot.

    The project lock serializes this read with schema activation and formal
    metadata writes. Legacy projects retain their filesystem admission rule;
    once schema 8 wins the lock, every frozen formal identity must have a
    current or stale Manifest claim before a paid provider can be called.
    """

    if not claims:
        return
    with project_metadata_lock(project_path):
        try:
            raw = (project_path / "project.json").read_bytes()
            project = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError("project.json is not valid UTF-8 JSON") from exc
        if not isinstance(project, Mapping):
            raise ValueError("project.json must contain an object")
        if project.get("schema_version") != TARGET_SCHEMA_VERSION:
            return
        assert_artifact_input_claims_usable(project_path, project, claims)


def resolve_usable_storyboard_video_inputs(
    *,
    project_path: Path,
    project: Mapping[str, object],
    episode: int | None,
    resource_id: str,
    item: dict[str, object],
    resolver: ArtifactCurrencyResolver | None = None,
    claims: list[ArtifactInputClaim] | None = None,
    allow_legacy_same_name: bool | None = None,
) -> tuple[Path, Path | None]:
    """Resolve video inputs and retain active-Manifest recheck evidence."""

    storyboard_file, end_frame = resolve_storyboard_video_inputs(
        project_path=project_path,
        project=project,
        resource_id=resource_id,
        item=item,
        allow_legacy_same_name=allow_legacy_same_name,
    )
    if resolver is None:
        resolver = active_artifact_currency_resolver(project_path, project)
    storyboard_rel = storyboard_file.relative_to(project_path).as_posix()
    if type(episode) is int and episode >= 1:
        if not artifact_input_is_usable(
            resolver=resolver,
            key=ArtifactKey.episode_storyboard(episode, resource_id),
            artifact_path=storyboard_rel,
            claims=claims,
        ):
            raise StoryboardImageUnavailable(f"storyboard is not registered: {storyboard_rel}")
    elif resolver is not None:
        raise ValueError("script episode must be a positive integer")
    return storyboard_file, end_frame


def register_current_artifact(
    project_dir: Path,
    key: ArtifactKey,
    *,
    adapter: ArtifactManifestAdapter | None = None,
    artifact_path: str | None = None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None = None,
) -> bool:
    """Register a formal artifact from current or execution-frozen evidence."""

    storage = adapter or ProjectArtifactManifestAdapter(project_dir)
    if basis is not None:
        if artifact_path is None:
            raise ValueError("artifact_path is required with a frozen basis")
        descriptor = basis if isinstance(basis, ArtifactBasisDescriptor) else ArtifactBasisDescriptor.from_basis(basis)
        return ArtifactManifest(storage).register_descriptor_transactionally(
            key,
            artifact_path=artifact_path,
            basis=descriptor,
        )
    entry = resolve_current_artifact_target(project_dir, key)
    if entry is None:
        raise ValueError(f"formal artifact target is not provable: {key.encode()}")
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


def prepare_episode_script_manifest_commit(
    project_dir: Path,
    *,
    episode: int,
    artifact_path: str,
    resource_ids: Sequence[str],
    removed_resource_ids: Sequence[str] = (),
    basis: ArtifactBasis | ArtifactBasisDescriptor | None = None,
    adapter: ArtifactManifestAdapter | None = None,
) -> Callable[[], None] | None:
    """Preflight one script replacement and return its atomic claim commit.

    The script claim and every claim orphaned by removal of a script item share
    one Manifest compare-and-swap.  Callers invoke the returned closure inside
    the same formal-write transaction that selects the script bytes.
    """

    if not _artifact_manifest_is_active(project_dir):
        return None
    if type(episode) is not int or episode < 1:
        raise ValueError("episode must be a positive integer")
    remaining_ids = frozenset(resource_ids)
    removed_ids = frozenset(removed_resource_ids)
    if any(not isinstance(resource_id, str) or not resource_id for resource_id in (*remaining_ids, *removed_ids)):
        raise ValueError("script resource identities must be non-empty strings")

    storage = adapter or ProjectArtifactManifestAdapter(project_dir)
    snapshot = storage.snapshot_entries()
    observation = storage.inspect_artifact(artifact_path)
    if observation.blocker is not None:
        raise ArtifactManifestError(observation.blocker.detail)
    script_key = ArtifactKey.episode_script(episode)
    orphaned_keys = [
        key
        for key in snapshot
        if key.episode_number == episode
        and key.kind in _EPISODE_RESOURCE_KINDS
        and cast(str, key.components[1]) not in remaining_ids
    ]
    orphaned_keys.extend(
        key
        for resource_id in sorted(removed_ids - remaining_ids)
        for key in ArtifactKey.episode_resource_artifacts(episode, resource_id)
    )
    orphaned_keys = list(dict.fromkeys(orphaned_keys))
    grid_claims = {
        key: entry
        for key, entry in snapshot.items()
        if key.kind is ArtifactKind.EPISODE_GRID and key.episode_number == episode
    }
    expected = {key: snapshot.get(key) for key in (script_key, *orphaned_keys)}
    expected.update(grid_claims)
    frozen_entry: ArtifactManifestEntry | None = None
    if basis is not None:
        descriptor = basis if isinstance(basis, ArtifactBasisDescriptor) else ArtifactBasisDescriptor.from_basis(basis)
        frozen_entry = ArtifactManifestEntry(artifact_path=artifact_path, basis_digest=descriptor.digest)

    def commit() -> None:
        replacements: dict[ArtifactKey, ArtifactManifestEntry | None] = {key: None for key in orphaned_keys}
        if grid_claims:
            grid_replacements, grid_plan = _plan_artifact_claim_reconciliation(project_dir, grid_claims)
            _assert_preflight_unchanged(project_dir, grid_plan)
            replacements.update(grid_replacements)
        replacements[script_key] = frozen_entry or resolve_current_artifact_target(project_dir, script_key)
        register_artifact_entries_atomically(
            project_dir,
            replacements,
            expected_entries=expected,
            adapter=storage,
        )

    return commit


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
    if resource_type == "grids":
        grid = next((candidate for candidate in planner._load_grid_records() if candidate.id == resource_id), None)
        if grid is None:
            raise KeyError(resource_id)
        planner._load_episode_bindings()
        binding = next((candidate for candidate in planner.bindings if candidate.episode == grid.episode), None)
        if binding is None or binding.script_file != _normalize_script_binding(grid.script_file):
            raise ValueError("formal grid no longer matches an episode script binding")
        return ArtifactKey.episode_grid(grid.episode, resource_id)
    if script_file is not None:
        planner._load_episode_bindings()
        normalized = _normalize_script_binding(script_file)
        binding = next((candidate for candidate in planner.bindings if candidate.script_file == normalized), None)
        if binding is None:
            raise ValueError("formal resource no longer matches an episode script binding")
        episode_number = binding.episode
    elif resource_type == "storyboards":
        planner._load_episodes()
        matches = [
            candidate
            for candidate in planner.episodes
            if any(str(item.get(candidate.id_field)) == resource_id for item in candidate.items)
        ]
        if len(matches) != 1:
            raise ValueError("storyboard identity does not resolve to exactly one episode binding")
        episode_number = matches[0].episode
    else:
        raise ValueError(f"script_file is required for {resource_type}")
    if resource_type == "storyboards":
        return ArtifactKey.episode_storyboard(episode_number, resource_id)
    if resource_type in {"videos", "reference_videos"}:
        return ArtifactKey.episode_video(episode_number, resource_id)
    if resource_type == "audio":
        return ArtifactKey.episode_audio(episode_number, resource_id)
    raise ValueError(f"unsupported formal artifact resource type: {resource_type}")


def resolve_current_resource_artifact_basis(
    project_dir: Path,
    *,
    resource_type: str,
    resource_id: str,
    script_file: str | None = None,
) -> ArtifactBasis | None:
    """Resolve one resource's canonical basis, preserving pre-activation writes."""

    if not _artifact_manifest_is_active(project_dir):
        return None
    key = artifact_key_for_resource(
        project_dir,
        resource_type=resource_type,
        resource_id=resource_id,
        script_file=script_file,
    )
    return resolve_current_artifact_basis(project_dir, key)


def register_current_resource_artifact(
    project_dir: Path,
    *,
    resource_type: str,
    resource_id: str,
    script_file: str | None = None,
    artifact_path: str | None = None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None = None,
) -> bool:
    """Register a successful formal commit from target or execution-frozen evidence."""

    if not _artifact_manifest_is_active(project_dir):
        return False

    key = artifact_key_for_resource(
        project_dir,
        resource_type=resource_type,
        resource_id=resource_id,
        script_file=script_file,
    )
    if basis is not None:
        descriptor = basis if isinstance(basis, ArtifactBasisDescriptor) else ArtifactBasisDescriptor.from_basis(basis)
        if artifact_path is None:
            artifact_path = resource_relative_path(resource_type, resource_id)
        return ArtifactManifest(ProjectArtifactManifestAdapter(project_dir)).register_descriptor_transactionally(
            key,
            artifact_path=artifact_path,
            basis=descriptor,
        )
    return register_current_artifact_if_provable(project_dir, key)


def register_task_current_resource_artifact(
    project_dir: Path,
    *,
    resource_type: str,
    resource_id: str,
    script_file: str | None = None,
    artifact_path: str | None = None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None = None,
) -> ArtifactRegistrationReceipt:
    """Register a task's frozen evidence and return its terminal-cancel receipt."""

    if not _artifact_manifest_is_active(project_dir):
        return ArtifactRegistrationReceipt(None, None, None, None)

    key = artifact_key_for_resource(
        project_dir,
        resource_type=resource_type,
        resource_id=resource_id,
        script_file=script_file,
    )
    if basis is None:
        entry = resolve_current_artifact_target(project_dir, key)
        if entry is None:
            raise ValueError(f"formal task artifact target is not provable: {key.encode()}")
    else:
        descriptor = basis if isinstance(basis, ArtifactBasisDescriptor) else ArtifactBasisDescriptor.from_basis(basis)
        entry = ArtifactManifestEntry(
            artifact_path=artifact_path or resource_relative_path(resource_type, resource_id),
            basis_digest=descriptor.digest,
        )
    adapter = ProjectArtifactManifestAdapter(project_dir)
    previous = adapter.get_entry(key)
    changed = ArtifactManifest(adapter).register_entry_transactionally(key, entry)
    return ArtifactRegistrationReceipt(
        adapter=adapter,
        key=key,
        registered=entry,
        previous=previous,
        changed=changed,
    )


def register_artifact_entries_atomically(
    project_dir: Path,
    entries: Mapping[ArtifactKey, ArtifactManifestEntry | None],
    *,
    expected_entries: Mapping[ArtifactKey, ArtifactManifestEntry | None] | None = None,
    adapter: ArtifactManifestAdapter | None = None,
) -> bool:
    """Replace a frozen batch of formal claims in one guarded Manifest commit.

    ``expected_entries`` protects source claims that the replacements were
    derived from without rewriting those sources. This lets a multi-file
    formal commit fail and roll back when an input claim changes after preflight.
    """

    if not entries or not _artifact_manifest_is_active(project_dir):
        return False
    storage = adapter or ProjectArtifactManifestAdapter(project_dir)
    replacements = dict(entries)
    guarded = dict(expected_entries or {})
    observed = {key: storage.get_entry(key) for key in {*guarded, *replacements}}
    expected = dict(guarded)
    for key, entry in replacements.items():
        if entry is not None:
            observation = storage.inspect_artifact(entry.artifact_path)
            if observation.blocker is not None:
                raise ArtifactManifestError(
                    f"cannot register blocked formal artifact {entry.artifact_path}: {observation.blocker.detail}"
                )
            if not observation.present:
                raise ArtifactManifestError(f"cannot register missing formal artifact: {entry.artifact_path}")
        expected.setdefault(key, observed[key])
    if any(observed[key] != value for key, value in expected.items()):
        raise ArtifactManifestError("artifact manifest changed during batch registration")
    if all(observed[key] == value for key, value in replacements.items()):
        return False
    after = dict(observed)
    after.update(replacements)
    try:
        receipt = ArtifactEntryRekeyPlan(
            adapter=storage,
            before=observed,
            after=after,
            changed=True,
        ).commit()
    except ArtifactManifestError as exc:
        if str(exc) == "artifact claims changed after the rekey preflight":
            raise ArtifactManifestError("artifact manifest changed during batch registration") from exc
        raise
    return receipt.changed


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


def _forget_unbound_episode_artifacts(
    project_dir: Path,
    resource_id: str,
    *,
    kind: ArtifactKind,
) -> bool:
    """Remove claims of one episode-scoped kind when its canonical owner is absent."""

    if not _artifact_manifest_is_active(project_dir):
        return False
    adapter = ProjectArtifactManifestAdapter(project_dir)
    keys = [key for key in adapter.snapshot_entries() if key.kind is kind and key.components[1] == resource_id]
    return ArtifactManifest(adapter).forget_entries_transactionally(keys)


def forget_unbound_storyboard_artifacts(project_dir: Path, resource_id: str) -> bool:
    """Remove storyboard claims when no canonical episode owns the resource."""

    return _forget_unbound_episode_artifacts(
        project_dir,
        resource_id,
        kind=ArtifactKind.EPISODE_STORYBOARD,
    )


def forget_unbound_grid_artifacts(project_dir: Path, resource_id: str) -> bool:
    """Remove grid claims when no valid grid record owns the resource."""

    return _forget_unbound_episode_artifacts(
        project_dir,
        resource_id,
        kind=ArtifactKind.EPISODE_GRID,
    )


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
    for path, expected in plan.dependency_digests.items():
        try:
            current = visual_file_digest(path)
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
        _ensure_activation_backup(source, stamp=stamp)


def _ensure_activation_backup(source: Path, *, stamp: int) -> None:
    content = source.read_bytes()
    pattern = f"{source.name}.bak.v7-*"
    for candidate in source.parent.glob(pattern):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            if candidate.read_bytes() == content:
                try:
                    os.utime(candidate, None, follow_symlinks=False)
                except (NotImplementedError, OSError):
                    continue
                return
        except OSError:
            continue
    backup = source.with_name(f"{source.name}.bak.v7-{stamp}")
    atomic_write_bytes(backup, content)


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
    "ArtifactInputClaim",
    "ArtifactRegistrationReceipt",
    "ArtifactTargetStatePlan",
    "EpisodeScriptInput",
    "TARGET_SCHEMA_VERSION",
    "activate_artifact_target_state",
    "active_artifact_currency_resolver",
    "artifact_input_is_usable",
    "artifact_is_usable",
    "assert_artifact_input_claims_usable",
    "assert_current_artifact_input_claims_usable",
    "artifact_key_for_resource",
    "ensure_imported_artifact_target_state",
    "forget_current_resource_artifact",
    "forget_unbound_grid_artifacts",
    "forget_unbound_storyboard_artifacts",
    "plan_artifact_target_state",
    "prepare_episode_script_manifest_commit",
    "rebase_preserved_artifact_entries",
    "register_current_artifact",
    "register_artifact_entries_atomically",
    "register_current_artifact_if_provable",
    "register_current_resource_artifact",
    "register_task_current_resource_artifact",
    "reconcile_artifact_target_claims",
    "resolve_artifact_episode",
    "resolve_current_artifact_basis",
    "resolve_current_artifact_target",
    "resolve_current_resource_artifact_basis",
    "resolve_usable_episode_script_input",
    "resolve_usable_artifact_input_claim",
    "resolve_usable_storyboard_video_inputs",
]
