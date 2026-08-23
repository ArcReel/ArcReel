from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lib.artifact_manifest import (
    HASH_ALGORITHM,
    LOCK_FILENAME,
    MANIFEST_FILENAME,
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ArtifactRegistrationError,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)

_RUNTIME_FIFO_COMPARISON = """
import json
import sys
from pathlib import Path

from lib.artifact_manifest import ArtifactBasis, ArtifactKey, ArtifactManifest, ProjectArtifactManifestAdapter

project = Path(sys.argv[1])
comparison = ArtifactManifest(ProjectArtifactManifestAdapter(project)).compare(
    ArtifactKey.episode_script(1),
    artifact_path="episode.json",
    basis=ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "source"}),
)
print(json.dumps({
    "status": comparison.status.value,
    "blocker": comparison.blocker.code if comparison.blocker is not None else None,
}))
"""


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


def test_project_adapter_replaces_the_complete_target_state_atomically(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "characters").mkdir(parents=True)
    (project / "characters" / "Alice.png").write_bytes(b"alice")
    (project / "scenes").mkdir()
    (project / "scenes" / "Cafe.png").write_bytes(b"cafe")
    adapter = ProjectArtifactManifestAdapter(project)
    old_key = ArtifactKey.asset_sheet("character", "Alice")
    adapter.put_entry(
        old_key,
        ArtifactManifestEntry(
            artifact_path="characters/Alice.png",
            basis_digest=ArtifactBasis.build("old", kind_version=1, inputs={}).digest,
        ),
    )
    target_key = ArtifactKey.asset_sheet("scene", "Cafe")
    target_entry = ArtifactManifestEntry(
        artifact_path="scenes/Cafe.png",
        basis_digest=ArtifactBasis.build("new", kind_version=1, inputs={}).digest,
    )

    assert adapter.replace_entries_atomically({target_key: target_entry}) is True
    assert adapter.get_entry(old_key) is None
    assert adapter.get_entry(target_key) == target_entry

    manifest_path = project / MANIFEST_FILENAME
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    assert adapter.replace_entries_atomically({target_key: target_entry}) is False
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_project_adapter_rejects_a_second_key_claiming_an_existing_formal_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    artifact = project / "videos" / "scene_E1S01.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"episode-one")
    adapter = ProjectArtifactManifestAdapter(project)
    first_key = ArtifactKey.episode_video(1, "E1S01")
    second_key = ArtifactKey.episode_video(2, "E1S01")
    first_entry = ArtifactManifestEntry(
        artifact_path="videos/scene_E1S01.mp4",
        basis_digest=ArtifactBasis.build("video", kind_version=1, inputs={"episode": 1}).digest,
    )
    second_entry = ArtifactManifestEntry(
        artifact_path="videos/scene_E1S01.mp4",
        basis_digest=ArtifactBasis.build("video", kind_version=1, inputs={"episode": 2}).digest,
    )
    assert adapter.put_entry(first_key, first_entry)
    manifest_before = (project / MANIFEST_FILENAME).read_bytes()

    with pytest.raises(ArtifactManifestError, match="formal artifact path.*multiple keys"):
        adapter.put_entry(second_key, second_entry)

    assert (project / MANIFEST_FILENAME).read_bytes() == manifest_before
    assert adapter.snapshot_entries() == {first_key: first_entry}


