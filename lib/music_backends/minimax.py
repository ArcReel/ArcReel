"""MiniMaxMusicBackend — MiniMax（海螺）music-3.0 音乐生成后端（单步同步）。

走 OpenAI 兼容 base 上的原生 /music_generation 同步端点：单次 POST 直接返回音频 URL
（24h 有效）或 hex 编码音频，立即落地为本地资产。prompt + 可选 lyrics 驱动带唱词生成，
is_instrumental 生成纯乐器伴奏；翻唱（music-cover）经 audio_url / audio_base64 提供源音频。
响应无 task_id / 查询端点：data.status=2 即完成、data.audio 承载结果，base_resp.status_code
非零为业务失败。国内站默认连 api.minimaxi.com，海外经 base_url 覆盖切到 api.minimax.io。
"""

from __future__ import annotations

import asyncio
import binascii
import logging
from pathlib import Path

import httpx

from lib.logging_utils import format_kwargs_for_log
from lib.minimax_shared import (
    extract_music_audio,
    minimax_headers,
    minimax_music_base_url,
    minimax_music_failure_reason,
    minimax_music_status,
    resolve_minimax_api_key,
    safe_body_for_log,
)
from lib.music_backends.base import (
    MusicCapability,
    MusicCapabilityError,
    MusicGenerationRequest,
    MusicGenerationResult,
    download_audio_to_path,
)
from lib.providers import PROVIDER_MINIMAX
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
from lib.video_backends.base import should_retry_download, should_retry_submit, submit_post

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "music-3.0"

_MUSIC_ENDPOINT = "/music_generation"

# music_generation 响应 data.status：1=生成中，2=已完成。单步端点无查询接口，完成即取 data.audio。
_STATUS_COMPLETED = 2

# 输出格式：url（下载 24h 有效链接）/ hex（响应内嵌十六进制音频，解码写盘）。
_OUTPUT_URL = "url"
_OUTPUT_HEX = "hex"
_SUPPORTED_OUTPUT_FORMATS = frozenset({_OUTPUT_URL, _OUTPUT_HEX})


