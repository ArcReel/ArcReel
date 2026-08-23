from __future__ import annotations

from pathlib import Path

import pytest

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifest,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.project_manager import ProjectManager
from lib.version_manager import VersionManager
from server.services.artifact_version_restore import get_typed_media_restore_target, restore_typed_media_version


def _descriptor(seed: str, *, kind: str = "narration-delivery/tts-audio") -> ArtifactBasisDescriptor:
    inputs = (
        {
            "text": seed,
            "provider_id": "dashscope",
            "model_id": "qwen3-tts-flash",
            "voice": "Cherry",
            "speed": None,
        }
        if kind == "narration-delivery/tts-audio"
        else {"seed": seed}
    )
    return ArtifactBasisDescriptor.from_basis(ArtifactBasis.build(kind, kind_version=1, inputs=inputs))


def _project(tmp_path: Path) -> tuple[ProjectManager, Path]:
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "title": "E1",
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "novel_text": "旁白",
                    "generated_assets": {
                        "narration_audio": "audio/segment_E1S01.wav",
                        "status": "pending",
                    },
                }
            ],
        },
        "episode_1.json",
        validate=False,
    )
    return pm, pm.get_project_path("demo")


def _add_audio_version(
    vm: VersionManager,
    current: Path,
    *,
    content: bytes,
    basis: ArtifactBasisDescriptor | None,
) -> int:
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_bytes(content)
    metadata = (
        {
            "artifact_episode": 1,
            "artifact_audio_basis": basis.to_dict(),
            "execution_script_file": "episode_1.json",
            "tts_actual_duration_seconds": 5.0,
            "tts_provider_id": "dashscope",
            "tts_model_id": "qwen3-tts-flash",
            "tts_voice": "Cherry",
            "tts_speed": None,
            "tts_basis_digest": basis.digest,
        }
        if basis is not None
        else {}
    )
    return vm.add_version("audio", "E1S01", content.decode(), source_file=current, **metadata)


def test_typed_audio_restore_selects_media_script_pointer_and_manifest_together(tmp_path):
    pm, project_path = _project(tmp_path)
    vm = VersionManager(project_path)
    current = project_path / "audio" / "segment_E1S01.wav"
    old_basis = _descriptor("old")
    current_basis = _descriptor("current")
    old_version = _add_audio_version(vm, current, content=b"old", basis=old_basis)
    _add_audio_version(vm, current, content=b"current", basis=current_basis)
    adapter = ProjectArtifactManifestAdapter(project_path)
    ArtifactManifest(adapter).register_descriptor(
        ArtifactKey.episode_audio(1, "E1S01"),
        artifact_path="audio/segment_E1S01.wav",
        basis=current_basis,
    )

    result = restore_typed_media_version(
        project_manager=pm,
        project_name="demo",
        project_path=project_path,
        versions=vm,
        resource_type="audio",
        resource_id="E1S01",
        version=old_version,
        current_file=current,
        artifact_path="audio/segment_E1S01.wav",
    )

    assert result["restored_version"] == old_version
    assert current.read_bytes() == b"old"
    assert vm.get_current_version("audio", "E1S01") == old_version
    assets = pm.load_script("demo", "episode_1.json")["segments"][0]["generated_assets"]
    assert assets["narration_audio"] == "audio/segment_E1S01.wav"
    assert adapter.get_entry(ArtifactKey.episode_audio(1, "E1S01")) == ArtifactManifestEntry(
        artifact_path="audio/segment_E1S01.wav",
        basis_digest=old_basis.digest,
    )


def test_typed_audio_restore_accepts_legacy_project_without_episode_index(tmp_path):
    pm, project_path = _project(tmp_path)
    vm = VersionManager(project_path)
    current = project_path / "audio" / "segment_E1S01.wav"
    old_basis = _descriptor("old")
    old_version = _add_audio_version(vm, current, content=b"old", basis=old_basis)
    _add_audio_version(vm, current, content=b"current", basis=_descriptor("current"))
    pm.update_project("demo", lambda project: project.pop("episodes", None))

    result = restore_typed_media_version(
        project_manager=pm,
        project_name="demo",
        project_path=project_path,
        versions=vm,
        resource_type="audio",
        resource_id="E1S01",
        version=old_version,
        current_file=current,
        artifact_path="audio/segment_E1S01.wav",
    )

    assert result["restored_version"] == old_version
    assert current.read_bytes() == b"old"


