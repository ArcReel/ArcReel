r"""v13→v14: normalize legacy styles once and rebase artifact basis to include style descriptions.

Two legacy forms: initial “画风：” prefixes from old templates, which combine with English ``Style:``
into mixed “Style: 画风：…”; and ``Photographic`` / ``Anime`` / ``3D Animation`` short labels, earlier
enums previously resolved/expanded into template snapshots on every ``ProjectManager`` read.
Persist normalization; runtime only reads normalized values, without prefix stripping/label resolution.

Custom styles with uploaded references have empty ``style`` and only ``style_description`` as their
style signal. Grid composites/split images/reference video consume it; v14 basis records nonempty
descriptions. Earlier schemas retain description-free ``project_basis_style_description`` semantics,
preventing preceding activation from registering new bases or discarding artifacts as stale.

Both changes alter target digests. Plan v13/v14 states, rewriting only previously current entries with
changed targets. Upgrade preserves currency and never turns already-stale entries current. Playback/
restoration reads frozen version basis; selected records for these three changed kinds also gain descriptions.

Commit history, manifests, then ``project.json``. Crashes after manifest writes but before schema
advance rerun from untouched ``project.json``; rewritten entries no longer match old digests and skip.
Crashes after history but before manifest writes recognize new history unchanged and rewrite manifests
normally, avoiding duplicates/lost repairs. Reverse order would mistake repaired entries for unfamiliar
digests on retry, permanently retaining stale markers."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import assert_artifact_target_state_plan_unchanged
from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasis,
    ArtifactKey,
    ArtifactKind,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifacts.artifact_planner import ArtifactTargetStatePlan, TargetStatePlanner
from lib.artifacts.artifact_version_provenance import IMAGE_ARTIFACT_BASIS_FIELD, parse_image_version_basis
from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.infra.json_io import atomic_write_bytes, atomic_write_json
from lib.project.project_migration_report import ArtifactBackfillOutcome, MigrationSkippedArtifact
from lib.project.project_migrations.backups import ensure_versioned_backup
from lib.project.project_schema import parse_project_schema_version
from lib.prompts.prompt_style import normalize_style_value
from lib.prompts.style_templates import resolve_template_prompt

TARGET_SCHEMA_VERSION = 14

_GRID_MEMBER_KIND = "artifact-visual/grid-member"
_REFERENCE_VIDEO_VISUAL_KIND = "artifact-visual/video-reference"

#: Legacy enums → current template IDs, consumed only here once to expand snapshots; values then disappear.
LEGACY_STYLE_MAP: dict[str, str] = {
    "Photographic": "live_premium_drama",
    "Anime": "anim_kyoto",
    "3D Animation": "anim_3d_cg",
}

#: Initial “画风：” prefixes with full/half-width colons. ``anim_arcane`` “油画三渲二画风：” does not start
#: with “画风”, so it never matches: the word is part of a compound, not a removable prefix.
_STYLE_PREFIX_RE = re.compile(r"^画风[：:]\s*")


def migrate_project_dict(project: dict[str, Any]) -> dict[str, Any]:
    r"""Pure, idempotent v13→v14 project-dict conversion.

    Preserve nonstring ``style`` for DataValidator reporting. Resolve labels only without
    ``style_template_id``: existing values indicate later authoritative user choices. Keep reference-first
    behavior: ``style_image`` clears style and nulls template IDs. Leave ``schema_version`` to file-level commit."""
    data = copy.deepcopy(project)
    raw = data.get("style")
    if not isinstance(raw, str):
        return data
    if "style_template_id" not in data and raw in LEGACY_STYLE_MAP:
        if data.get("style_image"):
            data["style_template_id"] = None
            data["style"] = ""
        else:
            template_id = LEGACY_STYLE_MAP[raw]
            data["style_template_id"] = template_id
            data["style"] = resolve_template_prompt(template_id)
        return data
    stripped = raw.strip()
    if _STYLE_PREFIX_RE.match(stripped):
        data["style"] = _STYLE_PREFIX_RE.sub("", stripped)
    return data


@dataclass(frozen=True, slots=True)
class _Planned:
    plan: ArtifactTargetStatePlan
    bases: Mapping[ArtifactKey, ArtifactBasis]


def _plan(project_dir: Path, project: Mapping[str, Any], *, allow_stale: bool) -> _Planned:
    r"""Plan complete target states from supplied content without prior persistence.

    ``allow_stale`` permits stale formal targets with nonempty descriptions: frozen reference-video basis
    lacks descriptions, so v14 planning otherwise skips them as changed-since-generation, preventing rebase.
    Registration equality with before-targets alone decides currency; permitting stale targets cannot admit already-stale entries."""
    project_bytes = json.dumps(project, ensure_ascii=False).encode("utf-8")
    planner = TargetStatePlanner(project_dir, project_bytes=project_bytes, allow_stale_formal_targets=allow_stale)
    plan = planner.plan()
    return _Planned(plan=plan, bases=dict(planner.bases))


def _description_bound(key: ArtifactKey, bases: Mapping[ArtifactKey, ArtifactBasis]) -> bool:
    r"""Three description-bound basis kinds: grid composites, split images, video (storyboard-video basis lacks style and stays unchanged).

    Split/single storyboard images share ``episode_storyboard`` keys; only basis kinds distinguish them."""
    if key.kind in {ArtifactKind.EPISODE_GRID, ArtifactKind.EPISODE_VIDEO}:
        return True
    basis = bases.get(key)
    return key.kind is ArtifactKind.EPISODE_STORYBOARD and basis is not None and basis.kind == _GRID_MEMBER_KIND


def _rebase_entries(
    stored: Mapping[ArtifactKey, ArtifactManifestEntry],
    before: Mapping[ArtifactKey, ArtifactManifestEntry],
    after: Mapping[ArtifactKey, ArtifactManifestEntry],
) -> dict[ArtifactKey, ArtifactManifestEntry]:
    r"""Select previously current entries with changed targets, returning new registrations.

    Artifact paths must also match; different paths mean different artifacts."""
    rebased: dict[ArtifactKey, ArtifactManifestEntry] = {}
    for key, current in stored.items():
        target_after = after.get(key)
        if target_after is None or current == target_after or target_after.artifact_path != current.artifact_path:
            continue
        if current == before.get(key):
            rebased[key] = target_after
    return rebased


def _selected_version_record(
    versions: Mapping[str, Any], resource_type: str, resource_id: str
) -> dict[str, Any] | None:
    bucket = versions.get(resource_type)
    resource = bucket.get(resource_id) if isinstance(bucket, Mapping) else None
    if not isinstance(resource, Mapping):
        return None
    records = resource.get("versions")
    if not isinstance(records, list):
        return None
    selected = [
        record
        for record in records
        if isinstance(record, dict) and record.get("version") == resource.get("current_version")
    ]
    return selected[0] if len(selected) == 1 else None


def _migrated_video_facts(
    facts: VideoArtifactCurrencyFacts, migrated: Mapping[str, Any], description: str
) -> VideoArtifactCurrencyFacts | None:
    r"""Replace frozen reference-video style inputs with migrated style/description and rebuild basis; None for nonreference video."""
    inputs = facts.visual_basis.to_evidence_dict()["inputs"]
    if facts.visual_basis.kind != _REFERENCE_VIDEO_VISUAL_KIND or not isinstance(inputs, Mapping):
        return None
    style = migrated.get("style")
    visual = ArtifactBasis.build(
        facts.visual_basis.kind,
        kind_version=facts.visual_basis.kind_version,
        inputs={**inputs, "style": style if isinstance(style, str) else "", "style_description": description},
    )
    video = compose_video_artifact_basis(visual=visual, speech=facts.speech_basis, duration=facts.duration_basis)
    return replace(facts, visual_basis=visual, video_basis=video)


@dataclass(frozen=True, slots=True)
class _VersionRewrite:
    changed: bool
    #: Selected history mismatches preventing joint rebase: leave registrations for read-time stale judgments.
    withdrawn: tuple[ArtifactKey, ...]


def _rewrite_selected_versions(
    versions: dict[str, Any],
    stored: Mapping[ArtifactKey, ArtifactManifestEntry],
    rebased: Mapping[ArtifactKey, ArtifactManifestEntry],
    after: _Planned,
    migrated: Mapping[str, Any],
    description: str,
) -> _VersionRewrite:
    r"""Rebase selected history records for the three changed artifact kinds in place.

    Playback/restoration judge frozen basis. Manifest-only updates would leave current video labeled older
    and restoration register old basis. Rewrite records equal to old registrations; retain already-new
    records from interrupted retries. If neither matches, or migrated inputs still mismatch new targets,
    withdraw that registration rebase to prevent current-manifest/stale-player disagreement. Artifacts
    without selected records update manifests only."""
    changed = False
    withdrawn: list[ArtifactKey] = []
    for key, target in rebased.items():
        if not _description_bound(key, after.bases):
            continue
        old_digest = stored[key].basis_digest
        resource_id = str(key.components[-1])
        if key.kind is ArtifactKind.EPISODE_VIDEO:
            record = _selected_version_record(versions, "reference_videos", resource_id)
            if record is None:
                continue
            try:
                frozen_facts = VideoArtifactCurrencyFacts.from_dict(record.get("artifact_video_currency"))
            except (TypeError, ValueError):
                withdrawn.append(key)
                continue
            if frozen_facts.video_basis.digest == target.basis_digest:
                continue
            facts = _migrated_video_facts(frozen_facts, migrated, description)
            if (
                frozen_facts.video_basis.digest != old_digest
                or facts is None
                or facts.video_basis.digest != target.basis_digest
            ):
                withdrawn.append(key)
                continue
            record["artifact_video_currency"] = facts.to_dict()
            changed = True
            continue
        resource_type = "grids" if key.kind is ArtifactKind.EPISODE_GRID else "storyboards"
        record = _selected_version_record(versions, resource_type, resource_id)
        if record is None:
            continue
        try:
            frozen = parse_image_version_basis(resource_type, resource_id, record)
        except (TypeError, ValueError):
            withdrawn.append(key)
            continue
        if frozen.digest == target.basis_digest:
            continue
        basis = after.bases.get(key)
        if frozen.digest != old_digest or basis is None or basis.digest != target.basis_digest:
            withdrawn.append(key)
            continue
        record[IMAGE_ARTIFACT_BASIS_FIELD] = basis.to_evidence_dict()
        changed = True
    return _VersionRewrite(changed=changed, withdrawn=tuple(withdrawn))


def _withdrawn_skip(key: ArtifactKey, entry: ArtifactManifestEntry) -> MigrationSkippedArtifact:
    episode = key.components[0]
    return MigrationSkippedArtifact(
        kind=key.kind.value,
        episode=episode if type(episode) is int else None,
        resource_id=str(key.components[-1]),
        artifact_path=entry.artifact_path,
        reason="selected version record does not match the registered basis; style description was not backfilled",
    )


def _with_versions_bytes(plan: ArtifactTargetStatePlan, versions_path: Path, raw: bytes) -> ArtifactTargetStatePlan:
    r"""History is a planning dependency; rewritten bytes become the stability-gate reference after this step changes it."""
    resolved = versions_path.resolve()
    return replace(
        plan,
        dependency_bytes={
            path: (raw if path.resolve() == resolved else content) for path, content in plan.dependency_bytes.items()
        },
    )


def _read_bytes_or_none(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def migrate_v13_to_v14(project_dir: Path) -> ArtifactBackfillOutcome | None:
    r"""File-level v13→v14 migration."""
    project_dir = Path(project_dir)
    pj = project_dir / "project.json"
    if not pj.exists():
        return None
    project_bytes = pj.read_bytes()
    data = json.loads(project_bytes)
    if not isinstance(data, dict):
        raise ValueError("project.json 必须是对象")
    if parse_project_schema_version(data) >= TARGET_SCHEMA_VERSION:
        return None
    migrated = migrate_project_dict(data)
    migrated["schema_version"] = TARGET_SCHEMA_VERSION
    description = normalize_style_value(data.get("style_description"))
    if migrated.get("style") == data.get("style") and not description:
        # Already-normal styles with empty descriptions have byte-identical bases; upgrades must not incur complete planning costs for them.
        atomic_write_json(pj, migrated)
        return None

    with project_metadata_lock(project_dir):
        allow_stale = bool(description)
        before = _plan(project_dir, data, allow_stale=allow_stale)
        after = _plan(project_dir, migrated, allow_stale=allow_stale)
        plans = [before.plan, after.plan]
        for plan in plans:
            assert_artifact_target_state_plan_unchanged(project_dir, plan, expected_project_bytes=project_bytes)
        adapter = ProjectArtifactManifestAdapter(project_dir)
        stored = adapter.snapshot_entries()
        rebased = _rebase_entries(stored, before.plan.entries, after.plan.entries)
        versions_path = project_dir / "versions" / "versions.json"
        versions_bytes = _read_bytes_or_none(versions_path)
        versions: dict[str, Any] = {}
        rewrite = _VersionRewrite(changed=False, withdrawn=())
        if rebased and description and versions_bytes is not None:
            loaded = json.loads(versions_bytes)
            if isinstance(loaded, dict):
                versions = loaded
                rewrite = _rewrite_selected_versions(versions, stored, rebased, after, migrated, description)
        for key in rewrite.withdrawn:
            del rebased[key]
        if rebased:
            _commit_rebase(
                project_dir,
                adapter,
                expected={key: stored[key] for key in rebased},
                rebased=rebased,
                plans=plans,
                project_bytes=project_bytes,
                versions=versions if rewrite.changed else None,
                versions_bytes=versions_bytes,
            )
        skipped = [*after.plan.skipped, *(_withdrawn_skip(key, stored[key]) for key in rewrite.withdrawn)]
        outcome = (
            ArtifactBackfillOutcome.from_entries(adapter.snapshot_entries(), skipped)
            if rebased or skipped or migrated.get("style") != data.get("style")
            else None
        )
        atomic_write_json(pj, migrated)
    return outcome


def _commit_rebase(
    project_dir: Path,
    adapter: ProjectArtifactManifestAdapter,
    *,
    expected: Mapping[ArtifactKey, ArtifactManifestEntry],
    rebased: Mapping[ArtifactKey, ArtifactManifestEntry],
    plans: list[ArtifactTargetStatePlan],
    project_bytes: bytes,
    versions: dict[str, Any] | None,
    versions_bytes: bytes | None,
) -> None:
    r"""Commit history before manifests, rolling back to premigration state on failure.

    Incomplete manifest rollback retains rewritten history too, keeping both on new semantics. Retries
    recognize new manifest targets, avoiding current-manifest/stale-player disagreement."""
    ensure_versioned_backup(project_dir / MANIFEST_FILENAME, TARGET_SCHEMA_VERSION - 1)
    versions_path = project_dir / "versions" / "versions.json"
    if versions is not None:
        ensure_versioned_backup(versions_path, TARGET_SCHEMA_VERSION - 1)
        atomic_write_json(versions_path, versions)
        new_versions_bytes = versions_path.read_bytes()
        plans = [_with_versions_bytes(plan, versions_path, new_versions_bytes) for plan in plans]
    try:
        if not adapter.replace_entries_if_matches_atomically(expected=expected, replacements=rebased):
            raise RuntimeError("artifact manifest changed while rebasing legacy style bases")
    except BaseException:
        if versions is not None and versions_bytes is not None:
            atomic_write_bytes(versions_path, versions_bytes)
        raise
    try:
        for plan in plans:
            assert_artifact_target_state_plan_unchanged(project_dir, plan, expected_project_bytes=project_bytes)
    except BaseException as original_error:
        restored = adapter.replace_entries_if_matches_atomically(expected=rebased, replacements=expected)
        if not restored and any(adapter.get_entry(key) != entry for key, entry in expected.items()):
            raise RuntimeError("artifact manifest dependency drifted and rollback was incomplete") from original_error
        if versions is not None and versions_bytes is not None:
            atomic_write_bytes(versions_path, versions_bytes)
        raise


__all__ = ["LEGACY_STYLE_MAP", "TARGET_SCHEMA_VERSION", "migrate_project_dict", "migrate_v13_to_v14"]
