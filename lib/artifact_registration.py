"""产物注册与遗忘。

一次成功的正式写入（或任务冻结证据）在清单里落成 current 条目；
无法证明的替换则移除 claim，宁可判 missing 也不留下无据条目。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from lib.artifact_currency import resolve_current_artifact_basis, resolve_current_artifact_target
from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactEntryRekeyPlan,
    ArtifactKey,
    ArtifactKind,
    ArtifactManifest,
    ArtifactManifestAdapter,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ProjectArtifactManifestAdapter,
)
from lib.artifact_planner import TargetStatePlanner, normalize_script_binding
from lib.asset_types import ASSET_SPECS
from lib.project_migration_failure import ProjectMigrationError
from lib.project_schema import project_schema_is_current
from lib.resource_paths import resource_relative_path


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
    planner = TargetStatePlanner(project_dir)
    if not project_schema_is_current(planner.project):
        raise ProjectMigrationError("Artifact Manifest is not activated for this project schema")
    if resource_type == "grids":
        grid = next((candidate for candidate in planner._load_grid_records() if candidate.id == resource_id), None)
        if grid is None:
            raise KeyError(resource_id)
        planner._load_episode_bindings()
        binding = next((candidate for candidate in planner.bindings if candidate.episode == grid.episode), None)
        if binding is None or binding.script_file != normalize_script_binding(grid.script_file):
            raise ValueError("formal grid no longer matches an episode script binding")
        return ArtifactKey.episode_grid(grid.episode, resource_id)
    if script_file is not None:
        planner._load_episode_bindings()
        normalized = normalize_script_binding(script_file)
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
    """Resolve one resource's canonical basis."""

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

    if not entries:
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

    adapter = ProjectArtifactManifestAdapter(project_dir)
    try:
        snapshot = adapter.snapshot_entries()
    except ArtifactManifestError:
        return adapter.repair_path_conflicted_entries_atomically(
            lambda entries: {
                key: entry for key, entry in entries.items() if key.kind is not kind or key.components[1] != resource_id
            }
        )
    keys = [key for key in snapshot if key.kind is kind and key.components[1] == resource_id]
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
