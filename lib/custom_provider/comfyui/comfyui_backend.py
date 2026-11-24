"""ComfyUI 端点的视频调用通道。

不在 ``lib.backends.video_backends``：本 backend 的输入是一份 ComfyUI 端点定义（workflow + 节点绑定），
读它要用本包的构造层，而分层契约（``pyproject.toml`` ``[tool.importlinter]``）不允许 backend 层反向依赖
``lib.custom_provider``；方向与声明式运行时一致——上层消费下层，下层不知道端点定义的存在。

一次生成的四段：上传素材换回服务端认的引用名 → 在底稿深拷贝上构造实发 workflow → ``POST /prompt``
拿 ``prompt_id`` → 轮询 ``/history`` 到终态后按 ``output`` 绑定取产物下载入库。素材上传排在构造
之前，因为引用名要填进 workflow；``provider_job_id`` 的持久化排在轮询之前，因为进程在轮询中途重启
时，没落库的那笔任务就再也找不回来了。

第四段本身与媒体类型无关，落在 :mod:`.comfyui_execution`，与图像通道共用；本模块只管视频这一侧
独有的部分——由绑定表推出的能力声明、首尾帧与参考图的上传、续跑，以及把取回的产物装成
``VideoGenerationResult``。

续跑接的是第四段：``provider_job_id`` 就是 ``prompt_id``，前三段已经在上一个进程里发生过。取消与
超时则反过来——本地这一侧不要这次执行了，就顺手把远端也停掉，否则它会一直占着用户的显卡。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from arcreel_market_core.video_backend_contract import (
    ResumeExpiredError,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import (
    ProviderJobIdPersistenceMixin,
    ReferenceImageData,
    notify_provider_response,
    read_video_images,
)
from lib.backends.input_limits import excess_image_names
from lib.custom_provider.comfyui.capabilities import derive_video_capabilities
from lib.custom_provider.comfyui.comfyui_client import ComfyuiClient, client_id_for, upload_filename
from lib.custom_provider.comfyui.comfyui_execution import HTTP_TIMEOUT_SECONDS, ComfyuiExecution, PickedArtifact
from lib.custom_provider.comfyui.request_builder import BuiltWorkflow, MediaInputs, build_workflow

logger = logging.getLogger(__name__)

MEDIA_TYPE = "video"


class ComfyuiVideoBackend(ProviderJobIdPersistenceMixin):
    """把一份 ComfyUI 端点定义跑成一个分镜视频。"""

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
        """``job_label`` 是非 worker 路径的任务标识，进上传文件名与 ``client_id``。

        worker 路径不传：那里有 ``request.task_id``，它才是这一笔在 ArcReel 这一侧的身份。两者
        都没有时回落到一串随机 hex——在 ComfyUI 的队列界面上认不出是谁发的，但至少不会与别的
        调用方撞名。
        """
        self._provider = provider_id
        self._model = model
        self._definition = definition
        self._job_label = job_label
        self._base_url = base_url
        self._api_key = api_key
        self._client = ComfyuiClient(base_url=base_url, api_key=api_key, definition=definition)
        self._execution = ComfyuiExecution(client=self._client, definition=definition, media_type=MEDIA_TYPE)

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def video_capabilities(self) -> VideoCapabilities:
        """这份 workflow 的绑定表说它能做什么。

        这不是一份只在绕过工厂时才读的兜底声明：包装层的档位查询
        （``CustomVideoBackend.video_capabilities_for_tier``）刻意不短路回工厂注入的合成结果，而是
        以被包装 backend 的这份声明为基底再叠加用户覆盖。生成前的能力闸门走的正是那条路——这里
        少宣称一位，闸门就会在请求到达 :meth:`generate` 之前把它挡掉。
        """
        return binding_video_capabilities(self._definition)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        bindings = self._definition.get("bindings") or {}
        if request.start_image is not None and not _targets(bindings.get("start_image")):
            raise VideoCapabilityError("video_first_frame_unsupported", provider=self.name, model=self.model)
        if request.end_image is not None and not _targets(bindings.get("end_image")):
            raise VideoCapabilityError("video_last_frame_unsupported", provider=self.name, model=self.model)
        count = len(request.reference_images or [])
        slots = derive_video_capabilities(self._definition).max_reference_images
        if count > slots:
            raise VideoCapabilityError(
                "video_reference_images_exceeded",
                names=excess_image_names(slots, count, request.reference_images or []),
                model=self._model,
                limit=slots,
                count=count,
            )
        if (
            request.reference_audio_files
            or self._definition["bindings"].get("reference_audio")
            or self._definition["bindings"].get("aspect_ratio")
        ):
            await asyncio.to_thread(
                build_workflow,
                self._definition,
                prompt=request.prompt,
                aspect_ratio=request.aspect_ratio,
                resolution=request.resolution,
                duration_seconds=request.duration_seconds,
                media=MediaInputs(
                    start_image=str(request.start_image) if request.start_image else None,
                    end_image=str(request.end_image) if request.end_image else None,
                    reference_images=tuple(str(path) for path in request.reference_images or []),
                    reference_audio_files=tuple(str(path) for path in request.reference_audio_files or []),
                ),
                seed=request.seed,
            )
        job_label = request.task_id or self._job_label or uuid4().hex
        async with artifact_http_client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as http:
            media = await self._upload_media(http, request, job_label=job_label)
            built = await asyncio.to_thread(
                build_workflow,
                self._definition,
                prompt=request.prompt,
                aspect_ratio=request.aspect_ratio,
                duration_seconds=request.duration_seconds,
                resolution=request.resolution,
                media=media,
                seed=request.seed,
            )
            prompt_id = await self._client.submit_prompt(
                http,
                built.workflow,
                client_id=client_id_for(job_label),
                record=lambda stage, body: notify_provider_response(request, stage, body),
            )
            await self._persist_provider_job_id(
                request, prompt_id, provider=self._provider, endpoint=self._client.base_url
            )
            picked = await self._execution.fetch_artifact(
                http,
                prompt_id,
                output_path=request.output_path,
                poll_timeout_seconds=request.poll_timeout_seconds,
                record=lambda stage, body: notify_provider_response(request, stage, body),
            )
            return self._result(request, prompt_id, picked, built=built)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续一次已经提交过的执行：直接进轮询，既不重传素材也不重新提交 workflow。

        ``prompt_id`` 就是 ``provider_job_id``，而 ComfyUI 的执行完全在服务端，重新提交会在用户
        的显卡上把同一张图再跑一遍。``output`` 绑定读的是**当前**这份端点定义——用户在续跑之前改
        过绑定时，按新绑定去取产物是唯一说得通的口径，取不到即 ``comfyui_output_missing``。

        实发种子与 workflow 指纹不随续跑回来：两者只在提交那一次的构造里存在，而这条路不构造。

        域名取提交那一次的（``submitted_base_url``，由 resume_executor 从任务行回放），与声明式
        运行时同一口径：供应商的 base_url 可以在提交之后被改，而这一笔活在原来那台 ComfyUI 上。
        照当前域名去问，问的是另一台机器，它答「没有这个 prompt_id」——一次仍在出片的执行会被
        判成丢失，用户的显卡还在为它转。域名是连接维度，不是协议维度。
        """
        submitted = request.submitted_base_url
        if submitted and submitted != self._base_url:
            return await self._bound_to(submitted).resume_video(job_id, request)
        async with artifact_http_client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as http:
            picked = await self._execution.fetch_artifact(
                http,
                job_id,
                output_path=request.output_path,
                poll_timeout_seconds=request.poll_timeout_seconds,
                record=lambda stage, body: notify_provider_response(request, stage, body),
                lost_error=lambda prompt_id: ResumeExpiredError(job_id=prompt_id, provider=self._provider),
            )
            return self._result(request, job_id, picked, built=None)

    def _bound_to(self, base_url: str) -> ComfyuiVideoBackend:
        """同一份定义、换一个域名的另一个实例：轮询、取件与叫停这一路都得落在同一台机器上。"""
        return ComfyuiVideoBackend(
            provider_id=self._provider,
            model=self._model,
            base_url=base_url,
            api_key=self._api_key,
            definition=self._definition,
            job_label=self._job_label,
        )

    def _result(
        self,
        request: VideoGenerationRequest,
        prompt_id: str,
        picked: PickedArtifact,
        *,
        built: BuiltWorkflow | None,
    ) -> VideoGenerationResult:
        """把取回的产物装成结果；``built`` 为 ``None`` 即这一笔是续跑（没有构造过）。"""
        warnings: tuple[Mapping[str, Any], ...] = ()
        if picked.count > 1:
            warnings = (
                {"key": "comfyui_multiple_outputs", "params": {"count": picked.count, "filename": picked.filename}},
            )
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=self._provider,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=self._execution.view_url(picked.artifact),
            task_id=prompt_id,
            seed=built.seed if built is not None else None,
            generate_audio=(request.video_capabilities or self.video_capabilities)
            .audio_track_for_route("r2v" if request.reference_images else "i2v")
            .output_audio(request.generate_audio),
            provenance={"workflow_sha256": built.workflow_sha256} if built is not None else None,
            warnings=warnings,
        )

    async def _upload_media(
        self, http: httpx.AsyncClient, request: VideoGenerationRequest, *, job_label: str
    ) -> MediaInputs:
        """上传绑定的首尾帧，以及已通过槽位容量校验的全部参考图。"""
        images = await asyncio.to_thread(read_video_images, request, self.model)
        start = await self._upload_one(http, request.start_image, "start_image", images, job_label=job_label)
        end = await self._upload_one(http, request.end_image, "end_image", images, job_label=job_label)
        references: list[str] = []
        for index, path in enumerate(request.reference_images or []):
            references.append(
                await self._client.upload_media(
                    http,
                    path,
                    image=images[Path(path)],
                    filename=upload_filename(job_label, "reference_images", path, index),
                )
            )
        audio = []
        for index, path in enumerate(request.reference_audio_files or []):
            audio.append(
                await self._client.upload_media(
                    http, path, filename=upload_filename(job_label, "reference_audio", path, index)
                )
            )
        return MediaInputs(
            start_image=start, end_image=end, reference_images=tuple(references), reference_audio_files=tuple(audio)
        )

    async def _upload_one(
        self,
        http: httpx.AsyncClient,
        path: Path | None,
        key: str,
        images: Mapping[Path, ReferenceImageData],
        *,
        job_label: str,
    ) -> str | None:
        if path is None:
            return None
        return await self._client.upload_media(
            http, path, image=images[Path(path)], filename=upload_filename(job_label, key, path)
        )


def _targets(raw: object) -> list[Mapping[str, Any]]:
    return [target for target in raw if isinstance(target, Mapping)] if isinstance(raw, list) else []


def binding_video_capabilities(definition: Mapping[str, Any]) -> VideoCapabilities:
    from lib.backends.video_capabilities import channel_projection
    from lib.custom_provider.comfyui.capabilities import comfyui_description

    return channel_projection(comfyui_description(definition, "video"))
