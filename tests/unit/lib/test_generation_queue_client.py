"""Tests for generation_queue_client async functions."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from lib.generation_batch import GenerationBatchRequestSnapshot
from lib.generation_queue_client import (
    BatchTaskResult,
    TaskCancelledError,
    TaskSpec,
    TaskSpecValidationError,
    TaskWaitTimeoutError,
    WorkerOfflineError,
    batch_enqueue_and_wait_sync,
    batch_enqueue_only,
    enqueue_and_wait,
    enqueue_task_only,
    submit_generation_batch,
    wait_for_task,
)
from lib.generation_result import GenerationSelectionMode


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

    def test_blank_resource_id_is_rejected(self):
        """纯空白的 resource_id 与空的同样不可用：它会在执行期变成一段空白文件名。"""

        with pytest.raises(TaskSpecValidationError) as excinfo:
            TaskSpec.from_request(task_type="video", media_type="video", resource_id="   ", prompt="镜头平移")
        assert excinfo.value.code == "resource_id_required"

    def test_path_like_resource_id_is_rejected(self):
        """带路径片段的 resource_id 在结构守卫处就拒，不留到执行期拼产物路径时才发现。"""

        with pytest.raises(TaskSpecValidationError) as excinfo:
            TaskSpec.from_request(task_type="video", media_type="video", resource_id="../bad", prompt="镜头平移")
        assert excinfo.value.code == "invalid_resource_id"

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

    @pytest.mark.parametrize("scene", [123, ["a"], {"text": "a"}, True])
    def test_storyboard_non_string_scene_rejected(self, scene):
        # 入队守卫与渲染出口共用 require_storyboard_scene：
        # 非字符串 scene 在入队即被拒，不推迟到执行期。
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(
                task_type="storyboard", media_type="image", resource_id="S01", prompt={"scene": scene}
            )
        assert exc.value.code == "prompt_scene_empty"

    def test_storyboard_string_prompt_builds_spec(self):
        # 文本形态提示词不走 scene 判据，非空串照常入队。
        spec = TaskSpec.from_request(task_type="storyboard", media_type="image", resource_id="S01", prompt="黄昏的码头")
        assert spec.payload == {"prompt": "黄昏的码头"}

    def test_storyboard_blank_string_prompt_rejected(self):
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(task_type="storyboard", media_type="image", resource_id="S01", prompt="  ")
        assert exc.value.code == "prompt_text_empty"

    def test_storyboard_non_mapping_composition_accepted(self):
        # 入队判据只看 scene：composition 形态不参与准入，prompt 原样进入 payload。
        prompt = {"scene": "黄昏的码头", "composition": "medium"}
        spec = TaskSpec.from_request(task_type="storyboard", media_type="image", resource_id="S01", prompt=prompt)
        assert spec.payload == {"prompt": prompt}

    def test_asset_empty_prompt_rejected(self):
        with pytest.raises(TaskSpecValidationError) as exc:
            TaskSpec.from_request(task_type="character", media_type="image", resource_id="张三", prompt="")
        assert exc.value.code == "prompt_text_empty"

    def test_asset_string_prompt_builds_spec(self):
        spec = TaskSpec.from_request(task_type="character", media_type="image", resource_id="张三", prompt="一位老者")
        assert spec.payload == {"prompt": "一位老者"}

    def test_reference_video_validates_prompt_without_snapshotting_it(self):
        # 当前 shots 只在入队守卫点校验；worker 从 script_file + resource_id 重读最新内容。
        spec = TaskSpec.from_request(
            task_type="reference_video",
            media_type="video",
            resource_id="E1U1",
            prompt="Shot 1 (3s): @张三 推门",
            script_file="episode_1.json",
        )
        assert spec.task_type == "reference_video"
        assert spec.payload == {"script_file": "episode_1.json"}

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
        with pytest.raises(ValueError, match=r"extra_payload contains reserved keys: text") as exc:
            TaskSpec.from_request(
                task_type="tts",
                media_type="audio",
                resource_id="E1S01",
                prompt="夜色深沉",
                extra_payload={"text": "未校验的文本"},
            )
        assert "reserved" in str(exc.value)

    def test_empty_resource_id_rejected(self):
        with pytest.raises(TaskSpecValidationError) as excinfo:
            TaskSpec.from_request(task_type="video", media_type="video", resource_id="", prompt="跑")
        assert excinfo.value.code == "resource_id_required"

    def test_extra_payload_cannot_override_reserved_keys(self):
        # extra_payload 携带保留键会绕过单一守卫点，必须拒绝。
        with pytest.raises(ValueError, match=r"extra_payload contains reserved keys: prompt") as exc:
            TaskSpec.from_request(
                task_type="video",
                media_type="video",
                resource_id="S01",
                prompt="跑",
                extra_payload={"prompt": "未校验的别的值"},
            )
        assert "reserved" in str(exc.value)

    def test_extra_payload_cannot_override_script_file(self):
        with pytest.raises(ValueError, match=r"extra_payload contains reserved keys: script_file") as exc:
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
        kwargs = {"task_type": "video", "media_type": "video", "resource_id": "S01", "prompt": {"action": ""}}
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
            TaskSpec(task_type="character", media_type="image", resource_id="张三", unit_id="character/张三"),
            TaskSpec(task_type="character", media_type="image", resource_id="李四"),
        ]
        successes, failures = batch_enqueue_and_wait_sync(
            project_name="demo",
            specs=specs,
        )

        assert len(successes) == 2
        assert successes[0].resource_id == "character/张三"
        assert len(failures) == 0
        assert {s.resource_id for s in successes} == {"character/张三", "李四"}
        assert mock_enqueue.call_count == 2
        assert mock_wait.call_count == 2

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_same_named_asset_types_wait_for_their_own_tasks(self, mock_enqueue, mock_wait):
        mock_enqueue.side_effect = [
            {"task_id": "character-task"},
            {"task_id": "prop-task"},
        ]
        mock_wait.side_effect = [
            {"status": "succeeded", "result": {"file_path": "character.png"}},
            {"status": "succeeded", "result": {"file_path": "prop.png"}},
        ]

        successes, failures = batch_enqueue_and_wait_sync(
            project_name="demo",
            specs=[
                TaskSpec(task_type="character", media_type="image", resource_id="玉佩", unit_id="character/玉佩"),
                TaskSpec(task_type="prop", media_type="image", resource_id="玉佩", unit_id="prop/玉佩"),
            ],
        )

        assert failures == []
        assert [call.args[0] for call in mock_wait.await_args_list] == ["character-task", "prop-task"]
        assert [result.resource_id for result in successes] == ["character/玉佩", "prop/玉佩"]

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
    def test_enqueue_itself_raising_does_not_abort_the_batch(self, mock_enqueue, mock_wait):
        """A spec whose enqueue call raises must not orphan the specs after it.

        Before the fix, an exception raised inside the sequential Phase-1 enqueue
        loop propagated out of ``batch_enqueue_and_wait`` entirely — the caller's
        ``await`` raised, so no result was returned and every spec in the batch
        (including ones enqueued before the failure, and every one after it)
        silently never got a task row, a builder entry, or a caller-visible
        failure. This asserts the failing spec is instead captured as a
        never-queued failure while its siblings still get enqueued and awaited.
        """
        mock_enqueue.side_effect = [
            {"task_id": "t1"},
            RuntimeError("queue backend unavailable"),
            {"task_id": "t3"},
        ]
        mock_wait.side_effect = [
            {"status": "succeeded", "result": {"file_path": "a.png"}},
            {"status": "succeeded", "result": {"file_path": "c.png"}},
        ]

        specs = [
            TaskSpec(task_type="clue", media_type="image", resource_id="玉佩"),
            TaskSpec(task_type="clue", media_type="image", resource_id="老槐树"),
            TaskSpec(task_type="clue", media_type="image", resource_id="铜镜"),
        ]
        successes, failures = batch_enqueue_and_wait_sync(
            project_name="demo",
            specs=specs,
        )

        assert {s.resource_id for s in successes} == {"玉佩", "铜镜"}
        assert len(failures) == 1
        assert failures[0].resource_id == "老槐树"
        assert failures[0].task_id == ""
        assert failures[0].status == "failed"
        assert "queue backend unavailable" in (failures[0].error or "")
        # The spec after the failing one is still enqueued and awaited.
        assert mock_enqueue.call_count == 3
        assert mock_wait.call_count == 2

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_dependent_spec_skipped_when_its_dependency_fails_to_enqueue(self, mock_enqueue, mock_wait):
        """A dependency chain must not enqueue a follower onto a never-queued task."""
        mock_enqueue.side_effect = [RuntimeError("queue backend unavailable")]

        specs = [
            TaskSpec(task_type="storyboard", media_type="image", resource_id="S01"),
            TaskSpec(
                task_type="storyboard",
                media_type="image",
                resource_id="S02",
                dependency_resource_id="S01",
                dependency_group="ep1:group:1",
                dependency_index=1,
            ),
        ]
        successes, failures = batch_enqueue_and_wait_sync(project_name="demo", specs=specs)

        assert successes == []
        assert {f.resource_id for f in failures} == {"S01", "S02"}
        # S02 never attempts an enqueue call once its dependency failed.
        assert mock_enqueue.call_count == 1
        assert mock_wait.call_count == 0

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
        successes, failures = batch_enqueue_and_wait_sync(
            project_name="demo",
            specs=specs,
            on_success=on_success,
            on_failure=on_failure,
        )

        # 返回值与回调同口径：终态决定去向，两边都要认得出是哪个 resource
        assert [r.resource_id for r in successes] == ["A"]
        assert [r.resource_id for r in failures] == ["B"]
        assert [r.status for r in failures] == ["failed"]
        assert success_ids == ["A"]
        assert failure_ids == ["B"]

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_wait_timeout_is_reported_as_interrupted_not_failed(self, mock_enqueue, mock_wait):
        """A cut-short wait (task still non-terminal on the worker side) must not
        be indistinguishable from a provider-judged failure — that would tell a
        caller to retry a task that may still complete, risking a duplicate paid
        submission."""

        mock_enqueue.side_effect = [{"task_id": "t1"}]
        mock_wait.side_effect = [TaskWaitTimeoutError("timed out waiting for task 't1' after 3600.0s")]

        specs = [TaskSpec(task_type="clue", media_type="image", resource_id="玉佩")]
        successes, failures = batch_enqueue_and_wait_sync(project_name="demo", specs=specs)

        assert successes == []
        assert len(failures) == 1
        assert failures[0].resource_id == "玉佩"
        assert failures[0].task_id == "t1"
        assert failures[0].status == "interrupted"
        assert "timed out" in (failures[0].error or "")

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_worker_offline_during_wait_is_reported_as_interrupted_not_failed(self, mock_enqueue, mock_wait):
        mock_enqueue.side_effect = [{"task_id": "t1"}]
        mock_wait.side_effect = [WorkerOfflineError("queue worker offline while waiting for task 't1'")]

        specs = [TaskSpec(task_type="clue", media_type="image", resource_id="玉佩")]
        _successes, failures = batch_enqueue_and_wait_sync(project_name="demo", specs=specs)

        assert len(failures) == 1
        assert failures[0].status == "interrupted"


class TestBatchEnqueueOnly:
    """入队中断不撤销已创建的任务，没轮到的目标逐 ID 报出来。"""

    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    async def test_projects_all_memberships_in_the_enqueue_transaction(self, mock_enqueue):
        mock_enqueue.return_value = {"task_id": "grid-task", "deduped": False}
        spec = TaskSpec(
            task_type="storyboard",
            media_type="image",
            resource_id="grid-1",
            unit_id="E1S01",
            batch_unit_ids=("E1S01", "E1S02"),
        )

        enqueued, failures = await batch_enqueue_only(project_name="demo", specs=[spec], batch_id="batch-1")

        assert failures == []
        assert enqueued[0].task_id == "grid-task"
        assert mock_enqueue.await_args.kwargs["batch_unit_ids"] == ("E1S01", "E1S02")

    async def test_migration_is_rejected_before_the_durable_batch_is_created(self):
        calls: list[str] = []

        class _Queue:
            async def assert_project_migration_ok(self, project_name: str) -> None:
                assert project_name == "demo"
                calls.append("migration")
                raise RuntimeError("migration blocked")

            async def create_generation_batch(self, **_kwargs: Any) -> str:
                calls.append("create")
                return "batch-1"

        with pytest.raises(RuntimeError, match="migration blocked"):
            await submit_generation_batch(
                project_name="demo",
                operation="generate_assets",
                requested=GenerationBatchRequestSnapshot(
                    selection=GenerationSelectionMode.MISSING_ONLY,
                    requested=[],
                ),
                blocked=[],
                specs=[],
                source="mcp",
                queue=_Queue(),
            )

        assert calls == ["migration"]

    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    async def test_creates_every_task_and_reports_dedup_per_resource(self, mock_enqueue):
        mock_enqueue.side_effect = [
            {"task_id": "t1", "deduped": False},
            {"task_id": "t2", "deduped": True},
        ]
        specs = [
            TaskSpec(task_type="reference_video", media_type="video", resource_id="E1U1"),
            TaskSpec(task_type="reference_video", media_type="video", resource_id="E1U2"),
        ]

        enqueued, failures = await batch_enqueue_only(project_name="demo", specs=specs)

        assert [(item.resource_id, item.task_id, item.deduped) for item in enqueued] == [
            ("E1U1", "t1", False),
            ("E1U2", "t2", True),
        ]
        assert failures == []

    @patch("lib.generation_queue_client.get_generation_queue")
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    async def test_keeps_created_tasks_and_reports_the_rest_when_a_spec_fails(self, mock_enqueue, mock_queue):
        """已创建的任务是准入通过的完整付费单元：不撤销，照常执行。"""

        mock_enqueue.side_effect = [
            {"task_id": "t1", "deduped": False},
            RuntimeError("queue unavailable"),
        ]
        cancelled: list[str] = []

        class _Queue:
            async def cancel_task(self, task_id: str) -> dict[str, Any]:
                cancelled.append(task_id)
                return {"cancelled": [{"task_id": task_id, "status": "cancelled"}], "cancelling": []}

        mock_queue.return_value = _Queue()
        specs = [
            TaskSpec(task_type="reference_video", media_type="video", resource_id="E1U1"),
            TaskSpec(task_type="reference_video", media_type="video", resource_id="E1U2"),
        ]

        enqueued, failures = await batch_enqueue_only(project_name="demo", specs=specs)

        assert cancelled == []
        assert [item.resource_id for item in enqueued] == ["E1U1"]
        assert [(f.resource_id, f.task_id, f.enqueue_interrupted) for f in failures] == [("E1U2", "", True)]
        assert "queue unavailable" in (failures[0].error or "")

    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    async def test_specs_after_the_interruption_are_reported_without_another_attempt(self, mock_enqueue):
        """中断后不再逐个重试：同一个系统性故障会被撞 N 次，结果还是一个都建不出来。"""

        mock_enqueue.side_effect = [
            {"task_id": "t1", "deduped": False},
            RuntimeError("queue unavailable"),
        ]
        specs = [
            TaskSpec(task_type="reference_video", media_type="video", resource_id=rid)
            for rid in ("E1U1", "E1U2", "E1U3")
        ]

        enqueued, failures = await batch_enqueue_only(project_name="demo", specs=specs)

        assert mock_enqueue.await_count == 2
        assert [item.resource_id for item in enqueued] == ["E1U1"]
        assert [f.resource_id for f in failures] == ["E1U2", "E1U3"]
        assert all(f.enqueue_interrupted for f in failures)
        assert "E1U2" in (failures[1].error or "")


async def test_batch_enqueue_only_keeps_created_tasks_when_the_caller_is_cancelled(monkeypatch):
    """调用方在中途被取消：取消语义原样上抛，已创建的任务留在队列里照常执行。"""
    import asyncio as _asyncio

    from lib import generation_queue_client as mod

    created: list[str] = []
    cancelled: list[str] = []

    async def fake_enqueue(**kwargs):
        if kwargs["resource_id"] == "S02":
            raise _asyncio.CancelledError
        created.append(kwargs["resource_id"])
        return {"task_id": f"t-{kwargs['resource_id']}", "deduped": False}

    class _FakeQueue:
        async def cancel_task(self, task_id: str):
            cancelled.append(task_id)
            return {"cancelled": [{"task_id": task_id}], "cancelling": [], "skipped_terminal": []}

    monkeypatch.setattr(mod, "enqueue_task_only", fake_enqueue)
    monkeypatch.setattr(mod, "get_generation_queue", lambda: _FakeQueue())

    specs = [
        TaskSpec.from_request(task_type="video", media_type="video", resource_id=rid, prompt="跑")
        for rid in ("S01", "S02")
    ]

    with pytest.raises(_asyncio.CancelledError):
        await mod.batch_enqueue_only(project_name="demo", specs=specs)

    assert created == ["S01"]
    assert cancelled == []
