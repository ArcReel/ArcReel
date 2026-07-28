"""KlingImageBackend — 可灵 Kling 图像生成后端（JWT 直连 / Bearer 中转双模式，异步轮询）。

走可灵原生图像端点：submit ``POST /v1/images/generations`` 取 ``data.task_id`` →
轮询 ``GET /v1/images/generations/{task_id}`` 至 ``task_status=succeed`` 取
``task_result.images[0].url``（24h 有效）→ 失效前立即下载本地。

鉴权 / base_url 装配 / submit-poll 骨架由 ``KlingBackendBase`` 共享（与 ``KlingVideoBackend`` 同源），
本类只填图像侧差异：端点路径、payload 构建、参考图编码、静态 capability 与 ``api_model_name`` 解耦。

双模式（对齐 ``KlingVideoBackend`` 的 ``auth_mode`` 先例）：
- ``auth_mode="jwt"``（内置 provider）：接 access_key + secret_key，走 ``KlingJWTManager``，
  每次 HTTP 调用前检查过期、距过期 <60s 按需重签——异步渲染可能超单 token 寿命。
- ``auth_mode="bearer"``（自定义 endpoint）：接静态 api_key + base_url，旁路 JWT 管理器。

注册图像模型：
- ``kling-image-o1``（默认）：文生图 / 图生图 / 1-10 图参考（跨图角色一致性），按 ``image`` 数组下传。
- ``kling-v3-omni-image``：文生图 / 图生图 / 组图生成（registry 别名键，API 模型名 ``kling-v3-omni``）。

发给 API 的模型名取 ``api_model_name``（registry 解耦键名与 API 名，供两栖模型共用 API 名），缺省回退
到 registry 键名——不硬发键名，否则别名键会把 ``kling-v3-omni-image`` 误当 API 模型名。

resolution（1K/2K/4K）由 registry 声明驱动 UI 下拉与计费，不下传请求体：可灵图像官方关键参数
（model_name/prompt/image/aspect_ratio/n 等）未含 resolution 字段，与 KlingVideoBackend 一致只发
已核实参数，避免猜测外部 API 结构。
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    ReferenceImage,
    download_image_to_path,
)
from lib.kling_backend_base import KlingBackendBase
from lib.kling_shared import (
    extract_kling_image_urls,
    image_to_base64,
)
from lib.providers import PROVIDER_KLING

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "kling-image-o1"

_IMAGE_ENDPOINT = "images/generations"
_POLL_INTERVAL_SECONDS = 5.0
_POLL_MAX_WAIT_SECONDS = 600.0

# 各图像模型参考图上限（多图主体，按 registry 键名）：o1 / v3-omni 均支持多图主体，官方 o1 上限 10 张。
_DEFAULT_REF_LIMIT = 10
_MODEL_REF_LIMITS: dict[str, int] = {
    "kling-image-o1": 10,
    "kling-v3-omni-image": 10,
}


class KlingImageBackend(KlingBackendBase):
    """可灵 Kling 图像后端（异步轮询，JWT / Bearer 双模式）。"""

    _media_label = "图像"

    def __init__(
        self,
        *,
        auth_mode: str = "jwt",
        access_key: str | None = None,
        secret_key: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        api_model_name: str | None = None,
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
        # 发给可灵 API 的模型名：调用方按 registry 解耦后传入；缺省回退 registry 键名（普通模型键名即 API 名）。
        self._api_model_name = api_model_name or self._model
        # o1 / v3-omni 均支持文生图 + 图生图（多图主体）。
        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    @property
    def _ref_limit(self) -> int:
        return _MODEL_REF_LIMITS.get(self._model, _DEFAULT_REF_LIMIT)

    # ── request building ────────────────────────────────────────────────

    def _build_payload(self, request: ImageGenerationRequest) -> dict:
        """构建图像请求体。无参考图 → 文生图；有参考图 → 图生图（image 数组）。"""
        payload: dict = {
            "model_name": self._api_model_name,
            "prompt": request.prompt,
            "aspect_ratio": request.aspect_ratio,
            "n": 1,
        }
        images = self._encode_references(request.reference_images)
        if images:
            payload["image"] = images
        return payload

    def _encode_references(self, reference_images: list[ReferenceImage]) -> list[str]:
        """参考图 → 纯 base64 列表（无 data URI 前缀）；超上限截断，缺失/不可读 fail-loud。"""
        if not reference_images:
            return []
        encoded: list[str] = []
        unreadable: list[str] = []
        # names 进多语言错误模板：空路径无文件名时用序号 #N 标识第几张，避免占位漏进非中文报错。
        for idx, ref in enumerate(reference_images, start=1):
            path = Path(ref.path) if ref.path else None
            if path is None or not path.is_file():
                # path.name 可能为空（如 "." / "/" 解析出空文件名）：回退序号 #N，避免空 token 漏进报错。
                unreadable.append(path.name if (path and path.name) else f"#{idx}")
                continue
            try:
                encoded.append(image_to_base64(path))
            except OSError as exc:
                logger.warning("Kling 参考图读取失败: %s (%s)", path, exc)
                unreadable.append(path.name)
        if unreadable:
            # fail-loud：声明的参考图缺失即中止，不静默用子集生成出错误结果还照常计费。
            raise ImageCapabilityError(
                "image_reference_images_unreadable", model=self._model, names=", ".join(unreadable)
            )
        if len(encoded) > self._ref_limit:
            logger.warning(
                "Kling 参考图数量 %d 超过 model=%s 上限 %d，截断",
                len(encoded),
                self._model,
                self._ref_limit,
            )
            encoded = encoded[: self._ref_limit]
        return encoded

    @staticmethod
    def _safe_log_view(payload: dict) -> dict:
        """预脱敏标量视图，直接喂 logger（避开 format_kwargs_for_log sink）。

        base64 参考图 / prompt 一律不展开：仅记是否存在 + 数量 + prompt 长度。
        """
        prompt = payload.get("prompt")
        images = payload.get("image")
        return {
            "endpoint": _IMAGE_ENDPOINT,
            "model_name": payload.get("model_name"),
            "aspect_ratio": payload.get("aspect_ratio"),
            "n": payload.get("n"),
            "reference_count": len(images) if isinstance(images, list) else 0,
            "prompt_len": len(prompt) if isinstance(prompt, str) else 0,
        }

    # ── generate ────────────────────────────────────────────────────────

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        payload = self._build_payload(request)
        logger.info("调用 Kling 图像 API payload=%s", self._safe_log_view(payload))
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            task_id = await self._submit_task(client, _IMAGE_ENDPOINT, payload)
            logger.info("Kling 图像任务已创建: task_id=%s model=%s", task_id, self._model)

            final = await self._poll_until_terminal(
                lambda: self._poll_query(client, f"{_IMAGE_ENDPOINT}/{task_id}"),
                poll_interval=_POLL_INTERVAL_SECONDS,
                max_wait=_POLL_MAX_WAIT_SECONDS,
            )
            # 24h 有效的 image_url 必须在失效前落地：取首张转存本地（组图按张产出，单输出取 [0]）。
            download_url = extract_kling_image_urls(final)[0]

        await download_image_to_path(download_url, request.output_path)
        logger.info("Kling 图像下载完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_KLING,
            model=self._model,
            image_uri=download_url,
            seed=request.seed,
        )
