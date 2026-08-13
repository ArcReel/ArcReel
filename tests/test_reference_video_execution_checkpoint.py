"""Reference-video provider submission checkpoint and immutable media staging."""

from __future__ import annotations

import errno
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from lib.artifact_manifest import MANIFEST_FILENAME, ArtifactBasis, ArtifactBasisDescriptor
from lib.path_safety import PathTraversalError
from lib.reference_video.execution_checkpoint import (
    NarrationExecutionFacts,
    ProviderMediaInput,
    ReferenceExecutionIdentityError,
    ReferenceResumeState,
    ReferenceSubmissionCheckpoint,
    StoryboardSubmissionCheckpoint,
    VideoResumeState,
    checkpoint_version_metadata,
    classify_reference_resume_state,
    classify_video_resume_state,
    cleanup_staged_provider_media,
    load_task_reference_checkpoint,
    stage_provider_media,
)

pytestmark = pytest.mark.integration


def _stage_inputs(project_path: Path) -> tuple[ProviderMediaInput, ...]:
    image = project_path / "characters" / "Alice.png"
    audio = project_path / "characters" / "refs_audio" / "Alice.wav"
    image.parent.mkdir(parents=True)
    audio.parent.mkdir(parents=True)
    image.write_bytes(b"immutable-image")
    audio.write_bytes(b"immutable-audio")
    return (
        ProviderMediaInput(
            path=image,
            role="reference_image",
            logical_type="character",
            logical_name="Alice",
            kind="sheet",
        ),
        ProviderMediaInput(
            path=audio,
            role="reference_audio",
            logical_type="speaker",
            logical_name="Alice",
            kind="voice_reference",
            target_index=0,
        ),
    )


def _checkpoint(project_path: Path) -> ReferenceSubmissionCheckpoint:
    staged = stage_provider_media(project_path, "task-1", _stage_inputs(project_path))
    return ReferenceSubmissionCheckpoint.create(
        task_id="task-1",
        project_name="demo",
        script_file="scripts/episode_1.json",
        unit_id="E1U1",
        capability="r2v",
        provider_id="custom-7",
        provider_model_id="cinema-v1",
        backend_model_id="cinema-v1-resolved",
        endpoint_guard="dashscope-async-video",
        api_call_id=42,
        prompt="actual execution prompt",
        duration_seconds=8,
        aspect_ratio="9:16",
        resolution="1080p",
        generate_audio=True,
        service_tier="default",
        seed=None,
        visual_basis_digest="a" * 64,
        artifact_visual_basis=ArtifactBasisDescriptor.from_basis(
            ArtifactBasis.build("artifact-visual/video-reference", kind_version=1, inputs={"unit": "E1U1"})
        ),
        narration=NarrationExecutionFacts(
            delivery="use_tts",
            tts_status="current",
            artifact_path="audio/segment_E1U1.wav",
            basis_digest="b" * 64,
            actual_duration_seconds=6.25,
        ),
        media=staged,
        reference_audio_targets=(0,),
    )


def test_stage_provider_media_copies_only_declared_inputs_with_digest_and_identity(tmp_path: Path) -> None:
    project_path = tmp_path / "demo"
    inputs = _stage_inputs(project_path)

    staged = stage_provider_media(project_path, "task-1", inputs)

    assert [item.role for item in staged] == ["reference_image", "reference_audio"]
    assert [item.logical_name for item in staged] == ["Alice", "Alice"]
    assert [item.target_index for item in staged] == [None, 0]
    assert [item.size_bytes for item in staged] == [15, 15]
    assert all(len(item.sha256) == 64 for item in staged)
    assert all(item.staged_locator.startswith(".arcreel/tasks/task-1/provider_media/") for item in staged)
    assert (project_path / staged[0].staged_locator).read_bytes() == b"immutable-image"
    assert (project_path / staged[1].staged_locator).read_bytes() == b"immutable-audio"


