"""CoolhsVideoBackend — 万万工作台（wan.coolhs.com）视频后端。

该平台是万相模型的中转站，协议与官方不同，体现在三处：

- **素材分两步上传**：先 ``POST /api/portal/media/presign`` 换一个短时有效的对象存储签名
  地址，再 ``PUT`` 直传文件体，最后 ``POST /api/portal/media/commit`` 登记、拿附件 id。
  ``media[]`` 只认这个 id（早期「一次 POST 直接上传」的接口已下线，返回 410）。
- **参考图靠 ``token`` 在正文里点名**。每个 ``role="ref"`` 的素材带一个 ``token``（``图1``、
  ``图2``…），正文用同一个记号指代。编号必须与上传顺序严格一致，且与提示词里渲染出的
  「图N」对齐——提示词方言由 ``lib.script.reference_video.prompt_dialect`` 的 ``WAN30``
  负责，这里只保证素材按同一顺序编号。
- **首帧/尾帧与参考素材互斥**（与官方一致）：带 ``role="first"`` 的请求不能再带 ``ref``。

另有三条运营事实决定了本实现的形态：

1. **单次排队可达数小时**，故 :meth:`resume_video` 是必做项而非可选优化：进程重启后
   ``restart_recovery`` 只有能 resume 的 backend 才接得上，否则会标 ``resume_unsupported``
   直接判失败，而额度已经扣了。
2. **成片链接 24 小时过期**，故取片先读随手返回的 ``videoUrl``，缺失时改用按 job id 的
   下载接口——续跑可能发生在提交后很久，URL 早已失效。
3. **``promptExtend`` 默认 -1 会自动扩写**，扩写会改写提示词、稀释参考图约束（人物不像），
   故固定下发 ``0``。
4. **平台会整体关单**（``/api/portal/ride`` 的 ``accepting=false``）。此时提交一律 503，
   所以提交前先看班次；关单就按间隔等到开班，超上限才放弃——等待期零计费。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import (
    ProviderJobIdPersistenceMixin,
    download_resumable_video,
    poll_with_retry,
    recording_poll,
    resume_expiry_gate,
    should_retry_poll,
    submit_post,
)
from lib.backends.coolhs_shared import COOLHS_BASE_URL, COOLHS_USER_AGENT, coolhs_headers, coolhs_mime
from lib.backends.video_backends.name_family import (
    UNRECOGNIZED_MODEL_CAPABILITIES,
    capabilities_by_names,
)

logger = logging.getLogger(__name__)

PROVIDER_COOLHS = "coolhs"

#: 平台根地址。用户可在供应商配置里覆盖（自带 base_url 走同名 optional key）。
DEFAULT_COOLHS_BASE_URL = COOLHS_BASE_URL

#: 提交与素材上传都含校验收单（含素材规格检查），给足预算；轮询是轻量 GET，短超时即可。
_SUBMIT_TIMEOUT_SECONDS = 180.0
_UPLOAD_TIMEOUT_SECONDS = 300.0
_POLL_HTTP_TIMEOUT_SECONDS = 60.0

#: 轮询间隔。平台对查询有**同 IP 600 次 / 15 分钟**的硬限——平均 1.5 秒一次，且全部任务、
#: 上传、提交、取片共享这一个池子，折成 40 次/分钟。
#:
#: 默认的 5 秒间隔在并发 3 时已到 36 次/分钟（90% 额度），等于把并发锁死在 2-3；这里放到
#: 60 秒后单任务只占 1 次/分钟，并发 20 才 20 次/分钟、留一半余量给上传与重试。排队本身是
#: 小时级（2-3 小时），出片后最多晚 60 秒发现，对总时长没有实质影响。
_POLL_INTERVAL_SECONDS = 60.0

_MEDIA_PRESIGN_ENDPOINT = "/api/portal/media/presign"
_MEDIA_COMMIT_ENDPOINT = "/api/portal/media/commit"
_JOBS_ENDPOINT = "/api/portal/jobs"
#: 班次状态。「排队发车」模式下平台会整体关单（``accepting=false``），此时 jobs 提交一律 503。
_RIDE_ENDPOINT = "/api/portal/ride"

#: 班次预检是轻量 GET、且**只作优化**（失败就放行），故超时给短：宁可少拦一次，也不要让
#: 一个抖动的状态接口把整批生成卡住。真正接单与否以提交响应为准。
_RIDE_PRECHECK_TIMEOUT_SECONDS = 15.0

#: 平台关单期间自动等候：每 5 分钟看一次班次，最多等 12 小时。
#:
#: 为什么是 5 分钟：等待期的请求同样吃那 600 次/15 分钟的额度。并发 15 时 5 分钟一探只有
#: 3 次/分钟，占额度 5%，不会跟真正在跑的轮询抢预算。开盘通常按小时计，密度再高没有意义。
#: 为什么是 12 小时：跨夜排队是常态，短于此会把半夜该出的片子判成失败；再长则一个已经
#: 没人等的批次会永久占住 worker 槽位。到点即放弃等待并明确告知（此时**一分钱都没花**）。
_RIDE_WAIT_INTERVAL_SECONDS = 300.0
_RIDE_WAIT_MAX_SECONDS = 12 * 3600.0

#: 重试间隔加 ±20% 抖动：开盘瞬间十几个等待中的任务会同时醒来，齐步走会在一次爆发里
#: 上传全部素材（每个任务 N 张图），容易一脚踢到限流。抖动把这一波摊开。
_RIDE_WAIT_JITTER = 0.2

#: 平台档位（与官方万相一致）：2–30 秒、480P/720P/1080P。
_SUPPORTED_DURATIONS = tuple(range(2, 31))
#: 单条任务素材上限：图片 10、参考音频 5（音视频各自合计 ≤15 秒，由平台侧校验）。
_MAX_REFERENCE_IMAGES = 10
_MAX_REFERENCE_AUDIO_FILES = 5

#: ``wait.phase`` 的终态与失败态。``status`` 只作后备：``/v1`` 形状会把取消伪装成
#: ``in_progress``，读 ``wait.phase`` 才是准的。
_TERMINAL_PHASES = ("done", "failed", "cancelled")
_FAILED_PHASES = ("failed", "cancelled")

#: 万相家族的完整能力：文生视频、首尾帧、10 张参考图、5 段参考音频、音轨可控。
#: 首帧与参考图在平台侧**不叠加**（按官方口径拒绝混用），但契约层把两条路径摊平进同一个
#: 能力对象，调用方按 ``generation_mode`` 二选一，故这里两份都如实声明。
_FULL_WAN_CAPABILITIES = VideoCapabilities(
    text_to_video=True,
    first_frame=True,
    last_frame=True,
    max_reference_images=_MAX_REFERENCE_IMAGES,
    audio_track=VideoAudioMode.CONTROLLABLE,
    reference_audio_mode=ReferenceAudioMode.DIRECT,
    max_reference_audio_count=_MAX_REFERENCE_AUDIO_FILES,
)


def _match_capabilities(name: str) -> VideoCapabilities | None:
    """名字族匹配：``wan`` / ``wan-480p`` / ``coolhs-wan3`` 都命中，其它名字交给统一回落。"""
    return _FULL_WAN_CAPABILITIES if "wan" in name.lower() else None


class CoolhsRideClosedError(RuntimeError):
    """平台整体关单中（班次未开），此刻提交必被 503 拒。

    单独成类是为了让调用方一眼看出「不是我们写错了，也不是额度/素材问题，是平台这班车没开、
    过会儿再来就行」——而它**发生在提交之前**，所以不产生任何计费，也不会留下半成品任务。
    """


def _ride_label(state: dict[str, Any]) -> str:
    label = state.get("label")
    return label.strip() if isinstance(label, str) and label.strip() else ""


def _ride_counts(state: dict[str, Any]) -> str:
    board_count = state.get("boarded")
    run_count = state.get("running")
    return "、".join(
        part
        for part in (
            f"候车 {board_count} 条" if isinstance(board_count, int) else "",
            f"运行 {run_count} 条" if isinstance(run_count, int) else "",
        )
        if part
    )


def _ride_brief(state: dict[str, Any]) -> str:
    """一行摘要，给重试日志与最终报错用：标签 + 队列量。

    用逗号串联而不是再套一层括号——它会出现在另一对括号里（如超时放弃那句话），
    嵌套括号读起来很别扭。
    """
    parts = [part for part in (_ride_label(state), _ride_counts(state)) if part]
    return "，".join(parts) if parts else "状态不明"


def _human_duration(seconds: float) -> str:
    """把秒数说人话：短等待用秒/分钟，别把 20 秒写成「0 小时」。"""
    if seconds < 60:
        return f"{seconds:.0f} 秒"
    if seconds < 3600:
        return f"{seconds / 60:.0f} 分钟"
    return f"{seconds / 3600:.1f} 小时"


def _ride_refusal(state: dict[str, Any]) -> str | None:
    """读班次快照，返回拒单原因；能正常接单或状态不明时返回 ``None``。

    **只在能明确判定关单时返回原因**：``accepting`` 是显式布尔 ``false``，或该字段缺失而
    ``open`` 为显式 ``false``。字段缺失、类型不对、值含糊一律放行——预检的作用是"少打一次必被
    拒的提交"，不是新增一道能让整批生成停摆的闸门。真正的接单与否以提交响应为准。
    """
    accepting = state.get("accepting")
    if isinstance(accepting, bool):
        if accepting:
            return None
    elif "open" in state and isinstance(state.get("open"), bool):
        if state["open"]:
            return None
    else:
        return None
    return f"平台暂停接单：{_ride_brief(state)}。这是平台的发车节奏，稍后重新提交即可，未产生扣费。"


def _resolution_label(value: str | None) -> str:
    """ArcReel 的分辨率档位是小写 ``720p``，平台要大写 ``720P``（小写会被 400 拒）。"""
    normalized = (value or "480p").strip().upper()
    return "1080P" if normalized == "1080P" else ("720P" if normalized == "720P" else "480P")


def _job_id_for(request: VideoGenerationRequest) -> str:
    """提交用的任务 id。

    平台按这个 id 做幂等：内容一致时重试不重复扣费，换了 id 就是另一单。所以 worker 路径用
    ``task_id`` 派生确定性 UUID；非 worker 路径（预览、试跑）没有稳定身份，退回随机值。
    """
    if request.task_id:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"arcreel-coolhs:{request.task_id}"))
    return str(uuid.uuid4())


def _phase(state: dict[str, Any]) -> str:
    """``wait.phase`` 的值；结构缺失时退空串，让调用方按"非终态"继续等。"""
    wait = state.get("wait")
    return str(wait.get("phase") or "") if isinstance(wait, dict) else ""


def _state_of(payload: Any) -> dict[str, Any]:
    """任务对象可能平铺返回，也可能包一层 ``job``；两条形状都认。"""
    if isinstance(payload, dict):
        job = payload.get("job")
        if isinstance(job, dict):
            return job
        return payload
    return {}


def _is_terminal(state: dict[str, Any]) -> bool:
    return _phase(state) in _TERMINAL_PHASES or state.get("status") in _TERMINAL_PHASES


def _is_failure(state: dict[str, Any]) -> bool:
    """``cancelled`` 也算失败：额度已退回，这一笔不会有片子。"""
    return _phase(state) in _FAILED_PHASES or state.get("status") in _FAILED_PHASES


def _failure_reason(state: dict[str, Any]) -> str | None:
    """失败原因；平台在 ``wait.detail`` 或 ``error`` 里给可读中文。"""
    wait = state.get("wait")
    detail = wait.get("detail") if isinstance(wait, dict) else None
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    error = state.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return None


def _failure_message(state: dict[str, Any]) -> str | None:
    """失败即返回可读原因，否则 ``None``——这是 ``poll_with_retry`` 的 ``is_failed`` 契约。

    不能直接传 ``_is_failure``：它返回 ``bool``，而谓词的返回值会被原样塞进
    ``RuntimeError``，结果用户看到的是一个「True」，平台给的中文原因全丢了。
    """
    if not _is_failure(state):
        return None
    reason = _failure_reason(state)
    phase = _phase(state) or state.get("status")
    return f"Coolhs 任务失败（{phase or 'unknown'}）：{reason or '平台未给出原因'}"


def _video_url_of(state: dict[str, Any]) -> str | None:
    """成片直链。平台返回 ``videoUrl``；**24 小时过期**，取到就得马上下。"""
    for key in ("videoUrl", "video_url", "url"):
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class CoolhsVideoBackend(ProviderJobIdPersistenceMixin):
    """把万万工作台的排队式视频接口跑成一个分镜视频。"""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        provider_id: str = PROVIDER_COOLHS,
        ride_wait_seconds: float | None = None,
    ) -> None:
        self._provider = provider_id
        self._model = model
        self._api_key = api_key
        self._base_url = (base_url or DEFAULT_COOLHS_BASE_URL).rstrip("/")
        #: 关单时最多等多久开班；0 表示不等（撞上关单立刻失败）。等待期间零计费。
        self._ride_wait_seconds = (
            _RIDE_WAIT_MAX_SECONDS if ride_wait_seconds is None else max(0.0, float(ride_wait_seconds))
        )
        self._ride_wait_interval_seconds = _RIDE_WAIT_INTERVAL_SECONDS
        self._ride_wait_jitter = _RIDE_WAIT_JITTER

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按名字族认定能力；认不得的模型回落统一回落值。

        平台对外只有 ``wan`` 与三个分辨率变体，catalog 条目名是 ``coolhs-wan3``——三者都含
        ``wan``，故用名字族匹配而不是写死型号清单。认不得时回落「只支持文生视频与首帧」，
        与其它通道同一口径：宁可少宣称，也不把一个未知模型谎报成支持参考图。
        """
        return capabilities_by_names(
            model,
            _match_capabilities,
            fallback=UNRECOGNIZED_MODEL_CAPABILITIES,
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """等班次 → 上传素材 → 提交任务 → 长时间排队轮询。"""
        async with artifact_http_client(timeout=_POLL_HTTP_TIMEOUT_SECONDS) as http:
            # 等班次排在上传之前：平台关单时连素材上传也省掉——上传同样占那 600 次/15 分钟的
            # 查询额度，而这批提交注定会被 503 拒。等待期零计费、由 worker 主循环续 lease。
            await self._await_ride_open(http)
            media = await self._upload_media(http, request)
            job_id = await self._submit(http, request, media)
            logger.info("Coolhs 视频任务已提交: job_id=%s model=%s 素材=%d", job_id, self._model, len(media))
            # 排队可能数小时：job_id 必须落库，否则重启后这笔已扣费的任务无从接续。
            await self._persist_provider_job_id(
                request, job_id, provider=self._provider, endpoint=self._base_url
            )
            return await self._poll_and_build(http, job_id, request, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续一笔已提交的任务：只轮询与取片，绝不重新提交（重提会二次扣费）。

        域名取提交那一次记录的 ``submitted_base_url``——用户可能在提交后改过供应商地址，
        而这一单活在原来那台机器上；照新域名去问只会得到"任务不存在"，把还在排队的单子
        判成丢失。
        """
        submitted = request.submitted_base_url
        if submitted and submitted.rstrip("/") != self._base_url:
            return await CoolhsVideoBackend(
                model=self._model,
                api_key=self._api_key,
                base_url=submitted,
                provider_id=self._provider,
            ).resume_video(job_id, request)
        async with artifact_http_client(timeout=_POLL_HTTP_TIMEOUT_SECONDS) as http:
            return await self._poll_and_build(http, job_id, request, is_resume=True)

    # ── 班次等待 ──────────────────────────────────────────────────────────

    async def _fetch_ride_state(self, http: httpx.AsyncClient) -> dict[str, Any] | None:
        """读一次班次快照；**任何自身故障都返回 ``None``**（等价于"看不出关单"）。

        等待与单次判定都建立在这上面，所以必须 fail-open：状态接口抖一下、回个 5xx、给段
        非 JSON，都不该把生成停掉——真正接单与否由提交响应裁决。
        """
        try:
            response = await http.get(
                f"{self._base_url}{_RIDE_ENDPOINT}",
                headers=coolhs_headers(self._api_key),
                timeout=_RIDE_PRECHECK_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            logger.warning("Coolhs 班次查询失败，按可接单继续: %s", exc)
            return None
        if response.status_code >= 400:
            logger.warning("Coolhs 班次查询返回 HTTP %s，按可接单继续", response.status_code)
            return None
        try:
            payload = response.json()
        except ValueError:
            logger.warning("Coolhs 班次查询响应不是 JSON，按可接单继续")
            return None
        state = _state_of(payload)
        if not state and isinstance(payload, dict):
            ride = payload.get("ride")
            if isinstance(ride, dict):
                state = ride
        return state

    async def _ensure_ride_open(self, http: httpx.AsyncClient) -> None:
        """单次判定：平台整体关单就立刻抛错。"""
        state = await self._fetch_ride_state(http)
        if state is None:
            return
        refusal = _ride_refusal(state)
        if refusal is not None:
            logger.info("Coolhs 班次未开，跳过本次提交: %s", state)
            raise CoolhsRideClosedError(refusal)

    async def _await_ride_open(self, http: httpx.AsyncClient) -> None:
        """关单就按间隔等到开班，超上限才放弃。

        等待发生在**上传与提交之前**，因此整个过程不产生计费，放弃时也一分钱没花。
        期间 worker lease 由主循环续约，长时间 await 不会被判死；任务若要取消，
        ``asyncio.sleep`` 会直接收到取消，不会拖着。
        """
        deadline = time.monotonic() + self._ride_wait_seconds
        waited = 0.0
        while True:
            state = await self._fetch_ride_state(http)
            refusal = None if state is None else _ride_refusal(state)
            if refusal is None:
                if waited:
                    logger.info("Coolhs 班次已开班，等待 %s 后继续提交", _human_duration(waited))
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CoolhsRideClosedError(
                    f"平台连续 {_human_duration(waited)}未开班（{_ride_brief(state or {})}），"
                    "本次放弃等待、未产生扣费。"
                )
            sleep_for = min(self._ride_wait_interval_seconds, remaining)
            if self._ride_wait_jitter:
                sleep_for *= 1 + random.uniform(-self._ride_wait_jitter, self._ride_wait_jitter)
                sleep_for = min(max(sleep_for, 0.0), remaining)
            logger.info(
                "Coolhs 班次未开（%s），已等 %s，%s后重试，上限 %s",
                _ride_brief(state or {}),
                _human_duration(waited),
                _human_duration(sleep_for),
                _human_duration(self._ride_wait_seconds),
            )
            await asyncio.sleep(sleep_for)
            waited += sleep_for

    # ── 素材上传 ──────────────────────────────────────────────────────────

    async def _upload_media(self, http: httpx.AsyncClient, request: VideoGenerationRequest) -> list[dict[str, Any]]:
        """把请求里的素材逐个上传，返回可直接放进 ``media[]`` 的条目。

        编号规则与提示词方言严格对齐：**参考图按 ``reference_images`` 顺序取 图1…图N**，
        首帧/尾帧不占编号（正文不引用它们）。平台限制首帧与参考素材不可同现，调用方
        （generation_mode 决定走哪条路径）已经保证这一点；这里若两者同时出现，以首尾帧
        优先并记一条 warning，避免提交一个必然被拒的请求。
        """
        media: list[dict[str, Any]] = []
        has_frames = request.start_image is not None or request.end_image is not None
        if has_frames and request.reference_images:
            logger.warning("Coolhs 收到首尾帧与参考图同现：平台不允许混用，按首尾帧提交，忽略参考图")
        if request.start_image is not None:
            media.append(await self._upload_one(http, request.start_image, kind="image", role="first", token=None))
        if request.end_image is not None:
            media.append(await self._upload_one(http, request.end_image, kind="image", role="last", token=None))
        if not has_frames:
            for index, path in enumerate(request.reference_images or [], start=1):
                media.append(
                    await self._upload_one(http, path, kind="image", role="ref", token=f"图{index}")
                )
        for path in request.reference_audio_files or []:
            media.append(await self._upload_one(http, path, kind="audio", role="ref", token=None))
        return media

    async def _upload_one(
        self, http: httpx.AsyncClient, path: Path, *, kind: str, role: str, token: str | None
    ) -> dict[str, Any]:
        """单个素材上传：预签名 → 直传对象存储 → 登记，三步换一个附件 id。

        平台原先的 ``POST /api/portal/media`` 直传接口已下线（返回 410），现在是
        「先签名、浏览器直连对象存储、再回登记」的两段式：服务器不再中转文件本体，
        只为一次 PUT 签一个短时有效的地址。附件 id 由最后一步 ``commit`` 返回，
        这个 id 才是 ``media[]`` 认的东西。
        """
        content = await asyncio.to_thread(path.read_bytes)
        mime = coolhs_mime(kind, path)
        presign = await self._presign(http, name=path.name, size=len(content), kind=kind, mime=mime)
        await self._put_object(http, presign, content=content, mime=mime)
        attachment_id = await self._commit(
            http, presign, name=path.name, kind=kind, token=token, mime=mime
        )
        entry: dict[str, Any] = {
            "id": attachment_id,
            "kind": kind,
            "name": path.name,
            "role": role,
        }
        if token:
            entry["token"] = token
        return entry

    async def _presign(
        self, http: httpx.AsyncClient, *, name: str, size: int, kind: str, mime: str
    ) -> dict[str, Any]:
        """换一张预签名直传地址；拿不到 ``url`` / ``key`` 就没有下文。"""
        response = await http.post(
            f"{self._base_url}{_MEDIA_PRESIGN_ENDPOINT}",
            json={"name": name, "size": size, "kind": kind, "mime": mime},
            headers=coolhs_headers(self._api_key),
            timeout=_UPLOAD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Coolhs 素材预签名返回了非对象: {str(payload)[:200]}")
        url = payload.get("url")
        key = payload.get("key")
        if not isinstance(url, str) or not url.strip() or not isinstance(key, str) or not key.strip():
            raise ValueError(f"Coolhs 素材预签名未返回 url/key: {str(payload)[:200]}")
        return payload

    async def _put_object(
        self, http: httpx.AsyncClient, presign: dict[str, Any], *, content: bytes, mime: str
    ) -> None:
        """把文件体 PUT 到预签名地址。

        **不带卡密**：那是对象存储的签名地址，卡密只属于平台域名；泄露给第三方存储域
        没有意义，还多一处暴露面。Content-Type 用平台回执里的 ``contentType``（它按自己
        的规则裁定），缺失时才退回我们推断的 MIME。
        """
        response = await http.put(
            presign["url"],
            content=content,
            headers={
                "User-Agent": COOLHS_USER_AGENT,
                "Content-Type": presign.get("contentType") or mime,
            },
            timeout=_UPLOAD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

    async def _commit(
        self, http: httpx.AsyncClient, presign: dict[str, Any], *, name: str, kind: str, token: str | None, mime: str
    ) -> str:
        """登记已直传的素材，返回附件 id（``media[]`` 唯一认的引用）。"""
        body: dict[str, Any] = {"key": presign["key"], "name": name, "kind": kind, "mime": mime}
        if token:
            body["token"] = token
        response = await http.post(
            f"{self._base_url}{_MEDIA_COMMIT_ENDPOINT}",
            json=body,
            headers=coolhs_headers(self._api_key),
            timeout=_UPLOAD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        attachment_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(attachment_id, str) or not attachment_id:
            raise ValueError(f"Coolhs 素材登记未返回 id: {name}")
        return attachment_id

    # ── 提交 ──────────────────────────────────────────────────────────────

    async def _submit(
        self, http: httpx.AsyncClient, request: VideoGenerationRequest, media: list[dict[str, Any]]
    ) -> str:
        """提交任务；返回 job id。``submit_post`` 已把歧义传输错误转成终态失败，避免重复扣费。"""
        body: dict[str, Any] = {
            "id": _job_id_for(request),
            "prompt": request.prompt,
            "ratio": request.aspect_ratio or "adaptive",
            "resolution": _resolution_label(request.resolution),
            "duration": int(request.duration_seconds),
            "audio": bool(request.generate_audio),
            # 固定关掉平台侧扩写：默认 -1 会自动改写提示词，参考图约束被稀释后人物就不像了。
            "promptExtend": 0,
        }
        if media:
            body["media"] = media
        response = await submit_post(
            lambda: http.post(
                f"{self._base_url}{_JOBS_ENDPOINT}",
                json=body,
                headers=coolhs_headers(self._api_key),
                timeout=_SUBMIT_TIMEOUT_SECONDS,
            ),
            provider=self._provider,
            request=request,
        )
        payload = response.json()
        state = _state_of(payload)
        job_id = state.get("id") or (payload.get("id") if isinstance(payload, dict) else None)
        if not isinstance(job_id, str) or not job_id:
            raise ValueError(f"Coolhs 提交未返回任务 id: {payload!r:.200}")
        return job_id

    # ── 轮询与取片 ────────────────────────────────────────────────────────

    async def _poll_once(self, http: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
        response = await http.get(
            f"{self._base_url}{_JOBS_ENDPOINT}/{job_id}",
            headers=coolhs_headers(self._api_key),
            timeout=_POLL_HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()

    async def _poll_and_build(
        self,
        http: httpx.AsyncClient,
        job_id: str,
        request: VideoGenerationRequest,
        *,
        is_resume: bool,
    ) -> VideoGenerationResult:
        async def poll_fn() -> dict[str, Any]:
            return _state_of(await self._poll_once(http, job_id))

        gated_poll = resume_expiry_gate(
            recording_poll(poll_fn, request),
            resume_job_id=job_id if is_resume else None,
            provider=self._provider,
        )

        final = await poll_with_retry(
            poll_fn=gated_poll,
            is_done=_is_terminal,
            is_failed=_failure_message,
            max_wait=request.poll_timeout_seconds,
            poll_interval=_POLL_INTERVAL_SECONDS,
            retry_if=should_retry_poll,
            label="Coolhs",
            on_progress=lambda state, elapsed: logger.info(
                "Coolhs 出片排队中... phase=%s status=%s elapsed=%ds",
                _phase(state),
                state.get("status"),
                int(elapsed),
            ),
        )

        # 走到这里说明 ``poll_with_retry`` 已确认终态且成功：失败分支由 ``_failure_message``
        # 在轮询内部直接抛出，带平台原文，不必也不能在这里重判。
        await self._download(http, job_id, final, request.output_path)
        logger.info("Coolhs 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=self._provider,
            model=self._model,
            duration_seconds=int(final.get("duration") or request.duration_seconds),
            video_uri=_video_url_of(final),
            task_id=job_id,
            seed=request.seed,
            generate_audio=bool(request.generate_audio),
        )

    async def _download(
        self, http: httpx.AsyncClient, job_id: str, state: dict[str, Any], output_path: Path
    ) -> None:
        """取片。直链优先（可走共用下载预算），直链缺失或已过期时退回按 id 的下载接口。"""
        video_url = _video_url_of(state)
        if video_url:
            try:
                await download_resumable_video(video_url, output_path, label="Coolhs")
                return
            except Exception:  # noqa: BLE001 - 直链过期是常态，退回去带认证的下载接口
                logger.warning("Coolhs 直链下载失败，改走任务下载接口: job_id=%s", job_id)
        await self._download_by_job(http, job_id, output_path)

    async def _download_by_job(self, http: httpx.AsyncClient, job_id: str, output_path: Path) -> None:
        """按 job id 取片（带卡密）。成片直链 24 小时过期，续跑时只剩这条路。

        平台会把成片 302 到对象存储（此处为阿里云 OSS）的签名直链。卡密只能发给平台源，
        绝不能跟着 302 跳到 OSS——所以第一跳带卡密、显式 ``follow_redirects=False``；
        拿到 ``Location`` 后改用不带凭据的统一产物下载器（它会跟随重定向，并把失败编码成
        可重试的 ``artifact_download_failed``，从而让「重试下载」入口可用）。
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with http.stream(
            "GET",
            f"{self._base_url}{_JOBS_ENDPOINT}/{job_id}/video",
            headers=coolhs_headers(self._api_key),
            timeout=_UPLOAD_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as response:
            if response.status_code == 404:
                raise ResumeExpiredError(job_id=job_id, provider=self._provider)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    response.raise_for_status()
                # OSS 签名直链：不带卡密下载，跨源不泄凭证。
                await download_resumable_video(
                    str(response.url.join(location)), output_path, label="Coolhs"
                )
                return
            response.raise_for_status()
            with output_path.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)
