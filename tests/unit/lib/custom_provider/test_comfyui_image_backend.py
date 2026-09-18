"""ComfyUI 图像通道：能力推导、参考图上传、产物白名单、不续跑与叫停远端。

提交之后那一段（轮询、终态判定、判丢失、留痕摘要）与视频通道共用
``lib.custom_provider.comfyui_execution``，已由 ``test_comfyui_video_backend.py`` 逐条钉住；本文件
只覆盖图像这一侧独有的部分，以及那份共用实现在图像通道上必须仍然成立的几处（白名单、叫停）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.custom_provider.comfyui_image_backend import ComfyuiImageBackend
from lib.custom_provider.endpoint_definition import validate_definition
from lib.custom_provider.endpoint_resolution import endpoint_spec_from_row
from lib.custom_provider.factory import create_custom_backend
from lib.image_backends.base import ImageCapability, ImageGenerationRequest, ReferenceImage
from tests.factories import comfyui_endpoint_definition
from tests.http_capture import capture_http, only_request, request_json

BASE_URL = "https://comfy.test"


def _definition(*, reference_slots: int = 0) -> dict[str, Any]:
    """一份最小可用的图像端点定义：产物节点存图，可带若干参考图格子。

    图像端点的语义键里没有首尾帧与帧数帧率（见 ``comfyui.bindings.IMAGE_BINDING_KEYS``），故绑定
    表整张重写而不是在视频那份上删几个键——后者留下的 ``output`` 仍指着一个存视频的节点。
    """
    definition = comfyui_endpoint_definition(media_type="image")
    workflow = definition["workflow"]
    workflow["9"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["8", 0], "filename_prefix": "ArcReel"},
        "_meta": {"title": "存图"},
    }
    bindings: dict[str, Any] = {
        "prompt": [{"node": "6", "input": "text", "class_type": "CLIPTextEncode", "title": "正向"}],
        "negative_prompt": [{"node": "7", "input": "text", "class_type": "CLIPTextEncode", "title": "负向"}],
        "width": [{"node": "5", "input": "width", "class_type": "EmptyLatentImage", "step": 16}],
        "height": [{"node": "5", "input": "height", "class_type": "EmptyLatentImage", "step": 16}],
        "seed": [{"node": "3", "input": "seed", "class_type": "KSampler", "policy": "random"}],
        "output": [{"node": "9", "class_type": "SaveImage", "title": "存图"}],
    }
    if reference_slots:
        targets = []
        for slot in range(reference_slots):
            node_id = str(20 + slot)
            workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": f"draft-{slot}.png"}}
            targets.append({"node": node_id, "input": "image", "class_type": "LoadImage"})
        bindings["reference_images"] = targets
    definition["bindings"] = bindings
    assert validate_definition(definition).valid
    return definition


def _backend(definition: dict[str, Any] | None = None, *, api_key: str = "") -> ComfyuiImageBackend:
    return ComfyuiImageBackend(
        provider_id="custom-1",
        model="flux-workflow",
        base_url=BASE_URL,
        api_key=api_key,
        definition=definition if definition is not None else _definition(),
        job_label="job-7",
    )


def _request(tmp_path: Path, **overrides: Any) -> ImageGenerationRequest:
    values: dict[str, Any] = {"prompt": "一只猫走过屋顶", "output_path": tmp_path / "out.png", "aspect_ratio": "9:16"}
    values.update(overrides)
    return ImageGenerationRequest(**values)


def _history(outputs: dict[str, Any]) -> dict[str, Any]:
    return {"status": {"completed": True, "status_str": "success"}, "outputs": outputs}


def _image_output(filename: str = "ArcReel_00001_.png") -> dict[str, Any]:
    return {"images": [{"filename": filename, "subfolder": "", "type": "output"}]}


def _cancel_here(_request: httpx.Request) -> httpx.Response:
    """让这一条路由的请求撞上一次任务取消。

    ``CancelledError`` 继承 ``BaseException``，respx 的异常型 ``side_effect`` 只收 ``Exception``，
    故经可调用的那一路抛。
    """
    raise asyncio.CancelledError


def _queue(*, running: tuple[str, ...] = (), pending: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "queue_running": [[index, job, {}, [], {}] for index, job in enumerate(running)],
        "queue_pending": [[index, job, {}, [], {}] for index, job in enumerate(pending)],
    }


class TestDeclaredCapabilities:
    """参考图格子有没有，决定这份 workflow 是纯文生图还是纯图生图，两者互斥（``docs/adr/0082``）。"""

    def test_a_workflow_without_reference_slots_is_text_to_image_only(self):
        backend = _backend()

        assert backend.capabilities == {ImageCapability.TEXT_TO_IMAGE}
        assert backend.max_reference_images == 0

    def test_a_workflow_with_reference_slots_is_image_to_image_only(self):
        """有格子却不给图，构造层要么删读图节点要么提交一张空图——都不是「也支持文生图」。"""
        backend = _backend(_definition(reference_slots=2))

        assert backend.capabilities == {ImageCapability.IMAGE_TO_IMAGE}
        assert backend.max_reference_images == 2

    @pytest.mark.parametrize(
        ("reference_slots", "expected"),
        [(0, ["text_to_image"]), (3, ["image_to_image"])],
    )
    def test_the_endpoint_projection_says_the_same_thing(self, reference_slots: int, expected: list[str]):
        """模型行进哪个桶读端点投影，发请求之前的兜底闸门读 backend 自己那份声明。

        两处各写一份判据就会打架：一个进得了 i2i 桶的端点会在闸门上被自己挡掉。
        """
        definition = _definition(reference_slots=reference_slots)
        spec = endpoint_spec_from_row(cast("Any", SimpleNamespace(id=7, definition=definition)))

        assert sorted(cap.value for cap in spec.image_capabilities or frozenset()) == expected

    def test_the_factory_wraps_an_image_backend_in_pure_forwarding(self):
        """图像那一路的包装不注入能力：ComfyUI 协议的 capability_overrides 整节关闭。"""
        from lib.custom_provider.backends import CustomImageBackend

        definition = _definition(reference_slots=1)
        spec = endpoint_spec_from_row(cast("Any", SimpleNamespace(id=7, definition=definition)))
        provider = SimpleNamespace(provider_id="custom-1", base_url=BASE_URL, api_key="")

        backend = create_custom_backend(
            provider=cast("Any", provider), model_id="flux-workflow", endpoint="ce-7", endpoint_spec=spec
        )

        assert isinstance(backend, CustomImageBackend)
        assert backend.capabilities == {ImageCapability.IMAGE_TO_IMAGE}
        assert backend.max_reference_images == 1


class TestGenerate:
    async def test_an_image_to_image_generation_uploads_submits_polls_and_stores_the_artifact(self, tmp_path: Path):
        """端到端一条：参考图回填 → 提交 → 轮询 → 产物落到 output_path，实发种子随结果回来。"""
        definition = _definition(reference_slots=2)
        reference = tmp_path / "ref.jpg"
        reference.write_bytes(b"jpg-bytes")

        with capture_http() as router:
            upload = router.post(f"{BASE_URL}/upload/image").mock(
                return_value=httpx.Response(200, json={"name": "job-7-reference_images-1.jpg", "subfolder": "arcreel"})
            )
            submit = router.post(f"{BASE_URL}/prompt").mock(
                return_value=httpx.Response(200, json={"prompt_id": "p-1", "node_errors": {}})
            )
            history = router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json={"p-1": _history({"9": _image_output()})})
            )
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"png-bytes"))

            result = await _backend(definition).generate(
                _request(tmp_path, reference_images=[ReferenceImage(path=str(reference))])
            )

        assert result.image_path.read_bytes() == b"png-bytes"
        assert (result.provider, result.model) == ("custom-1", "flux-workflow")
        assert history.call_count == 1
        # 引用值取响应里的 subfolder / name，不是请求里的——服务端会为重名改名。
        sent = request_json(only_request(submit))
        assert sent["prompt"]["20"]["inputs"]["image"] == "arcreel/job-7-reference_images-1.jpg"
        assert sent["client_id"] == "arcreel-job-7"
        assert upload.call_count == 1
        assert only_request(view).url.params["filename"] == "ArcReel_00001_.png"
        # 种子是提交那一刻现随机的，不回传这一版就再也复现不了。
        assert result.seed == sent["prompt"]["3"]["inputs"]["seed"]
        assert result.image_uri == f"{BASE_URL}/view?filename=ArcReel_00001_.png&subfolder=&type=output"

    async def test_only_as_many_references_as_there_are_slots_get_uploaded(self, tmp_path: Path):
        """格子数就是这份 workflow 能收几张；多出来的图不会被任何节点读到。"""
        definition = _definition(reference_slots=1)
        references = []
        for index in range(3):
            path = tmp_path / f"ref{index}.png"
            path.write_bytes(b"png")
            references.append(ReferenceImage(path=str(path)))

        with capture_http() as router:
            upload = router.post(f"{BASE_URL}/upload/image").mock(
                return_value=httpx.Response(200, json={"name": "stored.png", "subfolder": "arcreel"})
            )
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _image_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"png"))

            await _backend(definition).generate(_request(tmp_path, reference_images=references))

        assert upload.call_count == 1

    async def test_a_text_to_image_generation_uploads_nothing(self, tmp_path: Path):
        with capture_http() as router:
            upload = router.post(f"{BASE_URL}/upload/image")
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _image_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"png"))

            await _backend().generate(_request(tmp_path))

        assert upload.call_count == 0

    async def test_the_requested_size_is_derived_on_the_image_tier_table(self, tmp_path: Path):
        """图像与视频的分辨率档位短边表不是同一张；构造层按定义的 media_type 选表。"""
        with capture_http() as router:
            submit = router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _image_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"png"))

            await _backend().generate(_request(tmp_path, aspect_ratio="1:1", image_size="1k"))

        latent = request_json(only_request(submit))["prompt"]["5"]["inputs"]
        assert latent["width"] == latent["height"]
        # 1k 档的短边比 workflow 字面的 832×480 大，选档确实生效了。
        assert latent["width"] > 832

    async def test_the_credentials_ride_every_route(self, tmp_path: Path):
        definition = _definition(reference_slots=1)
        definition["auth"] = {"headers": {"Authorization": "Bearer {{api_key}}"}}
        assert validate_definition(definition).valid
        reference = tmp_path / "ref.png"
        reference.write_bytes(b"png")

        with capture_http() as router:
            upload = router.post(f"{BASE_URL}/upload/image").mock(
                return_value=httpx.Response(200, json={"name": "stored.png", "subfolder": "arcreel"})
            )
            submit = router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            history = router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _image_output()}))
            )
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"png"))

            await _backend(definition, api_key="k-1").generate(
                _request(tmp_path, reference_images=[ReferenceImage(path=str(reference))])
            )

        for route in (upload, submit, history, view):
            assert route.calls.last.request.headers["authorization"] == "Bearer k-1"


class TestArtifactWhitelist:
    """产物是不是这个端点该产的那一类，只有扩展名说得准。"""

    @pytest.mark.parametrize("filename", ["out.png", "out.jpg", "out.jpeg", "out.webp", "OUT.PNG"])
    async def test_a_whitelisted_suffix_is_stored(self, tmp_path: Path, filename: str):
        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _image_output(filename)}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"png"))

            result = await _backend().generate(_request(tmp_path))

        assert result.image_path.read_bytes() == b"png"

    @pytest.mark.parametrize("filename", ["out.gif", "out.apng", "out.mp4"])
    async def test_an_animated_or_video_artifact_is_a_type_mismatch(self, tmp_path: Path, filename: str):
        """一份图像端点产出动图意味着产物绑定指在了视频合成节点上；入库会得到一张只有首帧的图。"""
        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _image_output(filename)}))
            )
            view = router.get(f"{BASE_URL}/view")

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_type_mismatch"
        assert caught.value.params == {"filename": filename, "media_type": "image"}
        assert view.call_count == 0

    async def test_nothing_produced_by_the_output_node_is_its_own_code(self, tmp_path: Path):
        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"99": _image_output()}))
            )

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_missing"
        assert caught.value.params == {"nodes": "9"}


class TestMultipleArtifacts:
    async def test_a_batch_of_images_yields_the_first_one_and_a_warning(self, tmp_path: Path, caplog):
        """``batch_size > 1`` 的 workflow 一次出多张；本通道取第一张。

        这条提示只进日志：``ImageGenerationResult`` 没有视频通道那样的 ``warnings`` 位，图像调用
        入口也不收集执行期提示。导入端点时的绑定推断另有一条 ``batch_size_above_one`` 的界面提示。
        """
        outputs = {
            "9": {
                "images": [
                    {"filename": "ArcReel_00001_.png", "subfolder": "", "type": "output"},
                    {"filename": "ArcReel_00002_.png", "subfolder": "", "type": "output"},
                ]
            }
        }

        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"png"))

            with caplog.at_level("WARNING"):
                await _backend().generate(_request(tmp_path))

        assert only_request(view).url.params["filename"] == "ArcReel_00001_.png"
        assert "共 2 个" in caplog.text

    async def test_a_temp_preview_beside_the_image_is_not_taken(self, tmp_path: Path):
        """``type != "output"`` 的条目是中间预览，服务端随时会清掉它。"""
        outputs = {
            "9": {
                "images": [
                    {"filename": "preview.png", "subfolder": "", "type": "temp"},
                    {"filename": "ArcReel_00001_.png", "subfolder": "", "type": "output"},
                ]
            }
        }

        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"png"))

            await _backend().generate(_request(tmp_path))

        assert only_request(view).url.params["filename"] == "ArcReel_00001_.png"


class TestNoResume:
    def test_the_image_backend_offers_no_resume_entry(self):
        """图像任务不续跑：``prompt_id`` 无处持久化（请求不带 task_id），孤儿一律标 restart_lost。

        worker 的孤儿处置按 media_type 分流、不探 backend 的方法，故这条只钉住「本 backend 没有
        这一格」——有了它反而会让人以为图像任务接得回来。
        """
        backend = _backend()

        assert not hasattr(backend, "resume_video")
        assert not hasattr(backend, "resume")

    async def test_a_lost_job_is_a_plain_generation_failure(self, tmp_path: Path):
        """ComfyUI 重启把队列连同未落 history 的执行一起丢掉：判可重试的生成失败，不是续跑过期。"""
        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json={}))
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue()))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_job_lost"
        assert caught.value.params == {"prompt_id": "p-1"}


class TestStoppingTheRemote:
    """不续跑不等于不叫停：取消与超时那一刻 ``prompt_id`` 就在手里，远端还占着用户的显卡。"""

    @staticmethod
    def _version(router: Any, version: str) -> None:
        router.get(f"{BASE_URL}/system_stats").mock(
            return_value=httpx.Response(200, json={"system": {"comfyui_version": version}})
        )

    async def test_a_cancelled_generation_stops_the_remote_job(self, tmp_path: Path):
        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(side_effect=_cancel_here)
            self._version(router, "0.26.0")
            cancel = router.post(f"{BASE_URL}/api/jobs/p-1/cancel").mock(return_value=httpx.Response(200, json={}))

            with pytest.raises(asyncio.CancelledError):
                await _backend().generate(_request(tmp_path))

        assert cancel.call_count == 1

    async def test_an_older_server_interrupts_a_running_entry(self, tmp_path: Path):
        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(side_effect=_cancel_here)
            self._version(router, "0.25.14")
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue(running=("p-1",))))
            interrupt = router.post(f"{BASE_URL}/interrupt").mock(return_value=httpx.Response(200, json={}))

            with pytest.raises(asyncio.CancelledError):
                await _backend().generate(_request(tmp_path))

        assert interrupt.call_count == 1
