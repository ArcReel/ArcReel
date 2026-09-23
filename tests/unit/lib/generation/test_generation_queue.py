"""Tests for GenerationQueue (async wrapper over TaskRepository)."""

import asyncio

import pytest

from lib.db.repositories.task_repo import TaskNotCancellableError
from lib.generation.generation_admission import generation_admission_lock
from lib.generation.generation_queue import (
    GenerationQueue,
    reference_projection_for_queued_task,
)
from lib.generation.task_failure import encode_failure


@pytest.fixture
async def queue(db_factory):
    """Create a GenerationQueue backed by in-memory SQLite."""
    return GenerationQueue(session_factory=db_factory)


class TestGenerationQueue:
    async def test_narration_task_admission_waits_for_the_shared_restore_guard(
        self,
        queue,
        tmp_path,
        monkeypatch,
    ):
        from lib.infra.app_data_dir import reset_for_tests

        monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "app-data"))
        reset_for_tests()
        async with generation_admission_lock(
            project_name="admission-demo",
            script_file="scripts/episode_01.json",
            resource_id="E1S01",
        ):
            enqueue = asyncio.create_task(
                queue.enqueue_task(
                    project_name="admission-demo",
                    task_type="tts",
                    media_type="audio",
                    resource_id="E1S01",
                    script_file="episode_01.json",
                    provider_id="audio-provider",
                )
            )
            await asyncio.sleep(0.1)
            assert not enqueue.done()

        result = await enqueue
        assert result["deduped"] is False

    async def test_enqueue_dedupe_claim_and_succeed(self, queue):
        first = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test"},
            script_file="episode_01.json",
            source="webui",
        )
        assert not first["deduped"]

        deduped = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test2"},
            script_file="episode_01.json",
            source="webui",
        )
        assert deduped["deduped"]
        assert deduped["task_id"] == first["task_id"]

        running = await queue.claim_next_task(media_type="image")
        assert running is not None
        assert running["task_id"] == first["task_id"]
        assert running["status"] == "running"

        rows = await queue.mark_task_succeeded(first["task_id"], {"file_path": "storyboards/scene_E1S01.png"})
        assert rows == 1
        done = await queue.get_task(first["task_id"])
        assert done is not None
        assert done["status"] == "succeeded"
        assert done["result"]["file_path"] == "storyboards/scene_E1S01.png"

        # 终态后允许再次入队
        second = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test3"},
            script_file="episode_01.json",
            source="webui",
        )
        assert not second["deduped"]
        assert second["task_id"] != first["task_id"]

    async def test_active_video_task_rejects_conflicting_narration_delivery(self, queue):
        first = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={"narration_delivery_options": {"narration_delivery": "post_production"}},
            script_file="episode_01.json",
            provider_id="video-provider",
        )

        with pytest.raises(RuntimeError, match="different narration delivery request"):
            await queue.enqueue_task(
                project_name="demo",
                task_type="video",
                media_type="video",
                resource_id="E1S01",
                payload={"narration_delivery_options": {"narration_delivery": "use_tts"}},
                script_file="episode_01.json",
                provider_id="video-provider",
            )

        active = await queue.get_task(first["task_id"])
        assert active is not None
        assert active["payload"]["narration_delivery_options"] == {"narration_delivery": "post_production"}

    async def test_active_reference_video_task_rejects_a_different_confirmed_duration(self, queue):
        await queue.enqueue_task(
            project_name="demo",
            task_type="reference_video",
            media_type="video",
            resource_id="E1U1",
            payload={
                "reference_request_options": {
                    "narration_delivery": "use_tts",
                    "confirmed_request_duration_seconds": 8,
                }
            },
            script_file="episode_01.json",
            provider_id="video-provider",
        )

        with pytest.raises(RuntimeError, match="different narration delivery request"):
            await queue.enqueue_task(
                project_name="demo",
                task_type="reference_video",
                media_type="video",
                resource_id="E1U1",
                payload={
                    "reference_request_options": {
                        "narration_delivery": "use_tts",
                        "confirmed_request_duration_seconds": 12,
                    }
                },
                script_file="episode_01.json",
                provider_id="video-provider",
            )

    async def test_active_video_task_still_dedupes_the_same_narration_request(self, queue):
        payload = {
            "narration_delivery_options": {
                "narration_delivery": "use_tts",
                "confirmed_request_duration_seconds": 8,
            }
        }
        first = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload=payload,
            script_file="episode_01.json",
            provider_id="video-provider",
        )

        duplicate = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload=payload,
            script_file="episode_01.json",
            provider_id="video-provider",
        )

        assert duplicate["deduped"] is True
        assert duplicate["task_id"] == first["task_id"]

    async def test_reference_rate_limit_projection_ignores_narration_delivery(self, monkeypatch, tmp_path):
        seen_options = []
        sentinel = object()

        class _ProjectManager:
            def load_script(self, project_name, script_file):
                assert (project_name, script_file) == ("demo", "ep1.json")
                return {"video_units": [{"unit_id": "E1U1"}]}

            def get_project_path(self, project_name):
                assert project_name == "demo"
                return tmp_path

        async def _project(**kwargs):
            seen_options.append(kwargs["options"])
            return sentinel

        monkeypatch.setattr("lib.config.resolver.get_project_manager", lambda: _ProjectManager())
        monkeypatch.setattr("lib.script.reference_video.request_projection.project_reference_unit_request", _project)

        projection = await reference_projection_for_queued_task(
            project={},
            project_name="demo",
            payload={
                "script_file": "ep1.json",
                "reference_request_options": {
                    "narration_delivery": "use_tts",
                    "narration_duration_floor": 9.5,
                    "duration_confirmed": True,
                    "confirmed_request_duration_seconds": 12,
                },
            },
            resource_id="E1U1",
        )

        assert projection is sentinel
        assert seen_options[0].to_payload() == {"narration_delivery": "post_production"}

    async def test_worker_lease_takeover(self, queue):
        first_ok = await queue.acquire_or_renew_worker_lease(
            name="default",
            owner_id="worker-a",
            ttl_seconds=1,
        )
        assert first_ok

        second_ok = await queue.acquire_or_renew_worker_lease(
            name="default",
            owner_id="worker-b",
            ttl_seconds=1,
        )
        assert not second_ok

        await asyncio.sleep(1.2)

        takeover_ok = await queue.acquire_or_renew_worker_lease(
            name="default",
            owner_id="worker-b",
            ttl_seconds=1,
        )
        assert takeover_ok

    async def test_claim_next_task_respects_dependencies_without_blocking_other_heads(self, queue):
        head_one = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "p1"},
            script_file="episode_01.json",
            source="skill",
            dependency_group="episode_01.json:group:1",
            dependency_index=0,
        )
        await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S02",
            payload={"prompt": "p2"},
            script_file="episode_01.json",
            source="skill",
            dependency_task_id=head_one["task_id"],
            dependency_group="episode_01.json:group:1",
            dependency_index=1,
        )
        head_two = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S03",
            payload={"prompt": "p3"},
            script_file="episode_01.json",
            source="skill",
            dependency_group="episode_01.json:group:2",
            dependency_index=0,
        )

        first_claim = await queue.claim_next_task(media_type="image")
        second_claim = await queue.claim_next_task(media_type="image")
        blocked_claim = await queue.claim_next_task(media_type="image")

        assert first_claim is not None
        assert second_claim is not None
        assert {first_claim["task_id"], second_claim["task_id"]} == {
            head_one["task_id"],
            head_two["task_id"],
        }
        assert blocked_claim is None

        await queue.mark_task_succeeded(
            head_one["task_id"],
            {"file_path": "storyboards/scene_E1S01.png"},
        )
        unblocked_claim = await queue.claim_next_task(media_type="image")
        assert unblocked_claim is not None
        assert unblocked_claim["resource_id"] == "E1S02"

    async def test_mark_task_failed_cascades_to_queued_dependents(self, queue):
        first = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "p1"},
            script_file="episode_01.json",
            source="skill",
            dependency_group="episode_01.json:group:1",
            dependency_index=0,
        )
        second = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S02",
            payload={"prompt": "p2"},
            script_file="episode_01.json",
            source="skill",
            dependency_task_id=first["task_id"],
            dependency_group="episode_01.json:group:1",
            dependency_index=1,
        )
        third = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S03",
            payload={"prompt": "p3"},
            script_file="episode_01.json",
            source="skill",
            dependency_task_id=second["task_id"],
            dependency_group="episode_01.json:group:1",
            dependency_index=2,
        )

        running = await queue.claim_next_task(media_type="image")
        assert running is not None
        assert running["task_id"] == first["task_id"]

        await queue.mark_task_failed(first["task_id"], "boom")

        second_task = await queue.get_task(second["task_id"])
        third_task = await queue.get_task(third["task_id"])
        assert second_task is not None
        assert third_task is not None
        assert second_task["status"] == "failed"
        assert third_task["status"] == "failed"
        # 每层只记直接阻塞方 task_id，reason 沿链条原样传递根因（不随层数重新嵌套）——
        # 避免深层依赖链把上一层的完整编码串再嵌套进新一层 JSON 造成的近指数增长。
        expected_second = encode_failure(
            "cascade_blocked_dependency", dependency_task_id=first["task_id"], reason="boom"
        )
        expected_third = encode_failure(
            "cascade_blocked_dependency", dependency_task_id=second["task_id"], reason="boom"
        )
        assert second_task["error_message"] == expected_second
        assert third_task["error_message"] == expected_third

    async def test_requeue_running_tasks(self, queue):
        task = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={"prompt": "video"},
            script_file="episode_01.json",
            source="webui",
        )
        running = await queue.claim_next_task(media_type="video")
        assert running is not None
        assert running["status"] == "running"

        recovered = await queue.requeue_running_tasks()
        assert recovered == 1

        queued = await queue.get_task(task["task_id"])
        assert queued is not None
        assert queued["status"] == "queued"
        assert queued["started_at"] is None

        claimed_again = await queue.claim_next_task(media_type="video")
        assert claimed_again is not None
        assert claimed_again["task_id"] == task["task_id"]

    async def test_cancel_task(self, queue):
        result = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )

        cancel_result = await queue.cancel_task(result["task_id"])
        assert len(cancel_result["cancelled"]) == 1
        assert cancel_result["cancelled"][0]["status"] == "cancelled"

    async def test_cancel_all_queued(self, queue):
        await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S02",
            payload={},
            script_file="ep1.json",
        )

        result = await queue.cancel_all_queued("demo")
        assert result["cancelled_count"] == 2

        stats = await queue.get_task_stats(project_name="demo")
        assert stats["cancelled"] == 2
        assert stats["queued"] == 0

    async def test_persist_provider_job_id_wrapper(self, queue):
        """persist_provider_job_id 是 wrapper,只验证不抛(行为细节在 repo 层测过)。"""
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        # 入队的 task 此时是 queued,但 persist 不校验 status(独立 commit)
        await queue.persist_provider_job_id(enqueued["task_id"], "job-abc-123")
        task = await queue.get_task(enqueued["task_id"])
        assert task is not None
        assert task["provider_job_id"] == "job-abc-123"

    async def test_mark_task_interrupted_finalizes_running_task_and_cascades_queued_dependents(self, queue):
        """进程级打断兜底：running 任务落 cancelled，排队中下游级联取消；终态再调返回 0。"""
        parent = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        child = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
            dependency_task_id=parent["task_id"],
        )
        assert await queue.claim_next_task(media_type="image") is not None

        assert await queue.mark_task_interrupted(parent["task_id"]) == 1

        parent_row = await queue.get_task(parent["task_id"])
        child_row = await queue.get_task(child["task_id"])
        assert parent_row is not None
        assert parent_row["status"] == "cancelled"
        assert child_row is not None
        assert child_row["status"] == "cancelled"
        assert await queue.mark_task_interrupted(parent["task_id"]) == 0

    async def test_cancel_running_task_is_rejected_and_task_runs_to_success(self, queue):
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        assert await queue.claim_next_task("video") is not None

        with pytest.raises(TaskNotCancellableError) as exc_info:
            await queue.cancel_task(enqueued["task_id"])
        assert exc_info.value.task_id == enqueued["task_id"]
        with pytest.raises(TaskNotCancellableError):
            await queue.get_cancel_preview(enqueued["task_id"])

        still_running = await queue.get_task(enqueued["task_id"])
        assert still_running is not None
        assert still_running["status"] == "running"
        assert await queue.mark_task_succeeded(enqueued["task_id"], {"file_path": "videos/r1.mp4"}) == 1
        done = await queue.get_task(enqueued["task_id"])
        assert done is not None
        assert done["status"] == "succeeded"
        assert done["result"] == {"file_path": "videos/r1.mp4"}

    async def test_cancel_queued_task_cascades_to_queued_dependents(self, queue):
        parent = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        child = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
            dependency_task_id=parent["task_id"],
        )

        preview = await queue.get_cancel_preview(parent["task_id"])
        assert [item["task_id"] for item in preview["cascaded"]] == [child["task_id"]]

        result = await queue.cancel_task(parent["task_id"])
        assert set(result) == {"cancelled", "skipped_terminal"}
        assert {item["task_id"] for item in result["cancelled"]} == {parent["task_id"], child["task_id"]}
        child_row = await queue.get_task(child["task_id"])
        assert child_row is not None
        assert child_row["status"] == "cancelled"


