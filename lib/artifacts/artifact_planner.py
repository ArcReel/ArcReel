"""从项目规范状态投影产物目标态。

规划期只读：把 project.json、剧本、资产定义与宫格记录投影成一份完整的产物条目集合，
供激活提交与运行期比对复用；写回与提交守卫都不在本模块。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Generator, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from lib.artifacts.artifact_manifest import (
    PROJECT_LEVEL_ARTIFACT_KINDS,
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactBlocker,
    ArtifactContentFacts,
    ArtifactKey,
    ArtifactKind,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ArtifactManifestSnapshot,
    ArtifactObservation,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_provenance import (
    build_ad_episode_script_basis,
    build_episode_script_basis,
    build_script_plan_basis,
    decode_script_plan_source,
)
from lib.artifacts.artifact_version_provenance import (
    is_uploaded_video_version,
    parse_typed_media_version_target,
    uploaded_video_version_basis,
)
from lib.artifacts.generation_input import (
    ImageGenerationInput,
    InputRefused,
    ObservedFile,
    asset_sheet_input,
    derivative_sheet_input,
    storyboard_image_input,
)
from lib.artifacts.input_gap import GAP_TOLERANT_BASIS_SCHEMA_VERSION, InputGap, project_basis_records_gaps
from lib.artifacts.legacy_history_index import LegacyHistoryIndex
from lib.artifacts.media_artifact_currency import (
    CURRENT_AUDIO_RULES,
    AudioBasisRules,
    VideoDurationOverride,
    build_current_video_artifact_basis,
)
from lib.artifacts.sqlite_history import SQLiteHistory
from lib.artifacts.version_manager import VersionManager, selected_manual_upload_snapshot
from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.artifacts.visual_artifact_provenance import (
    UPLOADED_VIDEO_BASIS_KIND,
    GridStoryboardVisual,
    VisualReference,
    build_grid_composite_visual_basis,
    build_grid_member_storyboard_visual_basis,
    build_uploaded_asset_sheet_basis,
    build_uploaded_storyboard_basis,
    project_basis_records_asset_names,
    project_basis_style_description,
    visual_file_digest,
)
from lib.bgm.library import BgmSource, bgm_basis, bgm_key, read_bgm_library, resolve_bgm_sources
from lib.edit_timeline.bgm import bgm_ids
from lib.edit_timeline.model import BgmClip
from lib.episode.episode_paths import episode_source_relpath
from lib.episode.episode_sources import SOURCE_ORIGIN_FIELD, SourceOrigin, episode_entry, episode_source_origin
from lib.infra.content_digest import unprefixed
from lib.jianying_draft.basis import (
    DraftNarration,
    DraftUnitBasis,
    build_jianying_draft_basis,
    draft_unit_ids,
    effective_unit_variant,
    jianying_draft_artifact_path,
)
from lib.project.asset_derivatives import (
    DERIVATIVE_ASSET_TYPE,
    derivative_artifact_id,
    derivative_artifact_key,
    is_derivative_artifact_id,
    split_derivative_artifact_id,
)
from lib.project.asset_types import (
    ASSET_SPECS,
    DERIVATIVES_FIELD,
    AssetSpec,
    asset_name_comparison_key,
    resolve_asset_key,
)
from lib.project.project_migration_failure import ProjectMigrationError
from lib.project.project_migration_report import MigrationSkippedArtifact, SkippedArtifactCategory
from lib.project.project_schema import (
    CURRENT_PROJECT_SCHEMA_VERSION,
    parse_project_schema_version,
    project_schema_is_current,
)
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE, resource_relative_path
from lib.references.reference_admission import SHEET_MISSING_CODE
from lib.references.reference_catalog import ReferenceCatalog, build_reference_catalog
from lib.script import script_review
from lib.script.grid.layout import grid_aspect_ratio_for
from lib.script.grid.models import GridGeneration
from lib.script.script_editor import resolve_items
from lib.script.storyboard_sequence import get_storyboard_items
from lib.speech.narration_delivery import POST_PRODUCTION, USE_TTS
from lib.speech.speech_artifact_provenance import (
    PRESENTATION_WITHOUT_TRANSITION_SCHEMA_VERSION,
    RenditionVariant,
    SelectedMediaEvidence,
    build_blocked_subtitle_basis,
    build_legacy_transition_presentation_basis,
    build_presentation_basis,
    media_content_digest,
)
from lib.speech.speech_composition import (
    SpeechComposition,
    SpeechFieldLocation,
    SpeechInputUtterance,
    SpeechPreparation,
    SpeechUnitSnapshot,
    admit_script_unit,
    replan_marked_only_for_mixed_speech,
)
from lib.speech.speech_presentation import (
    PresentationMedia,
    SpeechPresentation,
    materialize_speech_presentation,
    presentation_artifact_paths,
)

ARTIFACT_MANIFEST_SCHEMA_VERSION = 8

_ACTIVATION_SCHEMA_VERSIONS = frozenset(
    {ARTIFACT_MANIFEST_SCHEMA_VERSION - 1, *range(ARTIFACT_MANIFEST_SCHEMA_VERSION, CURRENT_PROJECT_SCHEMA_VERSION + 1)}
)


_GRID_RECORD_RE = re.compile(r"grid_[0-9a-f]{12}\.json\Z")


def control_file_names(directory: Path) -> frozenset[str]:
    if directory.is_symlink():
        raise ValueError(f"{directory.name} control directory is not a safe directory")
    if not directory.exists():
        return frozenset()
    if not directory.is_dir():
        raise ValueError(f"{directory.name} control directory is not a safe directory")
    return frozenset(
        path.name
        for path in directory.glob("*.json")
        if directory.name != "grids" or _GRID_RECORD_RE.fullmatch(path.name)
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
    formal_paths: Mapping[ArtifactKey, str]
    project: Mapping[str, Any]
    project_bytes: bytes
    dependency_bytes: Mapping[Path, bytes]
    dependency_digests: Mapping[Path, str]
    script_paths: tuple[Path, ...]
    skipped: tuple[MigrationSkippedArtifact, ...] = ()
    """Formal pointers the planner saw but could not register, with the reason each was left out."""
    unselected_media_formal_paths: Mapping[ArtifactKey, str] = field(default_factory=dict)
    """依据的视频或音频版本已不是单元当前选中版本、对那个版本仍完整自证的字幕与呈现产物的正式路径。

    只在 ``allow_stale_formal_targets`` 下收集：这类登记暂不适用于当前媒体，切回它依据的版本即重新成立。
    """
    absent_paths: frozenset[Path] = frozenset()
    grid_records: frozenset[str] | None = None
    script_records: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class _EpisodeBinding:
    episode: int
    script_file: str


def _episode_bindings(project: Mapping[str, Any]) -> list[_EpisodeBinding]:
    bindings: list[_EpisodeBinding] = []
    raw_episodes = project.get("episodes")
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
        bindings.append(_EpisodeBinding(episode=episode, script_file=normalized))
    return bindings


@dataclass(slots=True)
class RuntimePlanningContext:
    script_snapshot: ArtifactObservation | None = None
    history: LegacyHistoryIndex = field(default_factory=LegacyHistoryIndex)
    bindings: dict[int, _EpisodeBinding] | None = None
    catalog: ReferenceCatalog | None = None

    asset_aliases: dict[int, dict[str, str]] = field(default_factory=dict)

    def episode_bindings(self, project: Mapping[str, Any]) -> Mapping[int, _EpisodeBinding]:
        if self.bindings is None:
            self.bindings = {binding.episode: binding for binding in _episode_bindings(project)}
        return self.bindings

    def selected_history(
        self,
        directory: Path,
        adapter: ProjectArtifactManifestAdapter,
        store: SQLiteHistory | None,
        keys: Iterable[tuple[str, str]],
        *,
        equivalent: bool = False,
        proof_version: int | None = None,
    ) -> Mapping[str, Any]:
        if store is not None:
            if equivalent:
                keys = [pair for kind, name in keys for pair in store.equivalent_keys(kind, name)]
            return store.selected_resources(keys, proof_version=proof_version)
        relative = "versions/versions.json"
        observation = adapter.inspect_artifact(relative)
        if observation.blocker is not None:
            raise ArtifactManifestError(observation.blocker.detail)
        if not observation.present:
            return {}
        return self.history.resources(directory / relative, keys, equivalent=equivalent, proof_version=proof_version)

    def index_asset_names(self, project: Mapping[str, Any]) -> None:
        def index(bucket: Mapping[Any, Any]) -> None:
            for raw in bucket:
                if isinstance(raw, str) and (normalized := asset_name_comparison_key(raw)) != raw:
                    self.asset_aliases.setdefault(id(bucket), {}).setdefault(normalized, raw)

        for spec in ASSET_SPECS.values():
            bucket = project.get(spec.bucket_key)
            if isinstance(bucket, Mapping):
                index(bucket)
                if spec.supports_derivatives:
                    for owner in bucket.values():
                        children = owner.get(DERIVATIVES_FIELD) if isinstance(owner, Mapping) else None
                        if isinstance(children, Mapping):
                            index(children)


@dataclass(frozen=True, slots=True)
class EpisodeScriptInputs:
    episode: int
    script_file: str
    script_path: Path
    script: dict[str, Any]
    positions: dict[str, int]
    by_id: dict[str, dict[str, Any]]
    items: tuple[dict[str, Any], ...]
    id_field: str
    kind: str

    @classmethod
    def parse(cls, directory: Path, binding: _EpisodeBinding, raw_script: bytes) -> EpisodeScriptInputs:
        try:
            parsed = json.loads(raw_script.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"episode script {binding.script_file} is not valid UTF-8 JSON") from exc
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
        return cls(
            episode=binding.episode,
            script_file=binding.script_file,
            script_path=directory / binding.script_file,
            script=script,
            positions={item[id_field]: index for index, item in enumerate(typed_items)},
            by_id={item[id_field]: item for item in typed_items},
            items=tuple(typed_items),
            id_field=id_field,
            kind=kind,
        )

    def video_input(self, resource_id: str, *, record_gaps: bool) -> tuple[str, str] | None:
        item = self.by_id.get(resource_id)
        if item is None:
            return None
        if item.get("needs_replan") is True and not (
            record_gaps and replan_marked_only_for_mixed_speech(self.kind, item)
        ):
            return None
        assets = item.get("generated_assets")
        path = assets.get("video_clip") if isinstance(assets, Mapping) else None
        if not isinstance(path, str) or not path:
            return None
        return ("reference_videos" if self.kind == "video_units" else "videos", path)


@dataclass(frozen=True, slots=True)
class _PersistedPresentationProof:
    frozen: SpeechPresentation
    preparation: SpeechPreparation
    frozen_presentation_basis: ArtifactBasis
    current_subtitle_basis: ArtifactBasis | None
    current_presentation_basis: ArtifactBasis | None
    admitted: bool = True


class _PlannerInputObservation:
    """规划器私有的生成输入观测。

    观测按待提交的改名映射找到当前落盘位置、按规范路径作答，并为读到的每个依赖文件登记内容
    摘要，供激活的稳定性闸门复核。登记条目在激活模式下查本轮已规划的条目——清单此时尚未
    写入，本轮规划的结果就是即将提交的清单；提交后模式查磁盘上的清单，一次规划只读一份快照。
    """

    def __init__(self, planner: TargetStatePlanner) -> None:
        self._planner = planner
        self._committed: Mapping[ArtifactKey, ArtifactManifestEntry] | None = None

    def observe(self, artifact_path: str) -> ObservedFile | None:
        planner = self._planner
        observation = planner.observe(planner._pending_source(artifact_path))
        if observation.blocker is not None or not observation.present:
            return None
        path = planner.project_dir.joinpath(*Path(observation.artifact_path).parts)
        return ObservedFile(
            artifact_path=planner._canonical_target(artifact_path, observation.artifact_path),
            path=path,
            content_digest=planner._track_dependency_digest(path),
        )

    def registered(self, key: ArtifactKey) -> ArtifactManifestEntry | None:
        planner = self._planner
        if planner._activation_mode:
            return planner.entries.get(key)
        if key in planner.pending_entries:
            return planner.pending_entries[key]
        if self._committed is None:
            snapshot = planner.manifest_snapshot
            self._committed = planner.adapter.snapshot_entries() if snapshot is None else snapshot.entries
        return self._committed.get(key)


class SharedVersionMetadata:
    """一次比对或对账内各 planner 共享的 ``versions/versions.json`` 解析结果。

    每个 planner 照常检查、读取并登记版本历史的字节；只有读到的字节与这里记下的完全相同时
    才复用解析结果，字节不同就重新解析并替换，因此文件被替换、删除或损坏后不会返回陈旧结果。
    解析结果是同一份对象，被所有使用方共享：使用方不得修改它，包括嵌套的版本记录。对外类型只标为
    ``Mapping``，只能约束顶层，嵌套结构靠这条约定保证。
    """

    __slots__ = ("_parsed", "_raw")

    def __init__(self) -> None:
        self._raw: bytes | None = None
        self._parsed: Mapping[str, Any] | None = None

    def parse(self, raw: bytes, decode: Callable[[bytes], Mapping[str, Any]]) -> Mapping[str, Any]:
        if self._parsed is not None and self._raw == raw:
            return self._parsed
        parsed = decode(raw)
        self._raw, self._parsed = raw, parsed
        return parsed


def _same_present_file(first: Path, second: Path) -> bool:
    """两个在场路径是否同一个文件。缓存已清理的远端工作文件不在本地，不会与另一路径是同一文件。"""

    try:
        return first.samefile(second)
    except FileNotFoundError:
        return False


def _refusal_reason(refused: InputRefused) -> str:
    return "generation input refused: " + ", ".join(
        f"{gap.code}({gap.asset_type}: {gap.name})" if gap.asset_type else f"{gap.code}({gap.name})"
        for gap in refused.reasons
    )


class TargetStatePlanner:
    def __init__(
        self,
        project_dir: Path,
        *,
        episode_scope: int | None = None,
        project_bytes: bytes | None = None,
        allow_stale_formal_targets: bool = False,
        pending_renames: Mapping[str, str] | None = None,
        pending_entries: Mapping[ArtifactKey, ArtifactManifestEntry | None] | None = None,
        audio_rules: AudioBasisRules = CURRENT_AUDIO_RULES,
        duration_override: VideoDurationOverride | None = None,
        legacy_audio_entries: Mapping[ArtifactKey, ArtifactManifestEntry] | None = None,
        shared_versions: SharedVersionMetadata | None = None,
        runtime: RuntimePlanningContext | None = None,
        parsed_project: dict[str, Any] | None = None,
        manifest_snapshot: ArtifactManifestSnapshot | None = None,
        shared_observations: dict[str, ArtifactObservation] | None = None,
    ) -> None:
        self.project_dir = project_dir.resolve(strict=True)
        store = SQLiteHistory(self.project_dir)
        self.history_store = store if store.path.is_file() else None
        self._history_path = store.path
        self.adapter = ProjectArtifactManifestAdapter(
            self.project_dir, legacy_history=runtime.history if runtime else None
        )
        self.project_path = self.project_dir / "project.json"
        self.pending_renames = dict(pending_renames or {})
        self.pending_entries = dict(pending_entries or {})
        self.audio_rules = audio_rules
        self.duration_override = duration_override
        self.legacy_audio_entries = legacy_audio_entries
        self.manifest_snapshot = manifest_snapshot
        self.shared_observations = shared_observations
        if episode_scope is not None and (type(episode_scope) is not int or episode_scope < 1):
            raise ValueError("episode scope must be a positive integer or null")
        self.episode_scope = episode_scope
        self.allow_stale_formal_targets = allow_stale_formal_targets
        self.runtime = runtime
        self.absent_paths: set[Path] = set()
        self.grid_records: frozenset[str] | None = None
        self.project_bytes = (
            self._read_required_control_file("project.json", "project.json")
            if project_bytes is None
            else bytes(project_bytes)
        )
        project = parsed_project if parsed_project is not None else self._parse_json(self.project_bytes, "project.json")
        if not isinstance(project, dict):
            raise ValueError("project.json must contain an object")
        self.project = cast(dict[str, Any], project)
        self.dependencies: dict[Path, bytes] = {}
        self.dependency_digests: dict[Path, str] = {}
        self.script_paths: list[Path] = []
        self.bindings: list[_EpisodeBinding] = []
        self.episodes: list[EpisodeScriptInputs] = []
        self._bindings_loaded = False
        self._episodes_loaded = False
        self.entries: dict[ArtifactKey, ArtifactManifestEntry] = {}
        self.bases: dict[ArtifactKey, ArtifactBasis] = {}
        self.current_presentation_bases: dict[ArtifactKey, ArtifactBasis] = {}
        self.formal_paths: dict[ArtifactKey, str] = {}
        self.unselected_media_formal_paths: dict[ArtifactKey, str] = {}
        self._path_owners: dict[str, ArtifactKey] = {}
        self.skipped: list[MigrationSkippedArtifact] = []
        self._versions: Mapping[str, Any] | None = None
        self._shared_versions = shared_versions or SharedVersionMetadata()
        self._activation_mode = False
        self._planned: set[str] = set()
        self._resolved_keys: set[ArtifactKey] = set()
        self._records_gaps = project_basis_records_gaps(self.project)
        self._unreadable_grid_ids: set[str] = set()
        self._grid_members: dict[tuple[int, str | None], dict[str, tuple[ArtifactKey, ArtifactBasis]]] = {}

    def _legacy_audio_entries(self) -> Mapping[ArtifactKey, ArtifactManifestEntry] | None:
        """Audio claims for legacy narrated durations; ``None`` reads them from storage."""

        if self.legacy_audio_entries is not None:
            return self.legacy_audio_entries
        if self._activation_mode:
            return self.entries
        return None if self.manifest_snapshot is None else self.manifest_snapshot.entries

    def plan(self) -> ArtifactTargetStatePlan:
        schema = parse_project_schema_version(self.project)
        if schema not in _ACTIVATION_SCHEMA_VERSIONS:
            raise ValueError(
                f"artifact activation requires schema in {sorted(_ACTIVATION_SCHEMA_VERSIONS)}, got {schema!r}"
            )

        self._activation_mode = True
        try:
            self.adapter.get_entry(ArtifactKey.episode_script(1))
            script_records = control_file_names(self.project_dir / "scripts")
            self.load_episodes()
            self._plan_assets()
            self._plan_script_plans()
            self._plan_structured_content()
            self._plan_grids()
            self._plan_storyboards()
            self._plan_typed_media()
            self._plan_persisted_presentations()
            self._plan_bgm()
        except ProjectMigrationError:
            raise
        except (ArtifactManifestError, ValueError) as exc:
            raise ProjectMigrationError(str(exc), file="project.json") from exc
        return ArtifactTargetStatePlan(
            entries=dict(self.entries),
            formal_paths=dict(self.formal_paths),
            unselected_media_formal_paths=dict(self.unselected_media_formal_paths),
            project=dict(self.project),
            project_bytes=self.project_bytes,
            absent_paths=frozenset(self.absent_paths),
            grid_records=self.grid_records,
            script_records=script_records,
            dependency_bytes=dict(self.dependencies),
            dependency_digests=dict(self.dependency_digests),
            script_paths=tuple(self.script_paths),
            skipped=tuple(self.skipped),
        )

    def project_registered_entries(self, keys: Collection[ArtifactKey]) -> dict[ArtifactKey, ArtifactManifestEntry]:
        """Project the requested manifest keys under this planner's schema and rules."""
        for key in keys:
            self._plan_key(key)
        return {key: self.entries[key] for key in keys if key in self.entries}

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

        if key in self._resolved_keys:
            return
        kind = key.kind.value
        if kind == "asset-sheet":
            self._plan_asset_key(key)
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
            self._plan_grids(str(key.components[1]))
        elif kind == "episode-storyboard":
            self.load_episodes()
            self._plan_storyboards(str(key.components[1]))
        elif kind in {"episode-video", "episode-audio"}:
            self.load_episodes()
            self._plan_typed_media(str(key.components[1]), kind)
        elif kind in {"episode-subtitle", "episode-presentation"}:
            self.load_episodes()
            self._plan_persisted_presentations(str(key.components[1]))
        elif kind == "episode-final-cut":
            self.load_episodes()
            self._plan_final_cut(key)
        elif kind == "episode-jianying-draft":
            self.load_episodes()
            self._plan_jianying_draft(key)
        elif kind == "project-bgm":
            self._plan_bgm()
        self._resolved_keys.add(key)

    def load_episode_bindings(self) -> None:
        if self._bindings_loaded:
            return
        if self.runtime is not None:
            self._use_runtime_bindings(self.runtime.episode_bindings(self.project))
        else:
            self.bindings = _episode_bindings(self.project)
            self._bindings_loaded = True

    def _use_runtime_bindings(self, bindings: Mapping[int, _EpisodeBinding]) -> None:
        if self.episode_scope is None:
            self.bindings = list(bindings.values())
        else:
            binding = bindings.get(self.episode_scope)
            self.bindings = [binding] if binding is not None else []
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
        observation = self.observe(binding.script_file)
        if observation.blocker is not None:
            raise ArtifactManifestError(observation.blocker.detail)
        if not observation.present:
            return
        raw_script = observation.content_bytes
        if raw_script is None:
            raw_script = self._read_dependency(binding.script_file, "episode script")
        episode = EpisodeScriptInputs.parse(self.project_dir, binding, raw_script)
        self.script_paths.append(episode.script_path)
        self.episodes.append(episode)
        self._record_formal_path(
            ArtifactKey.episode_script(binding.episode),
            observation.artifact_path,
        )

    def _plan_assets(self) -> None:
        """规划资产图与衍生资产图：每个本体先于它的衍生，激活模式下衍生能看到本轮已规划的本体。"""

        if "assets" in self._planned:
            return
        style = self.project.get("style", "")
        style_description = self.project.get("style_description", "")
        if not isinstance(style, str) or not isinstance(style_description, str):
            raise ValueError("project visual style fields must be strings")
        observation = _PlannerInputObservation(self)
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
                self._plan_owner_sheet(asset_type, spec, name, raw_entry, observation, self.project)
                if spec.supports_derivatives:
                    self._plan_asset_derivatives(spec, name, raw_entry, observation, self.project)
        self._planned.add("assets")

    def _plan_asset_key(self, key: ArtifactKey) -> None:
        asset_type, asset_id = cast(tuple[str, str], key.components)
        spec = ASSET_SPECS[asset_type]
        owner, derivative = (
            split_derivative_artifact_id(asset_id)
            if spec.supports_derivatives and is_derivative_artifact_id(asset_id)
            else (asset_id, None)
        )
        bucket = self.project.get(spec.bucket_key)
        entry = self._asset_by_identity(bucket, owner)
        if entry is None:
            return
        observation = _PlannerInputObservation(self)
        project = {**self.project, spec.bucket_key: {owner: entry}}
        if derivative is None:
            self._plan_owner_sheet(asset_type, spec, owner, entry, observation, project)
            return
        child = self._asset_by_identity(entry.get(DERIVATIVES_FIELD), derivative)
        if child is not None:
            narrowed = {**entry, DERIVATIVES_FIELD: {derivative: child}}
            project[spec.bucket_key] = {owner: narrowed}
            self._plan_asset_derivatives(spec, owner, narrowed, observation, project)

    def _asset_by_identity(self, bucket: object, identity: str) -> Mapping[str, Any] | None:
        if not isinstance(bucket, Mapping):
            return None
        entry = bucket.get(identity)
        if entry is None and self.runtime is not None:
            alias = self.runtime.asset_aliases.get(id(bucket), {}).get(identity)
            entry = bucket.get(alias) if alias is not None else None
        elif entry is None:
            entry = next(
                (
                    value
                    for name, value in bucket.items()
                    if isinstance(name, str) and asset_name_comparison_key(name) == identity
                ),
                None,
            )
        return entry if isinstance(entry, Mapping) else None

    def _plan_owner_sheet(
        self,
        asset_type: str,
        spec: AssetSpec,
        name: str,
        raw_entry: Mapping[str, Any],
        observation: _PlannerInputObservation,
        project: Mapping[str, Any],
    ) -> None:
        artifact_path = raw_entry.get(spec.sheet_field)
        if isinstance(artifact_path, str) and artifact_path:
            key = ArtifactKey.asset_sheet(asset_type, name)
            uploaded_basis = self._uploaded_sheet_basis(asset_type, spec.bucket_key, name, artifact_path)
            if uploaded_basis is not None:
                self._add_if_present(key, artifact_path, uploaded_basis)
            else:
                self._plan_generated_image(
                    key,
                    artifact_path,
                    lambda asset_type=asset_type, name=name: asset_sheet_input(
                        project, asset_type=asset_type, name=name, observation=observation
                    ),
                    label="asset sheet",
                )

    def _uploaded_sheet_basis(
        self,
        asset_type: str,
        resource_type: str,
        resource_id: str,
        artifact_path: str,
    ) -> ArtifactBasis | None:
        """作为成品带入的资产图或衍生资产图按图本身投影依据；否则返回 ``None``，改按生成输入投影。

        作者上传与从资产库应用的图都以一条选中的手动上传版本记录为凭据，成立条件见
        ``_selected_upload_digest``。
        """

        bucket = self._load_versions([(resource_type, resource_id)], equivalent=True).get(resource_type)
        if not isinstance(bucket, Mapping):
            return None
        history_key = resolve_asset_key(bucket, resource_id)
        digest = self._selected_upload_digest(
            bucket.get(history_key) if history_key is not None else None, resource_type, artifact_path
        )
        if digest is None:
            return None
        return build_uploaded_asset_sheet_basis(asset_type=asset_type, content_digest=digest)

    def _selected_upload_digest(self, history: object, resource_type: str, artifact_path: str) -> str | None:
        """选中版本是与声明产物字节一致的手动上传时，返回上传字节的摘要；否则返回 ``None``。

        成立条件：该资源选中的版本记录是手动上传，且它的受管快照与声明的产物字节一致。外部
        替换了产物、或选中的是生成的版本，都不按上传依据判定。
        """

        snapshot_rel = selected_manual_upload_snapshot(history, resource_type)
        if snapshot_rel is None:
            return None
        artifact = self._safe_present_path(self._pending_source(artifact_path))
        snapshot = self._safe_present_path(snapshot_rel)
        if artifact is None or snapshot is None:
            return None
        try:
            if _same_present_file(artifact, snapshot):
                return None
            artifact_digest = visual_file_digest(artifact)
            snapshot_digest = visual_file_digest(snapshot)
        except OSError:
            return None
        self._remember_dependency_digest(artifact, artifact_digest)
        self._remember_dependency_digest(snapshot, snapshot_digest)
        if artifact_digest != snapshot_digest:
            return None
        return artifact_digest

    def _plan_asset_derivatives(
        self,
        spec: AssetSpec,
        owner_name: str,
        entry: Mapping[str, Any],
        observation: _PlannerInputObservation,
        project: Mapping[str, Any],
    ) -> None:
        """规划一个本体条目下全部衍生资产图的规范依据。

        生成的衍生图是对本体资产图的一次编辑，规范依据因此由「本体资产图的内容 + 变化描述」
        决定，不含项目画风。本体资产图重生成后其内容指纹改变，该本体下每一张生成的衍生图的
        登记依据随之与规范状态不符，即判过期。作为成品带入的衍生图按图本身投影依据，与本体
        资产图和描述无关。
        """
        table = entry.get(DERIVATIVES_FIELD)
        if not isinstance(table, Mapping):
            return
        for raw_name, raw_derivative in table.items():
            if not isinstance(raw_name, str) or not isinstance(raw_derivative, Mapping):
                continue
            derivative_name = asset_name_comparison_key(raw_name)
            artifact_path = raw_derivative.get(spec.sheet_field)
            if not derivative_name or not isinstance(artifact_path, str) or not artifact_path:
                continue
            key = derivative_artifact_key(owner_name, derivative_name)
            uploaded_basis = self._uploaded_sheet_basis(
                DERIVATIVE_ASSET_TYPE,
                CHARACTER_DERIVATIVE_RESOURCE_TYPE,
                derivative_artifact_id(owner_name, derivative_name),
                artifact_path,
            )
            if uploaded_basis is not None:
                self._add_if_present(key, artifact_path, uploaded_basis)
                continue
            self._plan_generated_image(
                key,
                artifact_path,
                lambda derivative_name=derivative_name: derivative_sheet_input(
                    project, owner=owner_name, derivative=derivative_name, observation=observation
                ),
                label="derivative sheet",
            )

    def _plan_generated_image(
        self,
        key: ArtifactKey,
        artifact_path: str,
        generation_input: Callable[[], ImageGenerationInput[Any]],
        *,
        label: str,
    ) -> None:
        """按生成输入投影期望依据，缺口记为缺席；结构损坏时不登记、进迁移报告。

        schema < 18 的口径下有缺口即不登记、进迁移报告。
        """

        try:
            assembled = generation_input()
            refusal = assembled.refusal
            if refusal is not None and not self._records_gaps:
                self._skip(key, artifact_path, _refusal_reason(refusal))
                if self.allow_stale_formal_targets:
                    observed = self.observe(self._pending_source(artifact_path))
                    if observed.blocker is None and observed.present:
                        self._record_formal_path(key, self._canonical_target(artifact_path, observed.artifact_path))
                return
            basis = assembled.expected_basis()
        except (OSError, TypeError, ValueError) as exc:
            self._skip(key, artifact_path, f"invalid {label} generation input: {exc}")
            return
        self._add_if_present(key, artifact_path, basis)

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
        try:
            script_basis = build_episode_script_basis(project=self.project)
        except (TypeError, ValueError):
            script_basis = None
        if script_basis is not None:
            for episode in self.episodes:
                self._add_if_present(
                    ArtifactKey.episode_script(episode.episode),
                    episode.script_file,
                    script_basis,
                )
        self._planned.add("structured-content")

    def _plan_script_plans(self) -> None:
        for binding in self.bindings:
            if self.episode_scope is None or binding.episode == self.episode_scope:
                self._plan_one_script_plan(binding)

    def _plan_one_script_plan(self, binding: _EpisodeBinding) -> None:
        if self.project.get("content_mode") not in {"narration", "drama"}:
            return
        script_plan_path = script_review.script_plan_path(self.project_dir, self.project, binding.episode)
        if script_plan_path is None:
            return
        script_plan_rel = script_plan_path.relative_to(self.project_dir).as_posix()
        observation = self.observe(self._pending_source(script_plan_rel))
        script_plan_key = ArtifactKey.episode_script_plan(binding.episode)
        if observation.blocker is not None and self._activation_mode:
            self._skip_blocked(script_plan_key, script_plan_rel, observation.blocker)
            return
        if observation.blocker is not None or not observation.present:
            return
        try:
            script_plan_raw = self._read_dependency(script_plan_rel, "formal script_plan")
            self._parse_json(script_plan_raw, f"formal script_plan {script_plan_rel}")
        except ValueError:
            if not self._activation_mode:
                raise
            self._skip(script_plan_key, script_plan_rel, "script_plan draft cannot be read", category="unreadable")
            return
        entry = episode_entry(self.project, binding.episode)
        if entry is not None and SOURCE_ORIGIN_FIELD in entry and episode_source_origin(entry) is SourceOrigin.NONE:
            return
        source_rel = episode_source_relpath(binding.episode)
        source_observation = self.observe(source_rel)
        if source_observation.blocker is None and source_observation.present:
            source_raw = self._read_dependency(source_rel, "episode source")
            try:
                source_content = decode_script_plan_source(source_raw)
            except UnicodeDecodeError as exc:
                if not self._activation_mode:
                    raise ValueError(f"episode source {source_rel} is not UTF-8") from exc
                self._skip(script_plan_key, script_plan_rel, f"episode source {source_rel} is not UTF-8")
                return
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

    def _plan_storyboards(self, target: str | None = None) -> None:
        if "storyboards" in self._planned:
            return
        if self.project.get("generation_mode") != "storyboard":
            self._planned.add("storyboards")
            return
        style = self.project.get("style", "")
        style_description = self.project.get("style_description", "")
        if not isinstance(style, str) or not isinstance(style_description, str):
            raise ValueError("project storyboard style and style description must be strings")
        observation = _PlannerInputObservation(self)
        if self.runtime is not None and self.runtime.catalog is None:
            self.runtime.catalog = build_reference_catalog(self.project)
        catalog = self.runtime.catalog if self.runtime is not None else build_reference_catalog(self.project)
        raw_histories = self._load_versions(
            [("storyboards", target)] if target is not None else self._episode_resources(("storyboards",))
        ).get("storyboards")
        storyboard_histories: Mapping[str, Any] = raw_histories if isinstance(raw_histories, Mapping) else {}
        for episode in self.episodes:
            storyboard_items, id_field, *_ = get_storyboard_items(episode.script)
            if target is not None:
                storyboard_items = [episode.by_id[target]] if storyboard_items and target in episode.by_id else []
            grid_members = self._grid_members_by_resource(episode.episode) if target is None else {}
            for item in storyboard_items:
                resource_id = str(item[id_field])
                assets = item.get("generated_assets")
                if not isinstance(assets, Mapping) or item.get("needs_replan") is True:
                    continue
                artifact_path = assets.get("storyboard_image")
                if not isinstance(artifact_path, str) or not artifact_path:
                    continue
                key = ArtifactKey.episode_storyboard(episode.episode, resource_id)
                uploaded = self._selected_upload_digest(
                    storyboard_histories.get(resource_id), "storyboards", artifact_path
                )
                if uploaded is not None:
                    self._add_if_present(key, artifact_path, build_uploaded_storyboard_basis(content_digest=uploaded))
                    continue
                if assets.get("grid_id") is not None or assets.get("grid_cell_index") is not None:
                    if target is not None and isinstance(grid_id := assets.get("grid_id"), str):
                        grid_members = self._grid_members_by_resource(episode.episode, grid_id)
                    grid_target = grid_members.get(resource_id)
                    if grid_target is not None:
                        key, basis = grid_target
                        self._add_if_present(key, artifact_path, basis)
                    elif assets.get("grid_id") in self._unreadable_grid_ids:
                        self._skip(key, artifact_path, "grid record of this storyboard cannot be read")
                    continue
                self._plan_generated_image(
                    key,
                    artifact_path,
                    lambda episode=episode, resource_id=resource_id: storyboard_image_input(
                        self.project,
                        episode.script,
                        episode=episode.episode,
                        resource_id=resource_id,
                        observation=observation,
                        catalog=catalog,
                        item_index=episode.positions[resource_id],
                    ),
                    label="storyboard",
                )
        if target is None:
            self._planned.add("storyboards")

    def _plan_grids(self, grid_id: str | None = None) -> None:
        if "grids" in self._planned:
            return
        for grid in self.load_grid_records(grid_id):
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
            grid_references = self._grid_references(grid)
            if members is None or grid_references is None:
                continue
            references, gaps = grid_references
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
                    style_description=project_basis_style_description(self.project),
                    grid_aspect_ratio=grid_aspect_ratio_for(grid.rows, grid.cols, member_ratio),
                    references=references,
                    absent=gaps,
                    asset_names=project_basis_records_asset_names(self.project),
                )
            except (OSError, TypeError, ValueError):
                continue
            self._add_if_present(ArtifactKey.episode_grid(grid.episode, grid.id), grid.grid_image_path, basis)
        if grid_id is None:
            self._planned.add("grids")

    def _grid_members_by_resource(
        self,
        episode_number: int,
        grid_id: str | None = None,
    ) -> dict[str, tuple[ArtifactKey, ArtifactBasis]]:
        cache_key = episode_number, grid_id
        if cache_key in self._grid_members:
            return self._grid_members[cache_key]
        result: dict[str, tuple[ArtifactKey, ArtifactBasis]] = {}
        for grid in self.load_grid_records(grid_id):
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
            grid_references = self._grid_references(grid)
            composite_path = self._safe_present_path(grid.grid_image_path)
            if members is None or grid_references is None or composite_path is None:
                continue
            references, gaps = grid_references
            member_ratio = grid.video_aspect_ratio or self.project.get("aspect_ratio") or "9:16"
            if not isinstance(member_ratio, str):
                continue
            by_id = episode.by_id
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
                        style_description=project_basis_style_description(self.project),
                        member_aspect_ratio=member_ratio,
                        references=references,
                        source_composite_digest=composite_digest,
                        absent=gaps,
                        asset_names=project_basis_records_asset_names(self.project),
                    )
                except (OSError, TypeError, ValueError):
                    continue
                result[resource_id] = (
                    ArtifactKey.episode_storyboard(grid.episode, resource_id),
                    basis,
                )
        self._grid_members[cache_key] = result
        return result

    def _grid_visual_members(
        self,
        grid: GridGeneration,
        episode: EpisodeScriptInputs,
    ) -> tuple[GridStoryboardVisual, ...] | None:
        by_id = episode.by_id
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

    def _grid_references(self, grid: GridGeneration) -> tuple[tuple[VisualReference, ...], tuple[InputGap, ...]] | None:
        """宫格记录冻结的参考图与读不到的那几张；读不到的记为缺口（schema < 18 时整张宫格不投影）。"""

        references: list[VisualReference] = []
        gaps: list[InputGap] = []
        for raw in grid.reference_images or []:
            path = self._safe_present_path(raw.path)
            if path is None:
                if not self._records_gaps:
                    return None
                gaps.append(InputGap(SHEET_MISSING_CODE, raw.ref_type, raw.name))
                continue
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
        return tuple(references), tuple(gaps)

    def load_grid_records(self, grid_id: str | None = None) -> tuple[GridGeneration, ...]:
        if grid_id is not None:
            filename = f"{grid_id}.json"
            if not _GRID_RECORD_RE.fullmatch(filename):
                return ()
            rel = f"grids/{filename}"
            observation = self.observe(rel)
            if observation.blocker is not None:
                raise ArtifactManifestError(observation.blocker.detail)
            return (self._load_grid_record(self.project_dir / rel, rel),) if observation.present else ()
        cached = getattr(self, "_grids", None)
        if cached is not None:
            return cast(tuple[GridGeneration, ...], cached)
        grids_dir = self.project_dir / "grids"
        loaded: list[GridGeneration] = []
        self.grid_records = control_file_names(grids_dir)
        for filename in sorted(self.grid_records):
            path = grids_dir / filename
            rel = path.relative_to(self.project_dir).as_posix()
            try:
                observation = self.observe(rel)
                blocker = observation.blocker
                if self._activation_mode and blocker is not None and blocker.code == "artifact_unreadable":
                    raise ValueError(blocker.detail)
                loaded.append(self._load_grid_record(path, rel))
            except ValueError:
                if not self._activation_mode:
                    raise
                grid_id = path.name.removesuffix(".json")
                self._unreadable_grid_ids.add(grid_id)
                self.skipped.append(
                    MigrationSkippedArtifact(
                        kind=ArtifactKind.EPISODE_GRID.value,
                        resource_id=grid_id,
                        artifact_path=rel,
                        reason=f"grid record {rel} cannot be read",
                        category="unreadable",
                    )
                )
        result = tuple(loaded)
        self._grids = result
        return result

    def _load_grid_record(self, path: Path, rel: str) -> GridGeneration:
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
        return grid

    @staticmethod
    def _items_for(episode: EpisodeScriptInputs, target: str | None) -> Sequence[dict[str, Any]]:
        if target is None:
            return episode.items
        item = episode.by_id.get(target)
        return (item,) if item is not None else ()

    def _plan_typed_media(self, target: str | None = None, kind: str | None = None) -> None:
        if "typed-media" in self._planned:
            return
        for episode in self.episodes:
            for item in self._items_for(episode, target):
                resource_id = str(item[episode.id_field])
                assets = item.get("generated_assets")
                if not isinstance(assets, Mapping):
                    continue
                audio_path = assets.get("narration_audio")
                if (
                    isinstance(audio_path, str)
                    and audio_path
                    and item.get("needs_replan") is not True
                    and kind != "episode-video"
                ):
                    self._plan_one_typed_media(
                        episode=episode,
                        item=item,
                        resource_id=resource_id,
                        resource_type="audio",
                        artifact_path=audio_path,
                        key=ArtifactKey.episode_audio(episode.episode, resource_id),
                    )
                video = (
                    episode.video_input(resource_id, record_gaps=self._records_gaps)
                    if kind != "episode-audio"
                    else None
                )
                if video is not None:
                    resource_type, video_path = video
                    self._plan_one_typed_media(
                        episode=episode,
                        item=item,
                        resource_id=resource_id,
                        resource_type=resource_type,
                        artifact_path=video_path,
                        key=ArtifactKey.episode_video(episode.episode, resource_id),
                    )
        if target is None:
            self._planned.add("typed-media")

    def _skip(
        self,
        key: ArtifactKey,
        artifact_path: str,
        reason: str,
        *,
        category: SkippedArtifactCategory | None = None,
    ) -> None:
        """记一件没登记的产物；``category`` 为空表示依据算不出，只记账、不给用户看。"""

        self.skipped.append(
            MigrationSkippedArtifact.for_key(key, artifact_path=artifact_path, reason=reason, category=category)
        )

    def _skip_blocked(self, key: ArtifactKey, artifact_path: str, blocker: ArtifactBlocker) -> None:
        """文件在场却被拦下：只有文件本身读不出才进「未能带入」，符号链接、非普通文件只记账。"""

        category: SkippedArtifactCategory | None = "unreadable" if blocker.code == "artifact_unreadable" else None
        self._skip(key, artifact_path, blocker.detail, category=category)

    def _plan_one_typed_media(
        self,
        *,
        episode: EpisodeScriptInputs,
        item: Mapping[str, Any],
        resource_id: str,
        resource_type: str,
        artifact_path: str,
        key: ArtifactKey,
    ) -> None:
        if artifact_path != resource_relative_path(resource_type, resource_id):
            self._skip(key, artifact_path, "script points at a non-canonical media path")
            return
        resource_bucket = self._load_versions([(resource_type, resource_id)]).get(resource_type)
        resource = resource_bucket.get(resource_id) if isinstance(resource_bucket, Mapping) else None
        if not isinstance(resource, Mapping):
            self._skip(key, artifact_path, "version history has no record for this media")
            return
        record = selected_media_record(resource)
        if record is None:
            self._skip(key, artifact_path, "version history has no selected version")
            return
        if is_uploaded_video_version(resource_type, record):
            if self.manifest_snapshot is None:
                uploaded = self._selected_upload_digest(resource, resource_type, artifact_path)
            elif selected_manual_upload_snapshot(resource, resource_type) is not None:
                uploaded = self._registered_media_digest(key, artifact_path)
            else:
                uploaded = None
            if uploaded is None:
                self._skip(key, artifact_path, "uploaded video snapshot cannot be verified")
                return
            try:
                basis = uploaded_video_version_basis(record, snapshot_digest=uploaded)
            except ValueError as exc:
                self._skip(key, artifact_path, str(exc))
                return
            self.bases[key] = basis
            self.entries[key] = self._media_entry(artifact_path, basis.digest, uploaded)
            return
        try:
            target = parse_typed_media_version_target(resource_type, record, read_audio=self.audio_rules.read)
        except (TypeError, ValueError):
            self._skip(
                key,
                artifact_path,
                "selected version lacks typed provenance",
                category="legacy_audio_basis" if resource_type == "audio" else None,
            )
            return
        if target.episode != episode.episode or normalize_script_binding(target.script_file) != episode.script_file:
            self._skip(key, artifact_path, "selected version was generated for another episode or script")
            return
        snapshot_rel = record.get("file")
        if not VersionManager.is_managed_snapshot_path(resource_type, snapshot_rel):
            self._skip(key, artifact_path, "selected version snapshot is not a managed history path")
            return
        if self.manifest_snapshot is not None:
            artifact_digest = self._registered_media_digest(key, artifact_path)
            if artifact_digest is None:
                self._skip(key, artifact_path, "current media file is not the registered version")
                return
        else:
            artifact_digest = self._prove_media_snapshot(key, artifact_path, cast(str, snapshot_rel))
            if artifact_digest is None:
                return
        try:
            if resource_type == "audio":
                current_basis = self.audio_rules.project(
                    project=self.project,
                    item=item,
                    skeleton_kind=episode.kind,
                    version_record=record,
                )
            else:
                current_basis = build_current_video_artifact_basis(
                    project_path=self.project_dir,
                    project=self.project,
                    script=episode.script,
                    item_index=episode.positions[resource_id],
                    resource_type=resource_type,
                    resource_id=resource_id,
                    version_metadata=record,
                    duration_override=self.duration_override,
                    legacy_audio_entries=self._legacy_audio_entries(),
                )
        except (KeyError, OSError, TypeError, ValueError):
            self._skip(key, artifact_path, "current basis cannot be projected from the script")
            return
        if current_basis is None:
            self._skip(key, artifact_path, "current basis cannot be projected from the script")
            return
        if self._activation_mode and not self.allow_stale_formal_targets and current_basis != target.basis:
            self._skip(key, artifact_path, "selected version was generated from content that is no longer current")
            return
        self.entries[key] = self._media_entry(artifact_path, current_basis.digest, artifact_digest)

    def _prove_media_snapshot(self, key: ArtifactKey, artifact_path: str, snapshot_rel: str) -> str | None:
        """激活与登记前的证明：正式文件与选中版本快照字节一致时返回其内容指纹；不成立时记账并返回 ``None``。"""

        observation = self.observe(artifact_path)
        if observation.blocker is not None:
            self._skip_blocked(key, artifact_path, observation.blocker)
            return None
        snapshot_observation = self.observe(snapshot_rel)
        if snapshot_observation.blocker is not None and snapshot_observation.blocker.code == "artifact_unreadable":
            self._skip(key, artifact_path, "selected version snapshot cannot be read", category="unreadable")
            return None
        artifact = self._safe_present_path(artifact_path)
        snapshot = self._safe_present_path(snapshot_rel)
        if artifact is None or snapshot is None:
            self._skip(key, artifact_path, "current media file or its version snapshot is missing")
            return None
        try:
            if artifact.samefile(snapshot):
                self._skip(key, artifact_path, "current media file is the version snapshot itself")
                return None
            artifact_digest = visual_file_digest(artifact)
        except OSError:
            self._skip(key, artifact_path, "current media file cannot be read", category="unreadable")
            return None
        try:
            snapshot_digest = visual_file_digest(snapshot)
        except OSError:
            self._skip(key, artifact_path, "selected version snapshot cannot be read", category="unreadable")
            return None
        self._remember_dependency_digest(artifact, artifact_digest)
        self._remember_dependency_digest(snapshot, snapshot_digest)
        if artifact_digest != snapshot_digest:
            self._skip(key, artifact_path, "current media file differs from the selected version snapshot")
            return None
        return artifact_digest

    def _registered_media_digest(self, key: ArtifactKey, artifact_path: str) -> str | None:
        """时效判定：正式文件就是清单登记的那个版本时返回登记的内容指纹，否则 ``None``。

        字节数与纳秒 mtime 都与登记的内容事实一致就认定没变，不读文件内容；任一项不一致才完整取指纹
        与登记的比对。登记的 mtime 不早于清单文件自身的 mtime 时，同一时间戳内的改写可能不改变任何
        stat 项，按 racy 处理，退化为完整比对（同 git 以 index 文件为参照的做法）。没有内容事实的
        条目无从证明，返回 ``None``。
        """

        assert self.manifest_snapshot is not None
        observation, digest = inspect_registered_media(
            self.adapter, self.manifest_snapshot, key, artifact_path, self.observe(artifact_path)
        )
        if self.shared_observations is not None:
            self.shared_observations[artifact_path] = observation
        return digest

    def _media_entry(self, artifact_path: str, basis_digest: str, proven_digest: str) -> ArtifactManifestEntry:
        """视频与音频的规划条目。激活模式下证明成立时顺带记下正式文件的内容事实（与 v18 补录同一条规则）：
        size 与 mtime 取自重新取指纹的同一描述符，指纹须与证明时一致。"""

        entry = ArtifactManifestEntry(artifact_path=artifact_path, basis_digest=basis_digest)
        if not self._activation_mode:
            return entry
        observation = self.adapter.inspect_artifact_content(artifact_path)
        if observation.content_digest != proven_digest or observation.file_stat is None:
            return entry
        return replace(
            entry,
            content=ArtifactContentFacts(
                digest=proven_digest,
                size=observation.file_stat.st_size,
                mtime_ns=observation.file_stat.st_mtime_ns,
            ),
        )

    def _plan_persisted_presentations(self, target: str | None = None) -> None:
        """Rebuild only complete, internally provable persisted presentation pairs."""

        if "persisted-presentations" in self._planned:
            return
        self._plan_typed_media(target)
        for episode in self.episodes:
            for item in self._items_for(episode, target):
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
                        if self.allow_stale_formal_targets:
                            self._record_unselected_media_presentation(
                                episode=episode,
                                item=item,
                                resource_id=resource_id,
                                variant=variant,
                                subtitle_path=subtitle_path,
                                presentation_path=presentation_path,
                                subtitle=subtitle,
                                presentation=presentation,
                            )
                        continue
                    presentation_key = ArtifactKey.episode_presentation(episode.episode, resource_id, variant)
                    if proof.current_presentation_basis is not None and proof.admitted:
                        self.current_presentation_bases[presentation_key] = proof.current_presentation_basis
                    subtitle_basis = (
                        proof.frozen.subtitle_basis if self._activation_mode else proof.current_subtitle_basis
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
                        self.entries[presentation_key] = ArtifactManifestEntry(
                            artifact_path=presentation_path,
                            basis_digest=presentation_basis.digest,
                        )
        if target is None:
            self._planned.add("persisted-presentations")

    def proven_narrated_presentations(
        self,
    ) -> Iterator[tuple[str, str, Mapping[str, Any], SpeechPresentation, SpeechPreparation]]:
        """Yield verified persisted TTS presentations, including those bound to historical snapshots."""
        self.load_episodes()
        self._plan_typed_media()
        for episode in self.episodes:
            for item in episode.items:
                unit = str(item[episode.id_field])
                sub, pres = presentation_artifact_paths(episode.episode, unit, USE_TTS)
                presentation = self._read_optional_json_artifact(pres)
                subtitle = self._read_optional_json_artifact(sub)
                if presentation is None or subtitle is None:
                    continue
                proof = self._prove_persisted_presentation(
                    episode=episode,
                    item=item,
                    resource_id=unit,
                    variant=USE_TTS,
                    subtitle_path=sub,
                    presentation_path=pres,
                    subtitle=subtitle,
                    presentation=presentation,
                    allow_unselected_media=True,
                    require_current_basis=False,
                )
                if proof is not None:
                    yield sub, pres, presentation, proof.frozen, proof.preparation

    def _record_unselected_media_presentation(
        self,
        *,
        episode: EpisodeScriptInputs,
        item: Mapping[str, Any],
        resource_id: str,
        variant: RenditionVariant,
        subtitle_path: str,
        presentation_path: str,
        subtitle: Mapping[str, Any],
        presentation: Mapping[str, Any],
    ) -> None:
        """单元换了选中视频或音频后，按旧版本物化的字幕与呈现暂不适用。

        只有对它们自己依据的视频与音频版本仍完整自证时才记下正式路径；其余证明不了的原因照常不记。
        """

        proof = self._prove_persisted_presentation(
            episode=episode,
            item=item,
            resource_id=resource_id,
            variant=variant,
            subtitle_path=subtitle_path,
            presentation_path=presentation_path,
            subtitle=subtitle,
            presentation=presentation,
            allow_unselected_media=True,
        )
        if proof is None:
            return
        self.unselected_media_formal_paths[ArtifactKey.episode_subtitle(episode.episode, resource_id, variant)] = (
            subtitle_path
        )
        self.unselected_media_formal_paths[ArtifactKey.episode_presentation(episode.episode, resource_id, variant)] = (
            presentation_path
        )

    def _timeline(self, episode: int, timeline_id: str):
        from lib.edit_timeline.store import document_relpath, read_timeline_document

        relative = document_relpath(episode, timeline_id)
        if self._safe_present_path(relative) is None:
            return None
        return read_timeline_document(
            self.project_dir,
            episode,
            timeline_id,
            read_bytes=lambda _: self._read_dependency(relative, "edit timeline"),
        )

    def _plan_final_cut(self, key: ArtifactKey) -> None:
        """Rebuild one final cut basis from its edit timeline's latest revision.

        Final cuts are only registered by the render that produced them; activation
        never plans them, so a present file without its render-time claim reads missing.
        Narrated or subtitled versions also record each referenced unit's material layer,
        derived like the Jianying draft's from the current presentations.
        """

        from lib.final_cut.basis import (
            FinalCutVariant,
            SubtitleMode,
            current_video_from_history,
            final_cut_artifact_path,
            final_cut_basis,
            output_profile_for_project,
            resolve_final_cut_inputs,
            video_resource_type_for,
        )

        episode_number, timeline_id, narration, subtitles = key.components
        episode = next((candidate for candidate in self.episodes if candidate.episode == episode_number), None)
        if episode is None or type(episode_number) is not int or not isinstance(timeline_id, str):
            return
        document = self._timeline(episode_number, timeline_id)
        if document is None:
            return
        resource_type = video_resource_type_for(episode.kind)
        variant = FinalCutVariant(narration=cast(DraftNarration, narration), subtitles=cast(SubtitleMode, subtitles))
        inputs = resolve_final_cut_inputs(
            document=document,
            revision=document.latest,
            variant=variant,
            profile=output_profile_for_project(self.project, episode.kind),
            script_unit_ids=episode.by_id.keys(),
            video_of=lambda unit_id: current_video_from_history(
                self.project_dir,
                self._load_versions([(resource_type, unit_id)]).get(resource_type, {}).get(unit_id, {}),
                resource_type,
                unit_id,
                self._formal_content_digest,
            ),
            bgm_sources=self._bgm_sources(document.latest.content.bgm),
        )
        if inputs.missing_video_units:
            return
        if variant.consumes_unit_materials:
            self._plan_persisted_presentations()
            items = episode.by_id
            history = self._load_versions(("audio", name) for name in items)
            inputs = replace(
                inputs,
                units=tuple(
                    self._draft_unit_basis(
                        episode=episode,
                        item=items[unit_id],
                        unit_id=unit_id,
                        narration=variant.narration,
                        versions=history,
                    )
                    for unit_id in draft_unit_ids(document.latest.content, items)
                ),
            )
        self._add_if_present(
            key, final_cut_artifact_path(episode_number, timeline_id, variant), final_cut_basis(inputs)
        )

    def _plan_bgm(self) -> None:
        """上传的 BGM 按字节登记：依据只有正式文件当前字节的内容指纹，文件在场即可证明。"""

        if "bgm" in self._planned:
            return
        for track in read_bgm_library(self.project).values():
            key = bgm_key(track.id)
            observation = self.adapter.inspect_artifact_content(track.file)
            if observation.blocker is not None:
                self._skip_blocked(key, track.file, observation.blocker)
                continue
            if not observation.present or observation.content_digest is None:
                if self.runtime is None and not observation.present:
                    self.absent_paths.add(self.project_dir / observation.artifact_path)
                self._skip(key, track.file, "uploaded BGM file is not present")
                continue
            self._remember_dependency_digest(self.project_dir / observation.artifact_path, observation.content_digest)
            self._add_if_present(key, track.file, bgm_basis(observation.content_digest))
        self._planned.add("bgm")

    def _bgm_sources(self, bgm: Sequence[BgmClip]) -> dict[str, BgmSource]:
        ids = bgm_ids(bgm)
        if self.runtime is None:
            library = read_bgm_library(self.project)
            for bgm_id in ids:
                track = library.get(bgm_id)
                if track is not None:
                    self.observe(track.file)
        return resolve_bgm_sources(self.project_dir, self.project, ids, self._formal_content_digest)

    def _formal_content_digest(self, artifact_path: str) -> str:
        observation = self.adapter.inspect_artifact_content(artifact_path)
        if observation.blocker is not None:
            raise ArtifactManifestError(observation.blocker.detail)
        if not observation.present or observation.content_digest is None:
            raise ValueError(f"formal artifact input is not present: {observation.artifact_path}")
        self._remember_dependency_digest(self.project_dir / observation.artifact_path, observation.content_digest)
        return observation.content_digest

    def _plan_jianying_draft(self, key: ArtifactKey) -> None:
        """按剪辑时间线的最新修订与各单元当前的呈现模型重建剪映草稿的依据。

        剪映草稿只在渲染时登记，不参与整份激活。剪辑时间线或集已不在时不给出目标；某个视频单元
        没有可证明的当前素材层时，依据把它记为不可用，已登记的草稿读作过期、登记保留。
        """

        episode_number, timeline_id, narration = cast(tuple[int, str, DraftNarration], key.components)
        episode = next((candidate for candidate in self.episodes if candidate.episode == episode_number), None)
        if episode is None:
            return
        document = self._timeline(episode_number, timeline_id)
        if document is None:
            return
        self._plan_persisted_presentations()
        resource_type = "reference_videos" if episode.kind == "video_units" else "videos"
        items = episode.by_id
        versions = self._load_versions((kind, name) for name in items for kind in ("audio", resource_type))
        units = [
            self._draft_unit_basis(
                episode=episode,
                item=items[unit_id],
                unit_id=unit_id,
                narration=narration,
                versions=versions,
            )
            for unit_id in draft_unit_ids(document.latest.content, items)
        ]
        bucket = versions.get(resource_type)
        video_versions = {}
        for unit in units:
            history = bucket.get(unit.unit_id) if isinstance(bucket, Mapping) else None
            if isinstance(history, Mapping) and type(version := history.get("current_version")) is int:
                video_versions[unit.unit_id] = version
        basis = build_jianying_draft_basis(
            timeline_id=timeline_id,
            revision=document.latest,
            narration=narration,
            aspect_ratio=resolve_video_aspect_ratio(self.project, resource_type),
            units=units,
            video_versions=video_versions,
            bgm_sources=self._bgm_sources(document.latest.content.bgm),
        )
        self._add_if_present(key, jianying_draft_artifact_path(episode_number, timeline_id, narration), basis)

    def _draft_unit_basis(
        self,
        *,
        episode: EpisodeScriptInputs,
        item: Mapping[str, Any],
        unit_id: str,
        narration: DraftNarration,
        versions: Mapping[str, Any],
    ) -> DraftUnitBasis:
        audio_bucket = versions.get("audio")
        audio = audio_bucket.get(unit_id) if isinstance(audio_bucket, Mapping) else None
        audio_version = audio.get("current_version") if isinstance(audio, Mapping) else None
        effective = effective_unit_variant(
            narration,
            admit_script_unit(episode.kind, item).mode,
            has_narration_audio=type(audio_version) is int and audio_version > 0,
        )
        presentation = self.current_presentation_bases.get(
            ArtifactKey.episode_presentation(episode.episode, unit_id, effective)
        )
        if presentation is None:
            return DraftUnitBasis(unit_id, unavailable=True)
        return DraftUnitBasis(unit_id, presentation_digest=presentation.digest)

    def _prove_persisted_presentation(
        self,
        *,
        episode: EpisodeScriptInputs,
        item: Mapping[str, Any],
        resource_id: str,
        variant: RenditionVariant,
        subtitle_path: str,
        presentation_path: str,
        subtitle: Mapping[str, Any],
        presentation: Mapping[str, Any],
        allow_unselected_media: bool = False,
        require_current_basis: bool = True,
    ) -> _PersistedPresentationProof | None:
        """Validate a frozen typed presentation and derive its current basis.

        ``allow_unselected_media`` 为真时，视频与音频可按各自记录的历史版本核验（见 :meth:`_presentation_media_pair`）。
        """

        resource_type = "reference_videos" if episode.kind == "video_units" else "videos"
        video_pair = self._presentation_media_pair(
            presentation.get("video"),
            episode=episode,
            resource_id=resource_id,
            resource_type=resource_type,
            allow_unselected=allow_unselected_media,
            require_current_basis=require_current_basis,
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
                allow_unselected=allow_unselected_media,
                require_current_basis=require_current_basis,
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
        if frozen_video.evidence.basis.kind == UPLOADED_VIDEO_BASIS_KIND and provider_audio_enabled is not True:
            return None
        try:
            frozen = materialize_speech_presentation(
                frozen_preparation,
                variant=variant,
                video=frozen_video,
                narration_audio=frozen_audio,
                provider_audio_enabled=provider_audio_enabled,
                subtitle_sentences_prepared=True,
            )
        except (TypeError, ValueError):
            return None
        frozen_presentation_basis = frozen.presentation_basis
        expected_presentation = {
            "episode": episode.episode,
            "resource_type": resource_type,
            "script_file": Path(episode.script_file).name,
            "subtitle_artifact_path": subtitle_path,
            "presentation_artifact_path": presentation_path,
            "persisted": True,
            **frozen.to_dict(),
        }
        legacy_transition = presentation.get("transition_to_next")
        if (
            isinstance(legacy_transition, str)
            and parse_project_schema_version(self.project) < PRESENTATION_WITHOUT_TRANSITION_SCHEMA_VERSION
        ):
            frozen_presentation_basis = build_legacy_transition_presentation_basis(
                frozen.presentation_basis, legacy_transition
            )
            expected_presentation["transition_to_next"] = legacy_transition
            expected_presentation["presentation_basis"] = ArtifactBasisDescriptor.from_basis(
                frozen_presentation_basis
            ).to_dict()
        if dict(subtitle) != frozen.subtitle_artifact_dict() or dict(presentation) != expected_presentation:
            return None

        current_subtitle: ArtifactBasis | None = None
        current_presentation: ArtifactBasis | None = None
        admission = admit_script_unit(episode.kind, item)
        if admission.allowed:
            try:
                current = materialize_speech_presentation(
                    admission.preparation,
                    variant=variant,
                    video=current_video,
                    narration_audio=current_audio,
                    provider_audio_enabled=provider_audio_enabled,
                )
            except (TypeError, ValueError):
                pass
            else:
                current_subtitle = current.subtitle_basis
                current_presentation = current.presentation_basis
        elif self._records_gaps:
            try:
                current_subtitle = build_blocked_subtitle_basis(
                    admission.preparation,
                    variant=variant,
                    video=current_video.evidence,
                    narration_audio=current_audio.evidence if current_audio is not None else None,
                )
                current_presentation = build_presentation_basis(
                    variant=variant,
                    video=current_video.evidence,
                    subtitle=current_subtitle,
                    narration_audio=current_audio.evidence if current_audio is not None else None,
                    provider_audio_enabled=provider_audio_enabled,
                )
            except (TypeError, ValueError):
                current_subtitle = current_presentation = None
        return _PersistedPresentationProof(
            frozen=frozen,
            preparation=frozen_preparation,
            frozen_presentation_basis=frozen_presentation_basis,
            current_subtitle_basis=current_subtitle,
            current_presentation_basis=current_presentation,
            admitted=admission.allowed,
        )

    def _presentation_media_pair(
        self,
        raw: object,
        *,
        episode: EpisodeScriptInputs,
        resource_id: str,
        resource_type: str,
        allow_unselected: bool = False,
        require_current_basis: bool = True,
    ) -> tuple[PresentationMedia, PresentationMedia] | None:
        """Prove one selected media snapshot and expose frozen/current currency.

        ``allow_unselected`` 为真时也可核验非选中的历史版本：它的快照不在正式路径上，
        不比对正式文件字节，只核验快照本身与登记的内容摘要一致。
        """

        if not isinstance(raw, Mapping) or raw.get("selection") != "current":
            return None
        key = (
            ArtifactKey.episode_audio(episode.episode, resource_id)
            if resource_type == "audio"
            else ArtifactKey.episode_video(episode.episode, resource_id)
        )
        planned = self.entries.get(key)
        canonical = resource_relative_path(resource_type, resource_id)
        if (require_current_basis and planned is None) or (planned is not None and planned.artifact_path != canonical):
            return None
        proven_version = raw.get("version")
        if type(proven_version) is not int:
            return None
        versions = self._load_versions([(resource_type, resource_id)], proof_version=proven_version)
        bucket = versions.get(resource_type)
        resource = bucket.get(resource_id) if isinstance(bucket, Mapping) else None
        selected_version = resource.get("current_version") if isinstance(resource, Mapping) else None
        records = resource.get("versions") if isinstance(resource, Mapping) else None
        if type(selected_version) is not int or not isinstance(records, list):
            return None
        unselected = proven_version != selected_version
        if unselected and not allow_unselected:
            return None
        selected = [
            record for record in records if isinstance(record, Mapping) and record.get("version") == proven_version
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
        snapshot = self._safe_present_path(cast(str, snapshot_path))
        artifact = self._safe_present_path(canonical) if not unselected else None
        if snapshot is None or (not unselected and artifact is None):
            return None
        try:
            content_digest = media_content_digest(snapshot)
            if artifact is not None and (
                snapshot.samefile(artifact) or media_content_digest(artifact) != content_digest
            ):
                return None
            embedded_basis = ArtifactBasisDescriptor.from_dict(raw.get("basis"))
            if is_uploaded_video_version(resource_type, record):
                expected_basis = ArtifactBasisDescriptor.from_basis(
                    uploaded_video_version_basis(record, snapshot_digest=unprefixed(content_digest))
                )
            else:
                target = parse_typed_media_version_target(resource_type, record, read_audio=self.audio_rules.read)
                if (
                    target.episode != episode.episode
                    or normalize_script_binding(target.script_file) != episode.script_file
                ):
                    return None
                expected_basis = target.basis
            if embedded_basis != expected_basis:
                return None
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
            version=proven_version,
            selection="current",
            currency=cast(Any, frozen_currency),
            evidence=evidence,
        )
        current = PresentationMedia(
            artifact_path=frozen.artifact_path,
            version=frozen.version,
            selection=frozen.selection,
            currency="current" if planned is not None and planned.basis_digest == embedded_basis.digest else "stale",
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
            if self.runtime is None and self.dependencies.setdefault(path, raw) != raw:
                raise RuntimeError(f"artifact activation dependency changed during preflight: {path}")
            parsed = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return None
        if not isinstance(parsed, Mapping):
            return None
        return cast(Mapping[str, Any], parsed)

    def _episode_resources(self, kinds: tuple[str, ...]):
        for episode in self.episodes:
            for item in episode.items:
                for kind in kinds:
                    yield kind, str(item[episode.id_field])

    def _load_versions(
        self, keys: Iterable[tuple[str, str]], *, equivalent: bool = False, proof_version: int | None = None
    ) -> Mapping[str, Any]:
        if self.runtime is not None:
            return self.runtime.selected_history(
                self.project_dir,
                self.adapter,
                self.history_store,
                keys,
                equivalent=equivalent,
                proof_version=proof_version,
            )
        if self.history_store is not None:
            store = self.history_store
            revision = store.revision()
            if self.dependencies.setdefault(store.path, revision) != revision:
                raise ArtifactManifestError("version history changed during planning")
            if equivalent:
                keys = [pair for kind, name in keys for pair in store.equivalent_keys(kind, name)]
            return store.resources(keys)
        self.absent_paths.add(self._history_path)
        if self._versions is not None:
            return self._versions
        relative = "versions/versions.json"
        observation = self.observe(relative)
        if observation.blocker is not None:
            raise ArtifactManifestError(observation.blocker.detail)
        if not observation.present:
            self._versions = {}
            return self._versions
        raw = self._read_dependency(relative, "version metadata")
        self._versions = self._shared_versions.parse(raw, self._parse_versions)
        return self._versions

    @classmethod
    def _parse_versions(cls, raw: bytes) -> Mapping[str, Any]:
        parsed = cls._parse_json(raw, "version metadata")
        if not isinstance(parsed, dict):
            raise ValueError("version metadata must contain an object")
        return parsed

    def _add_if_present(self, key: ArtifactKey, artifact_path: str, basis: ArtifactBasis) -> None:
        existing_basis = self.bases.get(key)
        if existing_basis is not None and existing_basis != basis:
            raise ValueError(f"multiple canonical bases claim artifact key {key.encode()}")
        self.bases[key] = basis
        observation = self.observe(self._pending_source(artifact_path))
        if observation.blocker is not None and self._activation_mode:
            self._skip_blocked(key, artifact_path, observation.blocker)
            return
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

        if self.runtime is not None:
            return
        previous = self.dependency_digests.setdefault(path, digest)
        if previous != digest:
            raise RuntimeError(f"artifact activation dependency changed during preflight: {path}")

    def _safe_present_path(self, relative_path: str) -> Path | None:
        observation = self.observe(relative_path)
        if observation.blocker is not None or not observation.present:
            return None
        return self.project_dir.joinpath(*Path(observation.artifact_path).parts)

    def observe(self, relative_path: str) -> ArtifactObservation:
        """Inspect a formal path, through the owning resolver's request-scoped inspections when there is one."""

        snapshot = self.runtime.script_snapshot if self.runtime is not None else None
        if snapshot is not None and snapshot.artifact_path == relative_path:
            return snapshot
        if self.shared_observations is None:
            observation = self.adapter.inspect_artifact(relative_path)
            if self.runtime is None and observation.blocker is None and not observation.present:
                self.absent_paths.add(self.project_dir / observation.artifact_path)
            return observation
        observation = self.shared_observations.get(relative_path)
        if observation is None:
            observation = self.shared_observations[relative_path] = self.adapter.inspect_artifact(relative_path)
        return observation

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
        if self.runtime is None:
            path = self._canonical_path(relative_path, observation.artifact_path)
            if self.dependencies.setdefault(path, raw) != raw:
                raise RuntimeError(f"artifact activation dependency changed during preflight: {path}")
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
    audio_rules: AudioBasisRules = CURRENT_AUDIO_RULES,
    duration_override: VideoDurationOverride | None = None,
    pending_renames: Mapping[str, str] | None = None,
) -> ArtifactTargetStatePlan:
    """Perform the complete read-only activation preflight.

    ``pending_renames``（规范相对路径 → 当前落盘相对路径）供 schema 迁移使用：迁移在同一步里
    把输入改名到规范路径，预检必须在改名落盘之前完成，因而按旧名读取、按规范路径记录。计划
    里的依赖路径与产物路径因此都是提交时刻的事实，激活的稳定性闸门可直接复用。
    """

    return TargetStatePlanner(
        project_dir, audio_rules=audio_rules, duration_override=duration_override, pending_renames=pending_renames
    ).plan()


def plan_artifact_target_state_by_gap_rule(
    project_dir: Path,
    project: Mapping[str, Any],
    *,
    record_gaps: bool,
    audio_rules: AudioBasisRules = CURRENT_AUDIO_RULES,
) -> ArtifactTargetStatePlan:
    """按指定的缺口口径规划一次完整目标态，不必先落盘。

    ``record_gaps`` 为假时按 schema 17 的口径（有生成缺口即投影不出、不登记），为真时按 schema 18
    的口径（缺口记为缺席、照常登记，``docs/adr/0098``）。两次规划的差集就是因缺口被跳过、现在
    可以补录的产物，见 :func:`gap_backfill_entries`。
    """

    schema_version = GAP_TOLERANT_BASIS_SCHEMA_VERSION - (0 if record_gaps else 1)
    project_bytes = json.dumps({**project, "schema_version": schema_version}, ensure_ascii=False).encode("utf-8")
    return TargetStatePlanner(project_dir, audio_rules=audio_rules, project_bytes=project_bytes).plan()


def gap_backfill_entries(
    stored: Mapping[ArtifactKey, ArtifactManifestEntry],
    before: Mapping[ArtifactKey, ArtifactManifestEntry],
    after: Mapping[ArtifactKey, ArtifactManifestEntry],
) -> dict[ArtifactKey, ArtifactManifestEntry]:
    """缺口即不登记的口径下规划不出（``before``）、缺口记为缺席后规划得出（``after``）、清单里也没有的条目。

    改前就有目标却不在清单里的，是出于别的原因撤下的登记，不在这里补回；已登记条目一律不动。
    """

    return {key: entry for key, entry in after.items() if key not in before and key not in stored}


def episode_scope_for_key(key: ArtifactKey) -> int | None:
    """Return the one episode whose control files may affect ``key``."""

    if key.kind in PROJECT_LEVEL_ARTIFACT_KINDS:
        return None
    episode = key.components[0]
    if type(episode) is not int:
        raise ValueError("episode artifact key has no positive episode identity")
    return episode


def inspect_registered_media(
    adapter: ProjectArtifactManifestAdapter,
    snapshot: ArtifactManifestSnapshot,
    key: ArtifactKey,
    path: str,
    observation: ArtifactObservation,
) -> tuple[ArtifactObservation, str | None]:
    entry = snapshot.entries.get(key)
    facts = entry.content if entry is not None and entry.artifact_path == path else None
    stat = observation.file_stat
    if facts is None or observation.blocker is not None or not observation.present or stat is None:
        return observation, None
    if (
        snapshot.file_stat is not None
        and facts.mtime_ns < snapshot.file_stat.st_mtime_ns
        and (stat.st_size, stat.st_mtime_ns) == (facts.size, facts.mtime_ns)
    ):
        return observation, facts.digest
    observation = adapter.inspect_artifact_content(path)
    return observation, facts.digest if observation.content_digest == facts.digest else None


def selected_media_record(resource: Mapping[str, Any]) -> Mapping[str, Any] | None:
    version = resource.get("current_version")
    records = resource.get("versions")
    if type(version) is not int or not isinstance(records, list):
        return None
    selected = [r for r in records if isinstance(r, Mapping) and r.get("version") == version]
    return selected[0] if len(selected) == 1 else None


def normalize_script_binding(value: str) -> str:
    normalized = value.replace("\\", "/")
    if normalized.startswith("scripts/"):
        normalized = normalized.removeprefix("scripts/")
    if not normalized or "/" in normalized or normalized in {".", ".."}:
        raise ValueError(f"invalid episode script binding: {value!r}")
    return f"scripts/{normalized}"
