"""产物时新性判定。

在不写盘的前提下回答「磁盘上这份产物是否仍等于规范状态应有的那份」，
以及由此派生的剧集身份解析与可用性分类。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
    ArtifactComparison,
    ArtifactKey,
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ArtifactObservation,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_planner import (
    EpisodeScriptInputs,
    RuntimePlanningContext,
    TargetStatePlanner,
    episode_scope_for_key,
    inspect_registered_media,
    normalize_script_binding,
    selected_media_record,
)
from lib.artifacts.artifact_version_provenance import is_uploaded_video_version, parse_typed_media_version_target
from lib.artifacts.input_gap import project_basis_records_gaps
from lib.artifacts.sqlite_history import SQLiteHistory
from lib.artifacts.version_manager import VersionManager, selected_manual_upload_snapshot
from lib.project.project_migration_failure import ProjectMigrationError
from lib.project.project_schema import (
    CURRENT_PROJECT_SCHEMA_VERSION,
    parse_project_schema_version,
    project_schema_is_current,
)
from lib.project.resource_paths import resource_relative_path


def read_artifact_content_digest(
    adapter: ProjectArtifactManifestAdapter,
    artifact_path: str,
    *,
    chunk_observer: Callable[[bytes], None] | None = None,
) -> str:
    """Hash one safely admitted formal path without requiring an active Manifest."""

    observation = adapter.inspect_artifact_content(artifact_path, chunk_observer=chunk_observer)
    if observation.blocker is not None:
        raise ArtifactManifestError(observation.blocker.detail)
    if not observation.present:
        raise ValueError(f"formal artifact input is no longer registered: {observation.artifact_path}")
    if observation.content_digest is None:
        raise ArtifactManifestError(f"formal artifact input has no content digest: {observation.artifact_path}")
    return observation.content_digest


def read_artifact_content_snapshot(
    adapter: ProjectArtifactManifestAdapter,
    artifact_path: str,
) -> tuple[bytes, str]:
    """Read one safely admitted artifact and its digest from one descriptor."""

    observation = adapter.inspect_artifact_snapshot(artifact_path)
    if observation.blocker is not None:
        raise ArtifactManifestError(observation.blocker.detail)
    if not observation.present:
        raise ValueError(f"formal artifact input is no longer registered: {observation.artifact_path}")
    if observation.content_bytes is None or observation.content_digest is None:
        raise ArtifactManifestError(f"formal artifact input has no content snapshot: {observation.artifact_path}")
    return observation.content_bytes, observation.content_digest


def decode_script_content_snapshot(content: bytes, artifact_path: str) -> dict[str, Any]:
    """Decode the exact script bytes used to establish a formal input claim."""

    from lib.script.reference_video.duration_migration import migrate_script_unit_durations

    try:
        script = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"episode script is not valid UTF-8 JSON: {artifact_path}") from exc
    if not isinstance(script, dict):
        raise ValueError(f"episode script must contain an object: {artifact_path}")
    migrate_script_unit_durations(script)
    return script


class ArtifactComparer(Protocol):
    """一件产物的可用性判定入口：完整时新性比对与只看登记的轻量判定共用。"""

    def compare(self, key: ArtifactKey, *, artifact_path: str) -> ArtifactComparison: ...


class RegisteredArtifactResolver:
    """只按「清单已登记且文件在场」判定产物，不重建规范状态。

    与 ``ArtifactCurrencyResolver`` 的差别是不回答「是否比当前内容旧」：清单一次读入
    内存，每件产物只做一次路径准入检查（在场探针只读一个字节），不哈希产物内容、
    不构造目标态规划。
    因此它只会返回 current / missing / blocked，登记在案的过期产物一律按 current 报告。
    适用于项目列表这类只需要「有几件可用」的广度视图；工作台的决策仍走完整比对。
    """

    def __init__(self, project_dir: Path, project: Mapping[str, Any]) -> None:
        if not project_schema_is_current(project):
            raise ProjectMigrationError("Artifact Manifest is not activated for this project schema")
        self._adapter = ProjectArtifactManifestAdapter(Path(project_dir).resolve(strict=True))
        self._entries = self._adapter.snapshot_entries()

    def compare(self, key: ArtifactKey, *, artifact_path: str) -> ArtifactComparison:
        observation = self._adapter.inspect_artifact(artifact_path)
        if observation.blocker is not None:
            return ArtifactComparison(
                status=ArtifactStatus.BLOCKED,
                artifact_path=observation.artifact_path,
                blocker=observation.blocker,
            )
        if not observation.present:
            return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=observation.artifact_path)
        entry = self._entries.get(key)
        if entry is None or entry.artifact_path != observation.artifact_path:
            return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=observation.artifact_path)
        return ArtifactComparison(status=ArtifactStatus.CURRENT, artifact_path=observation.artifact_path)


class ArtifactCurrencyResolver:
    """Side-effect-free runtime comparison against canonical target state.

    A resolver is request-scoped: it reads the Manifest once on construction and
    answers every comparison from that snapshot, so a Manifest replaced mid-request
    does not change this request's answers.
    """

    def __init__(self, project_dir: Path, *, project: dict[str, Any] | None = None) -> None:
        self._runtime = RuntimePlanningContext()
        self._project_dir = project_dir.resolve(strict=True)
        self._root: TargetStatePlanner | None = None
        if project is None:
            self._root = TargetStatePlanner(self._project_dir, runtime=self._runtime)
            project = self._root.project
        if not project_schema_is_current(project):
            raise ProjectMigrationError("Artifact Manifest is not activated for this project schema")
        self._availability_inputs: EpisodeScriptInputs | None = None
        self._fallback_script: ArtifactObservation | None = None
        self._record_gaps = project_basis_records_gaps(project)
        self._project = project
        self._runtime.index_asset_names(project)
        self._adapter = ProjectArtifactManifestAdapter(self._project_dir, legacy_history=self._runtime.history)
        self.manifest_snapshot = self._adapter.read_snapshot()
        if self._root is not None:
            self._root.manifest_snapshot = self.manifest_snapshot
            self._root.shared_observations = {}
        self._active: TargetStatePlanner | None = None
        history = SQLiteHistory(self._project_dir)
        self._history = history if history.path.is_file() else None
        self._manifest = ArtifactManifest(self._adapter)

    def script_snapshot(self, path: str) -> ArtifactObservation:
        snapshot = self._runtime.script_snapshot
        if snapshot is None or snapshot.artifact_path != path:
            snapshot = self._fallback_script
            if snapshot is None or snapshot.artifact_path != path:
                snapshot = self._adapter.inspect_artifact_snapshot(path)
            self._runtime.script_snapshot = snapshot
        return snapshot

    def retain_fallback_script(self, path: str | None) -> None:
        """Keep just the auto-selection fallback while the ledger scan advances."""
        current = self._runtime.script_snapshot
        self._fallback_script = current if current is not None and current.artifact_path == path else None

    def _registered_script(self, key: ArtifactKey, path: str) -> bool:
        entry = self.manifest_snapshot.entries.get(key)
        return key.kind.value == "episode-script" and entry is not None and entry.artifact_path == path

    def compare_availability(self, key: ArtifactKey, *, artifact_path: str) -> ArtifactComparison:
        observation = (
            self.script_snapshot(artifact_path)
            if self._registered_script(key, artifact_path)
            else self._adapter.inspect_artifact(artifact_path)
        )
        entries = self.manifest_snapshot.entries
        admission = self._manifest.compare_observed_entry(key, observation=observation, expected=None, entries=entries)
        if admission.status in {ArtifactStatus.MISSING, ArtifactStatus.BLOCKED}:
            return admission
        episode = int(key.components[0])
        binding = self._runtime.episode_bindings(self._project).get(episode)
        if binding is None:
            return admission
        inputs = self._availability_inputs
        if inputs is None or inputs.episode != episode:
            source = self.script_snapshot(binding.script_file)
            if source.blocker is not None:
                raise ArtifactManifestError(source.blocker.detail)
            if source.content_bytes is None:
                return admission
            inputs = self._availability_inputs = EpisodeScriptInputs.parse(
                self._project_dir, binding, source.content_bytes
            )
        if key.kind.value == "episode-video" and self._video_reads_registered_content(key, artifact_path, inputs):
            observed, _ = inspect_registered_media(
                self._adapter, self.manifest_snapshot, key, artifact_path, observation
            )
            return self._manifest.compare_observed_entry(key, observation=observed, expected=None, entries=entries)
        return admission

    def _video_reads_registered_content(self, key: ArtifactKey, path: str, inputs: EpisodeScriptInputs) -> bool:
        resource_id = str(key.components[1])
        video = inputs.video_input(resource_id, record_gaps=self._record_gaps)
        if video is None:
            return False
        resource_type, formal = video
        if formal != resource_relative_path(resource_type, resource_id):
            return False
        history = self._runtime.selected_history(
            self._project_dir, self._adapter, self._history, [(resource_type, resource_id)]
        )
        resource = history.get(resource_type, {}).get(resource_id, {})
        record = selected_media_record(resource)
        if record is None:
            return False
        if is_uploaded_video_version(resource_type, record):
            return formal == path and selected_manual_upload_snapshot(resource, resource_type) is not None
        try:
            target = parse_typed_media_version_target(resource_type, record)
        except (TypeError, ValueError):
            return False
        return (
            target.episode == key.components[0]
            and normalize_script_binding(target.script_file) == inputs.script_file
            and formal == path
            and VersionManager.is_managed_snapshot_path(resource_type, record.get("file"))
        )

    def release_episode(self) -> None:
        self._active = None

    def _planner_for(self, key: ArtifactKey) -> TargetStatePlanner:
        self._availability_inputs = None
        scope = episode_scope_for_key(key)
        if scope is None:
            if self._root is None:
                self._root = TargetStatePlanner(
                    self._project_dir,
                    parsed_project=self._project,
                    project_bytes=b"",
                    runtime=self._runtime,
                    manifest_snapshot=self.manifest_snapshot,
                    shared_observations={},
                )
            return self._root
        if self._active is None or self._active.episode_scope != scope:
            self.release_episode()
            self._active = TargetStatePlanner(
                self._project_dir,
                episode_scope=scope,
                project_bytes=b"",
                parsed_project=self._project,
                runtime=self._runtime,
                manifest_snapshot=self.manifest_snapshot,
                shared_observations={},
            )
        return self._active

    def compare(self, key: ArtifactKey, *, artifact_path: str) -> ArtifactComparison:
        planner = self._planner_for(key)
        if self._registered_script(key, artifact_path):
            self.script_snapshot(artifact_path)
        entries = self.manifest_snapshot.entries
        admission = self._manifest.compare_observed_entry(
            key, observation=planner.observe(artifact_path), expected=None, entries=entries
        )
        if admission.status in {ArtifactStatus.MISSING, ArtifactStatus.BLOCKED}:
            return admission
        expected = planner.resolve_key(key)
        return self._manifest.compare_observed_entry(
            key, observation=planner.observe(artifact_path), expected=expected, entries=entries
        )

    def resolve_basis(self, key: ArtifactKey) -> ArtifactBasis | None:
        """Canonical basis of *key* from this resolver's planners; ``None`` when none is derivable."""

        return self._planner_for(key).resolve_basis(key)

    def resolve_usable_entry(self, key: ArtifactKey, *, artifact_path: str) -> ArtifactManifestEntry | None:
        """Return the exact registered entry selected through canonical admission."""

        comparison = self.compare(key, artifact_path=artifact_path)
        if comparison.status is ArtifactStatus.BLOCKED:
            assert comparison.blocker is not None
            raise ArtifactManifestError(comparison.blocker.detail)
        if comparison.status not in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}:
            return None
        entry = self.manifest_snapshot.entries.get(key)
        if entry is None or entry.artifact_path != comparison.artifact_path:
            return None
        return entry

    def compare_frozen_entry(self, key: ArtifactKey, entry: ArtifactManifestEntry) -> ArtifactComparison:
        """Compare the current formal claim with one provider-selected entry."""

        return self._manifest.compare_entry(
            key, artifact_path=entry.artifact_path, expected=entry, entries=self.manifest_snapshot.entries
        )

    def artifact_content_digest(self, artifact_path: str) -> str:
        """Hash one safely admitted formal path for provider-input identity."""

        return read_artifact_content_digest(self._adapter, artifact_path)