@pytest.mark.parametrize(
    ("first_path", "second_path"),
    [
        ("videos/scene_E1S01.mp4", "videos/scene_E1S01.mp4"),
        ("videos/scene_E1S01.mp4", "videos/scene_e1s01.mp4"),
        ("videos/é.mp4", "videos/e\u0301.mp4"),
    ],
    ids=("identical", "case-alias", "unicode-alias"),
)
def test_project_adapter_rejects_manifest_snapshot_with_duplicate_path_ownership(
    tmp_path: Path,
    first_path: str,
    second_path: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    first_key = ArtifactKey.episode_video(1, "E1S01")
    second_key = ArtifactKey.episode_video(2, "E1S01")
    malformed = json.dumps(
        {
            "entries": {
                first_key.encode(): {
                    "artifact_path": first_path,
                    "basis_digest": ArtifactBasis.build("video", kind_version=1, inputs={"episode": 1}).digest,
                },
                second_key.encode(): {
                    "artifact_path": second_path,
                    "basis_digest": ArtifactBasis.build("video", kind_version=1, inputs={"episode": 2}).digest,
                },
            },
            "hash_algorithm": HASH_ALGORITHM,
            "schema_version": 1,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest_path = project / MANIFEST_FILENAME
    manifest_path.write_bytes(malformed)
    adapter = ProjectArtifactManifestAdapter(project)

    with pytest.raises(ArtifactManifestError, match="formal artifact path.*multiple keys"):
        adapter.get_entry(first_key)
    with pytest.raises(ArtifactManifestError, match="formal artifact path.*multiple keys"):
        adapter.snapshot_entries()

    assert manifest_path.read_bytes() == malformed


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


def test_project_adapter_read_does_not_create_a_lock_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    adapter = ProjectArtifactManifestAdapter(project)

    assert adapter.get_entry(ArtifactKey.episode_script(1)) is None
    assert adapter.snapshot_entries() == {}
    assert not (project / LOCK_FILENAME).exists()


@pytest.mark.skipif(os.name != "posix", reason="exclusive lock-file creation protects concurrent openat calls")
def test_project_adapter_creates_lock_file_exclusively(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    artifact = project / "episode.json"
    artifact.write_text("{}", encoding="utf-8")
    original_open = os.open
    lock_open_flags: list[int] = []

    def record_lock_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if dir_fd is not None and os.fsdecode(path) == LOCK_FILENAME:
            lock_open_flags.append(flags)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("lib.artifact_manifest.os.open", record_lock_open)

    key = ArtifactKey.episode_script(1)
    adapter = ProjectArtifactManifestAdapter(project)
    assert ArtifactManifest(adapter).register(
        key,
        artifact_path="episode.json",
        basis=ArtifactBasis.build("test/script", kind_version=1, inputs={}),
    )
    assert lock_open_flags[0] & os.O_CREAT
    assert lock_open_flags[0] & os.O_EXCL

    lock_open_flags.clear()
    assert adapter.get_entry(key) is not None
    assert len(lock_open_flags) == 2
    assert lock_open_flags[0] & os.O_EXCL
    assert not lock_open_flags[1] & os.O_CREAT


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
    original_replace = os.replace

    def fail_replace(
        source: str | bytes | Path,
        destination: str | bytes | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        is_anchored_manifest = (
            os.fsdecode(destination) == MANIFEST_FILENAME and src_dir_fd is not None and dst_dir_fd == src_dir_fd
        )
        if is_anchored_manifest or Path(os.fsdecode(destination)) == manifest_path:
            raise OSError("injected replace failure")
        if src_dir_fd is None and dst_dir_fd is None:
            original_replace(source, destination)
        else:
            original_replace(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

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


@pytest.mark.parametrize("force_python_link_fallback", [False, True])
def test_project_adapter_blocks_escape_and_symlink_artifact_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    force_python_link_fallback: bool,
) -> None:
    if force_python_link_fallback:
        monkeypatch.setattr("lib.artifact_manifest._O_NOFOLLOW", 0)
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
    assert traversal.blocker is not None and traversal.blocker.code == "artifact_path_invalid"
    assert absolute.blocker is not None and absolute.blocker.code == "artifact_path_invalid"
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


@pytest.mark.skipif(os.name != "posix", reason="Python identity checks backstop platforms without O_NOFOLLOW")
def test_project_adapter_blocks_file_symlink_swap_without_no_follow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    artifact = project / "episode.json"
    artifact.write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    adapter = ProjectArtifactManifestAdapter(project)
    original_open = os.open
    swapped = False

    def swap_file_then_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and dir_fd is not None and os.fsdecode(path) == "episode.json":
            artifact.rename(project / "original-episode.json")
            artifact.symlink_to(outside)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("lib.artifact_manifest._O_NOFOLLOW", 0)
    monkeypatch.setattr("lib.artifact_manifest.os.open", swap_file_then_open)

    observation = adapter.inspect_artifact("episode.json")

    assert swapped
    assert not observation.present
    assert observation.blocker is not None and observation.blocker.code == "artifact_symlink"


@pytest.mark.skipif(os.name != "posix", reason="Python identity checks backstop platforms without O_NOFOLLOW")
def test_project_adapter_blocks_parent_vanishing_during_fallback_revalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "episode.json").write_text("inside", encoding="utf-8")
    adapter = ProjectArtifactManifestAdapter(project)
    original_open = os.open
    moved_scripts = project / "removed-scripts"
    moved = False

    def move_parent_after_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal moved
        fd = original_open(path, flags, mode, dir_fd=dir_fd)
        if not moved and dir_fd is not None and os.fsdecode(path) == "scripts":
            scripts.rename(moved_scripts)
            moved = True
        return fd

    monkeypatch.setattr("lib.artifact_manifest._O_NOFOLLOW", 0)
    monkeypatch.setattr("lib.artifact_manifest.os.open", move_parent_after_open)

    observation = adapter.inspect_artifact("scripts/episode.json")

    assert moved
    assert not observation.present
    assert observation.blocker is not None and observation.blocker.code == "artifact_unreadable"


@pytest.mark.skipif(os.name != "posix", reason="FIFO inspection uses POSIX nonblocking file flags")
def test_project_adapter_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    os.mkfifo(project / "artifact.fifo")
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project))

    comparison = manifest.compare(
        ArtifactKey.episode_script(1),
        artifact_path="artifact.fifo",
        basis=ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "source"}),
    )

    assert comparison.status is ArtifactStatus.BLOCKED
    assert comparison.blocker is not None and comparison.blocker.code == "artifact_not_regular_file"


