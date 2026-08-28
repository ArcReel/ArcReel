"""ComfyUIVideoBackend 单元测试（respx 捕获出站请求，假表压缩轮询等待）。"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest
import respx

from lib.providers import PROVIDER_COMFYUI
from lib.video_backends.base import AmbiguousSubmitError, ResumeExpiredError, VideoGenerationRequest
from lib.video_backends.comfyui import (
    BUILTIN_COMFYUI_MODELS,
    ComfyUIVideoBackend,
    ComfyUIWorkflowError,
    build_minimax_h3_eight_step_fp8_workflow,
    build_minimax_h3_eight_step_workflow,
    build_minimax_h3_workflow,
    dimensions_for,
    normalize_duration,
    normalize_minimax_h3_prompt,
    validate_comfyui_workflows,
)
from tests.fakes import bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http, only_request, request_json

_BASE_URL = "http://comfyui:8188"


class _ComfyUIRoutes(NamedTuple):
    """ComfyUI 的出站流量：上传、提交、轮询、下载。"""

    upload: respx.Route
    submit: respx.Route
    poll: respx.Route
    download: respx.Route


@contextmanager
def _comfyui(*, base_url: str = _BASE_URL) -> Iterator[_ComfyUIRoutes]:
    with capture_http() as router:
        yield _ComfyUIRoutes(
            upload=router.post(f"{base_url}/upload/image"),
            submit=router.post(f"{base_url}/prompt"),
            poll=router.get(url__regex=rf"^{re.escape(base_url)}/history/[^/]+$"),
            download=router.get(url__regex=rf"^{re.escape(base_url)}/view\?"),
        )


def _json(body: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=body)


def _prompt_id_response(prompt_id: str = "p-1") -> httpx.Response:
    return _json({"prompt_id": prompt_id})


def _in_progress() -> httpx.Response:
    return _json({})


def _completed(prompt_id: str = "p-1") -> httpx.Response:
    """完成态 history 响应：SaveVideo 节点输出 mp4 成片。"""
    return _json(
        {
            prompt_id: {
                "status": {
                    "status_str": "success",
                    "completed": True,
                    "messages": [["execution_success", {"prompt_id": prompt_id}]],
                },
                "outputs": {
                    "92": {
                        "videos": [{"filename": f"{prompt_id}.mp4", "subfolder": "video/MiniMax_H3", "type": "output"}]
                    }
                },
            }
        }
    )


def _failed(prompt_id: str = "p-1") -> httpx.Response:
    return _json(
        {
            prompt_id: {
                "status": {
                    "status_str": "error",
                    "completed": True,
                    "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]],
                },
                "outputs": {},
            }
        }
    )


def _request(tmp_path: Path, **overrides) -> VideoGenerationRequest:
    params: dict = {
        "prompt": "p",
        "output_path": tmp_path / "o.mp4",
        "aspect_ratio": "9:16",
        "duration_seconds": 5,
    }
    params.update(overrides)
    return VideoGenerationRequest(**params)


def _write_file(path: Path, payload: bytes = b"bytes") -> Path:
    path.write_bytes(payload)
    return path


def _sent_workflow(routes: _ComfyUIRoutes) -> dict:
    return request_json(only_request(routes.submit))["prompt"]


class TestWorkflowBuilders:
    def test_builtin_models_list(self):
        assert BUILTIN_COMFYUI_MODELS == (
            "minimax-h3-ref2va",
            "minimax-h3-ref2va-8step",
            "minimax-h3-ref2va-8step-fp8",
        )

    def test_builtin_model_builders(self):
        from lib.video_backends.comfyui import _BUILTIN_MODEL_BUILDERS

        assert set(_BUILTIN_MODEL_BUILDERS) == set(BUILTIN_COMFYUI_MODELS)

    def test_standard_workflow_wires_9_images_and_3_audios(self):
        workflow = build_minimax_h3_workflow(
            prompt="@图片1人物参考 @视频2动作参考 @音频3声音参考",
            image_files=[f"image-{i}.png" for i in range(9)],
            video_files=[f"video-{i}.mp4" for i in range(3)],
            audio_files=[f"audio-{i}.wav" for i in range(3)],
            duration=10,
            aspect_ratio="16:9",
            resolution="768p",
            seed=42,
        )
        h3 = workflow["136"]
        assert h3["class_type"] == "MiniMaxH3ReferenceToVideo"
        assert h3["inputs"]["prompt"] == "<Picture 1> 人物参考 <Video 2> 动作参考 <Audio 3> 声音参考"
        assert h3["inputs"]["length"] == 243
        assert h3["inputs"]["width"] % 32 == 0
        assert h3["inputs"]["height"] % 32 == 0
        pixels = h3["inputs"]["width"] * h3["inputs"]["height"]
        assert 0.85 * 1024 * 1024 < pixels < 0.95 * 1024 * 1024

        for index in range(9):
            assert h3["inputs"][f"ref_images.ref_image_{index}"] == [str(200 + index), 0]
        for index in range(3):
            assert workflow[str(300 + index)]["class_type"] == "LoadVideo"
            assert workflow[str(320 + index)]["class_type"] == "GetVideoComponents"
            assert h3["inputs"][f"ref_videos.ref_video_{index}"] == [str(320 + index), 0]
            assert h3["inputs"][f"ref_video_audios.ref_video_audio_{index}"] == [str(320 + index), 1]
            assert workflow[str(400 + index)]["class_type"] == "LoadAudio"
            assert h3["inputs"][f"ref_audios.ref_audio_{index}"] == [str(400 + index), 0]

    @pytest.mark.parametrize("duration", [5, 6, 7, 8, 9, 10, 11, 12, 15])
    def test_durations_use_17n_plus_5_frames(self, duration: int):
        workflow = build_minimax_h3_workflow(
            prompt="测试",
            image_files=["reference.png"],
            video_files=[],
            audio_files=[],
            duration=duration,
            aspect_ratio="9:16",
            resolution="480p",
            seed=1,
        )
        frames = workflow["136"]["inputs"]["length"]
        assert frames % 17 == 5
        assert 124 <= frames <= 362

    def test_480p_uses_about_0_4_megapixels(self):
        workflow = build_minimax_h3_workflow(
            prompt="测试",
            image_files=["reference.png"],
            video_files=[],
            audio_files=[],
            duration=10,
            aspect_ratio="16:9",
            resolution="480p",
            seed=1,
        )
        pixels = workflow["136"]["inputs"]["width"] * workflow["136"]["inputs"]["height"]
        assert 0.35 * 1024 * 1024 < pixels < 0.45 * 1024 * 1024

    def test_eight_step_workflow_applies_lora_to_scheduler_and_model_path(self):
        workflow = build_minimax_h3_eight_step_workflow(
            prompt="@图片1测试八步模型",
            image_files=["image-1.png"],
            video_files=[],
            audio_files=[],
            duration=5,
            aspect_ratio="9:16",
            resolution="480p",
            seed=42,
        )
        assert workflow["148"] == {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["127", 0],
                "lora_name": "minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors",
                "strength_model": 0.75,
            },
        }
        assert workflow["127"]["inputs"]["unet_name"] == "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
        assert workflow["141"]["inputs"]["model"] == ["148", 0]
        assert workflow["124"]["inputs"]["model"] == ["148", 0]
        assert workflow["124"]["inputs"]["steps"] == 8
        assert workflow["136"]["inputs"]["prompt"] == "<Picture 1> 测试八步模型"

    def test_eight_step_fp8_changes_only_unet_file(self):
        workflow = build_minimax_h3_eight_step_fp8_workflow(
            prompt="@图片1测试八步 FP8 模型",
            image_files=["image-1.png"],
            video_files=[],
            audio_files=[],
            duration=5,
            aspect_ratio="9:16",
            resolution="480p",
            seed=42,
        )
        assert workflow["127"]["inputs"]["unet_name"] == "minimax_h3_ref2va_pruned_fp8_scaled.safetensors"
        assert workflow["124"]["inputs"]["steps"] == 8
        assert workflow["148"]["class_type"] == "LoraLoaderModelOnly"

    def test_standard_workflow_steps_is_20_without_lora(self):
        workflow = build_minimax_h3_workflow(prompt="测试", image_files=[], video_files=[], audio_files=[], seed=3)
        assert workflow["124"]["inputs"]["steps"] == 20
        assert "148" not in workflow


class TestNormalizeHelpers:
    def test_prompt_keeps_reference_ordinals(self):
        # 源串里 @标记 与下一个 token 之间的空格原样保留（替换结果带尾随空格），故是双空格
        assert normalize_minimax_h3_prompt("@图片9 @视频3 @音频3") == "<Picture 9>  <Video 3>  <Audio 3>"

    def test_dimensions_align_to_32(self):
        width, height = dimensions_for("9:16", "480p")
        assert width % 32 == 0 and height % 32 == 0

    def test_normalize_duration_clamps_and_aligns(self):
        assert normalize_duration(5) == (5, 124)
        assert normalize_duration(20) == (15, 362)  # 越界截断
        assert normalize_duration(3) == (5, 124)  # 越界截断
        assert normalize_duration(None) == (5, 124)
        assert normalize_duration(10) == (10, 243)


class TestValidation:
    def test_validate_accepts_valid_configured_workflow(self):
        template = build_minimax_h3_workflow(prompt="t", image_files=[], video_files=[], audio_files=[], seed=7)
        validated = validate_comfyui_workflows([{"model": "minimax-h3-turbo", "workflow": template}])
        assert validated == [("minimax-h3-turbo", template)]

    def test_validate_returns_empty_for_none(self):
        assert validate_comfyui_workflows(None) == []

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda wf: wf.pop("136"),
            lambda wf: wf.pop("92"),
            lambda wf: wf.update({"900": {"class_type": "LoadImage", "inputs": "bad"}}),
            lambda wf: wf.update({"900": {"class_type": "LoadImage"}}),
        ],
    )
    def test_validate_rejects_missing_or_malformed_nodes(self, mutate):
        template = build_minimax_h3_workflow(prompt="t", image_files=[], video_files=[], audio_files=[], seed=7)
        mutate(template)
        with pytest.raises(ComfyUIWorkflowError):
            validate_comfyui_workflows([{"model": "m", "workflow": template}])

    def test_validate_rejects_not_a_list(self):
        with pytest.raises(ComfyUIWorkflowError, match="数组"):
            validate_comfyui_workflows({"model": "m", "workflow": {}})

    def test_validate_rejects_duplicate_and_builtin_models(self):
        template = build_minimax_h3_workflow(prompt="t", image_files=[], video_files=[], audio_files=[], seed=7)
        with pytest.raises(ComfyUIWorkflowError, match="重复"):
            validate_comfyui_workflows([{"model": "m", "workflow": template}, {"model": "m", "workflow": template}])
        with pytest.raises(ComfyUIWorkflowError, match="内置"):
            validate_comfyui_workflows([{"model": "minimax-h3-ref2va", "workflow": template}])


class TestConfiguredWorkflow:
    def test_configured_workflow_preserves_graph_and_receives_runtime_inputs(self):
        template = build_minimax_h3_workflow(prompt="template", image_files=[], video_files=[], audio_files=[], seed=7)
        template["123"]["inputs"]["sampler_name"] = "turbo_sampler"
        template["124"]["inputs"]["steps"] = 8

        from lib.video_backends.comfyui import build_configured_minimax_h3_workflow

        workflow = build_configured_minimax_h3_workflow(
            template,
            prompt="@图片1跑向镜头",
            image_files=["image-1.png"],
            video_files=["video-1.mp4"],
            audio_files=["audio-1.wav"],
            duration=5,
            aspect_ratio="9:16",
            resolution="480p",
            seed=42,
            filename_prefix="video/turbo/test",
        )
        assert workflow["123"]["inputs"]["sampler_name"] == "turbo_sampler"
        assert workflow["124"]["inputs"]["steps"] == 8
        assert workflow["136"]["inputs"]["prompt"] == "<Picture 1> 跑向镜头"
        assert workflow["129"]["inputs"]["noise_seed"] == 42
        assert workflow["92"]["inputs"]["filename_prefix"] == "video/turbo/test"
        assert workflow["136"]["inputs"]["ref_images.ref_image_0"]
        assert workflow["136"]["inputs"]["ref_videos.ref_video_0"]
        assert workflow["136"]["inputs"]["ref_audios.ref_audio_0"]
        # 模板原件不被注入改写
        assert template["136"]["inputs"]["prompt"] == "template"


class TestCapabilities:
    def test_name_and_model(self):
        backend = ComfyUIVideoBackend(base_url=_BASE_URL)
        assert backend.name == PROVIDER_COMFYUI
        assert backend.model == "minimax-h3-ref2va"

    def test_default_model_when_unset(self):
        backend = ComfyUIVideoBackend(base_url=_BASE_URL, model="minimax-h3-ref2va-8step")
        assert backend.model == "minimax-h3-ref2va-8step"

    def test_requires_base_url(self):
        with pytest.raises(ValueError, match="base_url"):
            ComfyUIVideoBackend()

    def test_video_capabilities(self):
        caps = ComfyUIVideoBackend.video_capabilities_for_model("minimax-h3-ref2va")
        assert caps.first_frame is True
        assert caps.last_frame is False
        assert caps.max_reference_images == 9
        assert caps.max_reference_audio_count == 3
        assert caps.audio_track == "always_on"


class TestHappyPath:
    async def test_generate_uploads_submits_polls_and_downloads(self, tmp_path: Path):
        img = _write_file(tmp_path / "ref.png", b"\x89PNG\r\nfake")

        with _comfyui() as routes:
            routes.upload.mock(
                return_value=_json({"name": "image-1.png", "subfolder": "arcreel/task-local", "type": "input"})
            )
            routes.submit.mock(return_value=_prompt_id_response("p-42"))
            routes.poll.mock(return_value=_completed("p-42"))
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4-bytes"))

            backend = ComfyUIVideoBackend(base_url=_BASE_URL, model="minimax-h3-ref2va")
            result = await backend.generate(
                _request(
                    tmp_path,
                    prompt="@图片1一只猫",
                    reference_images=[img],
                    output_path=tmp_path / "out.mp4",
                    seed=7,
                )
            )

        assert result.video_path == tmp_path / "out.mp4"
        assert result.video_path.read_bytes() == b"mp4-bytes"
        assert result.provider == PROVIDER_COMFYUI
        assert result.model == "minimax-h3-ref2va"
        assert result.duration_seconds == 5
        assert result.task_id == "p-42"
        assert result.generate_audio is True

        # 上传表单带 type/subfolder/overwrite，body 为二进制素材
        uploaded = only_request(routes.upload)
        assert 'name="image-1.png"' in uploaded.content.decode("utf-8", errors="replace")

        submitted = only_request(routes.submit)
        body = request_json(submitted)
        assert body["client_id"] == "arcreel-local"
        workflow = body["prompt"]
        assert workflow["136"]["class_type"] == "MiniMaxH3ReferenceToVideo"
        assert workflow["136"]["inputs"]["prompt"] == "<Picture 1> 一只猫"
        assert workflow["136"]["inputs"]["ref_images.ref_image_0"][0] == "200"
        assert workflow["92"]["class_type"] == "SaveVideo"

        # 下载打 /view，URL 带 filename/subfolder/type
        downloaded = only_request(routes.download)
        assert downloaded.url.query == b"filename=p-42.mp4&subfolder=video%2FMiniMax_H3&type=output"
        assert "Authorization" not in downloaded.headers

    async def test_text_to_video_without_reference_images(self, tmp_path: Path):
        """无参考图即文生视频：不上传素材，直接提交模板。"""
        with _comfyui() as routes:
            routes.submit.mock(return_value=_prompt_id_response("p-t2v"))
            routes.poll.mock(return_value=_completed("p-t2v"))
            routes.download.mock(return_value=httpx.Response(200, content=b"v"))

            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            await backend.generate(_request(tmp_path, prompt="纯文本生成"))

            assert routes.upload.call_count == 0

        workflow = _sent_workflow(routes)
        assert workflow["136"]["inputs"]["prompt"] == "纯文本生成"
        assert "200" not in workflow

    async def test_start_image_joins_reference_group(self, tmp_path: Path):
        start = _write_file(tmp_path / "start.png")
        ref = _write_file(tmp_path / "ref.png")

        with _comfyui() as routes:
            routes.upload.mock(
                side_effect=[
                    _json({"name": "image-1.png", "subfolder": "arcreel/task-local", "type": "input"}),
                    _json({"name": "image-2.png", "subfolder": "arcreel/task-local", "type": "input"}),
                ]
            )
            routes.submit.mock(return_value=_prompt_id_response("p-s"))
            routes.poll.mock(return_value=_completed("p-s"))
            routes.download.mock(return_value=httpx.Response(200, content=b"v"))

            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            await backend.generate(_request(tmp_path, start_image=start, reference_images=[ref]))

        workflow = _sent_workflow(routes)
        # 首帧并入参考组：ref_image_0 是首帧、ref_image_1 是参考图
        assert workflow["136"]["inputs"]["ref_images.ref_image_0"] == ["200", 0]
        assert workflow["136"]["inputs"]["ref_images.ref_image_1"] == ["201", 0]


class TestPollStates:
    async def test_polls_through_in_progress(self, tmp_path: Path):
        with _comfyui() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_prompt_id_response("p3"))
            routes.poll.mock(side_effect=[_in_progress(), _in_progress(), _completed("p3")])
            routes.download.mock(return_value=httpx.Response(200, content=b"v"))

            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

            assert routes.poll.call_count == 3
            assert routes.download.call_count == 1

        assert result.task_id == "p3"

    async def test_polls_through_running_status_entry(self, tmp_path: Path):
        """/history 对执行中的任务返回 running 条目（非空 {}）：继续轮询而非误判完成。"""
        running = _json(
            {
                "p-run": {
                    "status": {"status_str": "running", "completed": False, "messages": []},
                    "outputs": {},
                }
            }
        )
        with _comfyui() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_prompt_id_response("p-run"))
            routes.poll.mock(side_effect=[running, _completed("p-run")])
            routes.download.mock(return_value=httpx.Response(200, content=b"v"))

            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

            assert routes.poll.call_count == 2
            assert routes.download.call_count == 1

        assert result.task_id == "p-run"

    async def test_polling_timeout_raises(self, tmp_path: Path):
        with _comfyui() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_prompt_id_response("p-t"))
            routes.poll.mock(return_value=_in_progress())

            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            with pytest.raises(TimeoutError, match="ComfyUI"):
                await backend.generate(_request(tmp_path))

            assert routes.poll.call_count > 1
            assert routes.download.call_count == 0

    async def test_failed_status_raises_with_history_error(self, tmp_path: Path):
        with _comfyui() as routes:
            routes.submit.mock(return_value=_prompt_id_response("p-f"))
            routes.poll.mock(return_value=_failed("p-f"))

            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            with pytest.raises(RuntimeError, match="CUDA out of memory"):
                await backend.generate(_request(tmp_path))

            assert routes.download.call_count == 0

    async def test_completed_without_video_output_raises(self, tmp_path: Path):
        with _comfyui() as routes:
            routes.submit.mock(return_value=_prompt_id_response("p-novideo"))
            routes.poll.mock(
                return_value=_json(
                    {
                        "p-novideo": {
                            "status": {"status_str": "success", "completed": True, "messages": []},
                            "outputs": {},
                        }
                    }
                )
            )

            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            with pytest.raises(RuntimeError, match="没有找到视频输出"):
                await backend.generate(_request(tmp_path))

            assert routes.download.call_count == 0


class TestSubmitResilience:
    async def test_submit_retries_on_503(self, tmp_path: Path):
        busy = httpx.Response(503, text="Service busy")

        with _comfyui() as routes, bounded_poll_clock():
            routes.submit.mock(side_effect=[busy, busy, _prompt_id_response("p-r")])
            routes.poll.mock(return_value=_completed("p-r"))
            routes.download.mock(return_value=httpx.Response(200, content=b"v"))

            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

            assert routes.submit.call_count == 3

        assert result.task_id == "p-r"

    async def test_submit_400_node_errors_fails_fast(self, tmp_path: Path):
        with _comfyui() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_json({"node_errors": {"136": "missing input"}}, status_code=400))

            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            with pytest.raises(httpx.HTTPStatusError):
                await backend.generate(_request(tmp_path))

            assert routes.submit.call_count == 1
            assert routes.poll.call_count == 0

    async def test_submit_read_timeout_wraps_ambiguous(self, tmp_path: Path):
        with _comfyui() as routes, bounded_poll_clock():
            routes.submit.mock(side_effect=httpx.ReadTimeout("read timed out"))

            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            with pytest.raises(AmbiguousSubmitError):
                await backend.generate(_request(tmp_path))

            assert routes.submit.call_count == 1


class TestValidationFailures:
    async def test_missing_reference_image_fails_loud(self, tmp_path: Path):
        with _comfyui() as routes:
            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            with pytest.raises(Exception) as ei:
                await backend.generate(_request(tmp_path, reference_images=[tmp_path / "missing.png"]))

            assert getattr(ei.value, "code", None) == "video_reference_images_unreadable"
            assert routes.submit.call_count == 0

    async def test_missing_audio_fails_loud(self, tmp_path: Path):
        with _comfyui() as routes:
            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            with pytest.raises(Exception) as ei:
                await backend.generate(_request(tmp_path, reference_audio_files=[tmp_path / "missing.wav"]))

            assert getattr(ei.value, "code", None) == "video_reference_audio_unreadable"
            assert routes.submit.call_count == 0

    async def test_reference_images_exceeded_fails_loud(self, tmp_path: Path):
        refs = [_write_file(tmp_path / f"r{i}.png") for i in range(10)]

        with _comfyui() as routes:
            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            with pytest.raises(Exception) as ei:
                await backend.generate(_request(tmp_path, reference_images=refs))

            assert getattr(ei.value, "code", None) == "video_reference_images_exceeded"
            assert routes.upload.call_count == 0
            assert routes.submit.call_count == 0

    @pytest.mark.parametrize("duration", [0, 4, 16, 30])
    async def test_out_of_range_duration_fails_loud(self, tmp_path: Path, duration: int):
        with _comfyui() as routes:
            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            with pytest.raises(Exception) as ei:
                await backend.generate(_request(tmp_path, duration_seconds=duration))

            assert getattr(ei.value, "code", None) == "video_duration_not_supported"
            assert routes.submit.call_count == 0

    async def test_non_builtin_model_without_configured_workflow_raises(self, tmp_path: Path):
        with _comfyui() as routes:
            routes.submit.mock(return_value=_prompt_id_response("p-x"))
            routes.poll.mock(return_value=_completed("p-x"))
            routes.download.mock(return_value=httpx.Response(200, content=b"v"))

            backend = ComfyUIVideoBackend(base_url=_BASE_URL, model="minimax-h3-turbo")
            with pytest.raises(ComfyUIWorkflowError, match="没有绑定工作流"):
                await backend.generate(_request(tmp_path))

            assert routes.submit.call_count == 0

    async def test_configured_model_uses_override_template(self, tmp_path: Path):
        template = build_minimax_h3_workflow(prompt="t", image_files=[], video_files=[], audio_files=[], seed=7)
        template["124"]["inputs"]["steps"] = 6

        with _comfyui() as routes:
            routes.upload.mock(
                return_value=_json({"name": "image-1.png", "subfolder": "arcreel/task-local", "type": "input"})
            )
            routes.submit.mock(return_value=_prompt_id_response("p-cfg"))
            routes.poll.mock(return_value=_completed("p-cfg"))
            routes.download.mock(return_value=httpx.Response(200, content=b"v"))

            backend = ComfyUIVideoBackend(
                base_url=_BASE_URL,
                model="minimax-h3-turbo",
                configured_workflows=[{"model": "minimax-h3-turbo", "workflow": template}],
            )
            result = await backend.generate(
                _request(tmp_path, prompt="@图片1测试", reference_images=[_write_file(tmp_path / "r.png")])
            )

        workflow = _sent_workflow(routes)
        assert workflow["124"]["inputs"]["steps"] == 6
        assert workflow["136"]["inputs"]["prompt"] == "<Picture 1> 测试"
        assert result.task_id == "p-cfg"


class TestResume:
    async def test_resume_polls_and_downloads_without_submit(self, tmp_path: Path):
        with _comfyui() as routes:
            routes.poll.mock(return_value=_completed("p-resume"))
            routes.download.mock(return_value=httpx.Response(200, content=b"resumed"))

            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            result = await backend.resume_video("p-resume", _request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert routes.submit.call_count == 0
            assert only_request(routes.poll).url.path.endswith("/history/p-resume")

        assert result.task_id == "p-resume"
        assert (tmp_path / "out.mp4").read_bytes() == b"resumed"

    async def test_resume_404_raises_resume_expired_without_retry(self, tmp_path: Path):
        with _comfyui() as routes:
            routes.poll.mock(return_value=_json({"error": "not found"}, status_code=404))

            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            with pytest.raises(ResumeExpiredError) as ei:
                await backend.resume_video("p-404", _request(tmp_path))

            assert ei.value.job_id == "p-404"
            assert ei.value.provider == PROVIDER_COMFYUI
            assert routes.poll.call_count == 1

    async def test_resume_polls_submitted_base_url_after_config_change(self, tmp_path: Path):
        """在途改 base_url 后续跑：轮询与下载仍打提交时的域名，而非当下配置解析出的域名。"""
        with _comfyui(base_url="https://comfy-a.example.com:8188") as routes_a:
            routes_a.poll.mock(return_value=_completed("p-replay"))
            routes_a.download.mock(return_value=httpx.Response(200, content=b"replayed"))

            backend = ComfyUIVideoBackend(base_url="https://comfy-b.example.com:8188")
            result = await backend.resume_video(
                "p-replay",
                _request(
                    tmp_path,
                    output_path=tmp_path / "out.mp4",
                    submitted_base_url="https://comfy-a.example.com:8188",
                ),
            )

            assert only_request(routes_a.poll).url.path == "/history/p-replay"
            assert routes_a.download.call_count == 1

        assert result.task_id == "p-replay"
        assert (tmp_path / "out.mp4").read_bytes() == b"replayed"


class TestProviderJobIdPersistence:
    async def test_persists_prompt_id_for_worker_request(self, tmp_path: Path):
        with _comfyui() as routes, captured_provider_job_ids() as persisted:
            routes.upload.mock(
                return_value=_json({"name": "image-1.png", "subfolder": "arcreel/task-w", "type": "input"})
            )
            routes.submit.mock(return_value=_prompt_id_response("comfy-prompt-42"))
            routes.poll.mock(return_value=_completed("comfy-prompt-42"))
            routes.download.mock(return_value=httpx.Response(200, content=b"v"))

            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            await backend.generate(
                _request(
                    tmp_path,
                    task_id="worker-task-99",
                    reference_images=[_write_file(tmp_path / "r.png")],
                )
            )

        assert persisted == [
            {
                "task_id": "worker-task-99",
                "job_id": "comfy-prompt-42",
                "provider": PROVIDER_COMFYUI,
                # 提交域名落 base_url 位供续跑回放；comfyui 非协议类自定义后端，endpoint 位保持 None。
                "endpoint": None,
                "base_url": _BASE_URL,
            }
        ]

    async def test_non_worker_request_skips_persistence(self, tmp_path: Path):
        with _comfyui() as routes, captured_provider_job_ids() as persisted:
            routes.submit.mock(return_value=_prompt_id_response("comfy-prompt-1"))
            routes.poll.mock(return_value=_completed("comfy-prompt-1"))
            routes.download.mock(return_value=httpx.Response(200, content=b"v"))

            backend = ComfyUIVideoBackend(base_url=_BASE_URL)
            await backend.generate(_request(tmp_path))

        assert persisted == []
