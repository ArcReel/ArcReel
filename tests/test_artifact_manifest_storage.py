from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lib.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifest,
    ArtifactManifestError,
    ArtifactRegistrationError,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)

pytestmark = pytest.mark.integration


def test_project_adapter_persists_deterministic_utf8_and_skips_unchanged_write(tmp_path: Path) -> None:
    project = tmp_path / "project"
    artifact = project / "scripts" / "第一集.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"标题":"雪夜"}', encoding="utf-8")
    key = ArtifactKey.episode_script(1)
    basis = ArtifactBasis.build("test/script", kind_version=1, inputs={"标题": "雪夜"})
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project))
    manifest_path = project / MANIFEST_FILENAME

    assert manifest.compare(key, artifact_path="scripts/第一集.json", basis=basis).status is ArtifactStatus.MISSING
    assert not manifest_path.exists()
    assert manifest.register(key, artifact_path="scripts/第一集.json", basis=basis)
    first_bytes = manifest_path.read_bytes()
    first_mtime = manifest_path.stat().st_mtime_ns
    assert not manifest.register(key, artifact_path="scripts/第一集.json", basis=basis)

    assert manifest_path.read_bytes() == first_bytes
    assert manifest_path.stat().st_mtime_ns == first_mtime
    assert b"\xe7\xac\xac\xe4\xb8\x80\xe9\x9b\x86" in first_bytes
    assert json.loads(first_bytes) == {
        "entries": {
            key.encode(): {
                "artifact_path": "scripts/第一集.json",
                "basis_digest": basis.digest,
            }
        },
        "hash_algorithm": "sha256-v1",
        "schema_version": 1,
    }
    reloaded = ArtifactManifest(ProjectArtifactManifestAdapter(project))
    assert reloaded.compare(key, artifact_path="scripts/第一集.json", basis=basis).status is ArtifactStatus.CURRENT


def test_stale_comparison_preserves_paid_artifact_and_manifest(tmp_path: Path) -> None:
    project = tmp_path / "project"
    artifact = project / "videos" / "E1S01.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"paid-video-bytes")
    key = ArtifactKey.episode_video(1, "E1S01")
    recorded_basis = ArtifactBasis.build("test/video", kind_version=1, inputs={"prompt": "first"})
    current_basis = ArtifactBasis.build("test/video", kind_version=1, inputs={"prompt": "changed"})
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project))
    assert manifest.register(key, artifact_path="videos/E1S01.mp4", basis=recorded_basis)
    manifest_path = project / MANIFEST_FILENAME
    manifest_bytes = manifest_path.read_bytes()

    comparison = manifest.compare(key, artifact_path="videos/E1S01.mp4", basis=current_basis)

    assert comparison.status is ArtifactStatus.STALE
    assert comparison.usable
    assert artifact.read_bytes() == b"paid-video-bytes"
    assert manifest_path.read_bytes() == manifest_bytes


