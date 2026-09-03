"""Eager Artifact Manifest target-state planning and activation.

The schema migration and archive-import boundary both call this module.  It is
the only place that reconstructs a complete manifest from canonical project
state; ordinary readers never repair or infer entries on first access.

规划、时新性判定、输入认领与注册各自独立成模块（``artifact_planner`` /
``artifact_currency`` / ``artifact_input_claims`` / ``artifact_registration``）；
本模块留下激活与提交编排，并作为四者对外的统一入口 re-export 全部公开符号。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

from lib.artifact_currency import (
    ArtifactComparer,
    ArtifactCurrencyResolver,
    RegisteredArtifactResolver,
    active_artifact_currency_resolver,
    artifact_is_usable,
    read_artifact_content_digest,
    resolve_artifact_episode,
    resolve_current_artifact_basis,
    resolve_current_artifact_target,
)
from lib.artifact_input_claims import (
    ArtifactInputClaim,
    EpisodeScriptInput,
    artifact_input_is_usable,
    assert_artifact_input_claims_usable,
    assert_current_artifact_input_claims_usable,
    bind_artifact_input_claims_to_content_digests,
    bind_artifact_input_claims_to_frozen_visuals,
    resolve_usable_artifact_input_claim,
    resolve_usable_episode_script_input,
    resolve_usable_storyboard_video_inputs,
    snapshot_usable_artifact_input_claim,
)
from lib.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactEntryRekeyReceipt,
    ArtifactKey,
    ArtifactKind,
    ArtifactManifestAdapter,
    ArtifactManifestArchiveSnapshot,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ProjectArtifactManifestAdapter,
)
from lib.artifact_planner import (
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    ArtifactTargetStatePlan,
    TargetStatePlanner,
    episode_scope_for_key,
    plan_artifact_target_state,
)
from lib.artifact_registration import (
    ArtifactRegistrationReceipt,
    artifact_key_for_resource,
    forget_current_resource_artifact,
    forget_unbound_grid_artifacts,
    forget_unbound_storyboard_artifacts,
    register_artifact_entries_atomically,
    register_current_artifact,
    register_current_artifact_if_provable,
    register_current_resource_artifact,
    register_task_current_resource_artifact,
    resolve_current_resource_artifact_basis,
)
from lib.content_digest import TextModeDigest
from lib.formal_write import project_metadata_lock
from lib.json_io import atomic_write_bytes, atomic_write_json
from lib.project_schema import project_schema_is_current
from lib.visual_artifact_provenance import (
    visual_file_digest,
)

_EPISODE_RESOURCE_KINDS = frozenset(
    {
        ArtifactKind.EPISODE_STORYBOARD,
        ArtifactKind.EPISODE_VIDEO,
        ArtifactKind.EPISODE_AUDIO,
        ArtifactKind.EPISODE_SUBTITLE,
        ArtifactKind.EPISODE_PRESENTATION,
    }
)


def activate_artifact_target_state(
    project_dir: Path,
    *,
    bump_schema: bool,
    backup_file: Callable[[Path, int], None] | None = None,
    commit_schema: Callable[[Path, Mapping[str, Any]], None] | None = None,
    plan: ArtifactTargetStatePlan | None = None,
) -> bool:
    """Commit one complete target state, optionally advancing schema last.

    ``backup_file`` 与 ``commit_schema`` 是临界区内两个落盘步骤的注入点，缺省即生产实现。
    ``plan`` 供调用方把只读预检提到自身写盘段之前完成；缺省即在此就地预检。传入的计划仍要
    过 ``_assert_preflight_unchanged``，它对着盘上事实自证，晚于预检的改动一律拒绝。
    """

    if plan is None:
        plan = plan_artifact_target_state(project_dir)
    current_schema = plan.project.get("schema_version")
    if bump_schema and current_schema != ARTIFACT_MANIFEST_SCHEMA_VERSION - 1:
        raise ValueError(f"schema bump requires a v{ARTIFACT_MANIFEST_SCHEMA_VERSION - 1} project")
    if not bump_schema and not project_schema_is_current(plan.project):
        raise ValueError("schema-preserving activation requires a current-schema project")

    _assert_preflight_unchanged(project_dir, plan)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    if bump_schema:
        with project_metadata_lock(project_dir):
            _assert_preflight_unchanged(project_dir, plan)
            _backup_activation_inputs(project_dir, plan, backup_file=backup_file)
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
            (commit_schema or _commit_schema_version)(project_dir, plan.project)
            return True
    return adapter.replace_entries_atomically(plan.entries)


def ensure_imported_artifact_target_state(
    project_dir: Path,
    *,
    preserved_manifest: ArtifactManifestArchiveSnapshot | None = None,
) -> bool:
    """Eagerly materialize the v8 sidecar at the archive staging boundary.

    Official exports carry the complete source Manifest in their visible archive
    envelope.  Its basis digests are immutable generation evidence and its content
    digests bind those claims to the exported formal bytes.  Validate both against
    the imported canonical target plan, then restore that whole snapshot in one
    commit.  Legacy archives without the envelope retain the self-proving path.
    """

    raw = (project_dir / "project.json").read_bytes()
    try:
        project = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("project.json is not valid UTF-8 JSON") from exc
    if not isinstance(project, Mapping) or not project_schema_is_current(project):
        raise ValueError("archive activation requires a current-schema project")
    if preserved_manifest is not None:
        preserved_entries = dict(preserved_manifest.entries)
        preserved_content_digests = dict(preserved_manifest.content_digests)
        if set(preserved_entries) != set(preserved_content_digests):
            raise ValueError("archive Artifact Manifest content evidence does not cover every formal claim")
        with project_metadata_lock(project_dir):
            plan = _plan_preserved_artifact_target_state(project_dir)
            rebased = _rebase_preserved_artifact_entries(plan, preserved_entries)
            invalid = [key.encode() for key, archived in preserved_entries.items() if rebased[key] != archived]
            if invalid:
                raise ValueError(f"archive Artifact Manifest contains unprovable formal claims: {sorted(invalid)}")
            _assert_preflight_unchanged(project_dir, plan)
            adapter = ProjectArtifactManifestAdapter(project_dir)
            restored: dict[ArtifactKey, ArtifactManifestEntry] = {}
            replaced: list[str] = []
            for key, entry in preserved_entries.items():
                evidence = _archived_content_evidence(adapter, entry.artifact_path, preserved_content_digests[key])
                if evidence == "exact":
                    restored[key] = entry
                elif evidence == "text_mode":
                    # 文本模式摘要丢了字节，绑不住整份产物：这条 claim 按无信封归档的口径，
                    # 用当前投影自证补录；投影不出条目的目标不登记。
                    projected = plan.entries.get(key)
                    if projected is not None:
                        restored[key] = projected
                else:
                    replaced.append(key.encode())
            if replaced:
                raise ValueError(
                    f"archive Artifact Manifest formal artifact content does not match its claims: {sorted(replaced)}"
                )
            _assert_preflight_unchanged(project_dir, plan)
            return adapter.replace_entries_atomically(restored)
    return activate_artifact_target_state(project_dir, bump_schema=False)


def snapshot_preserved_artifact_manifest(
    project_dir: Path,
    preserved_entries: Mapping[ArtifactKey, ArtifactManifestEntry],
) -> ArtifactManifestArchiveSnapshot:
    """Rebase preserved claims and bind them to one stable formal-byte snapshot."""

    with project_metadata_lock(project_dir):
        plan = _plan_preserved_artifact_target_state(project_dir)
        rebased = _rebase_preserved_artifact_entries(plan, preserved_entries)
        _assert_preflight_unchanged(project_dir, plan)
        adapter = ProjectArtifactManifestAdapter(project_dir)
        content_digests = {
            key: read_artifact_content_digest(adapter, entry.artifact_path) for key, entry in rebased.items()
        }
        _assert_preflight_unchanged(project_dir, plan)
    return ArtifactManifestArchiveSnapshot(entries=rebased, content_digests=content_digests)


def _archived_content_evidence(
    adapter: ProjectArtifactManifestAdapter,
    artifact_path: str,
    archived_digest: str,
) -> Literal["exact", "text_mode", "mismatch"]:
    """Classify whether the archived content digest binds a claim to the imported bytes.

    Archives exported through a text-mode descriptor hashed CRLF folded to LF and stopped at
    the first ``0x1A``.  Both translations drop bytes, so such a digest cannot prove the whole
    artifact; it only shows the bytes came through that export path.  Both digests come from
    one streamed read so large formal media is never buffered.
    """

    text_mode = TextModeDigest()
    if read_artifact_content_digest(adapter, artifact_path, chunk_observer=text_mode.update) == archived_digest:
        return "exact"
    return "text_mode" if text_mode.hexdigest() == archived_digest else "mismatch"


def _plan_preserved_artifact_target_state(project_dir: Path) -> ArtifactTargetStatePlan:
    """Prove canonical paths while leaving preserved generation digests immutable."""

    return TargetStatePlanner(project_dir, allow_stale_formal_targets=True).plan()


def _rebase_preserved_artifact_entries(
    plan: ArtifactTargetStatePlan,
    preserved_entries: Mapping[ArtifactKey, ArtifactManifestEntry],
) -> dict[ArtifactKey, ArtifactManifestEntry]:
    rebased: dict[ArtifactKey, ArtifactManifestEntry] = {}
    invalid: list[str] = []
    for key, archived in preserved_entries.items():
        current = plan.entries.get(key)
        artifact_path = current.artifact_path if current is not None else plan.formal_paths.get(key)
        if artifact_path is None:
            invalid.append(key.encode())
            continue
        rebased[key] = ArtifactManifestEntry(
            artifact_path=artifact_path,
            basis_digest=archived.basis_digest,
        )
    if invalid:
        raise ValueError(f"archive Artifact Manifest contains unprovable formal claims: {sorted(invalid)}")
    return rebased


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
    if not requested:
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

    root_planner = TargetStatePlanner(project_dir)
    planners: dict[int | None, TargetStatePlanner] = {None: root_planner}
    dependency_bytes: dict[Path, bytes] = {}
    dependency_digests: dict[Path, str] = {}
    replacements: dict[ArtifactKey, None] = {}

    def _merge_dependency_snapshot[T](destination: dict[Path, T], source: Mapping[Path, T]) -> None:
        for path, value in source.items():
            previous = destination.setdefault(path, value)
            if previous != value:
                raise RuntimeError(f"artifact target dependency changed during reconciliation: {path}")

    for key, frozen_entry in claimed.items():
        scope = episode_scope_for_key(key)
        planner = planners.get(scope)
        if planner is None:
            planner = TargetStatePlanner(
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
            formal_paths={},
            project=root_planner.project,
            project_bytes=root_planner.project_bytes,
            dependency_bytes=dependency_bytes,
            dependency_digests=dependency_digests,
            script_paths=(),
        ),
    )


def prepare_episode_script_manifest_commit(
    project_dir: Path,
    *,
    episode: int,
    artifact_path: str,
    resource_ids: Sequence[str],
    removed_resource_ids: Sequence[str] = (),
    replaced_resource_ids: Sequence[str] = (),
    basis: ArtifactBasis | ArtifactBasisDescriptor | None = None,
    adapter: ArtifactManifestAdapter | None = None,
    cancellation_receipts: list[ArtifactEntryRekeyReceipt] | None = None,
) -> Callable[[], None] | None:
    """Preflight one script replacement and return its atomic claim commit.

    The script claim and every claim orphaned by removal or identity replacement
    share one Manifest compare-and-swap. Callers invoke the returned closure
    inside the same formal-write transaction that selects the script bytes.
    """

    if type(episode) is not int or episode < 1:
        raise ValueError("episode must be a positive integer")
    remaining_ids = frozenset(resource_ids)
    removed_ids = frozenset(removed_resource_ids)
    replaced_ids = frozenset(replaced_resource_ids)
    if any(not resource_id for resource_id in (*remaining_ids, *removed_ids, *replaced_ids)):
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
    orphaned_keys.extend(
        key
        for resource_id in sorted(replaced_ids)
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
        replacements: dict[ArtifactKey, ArtifactManifestEntry | None] = dict.fromkeys(orphaned_keys)
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
            cancellation_receipts=cancellation_receipts,
        )

    return commit


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


def _backup_activation_inputs(
    project_dir: Path,
    plan: ArtifactTargetStatePlan,
    *,
    backup_file: Callable[[Path, int], None] | None = None,
) -> None:
    candidates = [project_dir / "project.json", *plan.script_paths]
    manifest = project_dir / MANIFEST_FILENAME
    if manifest.exists():
        candidates.append(manifest)
    stamp = time.time_ns()
    for source in candidates:
        if backup_file is None:
            _ensure_activation_backup(source, stamp=stamp)
        else:
            backup_file(source, stamp)


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
    updated["schema_version"] = ARTIFACT_MANIFEST_SCHEMA_VERSION
    atomic_write_json(project_dir / "project.json", updated)


__all__ = [
    "ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "ArtifactComparer",
    "ArtifactCurrencyResolver",
    "ArtifactInputClaim",
    "ArtifactRegistrationReceipt",
    "ArtifactTargetStatePlan",
    "EpisodeScriptInput",
    "RegisteredArtifactResolver",
    "activate_artifact_target_state",
    "active_artifact_currency_resolver",
    "artifact_input_is_usable",
    "artifact_is_usable",
    "artifact_key_for_resource",
    "assert_artifact_input_claims_usable",
    "assert_current_artifact_input_claims_usable",
    "bind_artifact_input_claims_to_content_digests",
    "bind_artifact_input_claims_to_frozen_visuals",
    "ensure_imported_artifact_target_state",
    "forget_current_resource_artifact",
    "forget_unbound_grid_artifacts",
    "forget_unbound_storyboard_artifacts",
    "plan_artifact_target_state",
    "prepare_episode_script_manifest_commit",
    "reconcile_artifact_target_claims",
    "register_artifact_entries_atomically",
    "register_current_artifact",
    "register_current_artifact_if_provable",
    "register_current_resource_artifact",
    "register_task_current_resource_artifact",
    "resolve_artifact_episode",
    "resolve_current_artifact_basis",
    "resolve_current_artifact_target",
    "resolve_current_resource_artifact_basis",
    "resolve_usable_artifact_input_claim",
    "resolve_usable_episode_script_input",
    "resolve_usable_storyboard_video_inputs",
    "snapshot_preserved_artifact_manifest",
    "snapshot_usable_artifact_input_claim",
]
