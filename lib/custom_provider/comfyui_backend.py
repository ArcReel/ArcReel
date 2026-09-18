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

续跑接的是第四段：``provider_job_id`` 就是 ``prompt_id``，前三段已经在上一个进程里发生过。取消与
超时则反过来——本地这一侧不要这次执行了，就顺手把远端也停掉，否则它会一直占着用户的显卡。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from lib.custom_provider.comfyui.capabilities import derive_video_capabilities
from lib.custom_provider.comfyui.failures import (
    EXECUTION_ERROR,
    INTERRUPTED,
    JOB_LOST,
    OUTPUT_MISSING,
    OUTPUT_TYPE_MISMATCH,
    ComfyuiError,
)
from lib.custom_provider.comfyui.request_builder import BuiltWorkflow, MediaInputs, build_workflow
from lib.custom_provider.comfyui_client import ComfyuiClient, client_id_for, upload_filename
from lib.video_backends.base import (
    ProviderJobIdPersistenceMixin,
    ResumeExpiredError,
    VideoAudioMode,
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

#: 叫停远端用的超时。比生成路径的短得多：这几个请求发在任务已被取消之后，一台不响应的
#: ComfyUI 不该把 worker 的关停拖上几分钟。
_STOP_TIMEOUT_SECONDS = 15

#: 从这一版起 ``POST /api/jobs/{id}/cancel`` 一个动作同时覆盖排队中与执行中；更早的版本
#: 只有「删队列项」与「打断当前执行」两个分开的动作。
_JOB_CANCEL_MIN_VERSION = (0, 26, 0)

#: ``status.messages`` 里表示这次执行没能出片的两个事件名。
_EXECUTION_ERROR_EVENT = "execution_error"
_EXECUTION_INTERRUPTED_EVENT = "execution_interrupted"

#: 认不出出错节点时 ``comfyui_execution_error`` 的 ``node`` 占位值。
_UNKNOWN_NODE = "-"


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
        job_label = request.task_id or self._job_label or uuid4().hex
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
                client_id=client_id_for(job_label),
                record=lambda stage, body: notify_provider_response(request, stage, body),
            )
            await self._persist_provider_job_id(
                request, prompt_id, provider=self._provider, endpoint=self._client.base_url
            )
            return await self._poll_download_or_stop(http, prompt_id, request, built=built, is_resume=False)

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
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as http:
            return await self._poll_download_or_stop(http, job_id, request, built=None, is_resume=True)

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

    # ------------------------------------------------------------------ 叫停远端

    async def _poll_download_or_stop(
        self,
        http: httpx.AsyncClient,
        prompt_id: str,
        request: VideoGenerationRequest,
        *,
        built: BuiltWorkflow | None,
        is_resume: bool,
    ) -> VideoGenerationResult:
        """轮询取件，本地这一侧被取消或等超时的时候顺手把远端也停掉。

        只包轮询与取件：提交之前没有 ``prompt_id`` 可停，而提交本身的歧义态由 ``submit_post``
        处置。ComfyUI 跑在用户自己的显卡上，扔下一个没人要的执行会一直占着卡。
        """
        try:
            return await self._poll_download(http, prompt_id, request, built=built, is_resume=is_resume)
        except (asyncio.CancelledError, TimeoutError):
            await self._stop_remote(prompt_id)
            raise

    async def _stop_remote(self, prompt_id: str) -> None:
        """best-effort 叫停远端：失败只记日志，本地状态机不动。

        另开一个短超时的客户端而不是复用生成那一路的：这几个请求发在任务已经取消或超时之后，
        一台不响应的 ComfyUI 不该把 worker 的关停再拖上生成路径那一份超时。

        版本现打一次 ``/system_stats`` 而不是构造时缓存：一台 ComfyUI 会在两次生成之间被升级，
        而这个判断只在取消的那一刻用得上，读不到就按低版本路径走。
        """
        try:
            async with httpx.AsyncClient(timeout=_STOP_TIMEOUT_SECONDS) as http:
                if _supports_job_cancel(await self._server_version(http)):
                    await self._client.cancel_job(http, prompt_id)
                    return
                snapshot = await self._client.queue_snapshot(http)
                if snapshot is None:
                    return
                running, pending = snapshot
                if prompt_id in pending:
                    await self._client.drop_from_queue(http, prompt_id)
                elif prompt_id in running:
                    # ``/interrupt`` 打断的是「当前正在执行的那一个」、不认 id：running 里不是
                    # 自己这一笔时发出去，停掉的是别人的活。
                    await self._client.interrupt(http)
        except Exception:
            logger.warning("ComfyUI 远端叫停失败 prompt_id=%s", prompt_id, exc_info=True)

    async def _server_version(self, http: httpx.AsyncClient) -> str | None:
        try:
            return await self._client.server_version(http)
        except Exception:
            # 版本读不到不是失败：老版本与部分代理本就不回这一字段，走低版本那条路一样能停下来。
            logger.info("ComfyUI 版本读取失败，按低版本路径叫停", exc_info=True)
            return None

    async def _queue_snapshot(self, http: httpx.AsyncClient) -> tuple[list[str], list[str]] | None:
        """丢失判定用的队列快照：这张表读不出时给 ``None``（继续轮询），不占轮询的失败预算。

        ``/queue`` 只是丢失判定的辅助判据，而任务本身的地址是 ``/history``——走到这里时它这一轮
        是好的，坏的只有 ``/queue``。把它的 HTTP 失败抛给 ``poll_with_retry`` 会让一条只挡掉
        ``/queue`` 的反向代理在连续十轮空态之后把一次仍在出片的执行判成失败，而这条路的本意正是
        「读不出时『不知道』比『判死』安全」。
        """
        try:
            return await self._client.queue_snapshot(http)
        except httpx.HTTPError:
            logger.info("ComfyUI 队列读取失败，本轮不判丢失", exc_info=True)
            return None

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
        built: BuiltWorkflow | None,
        is_resume: bool,
    ) -> VideoGenerationResult:
        output_nodes = [
            str(target["node"]) for target in _targets((self._definition.get("bindings") or {}).get("output"))
        ]

        # 单元素列表包住这一轮的执行记录：``poll_with_retry`` 按 is_done 判终态，而「有没有记录」
        # 正是终态本身，空列表即「还没轮到」。
        async def poll_once() -> list[Mapping[str, Any]]:
            entry = await self._client.fetch_history(http, prompt_id)
            if entry is None:
                entry = await self._history_or_lost(http, prompt_id, is_resume=is_resume)
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
        entry = found[0]
        failure = _terminal_failure(entry)
        if failure is not None:
            raise failure
        artifacts = _output_artifacts(entry, output_nodes)
        if not artifacts:
            raise ComfyuiError(OUTPUT_MISSING, nodes=" / ".join(output_nodes))
        artifact = _first_video(artifacts)
        if artifact is None:
            raise ComfyuiError(
                OUTPUT_TYPE_MISMATCH, filename=str(artifacts[0].get("filename") or ""), media_type="video"
            )
        filename = str(artifact.get("filename") or "")
        warnings: tuple[Mapping[str, Any], ...] = ()
        if len(artifacts) > 1:
            logger.warning("ComfyUI 产物共 %d 个，取: %s", len(artifacts), filename)
            warnings = ({"key": "comfyui_multiple_outputs", "params": {"count": len(artifacts), "filename": filename}},)
        await notify_provider_response(request, "result", {"artifact": dict(artifact), "count": len(artifacts)})
        await self._client.download_output(http, artifact, request.output_path, max_wait=request.poll_timeout_seconds)
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=self._provider,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=self._view_url(artifact),
            task_id=prompt_id,
            # 实发种子与 workflow 指纹只在提交那一次的构造里存在：两者一起才说得清「这一版是照
            # 哪份图、用哪个种子出的」，而续跑这条路一样都没构造，故一起缺席而不是各给一个假值。
            seed=built.seed if built is not None else None,
            generate_audio=request.generate_audio,
            provenance={"workflow_sha256": built.workflow_sha256} if built is not None else None,
            warnings=warnings,
        )

    async def _history_or_lost(
        self, http: httpx.AsyncClient, prompt_id: str, *, is_resume: bool
    ) -> Mapping[str, Any] | None:
        """history 还空着的这一轮：确认这次执行仍在队列上，否则判丢失。

        ComfyUI 重启会把队列连同尚未写进 history 的执行一起丢掉，而客户端这一侧看到的只是
        history 永远为空——不查队列就会一路轮询到全局超时。

        队列与 history 是两次独立的请求，一次执行恰好在两次之间走完时，它既已离开队列、第一次
        history 又还没看到它。故「不在队列里」之后再查一次 history，查到即照常收下，把这一格与
        真丢失分开。
        """
        snapshot = await self._queue_snapshot(http)
        if snapshot is None:
            return None
        running, pending = snapshot
        if prompt_id in running or prompt_id in pending:
            return None
        entry = await self._client.fetch_history(http, prompt_id)
        if entry is not None:
            return entry
        if is_resume:
            # 续跑期的同一判定归 resume_expired：worker 据此标失败并结算那条 pending 的调用行，
            # 而不是把它当成一次可以就地重试的生成失败。
            raise ResumeExpiredError(job_id=prompt_id, provider=self._provider)
        raise ComfyuiError(JOB_LOST, prompt_id=prompt_id)

    def _view_url(self, artifact: Mapping[str, Any]) -> str:
        query = urlencode(
            {
                "filename": str(artifact.get("filename") or ""),
                "subfolder": str(artifact.get("subfolder") or ""),
                "type": str(artifact.get("type") or "output"),
            }
        )
        return f"{self._client.base_url}/view?{query}"


