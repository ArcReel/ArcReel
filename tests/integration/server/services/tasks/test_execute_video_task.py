"""Tests for execute_video_task."""

import asyncio
import json
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from arcreel_market_core.video_backend_contract import VideoCapabilities, VideoCapabilityError
from lib.artifacts.artifact_manifest import (
    ArtifactKey,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
)
from lib.backends.video_frame_slots import gate_video_request
from lib.generation.generation_queue import DispatchProviderChanged
from lib.generation.video_request_facts import (
    DEFAULT_PLANNED_DURATION_SECONDS,
    VideoRequestFactsError,
    VideoRequestFactsFailure,
)
from lib.speech.narration_delivery import (
    POST_PRODUCTION,
    USE_TTS,
    TtsSynthesisSettings,
    build_narration_audio_basis,
)
from lib.speech.speech_composition import admit_script_unit
from server.services.tasks import generation_tasks
from tests.factories import make_video_request_facts, wav_bytes
from tests.fakes import use_generation_context, use_project_manager, use_video_thumbnail_extractor, write_backend_output
from tests.integration.server.services.tasks.generation_tasks_support import (
    FakeGenerator,
    _FakePM,
    ad_pm,
    async_return,
    fake_resolve_ctx,
    persist_active_fake_project,
    prepare_files,
    register_stale_visual_claim,
    seed_current_storyboard,
)