def active_artifact_currency_resolver(
    project_dir: Path,
    project: Mapping[str, Any],
) -> ArtifactCurrencyResolver:
    """Return the resolver, refusing a project short of the current schema.

    The Manifest is the only reading rule for produced artifacts. A project that
    never reached the current schema has no backfilled claims to read, so it is
    refused with the migration verdict instead of being served from filesystem
    existence.
    """

    if not project_schema_is_current(project):
        raise ProjectMigrationError(
            f"project schema v{parse_project_schema_version(project)} did not reach v{CURRENT_PROJECT_SCHEMA_VERSION}",
            file="project.json",
        )
    return ArtifactCurrencyResolver(project_dir, project=project if isinstance(project, dict) else dict(project))


def resolve_artifact_episode(
    *,
    project: Mapping[str, object],
    script: dict[str, Any],
    script_filename: str,
) -> int:
    """Resolve the Manifest episode identity of one bound script.

    A positive identity bound in ``project.json`` is required: the canonical
    filename is valid evidence when the script omits its redundant top-level
    field, but an unbound or unreadable identity is refused rather than guessed.
    """

    from lib.project.project_manager import ProjectManager, resolve_episode_script_binding

    episode = ProjectManager.resolve_episode_from_script(script, script_filename)
    if episode < 1:
        raise ValueError("script episode must be a positive integer")
    if (
        resolve_episode_script_binding(
            project,
            episode,
            script_filename,
            require_indexed=True,
        )
        is None
    ):
        raise ValueError(f"script {script_filename} is not bound to episode {episode} in project.json")
    return episode