def _supports_job_cancel(version: str | None) -> bool:
    """这台 ComfyUI 是否有 ``POST /api/jobs/{id}/cancel``。

    版本读不到、或不是 ``x.y.z`` 形状时按「没有」处置：低版本那条路（查队列 + 删项 / 打断）在
    新版本上同样有效，猜错的代价是多发两个请求；反过来猜错会打在一个 404 上、什么都没停掉。
    """
    if not version:
        return False
    matched = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", version.strip())
    if matched is None:
        return False
    major, minor, patch = matched.groups()
    return (int(major), int(minor), int(patch or 0)) >= _JOB_CANCEL_MIN_VERSION


def _terminal_failure(entry: Mapping[str, Any]) -> ComfyuiError | None:
    """一条终态记录说的是成功还是失败——失败给出失败码，成功给 ``None``。

    判据是 ``status.messages`` 的**末尾事件**而不是 ``status_str`` / ``completed``：一次执行里
    前面的节点报错、后面的节点照跑完是常态，按「有没有出现过 error」判会把成片误判成失败，而
    ``completed`` 在被打断的执行上同样为真。

    ``status`` 为 null（部分版本与代理的形状）时无从判起：这一格照 outputs 分——有产出就当它跑
    完了，一个产出都没有则按执行失败兜底，否则这次执行会一路走到「产物节点没出东西」，把一个
    环境问题说成绑定配错了。
    """
    status = entry.get("status")
    if not isinstance(status, Mapping):
        if _has_outputs(entry):
            return None
        return ComfyuiError(
            EXECUTION_ERROR,
            node=_UNKNOWN_NODE,
            detail="ComfyUI reported no execution status and no outputs",
        )
    event, data = _last_message(status)
    if event == _EXECUTION_ERROR_EVENT:
        return ComfyuiError(EXECUTION_ERROR, node=_error_node(data), detail=_error_detail(data))
    if event == _EXECUTION_INTERRUPTED_EVENT:
        return ComfyuiError(INTERRUPTED)
    return None


