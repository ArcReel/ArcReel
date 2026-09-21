"""ComfyUI 端点的图像调用通道。

与视频通道（:mod:`.comfyui_backend`）共用同一个客户端（:mod:`.comfyui_client`）、同一个请求体
构造层（:mod:`.request_builder`）与同一份执行层（:mod:`.comfyui_execution`）。本模块只剩图像这一侧独有的三件事：参考图的上传、``bindings``
推出的能力集与参考图上限、把取回的产物装成 ``ImageGenerationResult``。

与视频通道的两处实质差别：

* **不续跑。** ``ImageBackend`` 协议没有 ``resume`` 这一格，``prompt_id`` 也就无处持久化
  （``ImageGenerationRequest`` 不带 ``task_id``）。进程在轮询中途重启时，worker 的孤儿处置把
  image 任务一律标 ``restart_lost_image``——提交本身已经发生过，重新提交等于让用户的显卡把同一
  张图再跑一遍。取消与超时仍然叫停远端：那一格不需要持久化，``prompt_id`` 就在手里。
* **多产物只进日志。** ``batch_size > 1`` 的 workflow 一次出多张，本通道取第一张；
  ``ImageGenerationResult`` 没有 ``result.warnings`` 那样的提示位（视频通道有），故这句提示只落
  日志。导入期的绑定推断另有一条 ``batch_size_above_one`` 的界面提示，用户在保存端点之前就看得到。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
)
from lib.custom_provider.comfyui.bindings import targets_of
from lib.custom_provider.comfyui.capabilities import takes_reference_images
from lib.custom_provider.comfyui.comfyui_client import ComfyuiClient, client_id_for, upload_filename
from lib.custom_provider.comfyui.comfyui_execution import HTTP_TIMEOUT_SECONDS, ComfyuiExecution
from lib.custom_provider.comfyui.request_builder import MediaInputs, build_workflow

logger = logging.getLogger(__name__)

#: 本端点产出的是图像：产物扩展名白名单与失败文案共读这一个值
#: （见 ``comfyui.artifacts.ARTIFACT_SUFFIXES_BY_MEDIA_TYPE``）。
MEDIA_TYPE = "image"

#: 一次图像执行等到终态的墙钟上限。
#:
#: 不读全局的 ``video_poll_timeout_seconds``：那个设置项说的是视频，把它的含义悄悄扩到图像上，
#: 用户调它的时候就不知道自己在调几件事。图像这一维没有可配项——内置图像供应商都是一次同步调用，
#: 而 ComfyUI 的出图既要排队又跑在用户自己的显卡上，故按后者的最坏情形取一个够宽的定值。
IMAGE_POLL_TIMEOUT_SECONDS = 1800


class ComfyuiImageBackend:
    """把一份 ComfyUI 端点定义跑成一张图。

    实现 ``lib.backends.image_backends.base.ImageBackend`` 协议。一次生成的四段与视频通道同构：上传参考图
    换回服务端认的引用名 → 在底稿深拷贝上构造实发 workflow → ``POST /prompt`` 拿 ``prompt_id`` →
    轮询 ``/history`` 到终态后按 ``output`` 绑定取产物下载入库。
    """

    def __init__(
        self,
        *,
        provider_id: str,
        model: str,
        base_url: str,
        api_key: str,
        definition: Mapping[str, Any],
        job_label: str | None = None,
    ) -> None:
        """``job_label`` 进上传文件名与 ``client_id``，让用户在 ComfyUI 的队列界面上认出是谁发的。

        图像请求不带 ``task_id``（``ImageGenerationRequest`` 没有这一维），故这里没有 worker 路径
        与非 worker 路径之分：调用方给了标签就用它，没给就回落到一串随机 hex——认不出是谁发的，
        但不会与别的调用方撞名、覆盖掉对方的上传。
        """
        self._provider = provider_id
        self._model = model
        self._definition = definition
        self._job_label = job_label
        self._client = ComfyuiClient(base_url=base_url, api_key=api_key, definition=definition)
        self._execution = ComfyuiExecution(client=self._client, definition=definition, media_type=MEDIA_TYPE)

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        """这份 workflow 的绑定表说它能做什么。

        端点投影（``endpoints.comfyui_endpoint_spec``）与这里共用 :func:`binding_image_capabilities`
        一份实现：投影那一份决定这个模型行进哪个桶，这一份是 ``media_generator`` 发请求之前的兜底
        闸门，两者说的必须是同一件事——否则一个进得了 i2i 桶的端点会在闸门上被自己挡掉。
        """
        return set(binding_image_capabilities(self._definition))

    @property
    def max_reference_images(self) -> int:
        """参考图格子数就是这份 workflow 能收几张。

        不返回 ``0``（「本后端不按数量裁剪」）：多出来的图在构造层本来就没有落点，让编排层先按这个
        数裁剪并提示，比静默丢掉几张用户挑过的参考图诚实。
        """
        return len(targets_of((self._definition.get("bindings") or {}).get("reference_images")))

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        job_label = self._job_label or uuid4().hex
        async with artifact_http_client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as http:
            media = await self._upload_media(http, request, job_label=job_label)
            built = build_workflow(
                self._definition,
                prompt=request.prompt,
                aspect_ratio=request.aspect_ratio,
                resolution=request.image_size,
                media=media,
                seed=request.seed,
            )
            prompt_id = await self._client.submit_prompt(http, built.workflow, client_id=client_id_for(job_label))
            picked = await self._execution.fetch_artifact(
                http,
                prompt_id,
                output_path=request.output_path,
                poll_timeout_seconds=IMAGE_POLL_TIMEOUT_SECONDS,
            )
            return ImageGenerationResult(
                image_path=request.output_path,
                provider=self._provider,
                model=self._model,
                image_uri=self._execution.view_url(picked.artifact),
                # 实发种子：``policy: "random"`` 是提交那一刻现随机的，请求里那个值说不出它。报出来
                # 而不是留空——图像调用入口不把它并进版本元数据（``_merge_result_provenance`` 只挂在
                # 视频那一条路上），而这一位是本层唯一说得清这一版用了哪个种子的地方。
                seed=built.seed,
            )

    async def _upload_media(
        self, http: httpx.AsyncClient, request: ImageGenerationRequest, *, job_label: str
    ) -> MediaInputs:
        """把这次要用到的参考图传上去，换回读图节点认的引用名。

        只传落得进格子的那几张：格子数就是这份 workflow 能收几张，多出来的图不会被任何节点读到，
        白占用户的带宽与磁盘。图像端点没有首尾帧这两个语义键（见 ``comfyui.bindings``），故这里
        只有参考图一路。
        """
        slots = self.max_reference_images
        references: list[str] = []
        for index, reference in enumerate(request.reference_images[:slots]):
            path = Path(reference.path)
            references.append(
                await self._client.upload_image(
                    http, path, filename=upload_filename(job_label, "reference_images", path, index)
                )
            )
        return MediaInputs(reference_images=tuple(references))


def binding_image_capabilities(definition: Mapping[str, Any]) -> frozenset[ImageCapability]:
    """把一份图像端点定义的绑定表装进 backend 层的能力类型。

    有参考图格子即仅图生图，没有即仅文生图，两者互斥（``docs/adr/0082``）。判据本身在 ``comfyui``
    子包里（它只认绑定表与 workflow）；装箱落在本模块，因为子包受「不依赖声明式运行时」的 forbidden
    契约约束，够不到 ``ImageCapability``。落点与视频那一侧的 ``binding_video_capabilities`` 同形。
    """
    if takes_reference_images(definition):
        return frozenset({ImageCapability.IMAGE_TO_IMAGE})
    return frozenset({ImageCapability.TEXT_TO_IMAGE})