@pytest.mark.skipif(os.name != "posix", reason="descriptor traversal is the POSIX storage path")
def test_project_adapter_reports_missing_posix_artifact_components(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    adapter = ProjectArtifactManifestAdapter(project)

    assert not adapter.inspect_artifact("missing/episode.json").present
    assert not adapter.inspect_artifact("episode.json").present


def test_project_adapter_hashes_content_only_through_the_explicit_snapshot_seam(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    content = b"formal-provider-input"
    (project / "episode.json").write_bytes(content)
    adapter = ProjectArtifactManifestAdapter(project)

    ordinary = adapter.inspect_artifact("episode.json")
    snapshot = adapter.inspect_artifact_content("episode.json")

    assert ordinary.present and ordinary.content_digest is None
    assert snapshot.present and snapshot.content_digest == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize("inspection_path", ["posix", "portable"])
def test_project_adapter_rejects_in_place_write_during_content_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inspection_path: str,
) -> None:
    if inspection_path == "posix" and os.name != "posix":
        pytest.skip("descriptor traversal is the POSIX artifact inspection path")
    project = tmp_path / "project"
    project.mkdir()
    artifact = project / "episode.json"
    artifact.write_bytes(b"formal-provider-input")
    original_identity = (artifact.stat().st_dev, artifact.stat().st_ino)
    adapter = ProjectArtifactManifestAdapter(project)
    original_read = os.read
    mutated = False

    def mutate_after_first_read(fd: int, length: int) -> bytes:
        nonlocal mutated
        chunk = original_read(fd, length)
        if chunk and not mutated:
            with artifact.open("ab") as handle:
                handle.write(b"-concurrent-update")
            mutated = True
        return chunk

    monkeypatch.setattr("lib.artifact_manifest.os.read", mutate_after_first_read)

    if inspection_path == "posix":
        observation = adapter._inspect_artifact_posix("episode.json", include_content_digest=True)
    else:
        observation = adapter._inspect_artifact_portable("episode.json", include_content_digest=True)

    assert mutated
    assert (artifact.stat().st_dev, artifact.stat().st_ino) == original_identity
    assert not observation.present
    assert observation.blocker is not None and observation.blocker.code == "artifact_unreadable"


@pytest.mark.skipif(os.name != "posix", reason="descriptor reads are the POSIX artifact inspection path")
def test_project_adapter_reports_posix_artifact_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("{}", encoding="utf-8")
    adapter = ProjectArtifactManifestAdapter(project)

    def fail_read(_fd: int, _length: int) -> bytes:
        raise OSError("read failed")

    monkeypatch.setattr("lib.artifact_manifest.os.read", fail_read)

    observation = adapter.inspect_artifact("episode.json")

    assert not observation.present
    assert observation.blocker is not None and observation.blocker.code == "artifact_unreadable"


@pytest.mark.skipif(os.name != "posix", reason="runtime FIFO inspection uses POSIX nonblocking file flags")
@pytest.mark.parametrize("runtime_path", [MANIFEST_FILENAME, LOCK_FILENAME])
def test_project_adapter_rejects_runtime_fifo_without_blocking(tmp_path: Path, runtime_path: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("{}", encoding="utf-8")
    os.mkfifo(project / runtime_path)
    try:
        result = subprocess.run(
            [sys.executable, "-c", _RUNTIME_FIFO_COMPARISON, str(project)],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"manifest comparison blocked on runtime FIFO: {runtime_path}")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "blocked", "blocker": "manifest_unreadable"}


def test_project_adapter_rejects_replaced_portable_project_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("inside", encoding="utf-8")
    adapter = ProjectArtifactManifestAdapter(project)
    moved_project = tmp_path / "moved-project"
    project.rename(moved_project)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "episode.json").write_text("outside", encoding="utf-8")
    project.symlink_to(outside, target_is_directory=True)

    observation = adapter._inspect_artifact_portable("episode.json")

    assert not observation.present
    assert observation.blocker is not None and observation.blocker.code == "artifact_symlink"
    with pytest.raises(ArtifactManifestError, match="project directory"):
        adapter.put_entry(
            ArtifactKey.episode_script(1),
            ArtifactManifestEntry(
                artifact_path="episode.json",
                basis_digest=ArtifactBasis.build("test/script", kind_version=1, inputs={}).digest,
            ),
        )
    assert (outside / "episode.json").read_text(encoding="utf-8") == "outside"
    assert not (outside / MANIFEST_FILENAME).exists()


