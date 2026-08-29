"""测试连接：真实提交一次生成并轮询到终态，会产生费用。

跑的是生产那一条路——同一个 backend 类、同一个 ``poll_with_retry``（全局
``video_poll_timeout_seconds``、连续失败预算、退避与 ``Retry-After``）、同一个下载路径。只有承载
不同：进程内 asyncio 任务，不走 tasks/worker 队列，产物不进项目也不进资产库。这样「测试连接通过」
才等价于「这个模型行真的能用」，而不是等价于「另一条只在测试里存在的路径能用」。

状态只在内存里活到终态，终态一到写盘（``app_data_dir()/trial_runs/{id}/``）并从内存移除，读接口
一律读盘；24 小时后整目录清掉。取消不通知供应商，只停本地轮询，记账按失败结算——钱可能已经花了，
账本不能因为用户点了取消就假装没发生。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from lib.app_data_dir import app_data_dir
from lib.config.resolver import ConfigResolver
from lib.custom_provider.declarative_backend import DeclarativeVideoBackend
from lib.db.base import DEFAULT_USER_ID
from lib.db.repositories.usage_repo import bound_provider_response
from lib.ledger import Ledger
from lib.video_backends.base import VideoGenerationRequest
from lib.video_frame_slots import resolve_first_frame_aspect_ratio

from .check import STAGES, check_response, stage_report_payload
from .inputs import EndpointTestCredentials, EndpointTestParameters

logger = logging.getLogger(__name__)

#: 结果目录的存活时长。清理时机是进程启动与每次新建，不挂定时器。
TRIAL_RUN_TTL_SECONDS = 24 * 3600

#: 结果体里保留的轮询响应条数。
MAX_POLL_RESPONSES = 20

_RESULT_FILE = "result.json"
_ARTIFACT_FILE = "artifact.mp4"

#: ``start`` 生成的 run_id 形状（``uuid4().hex``）。
_RUN_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


class TrialRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TrialRunBusyError(Exception):
    """该用户已有一个测试连接在跑。"""


@dataclass(frozen=True)
class TrialRunTarget:
    """一次测试连接的目标：记账身份 + backend 构造方式。

    内联定义与模型行两条入口在这里汇合成同一个值对象，之后的记账、轮询、写盘与取消对两者完全
    一致——「内置与自定义同一资源、同一结果体、同一记账」就是这个汇合点的直接结果。
    ``definition`` 在目标是声明式端点（内联定义，或模型行挂着自定义 / 内置声明式端点）时才有，
    它决定结果体里能不能给出渲染后的请求与逐阶段提取；Python 实现的端点两段为空。
    """

    provider: str
    model: str
    build_backend: Callable[[], Awaitable[Any]]
    definition: Mapping[str, Any] | None = None


def _as_float(value: object) -> float | None:
    """读盘字段按声明类型收口：坏值抛出后由 ``get`` 归入受控失败路径（读接口 404，不是 500）。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"不是数值：{value!r}")
    return float(value)


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError(f"不是整数：{value!r}")
    return int(value)


