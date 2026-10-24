"""OpenRouterImageBackend — OpenRouter 统一图片生成后端。

OpenRouter 的图片 API 与 OpenAI 官方不同源：不提供 /images/edits，T2I 与
I2I 统一走 ``POST {base_url}/images`` 的 JSON 端点——参考图以 data URI 进
``input_references``，响应 ``data[].b64_json``。本后端因此不复用 OpenAI SDK，请求构造
仿 MiniMax 的 raw httpx 模式，落盘在不可中断段内解码并统一转为 PNG。

模型能力取自 ``GET {base_url}/images/models`` 里该模型声明的 ``supported_parameters``（缓存在 backend
实例上：查到的结果 1h，查询失败 60s）。参考图上限（``input_references``）经 :meth:`reference_image_limit`
交给编排层先裁剪再编号；未声明的参数（``n`` / ``seed``）不下发。尺寸参数按声明自适应（OpenRouter 把
``size`` 与 ``resolution`` 同时出现视为冲突并返回 400，两套参数互斥、只能按声明选其一）：

- 声明 ``resolution``（gemini / seedream / qwen / grok / krea 系，同时声明 ``size`` 也算）→
  ``aspect_ratio`` + ``resolution``（不传 size）。档位短边映射到声明枚举里「≥ 请求值的最小档」，映射不到则省略。
- 未声明 ``resolution`` 但声明 ``size`` 或 ``aspect_ratio``（gpt-image / recraft / mai 系）→ ``size``
  （声明了 size 枚举时只发枚举里的值，否则合成 OpenAI 同款 WxH）+ ``aspect_ratio``，声明 ``quality`` 再叠加。
- 模型未收录或目录查询失败 → 只发 ``size``（与 OpenAI 兼容网关同款），保证生成不因查询
  失败而中断。
- 收录但 size / aspect_ratio / resolution 一概不声明（如包装型模型）→ 省略全部尺寸参数，
  信任模型默认。

usage 只按张上报输出与参考图张数，不解析响应里的 ``usage.cost``。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from arcreel_market_core.aspect_size import (
    IMAGE_TIER_SHORT_EDGE,
    aspect_size,
    parse_aspect_ratio,
    resolution_to_short_edge,
)
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import should_retry_submit, submit_post, with_artifact_retry
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageOutputFormatError,
    ImageOutputMissingError,
    download_image_to_path,
    image_to_base64_data_uri,
    per_image_usage,
)
from lib.backends.openai_shared import OPENAI_IMAGE_QUALITY_MAP as _QUALITY_MAP
from lib.backends.providers import PROVIDER_OPENROUTER
from lib.backends.transport_layer import TransportLayer
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.image_utils import convert_image_bytes_to_png
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-image-2"
_MAX_REFERENCE_IMAGES = 16

#: 生成与模型目录查询的请求路径（相对 base_url）。路径覆盖（docs/adr/0101）按这两条默认路径认出请求。
IMAGES_PATH = "/images"
MODELS_PATH = "/images/models"

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: 落盘后能转成 PNG 的输出格式
_RASTER_OUTPUT_FORMATS = frozenset({"png", "jpeg", "jpg", "webp"})

#: 生成耗时可达 90s+，超时须远大于常规请求
_HTTP_TIMEOUT = httpx.Timeout(300.0, connect=15.0)
#: 模型目录查询是轻量元数据请求，超时收紧；失败只影响参数精化，不影响生成
_MODELS_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# 档位 → quality 大小写不敏感（与 OpenAIImageBackend 同口径）：自定义档位词可能写
# "2k"/"4K "，不归一会让 size 解析成功但 quality 静默丢失。自定义 WxH（无档位）→ None。
_QUALITY_MAP_CI = {k.lower(): v for k, v in _QUALITY_MAP.items()}

# 模型目录查询结果缓存在 backend 实例上：实例随供应商配置（base_url、密钥、路径覆盖）构造，配置变更时
# 调用方整体失效 backend 缓存，目录结果随之作废，不会被另一份配置复用。查到的结果（含「目录里没有这个
# 模型」）缓存 1h；查询失败也短暂缓存为「未收录」，免得目录不可用时每次生成都重查一遍、各打一份堆栈。
_MODEL_PARAMS_TTL_SECONDS = 3600.0
_MODEL_PARAMS_FAILURE_TTL_SECONDS = 60.0


def _quality_for(image_size: str | None) -> str | None:
    return _QUALITY_MAP_CI.get(image_size.strip().lower()) if image_size else None


def _enum_values(supported: dict, name: str) -> list[str]:
    """取声明枚举的取值列表（{"type": "enum", "values": [...]}）；声明缺失 / 形态不符 → []。"""
    spec = supported.get(name)
    values = spec.get("values") if isinstance(spec, dict) else None
    return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []


def _range_bound(supported: dict, name: str, bound: str) -> int | None:
    """取声明 range 的一端（{"type": "range", "min": m, "max": n}，``bound`` 为 min / max）；缺失 → None。"""
    spec = supported.get(name)
    if not isinstance(spec, dict):
        return None
    value = spec.get(bound)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _declared_reference_max(supported: dict | None) -> int | None:
    """目录声明的参考图上限：收录了该模型却没声明 ``input_references`` 时为 ``0``（不收参考图）；
    目录不可得、未收录该模型或声明里没写上限时为 ``None``（不知道）。"""
    if supported is None:
        return None
    if "input_references" not in supported:
        return 0
    value = _range_bound(supported, "input_references", "max")
    return None if value is None else max(value, 0)


def _effective_reference_limit(supported: dict | None) -> int:
    """实际按多少张裁剪：模型声明的上限封顶端点的 16 张；声明不出正数上限时取 16。"""
    declared = _declared_reference_max(supported)
    return min(declared, _MAX_REFERENCE_IMAGES) if declared else _MAX_REFERENCE_IMAGES


def _resolution_px(value: str) -> int | None:
    """把 resolution 枚举值换算成短边像素（"512" → 512，"1K" / "1.5K" / "2K" → 1024 / 1536 / 2048）。"""
    text = value.strip().upper()
    scale = 1
    if text.endswith("K"):
        text, scale = text[:-1], 1024
    try:
        px = round(float(text) * scale)
    except ValueError:
        return None
    return px if px > 0 else None


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


def _parse_size(value: str) -> tuple[int, int] | None:
    """把 size 枚举值解析成 (宽, 高)；"auto" 等非 WxH 值 → None。"""
    w, sep, h = value.strip().lower().partition("x")
    if not sep or not w.isdigit() or not h.isdigit() or int(w) <= 0 or int(h) <= 0:
        return None
    return int(w), int(h)


def _pick_size(values: list[str], aspect_ratio: str, short_edge: int) -> str | None:
    """从声明的 WxH 枚举选一档：比例最接近者优先，同比例里取短边「≥ 请求值的最小档」，全档都低于
    请求值取最大档。枚举里没有可解析的 WxH → None。"""
    parsed = [(v, dims) for v in values if (dims := _parse_size(v)) is not None]
    if not parsed:
        return None
    target_w, target_h = parse_aspect_ratio(aspect_ratio)
    target = target_w / target_h
    best_score = min(abs(w / h - target) for _, (w, h) in parsed)
    same_ratio = sorted(
        ((min(w, h), v) for v, (w, h) in parsed if abs(abs(w / h - target) - best_score) < 1e-9),
    )
    for edge, v in same_ratio:
        if edge >= short_edge:
            return v
    return same_ratio[-1][1]


def _resolve_model_params(supported: dict | None, image_size: str | None, aspect_ratio: str) -> dict[str, str]:
    """按声明自适应下发尺寸参数，互斥的两套通道只能选其一（同传 size+resolution 必 400）。"""
    quality = _quality_for(image_size)
    if supported is None:
        # 模型未收录 / 目录不可得：只发 OpenAI 同款 size，不带任何没有声明依据的参数（如 quality）
        short = resolution_to_short_edge(image_size, tier_map=IMAGE_TIER_SHORT_EDGE)
        w, h = aspect_size(aspect_ratio, short, round_to=16)
        return {"size": f"{w}x{h}"}

    size_values = _enum_values(supported, "size")
    resolution_values = _enum_values(supported, "resolution")
    ratio_values = _enum_values(supported, "aspect_ratio")
    quality_values = _enum_values(supported, "quality")

    params: dict[str, str] = {}
    ratio = _pick_aspect_ratio(ratio_values, aspect_ratio) if ratio_values else None
    # size 由「实际下发的比例」算：ratio 选中枚举档时按该档，选不到才退回请求比例——
    # 否则 size 按 2:3 算、aspect_ratio 却写 9:16，两个参数自相矛盾，模型行为不可预期。
    effective_ratio = ratio or aspect_ratio
    short = resolution_to_short_edge(image_size, tier_map=IMAGE_TIER_SHORT_EDGE)
    if resolution_values:
        # 声明 resolution 即走这一支，即便同时声明了 size：两者互斥，不传 size。ratio / resolution
        # 拿不到（档位映射不上、极端比例超出枚举）时各自省略——比传冲突参数被 400 拒掉好。
        resolution = _pick_resolution(resolution_values, short)
        if resolution:
            params["resolution"] = resolution
        if ratio:
            params["aspect_ratio"] = ratio
    elif size_values or ratio:
        # 声明 size 或只声明 aspect_ratio：WxH 撑清晰度，ratio 锁比例。声明了 size 枚举时只发枚举里的值：
        # 有 WxH 就选最接近的一档，只有 auto 就发 auto；未声明 size 才按 OpenAI 同款合成精确 WxH。
        size = _pick_size(size_values, effective_ratio, short)
        if size is None and size_values:
            size = next((v for v in size_values if v.strip().lower() == "auto"), None)
        if size is None and not size_values:
            w, h = aspect_size(effective_ratio, short, round_to=16)
            size = f"{w}x{h}"
        if size is not None:
            params["size"] = size
        if ratio:
            params["aspect_ratio"] = ratio
    # 收录但三类都不声明：省略全部尺寸参数，信任模型默认
    if quality and quality_values and quality.lower() in {v.lower() for v in quality_values}:
        params["quality"] = quality
    return params


class OpenRouterImageBackend:
    """OpenRouter 统一图片后端：T2I 与 I2I 同 hit ``POST {base_url}/images``。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._api_key = api_key or ""
        self._model = model or DEFAULT_MODEL
        # OpenRouter 的 base_url 已含 /api/v1，不做 ensure_openai_base_url 的 /v1 追加
        self._base_url = (str(base_url).strip().rstrip("/") if base_url else "") or DEFAULT_BASE_URL
        # 路径覆盖（docs/adr/0101）在 transport 层按请求路径改写，生成与目录查询两条请求都经过它
        self._transport_layer = transport_layer
        # (clock 到期时刻, supported_parameters)；None = 还没查过。并发的首次查询经锁串行化，
        # 免得晚到的失败结果覆盖先到的成功结果。
        self._clock = clock
        self._model_params: tuple[float, dict | None] | None = None
        self._model_params_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return PROVIDER_OPENROUTER

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    async def reference_image_limit(self) -> int:
        """这个模型单请求可携带的参考图上限：目录声明的 ``input_references`` 上限，封顶 16。

        目录不可得、目录里没有这个模型，或模型没声明可用的参考图上限时取 16：没声明参考图的模型由
        ``generate`` 按不支持图生图报错（docs/adr/0015），这里不返回 ``0``——``0`` 的意思是「不裁剪」。
        """
        return _effective_reference_limit(await self._fetch_supported_params())

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        supported = await self._fetch_supported_params()
        refs = self._fit_references(request.reference_images, supported)

        input_references = await asyncio.to_thread(self._encode_references, refs)
        payload: dict = {"model": self._model, "prompt": request.prompt}
        # 目录收录了该模型却没声明的参数（n / seed）不下发：未声明的参数会被拒；未收录时照常带上
        if supported is None or "n" in supported:
            payload["n"] = 1
        payload.update(_resolve_model_params(supported, request.image_size, request.aspect_ratio))
        if request.seed is not None and (supported is None or "seed" in supported):
            payload["seed"] = request.seed
        # 产物路径固定是 .png：模型声明可选 png 时直接要 png，省去落盘后的转码；声明的格式里没有
        # 位图（如只出 SVG）时，产物注定转不成 PNG，在提交这次计费请求之前就拒绝
        output_formats = [v.lower() for v in _enum_values(supported, "output_format")] if supported else []
        if "png" in output_formats:
            payload["output_format"] = "png"
        elif output_formats and not _RASTER_OUTPUT_FORMATS.intersection(output_formats):
            raise ImageOutputFormatError(model=self._model, media_type="/".join(output_formats))
        if input_references:
            payload["input_references"] = input_references

        body = await self._submit(payload)
        await self._persist_image(body, request.output_path)
        logger.info("OpenRouter 图片生成完成: %s", request.output_path)
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_OPENROUTER,
            model=self._model,
            # 用量契约（docs/adr/0100）：按张上报输出与实际下发的参考图张数
            usage=per_image_usage(output_images=1, input_images=len(input_references)),
        )

    def _fit_references(self, refs: list, supported: dict | None) -> list:
        """按目录声明核对参考图：模型不收参考图、必须带参考图或张数超出上限时报错，不截断。

        编排层已按 :meth:`reference_image_limit` 裁剪参考图并在提示词里编号「图N」。这里仍超限，说明两次
        读到的目录不一致（如编排时目录查询失败按 16 张编号，提交前又查到更小的上限）；此时截断会让提示词
        指认没有发出的图、照常计费，所以不提交，交给重新生成按最新上限重新裁剪编号。
        """
        if supported is not None:
            if refs and _declared_reference_max(supported) == 0:
                # 目录收录了该模型，却没声明参考图或声明上限为 0：没有供应商接受参考图
                raise ImageCapabilityError(
                    "image_endpoint_mismatch_no_i2i",
                    model=self._model,
                    detail=f"model {self._model} declares no input_references",
                )
            declared_min = _range_bound(supported, "input_references", "min") or 0
            if not refs and declared_min > 0:
                raise ImageCapabilityError("image_endpoint_mismatch_no_t2i", model=self._model)
        limit = _effective_reference_limit(supported)
        if len(refs) > limit:
            raise ImageCapabilityError(
                "image_reference_images_exceeded", model=self._model, limit=limit, count=len(refs)
            )
        return refs

    async def _fetch_supported_params(self) -> dict | None:
        """查该 model 的 supported_parameters（缓存在实例上）；目录不可得或不含该模型时返回 None。

        网络异常与非 2xx 只影响参数精化：生成按 size 兜底照常走，不因查询失败而中断。
        """
        cached = self._model_params
        if cached and self._clock() < cached[0]:
            return cached[1]
        async with self._model_params_lock:
            cached = self._model_params
            if cached and self._clock() < cached[0]:
                return cached[1]
            supported, ttl = await self._query_supported_params()
            self._model_params = (self._clock() + ttl, supported)
            return supported

    async def _query_supported_params(self) -> tuple[dict | None, float]:
        """查一次模型目录，返回 (supported_parameters, 结果的缓存时长)。"""
        try:
            async with artifact_http_client(timeout=_MODELS_TIMEOUT, transport_layer=self._transport_layer) as client:
                resp = await client.get(
                    f"{self._base_url}{MODELS_PATH}",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                resp.raise_for_status()
                items = (resp.json() or {}).get("data") or []
        except Exception:
            logger.warning(
                "OpenRouter 模型目录查询失败，%ds 内按未收录处理: model=%s",
                int(_MODEL_PARAMS_FAILURE_TTL_SECONDS),
                self._model,
                exc_info=True,
            )
            return None, _MODEL_PARAMS_FAILURE_TTL_SECONDS
        for item in items:
            if isinstance(item, dict) and item.get("id") == self._model:
                raw = item.get("supported_parameters")
                return (raw if isinstance(raw, dict) else None), _MODEL_PARAMS_TTL_SECONDS
        return None, _MODEL_PARAMS_TTL_SECONDS

    def _encode_references(self, refs) -> list[dict]:
        """参考图 data URI 编码；任一张读不出即 fail-loud。

        prompt 按数组序位用「图N」指认参考图，跳过缺失的图会让后续参考图前移、序位错指，
        生成照常计费。因此收集全部读不出的参考图后统一报 image_reference_images_unreadable。
        """
        encoded: list[dict] = []
        unreadable: list[str] = []
        # names 进多语言错误模板：空路径无文件名时用序号 #N 标识第几张，避免占位漏进非中文报错。
        for idx, ref in enumerate(refs, start=1):
            path = Path(ref.path) if ref.path else None
            if path is None or not path.is_file():
                unreadable.append(path.name if (path and path.name) else f"#{idx}")
                continue
            try:
                encoded.append({"type": "image_url", "image_url": {"url": image_to_base64_data_uri(path)}})
            except OSError as exc:
                logger.warning("OpenRouter 参考图读取失败: %s (%s)", path, exc)
                unreadable.append(path.name or f"#{idx}")
        if unreadable:
            raise ImageCapabilityError(
                "image_reference_images_unreadable", model=self._model, names=", ".join(unreadable)
            )
        return encoded

    @with_retry_async(retry_if=should_retry_submit)
    async def _submit(self, payload: dict) -> Any:
        """单步图像生成 POST（非幂等「建图 + 计费」），返回解析后的响应体。

        重试范围严格限定在本方法内、不含落盘——落盘失败不会触发整流程重试导致重复建图与
        重复计费。submit_post 把歧义传输错误转 AmbiguousSubmitError 终态失败避免重复计费；
        >=400 抛 HTTPStatusError 交 should_retry_submit 按状态码分流。
        """
        log_view = {**payload, "prompt": payload.get("prompt", "")[:80]}
        logger.info("调用 %s 图片 API kwargs=%s", self.name, format_kwargs_for_log(log_view))
        async with artifact_http_client(timeout=_HTTP_TIMEOUT, transport_layer=self._transport_layer) as client:
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{IMAGES_PATH}",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                ),
                provider=PROVIDER_OPENROUTER,
            )
            return resp.json()

    async def _persist_image(self, body: Any, output_path: Path) -> None:
        items = body.get("data") if isinstance(body, dict) else None
        first = items[0] if isinstance(items, list) and items else None
        if not isinstance(first, dict) or not (first.get("b64_json") or first.get("url")):
            # 成功响应却没带回图片：通常是内容安全过滤命中或上游异常
            raise ImageOutputMissingError(model=self._model)
        media_type = first.get("media_type") if isinstance(first.get("media_type"), str) else None
        b64 = first.get("b64_json")
        # 供应商已返回、本次已计费：落盘与转 PNG 都在不可中断段内跑完，取消不能把付费产物丢掉
        # （docs/standards/backend.md「供应商返回之后的落账段不可中断」）。
        if b64:

            def _decode_and_save() -> None:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(base64.b64decode(b64))
                self._ensure_png(output_path, media_type)

            await run_noninterruptible_sync(_decode_and_save)
            return
        # 取件失败只在下载层重试，不回退到重跑已计费的出图请求
        await with_artifact_retry(lambda: download_image_to_path(first["url"], output_path), label="OpenRouter 图像")
        await run_noninterruptible_sync(self._ensure_png, output_path, media_type)

    def _ensure_png(self, output_path: Path, media_type: str | None) -> None:
        """产物路径固定是 .png：JPEG / WebP 等位图转成 PNG 落盘；转不了（如 SVG）即报错，不留错标格式的产物。"""
        content = output_path.read_bytes()
        if content.startswith(_PNG_SIGNATURE):
            return
        try:
            png = convert_image_bytes_to_png(content)
        except ValueError:
            # 转不成位图就不留下这份内容：格式与 .png 扩展名不符，下游会按 PNG 误读
            output_path.unlink(missing_ok=True)
            raise ImageOutputFormatError(model=self._model, media_type=media_type or "unknown") from None
        logger.info("OpenRouter 返回 %s，已转为 PNG: %s", media_type or "非 PNG 图片", output_path)
        output_path.write_bytes(png)
