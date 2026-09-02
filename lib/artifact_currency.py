"""产物时新性判定。

在不写盘的前提下回答「磁盘上这份产物是否仍等于规范状态应有的那份」，
以及由此派生的剧集身份解析与可用性分类。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactComparison,
    ArtifactKey,
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.artifact_planner import TargetStatePlanner, episode_scope_for_key
from lib.project_migration_failure import ProjectMigrationError
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION, parse_project_schema_version, project_schema_is_current


def read_artifact_content_digest(adapter: ProjectArtifactManifestAdapter, artifact_path: str) -> str:
    """Hash one safely admitted formal path without requiring an active Manifest."""

    observation = adapter.inspect_artifact_content(artifact_path)
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

    from lib.reference_video.duration_migration import migrate_script_unit_durations

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
    """Side-effect-free runtime comparison against canonical target state."""

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = Path(project_dir)
        root_planner = TargetStatePlanner(project_dir)
        if not project_schema_is_current(root_planner.project):
            raise ProjectMigrationError("Artifact Manifest is not activated for this project schema")
        # Validate the sidecar once even when a workflow phase has no artifacts
        # to compare.  A corrupt active manifest is a blocker, never an empty
        # target state or permission to fall back to filesystem existence.
        root_planner.adapter.get_entry(ArtifactKey.episode_script(1))
        self._project_bytes = root_planner.project_bytes
        self._planners: dict[int | None, TargetStatePlanner] = {None: root_planner}
        self._adapter = root_planner.adapter
        self._manifest = ArtifactManifest(self._adapter)

    def _planner_for(self, key: ArtifactKey) -> TargetStatePlanner:
        scope = episode_scope_for_key(key)
        planner = self._planners.get(scope)
        if planner is None:
            planner = TargetStatePlanner(
                self._project_dir,
                episode_scope=scope,
                project_bytes=self._project_bytes,
            )
            self._planners[scope] = planner
        return planner

    def compare(self, key: ArtifactKey, *, artifact_path: str) -> ArtifactComparison:
        # Admission precedes basis reconstruction.  An unclaimed or unsafe
        # formal path is not an input to the planner, so malformed orphan files
        # cannot block the workflow that is responsible for replacing them.
        admission = self._manifest.compare_entry(key, artifact_path=artifact_path, expected=None)
        if admission.status in {ArtifactStatus.MISSING, ArtifactStatus.BLOCKED}:
            return admission
        expected = self._planner_for(key).resolve_key(key)
        return self._manifest.compare_entry(key, artifact_path=artifact_path, expected=expected)

    def resolve_usable_entry(self, key: ArtifactKey, *, artifact_path: str) -> ArtifactManifestEntry | None:
        """Return the exact registered entry selected through canonical admission."""

        comparison = self.compare(key, artifact_path=artifact_path)
        if comparison.status is ArtifactStatus.BLOCKED:
            assert comparison.blocker is not None
            raise ArtifactManifestError(comparison.blocker.detail)
        if comparison.status not in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}:
            return None
        entry = self._adapter.get_entry(key)
        if entry is None or entry.artifact_path != comparison.artifact_path:
            return None
        return entry

    def compare_frozen_entry(self, key: ArtifactKey, entry: ArtifactManifestEntry) -> ArtifactComparison:
        """Compare the current formal claim with one provider-selected entry."""

        return self._manifest.compare_entry(key, artifact_path=entry.artifact_path, expected=entry)

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
    return ArtifactCurrencyResolver(project_dir)


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

    from lib.project_manager import ProjectManager, resolve_episode_script_binding

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


def resolve_current_artifact_target(project_dir: Path, key: ArtifactKey) -> ArtifactManifestEntry | None:
    """Resolve one formal post-commit target without repairing any other key."""

    return TargetStatePlanner(project_dir, episode_scope=episode_scope_for_key(key)).resolve_key(key)
