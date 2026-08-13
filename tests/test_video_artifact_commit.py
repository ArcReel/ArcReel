from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ProjectArtifactManifestAdapter,
)
from lib.version_manager import VersionManager
from lib.video_artifact_commit import commit_paid_video_artifact

pytestmark = pytest.mark.integration


def _descriptor(label: str) -> ArtifactBasisDescriptor:
    return ArtifactBasisDescriptor.from_basis(
        ArtifactBasis.build("artifact-components/video", kind_version=1, inputs={"label": label})
    )


def _seed_current(project: Path, versions: VersionManager) -> tuple[Path, int]:
    current = project / "videos" / "scene_E1S01.mp4"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old-current")
    version = versions.add_version("videos", "E1S01", "old", source_file=current)
    return current, version


def test_matching_typed_basis_selects_and_registers_inside_the_shared_guard(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    versions = VersionManager(project)
    current, _old_version = _seed_current(project, versions)
    staged = current.with_name(".scene_E1S01.new.mp4")
    staged.write_bytes(b"new-current")
    basis = _descriptor("new")
    events: list[str] = []

    @contextmanager
    def _guard() -> Iterator[object]:
        events.append("guard-enter")
        yield object()
        events.append("guard-exit")

    def _current(_metadata: dict[str, object]) -> ArtifactBasisDescriptor:
        events.append("compare")
        return basis

    outcome = commit_paid_video_artifact(
        project_path=project,
        versions=versions,
        resource_type="videos",
        resource_id="E1S01",
        prompt="new",
        staged_file=staged,
        current_file=current,
        duration_seconds=8,
        version_metadata={"artifact_episode": 1, "artifact_video_basis": basis.to_dict()},
        resolve_current_basis=_current,
        selection_guard=_guard,
    )

    assert outcome.selected is True
    assert current.read_bytes() == b"new-current"
    assert events == ["guard-enter", "compare", "guard-exit"]
    entry = ProjectArtifactManifestAdapter(project).get_entry(ArtifactKey.episode_video(1, "E1S01"))
    assert entry is not None
    assert entry.artifact_path == "videos/scene_E1S01.mp4"
    assert entry.basis_digest == basis.digest


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"artifact_episode": 1},
        {"artifact_episode": 1, "artifact_video_basis": {"kind": "broken"}},
    ],
)
def test_incomplete_or_malformed_typed_facts_are_history_only(
    tmp_path: Path,
    metadata: dict[str, object],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    versions = VersionManager(project)
    current, old_version = _seed_current(project, versions)
    staged = current.with_name(".scene_E1S01.late.mp4")
    staged.write_bytes(b"late-paid")

    outcome = commit_paid_video_artifact(
        project_path=project,
        versions=versions,
        resource_type="videos",
        resource_id="E1S01",
        prompt="late",
        staged_file=staged,
        current_file=current,
        duration_seconds=8,
        version_metadata=metadata,
        resolve_current_basis=lambda _metadata: pytest.fail("legacy output must not infer a basis"),
    )

    assert outcome.selected is False
    assert current.read_bytes() == b"old-current"
    history = versions.get_versions("videos", "E1S01")
    assert history["current_version"] == old_version
    assert len(history["versions"]) == 2
    assert ProjectArtifactManifestAdapter(project).get_entry(ArtifactKey.episode_video(1, "E1S01")) is None


def test_late_basis_mismatch_preserves_paid_history_without_taking_current(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    versions = VersionManager(project)
    current, old_version = _seed_current(project, versions)
    staged = current.with_name(".scene_E1S01.late.mp4")
    staged.write_bytes(b"late-paid")
    frozen = _descriptor("submitted")

    outcome = commit_paid_video_artifact(
        project_path=project,
        versions=versions,
        resource_type="videos",
        resource_id="E1S01",
        prompt="late",
        staged_file=staged,
        current_file=current,
        duration_seconds=8,
        version_metadata={"artifact_episode": 1, "artifact_video_basis": frozen.to_dict()},
        resolve_current_basis=lambda _metadata: _descriptor("edited"),
    )

    assert outcome.selected is False
    assert current.read_bytes() == b"old-current"
    history = versions.get_versions("videos", "E1S01")
    assert history["current_version"] == old_version
    assert (project / history["versions"][-1]["file"]).read_bytes() == b"late-paid"


def test_manifest_failure_restores_old_selection_but_keeps_paid_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    versions = VersionManager(project)
    current, old_version = _seed_current(project, versions)
    old_basis = _descriptor("old")
    key = ArtifactKey.episode_video(1, "E1S01")
    adapter = ProjectArtifactManifestAdapter(project)
    from lib.artifact_manifest import ArtifactManifest

    ArtifactManifest(adapter).register_descriptor(
        key,
        artifact_path="videos/scene_E1S01.mp4",
        basis=old_basis,
    )
    staged = current.with_name(".scene_E1S01.new.mp4")
    staged.write_bytes(b"new-paid")
    new_basis = _descriptor("new")
    original_put = ProjectArtifactManifestAdapter.put_entry

    def _write_then_fail(self, artifact_key, entry):
        changed = original_put(self, artifact_key, entry)
        if artifact_key == key and entry.basis_digest == new_basis.digest:
            raise RuntimeError("manifest injected failure")
        return changed

    monkeypatch.setattr(ProjectArtifactManifestAdapter, "put_entry", _write_then_fail)

    with pytest.raises(RuntimeError, match="manifest injected failure"):
        commit_paid_video_artifact(
            project_path=project,
            versions=versions,
            resource_type="videos",
            resource_id="E1S01",
            prompt="new",
            staged_file=staged,
            current_file=current,
            duration_seconds=8,
            version_metadata={"artifact_episode": 1, "artifact_video_basis": new_basis.to_dict()},
            resolve_current_basis=lambda _metadata: new_basis,
        )

    assert current.read_bytes() == b"old-current"
    history = versions.get_versions("videos", "E1S01")
    assert history["current_version"] == old_version
    assert len(history["versions"]) == 2
    assert (project / history["versions"][-1]["file"]).read_bytes() == b"new-paid"
    restored = ProjectArtifactManifestAdapter(project).get_entry(key)
    assert restored is not None
    assert restored.basis_digest == old_basis.digest


def test_selection_guard_failure_still_archives_the_paid_video(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    versions = VersionManager(project)
    current, old_version = _seed_current(project, versions)
    staged = current.with_name(".scene_E1S01.paid.mp4")
    staged.write_bytes(b"paid-before-project-read-failed")
    basis = _descriptor("submitted")

    @contextmanager
    def _failed_guard() -> Iterator[object]:
        raise OSError("project snapshot unavailable")
        yield object()

    with pytest.raises(OSError, match="project snapshot unavailable"):
        commit_paid_video_artifact(
            project_path=project,
            versions=versions,
            resource_type="videos",
            resource_id="E1S01",
            prompt="paid",
            staged_file=staged,
            current_file=current,
            duration_seconds=8,
            version_metadata={"artifact_episode": 1, "artifact_video_basis": basis.to_dict()},
            resolve_current_basis=lambda _metadata: basis,
            selection_guard=_failed_guard,
        )

    assert current.read_bytes() == b"old-current"
    history = versions.get_versions("videos", "E1S01")
    assert history["current_version"] == old_version
    assert len(history["versions"]) == 2
    assert (project / history["versions"][-1]["file"]).read_bytes() == b"paid-before-project-read-failed"
