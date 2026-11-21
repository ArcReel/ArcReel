"""MiniMax（海螺）图像生成后端，两条同步调用通道。

- ``MiniMaxImageBackend``（端点 ``minimax-image``，image-01）：走 OpenAI 兼容 base 上的原生
  /image_generation 同步端点：单次 POST 直接返回图片 URL（24h 有效）或 base64，立即落地为本地资产。
  T2I 与 I2I 共用同一请求体，I2I 经 subject_reference 单脸参考驱动多场景角色一致性立绘。尺寸用
  width/height（512–2048、8 倍数），按「比例优先、清晰度其次」从项目 aspect_ratio + image_size 精确算出。
- ``MiniMaxH3ImageBackend``（端点 ``minimax-h3-image``，MiniMax-H3-Image 系列）：文生图走 JSON 的
  /images/generations，带参考图走 multipart 的 /images/edits（``image[]`` 按传入顺序，1–16 张）。
  响应与 OpenAI Images 同形（``data[]`` 的 url / b64_json），用量按 token 拆分回报。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import ClassVar

import httpx

from arcreel_market_core.aspect_size import (
    IMAGE_TIER_SHORT_EDGE,
    aspect_size,
    parse_aspect_ratio,
    resolution_to_short_edge,
)
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import read_reference_images, should_retry_submit, submit_post, with_artifact_retry
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageOutputMissingError,
    ImageRequestFacts,
    download_image_to_path,
    per_image_usage,
)
from lib.backends.input_limits import excess_image_names
from lib.backends.minimax_shared import (
    extract_image_base64,
    extract_image_url,
    minimax_failure_reason,
    minimax_headers,
    minimax_text_base_url,
    resolve_minimax_api_key,
    safe_body_for_log,
)
from lib.backends.providers import PROVIDER_MINIMAX
from lib.backends.transport_layer import TransportLayer
from lib.billing.metering import Usage
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "image-01"

_IMAGE_ENDPOINT = "/image_generation"

# image-01 宽高均 ∈ [512, 2048] 且须被 8 整除；可表达的最极端比例为 2048/512 = 4:1。
_MIN_EDGE = 512
_MAX_EDGE = 2048
_ROUND_TO = 8
_MAX_RATIO = 4.0

# 缺 image_size 时的默认短边（2K 档），经 max_long_edge=2048 收口后竖屏得 1152*2048。
_DEFAULT_SHORT = 1440

# image-01 单脸参考：subject_reference 只支持一张参考图。
_SUBJECT_REF_LIMIT = 1


def _clamp_edge(value: int) -> int:
    """把单边像素夹进 [512, 2048]。两端点均为 8 的倍数，夹取后仍满足 8 整除约束。"""
    return max(_MIN_EDGE, min(_MAX_EDGE, value))


class MiniMaxImageBackend:
    """MiniMax 图像后端（单步同步 image_generation 端点）。"""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = _SUBJECT_REF_LIMIT

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 120.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        self._api_key = resolve_minimax_api_key(api_key)
        self._base_url = minimax_text_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        self._transport_layer = transport_layer

    @property
    def name(self) -> str:
        return PROVIDER_MINIMAX

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return set(self.CHANNEL_CAPABILITIES)

    async def reference_image_limit(self) -> int | None:
        return self.CHANNEL_REFERENCE_LIMIT

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        """预估只报张数：image-01 按张统一价，尺寸不影响费用。"""
        refs = facts.reference_count
        return per_image_usage(output_images=1, input_images=refs)

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        # 编排层不带重试：把非幂等的「建图 + 计费」submit 与幂等的结果下载隔离到各自的
        # 重试范围（_submit / _download_result），避免下载失败回退到重跑生成 POST 造成重复计费。
        width, height = self._resolve_dimensions(request)

        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "width": width,
            "height": height,
            "response_format": "url",
            "n": 1,
            # ArcReel 剧本 prompt 已是 LLM 精炼描述，关闭智能改写保留原意。
            "prompt_optimizer": False,
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.reference_images:
            # 参考图读盘 + base64 编码（可能数 MB）offload 到线程，避免阻塞事件循环；
            # 单次 to_thread 整体执行同步 helper，最小化线程调度开销。
            payload["subject_reference"] = await asyncio.to_thread(self._build_subject_reference, request)

        data = await self._submit(payload)
        image_uri = await self._persist_image(data, request.output_path)
        logger.info("MiniMax 图片生成完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_MINIMAX,
            model=self._model,
            image_uri=image_uri,
            usage=per_image_usage(
                output_images=1,
                input_images=len(payload.get("subject_reference", [])),
                dimensions={"resolution": f"{width}x{height}"},
            ),
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _submit(self, payload: dict) -> dict:
        """单步图像生成 POST（非幂等「建图 + 计费」），返回解析后的响应体。

        重试范围严格限定在本方法内、不含下载——下载失败不会触发整流程重试导致重复建图与
        重复计费。submit_post 把歧义传输错误转 AmbiguousSubmitError 终态失败避免重复计费；
        >=400 落 body 日志 + 抛 HTTPStatusError（保留 status_code 供咽喉层识别 413 降档），
        交 should_retry_submit 按状态码分流。
        """
        logger.info(
            "调用 %s 图片 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(safe_body_for_log(payload)),
        )
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{_IMAGE_ENDPOINT}",
                    json=payload,
                    headers=minimax_headers(self._api_key),
                ),
                provider=PROVIDER_MINIMAX,
            )
            return resp.json()

    def _resolve_dimensions(self, request: ImageGenerationRequest) -> tuple[int, int]:
        """按「比例优先、清晰度其次」算出 (宽, 高)。

        比例永远来自 aspect_ratio；image_size（档位词 / 自定义 宽*高 / None）只决定清晰度短边，
        自定义值剥离其自带比例（取 min）。短边先夹到 ≥ _MIN_EDGE 再算尺寸——否则过小的短边会让
        aspect_size 产出 <512 的边，随后 _clamp_edge 对宽高独立夹取会破坏比例（如 16:9 退化成
        512x512 的 1:1）。结果被 8 整除、长边受 2048 收口，每边仍经 _clamp_edge 夹进 [512, 2048] 兜底。
        """
        short = max(
            _MIN_EDGE,
            resolution_to_short_edge(
                request.image_size or None, tier_map=IMAGE_TIER_SHORT_EDGE, default_short=_DEFAULT_SHORT
            ),
        )
        w, h = aspect_size(
            request.aspect_ratio, short, round_to=_ROUND_TO, max_long_edge=_MAX_EDGE, max_ratio=_MAX_RATIO
        )
        return _clamp_edge(w), _clamp_edge(h)

    def _build_subject_reference(self, request: ImageGenerationRequest) -> list[dict]:
        """构建单脸参考；拒绝超量或不可读输入，避免丢图后照常计费。"""
        refs = request.reference_images
        if len(refs) > _SUBJECT_REF_LIMIT:
            raise ImageCapabilityError(
                "image_reference_images_exceeded",
                names=excess_image_names(_SUBJECT_REF_LIMIT, len(refs), (ref.path for ref in refs)),
                model=self._model,
                limit=self.CHANNEL_REFERENCE_LIMIT,
                count=len(refs),
            )
        image_file = read_reference_images([refs[0].path], self.model)[0].data_uri()
        return [{"type": "character", "image_file": image_file}]

    async def _persist_image(self, data: dict, output_path: Path) -> str | None:
        """把 image_generation 响应落地为本地文件，返回远端 URL（base64 路径返回 None）。

        先查 base_resp 业务错误（200 + 非零 status_code），再优先 URL（立即下载，24h 失效前落地），
        URL 缺失降级 base64 解码写盘；两者皆空即报错。
        """
        reason = minimax_failure_reason(data)
        if reason:
            raise RuntimeError(reason)

        url = extract_image_url(data)
        if url:
            await self._download_result(url, output_path)
            return url

        b64 = extract_image_base64(data)
        if b64:
            await _write_base64_image(b64, output_path)
            return None

        # 完整响应体记日志便于诊断，但不嵌进异常消息——避免 body 里的 "503"/"timeout" 等子串
        # 被默认 should_retry 误判为可重试（仓库已确立按状态码而非字符串判重试）。
        logger.error("MiniMax 图像响应缺少 image_urls/image_base64: %s", data)
        raise RuntimeError("MiniMax 图像响应缺少 image_urls/image_base64")

    async def _download_result(self, url: str, output_path: Path) -> None:
        """下载已签发的结果图 URL（幂等 GET），走共用的产物下载预算。

        瞬态失败在本层重试，绝不回退到重跑非幂等的生成 POST。共用谓词把 403/404 也算作
        可重试：终态刚签发的产物地址可能尚未在对象存储侧传播。其余 4xx 确定性失败。
        下载比生成更宽容——失败不浪费生成额度，故预算比生成阶段大得多。
        """
        await with_artifact_retry(lambda: download_image_to_path(url, output_path), label="MiniMax 图像")


# ── MiniMax-H3-Image 系列 ─────────────────────────────────────────────────────
# 官方接口：https://platform.minimax.cn/docs/api-reference/image-generation-h3-generations
#          https://platform.minimax.cn/docs/api-reference/image-generation-h3-edits

H3_ENDPOINT = "minimax-h3-image"
H3_DEFAULT_MODEL = "MiniMax-H3-Image"
H3_GENERATIONS_PATH = "/images/generations"
H3_EDITS_PATH = "/images/edits"

_H3_REFERENCE_LIMIT = 16
# 服务端同步等待最长 600 秒，官方要求客户端超时不少于此值。
_H3_HTTP_TIMEOUT_SECONDS = 600.0
# size=auto 时由 area 档位决定目标面积；缺 image_size 时取 2K，不落到服务端默认的 4K。
_H3_AREA_TIERS: tuple[str, ...] = ("1K", "2K", "4K")
_H3_DEFAULT_AREA = "2K"
_H3_SUPPORTED_AREAS = ", ".join(_H3_AREA_TIERS)
# aspect_ratio 的 X、Y 是 1–300 的整数，比例在 1:3 到 3:1 之间（含端点）；显式宽x高同样受此比例约束。
_H3_MAX_RATIO_TERM = 300
_H3_MAX_RATIO = 3
_H3_SUPPORTED_RATIOS = f"1:{_H3_MAX_RATIO} – {_H3_MAX_RATIO}:1"
# 显式宽x高的面积范围（含端点），服务端不缩放、不取整，越界直接 400。
_H3_MIN_PIXELS = 655_360
_H3_MAX_PIXELS = 20_000_000
_H3_SIZE_ROUND_TO = 8
_H3_OUTPUT_FORMATS: dict[str, str] = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp"}
# edits 受理的参考图格式：JPG、JPEG、PNG、WEBP、HEIC、HEIF。
_H3_REFERENCE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}


def _should_retry_h3_submit(exc: Exception) -> bool:
    """H3 生成提交的重试谓词：只重试请求确定没被受理的失败。

    提交不幂等：请求送达后，客户端超时或断开，服务端仍会生成并对成功的图片计费。传输错误沿用
    ``should_retry_submit``，只重试连接建立失败这类请求确定未送达的情形，歧义态由 ``submit_post``
    转成终态失败。HTTP 状态只重试 429（每分钟出图数或套餐额度超限，请求未被受理）；5xx、408 可能
    来自网关在服务端仍在生成时断开，一律不重试。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429
    return should_retry_submit(exc)