def test_project_adapter_rejects_replaced_portable_project_root_identity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    adapter = ProjectArtifactManifestAdapter(project)
    project.rename(tmp_path / "original-project")
    project.mkdir()

    observation = adapter._inspect_artifact_portable("missing.json")

    assert not observation.present
    assert observation.blocker is not None and observation.blocker.code == "artifact_unreadable"
    with pytest.raises(ArtifactManifestError, match="changed after adapter initialization"):
        adapter._assert_portable_project_root_identity()


@pytest.mark.skipif(os.name != "posix", reason="opened directory identity is the POSIX replacement defense")
def test_project_adapter_rejects_replaced_posix_project_root_identity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("inside", encoding="utf-8")
    adapter = ProjectArtifactManifestAdapter(project)
    project.rename(tmp_path / "original-project")
    project.mkdir()
    (project / "episode.json").write_text("replacement", encoding="utf-8")

    observation = adapter.inspect_artifact("episode.json")

    assert not observation.present
    assert observation.blocker is not None and observation.blocker.code == "artifact_unreadable"
    with pytest.raises(ArtifactManifestError, match="changed after adapter initialization"):
        adapter.put_entry(
            ArtifactKey.episode_script(1),
            ArtifactManifestEntry(
                artifact_path="episode.json",
                basis_digest=ArtifactBasis.build("test/script", kind_version=1, inputs={}).digest,
            ),
        )
    assert not (project / MANIFEST_FILENAME).exists()


@pytest.mark.skipif(os.name != "posix", reason="no-follow root descriptors bind POSIX adapter initialization")
def test_project_adapter_rejects_project_root_symlink_swap_during_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    original_project = tmp_path / "original-project"
    original_resolve = Path.resolve
    swapped = False

    def swap_then_resolve(path: Path, strict: bool = False) -> Path:
        nonlocal swapped
        if not swapped and path == project:
            project.rename(original_project)
            project.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_resolve(path, strict=strict)

    monkeypatch.setattr("lib.artifact_manifest.Path.resolve", swap_then_resolve)

    with pytest.raises(ArtifactManifestError, match="changed during adapter initialization"):
        ProjectArtifactManifestAdapter(project)

    assert swapped
    assert not (outside / MANIFEST_FILENAME).exists()


@pytest.mark.skipif(os.name != "posix", reason="opened directory identity is the POSIX replacement defense")
def test_project_adapter_reports_unavailable_opened_posix_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    adapter = ProjectArtifactManifestAdapter(project)

    with pytest.raises(ArtifactManifestError, match="opened project directory is unavailable"):
        adapter._assert_open_project_root_identity(-1)


@pytest.mark.skipif(os.name != "posix", reason="Python link checks backstop platforms without O_NOFOLLOW")
def test_project_adapter_rejects_replaced_posix_project_root_symlink_without_no_follow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("inside", encoding="utf-8")
    adapter = ProjectArtifactManifestAdapter(project)
    original_project = tmp_path / "original-project"
    project.rename(original_project)
    project.symlink_to(original_project, target_is_directory=True)
    monkeypatch.setattr("lib.artifact_manifest._O_NOFOLLOW", 0)

    observation = adapter.inspect_artifact("episode.json")

    assert not observation.present
    assert observation.blocker is not None and observation.blocker.code == "artifact_symlink"
    with pytest.raises(ArtifactManifestError, match="project directory is a symlink"):
        adapter.get_entry(ArtifactKey.episode_script(1))


def test_project_adapter_rejects_swapped_portable_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "episode.json").write_text("inside", encoding="utf-8")
    adapter = ProjectArtifactManifestAdapter(project)
    moved_scripts = project / "original-scripts"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "episode.json").write_text("outside", encoding="utf-8")
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
        if not swapped and dir_fd is None and Path(os.fsdecode(path)) == scripts / "episode.json":
            scripts.rename(moved_scripts)
            scripts.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("lib.artifact_manifest.os.open", swap_parent_then_open)

    observation = adapter._inspect_artifact_portable("scripts/episode.json")

    assert swapped
    assert not observation.present
    assert observation.blocker is not None and observation.blocker.code == "artifact_symlink"
    assert (outside / "episode.json").read_text(encoding="utf-8") == "outside"


@pytest.mark.skipif(os.name != "posix", reason="dir_fd anchors manifest storage to the opened POSIX root")
def test_project_adapter_keeps_manifest_write_on_opened_root_during_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("inside", encoding="utf-8")
    adapter = ProjectArtifactManifestAdapter(project)
    original_identity = project.stat()
    original_open = os.open
    moved_project = tmp_path / "moved-project"
    outside = tmp_path / "outside"
    outside.mkdir()
    swapped = False

    def swap_root_then_open_lock(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        opens_target_lock = (
            dir_fd is not None
            and os.fsdecode(path) == LOCK_FILENAME
            and os.fstat(dir_fd).st_dev == original_identity.st_dev
            and os.fstat(dir_fd).st_ino == original_identity.st_ino
        )
        if not swapped and opens_target_lock:
            project.rename(moved_project)
            project.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("lib.artifact_manifest.os.open", swap_root_then_open_lock)

    assert ArtifactManifest(adapter).register(
        ArtifactKey.episode_script(1),
        artifact_path="episode.json",
        basis=ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "source"}),
    )

    assert swapped
    assert (moved_project / MANIFEST_FILENAME).is_file()
    assert not (outside / MANIFEST_FILENAME).exists()
    assert not (outside / LOCK_FILENAME).exists()


@pytest.mark.parametrize("runtime_path", [MANIFEST_FILENAME, LOCK_FILENAME])
@pytest.mark.parametrize("force_python_link_fallback", [False, True])
def test_project_adapter_refuses_runtime_file_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_path: str,
    force_python_link_fallback: bool,
) -> None:
    if force_python_link_fallback:
        monkeypatch.setattr("lib.artifact_manifest._O_NOFOLLOW", 0)
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


