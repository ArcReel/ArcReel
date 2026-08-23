"""Tests for execute_video_task (split from test_generation_tasks_service.py)."""

import copy
import json
import re
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lib.artifact_manifest import (
    ArtifactKey,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
)
from lib.narration_delivery import (
    USE_TTS,
    NarratedVideoDurationBlockedError,
    NarrationDeliveryPreparation,
    NarrationTtsStatus,
    TtsSynthesisSettings,
    prepare_narrated_video_duration,
)
from lib.video_backends.base import VideoCapabilities, VideoCapabilityError
from lib.video_frame_slots import gate_video_request
from lib.video_visual_provenance import build_storyboard_video_visual_basis
from server.services import generation_tasks
from tests.integration.server.services.conftest import (
    _ad_pm,
    _async_return,
    _fake_resolve_ctx,
    _FakeGenerator,
    _FakePM,
    _persist_active_fake_project,
    _prepare_files,
    _register_asset_sheet_claims,
    _register_stale_visual_claim,
    _seed_current_storyboard,
)


class TestGenerationTasks:
    async def test_execute_video_task_generates_thumbnail(self, monkeypatch, tmp_path):
        """视频生成后应自动提取首帧缩略图"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()

        thumbnail_path = project_path / "thumbnails" / "scene_E1S01.jpg"

        async def fake_extract(video_path, out_path):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"thumb")
            return out_path

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", fake_extract)
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert result["resource_type"] == "videos"
        # 验证 update_scene_asset 被调用，其中包含 video_thumbnail
        asset_types = [call["asset_type"] for call in fake_pm.updated_assets]
        assert "video_thumbnail" in asset_types
        assert thumbnail_path.exists()

    async def test_storyboard_worker_materializes_current_request_and_checkpoints_staged_frames(
        self,
        monkeypatch,
        tmp_path,
    ):
        from lib.reference_video.execution_checkpoint import StoryboardSubmissionCheckpoint

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        item = fake_pm.script["segments"][0]
        item["novel_text"] = "旁白正文"
        current_prompt = {"action": "current action", "camera_motion": "Static", "dialogue": []}
        item["video_prompt"] = current_prompt
        item["duration_seconds"] = 8
        manifest = project_path / ".arcreel_artifacts.json"
        manifest_before = manifest.read_bytes()
        submitted: dict[str, Mapping[str, object]] = {}

        class _CheckpointingGenerator(_FakeGenerator):
            async def generate_video_async(self, **kwargs):
                self.video_calls.append(kwargs)
                start_image = kwargs["start_image"]
                assert isinstance(start_image, Path)
                assert ".arcreel/tasks/task-storyboard/provider_media/" in start_image.as_posix()
                assert start_image.read_bytes() == b"png"
                metadata = await kwargs["before_submit"](41)
                assert metadata is not None
                submitted["metadata"] = metadata
                from lib.version_manager import PaidVersionCommit

                kwargs["commit_formal_output"].outcome = PaidVersionCommit(version=2, selected=True)
                return project_path / "videos" / "scene_E1S01.mp4", 2, "ref", "uri"

        fake_generator = _CheckpointingGenerator()
        fake_queue = type("Queue", (), {})()
        fake_queue.persist_execution_checkpoint = AsyncMock()
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "get_generation_queue", lambda: fake_queue)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(fake_generator, supported_durations=(4, 8, 12)),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))

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
        """lane 归桶按项目生成模式求值，与提交入口使用同一口径。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()
        seen_lanes: list[dict] = []

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(fake_generator, seen_lane_requests=seen_lanes),
        )
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        payload = {
            "script_file": "episode_1.json",
            "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
        }
        fake_pm.project["generation_mode"] = "storyboard"
        await generation_tasks.execute_video_task("demo", "E1S01", payload)
        assert seen_lanes[-1]["video"].capability == "i2v"

        fake_pm.project["generation_mode"] = "reference_video"
        await generation_tasks.execute_video_task("demo", "E1S01", payload)
        assert seen_lanes[-1]["video"].capability == "r2v"

    async def test_execute_video_task_rejects_unsupported_duration(self, monkeypatch, tmp_path):
        """执行层在解析出 ProviderModel 后，对越界 duration 以明确错误拒绝。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

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
        # 越界 duration 在起跑时被拒，绝不应调用后端生成。
        assert fake_generator.video_calls == []

    async def test_execute_video_task_supported_duration_passes(self, monkeypatch, tmp_path):
        """合法 duration 通过守卫，正常进入后端生成。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

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
        """剧本身份只认 project.json 的 episodes 账本：未绑定的剧本文件一律拒绝，不猜集号。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        (project_path / "scripts" / "unbound_script.json").write_text(
            json.dumps(fake_pm.script, ensure_ascii=False),
            encoding="utf-8",
        )
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

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

    async def test_execute_video_task_reprojects_current_tts_and_rejects_changed_tier(self, monkeypatch, tmp_path):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()
        current_tts_duration = 6.2
        seen_lane_requests: list[dict] = []

        async def fake_prepare_current_narrated_video_duration(**kwargs):
            tts_settings = await kwargs["resolver"].resolve_tts_synthesis_settings(kwargs["project"])
            assert tts_settings == TtsSynthesisSettings("dashscope", "actual-tts", "Cherry", 1.1)
            narration = NarrationDeliveryPreparation(
                delivery=USE_TTS,
                unit_id="E1S01",
                speech_mode=None,
                tts_status=NarrationTtsStatus.CURRENT,
                artifact_path="audio/segment_E1S01.wav",
                basis_digest="current-basis",
                actual_duration_seconds=current_tts_duration,
                problems=(),
            )
            return prepare_narrated_video_duration(
                narration=narration,
                planned_duration_seconds=kwargs["planned_duration_seconds"],
                supported_durations=kwargs["supported_durations"],
                confirmed_request_duration_seconds=kwargs["confirmed_request_duration_seconds"],
            )

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(
                fake_generator,
                supported_durations=(4, 8, 12),
                seen_lane_requests=seen_lane_requests,
            ),
        )
        monkeypatch.setattr(
            generation_tasks,
            "prepare_current_narrated_video_duration",
            fake_prepare_current_narrated_video_duration,
        )
        monkeypatch.setattr(generation_tasks, "tts_task_in_progress", AsyncMock(return_value=False))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)
        payload = {
            "script_file": "episode_1.json",
            "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
            "narration_delivery_options": {
                "narration_delivery": USE_TTS,
                "confirmed_request_duration_seconds": 8,
            },
        }

        await generation_tasks.execute_video_task("demo", "E1S01", payload)
        assert fake_generator.video_calls[0]["duration_seconds"] == 8
        assert len(seen_lane_requests) == 1
        assert seen_lane_requests[0]["video"] is not None
        assert seen_lane_requests[0]["audio"] is not None

        # Worker 必须从执行时最新 unit 重新取规划时长；队列不保存预检派生档位。
        fake_pm.script["segments"][0]["duration_seconds"] = 8
        current_tts_duration = 9.5
        with pytest.raises(NarratedVideoDurationBlockedError) as exc:
            await generation_tasks.execute_video_task("demo", "E1S01", payload)

        assert exc.value.preparation.problem_payloads()[0]["code"] == "reference_duration_confirmation_required"
        assert exc.value.preparation.request_duration_seconds == 12
        assert len(fake_generator.video_calls) == 1
        assert len(seen_lane_requests) == 2

    async def test_execute_video_task_reuses_selected_visual_in_the_latest_tts_tier_without_side_effects(
        self,
        monkeypatch,
        tmp_path,
    ):
        from lib.artifact_manifest import (
            ArtifactKey,
            ProjectArtifactManifestAdapter,
            compose_video_artifact_basis,
        )
        from lib.speech_artifact_provenance import build_video_duration_basis, build_video_speech_basis
        from lib.speech_composition import admit_script_unit
        from lib.version_manager import VersionManager
        from lib.video_artifact_facts import VideoArtifactCurrencyFacts
        from lib.visual_artifact_provenance import build_storyboard_video_artifact_visual_basis

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()
        fake_generator.versions = VersionManager(project_path)
        item = fake_pm.script["segments"][0]
        item["novel_text"] = "current narration"
        item["generated_assets"] = {
            "status": "completed",
            "storyboard_image": "storyboards/scene_E1S01.png",
            "video_clip": "videos/scene_E1S01.mp4",
            "video_uri": "provider://existing",
        }
        current = project_path / "videos" / "scene_E1S01.mp4"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"existing-paid-video")
        visual_prompt = {"action": "跑", "camera_motion": "Static", "dialogue": []}
        item["video_prompt"] = visual_prompt
        visual_basis = build_storyboard_video_visual_basis(
            prompt=visual_prompt,
            storyboard_image=project_path / "storyboards" / "scene_E1S01.png",
            end_frame_image=None,
            aspect_ratio="9:16",
            provider_id="ark",
            model_id="seedance",
            resolution="720p",
            seed=None,
            requested_generate_audio=True,
            content_mode="narration",
            utterances=None,
            has_utterances=False,
            voice_characters=None,
        )
        artifact_visual_basis = build_storyboard_video_artifact_visual_basis(
            resource_id="E1S01",
            visual_prompt=visual_prompt,
            storyboard_image=project_path / "storyboards" / "scene_E1S01.png",
            end_frame_image=None,
            aspect_ratio="9:16",
        )
        artifact_speech_basis = build_video_speech_basis(admit_script_unit("segments", item).preparation)
        artifact_duration_basis = build_video_duration_basis(8)
        artifact_currency = VideoArtifactCurrencyFacts(
            episode=1,
            request_duration_seconds=8,
            visual_basis=artifact_visual_basis,
            speech_basis=artifact_speech_basis,
            duration_basis=artifact_duration_basis,
            video_basis=compose_video_artifact_basis(
                visual=artifact_visual_basis,
                speech=artifact_speech_basis,
                duration=artifact_duration_basis,
            ),
            voice_style_speakers=(),
            duration_tiers=(4, 8, 12),
            reference_image_limit=None,
            parent_version=0,
        )
        selected_version = fake_generator.versions.add_version(
            "videos",
            "E1S01",
            "old visual",
            source_file=current,
            duration_seconds=8,
            visual_basis_digest=visual_basis.digest,
            execution_checkpoint_schema_version=3,
            execution_script_file="episode_1.json",
            execution_duration_seconds=8,
            execution_request_digest="d" * 64,
            execution_provider_media=[],
            artifact_video_currency=artifact_currency.to_dict(),
        )
        ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register_descriptor(
            ArtifactKey.episode_video(1, "E1S01"),
            artifact_path="videos/scene_E1S01.mp4",
            basis=artifact_currency.video_descriptor,
        )
        script_before = copy.deepcopy(fake_pm.script)
        history_before = copy.deepcopy(fake_generator.versions.get_versions("videos", "E1S01"))

        async def _prepare(**kwargs):
            narration = NarrationDeliveryPreparation(
                delivery=USE_TTS,
                unit_id="E1S01",
                speech_mode=None,
                tts_status=NarrationTtsStatus.CURRENT,
                artifact_path="audio/segment_E1S01.wav",
                basis_digest="sha256-v1:" + "c" * 64,
                actual_duration_seconds=6.2,
                problems=(),
            )
            return prepare_narrated_video_duration(
                narration=narration,
                planned_duration_seconds=kwargs["planned_duration_seconds"],
                supported_durations=kwargs["supported_durations"],
                confirmed_request_duration_seconds=kwargs["confirmed_request_duration_seconds"],
            )

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(
                fake_generator,
                video_provider=("ark", "configured-seedance"),
                video_backend_model="seedance",
                supported_durations=(4, 8, 12),
            ),
        )
        monkeypatch.setattr(generation_tasks, "prepare_current_narrated_video_duration", _prepare)
        monkeypatch.setattr(generation_tasks, "tts_task_in_progress", AsyncMock(return_value=False))
        monkeypatch.setattr(
            "server.services.narration_delivery_tasks.probe_existing_media_duration_seconds",
            AsyncMock(return_value=8.0),
        )
        fake_queue = type("Queue", (), {})()
        fake_queue.persist_execution_checkpoint = AsyncMock()
        monkeypatch.setattr(generation_tasks, "get_generation_queue", lambda: fake_queue)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": visual_prompt,
                "narration_delivery_options": {
                    "narration_delivery": USE_TTS,
                    "confirmed_request_duration_seconds": 8,
                },
            },
            task_id="task-reuse",
        )

        assert result["reused_existing"] is True
        assert result["version"] == selected_version
        assert result["request_duration_seconds"] == 8
        assert fake_generator.video_calls == []
        fake_queue.persist_execution_checkpoint.assert_not_awaited()
        assert not (project_path / ".arcreel" / "tasks" / "task-reuse" / "provider_media").exists()
        assert fake_pm.script == script_before
        assert fake_generator.versions.get_versions("videos", "E1S01") == history_before
        assert current.read_bytes() == b"existing-paid-video"

    async def test_execute_video_task_storyboard_image_grid_filename_resolves(self, monkeypatch, tmp_path):
        """宫格项目 storyboard_image 指向 scene_{id}_first.png（非 canonical 文件名），只要登记在
        产物清单里且落在 storyboards/ 目录内就正常解析——与 end_frame_image 不同，这里不要求
        文件名与 canonical 路径逐一比对。"""
        project_path = _prepare_files(tmp_path)
        (project_path / "storyboards" / "scene_E1S01_first.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01_first.png"}
        _register_stale_visual_claim(
            project_path,
            ArtifactKey.episode_storyboard(1, "E1S01"),
            "storyboards/scene_E1S01_first.png",
        )

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["start_image"] == project_path / "storyboards" / "scene_E1S01_first.png"

    async def test_execute_video_task_missing_storyboard_image_fails_hard(self, monkeypatch, tmp_path):
        """storyboard_image 字段指向的文件缺失时硬失败，不调用后端生成。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_missing.png"}

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

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
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        _persist_active_fake_project(fake_pm)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

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

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        item = fake_pm.script["segments"][0]
        item["novel_text"] = "旁白正文"
        item["video_prompt"] = {"action": "跑", "camera_motion": "Static", "dialogue": []}
        item["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        _persist_active_fake_project(fake_pm)
        key = ArtifactKey.episode_storyboard(1, "E1S01")
        _register_stale_visual_claim(project_path, key, "storyboards/scene_E1S01.png")

        provider_submissions: list[str] = []

        class _SubmittingGenerator(_FakeGenerator):
            async def generate_video_async(self, **kwargs):
                await kwargs["before_submit"](72)
                provider_submissions.append("submitted")
                raise AssertionError("provider submission must remain unreachable")

        fake_generator = _SubmittingGenerator()
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        original_stage = generation_tasks.stage_provider_media_for_task

        async def _drop_claim_after_staging(project_dir, task_id, inputs):
            staged = await original_stage(project_dir, task_id, inputs)
            ProjectArtifactManifestAdapter(project_path).delete_entry(key)
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
        from lib.artifact_activation import activate_artifact_target_state

        project_path = _prepare_files(tmp_path)
        fake_pm = _ad_pm(project_path, with_sheet=False)
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

        class _SubmittingGenerator(_FakeGenerator):
            async def generate_video_async(self, **kwargs):
                await kwargs["before_submit"](73)
                provider_submissions.append("submitted")
                raise AssertionError("provider submission must remain unreachable")

        fake_generator = _SubmittingGenerator()
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        original_stage = generation_tasks.stage_provider_media_for_task

        async def _stage_then_replace_and_reactivate(project_dir, task_id, inputs):
            staged = await original_stage(project_dir, task_id, inputs)
            (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"replacement")
            _register_stale_visual_claim(
                project_path,
                ArtifactKey.episode_storyboard(1, "E1S01"),
                "storyboards/scene_E1S01.png",
            )
            assert ProjectArtifactManifestAdapter(project_path).get_entry(ArtifactKey.episode_script(1)) is not None
            assert (
                ProjectArtifactManifestAdapter(project_path).get_entry(ArtifactKey.episode_storyboard(1, "E1S01"))
                is not None
            )
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
        """generated_assets 容器本身被外部编辑损坏为非 dict（如 list）时读不出分镜指针，
        按「无登记指针」拒绝——不猜同名文件，也不抛未捕获 AttributeError。"""
        from lib.storyboard_sequence import StoryboardImageBindingRequired

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = ["bad"]

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

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
            # 越界与脏数据：统称非法路径
            ("/etc/passwd", "invalid storyboard image path"),  # 绝对路径：裸 `/` 拼接会整体丢弃左操作数
            ("../../outside.png", "invalid storyboard image path"),  # `..` 穿越出项目目录
            (123, "invalid storyboard image path"),  # 剧本 JSON 里的脏数据（非字符串）须可读失败而非 TypeError
            (0, "invalid storyboard image path"),  # falsy 脏数据：真值判断不得当成「未设置」而静默回退默认路径
            (False, "invalid storyboard image path"),  # 同上
            ([], "invalid storyboard image path"),  # 同上
            ({}, "invalid storyboard image path"),  # 同上
            # 目录归属：项目内但不在 storyboards/，措辞需与越界区分，便于定位外部编辑过的剧本
            ("storyboards/../end_frames/scene_E1S01.png", "must stay under storyboards/"),
            ("end_frames/scene_E1S01.png", "must stay under storyboards/"),
        ],
    )
    async def test_execute_video_task_storyboard_image_outside_dir_fails_hard(
        self, monkeypatch, tmp_path, storyboard_value, expected_message
    ):
        """剧本是磁盘 JSON，storyboard_image 字段不可信：越界 / 绕开 storyboards 目录 / 脏数据
        一律硬失败，不把任意服务器文件送进视频请求上传给供应商。"""
        project_path = _prepare_files(tmp_path)
        (tmp_path / "outside.png").write_bytes(b"png")
        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": storyboard_value}

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

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
        """storyboard_image 是项目 storyboards/ 内的绝对路径时同样硬失败：`os.path.join` 遇绝对
        路径会丢弃项目根，越界校验只看结果是否落在项目内，光靠目录归属挡不住这类值，须显式拒绝
        绝对路径本身。"""
        project_path = _prepare_files(tmp_path)
        (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        absolute_value = str(project_path / "storyboards" / "scene_E1S01.png")
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": absolute_value}

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

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
        """storyboard_image 是无盘符的根路径（如 `\\Users\\...`）时同样硬失败：`Path.is_absolute()`
        在 Windows 原生运行时对这类值返回 False，但 `os.path.join` 遇到根路径仍会丢弃项目根（仅
        保留 base 的盘符），须按正斜杠归一化后单独判断根分隔符开头。"""
        project_path = _prepare_files(tmp_path)
        (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "\\Users\\Alice\\scene.png"}

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

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
        """storyboard_image 指向 storyboards/ 内一个存在的目录（而非文件）时硬失败：目录同样
        通过 `exists()`，若不显式要求是文件，目录会被当作 start_image 传给视频后端，在编码阶段
        才失败且原因不可读。"""
        project_path = _prepare_files(tmp_path)
        (project_path / "storyboards" / "subdir").mkdir(parents=True)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/subdir"}

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

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
        """镜头设置了 end_frame_image 时，生成视频请求携带 end_image；快照路径取自
        镜头持久字段拼接的项目内固定相对路径。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()

        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["end_image"] == end_frame_dir / "scene_E1S01.png"

    async def test_execute_video_task_end_frame_image_bare_filename_resolves_via_default_dir(
        self, monkeypatch, tmp_path
    ):
        """尾帧字段是裸文件名（无 `end_frames/` 前缀）时按校验侧
        data_validator._resolve_existing_path 的 default_dir 回退口径解析——否则通过导入校验
        （校验器对裸文件名会补目录重试）的值会在生成期无理由硬失败。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()

        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm.script["segments"][0]["end_frame_image"] = "scene_E1S01.png"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["end_image"] == end_frame_dir / "scene_E1S01.png"

    @pytest.mark.parametrize("missing", [True, False], ids=["field-absent", "empty-string"])
    async def test_execute_video_task_without_end_frame_image_passes_none(self, monkeypatch, tmp_path, missing):
        """未设置尾帧的镜头行为不变：字段缺失或显式空字符串，end_image 均为 None。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()
        if not missing:
            fake_pm.script["segments"][0]["end_frame_image"] = ""

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["end_image"] is None

    async def test_execute_video_task_missing_end_frame_snapshot_fails_hard(self, monkeypatch, tmp_path):
        """尾帧字段指向的快照文件缺失时硬失败，不调用后端生成。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

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
            "/etc/passwd",  # 绝对路径：裸 `/` 拼接会整体丢弃左操作数，读到项目外文件
            "../../outside.png",  # `..` 穿越出项目目录
            "end_frames/../storyboards/scene_E1S01.png",  # 项目内但绕开快照目录，等于直接引用源图
            "storyboards/scene_E1S01.png",  # 同上：字段只接受 end_frames/ 内的快照
            123,  # 剧本 JSON 里的脏数据（非字符串）须给出可读失败，而非 TypeError
            0,  # falsy 脏数据：真值判断不得把它当成「未设置」而静默跳过尾帧
            False,  # 同上
            [],  # 同上
            {},  # 同上
            "end_frames/scene_E1S02.png",  # 落在快照目录内、文件也存在，但属于别的镜头——跨镜头误引
        ],
    )
    async def test_execute_video_task_end_frame_path_outside_snapshot_dir_fails_hard(
        self, monkeypatch, tmp_path, end_frame_value
    ):
        """剧本是磁盘 JSON，尾帧字段不可信：越界 / 绕开 end_frames 快照目录 / 脏数据一律硬失败，
        不把任意服务器文件送进视频请求。约束与写侧 end_frame.py、校验侧 data_validator 同口径。"""
        project_path = _prepare_files(tmp_path)
        (tmp_path / "outside.png").write_bytes(b"png")
        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S02.png").write_bytes(b"png")  # 别的镜头的快照，供跨镜头误引用例检查
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = end_frame_value

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

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
        """尾帧字段值恰是当前镜头的 canonical 相对路径，但磁盘上那个位置被替换成指向别处
        （另一镜头快照）的符号链接：try_safe_join / safe_join 都会展开符号链接把两侧解析到
        同一真实目标，仅凭路径相等挡不住「路径字符串对、磁盘对象被调包」，须显式拒绝。"""
        project_path = _prepare_files(tmp_path)
        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S02.png").write_bytes(b"png")
        (end_frame_dir / "scene_E1S01.png").symlink_to(end_frame_dir / "scene_E1S02.png")
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

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
        """符号链接调包不止发生在文件名这一级：`end_frames/` 目录本身被替换成指向项目内
        别的目录的符号链接时，最终文件名一致、realpath 展开后两侧路径也相等，仅检查文件名
        这一段挡不住——须逐段检查 canonical 路径的每个组件（含父目录）。"""
        project_path = _prepare_files(tmp_path)
        real_dir = project_path / "storyboards_end_frames_swap"
        real_dir.mkdir(parents=True, exist_ok=True)
        (real_dir / "scene_E1S01.png").write_bytes(b"png")
        (project_path / "end_frames").symlink_to(real_dir)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

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
        """Windows 原生环境下目录联接（junction）是独立于符号链接的 reparse point 类型，
        `Path.is_symlink()` 识别不到；`end_frames/` 被联接到项目内别的目录时须靠 `is_junction()`
        单独挡住。非 Windows 平台无法真实创建 junction，这里 monkeypatch `Path.is_junction()`
        模拟该状态，验证逐段检查确实调用了它而非只查符号链接。"""
        project_path = _prepare_files(tmp_path)
        real_dir = project_path / "storyboards_end_frames_swap"
        real_dir.mkdir(parents=True, exist_ok=True)
        (real_dir / "scene_E1S01.png").write_bytes(b"png")
        end_frames_dir = project_path / "end_frames"
        end_frames_dir.mkdir(parents=True, exist_ok=True)
        (end_frames_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
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
        """后端不支持尾帧能力时硬失败，不降级为参考图、不静默丢帧。

        替身只替换 provider 调用，能力判定交给生产代码 gate_video_request 跑真值（caps
        last_frame=False）——否则替身按自己的条件抛异常，验的是替身而非接线是否真能触达
        gating。能力组合的各分支另见 tests/test_video_frame_slots.py。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()

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

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

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
        """视频重生成无需额外操作即自动沿用尾帧：字段是镜头持久属性，每次执行都从剧本重新加载。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()

        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

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
        """drama 口型台词从分镜级 dialogue-kind utterances 取（覆盖 payload 已不带的
        video_prompt.dialogue）；voiceover-kind 不进视频 YAML。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        # 改用 drama 剧本：E1S01 携带有序 utterances（voiceover 在前、dialogue 在后）
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
        _seed_current_storyboard(fake_pm)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        # payload 的 video_prompt 不带 dialogue（drama 新结构）
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
        # dialogue-kind 台词与说话人进 YAML，voiceover-kind 不进视频提示词
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
        """utterances 迁移前的存量 drama 剧本：scene 无 utterances，台词仍留在 video_prompt.dialogue。"""
        script = self._drama_script()
        del script["scenes"][0]["utterances"]
        return script

    async def test_execute_video_task_injects_voice_profiles_for_audible_model(self, monkeypatch, tmp_path):
        """有音轨模型（voice_consistency != none）：dialogue speaker 命中的角色资产
        非空 voice_style 机械派生进 Voice_Profiles 顶部声明段。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = _FakeGenerator()
        fake_pm.script = self._drama_script()
        _seed_current_storyboard(fake_pm)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator, voice_consistency="soft")
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

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
        # 顶部集中声明段须先于 Action 出现
        assert prompt_yaml.index("Voice_Profiles") < prompt_yaml.index("Action")

    async def test_execute_video_task_skips_voice_profiles_for_silent_model(self, monkeypatch, tmp_path):
        """C 类（真无声，voice_consistency == none）模型不注入 Voice_Profiles。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = _FakeGenerator()
        fake_pm.script = self._drama_script()
        _seed_current_storyboard(fake_pm)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator, voice_consistency="none")
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

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
        # 台词不随声音风格一并省略：无声成片里台词文本照常下发，供应商可用作口型参考
        assert "你来了。" in prompt_yaml

    async def test_execute_video_task_skips_voice_profiles_when_episode_audio_disabled(self, monkeypatch, tmp_path):
        """本集关闭音频（requested_generate_audio=False）：即便模型有音轨也不注入 Voice_Profiles，
        与 C 类真无声模型同口径。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = _FakeGenerator()
        fake_pm.script = self._drama_script()
        _seed_current_storyboard(fake_pm)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(fake_generator, voice_consistency="soft", requested_generate_audio=False),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

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
        # 台词逐字不变，与 C 类真无声路径同口径
        assert "你来了。" in prompt_yaml

    async def test_execute_video_task_strips_caller_supplied_voice_profiles_for_non_drama(self, monkeypatch, tmp_path):
        """narration/ad（item 无 utterances 字段）请求体自带 voice_profiles 时一律剥离：
        该声明段唯一来源是 build_drama_video_prompt 的机械派生，调用方不得越权注入、绕过
        C 类（真无声）门控。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

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
        """utterances 迁移前的存量 drama 剧本（scene 无 utterances 字段，台词仍在
        video_prompt.dialogue）：load_script 按原始 JSON 读盘不过 pydantic 迁移，改走 legacy
        出口派生 Voice_Profiles，不因缺 utterances 静默丢失。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = _FakeGenerator()
        fake_pm.script = self._legacy_drama_script()
        _seed_current_storyboard(fake_pm)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator, voice_consistency="soft")
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

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
        """legacy dialogue 出口同过无声门控：本集关闭音频时不注入 Voice_Profiles，台词照常下发。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = _FakeGenerator()
        fake_pm.script = self._legacy_drama_script()
        _seed_current_storyboard(fake_pm)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(fake_generator, voice_consistency="soft", requested_generate_audio=False),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

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
        """存量 episode 剧本省略顶层 content_mode 时回退到 project.json 的 content_mode
        （与 lib.data_validator 已校验通过的既定口径一致），不因回退缺失而被误判为
        narration、静默跳过 dialogue 重建与 Voice_Profiles 注入。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["content_mode"] = "drama"
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = _FakeGenerator()
        fake_pm.script = {
            "episode": 1,
            # 顶层无 content_mode：存量 episode 省略该字段，真相源退到 project.json。
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
        _seed_current_storyboard(fake_pm)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator, voice_consistency="soft")
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

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
        """无显式 duration 时，默认值由 caps 收口（取 supported_durations[0]），且必然合法。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(fake_generator, supported_durations=(6, 10)),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)
        # 项目默认 duration 也置空，强制走 caps 默认。
        fake_pm.project.pop("default_duration", None)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )
        assert result["resource_type"] == "videos"
        assert fake_generator.video_calls[0]["duration_seconds"] == 6

    async def test_execute_video_task_default_duration_respects_resolution_constraint(self, monkeypatch, tmp_path):
        """Auto（无显式 duration）在受约束分辨率下取约束内的时长，而非 supported_durations 首项。

        Veo + 4k 只接受 8 秒；取首项 4 秒会让默认设置必然撞上 backend 的执行期拒绝。
        """
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(
                fake_generator,
                video_provider=("gemini-aistudio", "veo-3.1-generate-preview"),
                video_resolution="4k",
                supported_durations=(4, 6, 8),
            ),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)
        fake_pm.project.pop("default_duration", None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )
        assert fake_generator.video_calls[0]["duration_seconds"] == 8

    async def test_empty_supported_durations_guard_permissive(self, monkeypatch, tmp_path):
        """能力不可解析时 lane 交付空 supported_durations：守卫放行（不更坏），
        resolution 仍取自 lane 已解析出的值，不因能力缺失被改写。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(fake_generator, supported_durations=()),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                "duration_seconds": 9,
            },
        )
        assert result["resource_type"] == "videos"
        assert fake_generator.video_calls[0]["duration_seconds"] == 9
        assert fake_generator.video_calls[0]["resolution"] == "720p"

    async def test_video_resolve_failure_fails_task_without_fallback(self, monkeypatch, tmp_path):
        """视频解析失败即任务失败：异常原样上抛留痕，无硬编码 provider/model 兜底，后端不被调用。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        async def _boom(*args, **kwargs):
            raise RuntimeError("video provider unconfigured")

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _boom)

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

    async def test_tasks_declare_only_needed_lanes(self, monkeypatch, tmp_path):
        """任务只声明自己用到的 lane：图片类任务不声明 video/audio（只配置图片供应商的项目
        不因视频供应商缺配置失败，未声明 lane 不解析见 tests/server/test_generation_context.py），
        视频任务只声明 video；带参考图时 image lane 请求 i2i 能力。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _register_asset_sheet_claims(fake_pm)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator(project_path)
        seen: list[dict] = []

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(fake_generator, seen_lane_requests=seen),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        # E1S02 引用角色/场景/道具 sheet → 带参考图 → i2i；character 带 reference_image → i2i
        await generation_tasks.execute_storyboard_task(
            "demo", "E1S02", {"script_file": "episode_1.json", "prompt": "画面"}
        )
        await generation_tasks.execute_character_task("demo", "Alice", {"prompt": "角色描述"})
        await generation_tasks.execute_scene_task("demo", "祠堂", {"prompt": "场景描述"})
        for req in seen:
            assert req["image"] is not None
            assert req["video"] is None
            assert req["audio"] is None
        assert seen[0]["image"].capability == "i2i"

        seen.clear()
        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                "duration_seconds": 8,
            },
        )
        assert len(seen) == 1
        assert seen[0]["video"] is not None
        assert seen[0]["image"] is None
        assert seen[0]["audio"] is None