class TestGenerationTasks:
    async def test_execute_video_task_rechecks_audio_switch_from_request_facts(self, monkeypatch, tmp_path):
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()
        facts = make_video_request_facts(
            provider_id="dashscope",
            model_id="wan2.7-i2v",
            requested_generate_audio=False,
            generate_audio=False,
            has_audio_track=True,
            audio_switch_controllable=False,
        )
        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator, video_request_facts=facts))

        with pytest.raises(ValueError, match="video_audio_switch_not_supported") as exc:
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static"}},
            )

        assert exc.value.code == "video_audio_switch_not_supported"
        assert exc.value.params == {"provider": "dashscope", "model": "wan2.7-i2v"}
        assert fake_generator.video_calls == []

    async def test_execute_video_task_generates_thumbnail(self, monkeypatch, tmp_path):
        """Automatically extract first-frame thumbnails after video generation."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()

        thumbnail_path = project_path / "thumbnails" / "scene_E1S01.jpg"

        async def fake_extract(video_path, out_path):
            # Production ffmpeg subprocesses write thumbnails; substitutes write in worker threads without blocking the event loop.
            await write_backend_output(out_path, b"thumb")
            return out_path

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))
        use_video_thumbnail_extractor(monkeypatch, fake_extract)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert result["resource_type"] == "videos"
        # Verify update_scene_asset includes video_thumbnail.
        asset_types = [call["asset_type"] for call in fake_pm.updated_assets]
        assert "video_thumbnail" in asset_types
        assert thumbnail_path.exists()

    async def test_execute_video_task_carries_backend_warnings_into_the_result(self, monkeypatch, tmp_path):
        """Backend execution notices reach task result for users; operations logs alone are insufficient."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        warning = {"key": "comfyui_multiple_outputs", "params": {"count": 2, "filename": "final.mp4"}}

        class _WarningGenerator(FakeGenerator):
            async def generate_video_async(self, **kwargs):
                kwargs["warnings"].append(warning)
                return await super().generate_video_async(**kwargs)

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(_WarningGenerator()))
        use_video_thumbnail_extractor(monkeypatch, AsyncMock(return_value=False))

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert result["warnings"] == [warning]

    async def test_finalize_video_completes_when_cancelled_during_script_write(self, monkeypatch, tmp_path):
        """If cancellation arrives during video-accounting script writeback, finish writing/finalizing and return the result; do not forward cancellation to selection compensation."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        writing = threading.Event()
        release = threading.Event()
        original_update = fake_pm.update_scene_asset

        def _blocking_update(**kwargs):
            if kwargs["asset_type"] == "video_clip":
                writing.set()
                release.wait(5)
            return original_update(**kwargs)

        monkeypatch.setattr(fake_pm, "update_scene_asset", _blocking_update)
        use_project_manager(monkeypatch, fake_pm)
        use_video_thumbnail_extractor(monkeypatch, AsyncMock(return_value=False))
        task = asyncio.create_task(
            generation_tasks.finalize_video_task(
                project_name="demo",
                script_file="episode_1.json",
                project_path=project_path,
                resource_id="E1S01",
                version=2,
                video_uri="uri",
                generator=FakeGenerator(),
            )
        )
        try:
            assert await asyncio.to_thread(writing.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
        finally:
            release.set()
        result = await task

        assert result["version"] == 2
        assert [update["asset_type"] for update in fake_pm.updated_assets] == ["video_clip", "video_uri"]

    async def test_storyboard_worker_materializes_current_request_and_checkpoints_staged_frames(
        self,
        monkeypatch,
        tmp_path,
    ):
        from lib.script.reference_video.execution_checkpoint import StoryboardSubmissionCheckpoint

        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        item = fake_pm.script["segments"][0]
        item["novel_text"] = "旁白正文"
        current_prompt = {"action": "current action", "camera_motion": "Static", "dialogue": []}
        item["video_prompt"] = current_prompt
        item["duration_seconds"] = 8
        manifest = project_path / ".arcreel_artifacts.json"
        manifest_before = manifest.read_bytes()
        submitted: dict[str, Mapping[str, object]] = {}

        class _CheckpointingGenerator(FakeGenerator):
            async def generate_video_async(self, **kwargs):
                self.video_calls.append(kwargs)
                start_image = kwargs["start_image"]
                assert isinstance(start_image, Path)
                assert ".arcreel/tasks/task-storyboard/provider_media/" in start_image.as_posix()
                assert await asyncio.to_thread(start_image.read_bytes) == b"png"
                metadata = await kwargs["before_submit"]()
                assert metadata is not None
                submitted["metadata"] = metadata
                from lib.artifacts.version_manager import PaidVersionCommit

                kwargs["commit_formal_output"].outcome = PaidVersionCommit(version=2, selected=True)
                return project_path / "videos" / "scene_E1S01.mp4", 2, "ref", "uri"

        fake_generator = _CheckpointingGenerator()
        fake_queue = type("Queue", (), {})()
        fake_queue.persist_execution_checkpoint = AsyncMock()
        use_project_manager(monkeypatch, fake_pm)
        monkeypatch.setattr(generation_tasks, "get_generation_queue", lambda: fake_queue)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator, supported_durations=(4, 8, 12)))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "stale.json",
                "prompt": {"action": "stale enqueue prompt"},
                "duration_seconds": 4,
                "video_provider_i2v": "stale/model",
            },
            script_file="episode_1.json",
            task_id="task-storyboard",
        )

        raw = fake_queue.persist_execution_checkpoint.await_args.args[1]
        checkpoint = StoryboardSubmissionCheckpoint.from_json(raw)
        call = fake_generator.video_calls[0]
        assert checkpoint.script_file == "episode_1.json"
        assert checkpoint.prompt == call["prompt"]
        assert "current action" in checkpoint.prompt
        assert "stale enqueue prompt" not in checkpoint.prompt
        assert checkpoint.duration_seconds == 8
        assert checkpoint.provider_id == "ark"
        assert [media.role for media in checkpoint.media] == ["start_image"]
        assert checkpoint.artifact_visual_basis is not None
        assert checkpoint.artifact_visual_basis.kind == "artifact-visual/video-storyboard"
        assert checkpoint.artifact_currency is not None
        assert submitted["metadata"]["artifact_video_currency"] == checkpoint.artifact_currency.to_dict()
        assert call["formal_output"] is True
        assert submitted["metadata"]["execution_request_digest"] == checkpoint.request_digest
        assert manifest.read_bytes() == manifest_before
        assert not (project_path / ".arcreel" / "tasks" / "task-storyboard" / "provider_media").exists()

    async def test_execute_video_task_lane_bucket_follows_project_route(self, monkeypatch, tmp_path):
        """Evaluate lane buckets by project generation mode, matching submission entry points."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()
        seen_lanes: list[dict] = []

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator, seen_lane_requests=seen_lanes))

        payload = {
            "script_file": "episode_1.json",
            "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
        }
        fake_pm.project["generation_mode"] = "storyboard"
        await generation_tasks.execute_video_task("demo", "E1S01", payload)
        assert seen_lanes[-1]["video"].generation_type == "i2v"

        fake_pm.project["generation_mode"] = "reference_video"
        await generation_tasks.execute_video_task("demo", "E1S01", payload)
        assert seen_lanes[-1]["video"].generation_type == "r2v"

    async def test_execute_video_task_rejects_unsupported_duration(self, monkeypatch, tmp_path):
        """After resolving ProviderModel, execution rejects out-of-range duration with an explicit error."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        with pytest.raises(VideoCapabilityError) as exc:
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                    "duration_seconds": 5,
                },
            )
        assert exc.value.code == "video_duration_not_supported"
        # Reject out-of-range duration at startup without backend generation.
        assert fake_generator.video_calls == []

    async def test_execute_video_task_rejects_duration_removed_by_current_resolution(self, monkeypatch, tmp_path):
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()
        facts = make_video_request_facts(
            resolution="1080p",
            supported_durations=(4, 6, 8),
            allowed_durations=(8,),
            excluded_durations=((6, "resolution"),),
        )
        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator, video_request_facts=facts))

        with pytest.raises(VideoCapabilityError) as exc:
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                    "duration_seconds": 6,
                },
            )
        assert exc.value.code == "video_duration_not_supported"
        assert fake_generator.video_calls == []

    async def test_execute_video_task_supported_duration_passes(self, monkeypatch, tmp_path):
        """Valid durations pass guards and enter backend generation."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                "duration_seconds": 8,
            },
        )
        assert result["resource_type"] == "videos"
        assert fake_generator.video_calls[0]["duration_seconds"] == 8

    async def test_execute_video_task_refuses_a_script_outside_the_episode_ledger(self, monkeypatch, tmp_path):
        """Script identity comes only from project.json episodes ledger; reject all unbound files without guessing episode numbers."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        (project_path / "scripts" / "unbound_script.json").write_text(
            json.dumps(fake_pm.script, ensure_ascii=False),
            encoding="utf-8",
        )
        fake_generator = FakeGenerator()

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        with pytest.raises(ValueError, match="is not bound to episode"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "unbound_script.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                    "duration_seconds": 8,
                },
            )

        assert fake_generator.video_calls == []

    @pytest.mark.parametrize("narration_delivery", [USE_TTS, POST_PRODUCTION])
    async def test_execute_video_task_requests_planned_duration_regardless_of_narration_delivery(
        self, monkeypatch, tmp_path, narration_delivery
    ):
        """Request planned script duration with video lane only; neither narration delivery nor longer existing narration changes the request."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["narration_delivery"] = narration_delivery
        seed_current_storyboard(fake_pm)
        item = fake_pm.script["segments"][0]
        item["duration_seconds"] = 4
        item["novel_text"] = "旁白。"
        item["video_prompt"] = {}
        item["generated_assets"]["narration_audio"] = "audio/segment_E1S01.wav"
        audio = project_path / "audio" / "segment_E1S01.wav"
        audio.parent.mkdir()
        audio.write_bytes(wav_bytes(7.5))
        preparation = admit_script_unit("segments", item).preparation
        ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
            ArtifactKey.episode_audio(1, "E1S01"),
            artifact_path="audio/segment_E1S01.wav",
            basis=build_narration_audio_basis(
                preparation, TtsSynthesisSettings("dashscope", "actual-tts", "Cherry", 1.1)
            ),
        )
        fake_generator = FakeGenerator()
        seen_lane_requests: list[dict] = []
        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(
            monkeypatch,
            fake_resolve_ctx(fake_generator, supported_durations=(4, 8, 12), seen_lane_requests=seen_lane_requests),
        )
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["duration_seconds"] == 4
        assert [request["audio"] for request in seen_lane_requests] == [None]

    async def test_execute_video_task_blocks_on_unresolvable_request_facts(self, monkeypatch, tmp_path):
        """If queued configuration changes narrow tiers to empty, block before provider submission using the original problem code; never revert to the unrestricted set."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()
        failure = VideoRequestFactsFailure(
            "video_supported_durations_incompatible",
            (("provider", "ark"), ("model", "seedance"), ("resolution", "1080p"), ("capability", "i2v")),
        )
        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator, video_request_facts=failure))

        with pytest.raises(DispatchProviderChanged):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static"},
                },
                claimed_provider_id="other",
            )

        with pytest.raises(VideoRequestFactsError) as exc:
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )

        assert exc.value.code == "video_supported_durations_incompatible"
        assert exc.value.params["resolution"] == "1080p"
        assert fake_generator.video_calls == []

    async def test_execute_video_task_storyboard_image_grid_filename_resolves(self, monkeypatch, tmp_path):
        """For grid-project storyboard_image paths such as scene_{id}_first.png (noncanonical names), accept manifest registration within storyboards/. Unlike end_frame_image, do not require exact canonical filenames."""
        project_path = prepare_files(tmp_path)
        (project_path / "storyboards" / "scene_E1S01_first.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01_first.png"}
        register_stale_visual_claim(
            project_path,
            ArtifactKey.episode_storyboard(1, "E1S01"),
            "storyboards/scene_E1S01_first.png",
        )

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["start_image"] == project_path / "storyboards" / "scene_E1S01_first.png"

    async def test_execute_video_task_missing_storyboard_image_fails_hard(self, monkeypatch, tmp_path):
        """Hard-fail missing storyboard_image files without backend generation."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_missing.png"}

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="storyboard not found"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    async def test_execute_video_task_schema8_requires_registered_storyboard(self, monkeypatch, tmp_path):
        """Schema 8 workers reject an existing storyboard whose Manifest claim is absent."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        persist_active_fake_project(fake_pm)

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="storyboard is not registered"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    async def test_execute_video_task_rechecks_storyboard_claim_after_staging_before_provider(
        self,
        monkeypatch,
        tmp_path,
    ):
        """A restore that drops the selected claim cannot race a paid video submission."""

        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        item = fake_pm.script["segments"][0]
        item["novel_text"] = "旁白正文"
        item["video_prompt"] = {"action": "跑", "camera_motion": "Static", "dialogue": []}
        item["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        persist_active_fake_project(fake_pm)
        key = ArtifactKey.episode_storyboard(1, "E1S01")
        register_stale_visual_claim(project_path, key, "storyboards/scene_E1S01.png")

        provider_submissions: list[str] = []

        class _SubmittingGenerator(FakeGenerator):
            async def generate_video_async(self, **kwargs):
                await kwargs["before_submit"]()
                provider_submissions.append("submitted")
                raise AssertionError("provider submission must remain unreachable")

        fake_generator = _SubmittingGenerator()
        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        original_stage = generation_tasks.stage_provider_media_for_task

        async def _drop_claim_after_staging(project_dir, task_id, inputs):
            staged = await original_stage(project_dir, task_id, inputs)
            # Simulate concurrent claim revocation in a worker thread, not on the event loop.
            await asyncio.to_thread(lambda: ProjectArtifactManifestAdapter(project_path).delete_entry(key))
            return staged

        monkeypatch.setattr(generation_tasks, "stage_provider_media_for_task", _drop_claim_after_staging)
        fake_queue = type("Queue", (), {})()
        fake_queue.persist_execution_checkpoint = AsyncMock()
        monkeypatch.setattr(generation_tasks, "get_generation_queue", lambda: fake_queue)

        with pytest.raises(ValueError, match="no longer registered"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json"},
                task_id="task-storyboard-claim-race",
            )

        assert provider_submissions == []
        fake_queue.persist_execution_checkpoint.assert_not_awaited()
        assert not (project_path / ".arcreel" / "tasks" / "task-storyboard-claim-race" / "provider_media").exists()

    async def test_video_rejects_storyboard_replaced_after_staging_before_provider(
        self,
        monkeypatch,
        tmp_path,
    ):
        from lib.artifacts.artifact_activation import activate_artifact_target_state

        project_path = prepare_files(tmp_path)
        fake_pm = ad_pm(project_path, with_sheet=False)
        item = fake_pm.script["shots"][0]
        item["video_prompt"] = {"action": "跑", "camera_motion": "Static", "dialogue": []}
        item["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        fake_pm.project.update(
            {
                "generation_mode": "storyboard",
                "aspect_ratio": "9:16",
                "target_duration": 30,
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
            }
        )
        fake_pm.script["episode"] = 1
        (project_path / "project.json").write_text(
            json.dumps(fake_pm.project, ensure_ascii=False),
            encoding="utf-8",
        )
        fake_pm.load_script("demo", "episode_1.json")
        assert activate_artifact_target_state(project_path, bump_schema=False) is True
        provider_submissions: list[str] = []

        class _SubmittingGenerator(FakeGenerator):
            async def generate_video_async(self, **kwargs):
                await kwargs["before_submit"]()
                provider_submissions.append("submitted")
                raise AssertionError("provider submission must remain unreachable")

        fake_generator = _SubmittingGenerator()
        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))
        original_stage = generation_tasks.stage_provider_media_for_task

        def _replace_and_reactivate() -> None:
            (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"replacement")
            register_stale_visual_claim(
                project_path,
                ArtifactKey.episode_storyboard(1, "E1S01"),
                "storyboards/scene_E1S01.png",
            )
            assert ProjectArtifactManifestAdapter(project_path).get_entry(ArtifactKey.episode_script(1)) is not None
            assert (
                ProjectArtifactManifestAdapter(project_path).get_entry(ArtifactKey.episode_storyboard(1, "E1S01"))
                is not None
            )

        async def _stage_then_replace_and_reactivate(project_dir, task_id, inputs):
            staged = await original_stage(project_dir, task_id, inputs)
            # Simulate concurrent storyboard replacement and reclaim in a worker thread, not on the event loop.
            await asyncio.to_thread(_replace_and_reactivate)
            return staged

        monkeypatch.setattr(generation_tasks, "stage_provider_media_for_task", _stage_then_replace_and_reactivate)
        fake_queue = type("Queue", (), {})()
        fake_queue.persist_execution_checkpoint = AsyncMock()
        monkeypatch.setattr(generation_tasks, "get_generation_queue", lambda: fake_queue)

        with pytest.raises(ValueError, match="changed while it was selected"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json"},
                task_id="task-legacy-storyboard-race",
            )

        assert provider_submissions == []
        fake_queue.persist_execution_checkpoint.assert_not_awaited()
        assert not (project_path / ".arcreel" / "tasks" / "task-legacy-storyboard-race" / "provider_media").exists()

    async def test_execute_video_task_generated_assets_non_dict_is_refused(self, monkeypatch, tmp_path):
        """If externally damaged generated_assets is nondict (such as list), reject as missing registered pointer: never guess same-name files or leak AttributeError."""
        from lib.script.storyboard_sequence import StoryboardImageBindingRequired

        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = ["bad"]

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        with pytest.raises(StoryboardImageBindingRequired, match="storyboard binding missing"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )

        assert fake_generator.video_calls == []

    @pytest.mark.parametrize(
        ("storyboard_value", "expected_message"),
        [
            # Out-of-bound paths and malformed data: all invalid paths.
            (
                "/etc/passwd",
                "invalid storyboard image path",
            ),  # Absolute path: bare `/` joining discards the entire left operand.
            ("../../outside.png", "invalid storyboard image path"),  # `..` traversal escapes the project directory.
            (
                123,
                "invalid storyboard image path",
            ),  # Nonstring script JSON values produce readable failures rather than TypeError.
            (
                0,
                "invalid storyboard image path",
            ),  # Falsy malformed values must not silently become unset and fall back to default paths.
            (False, "invalid storyboard image path"),  # Same as above.
            ([], "invalid storyboard image path"),  # Same as above.
            ({}, "invalid storyboard image path"),  # Same as above.
            # Directory ownership: paths inside the project but outside storyboards/ need distinct wording from traversal, to locate externally edited scripts.
            ("storyboards/../end_frames/scene_E1S01.png", "must stay under storyboards/"),
            ("end_frames/scene_E1S01.png", "must stay under storyboards/"),
        ],
    )
    async def test_execute_video_task_storyboard_image_outside_dir_fails_hard(
        self, monkeypatch, tmp_path, storyboard_value, expected_message
    ):
        """On-disk script JSON storyboard_image is untrusted: hard-fail traversal, paths outside storyboards and malformed data; never upload arbitrary server files to video providers."""
        project_path = prepare_files(tmp_path)
        (tmp_path / "outside.png").write_bytes(b"png")
        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": storyboard_value}

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match=re.escape(expected_message)):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    async def test_execute_video_task_storyboard_image_absolute_path_inside_project_fails_hard(
        self, monkeypatch, tmp_path
    ):
        """Hard-fail even absolute storyboard_image paths inside project storyboards/: `os.path.join` discards project root on absolute input, so checking final containment alone is insufficient. Explicitly reject absolute paths."""
        project_path = prepare_files(tmp_path)
        (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator()
        absolute_value = str(project_path / "storyboards" / "scene_E1S01.png")
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": absolute_value}

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="invalid storyboard image path"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    async def test_execute_video_task_storyboard_image_rooted_no_drive_path_fails_hard(self, monkeypatch, tmp_path):
        """Hard-fail drive-less rooted storyboard_image such as `\\Users\\...`: `Path.is_absolute()` returns False on native Windows, but `os.path.join` still discards project root except its drive. Normalize separators and separately reject leading root separators."""
        project_path = prepare_files(tmp_path)
        (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "\\Users\\Alice\\scene.png"}

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="invalid storyboard image path"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    async def test_execute_video_task_storyboard_image_pointing_to_directory_fails_hard(self, monkeypatch, tmp_path):
        """Hard-fail existing directories in project storyboards/ rather than files: `exists()` accepts directories too; require files explicitly instead of sending them as start_image and failing later with unreadable encoding errors."""
        project_path = prepare_files(tmp_path)
        (project_path / "storyboards" / "subdir").mkdir(parents=True)
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/subdir"}

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="storyboard not found"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    async def test_execute_video_task_end_frame_image_passed_to_generator(self, monkeypatch, tmp_path):
        """When end_frame_image is set, video requests include end_image from the shot's persisted field and fixed project-relative path."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()

        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["end_image"] == end_frame_dir / "scene_E1S01.png"

    async def test_execute_video_task_end_frame_image_bare_filename_resolves_via_default_dir(
        self, monkeypatch, tmp_path
    ):
        """For bare end-frame filenames without `end_frames/`, match data_validator._resolve_existing_path default_dir fallback. Otherwise import-valid values (validator retries with a directory) would fail generation without reason."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()

        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm.script["segments"][0]["end_frame_image"] = "scene_E1S01.png"

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["end_image"] == end_frame_dir / "scene_E1S01.png"

    @pytest.mark.parametrize("missing", [True, False], ids=["field-absent", "empty-string"])
    async def test_execute_video_task_without_end_frame_image_passes_none(self, monkeypatch, tmp_path, missing):
        """Unconfigured end frames behave unchanged: absent fields or explicit empty strings both produce end_image=None."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()
        if not missing:
            fake_pm.script["segments"][0]["end_frame_image"] = ""

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["end_image"] is None

    async def test_execute_video_task_missing_end_frame_snapshot_fails_hard(self, monkeypatch, tmp_path):
        """Hard-fail missing end-frame snapshots without backend generation."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="end frame snapshot not found"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    @pytest.mark.parametrize(
        "end_frame_value",
        [
            "/etc/passwd",  # Absolute path: bare `/` joining discards the left operand and reads outside the project.
            "../../outside.png",  # `..` traversal escapes the project directory.
            "end_frames/../storyboards/scene_E1S01.png",  # Inside project but outside snapshot directory: equivalent to referencing the source image directly.
            "storyboards/scene_E1S01.png",  # Same restriction: field accepts only snapshots under end_frames/.
            123,  # Nonstring script JSON values produce readable failures rather than TypeError.
            0,  # Falsy malformed values must not silently become unset and skip the end frame.
            False,  # Same as above.
            [],  # Same as above.
            {},  # Same as above.
            "end_frames/scene_E1S02.png",  # Existing file within snapshot directory but owned by another shot: cross-shot misreference.
        ],
    )
    async def test_execute_video_task_end_frame_path_outside_snapshot_dir_fails_hard(
        self, monkeypatch, tmp_path, end_frame_value
    ):
        """Untrusted on-disk end-frame JSON hard-fails traversal, paths outside end_frames and malformed values, never sending arbitrary server files. Match end_frame.py writes and data_validator validation."""
        project_path = prepare_files(tmp_path)
        (tmp_path / "outside.png").write_bytes(b"png")
        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S02.png").write_bytes(
            b"png"
        )  # Another shot's snapshot for cross-shot misreference checks.
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = end_frame_value

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="invalid end frame snapshot path"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    async def test_execute_video_task_end_frame_canonical_path_symlink_fails_hard(self, monkeypatch, tmp_path):
        """Even a canonical relative end-frame path must reject a file replaced by a symlink to another shot's snapshot. try_safe_join / safe_join resolve both sides to the same real target; equality alone cannot detect correct path strings with swapped disk objects."""
        project_path = prepare_files(tmp_path)
        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S02.png").write_bytes(b"png")
        (end_frame_dir / "scene_E1S01.png").symlink_to(end_frame_dir / "scene_E1S02.png")
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="invalid end frame snapshot path"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    async def test_execute_video_task_end_frame_parent_dir_symlink_fails_hard(self, monkeypatch, tmp_path):
        """Symlink swaps can target parents too: if `end_frames/` points to another project-internal directory, filename and resolved paths still match. Check every canonical component, including parents, not only the final filename."""
        project_path = prepare_files(tmp_path)
        real_dir = project_path / "storyboards_end_frames_swap"
        real_dir.mkdir(parents=True, exist_ok=True)
        (real_dir / "scene_E1S01.png").write_bytes(b"png")
        (project_path / "end_frames").symlink_to(real_dir)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="invalid end frame snapshot path"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    async def test_execute_video_task_end_frame_parent_dir_junction_fails_hard(self, monkeypatch, tmp_path):
        """Native Windows junctions are reparse points distinct from symlinks, invisible to `Path.is_symlink()`. Separately block junctioned `end_frames/` using `is_junction()`. Other platforms monkeypatch `Path.is_junction()` to verify every component calls it, not only symlink checks."""
        project_path = prepare_files(tmp_path)
        real_dir = project_path / "storyboards_end_frames_swap"
        real_dir.mkdir(parents=True, exist_ok=True)
        (real_dir / "scene_E1S01.png").write_bytes(b"png")
        end_frames_dir = project_path / "end_frames"
        end_frames_dir.mkdir(parents=True, exist_ok=True)
        (end_frames_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(Path, "is_junction", lambda self: self == end_frames_dir)

        with pytest.raises(ValueError, match="invalid end frame snapshot path"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    async def test_execute_video_task_end_frame_capability_unsupported_propagates(self, monkeypatch, tmp_path):
        """Hard-fail backends lacking end-frame support without degrading to reference images or dropping frames.

        Replace provider calls only; production gate_video_request evaluates actual capabilities (caps last_frame=False). Otherwise a substitute raising by its own rules would test itself rather than wiring to the gate. Capability combinations are covered separately in tests/test_video_frame_slots.py."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()

        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        async def _plan_with_real_gating(**kwargs):
            fake_generator.video_calls.append(kwargs)
            gate_video_request(
                caps=VideoCapabilities(first_frame=True, last_frame=False),
                provider="ark",
                model="seedance",
                end_image=kwargs.get("end_image"),
            )
            raise AssertionError("gate_video_request 应在 end_image 非空且 caps.last_frame 为假时硬失败")

        fake_generator.generate_video_async = _plan_with_real_gating

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        with pytest.raises(VideoCapabilityError) as exc:
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert exc.value.code == "video_last_frame_unsupported"

    async def test_execute_video_task_reuses_end_frame_on_regeneration(self, monkeypatch, tmp_path):
        """Video regeneration automatically retains end frames: this is a persistent shot property reloaded from script on every execution."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()

        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        for _ in range(2):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )

        assert len(fake_generator.video_calls) == 2
        for call in fake_generator.video_calls:
            assert call["end_image"] == end_frame_dir / "scene_E1S01.png"

    async def test_execute_video_task_drama_dialogue_from_utterances(self, monkeypatch, tmp_path):
        """Drama lip-sync dialogue comes from shot-level dialogue-kind utterances, covering payloads lacking video_prompt.dialogue; voiceover-kind stays out of video YAML."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator()

        # Use drama script: E1S01 has ordered utterances, voiceover before dialogue.
        fake_pm.script = {
            "episode": 1,
            "content_mode": "drama",
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "duration_seconds": 8,
                    "segment_break": False,
                    "characters_in_scene": ["王"],
                    "scenes": [],
                    "props": [],
                    "image_prompt": "首镜头",
                    "video_prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                    "utterances": [
                        {"kind": "voiceover", "speaker": None, "text": "那是命运的开端。"},
                        {"kind": "dialogue", "speaker": "王", "text": "你来了。"},
                    ],
                }
            ],
        }
        seed_current_storyboard(fake_pm)

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        # Payload video_prompt lacks dialogue (new drama structure).
        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                "duration_seconds": 8,
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        # Dialogue-kind speech and speaker enter YAML; voiceover-kind never enters video prompts.
        assert "你来了。" in prompt_yaml
        assert "王" in prompt_yaml
        assert "那是命运的开端。" not in prompt_yaml

    def _drama_script(self):
        return {
            "episode": 1,
            "content_mode": "drama",
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "duration_seconds": 8,
                    "segment_break": False,
                    "characters_in_scene": ["王"],
                    "scenes": [],
                    "props": [],
                    "image_prompt": "首镜头",
                    "video_prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                    "utterances": [
                        {"kind": "dialogue", "speaker": "王", "text": "你来了。"},
                    ],
                }
            ],
        }

    def _legacy_drama_script(self):
        """Legacy pre-utterances drama scripts have no scene utterances; dialogue remains in video_prompt.dialogue."""
        script = self._drama_script()
        del script["scenes"][0]["utterances"]
        return script

    async def test_execute_video_task_injects_voice_profiles_for_audible_model(self, monkeypatch, tmp_path):
        """For audio-track models (voice_consistency != none), mechanically derive nonempty voice_style for dialogue speakers' character assets into top-level Voice_Profiles."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = FakeGenerator()
        fake_pm.script = self._drama_script()
        seed_current_storyboard(fake_pm)

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator, voice_consistency="soft"))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                "duration_seconds": 8,
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        assert "Voice_Profiles" in prompt_yaml
        assert "低沉沙哑" in prompt_yaml
        # Central declaration section must precede Action.
        assert prompt_yaml.index("Voice_Profiles") < prompt_yaml.index("Action")

    async def test_execute_video_task_skips_voice_profiles_for_silent_model(self, monkeypatch, tmp_path):
        """Class C truly silent models (voice_consistency == none) never inject Voice_Profiles."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = FakeGenerator()
        fake_pm.script = self._drama_script()
        seed_current_storyboard(fake_pm)

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator, voice_consistency="none"))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                "duration_seconds": 8,
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        assert "Voice_Profiles" not in prompt_yaml
        assert "低沉沙哑" not in prompt_yaml
        # Do not omit dialogue with voice styles: silent outputs still send speech text for provider lip-sync reference.
        assert "你来了。" in prompt_yaml

    async def test_execute_video_task_skips_voice_profiles_when_episode_audio_disabled(self, monkeypatch, tmp_path):
        """With requested_generate_audio=False, omit Voice_Profiles even for models with audio tracks, matching class C silence."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = FakeGenerator()
        fake_pm.script = self._drama_script()
        seed_current_storyboard(fake_pm)

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(
            monkeypatch, fake_resolve_ctx(fake_generator, voice_consistency="soft", requested_generate_audio=False)
        )
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                "duration_seconds": 8,
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        assert "Voice_Profiles" not in prompt_yaml
        assert "低沉沙哑" not in prompt_yaml
        # Dialogue stays verbatim, matching class C truly silent behavior.
        assert "你来了。" in prompt_yaml

    async def test_execute_video_task_strips_caller_supplied_voice_profiles_for_non_drama(self, monkeypatch, tmp_path):
        """Always strip caller-supplied voice_profiles in narration/ad (items lack utterances). Only mechanical build_drama_video_prompt derivation may produce this declaration; callers cannot bypass class C silence gates."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {
                    "action": "起身",
                    "camera_motion": "Static",
                    "ambiance_audio": "风声",
                    "voice_profiles": [{"Speaker": "赝品", "Voice_Style": "越权"}],
                },
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        assert "Voice_Profiles" not in prompt_yaml
        assert "越权" not in prompt_yaml

    async def test_execute_video_task_injects_voice_profiles_from_legacy_dialogue(self, monkeypatch, tmp_path):
        """For legacy drama with no utterances and dialogue in video_prompt.dialogue, load_script reads raw JSON without Pydantic migration. Derive Voice_Profiles through the legacy outlet rather than silently losing them."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = FakeGenerator()
        fake_pm.script = self._legacy_drama_script()
        seed_current_storyboard(fake_pm)

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator, voice_consistency="soft"))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {
                    "action": "起身",
                    "camera_motion": "Static",
                    "ambiance_audio": "风声",
                    "dialogue": [{"speaker": "王", "line": "你来了。"}],
                },
                "duration_seconds": 8,
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        assert "Voice_Profiles" in prompt_yaml
        assert "低沉沙哑" in prompt_yaml
        assert "你来了。" in prompt_yaml

    async def test_execute_video_task_skips_voice_profiles_from_legacy_dialogue_when_silent(
        self, monkeypatch, tmp_path
    ):
        """Legacy dialogue also passes silence gates: episode audio disabled omits Voice_Profiles while retaining dialogue text."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = FakeGenerator()
        fake_pm.script = self._legacy_drama_script()
        seed_current_storyboard(fake_pm)

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(
            monkeypatch, fake_resolve_ctx(fake_generator, voice_consistency="soft", requested_generate_audio=False)
        )
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {
                    "action": "起身",
                    "camera_motion": "Static",
                    "ambiance_audio": "风声",
                    "dialogue": [{"speaker": "王", "line": "你来了。"}],
                },
                "duration_seconds": 8,
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        assert "Voice_Profiles" not in prompt_yaml
        assert "低沉沙哑" not in prompt_yaml
        assert "你来了。" in prompt_yaml

    async def test_execute_video_task_content_mode_falls_back_to_project_when_episode_omits_it(
        self, monkeypatch, tmp_path
    ):
        """Legacy episode scripts without top-level content_mode fall back to project.json (matching existing data_validator acceptance), avoiding false narration classification and silently skipped dialogue reconstruction/Voice_Profiles."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["content_mode"] = "drama"
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = FakeGenerator()
        fake_pm.script = {
            "episode": 1,
            # Missing top-level content_mode in legacy episodes falls back to project.json.
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "duration_seconds": 8,
                    "segment_break": False,
                    "characters_in_scene": ["王"],
                    "scenes": [],
                    "props": [],
                    "image_prompt": "首镜头",
                    "video_prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                    "utterances": [
                        {"kind": "dialogue", "speaker": "王", "text": "你来了。"},
                    ],
                }
            ],
        }
        seed_current_storyboard(fake_pm)

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator, voice_consistency="soft"))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                "duration_seconds": 8,
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        assert "Voice_Profiles" in prompt_yaml
        assert "低沉沙哑" in prompt_yaml
        assert "你来了。" in prompt_yaml

    async def test_execute_video_task_default_duration_from_caps(self, monkeypatch, tmp_path):
        """Without explicit duration, capabilities choose supported_durations[0], guaranteed valid."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator, supported_durations=(6, 10)))
        use_video_thumbnail_extractor(monkeypatch, async_return(None))
        # Clear project default duration too, forcing capability defaults.
        fake_pm.project.pop("default_duration", None)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )
        assert result["resource_type"] == "videos"
        assert fake_generator.video_calls[0]["duration_seconds"] == 6

    async def test_execute_video_task_default_duration_on_endpoint_fixed_uses_planning_basis(
        self, monkeypatch, tmp_path
    ):
        """For endpoint-fixed durations with neither unit nor project duration specified, request shared default planning seconds."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(
            monkeypatch, fake_resolve_ctx(fake_generator, supported_durations=(), duration_endpoint_fixed=True)
        )
        use_video_thumbnail_extractor(monkeypatch, async_return(None))
        fake_pm.project.pop("default_duration", None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["duration_seconds"] == DEFAULT_PLANNED_DURATION_SECONDS

    async def test_execute_video_task_default_duration_respects_resolution_constraint(self, monkeypatch, tmp_path):
        """Auto without explicit duration uses constrained-resolution durations, not the first supported_durations entry.

        Veo + 4k accepts only eight seconds; choosing first-tier four seconds would always fail backend execution under default settings."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator()

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(
            monkeypatch,
            fake_resolve_ctx(
                fake_generator,
                video_provider=("gemini-aistudio", "veo-3.1-generate-preview"),
                video_resolution="4k",
                supported_durations=(4, 6, 8),
                video_request_facts=make_video_request_facts(
                    provider_id="gemini-aistudio",
                    model_id="veo-3.1-generate-preview",
                    resolution="4k",
                    allowed_durations=(8,),
                ),
            ),
        )
        use_video_thumbnail_extractor(monkeypatch, async_return(None))
        fake_pm.project.pop("default_duration", None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )
        assert fake_generator.video_calls[0]["duration_seconds"] == 8

    async def test_video_resolve_failure_fails_task_without_fallback(self, monkeypatch, tmp_path):
        """Video resolution failure fails the task: propagate original exceptions for diagnosis, without hardcoded provider/model fallback or backend calls."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator()

        async def _boom(*args, **kwargs):
            raise RuntimeError("video provider unconfigured")

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, _boom)

        with pytest.raises(RuntimeError, match="video provider unconfigured"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []
