"""从项目规范状态投影产物目标态。

规划期只读：把 project.json、剧本、资产定义与宫格记录投影成一份完整的产物条目集合，
供激活提交与运行期比对复用；写回与提交守卫都不在本模块。
"""

from __future__ import annotations

import json
import re
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from lib import script_review
from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactKind,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ProjectArtifactManifestAdapter,
)
from lib.artifact_provenance import (
    build_ad_episode_script_basis,
    build_episode_script_basis,
    build_script_plan_basis,
    decode_script_plan_source,
)
from lib.artifact_version_provenance import parse_typed_audio_settings, parse_typed_media_version_target
from lib.asset_types import ASSET_SPECS, asset_name_comparison_key
from lib.episode_paths import episode_source_relpath
from lib.grid.layout import grid_aspect_ratio_for
from lib.grid.models import GridGeneration
from lib.media_artifact_currency import build_current_audio_artifact_basis, build_current_video_artifact_basis
from lib.narration_delivery import POST_PRODUCTION, USE_TTS
from lib.project_migration_failure import ProjectMigrationError
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION, parse_project_schema_version, project_schema_is_current
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
from lib.storyboard_sequence import get_storyboard_items
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

#: 清单激活把项目提升到的版本。它是 v7→v8 这一步的落点，与「项目是否为当前版本」是两件事：
#: 后者随 ``CURRENT_PROJECT_SCHEMA_VERSION`` 走，把两者绑成一个常量会让每次 schema 升版都
#: 悄悄改掉迁移链中间那一步的前置条件，存量 v7 项目从此迁不动。
ARTIFACT_MANIFEST_SCHEMA_VERSION = 8

#: 允许跑清单激活的版本：迁移途中的 v7（此后由激活提升到 v8），以及已经走完迁移链的当前版本
#: 区间（归档导入在 ``migrate_project_dir`` 之后才激活，看到的是当前版本）。
_ACTIVATION_SCHEMA_VERSIONS = frozenset(
    {ARTIFACT_MANIFEST_SCHEMA_VERSION - 1, *range(ARTIFACT_MANIFEST_SCHEMA_VERSION, CURRENT_PROJECT_SCHEMA_VERSION + 1)}
)


_GRID_RECORD_RE = re.compile(r"grid_[0-9a-f]{12}\.json\Z")


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
    formal_paths: Mapping[ArtifactKey, str]
    project: Mapping[str, Any]
    project_bytes: bytes
    dependency_bytes: Mapping[Path, bytes]
    dependency_digests: Mapping[Path, str]
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
class _FormalScriptPlanState:
    artifact_path: str
    content: object


@dataclass(frozen=True, slots=True)
class _PersistedPresentationProof:
    frozen_subtitle_basis: ArtifactBasis
    frozen_presentation_basis: ArtifactBasis
    current_subtitle_basis: ArtifactBasis | None
    current_presentation_basis: ArtifactBasis | None


