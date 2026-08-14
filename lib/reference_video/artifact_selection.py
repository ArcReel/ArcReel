"""Manifest-aware selection and recheck evidence for reference-video images."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from lib.artifact_activation import (
    ArtifactCurrencyResolver,
    ArtifactInputClaim,
    active_artifact_currency_resolver,
    artifact_input_is_usable,
    resolve_usable_artifact_input_claim,
)
from lib.artifact_manifest import ArtifactKey
from lib.reference_video.request_projection import FilesystemReferenceAssets, ResolvedReferenceAsset


class CurrentReferenceAssets:
    """Select reference images through filesystem and active-Manifest ownership.

    Product originals are user-owned source media and remain filesystem-gated.
    Every generated sheet is a formal artifact: after schema activation its exact
    asset-sheet claim must be current or stale, and selected claims can be frozen
    for a fresh check at the provider boundary.
    """

    def __init__(self, project_path: Path, project: Mapping[str, object]) -> None:
        self._project_path = project_path.resolve()
        self._filesystem = FilesystemReferenceAssets(project_path)
        self._resolver: ArtifactCurrencyResolver | None = active_artifact_currency_resolver(project_path, project)

    def _claim_for(self, asset: ResolvedReferenceAsset) -> ArtifactInputClaim | None:
        if asset.kind == "original":
            return None
        try:
            artifact_path = asset.path.relative_to(self._project_path).as_posix()
        except ValueError:
            return None
        return ArtifactInputClaim(
            key=ArtifactKey.asset_sheet(asset.reference.type, asset.reference.name),
            artifact_path=artifact_path,
        )

    def is_available(self, asset: ResolvedReferenceAsset) -> bool:
        if not self._filesystem.is_available(asset):
            return False
        claim = self._claim_for(asset)
        if self._resolver is None or claim is None:
            return True
        return artifact_input_is_usable(
            resolver=self._resolver,
            key=claim.key,
            artifact_path=claim.artifact_path,
            claims=None,
        )

    def snapshot_selected_claims(self, assets: Sequence[ResolvedReferenceAsset]) -> tuple[ArtifactInputClaim, ...]:
        """Freeze only the formal sheets that survived provider projection."""

        claims: list[ArtifactInputClaim] = []
        for asset in assets:
            if not self._filesystem.is_available(asset):
                raise ValueError(f"selected reference image is no longer available: {asset.path}")
            claim = self._claim_for(asset)
            if claim is None:
                continue
            selected = resolve_usable_artifact_input_claim(
                resolver=self._resolver,
                key=claim.key,
                artifact_path=claim.artifact_path,
            )
            if selected is None:
                raise ValueError(f"formal artifact input is no longer registered: {claim.artifact_path}")
            claims.append(selected)
        return tuple(claims)


__all__ = ["CurrentReferenceAssets"]