def test_stage_provider_media_is_idempotent_but_never_replaces_published_bytes(tmp_path: Path) -> None:
    project_path = tmp_path / "demo"
    inputs = _stage_inputs(project_path)
    first = stage_provider_media(project_path, "task-1", inputs)

    assert stage_provider_media(project_path, "task-1", inputs) == first

    inputs[0].path.write_bytes(b"edited-after-staging")
    with pytest.raises(ValueError, match="immutable provider media staging"):
        stage_provider_media(project_path, "task-1", inputs)
    assert (project_path / first[0].staged_locator).read_bytes() == b"immutable-image"


def test_stage_provider_media_accepts_equivalent_concurrent_posix_publisher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lib.reference_video import execution_checkpoint

    project_path = tmp_path / "demo"
    inputs = _stage_inputs(project_path)
    real_rename = execution_checkpoint.os.rename

    def _publish_then_report_destination_exists(source: Path, destination: Path) -> None:
        real_rename(source, destination)
        raise OSError(errno.ENOTEMPTY, "concurrent directory publisher won")

    monkeypatch.setattr(execution_checkpoint.os, "rename", _publish_then_report_destination_exists)

    staged = stage_provider_media(project_path, "task-1", inputs)

    assert (project_path / staged[0].staged_locator).read_bytes() == b"immutable-image"


def test_stage_provider_media_rejects_escape_and_does_not_publish_partial_directory(tmp_path: Path) -> None:
    project_path = tmp_path / "demo"
    project_path.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"secret")
    valid = project_path / "characters" / "Alice.png"
    valid.parent.mkdir()
    valid.write_bytes(b"valid")

    with pytest.raises(PathTraversalError):
        stage_provider_media(
            project_path,
            "task-1",
            (
                ProviderMediaInput(valid, "reference_image", "character", "Alice", "sheet"),
                ProviderMediaInput(outside, "reference_image", "character", "Mallory", "sheet"),
            ),
        )

    assert not (project_path / ".arcreel" / "tasks" / "task-1" / "provider_media").exists()


@pytest.mark.parametrize("task_id", ["../escape", "/absolute", "bad/task"])
def test_stage_provider_media_rejects_unsafe_task_id(tmp_path: Path, task_id: str) -> None:
    project_path = tmp_path / "demo"
    with pytest.raises(ValueError, match="task_id"):
        stage_provider_media(project_path, task_id, ())


def test_cleanup_removes_only_task_provider_media(tmp_path: Path) -> None:
    project_path = tmp_path / "demo"
    stage_provider_media(project_path, "task-1", _stage_inputs(project_path))
    sibling = project_path / ".arcreel" / "tasks" / "task-1" / "paid-history.keep"
    sibling.write_bytes(b"keep")

    cleanup_staged_provider_media(project_path, "task-1")

    assert sibling.read_bytes() == b"keep"
    assert not (sibling.parent / "provider_media").exists()


def test_cleanup_unlinks_provider_media_symlink_without_deleting_its_target(tmp_path: Path) -> None:
    project_path = tmp_path / "demo"
    paid_history = project_path / "versions" / "videos" / "scene_E1S01"
    paid_history.mkdir(parents=True)
    paid_version = paid_history / "v1.mp4"
    paid_version.write_bytes(b"paid-history")
    task_dir = project_path / ".arcreel" / "tasks" / "task-1"
    task_dir.mkdir(parents=True)
    staging_link = task_dir / "provider_media"
    staging_link.symlink_to(paid_history, target_is_directory=True)

    cleanup_staged_provider_media(project_path, "task-1")

    assert paid_version.read_bytes() == b"paid-history"
    assert not staging_link.exists()