class TargetStatePlanner:
    def __init__(
        self,
        project_dir: Path,
        *,
        episode_scope: int | None = None,
        project_bytes: bytes | None = None,
        allow_stale_formal_targets: bool = False,
        pending_renames: Mapping[str, str] | None = None,
    ) -> None:
        self.project_dir = project_dir.resolve(strict=True)
        self.adapter = ProjectArtifactManifestAdapter(self.project_dir)
        self.project_path = self.project_dir / "project.json"
        self.pending_renames = dict(pending_renames or {})
        if episode_scope is not None and (type(episode_scope) is not int or episode_scope < 1):
            raise ValueError("episode scope must be a positive integer or null")
        self.episode_scope = episode_scope
        self.allow_stale_formal_targets = allow_stale_formal_targets
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
        self.formal_paths: dict[ArtifactKey, str] = {}
        self._path_owners: dict[str, ArtifactKey] = {}
        self._versions: dict[str, Any] | None = None
        self._activation_mode = False
        self._planned: set[str] = set()

    def plan(self) -> ArtifactTargetStatePlan:
        schema = parse_project_schema_version(self.project)
        if schema not in _ACTIVATION_SCHEMA_VERSIONS:
            raise ValueError(
                f"artifact activation requires schema in {sorted(_ACTIVATION_SCHEMA_VERSIONS)}, got {schema!r}"
            )

        self._activation_mode = True
        try:
            # Parsing the existing sidecar is part of preflight.  A corrupt manifest
            # is a real migration error, not permission to overwrite unknown state.
            self.adapter.get_entry(ArtifactKey.episode_script(1))
            self.load_episodes()
            self._plan_assets()
            self._plan_structured_content()
            self._plan_grids()
            self._plan_storyboards()
            self._plan_typed_media()
            self._plan_persisted_presentations()
        except ProjectMigrationError:
            # Already carries the episode / file it was rejected at.
            raise
        except (ArtifactManifestError, ValueError) as exc:
            # Preflight refused outside any single episode: the offending input is
            # project-level (bindings, asset buckets, the manifest sidecar itself).
            raise ProjectMigrationError(str(exc), file="project.json") from exc
        return ArtifactTargetStatePlan(
            entries=dict(self.entries),
            formal_paths=dict(self.formal_paths),
            project=dict(self.project),
            project_bytes=self.project_bytes,
            dependency_bytes=dict(self.dependencies),
            dependency_digests=dict(self.dependency_digests),
            script_paths=tuple(self.script_paths),
        )

    def resolve_key(self, key: ArtifactKey) -> ArtifactManifestEntry | None:
        """Resolve one post-commit target through the same canonical planner."""

        if not project_schema_is_current(self.project):
            raise ProjectMigrationError("Artifact Manifest is not activated for this project schema")
        self._plan_key(key)
        return self.entries.get(key)

    def resolve_basis(self, key: ArtifactKey) -> ArtifactBasis | None:
        """Resolve one canonical basis without requiring its formal output yet."""

        if not project_schema_is_current(self.project):
            raise ProjectMigrationError("Artifact Manifest is not activated for this project schema")
        self._plan_key(key)
        return self.bases.get(key)

    def _plan_key(self, key: ArtifactKey) -> None:
        """Run the canonical planning slice shared by target and basis resolution."""

        kind = key.kind.value
        if kind == "asset-sheet":
            self._plan_assets()
        elif kind == "episode-script-plan":
            self.load_episode_bindings()
            episode_number = cast(int, key.components[0])
            binding = next((candidate for candidate in self.bindings if candidate.episode == episode_number), None)
            if binding is not None:
                self._plan_one_script_plan(binding)
        elif kind == "episode-script":
            self.load_episodes()
            self._plan_structured_content()
        elif kind == "episode-grid":
            self.load_episodes()
            self._plan_grids()
        elif kind == "episode-storyboard":
            self.load_episodes()
            self._plan_grids()
            self._plan_storyboards()
        elif kind in {"episode-video", "episode-audio"}:
            self.load_episodes()
            self._plan_typed_media()
        elif kind in {"episode-subtitle", "episode-presentation"}:
            self.load_episodes()
            self._plan_persisted_presentations()

    def load_episode_bindings(self) -> None:
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
            normalized = normalize_script_binding(script_file)
            if episode in seen_episodes or normalized in seen_scripts:
                raise ValueError("project episode bindings must be unique")
            seen_episodes.add(episode)
            seen_scripts.add(normalized)
            self.bindings.append(_EpisodeBinding(episode=episode, script_file=normalized))
        self._bindings_loaded = True

    def load_episodes(self) -> None:
        if self._episodes_loaded:
            return
        self.load_episode_bindings()
        for binding in self.bindings:
            if self.episode_scope is not None and binding.episode != self.episode_scope:
                continue
            with self._episode_context(binding):
                self._load_episode(binding)
        self._episodes_loaded = True

    @contextmanager
    def _episode_context(self, binding: _EpisodeBinding) -> Generator[None]:
        """Name the episode and script a preflight rejection came from.

        Only active while planning a target state: the runtime resolve paths
        reuse the same loader and must keep raising their original exceptions.
        """

        if not self._activation_mode:
            yield
            return
        try:
            yield
        except ProjectMigrationError:
            raise
        except (ArtifactManifestError, ValueError) as exc:
            raise ProjectMigrationError(str(exc), episode=binding.episode, file=binding.script_file) from exc

    def _load_episode(self, binding: _EpisodeBinding) -> None:
        observation = self.adapter.inspect_artifact(binding.script_file)
        if observation.blocker is not None:
            raise ArtifactManifestError(observation.blocker.detail)
        if not observation.present:
            return
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
        self._record_formal_path(
            ArtifactKey.episode_script(binding.episode),
            observation.artifact_path,
        )

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
        script_plan_by_episode = {
            binding.episode: script_plan
            for binding in self.bindings
            if (self.episode_scope is None or binding.episode == self.episode_scope)
            and (script_plan := self._plan_one_script_plan(binding)) is not None
        }
        for episode in self.episodes:
            script_plan = script_plan_by_episode.get(episode.episode)
            if script_plan is None:
                continue
            try:
                script_basis = build_episode_script_basis(script_plan.content, project=self.project)
            except (TypeError, ValueError):
                continue
            self._add_if_present(
                ArtifactKey.episode_script(episode.episode),
                episode.script_file,
                script_basis,
            )
        self._planned.add("structured-content")

    def _plan_one_script_plan(self, binding: _EpisodeBinding) -> _FormalScriptPlanState | None:
        if self.project.get("content_mode") not in {"narration", "drama"}:
            return None
        script_plan_path = script_review.script_plan_path(self.project_dir, self.project, binding.episode)
        if script_plan_path is None:
            return None
        script_plan_rel = script_plan_path.relative_to(self.project_dir).as_posix()
        observation = self.adapter.inspect_artifact(self._pending_source(script_plan_rel))
        if observation.blocker is not None or not observation.present:
            return None
        script_plan_raw = self._read_dependency(script_plan_rel, "formal script_plan")
        script_plan_content = self._parse_json(script_plan_raw, f"formal script_plan {script_plan_rel}")
        script_plan_key = ArtifactKey.episode_script_plan(binding.episode)
        source_rel = episode_source_relpath(binding.episode)
        source_observation = self.adapter.inspect_artifact(source_rel)
        if source_observation.blocker is None and source_observation.present:
            source_raw = self._read_dependency(source_rel, "episode source")
            try:
                source_content = decode_script_plan_source(source_raw)
            except UnicodeDecodeError as exc:
                raise ValueError(f"episode source {source_rel} is not UTF-8") from exc
            try:
                script_plan_basis = build_script_plan_basis(
                    source_content,
                    episode=binding.episode,
                    project=self.project,
                )
            except (TypeError, ValueError):
                pass
            else:
                self._add_if_present(script_plan_key, script_plan_rel, script_plan_basis)
        if script_plan_key not in self.entries:
            return None
        return _FormalScriptPlanState(artifact_path=script_plan_rel, content=script_plan_content)

    def _plan_storyboards(self) -> None:
        if "storyboards" in self._planned:
            return
        if self.project.get("generation_mode") != "storyboard":
            self._planned.add("storyboards")
            return
        style = self.project.get("style", "")
        style_description = self.project.get("style_description", "")
        aspect_ratio = self.project.get("aspect_ratio") or "9:16"
        if not isinstance(style, str) or not isinstance(style_description, str) or not isinstance(aspect_ratio, str):
            raise ValueError("project storyboard style, style description, and aspect ratio must be strings")
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
                        style_description=style_description,
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
        for grid in self.load_grid_records():
            episode = next(
                (
                    candidate
                    for candidate in self.episodes
                    if candidate.episode == grid.episode
                    and candidate.script_file == normalize_script_binding(grid.script_file)
                ),
                None,
            )
            if (
                episode is None
                or (grid.status != "completed" and not self.allow_stale_formal_targets)
                or not grid.grid_image_path
            ):
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
        for grid in self.load_grid_records():
            if grid.episode != episode_number or not grid.split_at or not grid.grid_image_path:
                continue
            episode = next(
                (
                    candidate
                    for candidate in self.episodes
                    if candidate.episode == grid.episode
                    and candidate.script_file == normalize_script_binding(grid.script_file)
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

    def load_grid_records(self) -> tuple[GridGeneration, ...]:
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
        if target.episode != episode.episode or normalize_script_binding(target.script_file) != episode.script_file:
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
                )
        except (KeyError, OSError, TypeError, ValueError):
            return
        if current_basis is None or (
            self._activation_mode and not self.allow_stale_formal_targets and current_basis != target.basis
        ):
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
            or normalize_script_binding(target.script_file) != episode.script_file
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
        if target.episode != episode.episode or normalize_script_binding(target.script_file) != episode.script_file:
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
        observation = self.adapter.inspect_artifact(self._pending_source(artifact_path))
        if observation.blocker is not None or not observation.present:
            return
        canonical = self._canonical_target(artifact_path, observation.artifact_path)
        self._record_formal_path(key, canonical)
        if key.kind in _FORMAL_IMAGE_KINDS:
            self._track_dependency_digest(
                self.project_dir.joinpath(*Path(canonical).parts),
            )
        entry = ArtifactManifestEntry(
            artifact_path=canonical,
            basis_digest=basis.digest,
        )
        existing = self.entries.get(key)
        if existing is not None and existing != entry:
            raise ValueError(f"multiple canonical targets claim artifact key {key.encode()}")
        self.entries[key] = entry

    def _record_formal_path(self, key: ArtifactKey, artifact_path: str) -> None:
        """Remember a canonical present target independently from its current basis."""

        existing_path = self.formal_paths.get(key)
        if existing_path is not None and existing_path != artifact_path:
            raise ValueError(f"multiple canonical paths claim artifact key {key.encode()}")
        owner = self._path_owners.get(artifact_path)
        if owner is not None and owner != key:
            raise ValueError(
                f"formal artifact path is claimed by multiple keys: {artifact_path} ({owner.encode()}, {key.encode()})"
            )
        self.formal_paths[key] = artifact_path
        self._path_owners[artifact_path] = key

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

    def _pending_source(self, relative_path: str) -> str:
        """该规范路径当前的落盘位置：待改名输入仍在旧名下，其余原样返回。"""

        return self.pending_renames.get(relative_path, relative_path)

    def _canonical_target(self, relative_path: str, observed: str) -> str:
        """规划记下的落点：待改名输入按提交后的规范路径记，其余按实际观察到的路径记。"""

        return relative_path if relative_path in self.pending_renames else observed

    def _canonical_path(self, relative_path: str, observed: str) -> Path:
        """:meth:`_canonical_target` 的绝对路径形态。"""

        return self.project_dir.joinpath(*Path(self._canonical_target(relative_path, observed)).parts)

    def _read_dependency(self, relative_path: str, label: str) -> bytes:
        source = self._pending_source(relative_path)
        observation = self.adapter.inspect_artifact(source)
        if observation.blocker is not None:
            raise ArtifactManifestError(observation.blocker.detail)
        if not observation.present:
            raise ValueError(f"{label} is missing: {relative_path}")
        path = self.project_dir.joinpath(*Path(observation.artifact_path).parts)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot read {label}: {relative_path}") from exc
        self.dependencies[self._canonical_path(relative_path, observation.artifact_path)] = raw
        return raw

    @staticmethod
    def _parse_json(raw: bytes, label: str) -> object:
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"{label} is not valid UTF-8 JSON") from exc


