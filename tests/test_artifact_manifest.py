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
    ArtifactManifestError,
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


def test_manifest_does_not_apply_an_old_path_claim_to_a_new_pointer() -> None:
    old_path = "storyboards/scene_E1S01.png"
    new_path = "storyboards/scene_E1S01_first.png"
    adapter = InMemoryArtifactManifestAdapter(artifacts={old_path, new_path})
    manifest = ArtifactManifest(adapter)
    key = ArtifactKey.episode_storyboard(1, "E1S01")
    basis = ArtifactBasis.build("test/storyboard", kind_version=1, inputs={"prompt": "rain"})
    manifest.register(key, artifact_path=old_path, basis=basis)

    comparison = manifest.compare_entry(
        key,
        artifact_path=new_path,
        expected=ArtifactManifestEntry(artifact_path=new_path, basis_digest=basis.digest),
    )

    assert comparison.status is ArtifactStatus.MISSING
    assert not comparison.usable


def test_manifest_rekey_plan_moves_one_claim_atomically_and_can_compensate() -> None:
    old_key = ArtifactKey.asset_sheet("character", "角色A")
    new_key = ArtifactKey.asset_sheet("character", "主角甲")
    unrelated_key = ArtifactKey.episode_script(1)
    old_entry = ArtifactManifestEntry("characters/角色A.png", "sha256-v1:" + "a" * 64)
    unrelated_entry = ArtifactManifestEntry("scripts/episode_1.json", "sha256-v1:" + "b" * 64)
    adapter = InMemoryArtifactManifestAdapter()
    adapter.put_entry(old_key, old_entry)
    adapter.put_entry(unrelated_key, unrelated_entry)

    plan = ArtifactManifest(adapter).plan_entry_rekey(
        old_key,
        new_key,
        artifact_path_rewrites={"characters/角色A.png": "characters/主角甲.png"},
    )

    assert adapter.get_entry(old_key) == old_entry
    receipt = plan.commit()
    assert adapter.get_entry(old_key) is None
    assert adapter.get_entry(new_key) == ArtifactManifestEntry(
        "characters/主角甲.png",
        old_entry.basis_digest,
    )
    assert adapter.get_entry(unrelated_key) == unrelated_entry

    assert receipt.compensate()
    assert adapter.get_entry(old_key) == old_entry
    assert adapter.get_entry(new_key) is None
    assert adapter.get_entry(unrelated_key) == unrelated_entry


def test_manifest_rekey_plan_rejects_a_target_claim_without_mutating_either_key() -> None:
    old_key = ArtifactKey.asset_sheet("character", "角色A")
    new_key = ArtifactKey.asset_sheet("character", "主角甲")
    old_entry = ArtifactManifestEntry("characters/角色A.png", "sha256-v1:" + "a" * 64)
    target_entry = ArtifactManifestEntry("characters/主角甲.png", "sha256-v1:" + "b" * 64)
    adapter = InMemoryArtifactManifestAdapter()
    adapter.put_entry(old_key, old_entry)
    adapter.put_entry(new_key, target_entry)

    with pytest.raises(ArtifactManifestError, match="target key"):
        ArtifactManifest(adapter).plan_entry_rekey(old_key, new_key)

    assert adapter.get_entry(old_key) == old_entry
    assert adapter.get_entry(new_key) == target_entry


def test_complete_snapshot_cas_does_not_overwrite_an_unexpected_claim() -> None:
    first_key = ArtifactKey.episode_script(1)
    second_key = ArtifactKey.episode_script(2)
    first_entry = ArtifactManifestEntry("scripts/episode_1.json", "sha256-v1:" + "a" * 64)
    second_entry = ArtifactManifestEntry("scripts/episode_2.json", "sha256-v1:" + "b" * 64)
    adapter = InMemoryArtifactManifestAdapter()
    adapter.put_entry(first_key, first_entry)
    expected = adapter.snapshot_entries()
    adapter.put_entry(second_key, second_entry)

    assert not adapter.replace_snapshot_if_matches_atomically(expected=expected, replacement={})
    assert adapter.snapshot_entries() == {
        first_key: first_entry,
        second_key: second_entry,
    }
    assert adapter.replace_snapshot_if_matches_atomically(
        expected=adapter.snapshot_entries(),
        replacement=expected,
    )
    assert adapter.snapshot_entries() == expected


def test_manifest_rekey_plan_restores_both_keys_after_a_write_then_failure(monkeypatch) -> None:
    old_key = ArtifactKey.asset_sheet("character", "角色A")
    new_key = ArtifactKey.asset_sheet("character", "主角甲")
    old_entry = ArtifactManifestEntry("characters/角色A.png", "sha256-v1:" + "a" * 64)
    adapter = InMemoryArtifactManifestAdapter()
    adapter.put_entry(old_key, old_entry)
    plan = ArtifactManifest(adapter).plan_entry_rekey(old_key, new_key)
    original_replace = adapter.replace_entries_if_matches_atomically
    calls = 0

    def _write_then_fail(*, expected, replacements):
        nonlocal calls
        calls += 1
        changed = original_replace(expected=expected, replacements=replacements)
        if calls == 1:
            raise OSError("manifest write failed")
        return changed

    monkeypatch.setattr(adapter, "replace_entries_if_matches_atomically", _write_then_fail)

    with pytest.raises(OSError, match="manifest write failed"):
        plan.commit()

    assert adapter.get_entry(old_key) == old_entry
    assert adapter.get_entry(new_key) is None


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
