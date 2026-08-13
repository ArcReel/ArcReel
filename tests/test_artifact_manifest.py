from __future__ import annotations

import unicodedata

import pytest

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactKind,
    ArtifactManifest,
    ArtifactManifestEntry,
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
        ArtifactKey.episode_subtitle(12, "segment:/3", "use_tts"),
        ArtifactKey.episode_presentation(12, "segment:/3", "post_production"),
    ],
)
def test_artifact_key_round_trips_without_display_string_parsing(key: ArtifactKey) -> None:
    encoded = key.encode()

    assert encoded.startswith("artifact-key-v1:")
    assert ArtifactKey.decode(encoded) == key


@pytest.mark.parametrize("variant", ["", "automatic", "USE_TTS", 1])
def test_rendition_artifact_keys_reject_unknown_variants(variant: object) -> None:
    with pytest.raises(ValueError, match="variant"):
        ArtifactKey.episode_presentation(1, "E1U01", variant)  # type: ignore[arg-type]


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


def test_manifest_compares_a_resolved_target_entry_without_reconstructing_basis() -> None:
    path = "videos/scene_E1S01.mp4"
    adapter = InMemoryArtifactManifestAdapter(artifacts={path})
    manifest = ArtifactManifest(adapter)
    key = ArtifactKey.episode_video(1, "E1S01")
    recorded = ArtifactBasis.build("test/video", kind_version=1, inputs={"prompt": "old"})
    manifest.register(key, artifact_path=path, basis=recorded)

    current = manifest.compare_entry(
        key,
        artifact_path=path,
        expected=ArtifactManifestEntry(artifact_path=path, basis_digest=recorded.digest),
    )
    unprovable = manifest.compare_entry(key, artifact_path=path, expected=None)

    assert current.status is ArtifactStatus.CURRENT
    assert unprovable.status is ArtifactStatus.STALE
    assert unprovable.usable


def test_manifest_registers_a_strict_frozen_basis_descriptor_after_artifact_exists() -> None:
    path = "videos/scene_E1S01.mp4"
    adapter = InMemoryArtifactManifestAdapter(artifacts={path})
    manifest = ArtifactManifest(adapter)
    key = ArtifactKey.episode_video(1, "E1S01")
    basis = ArtifactBasis.build("test/video", kind_version=1, inputs={"source": "frozen"})

    assert manifest.register_descriptor(
        key,
        artifact_path=path,
        basis=ArtifactBasisDescriptor.from_basis(basis),
    )
    assert manifest.compare(key, artifact_path=path, basis=basis).status is ArtifactStatus.CURRENT


def test_transactional_descriptor_registration_restores_previous_entry_after_partial_write(monkeypatch) -> None:
    path = "videos/scene_E1S01.mp4"
    adapter = InMemoryArtifactManifestAdapter(artifacts={path})
    manifest = ArtifactManifest(adapter)
    key = ArtifactKey.episode_video(1, "E1S01")
    old = ArtifactBasis.build("test/video", kind_version=1, inputs={"source": "old"})
    new = ArtifactBasis.build("test/video", kind_version=1, inputs={"source": "new"})
    manifest.register(key, artifact_path=path, basis=old)
    original_put = adapter.put_entry
    calls = 0

    def _write_then_fail(write_key, entry):
        nonlocal calls
        calls += 1
        changed = original_put(write_key, entry)
        if calls == 1:
            raise RuntimeError("manifest write failed")
        return changed

    monkeypatch.setattr(adapter, "put_entry", _write_then_fail)

    with pytest.raises(RuntimeError, match="manifest write failed"):
        manifest.register_descriptor_transactionally(
            key,
            artifact_path=path,
            basis=ArtifactBasisDescriptor.from_basis(new),
        )

    assert manifest.compare(key, artifact_path=path, basis=old).status is ArtifactStatus.CURRENT


def test_transactional_descriptor_registration_preserves_original_and_rollback_failures(monkeypatch) -> None:
    path = "videos/scene_E1S01.mp4"
    adapter = InMemoryArtifactManifestAdapter(artifacts={path})
    manifest = ArtifactManifest(adapter)
    key = ArtifactKey.episode_video(1, "E1S01")
    old = ArtifactBasis.build("test/video", kind_version=1, inputs={"source": "old"})
    new = ArtifactBasis.build("test/video", kind_version=1, inputs={"source": "new"})
    manifest.register(key, artifact_path=path, basis=old)
    original_put = adapter.put_entry
    original_error = RuntimeError("manifest write failed")
    rollback_error = OSError("manifest rollback failed")
    calls = 0

    def _fail_write_and_rollback(write_key, entry):
        nonlocal calls
        calls += 1
        if calls == 1:
            original_put(write_key, entry)
            raise original_error
        raise rollback_error

    monkeypatch.setattr(adapter, "put_entry", _fail_write_and_rollback)

    with pytest.raises(RuntimeError, match="rollback was incomplete") as exc_info:
        manifest.register_descriptor_transactionally(
            key,
            artifact_path=path,
            basis=ArtifactBasisDescriptor.from_basis(new),
        )

    assert exc_info.value.__cause__ is rollback_error
    assert rollback_error.__cause__ is original_error


@pytest.mark.parametrize("kind_version", ["1", True, 1.0])
def test_artifact_basis_evidence_rejects_non_integer_kind_version(kind_version: object) -> None:
    evidence = ArtifactBasis.build("test/video", kind_version=1, inputs={}).to_evidence_dict()
    evidence["kind_version"] = kind_version

    with pytest.raises(ValueError, match="kind_version"):
        ArtifactBasis.from_evidence_dict(evidence)


def test_manifest_blocks_windows_drive_like_artifact_path() -> None:
    manifest = ArtifactManifest(InMemoryArtifactManifestAdapter())
    comparison = manifest.compare(
        ArtifactKey.episode_script(1),
        artifact_path="C:/outside.json",
        basis=ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "source"}),
    )

    assert comparison.status is ArtifactStatus.BLOCKED
    assert comparison.blocker is not None and comparison.blocker.code == "artifact_path_invalid"


@pytest.mark.parametrize(
    "artifact_path",
    [
        ".. /outside.json",
        ". /episode.json",
        "scripts /episode.json",
        ".arcreel_artifacts.json::$DATA",
        "episode.json:preview",
    ],
)
def test_manifest_blocks_windows_normalized_artifact_path_components(artifact_path: str) -> None:
    manifest = ArtifactManifest(InMemoryArtifactManifestAdapter())

    comparison = manifest.compare(
        ArtifactKey.episode_script(1),
        artifact_path=artifact_path,
        basis=ArtifactBasis.build("test/script", kind_version=1, inputs={}),
    )

    assert comparison.status is ArtifactStatus.BLOCKED
    assert comparison.blocker is not None and comparison.blocker.code == "artifact_path_invalid"


def test_manifest_blocks_non_utf8_artifact_path() -> None:
    manifest = ArtifactManifest(InMemoryArtifactManifestAdapter())

    comparison = manifest.compare(
        ArtifactKey.episode_script(1),
        artifact_path="bad_\udcff.json",
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
