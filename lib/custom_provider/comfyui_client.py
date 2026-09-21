"""一台 ComfyUI 服务的 HTTP 面：上传素材、提交 workflow、查历史、取产物。

凭据按端点定义的 ``auth`` 节渲染一次，全部路由共用同一份——ComfyUI 原生无鉴权，凭据只有套在
反向代理后面时才需要，而代理认的是同一组头 / 查询参数，逐路由各渲染一次只会让其中一条漏掉。

本模块只管协议形状（发什么、收到的原文长什么样），不判业务终态：一次执行是成功、失败还是仍在
排队，要读 ``output`` 绑定的那个节点才知道，那是 backend 的事。故这里只在「连协议都不成立」时
抛失败码——上传没成功、提交被拒。

住在 ``lib.custom_provider`` 顶层而非 ``comfyui`` 子包：子包受「不依赖声明式运行时」的 import
契约约束，而 import-linter 连间接引用一起查——本模块要用 ``lib.backends.video_backends.base`` 的提交包装
与下载器，那条链绕一圈会回到声明式 backend。同 ``comfyui_endpoint_spec`` 落在 ``endpoints.py``
的理由。
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from lib.backends.video_backends.base import (
    IMAGE_MIME_TYPES,
    ProviderResponseStage,
    redacted_status_error,
    request_with_scoped_credentials,
    should_retry_submit,
    stream_to_file,
    submit_post,
    url_origin,
    with_artifact_retry,
)
from lib.custom_provider.auth_section import render_auth
from lib.custom_provider.comfyui.failures import NODE_ERRORS, UPLOAD_FAILED, ComfyuiError
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import retry_async

logger = logging.getLogger(__name__)

#: 上传素材统一落在服务端 ``input`` 区的这个子目录下，不与用户自己传的图混在一起。
UPLOAD_SUBFOLDER = "arcreel"

#: 提交时带上的客户端标识前缀，便于在 ComfyUI 的队列界面上认出是谁发的。
CLIENT_ID_PREFIX = "arcreel-"

#: ComfyUI 的 ``type`` 维度：``input`` 是可被读图节点引用的输入区。
_INPUT_TYPE = "input"

#: 留痕回调：与声明式运行时同一形状（阶段 + 原文），由 backend 接到任务的诊断列上。
RecordResponse = Callable[[ProviderResponseStage, object], Awaitable[None]]


def normalize_comfyui_base_url(base_url: str) -> str:
    """补协议 + 去尾斜杠。

    不剥版本段：ComfyUI 的路由全在根下（``/prompt`` / ``/history``），没有声明式定义那种
    ``base_url`` 与定义各带一次版本前缀、拼出 ``/v1/v2`` 的形态。

    无 scheme 的纯域名补 ``https://``——httpx 拒收缺协议的相对 URL，而自定义供应商的 ``base_url``
    是不受约束的字符串。
    """
    stripped = base_url.strip().rstrip("/")
    if stripped and "://" not in stripped:
        stripped = f"https://{stripped}"
    return stripped


def media_reference(subfolder: str, name: str) -> str:
    """服务端上传响应的 ``subfolder`` / ``name`` 拼成读图节点认的那个引用值。"""
    return f"{subfolder}/{name}" if subfolder else name


def upload_filename(task_id: str, key: str, path: Path, index: int | None = None) -> str:
    """一次上传用的文件名：``<task_id>-<key>[-<序号>].<ext>``。

    带上任务 id 是为了让同一台 ComfyUI 上并行的几笔任务互不覆盖——``overwrite=true`` 按文件名
    覆盖，两笔任务的首帧重名就会互相踩掉对方的输入。
    """
    suffix = path.suffix.lower() or ".png"
    serial = "" if index is None else f"-{index + 1}"
    return f"{task_id}-{key}{serial}{suffix}"


def _mime_of(path: Path) -> str:
    """按扩展名给真值。

    ComfyUI 服务端按 multipart 的 content type 判这份上传收不收，``application/octet-stream``
    会被部分部署直接拒掉。
    """
    return IMAGE_MIME_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "image/png"


class ComfyuiClient:
    """按一份端点定义与一个供应商配置，接上一台 ComfyUI 服务。"""

    def __init__(self, *, base_url: str, api_key: str, definition: Mapping[str, Any]) -> None:
        self._base_url = normalize_comfyui_base_url(base_url)
        self._headers, self._query = render_auth(definition.get("auth") or {}, api_key=api_key)

    @property
    def base_url(self) -> str:
        return self._base_url

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _authed_url(self, path: str) -> str:
        """一条路由的地址，按 query 传的凭证拼在上面。

        ``request_with_scoped_credentials`` 约定首跳的 auth query 由调用方拼进 URL（它只在同源
        续跳时补回），且它不收 ``params``——那会把这里拼上的查询串整串替换掉。带自有查询参数的
        ``/view`` 因此不走这条，凭证与参数一起交给 ``stream_to_file`` 的首跳 ``params``。
        """
        url = httpx.URL(self._url(path))
        return str(url.copy_merge_params(self._query) if self._query else url)

    @property
    def _credential_origin(self) -> tuple[str, str, int | None]:
        """凭证只许发往这台 ComfyUI 自己的源。

        反向代理把 ``/view`` 之类的路由 302 到对象存储 / CDN 是常见部署，而 httpx 跨源只摘
        ``Authorization``——auth 节允许任意头名，交给 ``follow_redirects`` 会把 ``X-API-Key``
        原样送到第三方。
        """
        return url_origin(self._base_url)

    async def upload_image(self, http: httpx.AsyncClient, path: Path, *, filename: str) -> str:
        """把一份素材传进服务端 ``input`` 区，返回读图节点认的引用值。

        引用值以**响应**里的 ``subfolder`` / ``name`` 拼成而不是以请求里的：服务端会为重名改名、
        也可能把子目录规整掉，按请求值填进 workflow 会指向一个并不存在的文件。
        """
        try:
            payload = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise ComfyuiError(UPLOAD_FAILED, detail=str(exc)) from exc
        try:
            response = await request_with_scoped_credentials(
                http,
                "POST",
                self._authed_url("/upload/image"),
                headers=self._headers,
                json=None,
                auth_query=self._query,
                files={"image": (filename, payload, _mime_of(path))},
                data={"subfolder": UPLOAD_SUBFOLDER, "overwrite": "true", "type": _INPUT_TYPE},
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            raise ComfyuiError(UPLOAD_FAILED, detail=str(redacted_status_error(exc))) from None
        except (httpx.HTTPError, ValueError) as exc:
            raise ComfyuiError(UPLOAD_FAILED, detail=str(exc)) from exc
        if not isinstance(body, Mapping) or not body.get("name"):
            raise ComfyuiError(UPLOAD_FAILED, detail="upload response did not name the stored file")
        return media_reference(str(body.get("subfolder") or ""), str(body["name"]))

    async def submit_prompt(
        self,
        http: httpx.AsyncClient,
        workflow: Mapping[str, Any],
        *,
        client_id: str,
        record: RecordResponse | None = None,
    ) -> str:
        """提交一份实发 workflow，返回服务端分配的 ``prompt_id``。

        ``node_errors`` 随 200 回来与 400 同判失败：ComfyUI 对「图能提交、但某个节点的入参过不了
        校验」回的就是这两种形状，放行它等于让任务进轮询后永远等不到产物。

        ``record`` 可缺省：图像通道没有留痕通道（``ImageGenerationRequest`` 不带诊断回调），传一个
        什么都不做的回调只是把这件事写成看起来有。
        """

        async def note(stage: ProviderResponseStage, body: object) -> None:
            if record is not None:
                await record(stage, body)

        async def post() -> httpx.Response:
            response = await request_with_scoped_credentials(
                http,
                "POST",
                self._authed_url("/prompt"),
                headers=self._headers,
                json={"prompt": dict(workflow), "client_id": client_id},
                auth_query=self._query,
            )
            if response.status_code >= 400:
                body = _body_or_text(response)
                await note("submit", body)
                if response.status_code == 400:
                    # 400 是 ComfyUI 拒收这份图的固定形状，与 200 带 node_errors 同因；交给
                    # submit_post 按状态码分流会把它说成一次通用的上游拒绝。
                    raise _node_errors_error(body, workflow)
            return response

        response = await retry_async(lambda: submit_post(post, provider="comfyui"), retry_if=should_retry_submit)
        body = _body_or_text(response)
        await note("submit", body)
        if isinstance(body, Mapping) and body.get("node_errors"):
            raise _node_errors_error(body, workflow)
        prompt_id = str(body.get("prompt_id") or "").strip() if isinstance(body, Mapping) else ""
        if not prompt_id:
            raise ComfyuiError(NODE_ERRORS, nodes=0, summary="submit response did not contain a prompt_id")
        logger.info("ComfyUI 已提交: %s", format_kwargs_for_log({"prompt_id": prompt_id, "client_id": client_id}))
        return prompt_id

    async def fetch_history(self, http: httpx.AsyncClient, prompt_id: str) -> Mapping[str, Any] | None:
        """取一次执行记录；还没有记录时返回 ``None``。

        两种形状都接受：``{prompt_id: {...}}`` 包裹的与根即条目的。不同版本与代理各有一种，按
        其中一种写死会让另一半部署永远轮询不到终态。

        2xx 却读不出记录（非 JSON 的代理错误页、``{"error": ...}`` 之类）不在这里吸收：这一格
        与声明式通道同口径，判为确定性失败而不是继续轮询。
        """
        response = await request_with_scoped_credentials(
            http,
            "GET",
            self._authed_url(f"/history/{prompt_id}"),
            headers=self._headers,
            json=None,
            auth_query=self._query,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise redacted_status_error(exc) from None
        body = response.json()
        if not isinstance(body, Mapping) or not body:
            return None
        wrapped = body.get(prompt_id)
        entry = wrapped if isinstance(wrapped, Mapping) else body
        return entry or None

    async def queue_snapshot(self, http: httpx.AsyncClient) -> tuple[list[str], list[str]] | None:
        """``GET /queue`` 的两张单子，各取其中的 ``prompt_id``（running 在前、pending 在后）。

        条目是数组 ``[序号, prompt_id, prompt, 待执行节点, 额外数据]``，id 在下标 1；一并认
        ``{"prompt_id": ...}`` 形态的条目，反向代理改写这张表时给的是后者。

        读不出这张表时给 ``None`` 而不是两张空单子：调用方据「不在队列里」判任务丢失，把一份
        看不懂的响应算成空队列会因为代理回了一页 HTML 就把仍在跑的执行判死。两张单子缺一或不是
        数组同属读不出——``{"error": "restarting"}`` 这类 200 响应解得出 JSON，却同样没有队列
        内容可读。``None`` 只管响应体读不懂这一种；HTTP 失败照常抛出，由调用方按自己那一格该不该
        据此判死来处置。
        """
        response = await request_with_scoped_credentials(
            http,
            "GET",
            self._authed_url("/queue"),
            headers=self._headers,
            json=None,
            auth_query=self._query,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise redacted_status_error(exc) from None
        try:
            body = response.json()
        except ValueError:
            return None
        if not isinstance(body, Mapping):
            return None
        running, pending = body.get("queue_running"), body.get("queue_pending")
        if not isinstance(running, list) or not isinstance(pending, list):
            return None
        return _queue_ids(running), _queue_ids(pending)

    async def server_version(self, http: httpx.AsyncClient) -> str | None:
        """``GET /system_stats`` 回的 ``system.comfyui_version``；读不到给 ``None``。

        与连通性检查读的是同一个字段。老版本与部分代理不回这一字段，故「读不到」不是错误，
        由调用方按低版本路径处置。
        """
        response = await request_with_scoped_credentials(
            http,
            "GET",
            self._authed_url("/system_stats"),
            headers=self._headers,
            json=None,
            auth_query=self._query,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # 地址带着按 query 传的凭证，裸 HTTPStatusError 的消息会把它原样写进日志。
            raise redacted_status_error(exc) from None
        body = response.json()
        system = body.get("system") if isinstance(body, Mapping) else None
        version = system.get("comfyui_version") if isinstance(system, Mapping) else None
        # 非字符串一律当读不到：调用方据此走低版本路径，那条路在新版本上同样有效。
        return version.strip() or None if isinstance(version, str) else None

    async def cancel_job(self, http: httpx.AsyncClient, prompt_id: str) -> None:
        """``POST /api/jobs/{id}/cancel``：新版本上一个动作同时覆盖排队中与执行中。"""
        await self._post_and_raise(http, f"/api/jobs/{prompt_id}/cancel", body=None)

    async def drop_from_queue(self, http: httpx.AsyncClient, prompt_id: str) -> None:
        """``POST /queue {"delete": [id]}``：低版本上取消一个还在排队的执行。"""
        await self._post_and_raise(http, "/queue", body={"delete": [prompt_id]})

    async def interrupt(self, http: httpx.AsyncClient) -> None:
        """``POST /interrupt``：低版本上打断**当前正在执行**的那一个，不认 id。

        因此调用方必须先确认 running 里就是自己这一笔，否则打断的是别人的活。
        """
        await self._post_and_raise(http, "/interrupt", body=None)

    async def _post_and_raise(self, http: httpx.AsyncClient, path: str, *, body: object | None) -> None:
        response = await request_with_scoped_credentials(
            http,
            "POST",
            self._authed_url(path),
            headers=self._headers,
            json=body,
            auth_query=self._query,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise redacted_status_error(exc) from None

    async def download_output(
        self,
        http: httpx.AsyncClient,
        artifact: Mapping[str, Any],
        output_path: Path,
        *,
        max_wait: float,
    ) -> None:
        """按一条产物条目取文件落到 ``output_path``。

        ``/view`` 与其余路由同源，故带上同一份凭据；VHS 的 ``fullpath`` 不用——那是服务端本机的
        绝对路径，只有与 ComfyUI 同机部署时才碰巧可读。
        """

        async def once() -> None:
            await stream_to_file(
                http,
                self._url("/view"),
                output_path,
                headers=self._headers,
                # 凭证与产物参数同走首跳的 params：``stream_to_file`` 的 ``params`` 会整串替换
                # 查询串，拼进 URL 的那一份会被它冲掉。同源续跳时由 ``auth_query`` 补回。
                params={
                    **self._query,
                    "filename": str(artifact.get("filename") or ""),
                    "subfolder": str(artifact.get("subfolder") or ""),
                    "type": str(artifact.get("type") or "output"),
                },
                credential_origin=self._credential_origin,
                auth_query=self._query,
            )

        await with_artifact_retry(once, label="comfyui artifact download", max_wait=max_wait)


def _queue_ids(entries: object) -> list[str]:
    if not isinstance(entries, list):
        return []
    found: list[str] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            candidate = entry.get("prompt_id")
        elif isinstance(entry, list) and len(entry) > 1:
            candidate = entry[1]
        else:
            continue
        if isinstance(candidate, str) and candidate:
            found.append(candidate)
    return found


def _body_or_text(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return response.text


def _node_errors_error(body: object, workflow: Mapping[str, Any]) -> ComfyuiError:
    """把一次拒收编码成失败：params 只带节点数与首条摘要，完整结构留痕在诊断列上。

    摘要取 ``<节点标题或 class_type>: <message>``：用户在 ComfyUI 里看到的是标题，报里只给
    节点号要他自己去图上数。
    """
    node_errors = body.get("node_errors") if isinstance(body, Mapping) else None
    if isinstance(node_errors, Mapping) and node_errors:
        node_id, first = next(iter(node_errors.items()))
        summary = _node_error_summary(str(node_id), first, workflow)
        return ComfyuiError(NODE_ERRORS, nodes=len(node_errors), summary=summary)
    return ComfyuiError(NODE_ERRORS, nodes=0, summary=_rejection_summary(body))


def _node_error_summary(node_id: str, detail: object, workflow: Mapping[str, Any]) -> str:
    """节点标题优先于 class_type：用户在 ComfyUI 画布上看到的是自己起的标题。

    标题只在 workflow 的 ``_meta`` 里，``node_errors`` 自己不带；底稿改过标题而报错沿用 class_type
    时，用户得对着图逐个数节点号才认得出是哪一个。
    """
    if not isinstance(detail, Mapping):
        return node_id
    node = workflow.get(node_id)
    meta = node.get("_meta") if isinstance(node, Mapping) else None
    title = str(meta.get("title") or "").strip() if isinstance(meta, Mapping) else ""
    label = title or str(detail.get("class_type") or "").strip() or node_id
    errors = detail.get("errors")
    message = ""
    if isinstance(errors, list) and errors and isinstance(errors[0], Mapping):
        message = str(errors[0].get("message") or "").strip()
    return f"{label}: {message}" if message else label


def _rejection_summary(body: object) -> str:
    """没有 ``node_errors`` 的拒收（整图级的错误、或代理自己回的 400）取它的 message。"""
    error = body.get("error") if isinstance(body, Mapping) else None
    if isinstance(error, Mapping):
        return str(error.get("message") or error.get("type") or "").strip() or "prompt was refused"
    return "prompt was refused"


def client_id_for(job_label: str) -> str:
    """一次提交带上的客户端标识：``arcreel-<任务标识>``。

    ComfyUI 的队列界面按 ``client_id`` 分组显示，带上前缀才能让用户在自己手动跑的队列里认出
    哪几笔是 ArcReel 发的。提交与端点测试的预览请求共用这一份——预览给出的必须是真发的那个形状。
    """
    return f"{CLIENT_ID_PREFIX}{job_label}"
