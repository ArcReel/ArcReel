"""OpenRouterImageBackend — OpenRouter 统一图片生成后端。

OpenRouter 的图片 API 与 OpenAI 官方不同源：不提供 /images/edits（实测 404），T2I 与
I2I 统一走 ``POST {base_url}/images`` 的 JSON 端点——参考图以 data URI 进
``input_references``，响应 ``data[].b64_json``。本后端因此不复用 OpenAI SDK，请求构造
仿 MiniMax 的 raw httpx 模式，落盘复用 :func:`save_image_from_response_item`。

尺寸参数按模型在 ``GET {base_url}/images/models`` 声明的 ``supported_parameters`` 自适应
下发（进程级缓存 1h；实测 OpenRouter 把 ``size`` 与 ``resolution`` 同时出现视为冲突 400，
两套参数互斥、只能按声明选其一）：

- 声明 ``resolution``（gemini / seedream / qwen / grok / krea 系）→ ``aspect_ratio`` +
  ``resolution``（不传 size）。档位短边映射到声明枚举里「≥ 请求值的最小档」，映射不到则省略。
- 未声明 ``resolution`` 但声明 ``aspect_ratio``（gpt-image / recraft / mai 系）→ ``size``
  （OpenAI 同款 WxH，比例优先、清晰度其次）+ ``aspect_ratio``，声明 ``quality`` 再叠加。
- 模型未收录或发现端点失败 → 退回纯 ``size``（与 OpenAI 兼容网关同款），保证生成不因发现
  失败而中断。
- 收录但 size / aspect_ratio / resolution 一概不声明（如包装型模型）→ 省略全部尺寸参数，
  信任模型默认。

usage 不做拆分解析，置 None 让 cost_calculator 走静态 fallback。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import httpx

from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.aspect_size import IMAGE_TIER_SHORT_EDGE, aspect_size, parse_aspect_ratio, resolution_to_short_edge
from lib.backends.backend_runtime import should_retry_submit, submit_post
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    image_to_base64_data_uri,
    save_image_from_response_item,
)
from lib.backends.openai_shared import OPENAI_IMAGE_QUALITY_MAP as _QUALITY_MAP
from lib.backends.providers import PROVIDER_OPENROUTER
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-image-2"
_MAX_REFERENCE_IMAGES = 16

_IMAGES_ENDPOINT = "/images"
_MODELS_ENDPOINT = "/images/models"

#: 生成耗时可达 90s+，超时须远大于常规请求
_HTTP_TIMEOUT = httpx.Timeout(300.0, connect=15.0)
#: 模型目录查询是轻量元数据请求，超时收紧；失败只影响参数精化，不影响生成
_MODELS_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# 档位 → quality 大小写不敏感（与 OpenAIImageBackend 同口径）：自定义档位词可能写
# "2k"/"4K "，不归一会让 size 解析成功但 quality 静默丢失。自定义 WxH（无档位）→ None。
_QUALITY_MAP_CI = {k.lower(): v for k, v in _QUALITY_MAP.items()}

# (base_url, model) → (monotonic 截止, supported_parameters)。目录与租户无关，进程级缓存；
# 未收录模型负缓存同 TTL，避免每次生成都白打一趟目录。查询失败不缓存，下次生成再试。
_MODEL_PARAMS_TTL_SECONDS = 3600.0
_model_params_cache: dict[tuple[str, str], tuple[float, dict | None]] = {}


def _quality_for(image_size: str | None) -> str | None:
    return _QUALITY_MAP_CI.get(image_size.strip().lower()) if image_size else None


def _enum_values(supported: dict, name: str) -> list[str]:
    """取声明枚举的取值列表（{"type": "enum", "values": [...]}）；声明缺失 / 形态不符 → []。"""
    spec = supported.get(name)
    values = spec.get("values") if isinstance(spec, dict) else None
    return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []


def _range_max(supported: dict, name: str) -> int | None:
    """取声明 range 的上限（{"type": "range", "min": m, "max": n}）；缺失 → None。"""
    spec = supported.get(name)
    if not isinstance(spec, dict):
        return None
    value = spec.get("max")
    return value if isinstance(value, int) else None


def _resolution_px(value: str) -> int | None:
    """把 resolution 枚举值换算成短边像素（"512" → 512，"1K"/"2K"/"4K" → 1024/2048/4096）。"""
    text = value.strip().upper()
    if text.endswith("K"):
        try:
            return int(text[:-1]) * 1024
        except ValueError:
            return None
    try:
        return int(text)
    except ValueError:
        return None


def _pick_resolution(values: list[str], short_edge: int | None) -> str | None:
    """档位短边 → 声明枚举里「≥ 请求值的最小档」（不降档）；全档都低于请求值取最大档。"""
    if short_edge is None:
        return None
    candidates = sorted(px for v in values if (px := _resolution_px(v)) is not None)
    if not candidates:
        return None
    for px in candidates:
        if px >= short_edge:
            for v in values:
                if _resolution_px(v) == px:
                    return v
    for v in values:
        if _resolution_px(v) == candidates[-1]:
            return v
    return None


def _pick_aspect_ratio(values: list[str], aspect_ratio: str) -> str | None:
    """项目比例 → 声明枚举：精确命中（大小写不敏感）优先，否则取数值最接近的一档。"""
    candidates = [v for v in values if v.lower() != "auto"]
    lowered = {v.lower(): v for v in candidates}
    if aspect_ratio.lower() in lowered:
        return lowered[aspect_ratio.lower()]
    target_w, target_h = parse_aspect_ratio(aspect_ratio)
    target = target_w / target_h
    best: tuple[float, str] | None = None
    for v in candidates:
        try:
            w, h = parse_aspect_ratio(v)
        except ValueError:
            continue
        score = abs(w / h - target)
        if best is None or score < best[0]:
            best = (score, v)
    return best[1] if best else None


def _resolve_model_params(supported: dict | None, image_size: str | None, aspect_ratio: str) -> dict[str, str]:
    """按声明自适应下发尺寸参数，互斥的两套通道只能选其一（同传 size+resolution 必 400）。"""
    quality = _quality_for(image_size)
    if supported is None:
        # 模型未收录 / 目录不可得：退回 OpenAI 同款 size，保证兼容网关照常出图
        short = resolution_to_short_edge(image_size, tier_map=IMAGE_TIER_SHORT_EDGE)
        w, h = aspect_size(aspect_ratio, short, round_to=16)
        params: dict[str, str] = {"size": f"{w}x{h}"}
        if quality:
            params["quality"] = quality
        return params

    size_values = _enum_values(supported, "size")
    resolution_values = _enum_values(supported, "resolution")
    ratio_values = _enum_values(supported, "aspect_ratio")
    quality_values = _enum_values(supported, "quality")

    params = {}
    ratio = _pick_aspect_ratio(ratio_values, aspect_ratio) if ratio_values else None
    if size_values:
        # 声明 size：OpenAI 同款精确 WxH（比例优先、清晰度其次），比例档位只作冗余声明
        short = resolution_to_short_edge(image_size, tier_map=IMAGE_TIER_SHORT_EDGE)
        w, h = aspect_size(aspect_ratio, short, round_to=16)
        params["size"] = f"{w}x{h}"
        if ratio:
            params["aspect_ratio"] = ratio
    elif resolution_values:
        # 声明 resolution：按枚举下发，不传 size（互斥）。ratio / resolution 拿不到（档位映射
        # 不上、极端比例超出枚举）时各自省略——比传冲突参数被 400 拒掉好。
        resolution = _pick_resolution(
            resolution_values, resolution_to_short_edge(image_size, tier_map=IMAGE_TIER_SHORT_EDGE)
        )
        if resolution:
            params["resolution"] = resolution
        if ratio:
            params["aspect_ratio"] = ratio
    elif ratio:
        # 只声明 aspect_ratio：size 撑清晰度，ratio 锁比例
        short = resolution_to_short_edge(image_size, tier_map=IMAGE_TIER_SHORT_EDGE)
        w, h = aspect_size(aspect_ratio, short, round_to=16)
        params["size"] = f"{w}x{h}"
        params["aspect_ratio"] = ratio
    # 收录但三类都不声明：省略全部尺寸参数，信任模型默认
    if quality and quality_values and quality.lower() in {v.lower() for v in quality_values}:
        params["quality"] = quality
    return params


class OpenRouterImageBackend:
    """OpenRouter 统一图片后端：T2I 与 I2I 同 hit ``POST {base_url}/images``。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        self._api_key = api_key or ""
        self._model = model or DEFAULT_MODEL
        # OpenRouter 的 base_url 已含 /api/v1，不做 ensure_openai_base_url 的 /v1 追加
        self._base_url = (str(base_url).strip().rstrip("/") if base_url else "") or DEFAULT_BASE_URL

    @property
    def name(self) -> str:
        return PROVIDER_OPENROUTER

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    @property
    def max_reference_images(self) -> int:
        return _MAX_REFERENCE_IMAGES

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        refs = request.reference_images
        supported = await self._fetch_supported_params()
        refs = self._clamp_references(refs, supported)

        input_references = await asyncio.to_thread(self._encode_references, refs)
        payload: dict = {"model": self._model, "prompt": request.prompt, "n": 1}
        payload.update(_resolve_model_params(supported, request.image_size, request.aspect_ratio))
        if request.seed is not None:
            payload["seed"] = request.seed
        if input_references:
            payload["input_references"] = input_references
        elif refs:
            # 声明了 i2i 但所有参考图都读不出——按既有语义报错而非静默降级 T2I
            raise ImageCapabilityError(
                "image_endpoint_mismatch_no_i2i",
                model=self._model,
                detail="all reference images failed to open",
            )

        body = await self._submit(payload)
        await self._persist_image(body, request.output_path)
        logger.info("OpenRouter 图片生成完成: %s", request.output_path)
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_OPENROUTER,
            model=self._model,
            quality=_quality_for(request.image_size),
        )

    def _clamp_references(self, refs: list, supported: dict | None) -> list:
        """按模型声明的 input_references 上限收口：超限截断告警，上限 0 还带参考图即 fail-loud。"""
        declared_max = _range_max(supported, "input_references") if supported is not None else None
        if declared_max is None:
            return refs
        if declared_max == 0 and refs:
            raise ImageCapabilityError(
                "image_endpoint_mismatch_no_i2i",
                model=self._model,
                detail=f"model {self._model} declares input_references max {declared_max}",
            )
        if len(refs) > declared_max:
            logger.warning(
                "参考图数量 %d 超过模型声明上限 %d，截断 (model=%s)",
                len(refs),
                declared_max,
                self._model,
            )
            refs = refs[:declared_max]
        return refs

    async def _fetch_supported_params(self) -> dict | None:
        """查该 model 的 supported_parameters（进程级缓存 1h）。

        网络异常 / 非 2xx / 目录不含该模型一律按「未收录」返回 None——只影响尺寸参数精化，
        生成流程按 size 兜底照常走，不因发现失败而中断。
        """
        key = (self._base_url, self._model)
        now = time.monotonic()
        cached = _model_params_cache.get(key)
        if cached and now - cached[0] < _MODEL_PARAMS_TTL_SECONDS:
            return cached[1]
        try:
            async with artifact_http_client(timeout=_MODELS_TIMEOUT) as client:
                resp = await client.get(
                    f"{self._base_url}{_MODELS_ENDPOINT}",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                resp.raise_for_status()
                items = (resp.json() or {}).get("data") or []
        except Exception:
            logger.warning("OpenRouter 模型目录查询失败，按未收录处理: model=%s", self._model, exc_info=True)
            return None
        supported: dict | None = None
        for item in items:
            if isinstance(item, dict) and item.get("id") == self._model:
                raw = item.get("supported_parameters")
                supported = raw if isinstance(raw, dict) else None
                break
        _model_params_cache[key] = (now, supported)
        return supported

    @staticmethod
    def _encode_references(refs) -> list[dict]:
        """参考图 data URI 编码；缺失的跳过并告警，全缺由调用方判错。"""
        encoded = []
        for ref in refs:
            ref_path = Path(ref.path) if ref.path else None
            if ref_path is None or not ref_path.is_file():
                logger.warning("参考图不存在，跳过: %s", ref_path)
                continue
            encoded.append({"type": "image_url", "image_url": {"url": image_to_base64_data_uri(ref_path)}})
        return encoded

    @with_retry_async(retry_if=should_retry_submit)
    async def _submit(self, payload: dict) -> dict:
        """单步图像生成 POST（非幂等「建图 + 计费」），返回解析后的响应体。

        重试范围严格限定在本方法内、不含落盘——落盘失败不会触发整流程重试导致重复建图与
        重复计费。submit_post 把歧义传输错误转 AmbiguousSubmitError 终态失败避免重复计费；
        >=400 抛 HTTPStatusError 交 should_retry_submit 按状态码分流。
        """
        log_view = {**payload, "prompt": payload.get("prompt", "")[:80]}
        logger.info("调用 %s 图片 API kwargs=%s", self.name, format_kwargs_for_log(log_view))
        async with artifact_http_client(timeout=_HTTP_TIMEOUT) as client:
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{_IMAGES_ENDPOINT}",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                ),
                provider=PROVIDER_OPENROUTER,
            )
            body = resp.json()
        if body is None:
            # 空 data 通常是内容安全过滤命中或上游网关异常，给出清晰错误便于排查
            raise RuntimeError(
                f"OpenRouter 图片生成响应 data 为空 (model={self._model})，可能触发内容安全过滤或上游服务异常"
            )
        return body

    async def _persist_image(self, body: dict, output_path: Path) -> None:
        items = body.get("data") or []
        if not items:
            # 空 data 通常是内容安全过滤命中或上游网关异常，给出清晰错误便于排查
            raise RuntimeError(
                f"OpenRouter 图片生成响应 data 为空 (model={self._model})，可能触发内容安全过滤或上游服务异常"
            )
        first = items[0] if isinstance(items[0], dict) else {}
        # save_image_from_response_item 以属性访问读取 b64_json/url——dict 包一层即可复用
        wrapped = SimpleNamespace(b64_json=first.get("b64_json"), url=first.get("url"))
        await save_image_from_response_item(wrapped, output_path)