def test_cleanup_removes_provider_media_junction_without_using_file_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lib.reference_video import execution_checkpoint

    project_path = tmp_path / "demo"
    staging_dir = project_path / ".arcreel" / "tasks" / "task-1" / "provider_media"
    staging_dir.mkdir(parents=True)
    monkeypatch.setattr(
        execution_checkpoint.os.path,
        "isjunction",
        lambda path: Path(path) == staging_dir,
        raising=False,
    )

    cleanup_staged_provider_media(project_path, "task-1")

    assert not staging_dir.exists()


def test_cleanup_does_not_traverse_a_symlinked_task_ancestor(tmp_path: Path) -> None:
    project_path = tmp_path / "demo"
    paid_history = project_path / "versions" / "videos" / "scene_E1S01"
    provider_media = paid_history / "provider_media"
    provider_media.mkdir(parents=True)
    paid_version = provider_media / "v1.mp4"
    paid_version.write_bytes(b"paid-history")
    tasks_dir = project_path / ".arcreel" / "tasks"
    tasks_dir.mkdir(parents=True)
    task_link = tasks_dir / "task-1"
    task_link.symlink_to(paid_history, target_is_directory=True)

    cleanup_staged_provider_media(project_path, "task-1")

    assert task_link.is_symlink()
    assert paid_version.read_bytes() == b"paid-history"


def test_checkpoint_round_trip_is_versioned_strict_and_self_authenticating(tmp_path: Path) -> None:
    checkpoint = _checkpoint(tmp_path / "demo")

    restored = ReferenceSubmissionCheckpoint.from_json(checkpoint.to_json())

    assert restored == checkpoint
    assert restored.prompt_sha256 == "0a90e70b38c8a4b4675f11f2fca1da4324d41f12b15c9b6151581d41753c34b7"
    assert len(restored.request_digest) == 64
    assert restored.media[0].source_locator == "characters/Alice.png"
    assert restored.narration.actual_duration_seconds == 6.25
    assert restored.artifact_visual_basis == checkpoint.artifact_visual_basis
    assert checkpoint_version_metadata(restored)["artifact_visual_basis"] == {
        "kind": "artifact-visual/video-reference",
        "kind_version": 1,
        "digest": checkpoint.artifact_visual_basis.digest,
    }
    assert not (tmp_path / "demo" / MANIFEST_FILENAME).exists()

    raw = json.loads(checkpoint.to_json())
    raw["unexpected"] = True
    with pytest.raises(ValueError, match="unexpected checkpoint fields"):
        ReferenceSubmissionCheckpoint.from_json(json.dumps(raw))

    raw = json.loads(checkpoint.to_json())
    raw["duration_seconds"] = 12
    with pytest.raises(ValueError, match="request_digest"):
        ReferenceSubmissionCheckpoint.from_json(json.dumps(raw))

    raw = json.loads(checkpoint.to_json())
    raw["schema_version"] = True
    with pytest.raises(ValueError, match="version"):
        ReferenceSubmissionCheckpoint.from_json(json.dumps(raw))

    raw = json.loads(checkpoint.to_json())
    raw["script_file"] = "scripts//episode_1.json"
    with pytest.raises(ValueError, match="canonical"):
        ReferenceSubmissionCheckpoint.from_json(json.dumps(raw))

    for invalid in (
        {"kind": "", "kind_version": 1, "digest": "sha256-v1:" + "a" * 64},
        {"kind": "visual", "kind_version": True, "digest": "sha256-v1:" + "a" * 64},
        {"kind": "visual", "kind_version": 1, "digest": "a" * 64},
        {**checkpoint.artifact_visual_basis.to_dict(), "extra": True},
    ):
        raw = json.loads(checkpoint.to_json())
        raw["artifact_visual_basis"] = invalid
        with pytest.raises(ValueError, match="artifact basis descriptor"):
            ReferenceSubmissionCheckpoint.from_json(json.dumps(raw))