def test_project_adapter_serializes_concurrent_manifest_updates(tmp_path: Path) -> None:
    project = tmp_path / "project"
    artifact_dir = project / "scripts"
    artifact_dir.mkdir(parents=True)
    basis = ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "same"})
    episodes = list(range(1, 17))
    for episode in episodes:
        (artifact_dir / f"episode_{episode}.json").write_text("{}", encoding="utf-8")

    def register(episode: int) -> bool:
        manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project))
        return manifest.register(
            ArtifactKey.episode_script(episode),
            artifact_path=f"scripts/episode_{episode}.json",
            basis=basis,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(register, episodes))

    assert all(results)
    stored = json.loads((project / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert len(stored["entries"]) == len(episodes)


def test_project_adapter_replace_failure_preserves_manifest_and_cleans_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "episode_1.json").write_text("{}", encoding="utf-8")
    (scripts / "episode_2.json").write_text("{}", encoding="utf-8")
    basis = ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "source"})
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project))
    first_key = ArtifactKey.episode_script(1)
    second_key = ArtifactKey.episode_script(2)
    assert manifest.register(first_key, artifact_path="scripts/episode_1.json", basis=basis)
    manifest_path = project / MANIFEST_FILENAME
    original_bytes = manifest_path.read_bytes()

    def fail_replace(_source: str, _destination: Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr("lib.artifact_manifest.os.replace", fail_replace)

    with pytest.raises(ArtifactManifestError, match="replace artifact manifest"):
        manifest.register(second_key, artifact_path="scripts/episode_2.json", basis=basis)

    assert manifest_path.read_bytes() == original_bytes
    assert list(project.glob(f"{MANIFEST_FILENAME}.*.tmp")) == []
    assert (
        manifest.compare(first_key, artifact_path="scripts/episode_1.json", basis=basis).status
        is ArtifactStatus.CURRENT
    )
    assert (
        manifest.compare(second_key, artifact_path="scripts/episode_2.json", basis=basis).status
        is ArtifactStatus.MISSING
    )


def test_project_adapter_blocks_escape_and_symlink_artifact_paths(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret":true}', encoding="utf-8")
    (project / "linked-file.json").symlink_to(outside)
    (project / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project))
    key = ArtifactKey.episode_script(1)
    basis = ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "source"})

    traversal = manifest.compare(key, artifact_path="../outside.json", basis=basis)
    absolute = manifest.compare(key, artifact_path=str(outside), basis=basis)
    file_link = manifest.compare(key, artifact_path="linked-file.json", basis=basis)
    parent_link = manifest.compare(key, artifact_path="linked-dir/outside.json", basis=basis)

    assert traversal.status is ArtifactStatus.BLOCKED
    assert absolute.status is ArtifactStatus.BLOCKED
    assert file_link.status is ArtifactStatus.BLOCKED
    assert parent_link.status is ArtifactStatus.BLOCKED
    assert file_link.blocker is not None and file_link.blocker.code == "artifact_symlink"
    assert parent_link.blocker is not None and parent_link.blocker.code == "artifact_symlink"
    with pytest.raises(ArtifactRegistrationError):
        manifest.register(key, artifact_path="linked-file.json", basis=basis)
    assert outside.read_text(encoding="utf-8") == '{"secret":true}'


@pytest.mark.skipif(os.name != "posix", reason="dir_fd traversal is the POSIX symlink-race defense")
def test_project_adapter_blocks_parent_replaced_by_symlink_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "episode.json").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "episode.json").write_text("outside", encoding="utf-8")
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project))
    original_open = os.open
    swapped = False

    def swap_parent_then_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        path_text = os.fsdecode(path)
        opens_parent_by_fd = dir_fd is not None and path_text == "scripts"
        opens_final_by_path = Path(path_text) == scripts / "episode.json"
        if not swapped and (opens_parent_by_fd or opens_final_by_path):
            scripts.rename(project / "original-scripts")
            scripts.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("lib.artifact_manifest.os.open", swap_parent_then_open)

    comparison = manifest.compare(
        ArtifactKey.episode_script(1),
        artifact_path="scripts/episode.json",
        basis=ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "source"}),
    )

    assert swapped
    assert comparison.status is ArtifactStatus.BLOCKED
    assert comparison.blocker is not None and comparison.blocker.code == "artifact_symlink"
    assert (outside / "episode.json").read_text(encoding="utf-8") == "outside"


@pytest.mark.parametrize("runtime_path", [MANIFEST_FILENAME, ".artifact_manifest.lock"])
def test_project_adapter_refuses_runtime_file_symlinks(tmp_path: Path, runtime_path: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    artifact = project / "episode.json"
    artifact.write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text("do not touch", encoding="utf-8")
    (project / runtime_path).symlink_to(outside)
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project))
    key = ArtifactKey.episode_script(1)
    basis = ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "source"})

    comparison = manifest.compare(key, artifact_path="episode.json", basis=basis)

    assert comparison.status is ArtifactStatus.BLOCKED
    assert comparison.blocker is not None and comparison.blocker.code == "manifest_unreadable"
    with pytest.raises(ArtifactManifestError):
        manifest.register(key, artifact_path="episode.json", basis=basis)
    assert outside.read_text(encoding="utf-8") == "do not touch"


def test_project_adapter_reports_malformed_manifest_as_blocked_without_reset(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("{}", encoding="utf-8")
    malformed = b'{"schema_version":999,"entries":{}}'
    manifest_path = project / MANIFEST_FILENAME
    manifest_path.write_bytes(malformed)
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project))
    key = ArtifactKey.episode_script(1)
    basis = ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "source"})

    comparison = manifest.compare(key, artifact_path="episode.json", basis=basis)

    assert comparison.status is ArtifactStatus.BLOCKED
    assert comparison.blocker is not None and comparison.blocker.code == "manifest_unreadable"
    with pytest.raises(ArtifactManifestError):
        manifest.register(key, artifact_path="episode.json", basis=basis)
    assert manifest_path.read_bytes() == malformed