def _last_message(status: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    """``status.messages`` 的末尾事件，形状是 ``[事件名, 数据]``；没有消息时事件名为空串。"""
    messages = status.get("messages")
    if not isinstance(messages, list) or not messages:
        return "", {}
    last = messages[-1]
    if not isinstance(last, list) or not last:
        return "", {}
    data = last[1] if len(last) > 1 and isinstance(last[1], Mapping) else {}
    return str(last[0] or ""), data


def _error_node(data: Mapping[str, Any]) -> str:
    """报错节点在用户那里的名字：``node_type`` 就是画布上的节点类型，认不出退到节点号。"""
    return str(data.get("node_type") or data.get("node_id") or _UNKNOWN_NODE)


def _error_detail(data: Mapping[str, Any]) -> str:
    """异常摘要只取消息与类型两行，不带 traceback——它有上百行，而失败原因要整条落库。"""
    message = str(data.get("exception_message") or "").strip()
    exception_type = str(data.get("exception_type") or "").strip()
    if message and exception_type:
        return f"{exception_type}: {message}"
    return message or exception_type or "node raised an exception"


def _has_outputs(entry: Mapping[str, Any]) -> bool:
    outputs = entry.get("outputs")
    return isinstance(outputs, Mapping) and bool(outputs)


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


def binding_video_capabilities(definition: Mapping[str, Any]) -> VideoCapabilities:
    """把一份视频端点定义的绑定表装进 backend 层的能力类型。

    推导本身在 ``comfyui`` 子包里（它只认绑定表与 workflow）；装箱落在本模块，因为子包受
    「不依赖声明式运行时」的 forbidden 契约约束，够不到 ``VideoCapabilities``。端点投影
    （``endpoints.comfyui_endpoint_spec``）与 backend 自己的声明共用这一份，两处不各写一份——
    它们各自喂给能力闸门的不同一段，说的却必须是同一件事。

    参考音频三项与 ``max_prompt_chars`` 保持默认：绑定表里没有对应的语义键。
    """
    bound = derive_video_capabilities(definition)
    return VideoCapabilities(
        text_to_video=bound.text_to_video,
        first_frame=bound.first_frame,
        last_frame=bound.last_frame,
        max_reference_images=bound.max_reference_images,
        audio_track=VideoAudioMode(bound.audio_track),
    )


def _first_video(artifacts: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """这批产物里第一个视频文件；一个都没有时 ``None``。

    按扩展名挑而不是取第一个：一个绑定节点可以同时往 ``images`` 与 ``gifs`` 写（缩略图加成片），
    而 :func:`_output_artifacts` 给出的次序是 ``_ARTIFACT_KEYS`` 自己的次序，与「哪个是成片」无关。
    """
    return next(
        (item for item in artifacts if Path(str(item.get("filename") or "")).suffix.lower() in VIDEO_SUFFIXES),
        None,
    )


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