def plan_artifact_target_state(
    project_dir: Path,
    *,
    pending_renames: Mapping[str, str] | None = None,
) -> ArtifactTargetStatePlan:
    """Perform the complete read-only activation preflight.

    ``pending_renames``（规范相对路径 → 当前落盘相对路径）供 schema 迁移使用：迁移在同一步里
    把输入改名到规范路径，预检必须在改名落盘之前完成，因而按旧名读取、按规范路径记录。计划
    里的依赖路径与产物路径因此都是提交时刻的事实，激活的稳定性闸门可直接复用。
    """

    return TargetStatePlanner(project_dir, pending_renames=pending_renames).plan()


def episode_scope_for_key(key: ArtifactKey) -> int | None:
    """Return the one episode whose control files may affect ``key``."""

    if key.kind is ArtifactKind.ASSET_SHEET:
        return None
    episode = key.components[0]
    if type(episode) is not int:
        raise ValueError("episode artifact key has no positive episode identity")
    return episode


def normalize_script_binding(value: str) -> str:
    normalized = value.replace("\\", "/")
    if normalized.startswith("scripts/"):
        normalized = normalized.removeprefix("scripts/")
    if not normalized or "/" in normalized or normalized in {".", ".."}:
        raise ValueError(f"invalid episode script binding: {value!r}")
    return f"scripts/{normalized}"