def _h3_aspect_ratio(aspect_ratio: str, model: str) -> tuple[int, int]:
    """项目比例约简后的 (X, Y)；超出 H3 受理范围时在提交前报错。"""
    aw, ah = parse_aspect_ratio(aspect_ratio)
    if max(aw, ah) > _H3_MAX_RATIO_TERM or max(aw, ah) > _H3_MAX_RATIO * min(aw, ah):
        raise ImageCapabilityError(
            "image_aspect_ratio_not_supported",
            model=model,
            aspect_ratio=aspect_ratio,
            supported=_H3_SUPPORTED_RATIOS,
        )
    return aw, ah


def _h3_size_params(image_size: str | None, aspect_ratio: str, model: str) -> dict[str, str]:
    """按「比例优先、清晰度其次」给出 H3 的尺寸字段。

    档位词（1K / 2K / 4K，大小写不敏感）与缺省值发送 ``aspect_ratio`` + ``area``，由服务端按比例算宽高；
    自定义宽x高只取短边，按项目比例算出 ``size``，此时不带 ``aspect_ratio`` 与 ``area``（两者同发即 400）。
    其余档位（如 512px）与越界面积在提交前报错，不夹取、不改写成别的档位。
    """
    aw, ah = _h3_aspect_ratio(aspect_ratio, model)
    value = (image_size or "").strip()
    if not value or value.upper() in _H3_AREA_TIERS:
        return {"aspect_ratio": f"{aw}:{ah}", "area": value.upper() or _H3_DEFAULT_AREA}

    unsupported = ImageCapabilityError(
        "image_resolution_not_supported", model=model, resolution=value, supported=_H3_SUPPORTED_AREAS
    )
    if value.lower() in {tier.lower() for tier in IMAGE_TIER_SHORT_EDGE}:
        raise unsupported
    short = resolution_to_short_edge(value, tier_map={}, default_short=0)
    if short <= 0:
        raise unsupported
    width, height = aspect_size(aspect_ratio, short, round_to=_H3_SIZE_ROUND_TO)
    if not _H3_MIN_PIXELS <= width * height <= _H3_MAX_PIXELS:
        raise unsupported
    return {"size": f"{width}x{height}"}