def test_project_adapter_rejects_portable_manifest_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("{}", encoding="utf-8")
    adapter = ProjectArtifactManifestAdapter(project)
    manifest = ArtifactManifest(adapter)
    key = ArtifactKey.episode_script(1)
    basis = ArtifactBasis.build("test/script", kind_version=1, inputs={})
    assert manifest.register(key, artifact_path="episode.json", basis=basis)
    manifest_path = project / MANIFEST_FILENAME
    outside = tmp_path / "outside.json"
    outside.write_bytes(manifest_path.read_bytes())
    original_open = os.open
    swapped = False

    def swap_manifest_then_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and dir_fd is None and Path(os.fsdecode(path)) == manifest_path:
            manifest_path.rename(project / "original-manifest.json")
            manifest_path.symlink_to(outside)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("lib.artifact_manifest.os.open", swap_manifest_then_open)

    with pytest.raises(ArtifactManifestError, match="artifact manifest is a symlink"):
        adapter._load_unlocked(None)

    assert swapped


@pytest.mark.skipif(os.name != "posix", reason="Python identity checks backstop platforms without O_NOFOLLOW")
def test_project_adapter_rejects_manifest_symlink_swap_without_no_follow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("{}", encoding="utf-8")
    adapter = ProjectArtifactManifestAdapter(project)
    manifest = ArtifactManifest(adapter)
    key = ArtifactKey.episode_script(1)
    basis = ArtifactBasis.build("test/script", kind_version=1, inputs={})
    assert manifest.register(key, artifact_path="episode.json", basis=basis)
    manifest_path = project / MANIFEST_FILENAME
    outside = tmp_path / "outside.json"
    outside.write_bytes(manifest_path.read_bytes())
    original_open = os.open
    swapped = False

    def swap_manifest_then_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and dir_fd is not None and os.fsdecode(path) == MANIFEST_FILENAME:
            manifest_path.rename(project / "original-manifest.json")
            manifest_path.symlink_to(outside)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("lib.artifact_manifest._O_NOFOLLOW", 0)
    monkeypatch.setattr("lib.artifact_manifest.os.open", swap_manifest_then_open)

    comparison = manifest.compare(key, artifact_path="episode.json", basis=basis)

    assert swapped
    assert comparison.status is ArtifactStatus.BLOCKED
    assert comparison.blocker is not None and comparison.blocker.code == "manifest_unreadable"


@pytest.mark.skipif(os.name != "posix", reason="Python identity checks backstop platforms without O_NOFOLLOW")
def test_project_adapter_rejects_lock_symlink_swap_without_no_follow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("{}", encoding="utf-8")
    adapter = ProjectArtifactManifestAdapter(project)
    manifest = ArtifactManifest(adapter)
    key = ArtifactKey.episode_script(1)
    basis = ArtifactBasis.build("test/script", kind_version=1, inputs={})
    assert manifest.register(key, artifact_path="episode.json", basis=basis)
    lock_path = project / LOCK_FILENAME
    outside = tmp_path / "outside.lock"
    outside.write_bytes(b"")
    original_open = os.open
    swapped = False

    def swap_lock_then_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and dir_fd is not None and os.fsdecode(path) == LOCK_FILENAME:
            lock_path.rename(project / "original-lock")
            lock_path.symlink_to(outside)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("lib.artifact_manifest._O_NOFOLLOW", 0)
    monkeypatch.setattr("lib.artifact_manifest.os.open", swap_lock_then_open)

    comparison = manifest.compare(key, artifact_path="episode.json", basis=basis)

    assert swapped
    assert comparison.status is ArtifactStatus.BLOCKED
    assert comparison.blocker is not None and comparison.blocker.code == "manifest_unreadable"


def test_project_adapter_revalidates_portable_manifest_identity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("{}", encoding="utf-8")
    adapter = ProjectArtifactManifestAdapter(project)
    key = ArtifactKey.episode_script(1)
    basis = ArtifactBasis.build("test/script", kind_version=1, inputs={})
    assert ArtifactManifest(adapter).register(key, artifact_path="episode.json", basis=basis)

    entries, raw = adapter._load_unlocked(None)

    assert raw is not None
    assert entries[key.encode()].basis_digest == basis.digest


