from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifestError,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.speech_artifact_provenance import build_video_duration_basis
from lib.version_manager import VersionManager
from lib.video_artifact_commit import commit_paid_video_artifact
from lib.video_artifact_facts import VideoArtifactCurrencyFacts


def _descriptor(label: str) -> ArtifactBasisDescriptor:
    return ArtifactBasisDescriptor.from_basis(
        ArtifactBasis.build("artifact-components/video", kind_version=1, inputs={"label": label})
    )


def _currency(label: str, *, parent_version: int, episode: int = 1) -> VideoArtifactCurrencyFacts:
    visual = ArtifactBasis.build(
        "artifact-visual/video-storyboard",
        kind_version=1,
        inputs={
            "resource_id": label,
            "visual_prompt": {"action": label, "camera_motion": "Static"},
            "canvas": {"aspect_ratio": "9:16"},
            "frames": [{"role": "storyboard", "sha256": "a" * 64}],
        },
    )
    speech = ArtifactBasis.build("artifact-speech/video", kind_version=1, inputs={"mode": "silent"})
    duration = build_video_duration_basis(8)
    return VideoArtifactCurrencyFacts(
        episode=episode,
        request_duration_seconds=8,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=(8,),
        reference_image_limit=None,
        parent_version=parent_version,
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
    current, old_version = _seed_current(project, versions)
    staged = current.with_name(".scene_E1S01.new.mp4")
    staged.write_bytes(b"new-current")
    currency = _currency("new", parent_version=old_version)
    basis = currency.video_descriptor
    events: list[str] = []

    @contextmanager
    def _guard() -> Generator[object]:
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
        version_metadata={"artifact_video_currency": currency.to_dict()},
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
        {"artifact_video_currency": {}},
        {"artifact_video_currency": {"schema_version": 1}},
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
    frozen = _currency("submitted", parent_version=old_version)

    outcome = commit_paid_video_artifact(
        project_path=project,
        versions=versions,
        resource_type="videos",
        resource_id="E1S01",
        prompt="late",
        staged_file=staged,
        current_file=current,
        duration_seconds=8,
        version_metadata={"artifact_video_currency": frozen.to_dict()},
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
    currency = _currency("new", parent_version=old_version)
    new_basis = currency.video_descriptor
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
            version_metadata={"artifact_video_currency": currency.to_dict()},
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


def test_cross_episode_path_collision_restores_the_first_owner_and_archives_paid_history(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    versions = VersionManager(project)
    current, old_version = _seed_current(project, versions)
    first_key = ArtifactKey.episode_video(1, "E1S01")
    first_basis = _descriptor("episode-one")
    from lib.artifact_manifest import ArtifactManifest

    adapter = ProjectArtifactManifestAdapter(project)
    ArtifactManifest(adapter).register_descriptor(
        first_key,
        artifact_path="videos/scene_E1S01.mp4",
        basis=first_basis,
    )
    staged = current.with_name(".scene_E1S01.episode-two.mp4")
    staged.write_bytes(b"episode-two-paid")
    currency = _currency("episode-two", parent_version=old_version, episode=2)

    with pytest.raises(ArtifactManifestError, match=r"formal artifact path.*multiple keys"):
        commit_paid_video_artifact(
            project_path=project,
            versions=versions,
            resource_type="videos",
            resource_id="E1S01",
            prompt="episode two",
            staged_file=staged,
            current_file=current,
            duration_seconds=8,
            version_metadata={"artifact_video_currency": currency.to_dict()},
            resolve_current_basis=lambda _metadata: currency.video_descriptor,
        )

    assert current.read_bytes() == b"old-current"
    restored = adapter.get_entry(first_key)
    assert restored is not None
    assert restored.basis_digest == first_basis.digest
    assert adapter.get_entry(ArtifactKey.episode_video(2, "E1S01")) is None
    history = versions.get_versions("videos", "E1S01")
    assert history["current_version"] == old_version
    assert (project / history["versions"][-1]["file"]).read_bytes() == b"episode-two-paid"


def test_selection_guard_failure_still_archives_the_paid_video(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    versions = VersionManager(project)
    current, old_version = _seed_current(project, versions)
    staged = current.with_name(".scene_E1S01.paid.mp4")
    staged.write_bytes(b"paid-before-project-read-failed")
    currency = _currency("submitted", parent_version=old_version)
    basis = currency.video_descriptor

    @contextmanager
    def _failed_guard() -> Generator[object]:
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
            version_metadata={"artifact_video_currency": currency.to_dict()},
            resolve_current_basis=lambda _metadata: basis,
            selection_guard=_failed_guard,
        )

    assert current.read_bytes() == b"old-current"
    history = versions.get_versions("videos", "E1S01")
    assert history["current_version"] == old_version
    assert len(history["versions"]) == 2
    assert (project / history["versions"][-1]["file"]).read_bytes() == b"paid-before-project-read-failed"


def test_same_basis_late_result_cannot_replace_a_newer_user_selection(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    versions = VersionManager(project)
    current, submitted_parent = _seed_current(project, versions)
    currency = _currency("same", parent_version=submitted_parent)
    current.write_bytes(b"user-selection")
    user_version = versions.add_version("videos", "E1S01", "user", source_file=current)
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
        version_metadata={"artifact_video_currency": currency.to_dict()},
        resolve_current_basis=lambda _metadata: currency.video_descriptor,
    )

    assert outcome.selected is False
    assert current.read_bytes() == b"user-selection"
    history = versions.get_versions("videos", "E1S01")
    assert history["current_version"] == user_version
    assert (project / history["versions"][-1]["file"]).read_bytes() == b"late-paid"
