"""ComfyUI 端点的视频调用通道。

住在 ``lib.custom_provider`` 顶层，两侧各有一条理由。不在 ``lib.video_backends``：本 backend 的
输入是一份 ComfyUI 端点定义（workflow + 节点绑定），读它要用 ``comfyui`` 子包的构造层，而分层
契约（``pyproject.toml`` ``[tool.importlinter]``）不允许 backend 层反向依赖 ``lib.custom_provider``；
方向与声明式运行时一致——上层消费下层，下层不知道端点定义的存在。也不在 ``comfyui`` 子包内：那里
受「不依赖声明式运行时」的 forbidden 契约约束，而本模块要用的 ``lib.video_backends.base`` 绕一圈
会间接够到声明式 backend。

一次生成的四段：上传素材换回服务端认的引用名 → 在底稿深拷贝上构造实发 workflow → ``POST /prompt``
拿 ``prompt_id`` → 轮询 ``/history`` 到终态后按 ``output`` 绑定取产物下载入库。素材上传排在构造
之前，因为引用名要填进 workflow；``provider_job_id`` 的持久化排在轮询之前，因为进程在轮询中途重启
时，没落库的那笔任务就再也找不回来了。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from lib.custom_provider.comfyui.failures import OUTPUT_MISSING, OUTPUT_TYPE_MISMATCH, ComfyuiError
from lib.custom_provider.comfyui.request_builder import BuiltWorkflow, MediaInputs, build_workflow
from lib.custom_provider.comfyui_client import ComfyuiClient, upload_filename
from lib.video_backends.base import (
    ProviderJobIdPersistenceMixin,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
    notify_provider_response,
    poll_with_retry,
    should_retry_poll,
)

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT_SECONDS = 60

#: 判成视频的扩展名。ComfyUI 的产物条目只给文件名，节点类型说不准产的是图还是片
#: （``VHS_VideoCombine`` 也能导 webp 动图），扩展名是唯一可靠的信号。
VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mov"})

#: history 条目里可能挂产物的三个键。只读这三个，且只读 ``output`` 绑定的那个节点。
_ARTIFACT_KEYS = ("images", "gifs", "audio")

#: 提交时带上的客户端标识前缀，便于在 ComfyUI 的队列界面上认出是谁发的。
_CLIENT_ID_PREFIX = "arcreel-"


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
    ) -> None:
        self._provider = provider_id
        self._model = model
        self._definition = definition
        self._client = ComfyuiClient(base_url=base_url, api_key=api_key, definition=definition)

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def video_capabilities(self) -> VideoCapabilities:
        """能力由节点绑定推导，推导尚未落地时一位都不宣称。

        工厂路径下这份声明不会被读到——包装层注入的是「系统判定 ⊕ 用户覆盖」的合成结果；只有绕过
        工厂直接构造时才回落到这里。
        """
        return VideoCapabilities(text_to_video=False, first_frame=False)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        job_label = request.task_id or uuid4().hex
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as http:
            media = await self._upload_media(http, request, job_label=job_label)
            built = build_workflow(
                self._definition,
                prompt=request.prompt,
                aspect_ratio=request.aspect_ratio,
                # 时长原样转交：``frames`` 未绑定或读不到帧率时，构造层自己跳过帧数换算——在这里
                # 补一个缺省只会把「这份 workflow 的时长不由 ArcReel 驱动」写成一个具体秒数。
                duration_seconds=request.duration_seconds,
                resolution=request.resolution,
                media=media,
                seed=request.seed,
            )
            prompt_id = await self._client.submit_prompt(
                http,
                built.workflow,
                client_id=f"{_CLIENT_ID_PREFIX}{job_label}",
                record=lambda stage, body: notify_provider_response(request, stage, body),
            )
            await self._persist_provider_job_id(
                request, prompt_id, provider=self._provider, endpoint=self._client.base_url
            )
            return await self._poll_download(http, prompt_id, request, built=built)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """续跑尚未落地：抛 ``NotImplementedError`` 让孤儿处置标 ``[resume_unsupported]``。

        比默默重新提交好——那会在用户的显卡上重跑一遍已经在跑的任务。
        """
        raise NotImplementedError("ComfyUI 端点的续跑尚未落地")

    # ------------------------------------------------------------------ 上传

    async def _upload_media(
        self, http: httpx.AsyncClient, request: VideoGenerationRequest, *, job_label: str
    ) -> MediaInputs:
        """把这次要用到的素材传上去，换回读图节点认的引用名。

        只传「绑定了、且这次给了」的那些：多传的图不会被任何节点读到，白占用户的带宽与磁盘，而
        参考图格子数就是这份 workflow 能收几张，多出来的那几张在构造层本来就用不上。
        """
        bindings: Mapping[str, Any] = self._definition.get("bindings") or {}
        start = await self._upload_one(http, request.start_image, bindings, "start_image", job_label=job_label)
        end = await self._upload_one(http, request.end_image, bindings, "end_image", job_label=job_label)
        slots = len(_targets(bindings.get("reference_images")))
        references: list[str] = []
        for index, path in enumerate((request.reference_images or [])[:slots]):
            references.append(
                await self._client.upload_image(
                    http, path, filename=upload_filename(job_label, "reference_images", path, index)
                )
            )
        return MediaInputs(start_image=start, end_image=end, reference_images=tuple(references))

    async def _upload_one(
        self,
        http: httpx.AsyncClient,
        path: Path | None,
        bindings: Mapping[str, Any],
        key: str,
        *,
        job_label: str,
    ) -> str | None:
        if path is None or not _targets(bindings.get(key)):
            return None
        return await self._client.upload_image(http, path, filename=upload_filename(job_label, key, path))

    # ------------------------------------------------------------------ 轮询与产物

    async def _poll_download(
        self,
        http: httpx.AsyncClient,
        prompt_id: str,
        request: VideoGenerationRequest,
        *,
        built: BuiltWorkflow,
    ) -> VideoGenerationResult:
        output_nodes = [
            str(target["node"]) for target in _targets((self._definition.get("bindings") or {}).get("output"))
        ]

        # 单元素列表包住这一轮的执行记录：``poll_with_retry`` 按 is_done 判终态，而「有没有记录」
        # 正是终态本身，空列表即「还没轮到」。
        async def poll_once() -> list[Mapping[str, Any]]:
            entry = await self._client.fetch_history(http, prompt_id)
            if entry is None:
                return []
            await notify_provider_response(request, "poll", _history_digest(entry, output_nodes))
            return [entry]

        found = await poll_with_retry(
            poll_fn=poll_once,
            # 有记录即终态：ComfyUI 只在一次执行走完（成功、报错或被打断）之后才往 history 写。
            is_done=bool,
            is_failed=lambda _found: None,
            max_wait=request.poll_timeout_seconds,
            retry_if=should_retry_poll,
            label="comfyui",
        )
        artifacts = _output_artifacts(found[0], output_nodes)
        if not artifacts:
            raise ComfyuiError(OUTPUT_MISSING, nodes=" / ".join(output_nodes))
        artifact = artifacts[0]
        filename = str(artifact.get("filename") or "")
        if Path(filename).suffix.lower() not in VIDEO_SUFFIXES:
            raise ComfyuiError(OUTPUT_TYPE_MISMATCH, filename=filename, media_type="video")
        if len(artifacts) > 1:
            logger.warning("ComfyUI 产物共 %d 个，取第 1 个: %s", len(artifacts), filename)
        await notify_provider_response(request, "result", {"artifact": dict(artifact), "count": len(artifacts)})
        await self._client.download_output(http, artifact, request.output_path, max_wait=request.poll_timeout_seconds)
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=self._provider,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=self._view_url(artifact),
            task_id=prompt_id,
            seed=built.seed,
            generate_audio=request.generate_audio,
            # workflow 指纹只在这一次构造之后才存在，故与实发种子同路回传：两者一起才说得清
            # 「这一版是照哪份图、用哪个种子出的」。
            provenance={"workflow_sha256": built.workflow_sha256},
        )

    def _view_url(self, artifact: Mapping[str, Any]) -> str:
        query = urlencode(
            {
                "filename": str(artifact.get("filename") or ""),
                "subfolder": str(artifact.get("subfolder") or ""),
                "type": str(artifact.get("type") or "output"),
            }
        )
        return f"{self._client.base_url}/view?{query}"


def _targets(raw: object) -> list[Mapping[str, Any]]:
    return [target for target in raw if isinstance(target, Mapping)] if isinstance(raw, list) else []


def _output_artifacts(entry: Mapping[str, Any], output_nodes: Sequence[str]) -> list[Mapping[str, Any]]:
    """``output`` 绑定的节点这次产出的文件，按绑定次序、每个节点按三个键的次序。

    只读被绑定的那些节点：一份 workflow 里 ``PreviewImage`` 之类的旁支同样会往 history 写产物，
    扫全图会把一张预览图当成成片取走。``type != "output"`` 的条目一律跳过——``temp`` 是中间预览，
    服务端随时会清掉它。
    """
    outputs = entry.get("outputs")
    if not isinstance(outputs, Mapping):
        return []
    found: list[Mapping[str, Any]] = []
    for node_id in output_nodes:
        node = outputs.get(node_id)
        if not isinstance(node, Mapping):
            continue
        for key in _ARTIFACT_KEYS:
            items = node.get(key)
            if not isinstance(items, list):
                continue
            found.extend(
                item for item in items if isinstance(item, Mapping) and str(item.get("type") or "") == "output"
            )
    return found


def _history_digest(entry: Mapping[str, Any], output_nodes: Sequence[str]) -> dict[str, Any]:
    """留痕用的 history 摘要：只留状态与 ``output`` 节点那一段。

    整份 history 带着每个节点的全部产出，一条留痕就能把诊断列撑到几百 KB，而排查要看的只有这
    两块。
    """
    outputs = entry.get("outputs")
    kept = (
        {node_id: outputs[node_id] for node_id in output_nodes if node_id in outputs}
        if isinstance(outputs, Mapping)
        else {}
    )
    return {"status": entry.get("status"), "outputs": kept}
