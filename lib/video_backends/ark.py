"""ArkVideoBackend — 火山方舟 Ark 视频生成后端。"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from lib.ark_shared import create_ark_client
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_ARK
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
from lib.video_backends.base import (
    ProviderJobIdPersistenceMixin,
    ReferenceAudioMode,
    ResumeExpiredError,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    poll_with_retry,
)

logger = logging.getLogger(__name__)

# Seedance 2.0 系列每请求最多 3 段参考音频，且总时长不超过 15 秒（官方《创建视频生成任务
# API》音频信息章节）。
_SEEDANCE_2_MAX_REFERENCE_AUDIO = 3
# 两个参考音频维度都留在 backend 侧而不进 lib/config/registry.py：PROVIDER_REGISTRY 是配置面
# 与计费面的能力真相源（supported_durations 指的是生成时长档位，与参考音频输入无关），其
# ModelInfo 至今没有任何 reference_audio 维度；请求期硬拒绝的判定一律读 VideoCapabilities。
_SEEDANCE_2_MAX_REFERENCE_AUDIO_TOTAL_SECONDS = 15.0

# Seedance 1.5 pro 的参考图上限，唯一声明处（编排层裁剪与请求期校验同读 VideoCapabilities）。
_SEEDANCE_1_5_MAX_REFERENCE_IMAGES = 9

# Seedance 2.5 的多模态素材上限：参考图 30 张，参考音频 10 段、总时长 30 秒（官方《视频生成
# API》素材章节）。三项都比 2.0 宽，故与 2.0 各存一组常量而非共享——共享会在其中一档调整时
# 静默改动另一档的放行范围。
_SEEDANCE_2_5_MAX_REFERENCE_IMAGES = 30
_SEEDANCE_2_5_MAX_REFERENCE_AUDIO = 10
_SEEDANCE_2_5_MAX_REFERENCE_AUDIO_TOTAL_SECONDS = 30.0

# 参考音频的 data URI MIME：官方接受 wav / mp3 两种格式，要求 `data:audio/<格式>;base64,<内容>`
# 且格式小写。资产上传期已按同一组格式收窄，此处按扩展名回填，未知扩展名不猜测直接拒绝。
# 与 dashscope 侧的同名表刻意各存一份：mp3 在这里按官方示例写 audio/mp3，dashscope 走标准
# 的 audio/mpeg——供应商各自的接受口径，合并成一张共享表会让其中一家收到没验证过的 MIME。
_REFERENCE_AUDIO_MIME_TYPES = {".wav": "audio/wav", ".mp3": "audio/mp3"}


def _safe_create_params_for_log(create_params: dict[str, Any]) -> dict[str, Any]:
    """请求参数的安全日志视图：content 数组按条目类型折叠成计数，不展开内嵌素材。

    content 里的图片与音频都是 base64 data URI。``format_kwargs_for_log`` 只截断长字符串、
    不认识这层结构，直接交给它会把每个素材的 base64 前缀写进 info 日志（音频还可能带上
    mp3 文件头的 ID3 元数据）。dashscope / vidu / minimax 三家均已各有同名的安全视图并在
    注释里点明对齐 CodeQL clear-text-logging，Ark 此前是唯一漏网的一家。

    prompt 是用户文本、不是素材，仍按 ``format_kwargs_for_log`` 的既有截断规则输出，
    以保留排查生成效果时最常用的那一项。
    """
    view = {key: value for key, value in create_params.items() if key != "content"}
    content = create_params.get("content")
    if isinstance(content, list):
        counts: dict[str, int] = {}
        for item in content:
            kind = item.get("type", "unknown") if isinstance(item, dict) else "unknown"
            counts[kind] = counts.get(kind, 0) + 1
        view["content"] = "<" + ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items())) + ">"
        prompt = next(
            (item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"), None
        )
        if isinstance(prompt, str):
            view["prompt"] = prompt
    return view


def _reference_audio_to_data_uri(path: Path, *, model: str) -> str:
    """参考音频 → base64 data URI；格式不受支持或文件不可读一律抛错。

    与参考图的「缺失即跳过」不同，音频不能静默跳过：prompt 里的「音频N」按 content 数组中
    音频条目的出现顺序编号，跳过一段会让其后所有编号整体前移，把某个角色的音色安到另一个
    角色头上——错得无声无息，且照常扣费。
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