class MiniMaxMusicBackend:
    """MiniMax 音乐后端（单步同步 music_generation 端点）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 120.0,
    ) -> None:
        self._api_key = resolve_minimax_api_key(api_key)
        self._base_url = minimax_music_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return PROVIDER_MINIMAX

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[MusicCapability]:
        # music_generation 端点同时承载文生音乐与翻唱（music-cover 走同一 POST，源音频经
        # audio_url / audio_base64 提供）。
        return {MusicCapability.MUSIC_GENERATION, MusicCapability.MUSIC_COVER}

    async def generate(self, request: MusicGenerationRequest) -> MusicGenerationResult:
        # 编排层不带重试：把非幂等的「生成 + 计费」submit 与幂等的结果下载隔离到各自的重试
        # 范围（_submit / _download_result），避免下载失败回退到重跑生成 POST 造成重复计费。
        output_format = request.output_format or _OUTPUT_URL
        if output_format not in _SUPPORTED_OUTPUT_FORMATS:
            raise MusicCapabilityError(
                "music_output_format_unsupported", model=self._model, output_format=output_format
            )

        payload = self._build_payload(request, output_format)
        data = await self._submit(payload)
        audio_uri = await self._persist_audio(data, request.output_path, output_format)
        logger.info("MiniMax 音乐生成完成: %s", request.output_path)

        return MusicGenerationResult(
            music_path=request.output_path,
            provider=PROVIDER_MINIMAX,
            model=self._model,
            audio_uri=audio_uri,
        )

    def _build_payload(self, request: MusicGenerationRequest, output_format: str) -> dict:
        """按官方 request_fields 组装请求体；未提供的可选字段一律省略（用供应商默认）。

        required_fields 仅 model；prompt 为空亦不写入（翻唱由源音频驱动）。翻唱源音频二选一，
        同时提供时 audio_url 优先。
        """
        payload: dict = {"model": self._model, "output_format": output_format}
        if request.prompt:
            payload["prompt"] = request.prompt
        if request.lyrics is not None:
            payload["lyrics"] = request.lyrics
        if request.is_instrumental:
            payload["is_instrumental"] = True
        if request.lyrics_optimizer:
            payload["lyrics_optimizer"] = True
        if request.audio_setting:
            payload["audio_setting"] = request.audio_setting
        # 翻唱：二选一源音频（同时提供以 audio_url 优先），可选 cover_feature_id。
        if request.audio_url:
            payload["audio_url"] = request.audio_url
        elif request.audio_base64:
            payload["audio_base64"] = request.audio_base64
        if request.cover_feature_id is not None:
            payload["cover_feature_id"] = request.cover_feature_id
        return payload

    @with_retry_async(retry_if=should_retry_submit)
    async def _submit(self, payload: dict) -> dict:
        """单步音乐生成 POST（非幂等「生成 + 计费」），返回解析后的响应体。

        重试范围严格限定在本方法内、不含下载——下载失败不会触发整流程重试导致重复生成与
        重复计费。submit_post 把歧义传输错误转 AmbiguousSubmitError 终态失败避免重复计费；
        >=400 落 body 日志 + 抛 HTTPStatusError，交 should_retry_submit 按状态码分流。日志经
        safe_body_for_log 白名单化：仅 model + prompt 长度进日志，lyrics / audio_base64 / URL
        一律不展开。
        """
        logger.info(
            "调用 %s 音乐 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(safe_body_for_log(payload)),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{_MUSIC_ENDPOINT}",
                    json=payload,
                    headers=minimax_headers(self._api_key),
                ),
                provider=PROVIDER_MINIMAX,
            )
            return resp.json()

    async def _persist_audio(self, data: dict, output_path: Path, output_format: str) -> str | None:
        """把 music_generation 响应落地为本地文件，返回远端 URL（hex 路径返回 None）。

        先查 base_resp 业务错误（200 + 非零 status_code），再确认 data.status 为已完成（单步
        端点无查询接口，非完成态无法轮询，直接报错），最后按 output_format 取 data.audio：
        url 立即下载（24h 失效前落地），hex 解码十六进制写盘。
        """
        reason = minimax_music_failure_reason(data)
        if reason:
            raise RuntimeError(reason)

        status = minimax_music_status(data)
        if status is not None and status != _STATUS_COMPLETED:
            raise RuntimeError(f"MiniMax 音乐生成未完成 data.status={status}")

        audio = extract_music_audio(data)
        if not audio:
            # data.audio 缺失时可安全记全响应体（此分支无音频负载）；不嵌进异常消息避免
            # body 里的 "503"/"timeout" 子串被字符串兜底误判为可重试。
            logger.error("MiniMax 音乐响应缺少 data.audio: %s", data)
            raise RuntimeError("MiniMax 音乐响应缺少 data.audio")

        if output_format == _OUTPUT_URL:
            await self._download_result(audio, output_path)
            return audio

        await _write_hex_audio(audio, output_path)
        return None

    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=should_retry_download,
    )
    async def _download_result(self, url: str, output_path: Path) -> None:
        """下载已签发的结果音频 URL（幂等 GET），独立的下载重试范围。

        瞬态失败在本层重试，绝不回退到重跑非幂等的生成 POST；4xx（URL 失效等确定性错误）
        快速失败。下载比生成更宽容（失败不浪费生成额度），故用 DOWNLOAD_* 重试配置。
        """
        await download_audio_to_path(url, output_path)


async def _write_hex_audio(hex_audio: str, output_path: Path) -> None:
    """解码 hex 编码音频并写盘（解码 + 写盘 offload 到线程，避免事件循环内做 CPU 密集解码）。"""

    def _decode_and_save() -> None:
        audio_bytes = binascii.unhexlify(hex_audio)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio_bytes)

    await asyncio.to_thread(_decode_and_save)
