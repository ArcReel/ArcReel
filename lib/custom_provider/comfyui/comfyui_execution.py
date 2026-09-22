"""一次已提交的 ComfyUI 执行：轮询到终态、判成败、取件落盘，取消与超时顺手叫停远端。

图像与视频两条通道从提交那一刻起做的是同一件事——``prompt_id`` 到手之后，剩下的只有「这次执行
走完了没有」「走完是成功还是失败」「产物是哪个文件」「本地不要了怎么把远端也停掉」。这四个判断
与媒体类型无关（差别只在产物扩展名白名单，收在 :mod:`.artifacts` 的那张表上），两个
backend 各写一份的话，判丢失与叫停这类只在异常路径上跑到的逻辑迟早各判各的。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from lib.backends.artifact_download_guard import ARTIFACT_MAX_BYTES_BY_MEDIA_TYPE
from lib.backends.backend_runtime import poll_with_retry, should_retry_poll
from lib.custom_provider.comfyui.artifacts import (
    filename_of,
    history_digest,
    output_artifacts,
    output_nodes_of,
    pick_artifact,
    terminal_failure,
)
from lib.custom_provider.comfyui.comfyui_client import ComfyuiClient, RecordResponse
from lib.custom_provider.comfyui.failures import JOB_LOST, OUTPUT_MISSING, OUTPUT_TYPE_MISMATCH, ComfyuiError

logger = logging.getLogger(__name__)

#: 生成路径上每个 HTTP 请求的超时。
HTTP_TIMEOUT_SECONDS = 60

#: 叫停远端用的超时。比生成路径的短得多：这几个请求发在任务已被取消之后，一台不响应的
#: ComfyUI 不该把 worker 的关停拖上几分钟。
_STOP_TIMEOUT_SECONDS = 15

#: 从这一版起 ``POST /api/jobs/{id}/cancel`` 一个动作同时覆盖排队中与执行中；更早的版本
#: 只有「删队列项」与「打断当前执行」两个分开的动作。
_JOB_CANCEL_MIN_VERSION = (0, 26, 0)


@dataclass(frozen=True)
class PickedArtifact:
    """这次执行取走的那个产物，以及它是从几个里挑出来的。

    ``count`` 交给调用方：多产物在视频通道上落成任务 ``result.warnings`` 的一条提示，在图像通道
    上只有日志（``ImageGenerationResult`` 没有提示位），两处该说什么由各自决定。
    """

    artifact: Mapping[str, Any]
    count: int

    @property
    def filename(self) -> str:
        return filename_of(self.artifact)


class ComfyuiExecution:
    """接在一台 ComfyUI 上、围绕一个 ``prompt_id`` 的那半次生成。

    构造只记连接与定义，不发请求：一次生成的 HTTP 客户端由 backend 持有（素材上传与提交也用
    它），本类的取件方法收它作参数。叫停那一路例外——它自己另开一个短超时的客户端。
    """

    def __init__(self, *, client: ComfyuiClient, definition: Mapping[str, Any], media_type: str) -> None:
        self._client = client
        self._media_type = media_type
        self._output_nodes = output_nodes_of(definition)

    async def fetch_artifact(
        self,
        http: httpx.AsyncClient,
        prompt_id: str,
        *,
        output_path: Path,
        poll_timeout_seconds: float,
        record: RecordResponse | None = None,
        lost_error: Callable[[str], BaseException] | None = None,
    ) -> PickedArtifact:
        """轮询到终态、挑出产物、下载到 ``output_path``；本地不要这次执行了就顺手停掉远端。

        ``lost_error`` 换掉「执行在 ComfyUI 上找不着了」这一格抛的异常：生成路径用
        ``comfyui_job_lost``（可就地重试的生成失败），续跑路径要的是 ``ResumeExpiredError``
        ——worker 据此结算那条 pending 的调用行，而不是把它当成一次可重试的生成。

        叫停只包轮询与取件：提交之前没有 ``prompt_id`` 可停，而提交本身的歧义态由 ``submit_post``
        处置。ComfyUI 跑在用户自己的显卡上，扔下一个没人要的执行会一直占着卡。
        """
        try:
            entry = await self._poll_to_terminal(
                http, prompt_id, poll_timeout_seconds=poll_timeout_seconds, record=record, lost_error=lost_error
            )
            picked = self._pick(entry)
            if record is not None:
                await record("result", {"artifact": dict(picked.artifact), "count": picked.count})
            await self._client.download_output(
                http,
                picked.artifact,
                output_path,
                max_wait=poll_timeout_seconds,
                max_bytes=ARTIFACT_MAX_BYTES_BY_MEDIA_TYPE[self._media_type],
            )
            return picked
        except (asyncio.CancelledError, TimeoutError):
            await self.stop_remote(prompt_id)
            raise

    def view_url(self, artifact: Mapping[str, Any]) -> str:
        """产物在这台 ComfyUI 上的取件地址，作为这一版的来源留痕。"""
        query = urlencode(
            {
                "filename": filename_of(artifact),
                "subfolder": str(artifact.get("subfolder") or ""),
                "type": str(artifact.get("type") or "output"),
            }
        )
        return f"{self._client.base_url}/view?{query}"

    # ------------------------------------------------------------------ 轮询与取件

    async def _poll_to_terminal(
        self,
        http: httpx.AsyncClient,
        prompt_id: str,
        *,
        poll_timeout_seconds: float,
        record: RecordResponse | None,
        lost_error: Callable[[str], BaseException] | None,
    ) -> Mapping[str, Any]:
        """轮询到 history 里出现这次执行的记录，并按记录判成败；失败即抛失败码。"""

        # 单元素列表包住这一轮的执行记录：``poll_with_retry`` 按 is_done 判终态，而「有没有记录」
        # 正是终态本身，空列表即「还没轮到」。
        async def poll_once() -> list[Mapping[str, Any]]:
            entry = await self._client.fetch_history(http, prompt_id)
            if entry is None:
                entry = await self._history_or_lost(http, prompt_id, lost_error=lost_error)
            if entry is None:
                return []
            if record is not None:
                await record("poll", history_digest(entry, self._output_nodes))
            return [entry]

        found = await poll_with_retry(
            poll_fn=poll_once,
            # 有记录即终态：ComfyUI 只在一次执行走完（成功、报错或被打断）之后才往 history 写。
            is_done=bool,
            is_failed=lambda _found: None,
            max_wait=poll_timeout_seconds,
            retry_if=should_retry_poll,
            label="comfyui",
        )
        entry = found[0]
        failure = terminal_failure(entry)
        if failure is not None:
            raise failure
        return entry

    def _pick(self, entry: Mapping[str, Any]) -> PickedArtifact:
        """从一条成功的终态记录里挑出这一类媒体的产物。"""
        artifacts = output_artifacts(entry, self._output_nodes)
        if not artifacts:
            raise ComfyuiError(OUTPUT_MISSING, nodes=" / ".join(self._output_nodes))
        artifact = pick_artifact(artifacts, self._media_type)
        if artifact is None:
            raise ComfyuiError(OUTPUT_TYPE_MISMATCH, filename=filename_of(artifacts[0]), media_type=self._media_type)
        if len(artifacts) > 1:
            logger.warning("ComfyUI 产物共 %d 个，取: %s", len(artifacts), filename_of(artifact))
        return PickedArtifact(artifact=artifact, count=len(artifacts))

    async def _history_or_lost(
        self, http: httpx.AsyncClient, prompt_id: str, *, lost_error: Callable[[str], BaseException] | None
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
        if lost_error is not None:
            raise lost_error(prompt_id)
        raise ComfyuiError(JOB_LOST, prompt_id=prompt_id)

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

    # ------------------------------------------------------------------ 叫停远端

    async def stop_remote(self, prompt_id: str) -> None:
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
