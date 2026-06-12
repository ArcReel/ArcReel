"""Tests for generation_queue_client async functions."""

from unittest.mock import AsyncMock, patch

import pytest

from lib.generation_queue_client import (
    BatchTaskResult,
    TaskCancelledError,
    TaskSpec,
    TaskSpecValidationError,
    TaskWaitTimeoutError,
    WorkerOfflineError,
    batch_enqueue_and_wait_sync,
    enqueue_and_wait,
    enqueue_task_only,
    wait_for_task,
)


class TestTaskSpecFromRequest:
    def test_video_string_prompt_builds_spec(self):
        spec = TaskSpec.from_request(
            task_type="video",
            media_type="video",
            resource_id="S01",
            prompt="一个奔跑的镜头",
            script_file="episode_1.json",
        )
        assert spec.task_type == "video"
        assert spec.media_type == "video"
        assert spec.resource_id == "S01"
        assert spec.script_file == "episode_1.json"
        assert spec.payload == {"prompt": "一个奔跑的镜头", "script_file": "episode_1.json"}

    def test_video_action_object_prompt_builds_spec(self):
        prompt = {"action": "转身", "camera_motion": "Static", "dialogue": [{"speaker": "甲", "line": "走"}]}
        spec = TaskSpec.from_request(
            task_type="video",
            media_type="video",
            resource_id="S01",
            prompt=prompt,
        )
        assert spec.payload == {"prompt": prompt}

    def test_video_extra_payload_merged(self):
        spec = TaskSpec.from_request(
            task_type="video",
            media_type="video",
            resource_id="S01",
            prompt="跑",
            script_file="episode_1.json",
            extra_payload={"duration_seconds": 8, "seed": 42},
        )
        assert spec.payload == {
            "prompt": "跑",
            "script_file": "episode_1.json",
            "duration_seconds": 8,
            "seed": 42,
        }

    def test_video_empty_string_prompt_rejected(self):
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(task_type="video", media_type="video", resource_id="S01", prompt="   ")
        assert exc.value.code == "prompt_text_empty"

    def test_video_dict_without_action_rejected(self):
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(task_type="video", media_type="video", resource_id="S01", prompt={"scene": "x"})
        assert exc.value.code == "video_prompt_must_be_string_or_action_object"

    def test_video_empty_action_rejected(self):
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(task_type="video", media_type="video", resource_id="S01", prompt={"action": "  "})
        assert exc.value.code == "video_prompt_action_empty"

    def test_video_null_action_rejected(self):
        # 显式 null：str(None) 会得到 truthy 的 "None"，必须当作空值拒绝而非放行。
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(task_type="video", media_type="video", resource_id="S01", prompt={"action": None})
        assert exc.value.code == "video_prompt_action_empty"

    def test_video_dialogue_not_array_rejected(self):
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(
                task_type="video",
                media_type="video",
                resource_id="S01",
                prompt={"action": "转身", "dialogue": "走"},
            )
        assert exc.value.code == "video_prompt_dialogue_array"

    def test_video_non_string_non_dict_prompt_rejected(self):
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(task_type="video", media_type="video", resource_id="S01", prompt=123)
        assert exc.value.code == "prompt_must_be_string_or_object"

    def test_storyboard_scene_object_builds_spec(self):
        prompt = {"scene": "黄昏的码头", "composition": {}}
        spec = TaskSpec.from_request(
            task_type="storyboard", media_type="image", resource_id="S01", prompt=prompt, script_file="e.json"
        )
        assert spec.payload == {"prompt": prompt, "script_file": "e.json"}

    def test_storyboard_dict_without_scene_rejected(self):
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(task_type="storyboard", media_type="image", resource_id="S01", prompt={"action": "x"})
        assert exc.value.code == "prompt_must_be_string_or_scene_object"

    def test_storyboard_empty_scene_rejected(self):
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(task_type="storyboard", media_type="image", resource_id="S01", prompt={"scene": " "})
        assert exc.value.code == "prompt_scene_empty"

    def test_storyboard_null_scene_rejected(self):
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(task_type="storyboard", media_type="image", resource_id="S01", prompt={"scene": None})
        assert exc.value.code == "prompt_scene_empty"

    def test_asset_empty_prompt_rejected(self):
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(task_type="character", media_type="image", resource_id="张三", prompt="")
        assert exc.value.code == "prompt_text_empty"

    def test_asset_string_prompt_builds_spec(self):
        spec = TaskSpec.from_request(task_type="character", media_type="image", resource_id="张三", prompt="一位老者")
        assert spec.payload == {"prompt": "一位老者"}

    def test_reference_video_prompt_builds_spec(self):
        # 参考生视频的 prompt 由 shots[*].text 拼接而成，走默认（非空字符串）分支。
        spec = TaskSpec.from_request(
            task_type="reference_video",
            media_type="video",
            resource_id="E1U1",
            prompt="Shot 1 (3s): @张三 推门",
            script_file="episode_1.json",
        )
        assert spec.task_type == "reference_video"
        assert spec.payload == {"prompt": "Shot 1 (3s): @张三 推门", "script_file": "episode_1.json"}

    def test_reference_video_empty_prompt_rejected(self):
        # 所有 shots[*].text 拼接后只剩空白 → 守卫点拒绝，不再漏到执行层。
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(
                task_type="reference_video",
                media_type="video",
                resource_id="E1U1",
                prompt="\n   ",
                script_file="episode_1.json",
            )
        assert exc.value.code == "prompt_text_empty"

    def test_tts_null_prompt_builds_spec(self):
        # 旁白文本默认由执行层从剧本 novel_text 读取，prompt 留空合法。
        spec = TaskSpec.from_request(task_type="tts", media_type="audio", resource_id="E1S01", prompt=None)
        assert spec.payload == {"prompt": None}

    def test_tts_string_prompt_builds_spec(self):
        spec = TaskSpec.from_request(task_type="tts", media_type="audio", resource_id="E1S01", prompt="夜色深沉")
        assert spec.payload == {"prompt": "夜色深沉"}

    def test_tts_empty_string_prompt_rejected(self):
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(task_type="tts", media_type="audio", resource_id="E1S01", prompt="  \n")
        assert exc.value.code == "prompt_text_empty"

    def test_tts_object_prompt_rejected(self):
        # tts 只接受非空字符串或留空，对象类型用专用错误码标明实际约束。
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(task_type="tts", media_type="audio", resource_id="E1S01", prompt={"text": "x"})
        assert exc.value.code == "tts_prompt_must_be_string_or_null"

    def test_tts_extra_payload_text_rejected(self):
        # text 是 tts 执行层优先读取的字段，必须与 prompt 同走守卫点，不得经 extra_payload 绕过。
        with pytest.raises(ValueError) as exc:
            TaskSpec.from_request(
                task_type="tts",
                media_type="audio",
                resource_id="E1S01",
                prompt="夜色深沉",
                extra_payload={"text": "未校验的文本"},
            )
        assert "reserved" in str(exc.value)

    def test_empty_resource_id_rejected(self):
        with pytest.raises(ValueError):
            TaskSpec.from_request(task_type="video", media_type="video", resource_id="", prompt="跑")

    def test_extra_payload_cannot_override_reserved_keys(self):
        # extra_payload 携带保留键会绕过单一守卫点，必须拒绝。
        with pytest.raises(ValueError) as exc:
            TaskSpec.from_request(
                task_type="video",
                media_type="video",
                resource_id="S01",
                prompt="跑",
                extra_payload={"prompt": "未校验的别的值"},
            )
        assert "reserved" in str(exc.value)

    def test_extra_payload_cannot_override_script_file(self):
        with pytest.raises(ValueError) as exc:
            TaskSpec.from_request(
                task_type="video",
                media_type="video",
                resource_id="S01",
                prompt="跑",
                script_file="e.json",
                extra_payload={"script_file": "../越权.json"},
            )
        assert "reserved" in str(exc.value)

    def test_webui_and_sdk_same_input_same_spec(self):
        # 同一非法输入，两路（WebUI / SDK）都经 from_request，结果一致。
        kwargs = dict(task_type="video", media_type="video", resource_id="S01", prompt={"action": ""})
        with pytest.raises(TaskSpecValidationError) as web:
            TaskSpec.from_request(**kwargs)
        with pytest.raises(TaskSpecValidationError) as sdk:
            TaskSpec.from_request(**kwargs)
        assert web.value.code == sdk.value.code == "video_prompt_action_empty"