@pytest.mark.parametrize("schema_version", [999, True, 1.0])
def test_project_adapter_reports_invalid_manifest_schema_version_as_blocked_without_reset(
    tmp_path: Path,
    schema_version: object,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("{}", encoding="utf-8")
    malformed = json.dumps(
        {"entries": {}, "hash_algorithm": HASH_ALGORITHM, "schema_version": schema_version},
        separators=(",", ":"),
    ).encode()
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


def test_project_adapter_reports_oversized_manifest_integer_as_blocked_without_reset(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("{}", encoding="utf-8")
    malformed = (
        b'{"entries":{},"hash_algorithm":"' + HASH_ALGORITHM.encode() + b'","schema_version":' + b"9" * 5000 + b"}"
    )
    manifest_path = project / MANIFEST_FILENAME
    manifest_path.write_bytes(malformed)
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project))

    comparison = manifest.compare(
        ArtifactKey.episode_script(1),
        artifact_path="episode.json",
        basis=ArtifactBasis.build("test/script", kind_version=1, inputs={}),
    )

    assert comparison.status is ArtifactStatus.BLOCKED
    assert comparison.blocker is not None and comparison.blocker.code == "manifest_unreadable"
    assert manifest_path.read_bytes() == malformed


def test_project_adapter_reports_recursive_encoded_key_as_blocked_without_reset(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("{}", encoding="utf-8")
    nested_payload = ('["episode-script",' + "[" * 2000 + "0" + "]" * 2000 + "]").encode()
    encoded_key = "artifact-key-v1:" + base64.urlsafe_b64encode(nested_payload).decode().rstrip("=")
    malformed = json.dumps(
        {
            "entries": {
                encoded_key: {
                    "artifact_path": "episode.json",
                    "basis_digest": ArtifactBasis.build("test/script", kind_version=1, inputs={}).digest,
                }
            },
            "hash_algorithm": HASH_ALGORITHM,
            "schema_version": 1,
        },
        separators=(",", ":"),
    ).encode()
    manifest_path = project / MANIFEST_FILENAME
    manifest_path.write_bytes(malformed)
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project))

    comparison = manifest.compare(
        ArtifactKey.episode_script(1),
        artifact_path="episode.json",
        basis=ArtifactBasis.build("test/script", kind_version=1, inputs={}),
    )

    assert comparison.status is ArtifactStatus.BLOCKED
    assert comparison.blocker is not None and comparison.blocker.code == "manifest_unreadable"
    assert manifest_path.read_bytes() == malformed


@pytest.mark.parametrize("duplicate_location", ["top-level", "entry"])
def test_project_adapter_reports_duplicate_manifest_fields_as_blocked_without_reset(
    tmp_path: Path,
    duplicate_location: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("{}", encoding="utf-8")
    key = ArtifactKey.episode_script(1)
    basis = ArtifactBasis.build("test/script", kind_version=1, inputs={"step1": "source"})
    encoded_key = key.encode()
    entry = json.dumps(
        {"artifact_path": "episode.json", "basis_digest": basis.digest},
        separators=(",", ":"),
    )
    if duplicate_location == "top-level":
        malformed_text = (
            f'{{"entries":{{}},"hash_algorithm":"{HASH_ALGORITHM}","schema_version":999,"schema_version":1}}'
        )
    else:
        malformed_text = (
            f'{{"entries":{{"{encoded_key}":{entry},"{encoded_key}":{entry}}},'
            f'"hash_algorithm":"{HASH_ALGORITHM}","schema_version":1}}'
        )
    malformed = malformed_text.encode("utf-8")
    manifest_path = project / MANIFEST_FILENAME
    manifest_path.write_bytes(malformed)
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project))

    comparison = manifest.compare(key, artifact_path="episode.json", basis=basis)

    assert comparison.status is ArtifactStatus.BLOCKED
    assert comparison.blocker is not None and comparison.blocker.code == "manifest_unreadable"
    with pytest.raises(ArtifactManifestError):
        manifest.register(key, artifact_path="episode.json", basis=basis)
    assert manifest_path.read_bytes() == malformed


def test_project_adapter_reports_excessive_manifest_nesting_as_blocked_without_reset(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "episode.json").write_text("{}", encoding="utf-8")
    malformed = b"[" * 2000 + b"]" * 2000
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