def artifact_is_usable(
    resolver: ArtifactCurrencyResolver,
    key: ArtifactKey,
    artifact_path: object,
) -> bool:
    """Classify selection eligibility without treating stale artifacts as missing.

    Only Manifest current/stale entries are usable; a blocked comparison fails
    loud so a damaged sidecar cannot trigger paid regeneration.
    """

    if not isinstance(artifact_path, str) or not artifact_path:
        return False
    comparison = resolver.compare(key, artifact_path=artifact_path)
    if comparison.status is ArtifactStatus.BLOCKED:
        assert comparison.blocker is not None
        raise ArtifactManifestError(comparison.blocker.detail)
    return comparison.status in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}


def resolve_current_artifact_basis(project_dir: Path, key: ArtifactKey) -> ArtifactBasis | None:
    """Resolve canonical evidence for a formal write before its bytes are selected."""

    return TargetStatePlanner(project_dir, episode_scope=episode_scope_for_key(key)).resolve_basis(key)


def resolve_current_artifact_target(
    project_dir: Path,
    key: ArtifactKey,
    *,
    pending_entries: Mapping[ArtifactKey, ArtifactManifestEntry | None] | None = None,
) -> ArtifactManifestEntry | None:
    """Resolve one formal post-commit target without repairing any other key."""

    return TargetStatePlanner(
        project_dir, episode_scope=episode_scope_for_key(key), pending_entries=pending_entries
    ).resolve_key(key)