class TestGenerationQueueClient:
    async def test_enqueue_task_only_requires_online_worker(self, generation_queue):
        with pytest.raises(WorkerOfflineError):
            await enqueue_task_only(
                project_name="demo",
                task_type="storyboard",
                media_type="image",
                resource_id="S00",
                payload={"prompt": "p"},
                script_file="episode_01.json",
            )

    async def test_enqueue_task_only_enqueues_when_worker_online(self, generation_queue):
        await generation_queue.acquire_or_renew_worker_lease(
            name="default",
            owner_id="worker-a",
            ttl_seconds=30,
        )

        result = await enqueue_task_only(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="S01",
            payload={"prompt": "p"},
            script_file="episode_01.json",
            dependency_group="episode_01.json:group:1",
            dependency_index=0,
        )

        task = await generation_queue.get_task(result["task_id"])
        assert task is not None
        assert task["status"] == "queued"
        assert task["dependency_group"] == "episode_01.json:group:1"
        assert task["dependency_index"] == 0

    async def test_wait_for_task_timeout(self, generation_queue):
        task = await generation_queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="S01",
            payload={"prompt": "p"},
            script_file="episode_01.json",
            source="skill",
        )

        with pytest.raises(TaskWaitTimeoutError):
            await wait_for_task(
                task["task_id"],
                poll_interval=0.05,
                timeout_seconds=0.2,
                worker_offline_grace_seconds=10.0,
            )

    async def test_wait_for_task_raises_when_worker_offline(self, generation_queue):
        task = await generation_queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="S02",
            payload={"prompt": "p"},
            script_file="episode_01.json",
            source="skill",
        )

        with pytest.raises(WorkerOfflineError):
            await wait_for_task(
                task["task_id"],
                poll_interval=0.05,
                timeout_seconds=5.0,
                worker_offline_grace_seconds=0.2,
            )

    async def test_wait_for_task_returns_when_cancelled(self, generation_queue):
        task = await generation_queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="S03",
            payload={"prompt": "p"},
            script_file="episode_01.json",
            source="skill",
        )
        # 取消任务后 wait_for_task 应正常返回（不抛异常），状态为 cancelled
        await generation_queue.cancel_task(task["task_id"])

        result = await wait_for_task(
            task["task_id"],
            poll_interval=0.05,
            timeout_seconds=5.0,
            worker_offline_grace_seconds=10.0,
        )
        assert result["status"] == "cancelled"

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    async def test_enqueue_and_wait_raises_task_cancelled_error(self, mock_enqueue, mock_wait, generation_queue):
        """enqueue_and_wait 应在 wait_for_task 返回 cancelled 状态时抛出 TaskCancelledError。"""
        mock_enqueue.return_value = {"task_id": "task-cancelled-123"}
        mock_wait.return_value = {"status": "cancelled", "task_id": "task-cancelled-123"}

        with pytest.raises(TaskCancelledError):
            await enqueue_and_wait(
                project_name="demo",
                task_type="storyboard",
                media_type="image",
                resource_id="S04",
                payload={"prompt": "p"},
                script_file="episode_01.json",
                source="skill",
            )