def test_restore_registration_failure_rolls_back_media_pointer_and_script(tmp_path, monkeypatch):
    pm, project_path = _project(tmp_path)
    vm = VersionManager(project_path)
    current = project_path / "audio" / "segment_E1S01.wav"
    old_basis = _descriptor("old")
    current_basis = _descriptor("current")
    old_version = _add_audio_version(vm, current, content=b"old", basis=old_basis)
    current_version = _add_audio_version(vm, current, content=b"current", basis=current_basis)
    adapter = ProjectArtifactManifestAdapter(project_path)
    ArtifactManifest(adapter).register_descriptor(
        ArtifactKey.episode_audio(1, "E1S01"),
        artifact_path="audio/segment_E1S01.wav",
        basis=current_basis,
    )
    before_script = (project_path / "scripts" / "episode_1.json").read_bytes()

    def _fail(*args, **kwargs):
        raise RuntimeError("injected manifest failure")

    monkeypatch.setattr(ArtifactManifest, "register_descriptor_transactionally", _fail)

    with pytest.raises(RuntimeError, match="injected manifest failure"):
        restore_typed_media_version(
            project_manager=pm,
            project_name="demo",
            project_path=project_path,
            versions=vm,
            resource_type="audio",
            resource_id="E1S01",
            version=old_version,
            current_file=current,
            artifact_path="audio/segment_E1S01.wav",
        )

    assert current.read_bytes() == b"current"
    assert vm.get_current_version("audio", "E1S01") == current_version
    assert (project_path / "scripts" / "episode_1.json").read_bytes() == before_script
    assert adapter.get_entry(ArtifactKey.episode_audio(1, "E1S01")).basis_digest == current_basis.digest


def test_legacy_audio_restore_without_typed_basis_is_rejected_without_mutation(tmp_path):
    pm, project_path = _project(tmp_path)
    vm = VersionManager(project_path)
    current = project_path / "audio" / "segment_E1S01.wav"
    legacy_version = _add_audio_version(vm, current, content=b"legacy", basis=None)
    current_basis = _descriptor("current")
    current_version = _add_audio_version(vm, current, content=b"current", basis=current_basis)

    with pytest.raises(ValueError, match="typed artifact metadata"):
        restore_typed_media_version(
            project_manager=pm,
            project_name="demo",
            project_path=project_path,
            versions=vm,
            resource_type="audio",
            resource_id="E1S01",
            version=legacy_version,
            current_file=current,
            artifact_path="audio/segment_E1S01.wav",
        )

    assert current.read_bytes() == b"current"
    assert vm.get_current_version("audio", "E1S01") == current_version


def test_audio_restore_rejects_kind_only_descriptor_that_metadata_cannot_verify(tmp_path):
    pm, project_path = _project(tmp_path)
    vm = VersionManager(project_path)
    current = project_path / "audio" / "segment_E1S01.wav"
    unverifiable = _descriptor("different prompt")
    invalid_version = _add_audio_version(vm, current, content=b"old", basis=unverifiable)
    current_version = _add_audio_version(vm, current, content=b"current", basis=_descriptor("current"))

    with pytest.raises(ValueError, match="typed artifact metadata"):
        restore_typed_media_version(
            project_manager=pm,
            project_name="demo",
            project_path=project_path,
            versions=vm,
            resource_type="audio",
            resource_id="E1S01",
            version=invalid_version,
            current_file=current,
            artifact_path="audio/segment_E1S01.wav",
        )

    assert current.read_bytes() == b"current"
    assert vm.get_current_version("audio", "E1S01") == current_version


def test_video_restore_rejects_composite_kind_without_complete_v3_components(tmp_path):
    project_path = tmp_path / "demo"
    current = project_path / "videos" / "scene_E1S01.mp4"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"video")
    vm = VersionManager(project_path)
    kind_only = _descriptor("video", kind="artifact-components/video")
    version = vm.add_version(
        "videos",
        "E1S01",
        "video",
        source_file=current,
        artifact_episode=1,
        execution_script_file="episode_1.json",
        artifact_video_basis=kind_only.to_dict(),
    )

    with pytest.raises(ValueError, match="typed artifact metadata"):
        get_typed_media_restore_target(
            vm,
            resource_type="videos",
            resource_id="E1S01",
            version=version,
        )
