"""ComfyUI 视频通道：上传、提交、轮询、产物入库与四个失败码。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.custom_provider.comfyui.request_builder import workflow_sha256
from lib.custom_provider.comfyui_backend import ComfyuiVideoBackend
from lib.custom_provider.endpoint_definition import validate_definition
from lib.generation_worker import _encode_task_failure_message
from lib.task_failure import render_failure
from lib.video_backends.base import ProviderResponseStage, VideoGenerationRequest
from tests.factories import comfyui_endpoint_definition, make_translator
from tests.fakes import bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http, only_request, request_json

BASE_URL = "https://comfy.test"


def _definition(**overrides: Any) -> dict[str, Any]:
    definition = comfyui_endpoint_definition(**overrides)
    assert validate_definition(definition).valid
    return definition


def _backend(definition: dict[str, Any] | None = None, *, api_key: str = "") -> ComfyuiVideoBackend:
    return ComfyuiVideoBackend(
        provider_id="custom-1",
        model="wan-t2v",
        base_url=BASE_URL,
        api_key=api_key,
        definition=definition if definition is not None else _definition(),
    )


def _request(tmp_path: Path, **overrides: Any) -> VideoGenerationRequest:
    values: dict[str, Any] = {
        "prompt": "一只猫走过屋顶",
        "output_path": tmp_path / "out.mp4",
        "aspect_ratio": "9:16",
        "duration_seconds": 5,
        "task_id": "task-7",
    }
    values.update(overrides)
    return VideoGenerationRequest(**values)


def _history(outputs: dict[str, Any], *, completed: bool = True) -> dict[str, Any]:
    return {"status": {"completed": completed, "status_str": "success"}, "outputs": outputs}


def _video_output(filename: str = "final_00001.mp4") -> dict[str, Any]:
    return {"images": [], "gifs": [{"filename": filename, "subfolder": "video", "type": "output"}]}


def _with_image_bindings() -> dict[str, Any]:
    """在最小定义上补首帧与两个参考图格子，把上传那一段带进来。"""
    definition = comfyui_endpoint_definition()
    definition["workflow"]["10"] = {"class_type": "LoadImage", "inputs": {"image": "draft.png"}}
    definition["workflow"]["11"] = {"class_type": "LoadImage", "inputs": {"image": "ref-a.png"}}
    definition["workflow"]["12"] = {"class_type": "LoadImage", "inputs": {"image": "ref-b.png"}}
    definition["bindings"]["start_image"] = [{"node": "10", "input": "image", "class_type": "LoadImage"}]
    definition["bindings"]["reference_images"] = [
        {"node": "11", "input": "image", "class_type": "LoadImage"},
        {"node": "12", "input": "image", "class_type": "LoadImage"},
    ]
    assert validate_definition(definition).valid
    return definition


class TestGenerate:
    async def test_a_generation_uploads_submits_polls_and_stores_the_artifact(self, tmp_path: Path):
        """端到端一条：素材回填 → 提交 → 轮询 → 产物落到 output_path，溯源随结果回来。"""
        definition = _with_image_bindings()
        start = tmp_path / "first.png"
        start.write_bytes(b"png-bytes")
        reference = tmp_path / "ref.jpg"
        reference.write_bytes(b"jpg-bytes")

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            upload = router.post(f"{BASE_URL}/upload/image").mock(
                side_effect=[
                    httpx.Response(200, json={"name": "task-7-start_image.png", "subfolder": "arcreel"}),
                    httpx.Response(200, json={"name": "task-7-reference_images-1.jpg", "subfolder": "arcreel"}),
                ]
            )
            submit = router.post(f"{BASE_URL}/prompt").mock(
                return_value=httpx.Response(200, json={"prompt_id": "p-1", "node_errors": {}})
            )
            history = router.get(f"{BASE_URL}/history/p-1").mock(
                side_effect=[
                    httpx.Response(200, json={}),
                    httpx.Response(200, json={"p-1": _history({"9": _video_output()})}),
                ]
            )
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"mp4-bytes"))

            result = await _backend(definition).generate(
                _request(tmp_path, start_image=start, reference_images=[reference])
            )

        assert result.video_path.read_bytes() == b"mp4-bytes"
        assert result.task_id == "p-1"
        assert upload.call_count == 2
        assert history.call_count == 2
        # 引用值取响应里的 subfolder / name，不是请求里的——服务端会为重名改名。
        submitted = request_json(submit.calls.last.request)["prompt"]
        assert submitted["10"]["inputs"]["image"] == "arcreel/task-7-start_image.png"
        assert submitted["11"]["inputs"]["image"] == "arcreel/task-7-reference_images-1.jpg"
        assert request_json(submit.calls.last.request)["client_id"] == "arcreel-task-7"
        assert view.calls.last.request.url.params["filename"] == "final_00001.mp4"
        assert persisted == [
            {
                "task_id": "task-7",
                "job_id": "p-1",
                "provider": "custom-1",
                "endpoint": None,
                "base_url": BASE_URL,
            }
        ]

    async def test_the_version_metadata_gets_the_actual_seed_and_the_workflow_fingerprint(self, tmp_path: Path):
        """两者都只有生成过一次才知道：种子是提交那一刻现随机的，指纹是那份实发 workflow 的。"""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            submit = router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"mp4"))

            result = await _backend().generate(_request(tmp_path))

        sent = request_json(submit.calls.last.request)["prompt"]
        assert result.seed == sent["3"]["inputs"]["seed"]
        assert result.provenance == {"workflow_sha256": workflow_sha256(sent)}

    async def test_an_unbound_duration_is_forwarded_untouched(self, tmp_path: Path):
        """``frames`` 未绑定即时长不由 ArcReel 驱动：照常提交，帧数保持 workflow 字面值。"""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            submit = router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"mp4"))

            result = await _backend().generate(_request(tmp_path, duration_seconds=5))

        assert "frames" not in _definition()["bindings"]
        assert result.video_path.exists()
        assert request_json(submit.calls.last.request)["prompt"]["9"]["inputs"]["fps"] == 16

    async def test_only_bound_and_supplied_media_is_uploaded(self, tmp_path: Path):
        """多出来的参考图一张都不传：格子数就是这份 workflow 能收几张，多传只是白占带宽。"""
        definition = _with_image_bindings()
        references = []
        for index in range(4):
            path = tmp_path / f"ref{index}.png"
            path.write_bytes(b"png")
            references.append(path)

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            upload = router.post(f"{BASE_URL}/upload/image").mock(
                return_value=httpx.Response(200, json={"name": "stored.png", "subfolder": "arcreel"})
            )
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"mp4"))

            await _backend(definition).generate(_request(tmp_path, reference_images=references))

        # 两个格子传两张；首帧绑定了但这次没给，不传。
        assert upload.call_count == 2

    async def test_a_root_level_history_entry_is_accepted(self, tmp_path: Path):
        """两种形状都得认：不同版本与代理各回一种，只认包裹那种会让另一半部署永远等不到终态。"""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"mp4"))

            result = await _backend().generate(_request(tmp_path))

        assert result.video_path.read_bytes() == b"mp4"

    async def test_credentials_render_once_and_ride_every_route(self, tmp_path: Path):
        definition = _with_image_bindings()
        definition["auth"] = {"headers": {"Authorization": "Bearer {{api_key}}"}}
        assert validate_definition(definition).valid
        start = tmp_path / "first.png"
        start.write_bytes(b"png")

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            upload = router.post(f"{BASE_URL}/upload/image").mock(
                return_value=httpx.Response(200, json={"name": "stored.png", "subfolder": "arcreel"})
            )
            submit = router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            history = router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"mp4"))

            await _backend(definition, api_key="k-1").generate(_request(tmp_path, start_image=start))

        for route in (upload, submit, history, view):
            assert route.calls.last.request.headers["authorization"] == "Bearer k-1"

    async def test_query_credentials_ride_every_route_too(self, tmp_path: Path):
        """按 query 传凭证的反代同样要认：拼进每一条路由的 URL，不只是头那一张表。"""
        definition = _definition(auth={"query": {"token": "{{api_key}}"}})

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            submit = router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            history = router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"mp4"))

            await _backend(definition, api_key="k-1").generate(_request(tmp_path))

        for route in (submit, history, view):
            assert route.calls.last.request.url.params["token"] == "k-1"
        # 产物地址自己的三个参数不被凭证挤掉。
        assert view.calls.last.request.url.params["filename"] == "final_00001.mp4"

    @pytest.mark.parametrize("route", ["upload", "submit", "history", "view"])
    async def test_a_cross_origin_redirect_never_takes_the_credential_along(self, tmp_path: Path, route: str):
        """反代把某条路由 302 到别处时凭证不许跟过去。

        httpx 的 follow_redirects 跨源只摘 ``Authorization``，而 auth 节允许任意头名——交给它
        自动跟随，一次指向对象存储的 ``/view`` 跳转就会把 ``X-API-Key`` 送进第三方的访问日志。
        """
        definition = _with_image_bindings()
        definition["auth"] = {"headers": {"X-API-Key": "{{api_key}}"}}
        assert validate_definition(definition).valid
        start = tmp_path / "first.png"
        start.write_bytes(b"png")
        elsewhere = "https://elsewhere.test"
        redirect = httpx.Response(307, headers={"location": f"{elsewhere}/moved"})

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            moved_post = router.post(f"{elsewhere}/moved")
            moved_get = router.get(f"{elsewhere}/moved")
            upload = router.post(f"{BASE_URL}/upload/image").mock(
                return_value=redirect
                if route == "upload"
                else httpx.Response(200, json={"name": "stored.png", "subfolder": "arcreel"})
            )
            moved_post.mock(
                return_value=httpx.Response(200, json={"name": "stored.png", "subfolder": "arcreel"})
                if route == "upload"
                else httpx.Response(200, json={"prompt_id": "p-1"})
            )
            router.post(f"{BASE_URL}/prompt").mock(
                return_value=redirect if route == "submit" else httpx.Response(200, json={"prompt_id": "p-1"})
            )
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=redirect
                if route == "history"
                else httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(
                return_value=redirect if route == "view" else httpx.Response(200, content=b"mp4")
            )
            moved_get.mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
                if route == "history"
                else httpx.Response(200, content=b"mp4")
            )

            await _backend(definition, api_key="secret").generate(_request(tmp_path, start_image=start))

        followed = moved_post if route in {"upload", "submit"} else moved_get
        assert followed.call_count == 1
        assert "x-api-key" not in followed.calls.last.request.headers
        # 同源那一跳仍要带上，否则套了反代的部署一条都发不出去。
        assert upload.calls.last.request.headers["x-api-key"] == "secret"

    async def test_a_same_origin_redirect_keeps_the_query_credential(self, tmp_path: Path):
        """``Location`` 整串替换查询串，凭证不补回就会在一次 ``/view`` → ``/view/`` 规范化跳转上丢掉。"""
        definition = _definition(auth={"query": {"token": "{{api_key}}"}})

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(
                return_value=httpx.Response(302, headers={"location": f"{BASE_URL}/files/final.mp4"})
            )
            moved = router.get(f"{BASE_URL}/files/final.mp4").mock(return_value=httpx.Response(200, content=b"mp4"))

            await _backend(definition, api_key="k-1").generate(_request(tmp_path))

        assert moved.calls.last.request.url.params["token"] == "k-1"

    async def test_an_empty_api_key_leaves_the_auth_section_unrendered(self, tmp_path: Path):
        """ComfyUI 原生无鉴权：发一个空的 ``Bearer `` 只会让反代以 401 拒掉本该放行的请求。"""
        definition = _definition(auth={"headers": {"Authorization": "Bearer {{api_key}}"}})

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            submit = router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"mp4"))

            await _backend(definition, api_key="").generate(_request(tmp_path))

        assert "authorization" not in submit.calls.last.request.headers

    async def test_resume_is_refused_until_it_lands(self, tmp_path: Path):
        """续跑未落地：抛 NotImplementedError 让孤儿处置标记，而不是在用户显卡上重跑一遍。"""
        with pytest.raises(NotImplementedError):
            await _backend().resume_video("p-1", _request(tmp_path))


class TestFailures:
    async def test_an_upload_failure_stops_before_any_submit(self, tmp_path: Path):
        definition = _with_image_bindings()
        start = tmp_path / "first.png"
        start.write_bytes(b"png")

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/upload/image").mock(return_value=httpx.Response(507, text="disk full"))
            submit = router.post(f"{BASE_URL}/prompt")

            with pytest.raises(ComfyuiError) as caught:
                await _backend(definition).generate(_request(tmp_path, start_image=start))

        assert caught.value.code == "comfyui_upload_failed"
        assert submit.call_count == 0

    async def test_node_errors_on_a_200_fail_the_task_without_polling(self, tmp_path: Path):
        """图提交上去了、节点参数却过不了校验：进轮询只会等到超时。"""
        node_errors = {
            "3": {"class_type": "KSampler", "errors": [{"message": "value 4096 out of range"}]},
            "4": {"class_type": "CheckpointLoaderSimple", "errors": [{"message": "model not found"}]},
        }

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            router.post(f"{BASE_URL}/prompt").mock(
                return_value=httpx.Response(200, json={"prompt_id": "p-1", "node_errors": node_errors})
            )
            history = router.get(f"{BASE_URL}/history/p-1")

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_node_errors"
        assert caught.value.params == {"nodes": 2, "summary": "KSampler: value 4096 out of range"}
        assert history.call_count == 0
        assert persisted == []

    async def test_a_400_carries_the_same_failure_code(self, tmp_path: Path):
        """400 与 200 带 node_errors 同因，摘要取用户在画布上看到的那个标题。"""
        body = {"error": {"message": "Prompt has no outputs"}, "node_errors": {"6": {"class_type": "CLIPTextEncode"}}}

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(400, json=body))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_node_errors"
        assert caught.value.params == {"nodes": 1, "summary": "正向"}

    async def test_a_400_without_node_errors_falls_back_to_the_error_message(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(
                return_value=httpx.Response(400, json={"error": {"message": "prompt outputs failed validation"}})
            )

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.params == {"nodes": 0, "summary": "prompt outputs failed validation"}

    async def test_a_failed_job_id_persistence_stops_before_polling(self, tmp_path: Path):
        """job_id 没落库就进轮询，进程一重启这笔已在跑的任务就再也找不回来。"""

        async def _boom(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("db is down")

        with capture_http() as router, bounded_poll_clock(), pytest.MonkeyPatch.context() as patch:
            patch.setattr("lib.video_backends.base.persist_provider_job_id", _boom)
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            history = router.get(f"{BASE_URL}/history/p-1")

            with pytest.raises(RuntimeError, match="db is down"):
                await _backend().generate(_request(tmp_path))

        assert history.call_count == 0

    async def test_an_output_node_without_artifacts_is_refused(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history({"9": {}})))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_missing"
        assert caught.value.params == {"nodes": "9"}

    async def test_temp_artifacts_do_not_count_as_output(self, tmp_path: Path):
        """``temp`` 是中间预览、服务端随时会清掉它；只认 ``type == "output"``。"""
        outputs = {"9": {"gifs": [{"filename": "preview.mp4", "subfolder": "", "type": "temp"}]}}

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_missing"

    async def test_artifacts_of_an_unbound_node_are_not_taken(self, tmp_path: Path):
        """旁支的 ``PreviewImage`` 同样会往 history 写产物，扫全图会把预览图当成成片取走。"""
        outputs = {"99": _video_output("preview.mp4")}

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_missing"

    async def test_a_still_image_from_a_video_endpoint_is_refused(self, tmp_path: Path):
        outputs = {"9": {"images": [{"filename": "final_00001.png", "subfolder": "", "type": "output"}]}}

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_type_mismatch"
        assert caught.value.params == {"filename": "final_00001.png", "media_type": "video"}

    @pytest.mark.parametrize(
        ("code", "params"),
        [
            ("comfyui_upload_failed", {"detail": "disk full"}),
            ("comfyui_node_errors", {"nodes": 2, "summary": "KSampler: out of range"}),
            ("comfyui_output_missing", {"nodes": "9"}),
            ("comfyui_output_type_mismatch", {"filename": "a.png", "media_type": "video"}),
        ],
    )
    @pytest.mark.parametrize("locale", ["zh", "en", "vi"])
    def test_every_failure_code_renders_in_every_locale(self, code: str, params: dict[str, Any], locale: str):
        """落库只存机器码，读侧按 Accept-Language 渲染；三语缺一就有用户看到裸码。"""
        message = _encode_task_failure_message(ComfyuiError(code, **params))

        rendered = render_failure(message, make_translator(locale))

        assert rendered
        assert code not in rendered


class TestMultipleArtifacts:
    async def test_the_first_artifact_is_taken_and_the_rest_are_reported(self, tmp_path: Path, caplog):
        outputs = {
            "9": {
                "gifs": [
                    {"filename": "final_00001.mp4", "subfolder": "video", "type": "output"},
                    {"filename": "final_00002.mp4", "subfolder": "video", "type": "output"},
                ]
            }
        }

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"mp4"))

            with caplog.at_level("WARNING"):
                await _backend().generate(_request(tmp_path))

        assert only_request(view).url.params["filename"] == "final_00001.mp4"
        assert "共 2 个" in caplog.text


class TestDiagnostics:
    async def test_the_recorded_history_keeps_only_the_status_and_the_output_node(self, tmp_path: Path):
        """整份 history 带着每个节点的全部产出，一条留痕就能把诊断列撑到几百 KB。"""
        recorded: list[tuple[ProviderResponseStage, object]] = []

        async def _record(stage: ProviderResponseStage, body: object) -> None:
            recorded.append((stage, body))

        outputs = {"9": _video_output(), "99": {"images": [{"filename": "noise.png", "type": "temp"}]}}

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"mp4"))

            await _backend().generate(_request(tmp_path, on_provider_response=_record))

        polled = [body for stage, body in recorded if stage == "poll"]
        assert polled == [{"status": {"completed": True, "status_str": "success"}, "outputs": {"9": _video_output()}}]
        assert [stage for stage, _ in recorded] == ["submit", "poll", "result"]