def test_request_digest_is_stable_across_local_ledger_call_ids(tmp_path: Path) -> None:
    checkpoint = _checkpoint(tmp_path / "demo")

    replay = replace(checkpoint, api_call_id=checkpoint.api_call_id + 1)

    assert replay.request_digest == checkpoint.request_digest


def test_legacy_checkpoint_remains_resumable_without_inventing_artifact_basis(tmp_path: Path) -> None:
    raw = json.loads(_checkpoint(tmp_path / "demo").to_json())
    raw["schema_version"] = 1
    raw.pop("artifact_visual_basis")
    digest_payload = {key: value for key, value in raw.items() if key not in {"api_call_id", "request_digest"}}
    raw["request_digest"] = hashlib.sha256(
        json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    restored = ReferenceSubmissionCheckpoint.from_json(json.dumps(raw))

    assert restored.schema_version == 1
    assert restored.artifact_visual_basis is None
    assert "artifact_visual_basis" not in checkpoint_version_metadata(restored)


def test_checkpoint_rejects_incoherent_narration_and_media_facts(tmp_path: Path) -> None:
    checkpoint = _checkpoint(tmp_path / "demo")

    with pytest.raises(ValueError, match="current TTS"):
        replace(checkpoint, narration=replace(checkpoint.narration, tts_status="stale"))
    with pytest.raises(ValueError, match="post-production"):
        replace(
            checkpoint,
            narration=replace(checkpoint.narration, delivery="post_production"),
        )
    with pytest.raises(ValueError, match="target_index"):
        replace(
            checkpoint,
            media=(checkpoint.media[0], replace(checkpoint.media[1], target_index=99)),
            reference_audio_targets=(99,),
        )


def test_checkpoint_rejects_noncanonical_staged_locator_and_wrong_identity(tmp_path: Path) -> None:
    checkpoint = _checkpoint(tmp_path / "demo")
    with pytest.raises(ValueError, match="staged_locator"):
        replace(checkpoint.media[0], staged_locator="../outside.png")

    wrong_task = replace(
        checkpoint.media[0],
        staged_locator=".arcreel/tasks/task-2/provider_media/000-reference_image.png",
    )
    with pytest.raises(ValueError, match="staged_locator"):
        ReferenceSubmissionCheckpoint.create(
            task_id=checkpoint.task_id,
            project_name=checkpoint.project_name,
            script_file=checkpoint.script_file,
            unit_id=checkpoint.unit_id,
            capability=checkpoint.capability,
            provider_id=checkpoint.provider_id,
            provider_model_id=checkpoint.provider_model_id,
            backend_model_id=checkpoint.backend_model_id,
            endpoint_guard=checkpoint.endpoint_guard,
            api_call_id=checkpoint.api_call_id,
            prompt=checkpoint.prompt,
            duration_seconds=checkpoint.duration_seconds,
            aspect_ratio=checkpoint.aspect_ratio,
            resolution=checkpoint.resolution,
            generate_audio=checkpoint.generate_audio,
            service_tier=checkpoint.service_tier,
            seed=checkpoint.seed,
            visual_basis_digest=checkpoint.visual_basis_digest,
            artifact_visual_basis=checkpoint.artifact_visual_basis,
            narration=checkpoint.narration,
            media=(wrong_task, *checkpoint.media[1:]),
            reference_audio_targets=checkpoint.reference_audio_targets,
        )

    with pytest.raises(ReferenceExecutionIdentityError, match="script_file"):
        load_task_reference_checkpoint(
            {
                "task_id": checkpoint.task_id,
                "project_name": checkpoint.project_name,
                "resource_id": checkpoint.unit_id,
                "script_file": "scripts/other.json",
                "execution_checkpoint_json": checkpoint.to_json(),
            }
        )


def test_reference_resume_state_classifies_all_checkpoint_job_combinations(tmp_path: Path) -> None:
    checkpoint = _checkpoint(tmp_path / "demo")
    base = {
        "task_id": checkpoint.task_id,
        "project_name": checkpoint.project_name,
        "resource_id": checkpoint.unit_id,
        "script_file": checkpoint.script_file,
    }

    assert classify_reference_resume_state({**base, "provider_job_id": None}) == (
        ReferenceResumeState.NO_CHECKPOINT_NO_JOB,
        None,
    )
    assert (
        classify_reference_resume_state(
            {**base, "provider_job_id": None, "execution_checkpoint_json": checkpoint.to_json()}
        )[0]
        is ReferenceResumeState.CHECKPOINT_WITHOUT_JOB
    )
    state, restored = classify_reference_resume_state(
        {**base, "provider_job_id": "job-1", "execution_checkpoint_json": checkpoint.to_json()}
    )
    assert state is ReferenceResumeState.READY
    assert restored == checkpoint
    assert classify_reference_resume_state({**base, "provider_job_id": "job-1"})[0] is (
        ReferenceResumeState.IDENTITY_UNRECOVERABLE
    )
    assert (
        classify_reference_resume_state({**base, "provider_job_id": "job-1", "execution_checkpoint_json": "{broken"})[0]
        is ReferenceResumeState.IDENTITY_UNRECOVERABLE
    )


def test_storyboard_checkpoint_round_trip_and_four_resume_states(tmp_path: Path) -> None:
    project_path = tmp_path / "demo"
    frame = project_path / "storyboards" / "scene_E1S01.png"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"immutable-storyboard")
    staged = stage_provider_media(
        project_path,
        "task-storyboard",
        (
            ProviderMediaInput(
                path=frame,
                role="start_image",
                logical_type="storyboard",
                logical_name="E1S01",
                kind="first_frame",
            ),
        ),
    )
    checkpoint = StoryboardSubmissionCheckpoint.create(
        task_id="task-storyboard",
        project_name="demo",
        script_file="episode_1.json",
        unit_id="E1S01",
        capability="i2v",
        provider_id="openai",
        provider_model_id="sora-2",
        backend_model_id="sora-2",
        endpoint_guard=None,
        api_call_id=7,
        prompt="current script prompt",
        duration_seconds=8,
        aspect_ratio="9:16",
        resolution="720p",
        generate_audio=True,
        service_tier="default",
        seed=123,
        visual_basis_digest="c" * 64,
        artifact_visual_basis=ArtifactBasisDescriptor.from_basis(
            ArtifactBasis.build("artifact-visual/video-storyboard", kind_version=1, inputs={"unit": "E1S01"})
        ),
        narration=NarrationExecutionFacts(
            delivery="post_production",
            tts_status="not_applicable",
            artifact_path="",
            basis_digest=None,
            actual_duration_seconds=None,
        ),
        media=staged,
        reference_audio_targets=None,
    )
    restored = StoryboardSubmissionCheckpoint.from_json(checkpoint.to_json())
    assert restored == checkpoint
    assert restored.media[0].role == "start_image"

    base = {
        "task_id": checkpoint.task_id,
        "task_type": "video",
        "project_name": checkpoint.project_name,
        "resource_id": checkpoint.unit_id,
        "script_file": checkpoint.script_file,
    }
    assert classify_video_resume_state({**base, "provider_job_id": None})[0] is VideoResumeState.NO_CHECKPOINT_NO_JOB
    assert (
        classify_video_resume_state(
            {**base, "provider_job_id": None, "execution_checkpoint_json": checkpoint.to_json()}
        )[0]
        is VideoResumeState.CHECKPOINT_WITHOUT_JOB
    )
    state, parsed = classify_video_resume_state(
        {**base, "provider_job_id": "job-7", "execution_checkpoint_json": checkpoint.to_json()}
    )
    assert state is VideoResumeState.READY
    assert parsed == checkpoint
    assert (
        classify_video_resume_state({**base, "provider_job_id": "job-7"})[0] is VideoResumeState.IDENTITY_UNRECOVERABLE
    )
