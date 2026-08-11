from __future__ import annotations

import unicodedata

import pytest

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactKey,
    ArtifactKind,
    ArtifactManifest,
    ArtifactStatus,
    InMemoryArtifactManifestAdapter,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "key",
    [
        ArtifactKey.asset_sheet("character", "阿黎:/%"),
        ArtifactKey.asset_sheet("scene", "屋顶"),
        ArtifactKey.asset_sheet("prop", "钥匙"),
        ArtifactKey.asset_sheet("product", "咖啡豆"),
        ArtifactKey.episode_step1(12),
        ArtifactKey.episode_script(12),
        ArtifactKey.episode_grid(12, "group:/一"),
        ArtifactKey.episode_storyboard(12, "E12S03:/"),
        ArtifactKey.episode_video(12, "unit:/3"),
        ArtifactKey.episode_audio(12, "segment:/3"),
    ],
)
def test_artifact_key_round_trips_without_display_string_parsing(key: ArtifactKey) -> None:
    encoded = key.encode()

    assert encoded.startswith("artifact-key-v1:")
    assert ArtifactKey.decode(encoded) == key


def test_artifact_key_rejects_direct_construction_that_cannot_round_trip() -> None:
    with pytest.raises(ValueError, match="components"):
        ArtifactKey(ArtifactKind.EPISODE_SCRIPT, ("localized episode label",))


def test_asset_sheet_key_uses_the_asset_name_equality_coordinate() -> None:
    nfc_name = unicodedata.normalize("NFC", "Hiếu")
    nfd_name = unicodedata.normalize("NFD", nfc_name)

    canonical = ArtifactKey.asset_sheet("character", nfc_name)
    from_nfd_factory = ArtifactKey.asset_sheet("character", nfd_name)
    from_nfd_constructor = ArtifactKey(ArtifactKind.ASSET_SHEET, ("character", nfd_name))

    assert nfc_name != nfd_name
    assert from_nfd_factory == canonical
    assert from_nfd_constructor == canonical
    assert ArtifactKey.decode(canonical.encode()) == canonical


def test_manifest_compares_registered_basis_without_mutating_the_artifact() -> None:
    path = "scripts/episode_1.json"
    adapter = InMemoryArtifactManifestAdapter(artifacts={path})
    manifest = ArtifactManifest(adapter)
    key = ArtifactKey.episode_script(1)
    original_basis = ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "original"})
    changed_basis = ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "changed"})

    assert manifest.compare(key, artifact_path=path, basis=original_basis).status is ArtifactStatus.MISSING
    assert manifest.register(key, artifact_path=path, basis=original_basis)

    current = manifest.compare(key, artifact_path=path, basis=original_basis)
    stale = manifest.compare(key, artifact_path=path, basis=changed_basis)
    adapter.remove_artifact(path)
    missing = manifest.compare(key, artifact_path=path, basis=original_basis)
    adapter.block_artifact(path, code="artifact_symlink", detail="artifact path is a symlink")
    blocked = manifest.compare(key, artifact_path=path, basis=original_basis)

    assert current.status is ArtifactStatus.CURRENT
    assert current.usable
    assert stale.status is ArtifactStatus.STALE
    assert stale.usable
    assert missing.status is ArtifactStatus.MISSING
    assert not missing.usable
    assert blocked.status is ArtifactStatus.BLOCKED
    assert blocked.blocker is not None
    assert blocked.blocker.code == "artifact_symlink"
    assert not blocked.usable


def test_manifest_blocks_windows_drive_like_artifact_path() -> None:
    manifest = ArtifactManifest(InMemoryArtifactManifestAdapter())
    comparison = manifest.compare(
        ArtifactKey.episode_script(1),
        artifact_path="C:/outside.json",
        basis=ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "source"}),
    )

    assert comparison.status is ArtifactStatus.BLOCKED
    assert comparison.blocker is not None and comparison.blocker.code == "artifact_path_invalid"


@pytest.mark.parametrize("artifact_path", [".. /outside.json", ". /episode.json", "scripts /episode.json"])
def test_manifest_blocks_windows_normalized_artifact_path_components(artifact_path: str) -> None:
    manifest = ArtifactManifest(InMemoryArtifactManifestAdapter())

    comparison = manifest.compare(
        ArtifactKey.episode_script(1),
        artifact_path=artifact_path,
        basis=ArtifactBasis.build("test/script", kind_version=1, inputs={}),
    )

    assert comparison.status is ArtifactStatus.BLOCKED
    assert comparison.blocker is not None and comparison.blocker.code == "artifact_path_invalid"


@pytest.mark.parametrize(
    "artifact_path",
    [".ARCREEL_ARTIFACTS.JSON", ".arcreel_artifacts.json.", ".artifact_manifest.lock "],
)
def test_manifest_blocks_windows_aliases_of_runtime_paths(artifact_path: str) -> None:
    manifest = ArtifactManifest(InMemoryArtifactManifestAdapter())

    comparison = manifest.compare(
        ArtifactKey.episode_script(1),
        artifact_path=artifact_path,
        basis=ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "source"}),
    )

    assert comparison.status is ArtifactStatus.BLOCKED
    assert comparison.blocker is not None and comparison.blocker.code == "artifact_path_invalid"