@pytest.fixture
def stub_enqueue_resolution(monkeypatch):
    """把入队解析链（视频 / 图片 / 音频三条）换成固定身份，返回一个可改写解析结果的 holder。"""
    from lib.config.resolver import ProviderModel

    holder = {"resolved": ProviderModel("custom-7", "advisory-video-model")}

    class _FakeResolver:
        def __init__(self, factory):
            pass

        async def resolve_video_backend(self, project, payload, *, generation_type=None):
            return holder["resolved"]

        async def resolve_image_backend(self, project, payload, *, generation_type):
            return holder["resolved"]

        async def resolve_audio_backend(self, project, payload):
            return holder["resolved"]

    class _FakeProjectManager:
        def load_project(self, project_name):
            return {}

    monkeypatch.setattr("lib.config.resolver.ConfigResolver", _FakeResolver)
    monkeypatch.setattr("lib.config.resolver.get_project_manager", lambda: _FakeProjectManager())
    return holder


class TestProjectExecutionProviderOnEnqueue:
    """两种视频生成模式入队都只保存 advisory provider，不冻结执行 model。"""

    async def test_video_task_keeps_only_advisory_provider(self, queue, stub_enqueue_resolution):
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={"prompt": "p"},
            script_file="ep1.json",
        )
        task = await queue.get_task(enqueued["task_id"])
        assert task["payload"] == {"prompt": "p"}
        assert "video_provider_i2v" not in task["payload"]
        assert task["provider_id"] == "custom-7"

    async def test_reference_video_task_keeps_only_advisory_provider(self, queue, stub_enqueue_resolution):
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="reference_video",
            media_type="video",
            resource_id="r1",
            payload={
                "prompt": "p",
                "references": [{"type": "character", "name": "A"}],
                "style": "snapshot",
                "duration_seconds": 9,
                "reference_request_options": {
                    "narration_delivery": "use_tts",
                    "duration_confirmed": True,
                    "narration_duration_floor": 9.5,
                    "confirmed_request_duration_seconds": 12,
                    "basis_digest": "must-not-freeze",
                },
            },
            script_file="ep1.json",
        )
        task = await queue.get_task(enqueued["task_id"])
        assert task["payload"] == {
            "script_file": "ep1.json",
            "reference_request_options": {
                "narration_delivery": "use_tts",
                "confirmed_request_duration_seconds": 12,
            },
        }
        assert "video_provider_r2v" not in task["payload"]
        assert "video_provider_i2v" not in task["payload"]
        assert task["provider_id"] == "custom-7"

    async def test_non_video_task_pins_nothing(self, queue, stub_enqueue_resolution):
        """图片任务的任务类型执行时才定，入队不锁——只落 provider_id。"""
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={"prompt": "p"},
            script_file="ep1.json",
        )
        task = await queue.get_task(enqueued["task_id"])
        assert task["payload"] == {"prompt": "p"}
        assert task["provider_id"] == "custom-7"

    async def test_unresolvable_model_leaves_payload_untouched(self, queue, stub_enqueue_resolution):
        """解析补不出 provider → payload 不变，provider_id 保持 NULL 兜底。"""
        from lib.config.resolver import ProviderModel

        stub_enqueue_resolution["resolved"] = ProviderModel("", "")
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={"prompt": "p"},
            script_file="ep1.json",
        )
        task = await queue.get_task(enqueued["task_id"])
        assert task["payload"] == {"prompt": "p"}
        assert task["provider_id"] is None

    async def test_provider_without_model_pins_nothing(self, queue, stub_enqueue_resolution):
        """解析出 provider 但补不出 model → 只落 provider_id，不锁半截桶键。"""
        from lib.config.resolver import ProviderModel

        stub_enqueue_resolution["resolved"] = ProviderModel("custom-7", "")
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={"prompt": "p"},
            script_file="ep1.json",
        )
        task = await queue.get_task(enqueued["task_id"])
        assert task["payload"] == {"prompt": "p"}
        assert task["provider_id"] == "custom-7"

    async def test_caller_payload_not_mutated(self, queue, stub_enqueue_resolution):
        """锁入走新 dict：调用方常复用同一份 payload 批量入队。"""
        payload = {"prompt": "p"}
        await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload=payload,
            script_file="ep1.json",
        )
        assert payload == {"prompt": "p"}
