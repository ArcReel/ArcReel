"""正式输入认领。

生成任务把选中的产物冻结成 claim：解析时留下复检证据，提交前再验证内容未变，
使「用哪份输入产出了这份产物」可被证明。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifact_currency import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    artifact_is_usable,
    decode_script_content_snapshot,
    read_artifact_content_snapshot,
    resolve_artifact_episode,
)
from lib.artifact_manifest import (
    ArtifactKey,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.artifact_planner import normalize_script_binding
from lib.formal_write import project_metadata_lock
from lib.storyboard_sequence import StoryboardImageUnavailable, resolve_storyboard_video_inputs
from lib.visual_artifact_provenance import (
    VisualReference,
)


@dataclass(frozen=True, slots=True)
class ArtifactInputClaim:
    """One Manifest-backed formal artifact selected as a provider input."""

    key: ArtifactKey
    artifact_path: str
    basis_digest: str | None = None
    content_digest: str | None = None


def _assert_input_claim_content_unchanged(
    claim: ArtifactInputClaim,
    current_digest: str,
) -> None:
    if claim.content_digest is None:
        return
    if current_digest != claim.content_digest:
        raise ValueError(f"formal artifact input changed since it was selected: {claim.artifact_path}")


@dataclass(frozen=True, slots=True)
class EpisodeScriptInput:
    """A bound formal script and the identity frozen for provider admission."""

    episode: int
    claim: ArtifactInputClaim


def resolve_usable_episode_script_input(
    *,
    project_path: Path,
    project: Mapping[str, object],
    script: dict[str, Any],
    script_filename: str,
) -> EpisodeScriptInput:
    """Resolve one bound episode script through the shared formal-input seam.

    The exact bound script claim is required: an identity that is not registered
    in the Manifest is refused rather than admitted on the caller's word.
    """

    from lib.project_manager import ProjectManager

    episode = resolve_artifact_episode(
        project=project,
        script=script,
        script_filename=script_filename,
    )
    artifact_path = normalize_script_binding(ProjectManager.normalize_script_filename(script_filename))
    content_bytes, content_digest = read_artifact_content_snapshot(
        ProjectArtifactManifestAdapter(project_path),
        artifact_path,
    )
    if decode_script_content_snapshot(content_bytes, artifact_path) != script:
        raise ValueError(f"formal artifact input changed while it was selected: {artifact_path}")
    claim = snapshot_usable_artifact_input_claim(
        resolver=active_artifact_currency_resolver(project_path, project),
        key=ArtifactKey.episode_script(episode),
        artifact_path=artifact_path,
        content_digest=content_digest,
    )
    if claim is None:
        raise ValueError(f"episode script is not registered: {artifact_path}")
    return EpisodeScriptInput(episode=episode, claim=claim)


def artifact_input_is_usable(
    *,
    resolver: ArtifactCurrencyResolver,
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
    resolver: ArtifactCurrencyResolver,
    key: ArtifactKey,
    artifact_path: str,
    content_digest: str | None = None,
) -> ArtifactInputClaim | None:
    """Return recheck evidence for one usable formal input.

    Retaining the logical key and the exact registered path lets the
    provider-boundary check recheck the identical claim that was selected.
    """

    entry = resolver.resolve_usable_entry(key, artifact_path=artifact_path)
    if entry is None:
        return None
    observed_digest = resolver.artifact_content_digest(entry.artifact_path)
    if content_digest is not None and observed_digest != content_digest:
        raise ValueError(f"formal artifact input changed while it was selected: {entry.artifact_path}")
    comparison = resolver.compare_frozen_entry(key, entry)
    if comparison.status is not ArtifactStatus.CURRENT:
        raise ValueError(f"formal artifact input changed while it was selected: {entry.artifact_path}")
    return ArtifactInputClaim(
        key=key,
        artifact_path=entry.artifact_path,
        basis_digest=entry.basis_digest,
        content_digest=observed_digest,
    )


def snapshot_usable_artifact_input_claim(
    *,
    resolver: ArtifactCurrencyResolver,
    key: ArtifactKey,
    artifact_path: str,
    content_digest: str | None = None,
) -> ArtifactInputClaim | None:
    """Select one formal input and freeze its byte identity."""

    if content_digest is None:
        content_digest = resolver.artifact_content_digest(artifact_path)
    return resolve_usable_artifact_input_claim(
        resolver=resolver,
        key=key,
        artifact_path=artifact_path,
        content_digest=content_digest,
    )


def bind_artifact_input_claims_to_frozen_visuals(
    *,
    project_path: Path,
    resolver: ArtifactCurrencyResolver,
    claims: Sequence[ArtifactInputClaim],
    source_references: Sequence[VisualReference],
    frozen_references: Sequence[VisualReference],
) -> tuple[ArtifactInputClaim, ...]:
    """Bind matching formal claims to the exact visual bytes sent to a provider."""

    if len(source_references) != len(frozen_references):
        raise ValueError("source and frozen visual references must remain aligned")
    content_digests: dict[str, str] = {}
    for source, frozen in zip(source_references, frozen_references, strict=True):
        if frozen.content_digest is None:
            raise ValueError("frozen visual reference has no content digest")
        try:
            artifact_path = source.path.relative_to(project_path).as_posix()
        except ValueError:
            continue
        existing = content_digests.get(artifact_path)
        if existing is not None and existing != frozen.content_digest:
            raise ValueError(f"formal visual input was frozen with conflicting bytes: {artifact_path}")
        content_digests[artifact_path] = frozen.content_digest

    return bind_artifact_input_claims_to_content_digests(
        resolver=resolver,
        claims=claims,
        content_digests=content_digests,
    )


def bind_artifact_input_claims_to_content_digests(
    *,
    resolver: ArtifactCurrencyResolver,
    claims: Sequence[ArtifactInputClaim],
    content_digests: Mapping[str, str],
) -> tuple[ArtifactInputClaim, ...]:
    """Bind matching formal claims to exact task-owned input bytes."""

    bound: list[ArtifactInputClaim] = []
    for claim in claims:
        content_digest = content_digests.get(claim.artifact_path)
        if content_digest is None:
            bound.append(claim)
            continue
        selected = resolve_usable_artifact_input_claim(
            resolver=resolver,
            key=claim.key,
            artifact_path=claim.artifact_path,
            content_digest=content_digest,
        )
        if selected is None:
            raise ValueError(f"formal artifact input is no longer registered: {claim.artifact_path}")
        bound.append(selected)
    return tuple(bound)


def assert_artifact_input_claims_usable(
    project_path: Path,
    project: Mapping[str, Any],
    claims: Sequence[ArtifactInputClaim],
) -> None:
    """Recheck selected formal inputs immediately before provider submission."""

    if not claims:
        return
    resolver = active_artifact_currency_resolver(project_path, project)
    for claim in claims:
        if claim.basis_digest is None:
            if not artifact_is_usable(resolver, claim.key, claim.artifact_path):
                raise ValueError(f"formal artifact input is no longer registered: {claim.artifact_path}")
        else:
            comparison = resolver.compare_frozen_entry(
                claim.key,
                ArtifactManifestEntry(
                    artifact_path=claim.artifact_path,
                    basis_digest=claim.basis_digest,
                ),
            )
            if comparison.status is ArtifactStatus.BLOCKED:
                assert comparison.blocker is not None
                raise ArtifactManifestError(comparison.blocker.detail)
            if comparison.status is ArtifactStatus.STALE:
                raise ValueError(f"formal artifact input changed since it was selected: {claim.artifact_path}")
            if comparison.status is not ArtifactStatus.CURRENT:
                raise ValueError(f"formal artifact input is no longer registered: {claim.artifact_path}")
        if claim.content_digest is not None:
            _assert_input_claim_content_unchanged(
                claim,
                resolver.artifact_content_digest(claim.artifact_path),
            )


def assert_current_artifact_input_claims_usable(
    project_path: Path,
    claims: Sequence[ArtifactInputClaim],
) -> None:
    """Recheck frozen input identities against one committed project snapshot.

    The project lock serializes this read with formal metadata writes, so every
    frozen formal identity must still hold a current or stale Manifest claim
    before a paid provider can be called.
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
        assert_artifact_input_claims_usable(project_path, project, claims)


def resolve_usable_storyboard_video_inputs(
    *,
    project_path: Path,
    project: Mapping[str, object],
    episode: int,
    resource_id: str,
    item: dict[str, object],
    resolver: ArtifactCurrencyResolver | None = None,
    claims: list[ArtifactInputClaim] | None = None,
) -> tuple[Path, Path | None]:
    """Resolve video inputs and retain Manifest recheck evidence."""

    if type(episode) is not int or episode < 1:
        raise ValueError("script episode must be a positive integer")
    storyboard_file, end_frame = resolve_storyboard_video_inputs(
        project_path=project_path,
        resource_id=resource_id,
        item=item,
    )
    if resolver is None:
        resolver = active_artifact_currency_resolver(project_path, project)
    storyboard_rel = storyboard_file.relative_to(project_path).as_posix()
    if not artifact_input_is_usable(
        resolver=resolver,
        key=ArtifactKey.episode_storyboard(episode, resource_id),
        artifact_path=storyboard_rel,
        claims=claims,
    ):
        raise StoryboardImageUnavailable(f"storyboard is not registered: {storyboard_rel}")
    return storyboard_file, end_frame
