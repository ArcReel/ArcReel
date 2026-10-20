"""视频与音频产物登记时记下正式文件自身的内容事实：内容指纹、字节数与纳秒 mtime。"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactContentFacts,
    ArtifactKey,
    ArtifactManifest,
    ArtifactManifestError,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.version_manager import VersionManager
from lib.artifacts.video_artifact_commit import commit_paid_video_artifact
from lib.project.project_manager import ProjectManager
from lib.speech.narration_delivery import register_narration_audio_transactionally
from server.services.project.project_archive import ProjectArchiveService
from tests.integration.lib.artifacts.test_video_artifact_commit import _currency, _seed_current
from tests.integration.server.routers.test_shot_uploads_currency import _client, _upload
from tests.unit.lib.speech.test_narration_delivery import _narrator_preparation, _settings

Registered = tuple[Path, ArtifactKey, str]


def _paid_video(tmp_path: Path, _monkeypatch: pytest.MonkeyPatch) -> Registered:
    project = tmp_path / "project"
    project.mkdir()
    versions = VersionManager(project)
    current, old_version = _seed_current(project, versions)
    staged = current.with_name(".scene_E1S01.new.mp4")
    staged.write_bytes(b"new-paid-video")
    currency = _currency("new", parent_version=old_version)
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
        resolve_current_basis=lambda _metadata: currency.video_descriptor,
    )
    assert outcome.selected is True
    return project, ArtifactKey.episode_video(1, "E1S01"), "videos/scene_E1S01.mp4"


def _paid_audio(tmp_path: Path, _monkeypatch: pytest.MonkeyPatch) -> Registered:
    artifact = tmp_path / "audio" / "segment_E1U1.wav"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"paid-narration-audio")
    register_narration_audio_transactionally(
        project_path=tmp_path,
        episode=1,
        preparation=_narrator_preparation("E1U1"),
        settings=_settings(),
    )
    return tmp_path, ArtifactKey.episode_audio(1, "E1U1"), "audio/segment_E1U1.wav"


def _upload_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectManager:
    client, pm, _project_dir = _client(monkeypatch, tmp_path)
    with client:
        _upload(client, "video", "clip.mp4", b"\x00" * 512)
    return pm


def _uploaded_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Registered:
    pm = _upload_video(tmp_path, monkeypatch)
    return pm.get_project_path("demo"), ArtifactKey.episode_video(1, "E1S01"), "videos/scene_E1S01.mp4"


def _imported_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Registered:
    pm = _upload_video(tmp_path, monkeypatch)
    project_dir = pm.get_project_path("demo")
    key, artifact_path = ArtifactKey.episode_video(1, "E1S01"), "videos/scene_E1S01.mp4"
    service = ProjectArchiveService(pm)
    archive_path, _ = service.export_project("demo", scope="current")
    shutil.rmtree(project_dir)
    service.import_project_archive(archive_path, uploaded_filename="demo.zip")
    return pm.get_project_path("demo"), key, artifact_path


def _facts_on_disk(path: Path) -> ArtifactContentFacts:
    file_stat = path.stat()
    return ArtifactContentFacts(
        digest=hashlib.sha256(path.read_bytes()).hexdigest(),
        size=file_stat.st_size,
        mtime_ns=file_stat.st_mtime_ns,
    )


@pytest.mark.parametrize(
    "register",
    [_paid_video, _paid_audio, _uploaded_video, _imported_video],
    ids=["paid-video", "paid-audio", "upload-video", "import-video"],
)
def test_registered_video_and_audio_entries_record_the_formal_file_content_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    register: Callable[[Path, pytest.MonkeyPatch], Registered],
) -> None:
    project, key, artifact_path = register(tmp_path, monkeypatch)

    entry = ProjectArtifactManifestAdapter(project).get_entry(key)

    assert entry is not None
    assert entry.content == _facts_on_disk(project / artifact_path)


def _register_video(project: Path, key: ArtifactKey, artifact_path: str, content: bytes) -> None:
    artifact = project / artifact_path
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(content)
    ArtifactManifest(ProjectArtifactManifestAdapter(project)).register_descriptor(
        key,
        artifact_path=artifact_path,
        basis=ArtifactBasisDescriptor.from_basis(
            ArtifactBasis.build("artifact-components/video", kind_version=1, inputs={"label": "v"})
        ),
    )


def test_rekey_keeps_the_content_facts_of_the_moved_claim(tmp_path: Path) -> None:
    source = ArtifactKey.episode_video(1, "E1S01")
    target = ArtifactKey.episode_video(2, "E1S01")
    _register_video(tmp_path, source, "videos/scene_E1S01.mp4", b"video-bytes")
    adapter = ProjectArtifactManifestAdapter(tmp_path)
    before = adapter.get_entry(source)
    assert before is not None
    assert before.content is not None
    (tmp_path / "videos" / "scene_E1S01.mp4").rename(tmp_path / "videos" / "scene_E2S01.mp4")

    ArtifactManifest(adapter).plan_entry_rekey(
        source,
        target,
        artifact_path_rewrites={"videos/scene_E1S01.mp4": "videos/scene_E2S01.mp4"},
    ).commit()

    moved = adapter.get_entry(target)
    assert moved is not None
    assert moved.artifact_path == "videos/scene_E2S01.mp4"
    assert moved.content == before.content


def test_entries_with_and_without_content_facts_round_trip_through_the_manifest(tmp_path: Path) -> None:
    video = ArtifactKey.episode_video(1, "E1S01")
    storyboard = ArtifactKey.episode_storyboard(1, "E1S01")
    _register_video(tmp_path, video, "videos/scene_E1S01.mp4", b"video-bytes")
    (tmp_path / "storyboards").mkdir()
    (tmp_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
    ArtifactManifest(ProjectArtifactManifestAdapter(tmp_path)).register_descriptor(
        storyboard,
        artifact_path="storyboards/scene_E1S01.png",
        basis=ArtifactBasisDescriptor.from_basis(
            ArtifactBasis.build("artifact-components/storyboard", kind_version=1, inputs={"label": "s"})
        ),
    )

    entries = ProjectArtifactManifestAdapter(tmp_path).snapshot_entries()

    assert entries[video].content == _facts_on_disk(tmp_path / "videos" / "scene_E1S01.mp4")
    assert entries[storyboard].content is None


@pytest.mark.parametrize(
    "facts",
    [
        {"content_digest": "a" * 64},
        {"content_digest": "A" * 64, "content_size": 1, "content_mtime_ns": 1},
        {"content_digest": "a" * 64, "content_size": True, "content_mtime_ns": 1},
        {"content_digest": "a" * 64, "content_size": -1, "content_mtime_ns": 1},
        {"content_digest": "a" * 64, "content_size": 1, "content_mtime_ns": 1.5},
    ],
    ids=["partial", "uppercase-digest", "bool-size", "negative-size", "float-mtime"],
)
def test_manifest_rejects_malformed_content_facts(tmp_path: Path, facts: dict[str, object]) -> None:
    key = ArtifactKey.episode_video(1, "E1S01")
    _register_video(tmp_path, key, "videos/scene_E1S01.mp4", b"video-bytes")
    manifest_path = tmp_path / MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = payload["entries"][key.encode()]
    for field in ("content_digest", "content_size", "content_mtime_ns"):
        entry.pop(field)
    entry.update(facts)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ArtifactManifestError):
        ProjectArtifactManifestAdapter(tmp_path).get_entry(key)
