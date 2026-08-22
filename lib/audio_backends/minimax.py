"""MiniMaxAudioBackend — MiniMax (海螺) speech synthesis backend (synchronous /v1/t2a_v2).

Native text-to-audio endpoint: a single POST returns a download URL (24h valid) when
``output_format="url"`` is requested, then an HTTP GET downloads the audio bytes to
disk. Billing is per character (``extra_info.usage_characters``), mirrored from the
synthesis request length by the AudioSynthesisResult. Schema per the official MiniMax
speech-t2a-http API reference: required fields ``model`` / ``text``; voice carried in
``voice_setting.voice_id``; audio container chosen via ``audio_setting.format``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.audio_backends.base import (
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
)
from lib.minimax_shared import (
    minimax_headers,
    minimax_text_base_url,
    resolve_minimax_api_key,
)
from lib.providers import PROVIDER_MINIMAX
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
from lib.video_backends.base import should_retry_download, should_retry_submit, submit_post

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "speech-2.8-hd"

_TTS_ENDPOINT = "/t2a_v2"

# t2a_v2 audio_setting.format 接受的容器（与 audio_formats 参考一致）。
_SUPPORTED_AUDIO_FORMATS = frozenset({"mp3", "wav", "flac", "pcm"})
_FALLBACK_AUDIO_FORMAT = "wav"


def _audio_format_for(output_path: Path) -> str:
    """按落盘扩展名选输出容器，保证文件内容与扩展名一致（资源路径约定 .wav）。"""
    suffix = output_path.suffix.lstrip(".").lower()
    return suffix if suffix in _SUPPORTED_AUDIO_FORMATS else _FALLBACK_AUDIO_FORMAT


def _as_dict(value: object) -> dict:
    """把任意值归一化为 dict（与 minimax_shared 同口径），避免非 dict 真值调 .get。"""
    return value if isinstance(value, dict) else {}


def _tts_failure_reason(payload: dict) -> str | None:
    """base_resp.status_code 非零时返回错误描述；成功（0）或缺失 base_resp 返回 None。

    MiniMax 业务错误以 HTTP 200 + base_resp.status_code 非零承载，故同步响应须先查
    base_resp 再取音频 URL（与图像 / 视频响应同构）。
    """
    base = _as_dict(_as_dict(payload).get("base_resp"))
    status = base.get("status_code")
    if status is not None and status != 0:
        msg = base.get("status_msg") or ""
        return f"MiniMax 语音合成失败 status_code={status}: {msg}".strip()
    return None


class _EmptyDownloadError(RuntimeError):
    """200 但空响应体（瞬时代理/CDN 异常），视为瞬态触发下载重试。"""


class MiniMaxAudioBackend:
    """MiniMax 语音合成后端（同步 t2a_v2 端点）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        self._api_key = resolve_minimax_api_key(api_key)
        self._base_url = minimax_text_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return PROVIDER_MINIMAX

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH}

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        # 合成（计费）与下载分两段独立重试：下载瞬时失败只重试 GET，绝不回头重跑会再次计费的
        # 合成 POST（与 DashScopeAudioBackend 及 lib.retry.DOWNLOAD_* 语义一致）。
        url = await self._request_synthesis(request)
        await self._download_audio(url, request.output_path)
        logger.info("MiniMax 语音合成完成: %s", request.output_path)
        return AudioSynthesisResult(
            provider=PROVIDER_MINIMAX,
            model=self._model,
            characters=len(request.text),
            output_path=request.output_path,
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _request_synthesis(self, request: AudioSynthesisRequest) -> str:
        """提交合成请求（计费段），返回 data.audio 下载 URL。"""
        voice_setting: dict[str, object] = {"voice_id": request.voice}
        if request.speed is not None:
            voice_setting["speed"] = request.speed
        payload: dict = {
            "model": self._model,
            "text": request.text,
            "output_format": "url",
            "voice_setting": voice_setting,
            "audio_setting": {"format": _audio_format_for(request.output_path)},
        }
        if request.language_type:
            payload["language_boost"] = request.language_type

        # 日志只带白名单标量 + 文本长度，不展开合成文本（CodeQL clear-text-logging 约束）。
        logger.info(
            "调用 %s 语音合成 API model=%s voice=%s language=%s format=%s chars=%d",
            self.name,
            self._model,
            request.voice,
            request.language_type,
            payload["audio_setting"]["format"],
            len(request.text),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            # 合成是非幂等的「计费」POST：submit_post 把歧义传输错误转 AmbiguousSubmitError
            # 终态失败避免重复计费；>=400 落 body 日志 + 抛 HTTPStatusError（保留 status_code），
            # 交 should_retry_submit 按状态码分流——4xx fail-fast、5xx/429 重试。
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{_TTS_ENDPOINT}",
                    json=payload,
                    headers=minimax_headers(self._api_key),
                ),
                provider=PROVIDER_MINIMAX,
            )
            data = resp.json()

        reason = _tts_failure_reason(data)
        if reason:
            # 不嵌完整响应体进异常消息：body 里的 "503"/"timeout" 子串会被默认 _should_retry
            # 误判为可重试（仓库已确立按状态码而非字符串判重试）。
            logger.error("MiniMax 语音合成业务错误: %s", reason)
            raise RuntimeError(reason)

        url = _as_dict(_as_dict(data).get("data")).get("audio")
        if not isinstance(url, str) or not url:
            logger.error("MiniMax 语音合成响应缺少 data.audio URL: %s", data)
            raise RuntimeError("MiniMax 语音合成响应缺少 data.audio URL")
        return url

    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        # 下载是幂等 GET：HTTPStatusError 按 status_code 闸门（should_retry_download，4xx 含 404 一律
        # fail-fast——结果 URL 的 4xx 是确定性错误），5xx/传输/网络错误重试，业务错误 fail-fast；
        # 200-空体（_EmptyDownloadError）属瞬态另行重试。
        retry_if=lambda e: isinstance(e, _EmptyDownloadError) or should_retry_download(e),
    )
    async def _download_audio(self, url: str, output_path: Path) -> None:
        """下载合成音频（非计费段，可独立多次重试）。"""
        # 日志与异常只带去掉 query 的 URL：结果 URL 在有效期内等同下载凭证。
        safe_url = url.split("?", 1)[0]
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                logger.warning("MiniMax 音频下载返回 %s: %s", resp.status_code, safe_url)
                # 不用 raise_for_status：它生成的异常文本携带完整结果 URL；
                # 手动构造保留异常类型与 .response.status_code，消息只带脱敏 URL。
                raise httpx.HTTPStatusError(
                    f"MiniMax 音频下载返回 {resp.status_code}: {safe_url}",
                    request=resp.request,
                    response=resp,
                )
            if not resp.content:
                # 200 但空体：不写 0 字节音频
                raise _EmptyDownloadError(f"MiniMax 音频下载返回空内容: {safe_url}")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(resp.content)