def _as_str(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise TypeError(f"不是字符串：{value!r}")


@dataclass
class TrialRun:
    """一次测试连接的完整结果体。运行中只有前几个字段有值。"""

    id: str
    status: TrialRunStatus
    provider: str
    model: str
    created_at: float
    finished_at: float | None = None
    api_call_id: int | None = None
    #: 渲染并脱敏后的提交请求；无定义可渲（Python 实现的端点）为 None。
    request: dict[str, Any] | None = None
    submit_response: object | None = None
    poll_responses: list[object] = field(default_factory=list)
    #: 逐阶段提取，键取自 :data:`check.STAGES`；无定义可读（Python 实现的端点）为空。
    extractions: dict[str, Any] = field(default_factory=dict)
    video_url: str | None = None
    duration_seconds: int | None = None
    error: str | None = None
    has_artifact: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.value,
            "provider": self.provider,
            "model": self.model,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "api_call_id": self.api_call_id,
            "request": self.request,
            "submit_response": self.submit_response,
            "poll_responses": self.poll_responses,
            "extractions": self.extractions,
            "video_url": self.video_url,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "has_artifact": self.has_artifact,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TrialRun:
        return cls(
            id=str(payload["id"]),
            status=TrialRunStatus(payload["status"]),
            provider=str(payload.get("provider", "")),
            model=str(payload.get("model", "")),
            created_at=_as_float(payload.get("created_at")) or 0.0,
            finished_at=_as_float(payload.get("finished_at")),
            api_call_id=_as_int(payload.get("api_call_id")),
            request=payload.get("request"),
            submit_response=payload.get("submit_response"),
            poll_responses=list(payload.get("poll_responses") or []),
            extractions=dict(payload.get("extractions") or {}),
            video_url=_as_str(payload.get("video_url")),
            duration_seconds=_as_int(payload.get("duration_seconds")),
            error=_as_str(payload.get("error")),
            has_artifact=bool(payload.get("has_artifact")),
        )


class _ResponseCapture:
    """按运行时的固定次序收供应商响应：提交一次，其后是轮询与可选的二次取件。

    每条按与账本诊断列同一个 64 KiB 上限收口。提交经重试才成功时，首条留痕是失败的那次尝试，
    成功的那次会落进轮询序列——两者都是提交阶段的真实响应，不额外猜。
    """

    def __init__(self) -> None:
        self.submit: object | None = None
        self.polls: deque[object] = deque(maxlen=MAX_POLL_RESPONSES)
        self._seen_submit = False

    def add(self, body: object) -> None:
        bounded = bound_provider_response(body)
        if not self._seen_submit:
            self._seen_submit = True
            self.submit = bounded
            return
        self.polls.append(bounded)


class TrialRunManager:
    """进程内测试连接的登记处：并发闸、后台任务、终态写盘与 TTL 清理。"""

    def __init__(
        self,
        *,
        root: Path | None = None,
        ledger: Ledger | None = None,
        read_poll_timeout: Callable[[], Awaitable[int]] | None = None,
    ) -> None:
        self._root = root
        self._ledger = ledger or Ledger()
        self._read_poll_timeout = read_poll_timeout
        self._runs: dict[str, TrialRun] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._owners: dict[str, str] = {}  # run_id -> user_id
        self._staging: dict[str, Path] = {}  # run_id -> 素材临时目录
        self._response_writes: dict[str, set[asyncio.Task[None]]] = {}  # run_id -> 在途诊断写入

    @property
    def root(self) -> Path:
        root = self._root or (app_data_dir() / "trial_runs")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def purge_expired(self, *, now: float | None = None) -> int:
        """清掉过了 TTL 的结果目录，返回清理条数。进程启动与每次新建各扫一遍。"""
        moment = now if now is not None else time.time()
        removed = 0
        for entry in self.root.iterdir():
            if not entry.is_dir() or entry.name in self._runs:
                continue
            try:
                if not _expired(entry, moment):
                    continue
                _remove_tree(entry)
            except OSError:
                logger.warning("测试连接结果目录清理失败：%s", entry.name, exc_info=True)
                continue
            removed += 1
        return removed

    def _expired(self, run_id: str) -> bool:
        """结果是否过了 TTL。清理与读取共用这一条判定。

        清理只在进程启动与每次新建时扫，读接口自己判：一台长时间没有新 run 的服务器上，
        「24 小时后结果不可读」不能取决于恰好有没有人再发一次测试连接。
        """
        try:
            return _expired(self.root / run_id, time.time())
        except OSError:
            return False

    async def start(
        self,
        target: TrialRunTarget,
        parameters: EndpointTestParameters,
        *,
        user_id: str = DEFAULT_USER_ID,
        assets: Mapping[str, Path | list[Path] | None] | None = None,
        staging: Path | None = None,
        request_preview: dict[str, Any] | None = None,
    ) -> TrialRun:
        """派发一次测试连接。同一用户已有在跑的 run 时拒绝。

        Raises:
            TrialRunBusyError: 该用户已有一个测试连接在跑。
        """
        if user_id in self._owners.values():
            raise TrialRunBusyError(user_id)
        self.purge_expired()
        run_id = uuid.uuid4().hex
        run = TrialRun(
            id=run_id,
            status=TrialRunStatus.QUEUED,
            provider=target.provider,
            model=target.model,
            created_at=time.time(),
            request=request_preview,
        )
        self._runs[run_id] = run
        self._owners[run_id] = user_id
        if staging is not None:
            self._staging[run_id] = staging
        # 名额与素材目录先占后建目录：建目录失败（盘满、只读挂载）时占位不还，后续测试永远
        # 被拒成 busy，而取消又因 task 尚未登记而 404——与结算失败那条路同一种死锁。
        try:
            (self.root / run_id).mkdir(parents=True, exist_ok=True)
            task = asyncio.create_task(self._execute(run, target, parameters, assets or {}))
        except Exception:
            self._release(run_id)
            _remove_tree(self.root / run_id)
            raise
        self._tasks[run_id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(run_id, None))
        return run

    def get(self, run_id: str) -> TrialRun | None:
        """读一次 run：运行中读内存，终态读盘。取消或重启中断的 run 没有结果文件。"""
        if run_id in self._runs:
            return self._runs[run_id]
        # run_id 是调用方传入的路径段，读盘前先按生成格式校验，防 ".."（Windows 上还有反斜杠）越出 root。
        if _RUN_ID_PATTERN.fullmatch(run_id) is None:
            return None
        if self._expired(run_id):
            return None
        path = self.root / run_id / _RESULT_FILE
        try:
            # 反序列化与读盘同属一条受控失败路径：读不出一个可用的 run 一律 None。
            return TrialRun.from_payload(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, KeyError, TypeError, ValueError):
            return None

    async def wait(self, run_id: str) -> TrialRun | None:
        """等一次 run 停下并返回它的终态。已经终态或不存在的 run 立即返回。

        产品路径靠 ``GET`` 轮询，不靠这里；它服务的是「必须等这一笔跑完」的场合——进程关停前
        排空、以及测试。
        """
        task = self._tasks.get(run_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return self.get(run_id)

    def artifact_path(self, run_id: str) -> Path | None:
        if _RUN_ID_PATTERN.fullmatch(run_id) is None:
            return None
        if self._expired(run_id):
            return None
        path = self.root / run_id / _ARTIFACT_FILE
        return path if path.is_file() else None

    async def cancel(self, run_id: str) -> bool:
        """停本地轮询并按失败结算。不通知供应商——远端任务照跑，钱照花。"""
        task = self._tasks.get(run_id)
        run = self._runs.get(run_id)
        if task is None or run is None:
            return False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # 被 shield 保护的诊断写入在任务停下后可能还在跑：结算前等它写完，否则 resume_failed
        # 会与它并发写同一行，关停路径还可能在写入未完时就关库。
        for write in list(self._response_writes.get(run_id, ())):
            with contextlib.suppress(Exception):
                await write
        # 记账在被取消的协程里补不了（那条协程正在被取消，任何 await 都可能再次被打断），
        # 所以由取消方在任务确实停下之后翻账：pending 行绝不能留给一笔用户已经放弃的调用。
        # 结算抛错也要把名额与盘上目录还掉——任务已经停了，占位留下去会把后续测试永远拒成 busy，
        # 而重试取消又因 task 已移除而 404，死锁只能重启解。
        try:
            if run.api_call_id is not None:
                await self._ledger.resume_failed(call_id=run.api_call_id)
        finally:
            self._release(run_id)
            _remove_tree(self.root / run_id)
        return True

    async def _execute(
        self,
        run: TrialRun,
        target: TrialRunTarget,
        parameters: EndpointTestParameters,
        assets: Mapping[str, Path | list[Path] | None],
    ) -> None:
        capture = _ResponseCapture()
        run.status = TrialRunStatus.RUNNING
        try:
            poll_timeout = await self._poll_timeout()
            # backend 先装配再开账：装配失败（模型行不存在、供应商配错）时一个字节都没发出去，
            # 记一笔 pending 再翻成 failed 会让账本上多一条根本没打过的调用。
            backend = await target.build_backend()
            # 生产路径在记账括号前跑同一道能力闸（声明的违约在付费前拒绝，不发给供应商）；
            # 测试连接跑的是生产那条路，闸也一致。
            await _gate_trial_request(backend, target, parameters, assets)
            # 声明 first_frame_ratio_adaptive_only 的端点在带首帧的请求上只接受 adaptive；
            # 下发值与记账值分离，账本记的仍是用户填的比例意图（与生产同一分工）。
            request_aspect_ratio = resolve_first_frame_aspect_ratio(
                caps=getattr(backend, "video_capabilities", None),
                aspect_ratio=parameters.aspect_ratio,
                has_first_frame=_single(assets.get("start_image")) is not None,
            )
            async with self._ledger.record(
                project_name="",
                call_type="video",
                model=target.model,
                provider=target.provider,
                prompt=parameters.prompt,
                resolution=parameters.resolution,
                duration_seconds=parameters.duration_seconds,
                aspect_ratio=parameters.aspect_ratio,
                generate_audio=parameters.generate_audio,
            ) as call:
                run.api_call_id = call.call_id
                result = await backend.generate(
                    self._request(
                        run,
                        parameters,
                        assets,
                        capture,
                        call.call_id,
                        poll_timeout,
                        aspect_ratio=request_aspect_ratio,
                    )
                )
                call.success(result)
                run.video_url = getattr(result, "video_uri", None)
                run.duration_seconds = getattr(result, "duration_seconds", None)
                run.has_artifact = self._artifact_file(run.id).is_file()
            self._finish(run, target, capture, TrialRunStatus.SUCCEEDED)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            run.error = str(exc)
            self._finish(run, target, capture, TrialRunStatus.FAILED)

    def _request(
        self,
        run: TrialRun,
        parameters: EndpointTestParameters,
        assets: Mapping[str, Path | list[Path] | None],
        capture: _ResponseCapture,
        call_id: int,
        poll_timeout: int,
        *,
        aspect_ratio: str,
    ) -> VideoGenerationRequest:
        async def on_provider_response(body: object) -> None:
            capture.add(body)
            # 用户随时可能取消，而取消可能正落在这次写入中间。诊断留痕的写入被拦腰截断会留下
            # 一个半开的事务，随后的失败结算就得在一条坏掉的连接上做——shield 让这次写入自己跑完，
            # 取消照常传给调用它的那一层。写入任务登记在 run 名下：shield 只保护协程不被取消，
            # 外层任务停下后它还在跑，取消方结算前要等它（见 ``cancel``）。
            write = asyncio.ensure_future(self._ledger.record_provider_response(call_id=call_id, body=body))
            writes = self._response_writes.setdefault(run.id, set())
            writes.add(write)
            write.add_done_callback(writes.discard)
            await asyncio.shield(write)

        reference_images = assets.get("reference_images")
        reference_audio = assets.get("reference_audio_files")
        return VideoGenerationRequest(
            prompt=parameters.prompt,
            output_path=self._artifact_file(run.id),
            aspect_ratio=aspect_ratio,
            duration_seconds=parameters.duration_seconds,
            resolution=parameters.resolution,
            start_image=_single(assets.get("start_image")),
            end_image=_single(assets.get("end_image")),
            reference_images=list(reference_images) if isinstance(reference_images, list) else None,
            reference_audio_files=list(reference_audio) if isinstance(reference_audio, list) else None,
            generate_audio=parameters.generate_audio,
            poll_timeout_seconds=poll_timeout,
            on_provider_response=on_provider_response,
        )

    async def _poll_timeout(self) -> int:
        if self._read_poll_timeout is not None:
            return await self._read_poll_timeout()
        from lib.config.service import read_video_poll_timeout_seconds

        return await read_video_poll_timeout_seconds()

    def _finish(
        self,
        run: TrialRun,
        target: TrialRunTarget,
        capture: _ResponseCapture,
        status: TrialRunStatus,
    ) -> None:
        run.status = status
        run.finished_at = time.time()
        run.submit_response = capture.submit
        run.poll_responses = list(capture.polls)
        run.extractions = _stage_reports(target.definition, capture, succeeded=status is TrialRunStatus.SUCCEEDED)
        try:
            self._result_file(run.id).write_text(
                json.dumps(run.to_payload(), ensure_ascii=False, default=str), encoding="utf-8"
            )
        except OSError:
            logger.warning("测试连接结果写盘失败 run_id=%s", run.id, exc_info=True)
        self._release(run.id)

    async def shutdown(self) -> None:
        """进程关停：停掉所有在跑的 run 并按失败结算。

        任务随事件循环消亡时 ``Ledger.record`` 的 ``CancelledError`` 穿透会把 pending 行
        永远留在账本上——没有进程会回来翻它；由关停方走取消那条结算路径。

        单条 run 结算失败（如库已不可用）不中断这趟排空：异常穿透出去会让后面的 run 一条都
        不停，关停流程里排在本步之后的关 HTTP 客户端与关库也一起跳过。
        """
        for run_id in list(self._tasks):
            try:
                await self.cancel(run_id)
            except Exception:
                logger.warning("关停时结算测试连接失败：%s", run_id, exc_info=True)

    def _release(self, run_id: str) -> None:
        """让出用户的并发名额，并删掉素材临时目录——素材用完即丢，不留在盘上。"""
        self._runs.pop(run_id, None)
        self._owners.pop(run_id, None)
        self._response_writes.pop(run_id, None)
        staging = self._staging.pop(run_id, None)
        if staging is not None:
            _remove_tree(staging)

    def _result_file(self, run_id: str) -> Path:
        return self.root / run_id / _RESULT_FILE

    def _artifact_file(self, run_id: str) -> Path:
        return self.root / run_id / _ARTIFACT_FILE


async def _gate_trial_request(
    backend: Any,
    target: TrialRunTarget,
    parameters: EndpointTestParameters,
    assets: Mapping[str, Path | list[Path] | None],
) -> None:
    from lib.audio_utils import probe_reference_audio_total_seconds
    from lib.video_frame_slots import gate_video_request, plan_frame_slots

    reference_images = assets.get("reference_images")
    reference_audio = assets.get("reference_audio_files")
    audio_files = list(reference_audio) if isinstance(reference_audio, list) else None
    # 与生产路径同一探测：总时长探不出（ffprobe 不可用）传 None，闸按未知跳过该项而非拒绝。
    total_seconds = await probe_reference_audio_total_seconds(audio_files) if audio_files else None
    images = list(reference_images) if isinstance(reference_images, list) else None
    end_image = _single(assets.get("end_image"))
    # has_image 取整份槽位计划，与生产同源：参考图驱动的端点（如 S2V）没有首帧但带参考图，
    # 只看首帧会把每一次合法请求都判成纯文生、按 text_to_video=false 拒掉。
    slot_plan = plan_frame_slots(
        start_image=_single(assets.get("start_image")),
        end_image=end_image,
        reference_images=images,
    )
    gate_video_request(
        caps=getattr(backend, "video_capabilities", None),
        provider=target.provider,
        model=target.model,
        prompt=parameters.prompt,
        has_image=bool(slot_plan.specs),
        end_image=end_image,
        reference_images=images,
        reference_audio_files=audio_files,
        reference_audio_total_seconds=total_seconds,
    )


def declarative_target(
    definition: Mapping[str, Any],
    credentials: EndpointTestCredentials,
    parameters: EndpointTestParameters,
    *,
    provider: str | None = None,
) -> TrialRunTarget:
    """内联定义的目标：直接构造声明式 backend。

    ``provider`` 缺省取 ``base_url`` 的 host——内联凭证没有供应商身份，而账本必须落一个能让用户
    日后认出这笔钱花在哪里的值。三节 URL 都写死绝对地址的定义没有 base_url 可取，退而取提交
    地址的 host：那正是这笔钱实际打给谁。
    """
    label = provider or provider_from_base_url(credentials.base_url) or _definition_host(definition)

    async def build() -> DeclarativeVideoBackend:
        return DeclarativeVideoBackend(
            api_key=credentials.api_key,
            base_url=credentials.base_url,
            model=parameters.model,
            definition=definition,
            provider=label,
        )

    return TrialRunTarget(
        provider=label,
        model=parameters.model,
        build_backend=build,
        definition=definition,
    )


def model_ref_target(
    provider_id: str,
    model_id: str,
    *,
    resolver: ConfigResolver,
    definition: Mapping[str, Any] | None = None,
) -> TrialRunTarget:
    """模型行的目标：经生产那道构造缝装配 backend，内置与自定义供应商同一入口。

    ``definition`` 在模型行解析出声明式定义（自定义调用端点或内置声明式端点）时由调用方带上，
    用来在结果体里给出渲染后的请求与逐阶段提取；Python 实现的端点两段留空。
    """

    async def build() -> Any:
        from lib.backend_assembly import assemble_backend

        return await assemble_backend(provider_id=provider_id, media_type="video", model_id=model_id, resolver=resolver)

    return TrialRunTarget(provider=provider_id, model=model_id, build_backend=build, definition=definition)


def provider_from_base_url(base_url: str) -> str:
    return urlsplit(base_url).hostname or base_url


def _definition_host(definition: Mapping[str, Any]) -> str:
    return urlsplit(str(definition.get("submit", {}).get("url", ""))).hostname or ""


def _single(value: Path | list[Path] | None) -> Path | None:
    return value if isinstance(value, Path) else None


def _stage_reports(
    definition: Mapping[str, Any] | None, capture: _ResponseCapture, *, succeeded: bool
) -> dict[str, Any]:
    """按运行时的固定次序把留痕对回三节。没有定义可读（Python 实现的端点）返回空。

    只在能确定对应关系时才出报告：成功且定义声明了二次取件节时，最后一条留痕必然是取件响应，
    其前一条是最后一次轮询；其余情形最后一条就是最后一次轮询。对不上就不报，不猜。
    """
    if definition is None:
        return {}
    has_result = "result" in definition
    polls = list(capture.polls)
    bodies: dict[str, object] = {}
    if capture.submit is not None:
        bodies["submit"] = capture.submit
    if succeeded and has_result and len(polls) >= 2:
        bodies["poll"] = polls[-2]
        bodies["result"] = polls[-1]
    elif polls:
        bodies["poll"] = polls[-1]
    reports: dict[str, Any] = {}
    for stage in STAGES:
        body = bodies.get(stage)
        if body is None:
            continue
        try:
            reports[stage] = stage_report_payload(check_response(definition, stage, body))
        except Exception:
            logger.debug("测试连接的 %s 节提取报告生成失败", stage, exc_info=True)
    return reports


def _expired(entry: Path, now: float) -> bool:
    return now - entry.stat().st_mtime > TRIAL_RUN_TTL_SECONDS


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


_manager: TrialRunManager | None = None


def trial_run_manager() -> TrialRunManager:
    """进程内唯一的登记处。首次取用时扫一遍过期目录（重启中断的 run 没有结果文件，一并清掉）。"""
    global _manager
    if _manager is None:
        _manager = TrialRunManager()
        _manager.purge_expired()
    return _manager


async def shutdown_trial_runs() -> None:
    """进程关停入口：登记处从未被取用时不为关停凭空建一台。"""
    if _manager is not None:
        await _manager.shutdown()
