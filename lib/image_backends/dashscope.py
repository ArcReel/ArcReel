"""DashScopeImageBackend — 阿里百炼 Qwen-Image / 万相图像生成后端（同步）。

走原生 multimodal-generation/generation 同步端点，T2I 与 I2I 共用同一请求体，
只差 content 是否含 image 元素。覆盖 qwen-image-2.0 融合系列、qwen-image-edit
编辑系列与 wan2.7-image 系列。schema 依据 docs/dashscope-docs/ 一手核实快照。
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.aspect_size import IMAGE_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from lib.dashscope_shared import (
    dashscope_headers,
    dashscope_native_base_url,
    extract_image_url,
    image_to_data_uri,
    resolve_dashscope_api_key,
    safe_body_for_log,
)
from lib.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    download_image_to_path,
)
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_DASHSCOPE
from lib.retry import with_retry_async
from lib.video_backends.base import should_retry_submit, submit_post

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen-image-2.0"

_IMAGE_ENDPOINT = "/services/aigc/multimodal-generation/generation"

# 编辑系列仅图生图（无文生图能力）；子串覆盖 qwen-image-edit / -edit-plus / -edit-max
_I2I_ONLY_MARKERS = ("qwen-image-edit",)

# 参考图上限：qwen 系 1~3 张、wan 系 0~9 张（docs 确权）
_QWEN_REF_LIMIT = 3
_WAN_REF_LIMIT = 9

# 缺分辨率时各族默认短边。比例永远来自 aspect_ratio，不再用带比例偏差的静态像素表，改由
# lib.aspect_size 在各族像素约束内算精确比例（见 docs/adr/0011）：
#   - qwen 融合系列 native 默认 2048²；wan 默认 2K 档；编辑系列由 max_long_edge 自然收口。
_DEFAULT_WAN_BUDGET = "2K"
_DEFAULT_SHORT_FUSION = 2048
_DEFAULT_SHORT_WAN = 1440
_DEFAULT_SHORT_EDIT = 2048

# 标准档总像素预算（非 pro / 非文生图上限）= 2048×2048；超出须 wan2.7-image-pro 文生图（4K=4096×4096）
_STANDARD_PIXEL_BUDGET = 2048 * 2048
_FOURK_PIXEL_BUDGET = 4096 * 4096
# 编辑系列（qwen-image-edit-plus / -max）宽高均 ∈ [512, 2048]
_EDIT_MAX_LONG_EDGE = 2048
# DashScope 生成系列比例支持区间 1:8 ~ 8:1
_DASHSCOPE_MAX_RATIO = 8.0
# 编辑系列因宽高均 ∈ [512, 2048]，可表达的比例上限为 2048/512 = 4:1（比生成系列更窄）
_EDIT_MAX_RATIO = 4.0


class DashScopeImageBackend:
    """阿里百炼图像后端（同步 multimodal 端点）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 120.0,
    ) -> None:
        self._api_key = resolve_dashscope_api_key(api_key)
        self._base_url = dashscope_native_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        mid = self._model.lower()
        self._is_wan = mid.startswith("wan")
        self._is_edit = "qwen-image-edit" in mid
        self._capabilities = self._resolve_caps(self._model)

    @staticmethod
    def _resolve_caps(model: str) -> set[ImageCapability]:
        mid = model.lower()
        if any(marker in mid for marker in _I2I_ONLY_MARKERS):
            return {ImageCapability.IMAGE_TO_IMAGE}
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    @staticmethod
    def _exceeds_standard_budget(size: str) -> bool:
        """size 是否超出标准档总像素预算（2048×2048）。

        docs 口径：超出 2048×2048 的输出仅 wan2.7-image-pro 文生图支持（4K 档=4096×4096）。
        档位 "1K"/"2K" 在预算内、"4K" 超预算；像素值按"总像素 > 预算"判定，避免只认 "4K"
        字面而让 "4096*4096" / "3000*3000" 等数字写法绕过门控（这是按比例算总像素，
        故 "4096*512" 这类窄幅合法尺寸不会被误拒）。
        """
        normalized = size.strip().upper()
        if normalized in ("1K", "2K"):
            return False
        if normalized == "4K":
            return True
        for sep in ("*", "X", "×"):
            if sep in normalized:
                parts = normalized.split(sep, 1)
                try:
                    return int(parts[0]) * int(parts[1]) > _STANDARD_PIXEL_BUDGET
                except ValueError:
                    return False
        return False

    @property
    def name(self) -> str:
        return PROVIDER_DASHSCOPE

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    @property
    def _ref_limit(self) -> int:
        return _WAN_REF_LIMIT if self._is_wan else _QWEN_REF_LIMIT

    @with_retry_async(retry_if=should_retry_submit)
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        has_refs = bool(request.reference_images)
        if has_refs and ImageCapability.IMAGE_TO_IMAGE not in self._capabilities:
            raise ImageCapabilityError("image_endpoint_mismatch_no_i2i", model=self._model)
        if not has_refs and ImageCapability.TEXT_TO_IMAGE not in self._capabilities:
            raise ImageCapabilityError("image_endpoint_mismatch_no_t2i", model=self._model)

        size = self._resolve_size(request, has_refs)
        content = self._build_content(request, has_refs)

        parameters: dict = {
            "n": 1,
            "watermark": False,
            # ArcReel 剧本 prompt 已是 LLM 精炼描述，关闭智能改写保留原意
            "prompt_extend": False,
            "size": size,
        }
        if request.seed is not None:
            parameters["seed"] = request.seed

        payload = {
            "model": self._model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": parameters,
        }

        logger.info(
            "调用 %s 图片 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(safe_body_for_log(payload)),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            # 同步图像生成是非幂等的「建图 + 计费」POST：submit_post 把歧义传输错误（请求可能已送达
            # 但响应在途丢失）转 AmbiguousSubmitError 终态失败，避免自动重试重复计费；>=400 落 body
            # 日志 + 抛 HTTPStatusError（保留 status_code 供咽喉层识别 413 降档），交 should_retry_submit
            # 按状态码分流——4xx fail-fast、5xx/429 重试。
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{_IMAGE_ENDPOINT}",
                    json=payload,
                    headers=dashscope_headers(self._api_key),
                ),
                provider=PROVIDER_DASHSCOPE,
            )
            data = resp.json()

        url = extract_image_url(data)
        await download_image_to_path(url, request.output_path)
        logger.info("DashScope 图片生成完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_DASHSCOPE,
            model=self._model,
            image_uri=url,
        )

    def _resolve_size(self, request: ImageGenerationRequest, has_refs: bool) -> str:
        """按「比例优先、清晰度其次」算出 宽*高。

        比例永远来自 aspect_ratio；image_size（档位词 / 自定义 宽*高 / None）只决定清晰度短边，
        自定义值剥离其自带比例（取 min）。各族按自身像素约束精确收口：
          - wan 系：方式二像素值（接受任意、对齐 16），总像素 ≤ 标准 2048² / 4K 4096²；
            绝不下传档位词（wan 文生图会被强制输出正方形、丢比例）。
          - qwen-image-2.0 融合系列：总像素 ∈ [512², 2048²]，自由设宽高。
          - qwen-image-edit-plus / -max 编辑系列：宽高均 ∈ [512, 2048]。
        经典系列（qwen-image / -plus / -max）仅 5 固定档、不接受任意像素，未注册预设、不推荐，
        不在本机制覆盖内（见 docs/adr/0011）。
        """
        explicit = (request.image_size or "").strip()
        aspect = request.aspect_ratio

        if self._is_wan:
            # 超 2048×2048 预算（4K 档或大像素值）仅 wan2.7-image-pro 文生图支持，
            # 非 pro 不支持、pro 的 I2I 不支持 —— 先门控（档位词与像素值统一判定）
            budget_word = explicit or _DEFAULT_WAN_BUDGET
            exceeds = self._exceeds_standard_budget(budget_word)
            if exceeds and ("pro" not in self._model.lower() or has_refs):
                raise ImageCapabilityError("image_dashscope_4k_t2i_only", model=self._model)
            max_total = _FOURK_PIXEL_BUDGET if exceeds else _STANDARD_PIXEL_BUDGET
            short = resolution_to_short_edge(
                explicit or None, tier_map=IMAGE_TIER_SHORT_EDGE, default_short=_DEFAULT_SHORT_WAN
            )
            w, h = aspect_size(aspect, short, round_to=16, max_total_pixels=max_total, max_ratio=_DASHSCOPE_MAX_RATIO)
            return f"{w}*{h}"

        if self._is_edit:
            short = resolution_to_short_edge(
                explicit or None, tier_map=IMAGE_TIER_SHORT_EDGE, default_short=_DEFAULT_SHORT_EDIT
            )
            w, h = aspect_size(aspect, short, round_to=16, max_long_edge=_EDIT_MAX_LONG_EDGE, max_ratio=_EDIT_MAX_RATIO)
            return f"{w}*{h}"

        # qwen-image-2.0 融合系列
        short = resolution_to_short_edge(
            explicit or None, tier_map=IMAGE_TIER_SHORT_EDGE, default_short=_DEFAULT_SHORT_FUSION
        )
        w, h = aspect_size(
            aspect, short, round_to=16, max_total_pixels=_STANDARD_PIXEL_BUDGET, max_ratio=_DASHSCOPE_MAX_RATIO
        )
        return f"{w}*{h}"

    def _build_content(self, request: ImageGenerationRequest, has_refs: bool) -> list[dict]:
        content: list[dict] = []
        if has_refs:
            # fail-loud：任一声明的参考图缺失（含目录/空串解析出的 "."）或读取失败（权限/并发删除
            # → OSError）即中止生成并报错列出文件名，让用户感知到有图未被使用，而非静默丢弃、用子集
            # 生成出错误结果还照常计费。
            data_uris: list[str] = []
            unreadable: list[str] = []
            # names 进多语言错误模板（en/vi 也渲染），分隔符与占位用 locale 中性形式：
            # 空路径无文件名可显示，用序号 #N 标识第几张参考图，避免中文占位漏进非中文报错。
            for idx, ref in enumerate(request.reference_images, start=1):
                path = Path(ref.path) if ref.path else None
                if path is None or not path.is_file():
                    unreadable.append(path.name if path else f"#{idx}")
                    continue
                try:
                    data_uris.append(image_to_data_uri(path))
                except OSError as exc:
                    logger.warning("DashScope 参考图读取失败: %s (%s)", path, exc)
                    unreadable.append(path.name)
            if unreadable:
                raise ImageCapabilityError(
                    "image_reference_images_unreadable", model=self._model, names=", ".join(unreadable)
                )
            if len(data_uris) > self._ref_limit:
                logger.warning(
                    "DashScope 参考图数量 %d 超过 model=%s 上限 %d，截断",
                    len(data_uris),
                    self._model,
                    self._ref_limit,
                )
                data_uris = data_uris[: self._ref_limit]
            content.extend({"image": uri} for uri in data_uris)
        content.append({"text": request.prompt})
        return content
