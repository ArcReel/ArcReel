"""MiniMaxVideoBackend — MiniMax（海螺）视频生成后端，两代 API 并存。

v1（海螺系列）两步取 URL：submit POST /v1/video_generation 取 task_id →
轮询 GET /v1/query/video_generation?task_id= 至 status=Success 取 file_id →
GET /v1/files/retrieve?file_id= 取 download_url → 下载本地。覆盖 MiniMax-Hailuo-2.3
（t2v+i2v）、MiniMax-Hailuo-2.3-Fast（仅 i2v，约半价）与 S2V-01（单脸参考生视频 R2V）。

v2（MiniMax-H3）单步取 URL：submit POST /v2/video_generation 取 task_id →
轮询 GET /v2/query/video_generation/{task_id} 至 task.status=succeeded 直接取
task.content.url。请求体是多模态 content[] 数组，条目按 role 区分首帧/尾帧/参考图/参考音频，
一次请求覆盖 t2v、i2v（首尾帧）与 r2v（参考图 + 参考音频）三条路径。

能力约束：Hailuo resolution ∈ {768P, 1080P}，1080P 仅 6s（10s 仅 768P）；越界抛
VideoCapabilityError，Fast 仅图生视频、无首帧的文生视频请求被能力拒绝。S2V-01 走单脸
subject_reference（reference_images[0]→{"type":"character","image":[...]}），固定输出、
不传 resolution/duration，无参考图即 fail-loud。H3 resolution ∈ {768P, 2K}、时长 4–15 秒任意整数，
首尾帧与参考素材互斥（官方口径「图生视频与多模态参考生视频互斥」）。
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from lib.config.registry import model_info_for
from lib.logging_utils import format_kwargs_for_log
from lib.minimax_shared import (
    MINIMAX_VIDEO_POLL_INTERVAL_SECONDS,
    extract_minimax_download_url,
    extract_minimax_file_id,
    extract_minimax_v2_download_url,
    extract_minimax_video_task_id,
    image_to_data_uri,
    is_minimax_v2_video_terminal,
    is_minimax_video_terminal,
    minimax_headers,
    minimax_v2_video_failure_reason,
    minimax_v2_video_status,
    minimax_video_base_url,
    minimax_video_failure_reason,
    minimax_video_v2_base_url,
    resolve_minimax_api_key,
)
from lib.providers import PROVIDER_MINIMAX
from lib.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DOWNLOAD_BACKOFF_SECONDS,
    DOWNLOAD_MAX_ATTEMPTS,
    with_retry_async,
)
from lib.video_backends.base import (
    ProviderJobIdPersistenceMixin,
    ReferenceAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    poll_with_retry,
    should_retry_download,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)

logger = logging.getLogger(__name__)

_HAILUO = "MiniMax-Hailuo-2.3"
_HAILUO_FAST = "MiniMax-Hailuo-2.3-Fast"
# S2V-01：单张人脸驱动的角色一致性参考生视频（R2V），走 subject_reference 单脸字段，
# 不接受 first_frame_image / resolution / duration（固定输出）。
_S2V = "S2V-01"
# MiniMax-H3：多模态 content[] 端点（v2），唯一走 v2 分支的型号。
_H3 = "MiniMax-H3"

DEFAULT_MODEL = _H3

_SUBMIT_ENDPOINT = "/video_generation"
_QUERY_ENDPOINT = "/query/video_generation"
_RETRIEVE_ENDPOINT = "/files/retrieve"

_MIN_POLL_TIMEOUT_SECONDS = 900.0
_POLL_TIMEOUT_PER_SECOND = 60.0

# 无首帧的文生视频不是各档通用：2.3-Fast 仅图生视频；S2V-01 由 subject_reference 驱动
# （参考图路径经 VideoCapabilities.max_reference_images 表达），两者都不接受纯文本请求。
# 未登记 model（代理中转自定义命名）按支持处理，与其余能力维度的「未知即通用默认」一致。
_NO_TEXT_TO_VIDEO_MODELS: frozenset[str] = frozenset({_HAILUO_FAST, _S2V})

# (分辨率小写 → 允许的时长集合)：1080P 仅 6s，768P 支持 6s/10s（两代 Hailuo 同此矩阵）。
# 仅 v1 分支适用——H3 无跨维约束，两档分辨率共用同一段连续时长，直接读 registry 声明。
_RESOLUTION_DURATIONS: dict[str, set[int]] = {"768p": {6, 10}, "1080p": {6}}

# H3 输入上限，出处为官方《创建视频生成任务 (V2)》
# https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create.md
# 输出规格（分辨率档、时长）不在此处声明：registry 的 resolutions / supported_durations 是其
# 真相源，下方兜底常量（同一出处：768P/2K、4–15 秒任意整数）仅在型号未登记时生效。
_H3_FALLBACK_RESOLUTIONS: frozenset[str] = frozenset({"768p", "2k"})
_H3_FALLBACK_DURATIONS: frozenset[int] = frozenset(range(4, 16))
_H3_MAX_REFERENCE_IMAGES = 9
_H3_MAX_REFERENCE_AUDIO = 3
_H3_MAX_REFERENCE_AUDIO_TOTAL_SECONDS = 15.0
_H3_MAX_PROMPT_CHARS = 7000

# 参考音频的 data URI MIME：官方接受 wav / mp3，要求 `data:audio/<格式>;base64,<内容>` 且格式小写。
# 与 ark 侧的同名表各存一份——各家对 mp3 的接受口径不同（此处按官方写法 audio/mp3），
# 合并成共享表会让其中一家收到没验证过的 MIME。
_REFERENCE_AUDIO_MIME_TYPES: dict[str, str] = {".wav": "audio/wav", ".mp3": "audio/mp3"}

# 进日志的安全标量白名单；first_frame_image / subject_reference / content[]（base64）一律不入日志。
_SAFE_LOG_KEYS: frozenset[str] = frozenset({"model", "resolution", "duration", "ratio"})


def _supports_text_to_video(model: str | None) -> bool:
    return (model or "").strip() not in _NO_TEXT_TO_VIDEO_MODELS


def _is_h3_model(model: str | None) -> bool:
    """H3 判定：大小写不敏感、容忍命名空间前缀（如中转站可能把型号存成 "proxy/minimax-h3"）。

    与 ``lib.custom_provider.endpoints.infer_endpoint`` 发现 H3 原生 token 时用的
    ``"minimax-h3" in lowered`` 同一判定口径（不能互相 import，两处各存一份字面量，改
    其一须同改另一处）。发现与派发若用不同谓词，会出现"发现路由到 minimax-video，
    派发却因大小写/命名空间不匹配落回 v1"的裂缝。
    """
    return "minimax-h3" in (model or "").lower()


def _safe_body_for_log(body: dict) -> dict:
    """安全日志视图：白名单标量 + prompt 截断；素材字段一律折叠，不展开 base64。

    v2 的 content[] 里图片与音频都是 base64 data URI，按条目类型折叠成计数后再入日志
    （对齐 CodeQL clear-text-logging 约束），其中的 text 条目即 prompt，按同一规则截断。
    """
    safe = {k: v for k, v in body.items() if k in _SAFE_LOG_KEYS}
    prompt = body.get("prompt")
    content = body.get("content")
    if isinstance(content, list):
        counts: dict[str, int] = {}
        for item in content:
            kind = item.get("type", "unknown") if isinstance(item, dict) else "unknown"
            counts[kind] = counts.get(kind, 0) + 1
        safe["content"] = "<" + ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items())) + ">"
        prompt = next(
            (item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"), None
        )
    if prompt is not None or "prompt" in body:
        text = prompt or ""
        safe["prompt"] = text[:120] + ("…" if len(text) > 120 else "")
    if body.get("first_frame_image"):
        safe["first_frame_image"] = "<data_uri>"
    if body.get("subject_reference"):
        safe["subject_reference"] = "<character_ref>"
    return safe


def _image_content_item(data_uri: str, *, role: str) -> dict[str, Any]:
    """图片 data URI → v2 content[] 条目。"""
    return {"type": "image_url", "image_url": {"url": data_uri}, "role": role}


def _reference_audio_to_data_uri(path: Path, *, model: str) -> str:
    """参考音频 → base64 data URI；格式不受支持或文件不可读一律抛错。

    与参考图的处理同为 fail-loud：prompt 里的「音频N」按 content 数组中音频条目的出现顺序
    编号，跳过一段会让其后所有编号整体前移，把某个角色的音色安到另一个角色头上——错得无声
    无息，且照常扣费。
    """
    mime = _REFERENCE_AUDIO_MIME_TYPES.get(path.suffix.lower())
    if mime is None:
        raise VideoCapabilityError(
            "video_reference_audio_format_unsupported",
            model=model,
            name=path.name,
            supported=", ".join(sorted(_REFERENCE_AUDIO_MIME_TYPES)),
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise VideoCapabilityError("video_reference_audio_unreadable", model=model, names=path.name) from exc
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


class MiniMaxVideoBackend(ProviderJobIdPersistenceMixin):
    """MiniMax 视频后端（异步轮询）；走 v1 还是 v2 在构造期按 model 定下，各步据此派发。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        self._api_key = resolve_minimax_api_key(api_key)
        self._model = model or DEFAULT_MODEL
        # v2 是整条链路的分支（base、提交体、轮询响应形状、取件方式全不同），不是单点差异，
        # 故在构造期定好走哪一代，后续各步按此派发。
        self._is_v2 = _is_h3_model(self._model)
        self._base_url = minimax_video_v2_base_url(base_url) if self._is_v2 else minimax_video_base_url(base_url)
        self._http_timeout = http_timeout
        self._supports_text_to_video = _supports_text_to_video(self._model)

    @property
    def name(self) -> str:
        return PROVIDER_MINIMAX

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """海螺图生视频走 first_frame_image 首帧；S2V-01 走 subject_reference 单脸参考生视频。

        S2V-01 仅接受单张人脸参考、不接受首帧图，故 first_frame=False + max_reference_images=1。
        Hailuo 系列首批不建模尾帧/参考图。

        H3 的 content[] 数组按 role 同时承载首帧、尾帧、参考图与参考音频，各维度上限取官方
        《创建视频生成任务 (V2)》声明值。首帧任务只接受 ratio=adaptive（官方 ratio 枚举含
        adaptive，图生视频示例即用它），故声明 first_frame_ratio_adaptive_only。
        """
        if model == _S2V:
            return VideoCapabilities(first_frame=False, max_reference_images=1)
        if _is_h3_model(model):
            return VideoCapabilities(
                first_frame=True,
                last_frame=True,
                max_reference_images=_H3_MAX_REFERENCE_IMAGES,
                reference_audio_mode=ReferenceAudioMode.DIRECT,
                # 段数与总时长两个维度独立声明：3 段各 10 秒都在单段合法区间内，合计已超 15 秒。
                max_reference_audio_count=_H3_MAX_REFERENCE_AUDIO,
                max_reference_audio_total_seconds=_H3_MAX_REFERENCE_AUDIO_TOTAL_SECONDS,
                max_prompt_chars=_H3_MAX_PROMPT_CHARS,
                first_frame_ratio_adaptive_only=True,
            )
        return VideoCapabilities(first_frame=True)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        payload = self._build_payload(request)
        logger.info(
            "调用 %s 视频 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(_safe_body_for_log(payload)),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            task_id = await self._create_task(client, payload)
            logger.info("MiniMax 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            await self._persist_provider_job_id(request, task_id, provider=PROVIDER_MINIMAX)
            return await self._poll_and_build(client, task_id, request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 MiniMax task：仅轮询 + 取回 + 下载，不重新提交（ADR 0007）。"""
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            return await self._poll_and_build(client, job_id, request)

    # ── request building ────────────────────────────────────────────────

    def _build_payload(self, request: VideoGenerationRequest) -> dict:
        # H3 走 v2 多模态 content[] 端点；S2V-01 走单脸 subject_reference 路径：
        # 不取首帧、不传 resolution/duration（固定输出）。
        if self._is_v2:
            return self._build_v2_payload(request)
        if self._model == _S2V:
            return self._build_s2v_payload(request)

        resolution = (request.resolution or "768p").lower()
        duration = request.duration_seconds
        has_start_image = isinstance(request.start_image, (str, Path)) and str(request.start_image)

        # 无首帧 = 文生视频意图；模型不支持 t2v（如 Fast）即拒绝。
        if not has_start_image and not self._supports_text_to_video:
            raise VideoCapabilityError("video_capability_missing_t2v", provider=self.name, model=self._model)

        allowed_durations = _RESOLUTION_DURATIONS.get(resolution, set())
        if duration not in allowed_durations:
            # 空集合（分辨率未知）用语言中性占位符：这个值会原样进 en/vi 文案，
            # 中文兜底会在非中文界面里露出中文。
            supported = ", ".join(f"{d}s" for d in sorted(allowed_durations)) or "-"
            raise VideoCapabilityError(
                "video_resolution_duration_unsupported",
                model=self._model,
                resolution=resolution.upper(),
                duration=duration,
                supported=supported,
            )

        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "duration": duration,
            "resolution": resolution.upper(),
        }
        if has_start_image:
            p = Path(request.start_image)  # type: ignore[arg-type]
            # fail-loud：声明了首帧图却缺失/不可读即中止，不静默退化为文生视频。
            if not p.is_file():
                raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=p.name)
            try:
                payload["first_frame_image"] = image_to_data_uri(p)
            except OSError as exc:
                raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=p.name) from exc
        return payload

    def _build_s2v_payload(self, request: VideoGenerationRequest) -> dict:
        """S2V-01：把 reference_images[0] 映射成单脸 subject_reference。

        编排层已按本 backend 声明的 max_reference_images=1 裁剪，此处防御性仅取首张人脸图。
        fail-loud：未提供参考图 → required；声明的参考图缺失/不可读 → unreadable，
        不静默退化为无参考生成（会产出错误结果且照常计费）。
        """
        provided = [r for r in (request.reference_images or []) if r]
        if not provided:
            raise VideoCapabilityError("video_reference_images_required", model=self._model)
        face = Path(provided[0])
        if not face.is_file():
            raise VideoCapabilityError("video_reference_images_unreadable", model=self._model, names=face.name)
        try:
            data_uri = image_to_data_uri(face)
        except OSError as exc:
            raise VideoCapabilityError("video_reference_images_unreadable", model=self._model, names=face.name) from exc
        return {
            "model": self._model,
            "prompt": request.prompt,
            "subject_reference": [{"type": "character", "image": [data_uri]}],
        }

    def _build_v2_payload(self, request: VideoGenerationRequest) -> dict:
        """H3：把首帧/尾帧/参考图/参考音频按 role 摊进多模态 content[] 数组。

        prompt 恒为首条 text 条目（官方要求所有场景都带一个非空 text）；素材条目顺序即
        prompt 中「音频N」等指认编号的依据，故任何一段素材缺失都 fail-loud，不静默跳过。
        """
        resolution = (request.resolution or "768p").lower()
        duration = request.duration_seconds
        resolutions, durations = self._v2_output_specs()
        if resolution not in resolutions or duration not in durations:
            # v2 的分辨率与时长是两个独立集合（不像 v1 按分辨率给时长），越界可能来自任一维度；
            # supported 一并列出两维，避免「该分辨率下不支持 Ns」读成分辨率本身合法。
            supported_resolutions = ", ".join(sorted(r.upper() for r in resolutions))
            supported_durations = ", ".join(f"{d}s" for d in sorted(durations))
            raise VideoCapabilityError(
                "video_resolution_duration_unsupported",
                model=self._model,
                resolution=resolution.upper(),
                duration=duration,
                supported=f"{supported_resolutions} × {supported_durations}",
            )

        start_image = self._existing_path(request.start_image)
        end_image = self._existing_path(request.end_image)
        references = [Path(r) for r in (request.reference_images or []) if r]
        # 官方口径：图生视频与多模态参考生视频互斥，参考音频同属参考生视频维度。混合请求会
        # 被上游 400 拒绝，在此提前拦下，避免把一次注定失败的请求发出去。
        if (references or request.reference_audio_files) and (start_image is not None or end_image is not None):
            raise VideoCapabilityError("video_reference_images_with_frames_unsupported", model=self._model)
        if end_image is not None and start_image is None:
            raise VideoCapabilityError("video_end_image_requires_start_image", model=self._model)

        content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
        if start_image is not None:
            uri = self._image_data_uri(start_image)
            if uri is None:
                raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=start_image.name)
            content.append(_image_content_item(uri, role="first_frame"))
        if end_image is not None:
            uri = self._image_data_uri(end_image)
            if uri is None:
                raise VideoCapabilityError("video_end_image_unreadable", model=self._model, name=end_image.name)
            content.append(_image_content_item(uri, role="last_frame"))
        for reference in references:
            uri = self._image_data_uri(reference)
            if uri is None:
                raise VideoCapabilityError("video_reference_images_unreadable", model=self._model, names=reference.name)
            content.append(_image_content_item(uri, role="reference_image"))
        for audio_path in request.reference_audio_files or []:
            content.append(
                {
                    "type": "audio_url",
                    "audio_url": {"url": _reference_audio_to_data_uri(Path(audio_path), model=self._model)},
                    "role": "reference_audio",
                }
            )

        return {
            "model": self._model,
            "content": content,
            "resolution": resolution.upper(),
            "duration": duration,
            "ratio": request.aspect_ratio,
        }

    def _v2_output_specs(self) -> tuple[frozenset[str], frozenset[int]]:
        """本模型允许的（分辨率档，时长集合），取自 registry 声明。

        registry 是这两项的真相源，前端下拉门控读的也是它——两处若各写一份，改档位时必然漂移。
        本方法只在 `_is_v2`（即 `_is_h3_model` 判真）时被调用，故一律按 canonical 名 `_H3`
        查 registry——self._model 可能是中转站发现的大小写/命名空间变体，字面值不一定与
        registry key 一致，用它直接查会把已注册的 H3 误判成未登记而回落兜底常量，让变体悄悄
        脱离 registry 声明的时长/分辨率范围（与 `_is_h3_model` 判定和 `video_capabilities_for_model`
        走同一枚 canonical 名，避免第三处再长出不一致）。回落只在 registry 意外缺失该条目时触发。
        """
        info = model_info_for(PROVIDER_MINIMAX, _H3)
        if info is None:
            return _H3_FALLBACK_RESOLUTIONS, _H3_FALLBACK_DURATIONS
        return frozenset(r.lower() for r in info.resolutions), frozenset(info.supported_durations)

    @staticmethod
    def _existing_path(value: Path | str | None) -> Path | None:
        """把首/尾帧入参归一化为 Path；未声明该槽位返回 None。

        与 `plan_frame_slots` 的槽位口径一致：只有 str/Path 才构成槽位声明，其余类型视为未声明。
        文件存在性不在此判定——声明了却读不到要 fail-loud 报出具体槽位，由各自的 unreadable 码承载。
        """
        if isinstance(value, (str, Path)) and str(value):
            return Path(value)
        return None

    @staticmethod
    def _image_data_uri(path: Path) -> str | None:
        """图片 → data URI；缺失或不可读返回 None，由调用方按所属槽位抛对应的 unreadable 码。

        错误码留在调用方而非集中到本函数：槽位与码一一对应，字面量码才能被
        `tests/test_task_failure_capability.py` 的漂移守卫静态扫到。
        """
        if not path.is_file():
            return None
        try:
            return image_to_data_uri(path)
        except OSError:
            return None

    # ── HTTP submit / poll / retrieve / download ────────────────────────

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _create_task(self, client: httpx.AsyncClient, payload: dict) -> str:
        # 非幂等的「建任务 + 计费」POST：submit_post 把歧义传输错误转 AmbiguousSubmitError
        # 终态失败，避免重试重复建任务 + 重复计费；>=400 抛 HTTPStatusError 交 should_retry_submit
        # 按状态码分流（4xx fail-fast、5xx/429 重试）。
        resp = await submit_post(
            lambda: client.post(
                f"{self._base_url}{_SUBMIT_ENDPOINT}",
                json=payload,
                headers=minimax_headers(self._api_key),
            ),
            provider=PROVIDER_MINIMAX,
        )
        return extract_minimax_video_task_id(resp.json())

    async def _poll_query(self, client: httpx.AsyncClient, task_id: str) -> dict:
        # v2 把 task_id 放路径段，v1 放 query string。task_id 来自上游响应/持久化记录，
        # 非本地生成的受控值，编码后再拼入路径段，避免被改写指向非预期端点。
        url = f"{self._base_url}{_QUERY_ENDPOINT}"
        params = None if self._is_v2 else {"task_id": task_id}
        if self._is_v2:
            url = f"{url}/{quote(task_id, safe='')}"
        resp = await client.get(url, params=params, headers=minimax_headers(self._api_key))
        resp.raise_for_status()
        return resp.json()

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_poll,
    )
    async def _retrieve_download_url(self, client: httpx.AsyncClient, file_id: str) -> str:
        # 取回是幂等 GET（不计费），瞬态错误重试无副作用。
        resp = await client.get(
            f"{self._base_url}{_RETRIEVE_ENDPOINT}",
            params={"file_id": file_id},
            headers=minimax_headers(self._api_key),
        )
        resp.raise_for_status()
        return extract_minimax_download_url(resp.json())

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        request: VideoGenerationRequest,
    ) -> VideoGenerationResult:
        is_v2 = self._is_v2
        final = await poll_with_retry(
            poll_fn=lambda: self._poll_query(client, task_id),
            is_done=is_minimax_v2_video_terminal if is_v2 else is_minimax_video_terminal,
            is_failed=minimax_v2_video_failure_reason if is_v2 else minimax_video_failure_reason,
            poll_interval=MINIMAX_VIDEO_POLL_INTERVAL_SECONDS,
            max_wait=self._max_wait(request.duration_seconds),
            retry_if=should_retry_poll,
            label="MiniMax",
            on_progress=lambda v, elapsed: logger.info(
                "MiniMax 视频生成中... status=%s elapsed=%ds",
                minimax_v2_video_status(v) if is_v2 else v.get("status"),
                int(elapsed),
            ),
        )

        if is_v2:
            # v2 查询响应直接带限时下载地址，没有 file_id → files/retrieve 这一步。
            download_url = extract_minimax_v2_download_url(final)
        else:
            file_id = extract_minimax_file_id(final)
            download_url = await self._retrieve_download_url(client, file_id)
        await self._download_with_retry(download_url, request.output_path)
        logger.info("MiniMax 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_MINIMAX,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=download_url,
            task_id=task_id,
            generate_audio=request.generate_audio,
        )

    @staticmethod
    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=should_retry_download,
    )
    async def _download_with_retry(download_url: str, output_path: Path) -> None:
        await download_video(download_url, output_path)

    @staticmethod
    def _max_wait(duration_seconds: int) -> float:
        return max(_MIN_POLL_TIMEOUT_SECONDS, duration_seconds * _POLL_TIMEOUT_PER_SECOND)