class ArkVideoBackend(ProviderJobIdPersistenceMixin):
    """Ark (火山方舟) 视频生成后端。"""

    DEFAULT_MODEL = "doubao-seedance-1-5-pro-251215"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self._client = create_ark_client(api_key=api_key, base_url=base_url)
        self._model = model or self.DEFAULT_MODEL
        # service_tier 参数仅 seedance-1.x 等老模型支持；2.0 上游在 r2v 下会 400 拒绝该参数，
        # 必须不下传。2.5 按同代口径一并剔除，该口径系从 2.0 的拒绝行为推断，收窄判定
        # 只影响 2.5 一支。判定见 _is_seedance_2 / _is_seedance_2_5：用
        # `in` 子串兼容多套前缀命名（doubao-/dreamina-），版本号逐个收窄到已登记的世代——收窄
        # 的粒度是世代而非具体型号：同代的未知后缀照样按族群剔除 service_tier，这正是要的，
        # 该参数是整代都不接受。逐型号的严格判定另走 _matches_known_model 白名单（尾帧与参考
        # 音频用），两者不可互换。这是 backend 内部的运输细节，不进 VideoCapabilities——后者只
        # 声明调用方需要据以取舍的输入路径。
        self._supports_service_tier = not (self._is_seedance_2(self._model) or self._is_seedance_2_5(self._model))

    @property
    def name(self) -> str:
        return PROVIDER_ARK

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def _is_seedance_2(model: str) -> bool:
        """按 model_id 子串识别已验证的 seedance-2-0 / seedance-2.0 系列（含 fast 变体）。

        只匹配 2-0 与 2.0 两种写法，2.5 由 _is_seedance_2_5 单独识别——两代的素材上限与
        尾帧口径都不同，合成一个族群判定会让 2.5 继承 2.0 的上限。用 `in` 子串而非前缀匹配，
        以兼容上游多套前缀命名（doubao- 火山国内站 / dreamina- BytePlus 国际站）。
        service_tier 剔除与参考图能力共用本判定，避免两条路径口径漂移。
        """
        model_lower = model.lower()
        return "seedance-2-0" in model_lower or "seedance-2.0" in model_lower

    @staticmethod
    def _is_seedance_2_5(model: str) -> bool:
        """按 model_id 子串识别 seedance-2-5 / seedance-2.5 系列。

        与 _is_seedance_2 同为宽松族群识别（供 service_tier 剔除与参考图能力复用），已验证
        型号的尾帧与参考音频另走 _SEEDANCE_2_5_ALLOW_SUBSTRINGS 边界白名单。
        """
        model_lower = model.lower()
        return "seedance-2-5" in model_lower or "seedance-2.5" in model_lower

    # docs/ark-docs/seedance2.0.md 能力表中，非 seedance-2.0 系列里明确支持首尾帧的仅这三个
    # 1.x 型号（1.0 pro fast 与 1.0 lite t2v 标 "-"，其余未上表的型号未经验证）。改用白名单
    # 而非黑名单：自定义供应商配置或上游新增的未知型号一律保守判定为不支持尾帧，避免错误声明
    # 支持而绕过本模块新增的硬拒绝——一旦放行，真实不支持的型号会照样产生供应商侧调用与扣费，
    # 与本 issue 的验收标准直接相悖。子串同时收录连字符与点号两种版本号写法（如 "1-0" /
    # "1.0"）——上游命名不统一，docs/ark-docs/火山方舟费用参考.md 中 doubao-seedance-1.0-pro-fast
    # 即用点号。
    _NO_FIRST_FRAME_SUBSTRINGS = ("seedance-1-0-lite-t2v", "seedance-1.0-lite-t2v")
    _LAST_FRAME_ALLOW_SUBSTRINGS = (
        "seedance-1-5-pro",
        "seedance-1.5-pro",
        "seedance-1-0-pro",
        "seedance-1.0-pro",
        "seedance-1-0-lite-i2v",
        "seedance-1.0-lite-i2v",
    )
    _NO_LAST_FRAME_SUBSTRINGS = (
        "seedance-1-0-pro-fast",
        "seedance-1.0-pro-fast",
        "seedance-1-0-lite-t2v",
        "seedance-1.0-lite-t2v",
    )
    # 白名单命中后要求剩余部分为空或纯数字日期戳（如 "-251215"）——单纯 `in` 子串匹配会让
    # "doubao-seedance-1-5-pro-future" 这类未上表的未知变体因包含 "seedance-1-5-pro" 而
    # 被误判为继承已验证型号的尾帧能力，绕过本模块新增的硬拒绝。
    _KNOWN_MODEL_SUFFIX_RE = re.compile(r"^(-\d+)?$")

    # 非 2.0 系列里支持参考生视频的型号：1.5 pro 的参考图上限由本模块的 VideoCapabilities
    # 单独声明，编排层裁剪与请求期校验同读该声明。
    _REFERENCE_IMAGE_ALLOW_SUBSTRINGS = ("seedance-1-5-pro", "seedance-1.5-pro")

    # Seedance 2.0 系列已验证支持首尾帧的三个变体（lib/config/registry.py 内建型号：
    # doubao-seedance-2-0-260128 / -2-0-fast-260128 / -2-0-mini-260615，及无日期戳的
    # doubao-seedance-2.0 / -2.0-fast / -2.0-mini）。同样走边界匹配，未知后缀（如
    # "doubao-seedance-2-0-future"）不得因子串包含 "seedance-2-0" 而继承尾帧能力。
    _SEEDANCE_2_LAST_FRAME_ALLOW_SUBSTRINGS = (
        "seedance-2-0-fast",
        "seedance-2.0-fast",
        "seedance-2-0-mini",
        "seedance-2.0-mini",
        "seedance-2-0",
        "seedance-2.0",
    )

    # Seedance 2.5 已验证支持首尾帧与参考音频的型号（内建 doubao-seedance-2-5-260628 及无日期
    # 戳的 doubao-seedance-2.5）。同走边界匹配，未知后缀不得因子串包含而继承这两项能力。
    _SEEDANCE_2_5_ALLOW_SUBSTRINGS = ("seedance-2-5", "seedance-2.5")

    @staticmethod
    def _matches_known_model(model_lower: str, prefixes: tuple[str, ...]) -> bool:
        for prefix in prefixes:
            idx = model_lower.find(prefix)
            if idx == -1:
                continue
            if ArkVideoBackend._KNOWN_MODEL_SUFFIX_RE.match(model_lower[idx + len(prefix) :]):
                return True
        return False

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按 model_id 纯计算参考图等 caps —— 不构造 SDK client（无需 api_key）。

        resolver 解析参考图上限时调本方法即可，不必构造整个 backend；instance property 委托至此，
        保持 backend 为单一真相源。
        """
        if ArkVideoBackend._is_seedance_2_5(model):
            # 2.5 与 2.0 的差别不止数值：素材上限放宽到参考图 30 / 音频 10 段、总时长 30 秒，
            # 且首帧在场时比例必须交给上游按首帧自适应（见 first_frame_ratio_adaptive_only），
            # 下发固定 ratio 会与首帧尺寸冲突。故独立分支，不复用面向 2.0 的判定。
            on_verified_allowlist = ArkVideoBackend._matches_known_model(
                model.lower(), ArkVideoBackend._SEEDANCE_2_5_ALLOW_SUBSTRINGS
            )
            return VideoCapabilities(
                last_frame=on_verified_allowlist,
                max_reference_images=_SEEDANCE_2_5_MAX_REFERENCE_IMAGES,
                reference_audio_mode=(ReferenceAudioMode.DIRECT if on_verified_allowlist else ReferenceAudioMode.NONE),
                max_reference_audio_count=_SEEDANCE_2_5_MAX_REFERENCE_AUDIO if on_verified_allowlist else 0,
                max_reference_audio_total_seconds=(
                    _SEEDANCE_2_5_MAX_REFERENCE_AUDIO_TOTAL_SECONDS if on_verified_allowlist else None
                ),
                first_frame_ratio_adaptive_only=True,
            )
        if ArkVideoBackend._is_seedance_2(model):
            # API 拒绝首帧/尾帧与参考素材混合请求（InvalidParameter: first/last frame content
            # cannot be mixed with reference media content，实测）——参考图是与首尾帧互斥的
            # 参考生视频模式，故不声明首帧叠加参考能力；若上游后续放开混合可重新开启。
            # 尾帧与参考音频都单独走边界校验的已验证型号白名单：_is_seedance_2 本身只做宽松
            # 族群识别（供 service_tier 剔除复用），未验证的 2.0 系列未来变体不应继承这两项
            # 能力。两者当前覆盖同一组已上表型号（2.0 / 2.0-fast / 2.0-mini 三档官方均声明
            # 支持音频参考），故共用同一份白名单常量而非维护两份同内容清单。
            on_verified_allowlist = ArkVideoBackend._matches_known_model(
                model.lower(), ArkVideoBackend._SEEDANCE_2_LAST_FRAME_ALLOW_SUBSTRINGS
            )
            return VideoCapabilities(
                last_frame=on_verified_allowlist,
                max_reference_images=9,
                reference_audio_mode=(ReferenceAudioMode.DIRECT if on_verified_allowlist else ReferenceAudioMode.NONE),
                # 官方限制：每请求最多 3 段参考音频，单段时长 2–15 秒，且总时长不超过 15 秒。
                # 单段上限推不出总时长——两段各 10 秒都合法，合起来已经超；段数与总时长两个
                # 维度独立声明，由 gate_video_request 分别校验。出处：《创建视频生成任务 API》
                # 音频信息章节。
                max_reference_audio_count=_SEEDANCE_2_MAX_REFERENCE_AUDIO if on_verified_allowlist else 0,
                max_reference_audio_total_seconds=(
                    _SEEDANCE_2_MAX_REFERENCE_AUDIO_TOTAL_SECONDS if on_verified_allowlist else None
                ),
            )
        # 非 2.0 系列：DEFAULT_MODEL 1.5 pro 实测正常下发 role="last_frame"（见
        # test_first_last_frame_role_fields），此前统一按 VideoCapabilities() 默认
        # last_frame=False 处理是误判；白名单覆盖能力表已验证支持首尾帧的三个型号，
        # 未命中白名单的一律 last_frame=False（含未来新增/自定义供应商的未知型号）。
        model_lower = model.lower()
        no_first_frame = any(sub in model_lower for sub in ArkVideoBackend._NO_FIRST_FRAME_SUBSTRINGS)
        allowed_last_frame = ArkVideoBackend._matches_known_model(
            model_lower, ArkVideoBackend._LAST_FRAME_ALLOW_SUBSTRINGS
        )
        denied_last_frame = any(sub in model_lower for sub in ArkVideoBackend._NO_LAST_FRAME_SUBSTRINGS)
        # _create_task 对任何 model 都会把 reference_images 序列化成 role="reference_image"，
        # 参考生视频在 1.5 pro 上是既有可用路径；此处不声明容量会让 gate_video_request 把编排层
        # 正常派好参考图的请求整批拒掉——编排层的派图上限同样读这里。未上表的型号仍保守判 0。
        return VideoCapabilities(
            first_frame=not no_first_frame,
            last_frame=allowed_last_frame and not denied_last_frame,
            max_reference_images=(
                _SEEDANCE_1_5_MAX_REFERENCE_IMAGES
                if ArkVideoBackend._matches_known_model(model_lower, ArkVideoBackend._REFERENCE_IMAGE_ALLOW_SUBSTRINGS)
                else 0
            ),
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """生成视频。任务创建和轮询阶段分离重试，避免瞬态错误导致重建任务。"""
        provider_task_id = await self._create_task(request)
        await self._persist_provider_job_id(request, provider_task_id, provider=PROVIDER_ARK)
        return await self._poll_until_done(provider_task_id, request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 Ark task：仅 poll + 下载。

        Ark 端 task 不存在/已过期通常表现为 SDK 抛 404 类异常或返回 status='expired'；
        本接口在 _poll_until_done 内识别后者，前者由本方法拦截转 ResumeExpiredError。
        """
        try:
            return await self._poll_until_done(job_id, request)
        except Exception as exc:
            if _is_ark_not_found(exc):
                raise ResumeExpiredError(job_id=job_id, provider=PROVIDER_ARK) from exc
            raise

    @with_retry_async()
    async def _create_task(self, request: VideoGenerationRequest) -> str:
        """创建 Ark 视频生成任务（带重试保护）。"""
        # 1. Build content list
        content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]

        # Ark 视频 API 要求每个 image_url 条目在顶层带 `role` 字段
        # （first_frame / last_frame / reference_image），否则 400 InvalidParameter。
        if request.start_image:
            from lib.image_backends.base import image_to_base64_data_uri

            data_uri = image_to_base64_data_uri(request.start_image)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                    "role": "first_frame",
                }
            )

        if request.end_image:
            from lib.image_backends.base import image_to_base64_data_uri

            end_path = Path(request.end_image)
            if not end_path.is_file():
                # 尾帧缺失不静默跳过：跳过后任务照常提交并计费，用户拿到一段没有尾帧、
                # 与分镜衔接不上的视频却无从知道原因。
                raise VideoCapabilityError(
                    "video_end_image_unreadable", model=self._model, name=end_path.name or str(end_path)
                )
            data_uri = image_to_base64_data_uri(request.end_image)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                    "role": "last_frame",
                }
            )

        if request.reference_images:
            from lib.image_backends.base import image_to_base64_data_uri

            missing = [p for r in request.reference_images if not (p := Path(r)).is_file()]
            if missing:
                # 与尾帧同理：少一张参考图仍会出片并计费，成片里的角色/场景却已跑偏。
                raise VideoCapabilityError(
                    "video_reference_images_unreadable",
                    model=self._model,
                    names=", ".join(p.name or str(p) for p in missing),
                )
            for ref_path in request.reference_images:
                data_uri = image_to_base64_data_uri(Path(ref_path))
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": data_uri},
                        "role": "reference_image",
                    }
                )

        if request.reference_audio_files:
            # 音频条目与图片条目同列 content 数组，各自按类型独立编号：prompt 里的「音频N」
            # 指的是第 N 个 type="audio_url" 条目（官方《快速入门》提示词规则）。故这里必须
            # 保持 reference_audio_files 的原始顺序、不跳过任何一段——跳过会让编排层拼好的
            # 指认文本整体错位，把 A 角色的音色安到 B 角色头上。
            for audio_path in request.reference_audio_files:
                content.append(
                    {
                        "type": "audio_url",
                        "audio_url": {"url": _reference_audio_to_data_uri(Path(audio_path), model=self._model)},
                        "role": "reference_audio",
                    }
                )

        # 2. Build API params
        # 比例优先：ratio 是独立 SDK 字段，由 aspect_ratio 直接决定；resolution 仅清晰度档位，
        # 与比例正交（SDK 内部按 ratio×resolution 算像素），不把比例压进像素 size，故无尺寸 bug。
        create_params = {
            "model": self._model,
            "content": content,
            "ratio": request.aspect_ratio,
            "duration": request.duration_seconds,
            "generate_audio": request.generate_audio,
            "watermark": False,
        }
        if request.resolution is not None:
            create_params["resolution"] = request.resolution
        # seedance-2.0 等模型不接受 service_tier
        if self._supports_service_tier:
            create_params["service_tier"] = request.service_tier
        if request.seed is not None:
            create_params["seed"] = request.seed

        # 3. Create task (sync SDK call, run in executor)
        logger.info(
            "调用 %s 视频 SDK kwargs=%s", self.name, format_kwargs_for_log(_safe_create_params_for_log(create_params))
        )
        create_result = await asyncio.to_thread(
            self._client.content_generation.tasks.create,
            **create_params,
        )
        logger.info("Ark 任务已创建: %s", create_result.id)
        return create_result.id

    @staticmethod
    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=lambda e: (
            isinstance(e, httpx.HTTPStatusError)
            and e.response.status_code == 400
            and "video_not_ready" in str(e.response.text)
        ),
    )
    async def _download_video_with_retry(video_url: str, output_path) -> None:
        """单独重试视频下载，避免下载失败导致重新生成视频而浪费额度。

        Ark 的视频 URL 在任务 succeeded 后可能仍未就绪（返回 400 video_not_ready），
        仅针对该瞬态状态重试；其余 HTTP 错误及网络瞬态错误由内层 download_video 处理。
        """
        await download_video(video_url, output_path)

    async def _poll_until_done(self, task_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """轮询任务状态直到完成，瞬态错误仅重试当次轮询请求。"""
        # 轮询节奏按**实际提交的**档位定，不按请求里写的档位：_supports_service_tier 为假的
        # 型号根本不下传 service_tier，方舟按默认档建任务，此时若照 request 里残留的 flex 值
        # 走 60s 节奏，已完成的任务要多空等近一分钟才被发现。
        effective_tier = request.service_tier if self._supports_service_tier else "default"
        poll_interval = 10 if effective_tier == "default" else 60
        # 生成耗时大体随输出时长线性增长：固定 600s 上限只够到 10 秒档，30 秒档会在任务仍在
        # 排队时被判超时（视频已在生成且照常计费）。按 60s/输出秒 取下限兜底，与 dashscope 侧
        # 同口径；flex 队列本就按小时级排队，保持 3600 不变。
        max_wait_time = max(600, 60 * request.duration_seconds) if effective_tier == "default" else 3600

        result = await poll_with_retry(
            poll_fn=lambda: asyncio.to_thread(self._client.content_generation.tasks.get, task_id=task_id),
            is_done=lambda r: r.status == "succeeded",
            is_failed=lambda r: (
                f"Ark 视频生成失败(status={r.status}): {getattr(r, 'error', None) or 'Unknown error'}"
                if r.status in ("failed", "expired")
                else None
            ),
            poll_interval=poll_interval,
            max_wait=max_wait_time,
            label="Ark",
            on_progress=lambda r, elapsed: logger.info(
                "Ark 视频生成中... 状态: %s, 已等待 %d 秒", r.status, int(elapsed)
            ),
        )

        # Download video
        video_url = result.content.video_url
        await self._download_video_with_retry(video_url, request.output_path)

        # Extract result metadata
        seed = getattr(result, "seed", None)
        usage_tokens = None
        if hasattr(result, "usage") and result.usage:
            usage_tokens = getattr(result.usage, "completion_tokens", None)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_ARK,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=video_url,
            seed=seed,
            usage_tokens=usage_tokens,
            task_id=task_id,
            generate_audio=request.generate_audio,
        )


def _is_ark_not_found(exc: BaseException) -> bool:
    """识别 Ark 任务「不存在 / 已过期」响应。

    精确匹配官方稳定 ``task_not_found`` 错误码；移除宽泛的 ``"not found"`` 子串兜底，
    避免业务侧错误（如 reference image not found）被误判为 provider 端任务过期。
    ``expired`` 字串保留：Ark 自身 ``_poll_until_done`` 把 status in (failed, expired)
    转成 ``RuntimeError("Ark 任务失败 ... status=expired")``，要靠该字串识别回 ResumeExpiredError。
    """
    status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status_code == 404:
        return True
    msg = str(exc).lower()
    return "task_not_found" in msg or "tasknotfound" in msg or "expired" in msg
