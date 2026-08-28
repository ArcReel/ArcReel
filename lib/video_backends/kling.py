"""KlingVideoBackend — 可灵 Kling 视频生成后端（JWT 直连 / Bearer 中转双模式，异步轮询）。

走可灵原生视频端点：submit ``POST /v1/videos/{text2video|image2video}`` 取 ``data.task_id`` →
轮询 ``GET /v1/videos/{subpath}/{task_id}`` 至 ``task_status=succeed`` 取
``task_result.videos[0].url`` → 下载本地。复用 base.py 的 submit/poll/download helpers，
自包含异步状态机、不依赖 DashScope async 机制。

双模式（对齐 ``GeminiVideoBackend`` 的 ``backend_type`` 先例）：
- ``auth_mode="jwt"``（内置 provider）：接 access_key + secret_key，走 ``KlingJWTManager``，
  每次 HTTP 调用前检查过期、距过期 <60s 按需重签——异步渲染可能超单 token 寿命。
- ``auth_mode="bearer"``（自定义 endpoint）：接静态 api_key + base_url，旁路 JWT 管理器。

各视频模型能力按 ``_KLING_VIDEO_CAPS`` 表驱动（官方一手核实）：
- ``kling-v2-5-turbo``：文/图生视频含首尾帧，无音频/参考（默认 model）。
- ``kling-v3`` / ``kling-v3-omni``：旗舰，首尾帧 + 4K（``mode="4k"``）+ 音画同出；v3-omni 多图主体 R2V。
- ``kling-v2-6``：支持视频内人声，官方限 1080P。
- ``kling-video-o1``：图生 + 多图主体 R2V；只能保留参考视频原声、不生成原生人声。
未登记 model（bearer 透传原生 model_name）回落保守默认能力。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from lib.kling_backend_base import KlingBackendBase
from lib.kling_shared import (
    extract_kling_video_url,
    image_to_base64,
)
from lib.providers import PROVIDER_KLING
from lib.video_backends.base import (
    ProviderJobIdPersistenceMixin,
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
    VideoRoute,
    download_video,
    notify_provider_response,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "kling-v2-5-turbo"

_TEXT2VIDEO = "text2video"
_IMAGE2VIDEO = "image2video"
_MULTI_IMAGE2VIDEO = "multi-image2video"
_RESUMABLE_SUBPATHS = frozenset({_TEXT2VIDEO, _IMAGE2VIDEO, _MULTI_IMAGE2VIDEO})

# 多图主体（R2V）参考图上限，由 backend caps 单独声明（编排层裁剪与生成时防御同读）。
# 取保守值：官方文档未明确列出该上限。
_R2V_MAX_REFERENCE_IMAGES = 4


@dataclass(frozen=True)
class _KlingVideoModelCaps:
    """单个可灵视频模型的能力位（官方一手核实）。"""

    # 文生视频闸：为假的 model 不接受无首帧的请求（_build_payload 处拒）。图生视频没有对应位
    # ——各档首帧恒可用，video_capabilities_for_model 直接声明 first_frame=True。
    text_to_video: bool
    last_frame: bool
    # last_frame=True 但仅 pro 档可用（官方文档：kling-v2-5-turbo、kling-v2-6 首尾帧均标"仅 pro"）；
    # std 档提交 image_tail 请求体虽会
    # 被受理，尾帧约束却不生效——_build_payload 按此位在 std 档拒绝 image_tail，而非放行一个
    # 调用方以为已生效实则被忽略的请求。
    last_frame_requires_pro: bool
    reference_images: bool
    max_reference_images: int
    # 能产出视频内人声（官方能力地图的「音画同出」列）：v2-6 / v3 / v3-omni ✅。该位同时决定请求体
    # 是否携带音频开关 sound——官方各档默认 off，无此能力的 model 不发该字段而非发 "off" 压制。
    # 对外的音轨形态（VideoCapabilities.audio_track）由 `_audio_track_for` 从本位投影，展示层与
    # 入队预检读那一份，不直接读本位。
    generate_audio: bool
    # 有声仅在 1080P 下可用（官方 v2-6 明文「生成有声视频时，仅支持生成 1080P」）。可灵请求体没有
    # 分辨率字段——输出档位只由 mode 决定，故执行期判据落在 mode 上（见 `_effective_audio`）。
    # v3 系官方未声明任何分辨率或档位限制，故为 False。
    audio_requires_1080p: bool


# turbo / 未登记 model（bearer 透传原生 model_name）兜底：文/图生视频、首尾帧，无音频/参考。
_DEFAULT_VIDEO_CAPS = _KlingVideoModelCaps(
    text_to_video=True,
    last_frame=True,
    last_frame_requires_pro=True,
    reference_images=False,
    max_reference_images=0,
    generate_audio=False,
    audio_requires_1080p=False,
)

_KLING_VIDEO_CAPS: dict[str, _KlingVideoModelCaps] = {
    "kling-v2-5-turbo": _DEFAULT_VIDEO_CAPS,
    "kling-v3": _KlingVideoModelCaps(
        text_to_video=True,
        last_frame=True,
        last_frame_requires_pro=False,
        reference_images=False,
        max_reference_images=0,
        generate_audio=True,
        audio_requires_1080p=False,
    ),
    "kling-v3-omni": _KlingVideoModelCaps(
        text_to_video=True,
        last_frame=True,
        last_frame_requires_pro=False,
        reference_images=True,
        max_reference_images=_R2V_MAX_REFERENCE_IMAGES,
        generate_audio=True,
        audio_requires_1080p=False,
    ),
    "kling-v2-6": _KlingVideoModelCaps(
        text_to_video=True,
        last_frame=True,
        last_frame_requires_pro=True,
        reference_images=False,
        max_reference_images=0,
        generate_audio=True,
        audio_requires_1080p=True,
    ),
    "kling-video-o1": _KlingVideoModelCaps(
        text_to_video=False,
        last_frame=True,
        last_frame_requires_pro=False,
        reference_images=True,
        max_reference_images=_R2V_MAX_REFERENCE_IMAGES,
        generate_audio=False,
        audio_requires_1080p=False,
    ),
}


def _audio_track_for(caps: _KlingVideoModelCaps) -> VideoAudioMode:
    """文生 / 图生子路径的成片音轨形态。

    这两条子路径的请求体带 ``sound`` 开关，故有音频能力的 model 音轨可控；无音频能力的 model
    不发该字段，可灵各档默认 off，成片恒无声。``audio_requires_1080p`` 不在此处收窄——它是逐
    请求档位（mode）维度的约束，本函数没有档位上下文，按最宽档如实声明「开关可控」，实际是否
    产出人声由 ``_effective_audio`` 在请求期定夺（计价侧另见
    ``effective_generate_audio_for_model``）。
    """
    return VideoAudioMode.CONTROLLABLE if caps.generate_audio else VideoAudioMode.ALWAYS_OFF


def _lookup_video_caps(model: str) -> _KlingVideoModelCaps:
    """按 model 取能力位：剥厂商前缀后 + 去首尾空白 + lower 归一化，再做【精确】命中 _KLING_VIDEO_CAPS。
    中转前缀分隔符仅认仓库既有约定 ``/``（``vendor/kling-v3-omni``）与 ``:``（``provider:kling-v3-omni``）
    ——把 ``:`` 统一成 ``/`` 后取最后一段。刻意不把 ``_``/``.`` 当分隔符：它们是 model 名合法字符
    （wan2. / image-01 / kling-v3-omni 都含），当分隔符会切坏真实 model 名。未登记 model（含未来版本
    kling-v4、归一化后仍不精确匹配的中转自定义 id）回落保守默认（首尾帧、无参考/音频）——绝不按子串猜
    未知 model 的能力上限：未知 model 的限额可能与已知档不同，误报参考图能力会在请求期触发 provider 400
    或计费漂移，宁可保守。"""
    key = model.replace(":", "/").rsplit("/", 1)[-1].strip().lower()
    return _KLING_VIDEO_CAPS.get(key, _DEFAULT_VIDEO_CAPS)


def _encode_job_id(subpath: str, task_id: str, *, generate_audio: bool) -> str:
    """把生成类型子路径 + 有声标志编进持久化 job_id（``subpath:task_id:audio``）。

    可灵查询端点按生成类型分路径（``GET /v1/videos/{text2video|image2video}/{id}``），
    且重启 resume 时请求已无 ``start_image`` 可推断子路径——必须把子路径随 task_id 一起
    持久化，否则 image2video 任务 resume 会误查 text2video 端点取不到任务。

    有声标志（0/1）同理随 task_id 持久化：resume 直接复用 submit 时算定的有声决策，
    不按 resume 时（config 默认/请求可能已漂移）重算，避免有声/无声计费漂移。
    """
    return f"{subpath}:{task_id}:{1 if generate_audio else 0}"


def _decode_job_id(job_id: str) -> tuple[str, str, bool | None]:
    """从持久化 job_id 复原 ``(子路径, task_id, 有声标志)``。

    ``subpath:task_id:audio``（3 段，audio 为 0/1）带有声标志；``subpath:task_id``
    （2 段）不带，返回 None——caller 据此按无声处理；无已知前缀回落 text2video、
    整串作 task_id。
    """
    parts = job_id.split(":")
    if len(parts) == 3 and parts[0] in _RESUMABLE_SUBPATHS and parts[2] in ("0", "1"):
        return parts[0], parts[1], parts[2] == "1"
    prefix, sep, rest = job_id.partition(":")
    if sep and prefix in _RESUMABLE_SUBPATHS:
        return prefix, rest, None
    return _TEXT2VIDEO, job_id, None


class KlingVideoBackend(KlingBackendBase, ProviderJobIdPersistenceMixin):
    """可灵 Kling 视频后端（异步轮询，JWT / Bearer 双模式）。

    鉴权 / base_url 装配 / submit-poll 骨架由 ``KlingBackendBase`` 共享；``provider_job_id`` 持久化由
    ``ProviderJobIdPersistenceMixin`` 收口。本类只填视频侧差异：子路径派生、能力位查表、resume 与下载。
    """

    _media_label = "视频"

    def __init__(
        self,
        *,
        auth_mode: str = "jwt",
        access_key: str | None = None,
        secret_key: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        super().__init__(
            auth_mode=auth_mode,
            access_key=access_key,
            secret_key=secret_key,
            api_key=api_key,
            model=model or DEFAULT_MODEL,
            base_url=base_url,
            http_timeout=http_timeout,
        )
        # 按 model 取能力位（归一化前缀/大小写后精确命中）；未登记 model（bearer 透传）回落保守默认。
        self._caps = _lookup_video_caps(self._model)

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        # first_frame 恒真（各档均支持 i2v 首帧）；last_frame / reference_images / 上限按 model 从
        # _KLING_VIDEO_CAPS 读（_lookup_video_caps 归一化前缀/大小写后精确命中，未登记回落保守默认）。
        # max_reference_images 只在此处声明（编排层裁剪与生成时防御同读它），取保守值——
        # 官方文档未明确列出该上限。纯函数（不构造 client /
        # 不需 api_key），供 custom endpoint resolver 按 model_id 读上限复用。
        #
        # last_frame_requires_pro 为真的 model（kling-v2-5-turbo、kling-v2-6）：该位不按 service_tier
        # 分档——service_tier 是逐请求字段（generation_tasks 入队时选定），本函数只按 model 声明、
        # 无从得知调用方将选哪档。std/4k 档提交尾帧会被拒绝——有 tier 上下文的调用方走
        # video_capabilities_for_tier 在 media_generator 处拒，能力被用户覆盖放行时由
        # _build_payload 的 fail-loud 护栏兜底。declare 一个仅在少数档位成立的 True 会让无 tier
        # 上下文的调用方按此位放行 end_image、多数请求撞硬失败；保守声明 False 更贴近默认档的
        # 真实执行结果，与未登记 model 回落保守默认同一原则。
        caps = _lookup_video_caps(model)
        return VideoCapabilities(
            text_to_video=caps.text_to_video,
            first_frame=True,
            last_frame=caps.last_frame and not caps.last_frame_requires_pro,
            max_reference_images=caps.max_reference_images,
            audio_track=_audio_track_for(caps),
            reference_route_audio_track=VideoAudioMode.ALWAYS_OFF,
        )

    @staticmethod
    def effective_generate_audio_for_model(model: str) -> bool:
        """无逐请求档位上下文时，返回默认执行档真正生效的音频计价参数。

        有声受 1080P 约束的 model（v2-6）无从得知调用方将选哪档质量档，保守返回
        False；无约束的有声 model（v3 / v3-omni）只要请求要人声就产出，返回 True。执行期仍由
        ``_effective_audio`` 按请求实际质量档与子路径决定——v3-omni 走多图主体子路径时实际无声，
        本接口无从得知是否带参考图而返回 True，方向是高估成本（预估偏保守），不会低报。
        """
        caps = _lookup_video_caps(model)
        return caps.generate_audio and not caps.audio_requires_1080p

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    def video_capabilities_for_tier(self, service_tier: str, resolution: str | None = None) -> VideoCapabilities:
        """按实际请求档位收窄的 last_frame 声明，供有请求上下文的调用方使用。

        `video_capabilities_for_model(model)` 是无请求上下文的纯函数，对
        `last_frame_requires_pro` 的 model 只能保守声明 `last_frame=False`（供
        `/video-capabilities`、custom provider resolver 等无 tier 信息的调用方）。而
        `media_generator` 转发 `end_image` 前已知 `service_tier`/`resolution`——按此处收窄，
        实际解析出的 mode（复用 `_resolve_mode_from` 同一派生规则，`resolution="4k"` 优先于
        `service_tier`）为 pro 才放行、std/4k 档仍保守拒绝，与 `_build_payload` 的 fail-loud
        护栏放行条件对齐，避免 pro 档请求被上层静默丢帧（该请求实际会被 `_build_payload` 接受），
        也避免 4k+pro 组合被误判放行（`_resolve_mode` 对该组合解出 ``"4k"`` 而非 ``"pro"``）。
        """
        caps = _lookup_video_caps(self._model)
        mode = self._resolve_mode_from(resolution, service_tier)
        last_frame = caps.last_frame and (not caps.last_frame_requires_pro or mode == "pro")
        return VideoCapabilities(
            text_to_video=caps.text_to_video,
            first_frame=True,
            last_frame=last_frame,
            max_reference_images=caps.max_reference_images,
            audio_track=_audio_track_for(caps),
            reference_route_audio_track=VideoAudioMode.ALWAYS_OFF,
        )

    # ── request building ────────────────────────────────────────────────

    @staticmethod
    def _resolve_mode_from(resolution: str | None, service_tier: str | None) -> str:
        """质量档 → mode：resolution=4k 独立成 ``4k`` 档（仅 v3/v3-omni 可达），否则 service_tier→std/pro。

        与 per_second_tiered 定价的档位派生一致（4k 优先于 std/pro），保证请求档与计费档同源。
        `_resolve_mode` 与 `video_capabilities_for_tier` 共用此同一派生规则，避免两处独立实现
        对同一请求解出不同 mode（曾因此让 tier-aware 能力查询对 4k+pro 组合误判 last_frame=True）。
        """
        if (resolution or "").lower() == "4k":
            return "4k"
        return "pro" if (service_tier or "").lower() == "pro" else "std"

    def _resolve_mode(self, request: VideoGenerationRequest) -> str:
        return self._resolve_mode_from(request.resolution, request.service_tier)

    def _effective_audio(self, request: VideoGenerationRequest, *, subpath: str) -> bool:
        """实际是否产出视频内人声：请求要 + model 有 generate_audio 能力 + 走得到音频开关的子路径
        + 满足该 model 的分辨率约束。

        无能力的 model 恒 False——不被错配有声价（下游 pricing 取 ``result.generate_audio``）。

        ``subpath`` 必传而非由 ``request.reference_images`` 推断：resume 请求重建时不带图字段
        （见 ``media_generator.resume_video_async``），按参考图推断会把旧格式 job_id 的多图主体
        任务判成有声。generate 侧由 ``_build_payload`` 给出，resume 侧由 job_id 解码得出。

        「该子路径有没有音轨开关」不在此处二次实现，直接读 ``video_capabilities_for_model`` 的
        逐路径声明——那份声明就是展示层与入队预检读到的同一份（multi-image2video 原生 schema 不
        含 sound，故参考生视频声明为恒无声）。两处各写一遍的话，界面会继续放行一个执行期必然丢弃
        的开关，也会让该请求因标志进 ledger 而按有声价出账。
        """
        route: VideoRoute = "r2v" if subpath == _MULTI_IMAGE2VIDEO else "i2v"
        if self.video_capabilities_for_model(self._model).audio_track_for_route(route) != VideoAudioMode.CONTROLLABLE:
            return False
        if not request.generate_audio:
            return False
        if self._caps.audio_requires_1080p:
            # 官方约束的维度是分辨率（v2-6「生成有声视频时，仅支持生成 1080P」），但可灵请求体没有
            # 分辨率字段：输出档位只由 mode 决定，``request.resolution`` 除识别 4k 档外不进入 payload。
            # 判据因此落在实际发出的 mode 上。声明该位的 model（v2-6）只有 std / pro 两档，pro 即
            # 1080P；4k 档是 v3 系专有，对这些 model 是非法请求，不当作满足 1080P 放行——否则会发
            # sound="on" 并按有声价出账，换回一个必然失败或无声的任务。
            return self._resolve_mode(request) == "pro"
        return True

    @staticmethod
    def _valid_frames(images: list[Path] | None) -> list[Path]:
        """过滤出有效（非空）参考图路径；空 / None 归空列表。"""
        if not images:
            return []
        return [Path(img) for img in images if str(img)]

    def _build_payload(self, request: VideoGenerationRequest) -> tuple[str, dict]:
        """返回 (子路径, 请求体)。

        子路径优先级：有 reference_images → multi-image2video（多图主体 R2V）；
        有 start_image → image2video（含可选尾帧）；都无 → text2video。
        """
        payload: dict = {
            "model_name": self._model,
            "prompt": request.prompt,
            "mode": self._resolve_mode(request),
            "duration": str(request.duration_seconds),
            "aspect_ratio": request.aspect_ratio,
        }

        reference_images = self._valid_frames(request.reference_images)
        if reference_images:
            # 生成时防御（fail-loud）：未声明多图主体能力的 model 不得升级到 R2V 子路径，
            # 超上限的参考图数同样拦截——否则会把必然报错的请求发出去且照常计费。
            if not self._caps.reference_images:
                raise VideoCapabilityError("video_reference_images_unsupported", model=self._model)
            if len(reference_images) > self._caps.max_reference_images:
                raise VideoCapabilityError(
                    "video_reference_images_exceeded",
                    model=self._model,
                    count=len(reference_images),
                    limit=self._caps.max_reference_images,
                )
            # 多图主体：image_list 为 [{"image": <base64>}]（可灵原生 schema），无单首帧概念。
            payload["image_list"] = [{"image": self._encode_frame(p)} for p in reference_images]
            return _MULTI_IMAGE2VIDEO, payload

        start_image = request.start_image
        if not (isinstance(start_image, (str, Path)) and str(start_image)):
            # 无首帧/无参考 = 文生视频意图；不支持 t2v 的 model（如 kling-video-o1）即拒绝。
            if not self._caps.text_to_video:
                raise VideoCapabilityError("video_capability_missing_t2v", provider=self.name, model=self._model)
            subpath = _TEXT2VIDEO
        else:
            payload["image"] = self._encode_frame(Path(start_image))
            end_image = request.end_image
            if isinstance(end_image, (str, Path)) and str(end_image):
                # 该 model 的首尾帧仅 pro 档生效时，std/4k 档提交 image_tail 虽会被官方接口受理，
                # 尾帧约束却不生效——fail loud 拒绝而非放行一个调用方以为已生效实则被忽略的请求。
                if self._caps.last_frame_requires_pro and self._resolve_mode(request) != "pro":
                    raise VideoCapabilityError("video_last_frame_requires_pro", provider=self.name, model=self._model)
                payload["image_tail"] = self._encode_frame(Path(end_image))
            subpath = _IMAGE2VIDEO

        # 音频开关 sound（官方参数，取 "on"/"off"，各档默认 off）仅 text2video / image2video 子路径
        # 携带（multi-image2video 原生 schema 不含）；无音频能力的 model 不发该字段，避免向不支持的
        # 端点递未知参数。
        if self._caps.generate_audio:
            payload["sound"] = "on" if self._effective_audio(request, subpath=subpath) else "off"
        return subpath, payload

    def _encode_frame(self, path: Path) -> str:
        # fail-loud：声明了帧图却缺失/不可读即中止，不静默退化（会产出错误结果且照常计费）。
        if not path.is_file():
            raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=path.name)
        try:
            return image_to_base64(path)
        except OSError as exc:
            raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=path.name) from exc

    @staticmethod
    def _safe_log_view(subpath: str, payload: dict) -> dict:
        """预脱敏标量视图，直接喂 logger（避开 format_kwargs_for_log sink）。

        base64 帧图 / prompt 一律不展开：仅记是否存在 + prompt 长度。
        """
        prompt = payload.get("prompt")
        image_list = payload.get("image_list")
        return {
            "endpoint": subpath,
            "model_name": payload.get("model_name"),
            "mode": payload.get("mode"),
            "duration": payload.get("duration"),
            "aspect_ratio": payload.get("aspect_ratio"),
            # 空串 = 该档不携带音频开关，与显式 "off" 区分开
            "sound": payload.get("sound", ""),
            "has_image": "image" in payload,
            "has_image_tail": "image_tail" in payload,
            "reference_count": len(image_list) if isinstance(image_list, list) else 0,
            "prompt_len": len(prompt) if isinstance(prompt, str) else 0,
        }

    # ── generate / resume ───────────────────────────────────────────────

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        subpath, payload = self._build_payload(request)
        generate_audio = self._effective_audio(request, subpath=subpath)
        logger.info("调用 Kling 视频 API payload=%s", self._safe_log_view(subpath, payload))
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            task_id = await self._submit_task(client, f"videos/{subpath}", payload)
            logger.info("Kling 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            # 持久化「子路径:task_id:有声标志」而非裸 task_id：resume 据此复原查询端点
            # 与 submit 时的有声决策（见 _encode_job_id）。
            await self._persist_provider_job_id(
                request,
                _encode_job_id(subpath, task_id, generate_audio=generate_audio),
                provider=PROVIDER_KLING,
            )
            return await self._poll_and_build(client, subpath, task_id, request, generate_audio=generate_audio)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 Kling task：仅轮询 + 取 url + 下载，不重新提交（ADR 0007）。

        查询子路径从持久化 job_id 复原（submit 时编入）——可灵查询端点按生成类型分路径，
        而 resume 请求已无 ``start_image`` 可推断，故不能再从 request 取（见 _encode_job_id）。

        有声标志同样优先取持久化值（submit 时算定）：直连有声/无声计费，避免按 resume 时
        可能已漂移的 config 默认/请求重算。不含该标志的 2 段 job_id 一律判无声：这类 job_id 只
        由不具备音频能力的实现产出，成片必然无声，按当前能力表重算会让它们按有声价出账。
        """
        subpath, task_id, persisted_audio = _decode_job_id(job_id)
        generate_audio = persisted_audio if persisted_audio is not None else False
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            return await self._poll_and_build(client, subpath, task_id, request, generate_audio=generate_audio)

    # ── HTTP poll / download ────────────────────────────────────────────

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        subpath: str,
        task_id: str,
        request: VideoGenerationRequest,
        *,
        generate_audio: bool,
    ) -> VideoGenerationResult:
        final = await self._poll_until_terminal(
            lambda: self._poll_query(client, f"videos/{subpath}/{task_id}"),
            max_wait=request.poll_timeout_seconds,
        )
        await notify_provider_response(request, final)

        download_url = extract_kling_video_url(final)
        await self._download_with_retry(download_url, request.output_path)
        logger.info("Kling 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_KLING,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=download_url,
            task_id=task_id,
            # audio 门控后的实际有声标志（下游 finish_call 取它定有声/无声价）。
            generate_audio=generate_audio,
        )

    @staticmethod
    async def _download_with_retry(download_url: str, output_path: Path) -> None:
        await download_video(download_url, output_path, label="Kling")