def _h3_resolution_dimension(params: dict[str, str]) -> dict[str, object]:
    """请求里决定清晰度的字段作为选择维度：显式宽x高取 ``size``，否则取 ``area`` 档位。"""
    return {"resolution": params.get("size") or params["area"]}


def _h3_token_counts(payload: dict) -> tuple[int | None, int | None, int | None]:
    """从响应 ``usage`` 取 (文字输入, 参考图输入, 图片输出) token；拿不到输出 token 时三项都为 None。

    文字输入含提示词增强消耗的 token，参考图输入在文生图时为 0。输出 token 优先取
    ``output_tokens_details.image_tokens``，缺失时取 ``output_tokens``。
    """
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None, None
    in_details = usage.get("input_tokens_details")
    out_details = usage.get("output_tokens_details")
    in_details = in_details if isinstance(in_details, dict) else {}
    out_details = out_details if isinstance(out_details, dict) else {}

    def _int(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    image_out = _int(out_details.get("image_tokens"))
    if image_out is None:
        image_out = _int(usage.get("output_tokens"))
    text_in = _int(in_details.get("text_tokens"))
    image_in = _int(in_details.get("image_tokens"))
    if image_out is None or (text_in is None and image_in is None):
        # 输入或输出缺一侧时数据残缺，按拿不到用量处理，不按部分 token 记账。
        return None, None, None
    return text_in, image_in, image_out


def _h3_safe_log_view(fields: dict, reference_count: int) -> dict:
    """请求日志视图：标量字段原样，提示词只记长度，参考图只记张数。"""
    view = safe_body_for_log(fields)
    if reference_count:
        view["image"] = f"<{reference_count} files>"
    return view


class MiniMaxH3ImageBackend:
    """MiniMax H3 图像后端：文生图 /images/generations（JSON），参考图编辑 /images/edits（multipart）。

    提交与下载分属两段重试：生成 POST 只在请求确定未被受理时重试；结果 URL 的下载在产物下载预算内
    单独重试，失败不会重新提交生成。
    """

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = _H3_REFERENCE_LIMIT

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = _H3_HTTP_TIMEOUT_SECONDS,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        self._api_key = resolve_minimax_api_key(api_key)
        self._base_url = minimax_text_base_url(base_url)
        self._model = model or H3_DEFAULT_MODEL
        self._http_timeout = http_timeout
        self._transport_layer = transport_layer

    @property
    def name(self) -> str:
        return PROVIDER_MINIMAX

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return set(self.CHANNEL_CAPABILITIES)

    async def reference_image_limit(self) -> int | None:
        return self.CHANNEL_REFERENCE_LIMIT

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        """预估用量保持未知：官方按实际 token 计费，没有公布由请求事实推出 token 数的规则。

        不报任何计量项，按 token 计价的价目表据此记为无法预估，不套用其他供应商的 token 公式，也不记 0；
        选择维度仍按请求构造的同一套尺寸规则给出，使自定义供应商下按张分分辨率计价的模型与结算同口径。
        """
        try:
            params = _h3_size_params(facts.image_size, facts.aspect_ratio, facts.model)
        except (ImageCapabilityError, ValueError):
            return Usage()
        return Usage(dimensions=_h3_resolution_dimension(params))

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        params = _h3_size_params(request.image_size, request.aspect_ratio, self._model)
        fields: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "n": 1,
            "response_format": "url",
            "output_format": _H3_OUTPUT_FORMATS.get(request.output_path.suffix.lower(), "png"),
            **params,
        }
        refs = request.reference_images
        if refs:
            if len(refs) > _H3_REFERENCE_LIMIT:
                raise ImageCapabilityError(
                    "image_reference_images_exceeded",
                    names=excess_image_names(_H3_REFERENCE_LIMIT, len(refs), (ref.path for ref in refs)),
                    model=self._model,
                    limit=_H3_REFERENCE_LIMIT,
                    count=len(refs),
                )
            files = await asyncio.to_thread(self._read_reference_files, request)
            data = await self._submit_edit(fields, files)
        else:
            # ArcReel 的提示词已经过 LLM 精炼，关闭提示词增强以保留原意；edits 不提供这个开关。
            data = await self._submit_generation({**fields, "disable_prompt_enhanced": True})

        image_uri = await self._persist_image(data, request.output_path)
        logger.info("MiniMax H3 图片生成完成: %s", request.output_path)

        text_in, image_in, image_out = _h3_token_counts(data)
        dimensions = _h3_resolution_dimension(params)
        usage = (
            Usage(
                quantities={
                    meter: value
                    for meter, value in (
                        ("uncached_input_tokens", text_in),
                        ("uncached_image_input_tokens", image_in),
                        ("image_output_tokens", image_out),
                    )
                    if value is not None
                },
                dimensions=dimensions,
            )
            if image_out is not None
            # 拿不到 token 用量时只报出图张数：价目表按 token 计价，这笔记为未定价，不按估算值记账。
            else per_image_usage(output_images=1, input_images=None, dimensions=dimensions)
        )
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_MINIMAX,
            model=self._model,
            image_uri=image_uri,
            usage=usage,
            image_input_tokens=image_in,
            image_output_tokens=image_out,
            text_input_tokens=text_in,
        )

    def _read_reference_files(self, request: ImageGenerationRequest) -> list[tuple[str, tuple[str, bytes, str]]]:
        """按传入顺序读出参考图，组成 multipart 的 ``image[]`` 部分；缺失或读不出的参考图直接报错，不跳过。"""
        return [
            ("image[]", image.multipart())
            for image in read_reference_images((ref.path for ref in request.reference_images), self.model)
        ]

    @with_retry_async(retry_if=_should_retry_h3_submit)
    async def _submit_generation(self, payload: dict) -> dict:
        """文生图 POST（非幂等「出图 + 计费」），返回解析后的响应体。"""
        logger.info(
            "调用 %s 图片 API (T2I) model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(_h3_safe_log_view(payload, 0)),
        )
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{H3_GENERATIONS_PATH}",
                    json=payload,
                    headers=minimax_headers(self._api_key),
                ),
                provider=PROVIDER_MINIMAX,
            )
            return resp.json()

    @with_retry_async(retry_if=_should_retry_h3_submit)
    async def _submit_edit(self, fields: dict, files: list[tuple[str, tuple[str, bytes, str]]]) -> dict:
        """参考图编辑 POST（multipart，非幂等「出图 + 计费」），返回解析后的响应体。

        只带鉴权头：Content-Type 由 httpx 按 multipart 边界生成，不能沿用 JSON 的固定值。
        """
        logger.info(
            "调用 %s 图片 API (I2I) model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(_h3_safe_log_view(fields, len(files))),
        )
        form = {key: str(value) for key, value in fields.items()}
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{H3_EDITS_PATH}",
                    data=form,
                    files=files,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                ),
                provider=PROVIDER_MINIMAX,
            )
            return resp.json()

    async def _persist_image(self, data: dict, output_path: Path) -> str | None:
        """把首张结果落地为本地文件，返回远端 URL（b64_json 路径返回 None）。

        优先 URL（24h 内有效，立即下载），没有 URL 时解码 b64_json；两者皆无（含该图的 ``error`` 项）
        报 ``ImageOutputMissingError``。
        """
        items = data.get("data")
        first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
        url = first.get("url")
        if isinstance(url, str) and url:
            await with_artifact_retry(lambda: download_image_to_path(url, output_path), label="MiniMax H3 图像")
            return url
        b64 = first.get("b64_json")
        if isinstance(b64, str) and b64:
            await _write_base64_image(b64, output_path)
            return None
        # 只记错误码与 request_id，不落整 body（含 prompt 增强结果等长文本）。
        error = first.get("error")
        error = error if isinstance(error, dict) else {}
        logger.error(
            "MiniMax H3 图像响应没有图片: error_code=%s request_id=%s",
            error.get("code"),
            data.get("request_id"),
        )
        raise ImageOutputMissingError(model=self._model)


MINIMAX_IMAGE_BACKENDS: dict[str, type[MiniMaxH3ImageBackend]] = {H3_ENDPOINT: MiniMaxH3ImageBackend}


async def _write_base64_image(b64: str, output_path: Path) -> None:
    """解码 base64 图片并写盘（解码 + 写盘 offload 到线程，避免事件循环内做 CPU 密集解码）。

    容忍少数中转返回 data URI（``data:image/...;base64,<payload>``）：剥前缀后再解码。
    """
    payload = b64
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]

    def _decode_and_save() -> None:
        image_bytes = base64.b64decode(payload)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)

    await run_noninterruptible_sync(_decode_and_save)