class TestBatchEnqueueAndWaitSync:
    """Tests for batch_enqueue_and_wait_sync (mocked async functions)."""

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_empty_specs(self, mock_enqueue, mock_wait):
        successes, failures = batch_enqueue_and_wait_sync(
            project_name="demo",
            specs=[],
        )
        assert successes == []
        assert failures == []
        mock_enqueue.assert_not_called()
        mock_wait.assert_not_called()

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_basic_success(self, mock_enqueue, mock_wait):
        mock_enqueue.side_effect = [
            {"task_id": "t1"},
            {"task_id": "t2"},
        ]
        mock_wait.side_effect = [
            {"status": "succeeded", "result": {"file_path": "a.png"}},
            {"status": "succeeded", "result": {"file_path": "b.png"}},
        ]

        specs = [
            TaskSpec(task_type="character", media_type="image", resource_id="张三"),
            TaskSpec(task_type="character", media_type="image", resource_id="李四"),
        ]
        successes, failures = batch_enqueue_and_wait_sync(
            project_name="demo",
            specs=specs,
        )

        assert len(successes) == 2
        assert len(failures) == 0
        assert {s.resource_id for s in successes} == {"张三", "李四"}
        assert mock_enqueue.call_count == 2
        assert mock_wait.call_count == 2

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_partial_failure(self, mock_enqueue, mock_wait):
        mock_enqueue.side_effect = [
            {"task_id": "t1"},
            {"task_id": "t2"},
        ]
        mock_wait.side_effect = [
            {"status": "succeeded", "result": {"file_path": "a.png"}},
            {"status": "failed", "error_message": "API error"},
        ]

        specs = [
            TaskSpec(task_type="clue", media_type="image", resource_id="玉佩"),
            TaskSpec(task_type="clue", media_type="image", resource_id="老槐树"),
        ]
        successes, failures = batch_enqueue_and_wait_sync(
            project_name="demo",
            specs=specs,
        )

        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].resource_id in ("玉佩", "老槐树")
        assert failures[0].status == "failed"

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_wait_exception_becomes_failure(self, mock_enqueue, mock_wait):
        mock_enqueue.return_value = {"task_id": "t1"}
        mock_wait.side_effect = RuntimeError("connection lost")

        specs = [
            TaskSpec(task_type="storyboard", media_type="image", resource_id="S01"),
        ]
        successes, failures = batch_enqueue_and_wait_sync(
            project_name="demo",
            specs=specs,
        )

        assert len(successes) == 0
        assert len(failures) == 1
        assert "connection lost" in failures[0].error

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_dependency_resource_id_resolution(self, mock_enqueue, mock_wait):
        mock_enqueue.side_effect = [
            {"task_id": "t-first"},
            {"task_id": "t-second"},
        ]
        mock_wait.side_effect = [
            {"status": "succeeded", "result": {}},
            {"status": "succeeded", "result": {}},
        ]

        specs = [
            TaskSpec(
                task_type="storyboard",
                media_type="image",
                resource_id="S01",
            ),
            TaskSpec(
                task_type="storyboard",
                media_type="image",
                resource_id="S02",
                dependency_resource_id="S01",
                dependency_group="ep1:group:1",
                dependency_index=1,
            ),
        ]
        batch_enqueue_and_wait_sync(project_name="demo", specs=specs)

        # First enqueue: no dependency
        first_call = mock_enqueue.call_args_list[0]
        assert first_call.kwargs.get("dependency_task_id") is None

        # Second enqueue: dependency_task_id resolved to "t-first"
        second_call = mock_enqueue.call_args_list[1]
        assert second_call.kwargs["dependency_task_id"] == "t-first"
        assert second_call.kwargs["dependency_group"] == "ep1:group:1"
        assert second_call.kwargs["dependency_index"] == 1

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_callbacks_invoked(self, mock_enqueue, mock_wait):
        mock_enqueue.side_effect = [
            {"task_id": "t1"},
            {"task_id": "t2"},
        ]
        mock_wait.side_effect = [
            {"status": "succeeded", "result": {}},
            {"status": "failed", "error_message": "boom"},
        ]

        success_ids = []
        failure_ids = []

        def on_success(br: BatchTaskResult):
            success_ids.append(br.resource_id)

        def on_failure(br: BatchTaskResult):
            failure_ids.append(br.resource_id)

        specs = [
            TaskSpec(task_type="character", media_type="image", resource_id="A"),
            TaskSpec(task_type="character", media_type="image", resource_id="B"),
        ]
        batch_enqueue_and_wait_sync(
            project_name="demo",
            specs=specs,
            on_success=on_success,
            on_failure=on_failure,
        )

        assert len(success_ids) == 1
        assert len(failure_ids) == 1
