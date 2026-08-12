"""DashScopeAudioBackend — 阿里百炼 Qwen3-TTS 语音合成后端（同步）。

走原生 multimodal-generation/generation 同步端点：请求体 ``input`` 直接携带
``text`` / ``voice`` / ``language_type``（区别于图像的 messages 结构），响应在
``output.audio.url`` 给出 wav 文件 URL（24kHz，24h 有效），再 HTTP GET 下载字节落盘。
TTS 同步调用不带 X-DashScope-Async 头（该头仅图像/视频异步两步式使用）。
schema 依据 docs/api-docs/providers/dashscope.md 所列一手官方文档。
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.audio_backends.base import (
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.dashscope_shared import (
    dashscope_headers,
    dashscope_native_base_url,
    extract_audio_url,
    resolve_dashscope_api_key,
    safe_body_for_log,
)
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_DASHSCOPE
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
from lib.video_backends.base import should_retry_download, should_retry_submit, submit_post

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen3-tts-flash"

_TTS_ENDPOINT = "/services/aigc/multimodal-generation/generation"


# Qwen3-TTS 系统预设音色（``voice`` 参数取值）子集，出处见 docs/api-docs/providers/dashscope.md
# 「三、系统预设音色」表格（来源：
# https://help.aliyun.com/zh/model-studio/qwen-tts 官方文档 + 百炼控制台模型市场）。
# 未列出的其余预设音色不在此暴露——该文档明确标注为「ArcReel 场景最相关的音色子集」，
# 完整 48 音色列表见官方文档，未逐一核实中文名/描述不收录，避免编造。
#
# label 存的是 lib/i18n 翻译 key（``voice_label_dashscope_*``，见 lib/i18n/{zh,en,vi}/assets.py），
# 不是直出文案——端点固定硬编码中文会让 en/vi 用户在下拉里看到中文描述，与「后端面向用户的
# 文本经 _t: Translator 注入」的项目惯例不符。真正的本地化文案由 get_audio_backend_voices
# 路由层用请求 locale 对应的 Translator 渲染。
_VOICE_CATALOG: tuple[VoiceOption, ...] = (
    VoiceOption(id="Cherry", label="voice_label_dashscope_cherry"),
    VoiceOption(id="Serena", label="voice_label_dashscope_serena"),
    VoiceOption(id="Ethan", label="voice_label_dashscope_ethan"),
    VoiceOption(id="Chelsie", label="voice_label_dashscope_chelsie"),
    VoiceOption(id="Nofish", label="voice_label_dashscope_nofish"),
    VoiceOption(id="Jennifer", label="voice_label_dashscope_jennifer"),
    VoiceOption(id="Ryan", label="voice_label_dashscope_ryan"),
    VoiceOption(id="Bellona", label="voice_label_dashscope_bellona"),
    VoiceOption(id="Neil", label="voice_label_dashscope_neil"),
    VoiceOption(id="Elias", label="voice_label_dashscope_elias"),
    VoiceOption(id="Momo", label="voice_label_dashscope_momo"),
    VoiceOption(id="Vivian", label="voice_label_dashscope_vivian"),
    VoiceOption(id="Moon", label="voice_label_dashscope_moon"),
    VoiceOption(id="Maia", label="voice_label_dashscope_maia"),
    VoiceOption(id="Kai", label="voice_label_dashscope_kai"),
    VoiceOption(id="Katerina", label="voice_label_dashscope_katerina"),
    VoiceOption(id="Bella", label="voice_label_dashscope_bella"),
    VoiceOption(id="Eldric Sage", label="voice_label_dashscope_eldric_sage"),
    VoiceOption(id="Vincent", label="voice_label_dashscope_vincent"),
)


class _EmptyDownloadError(RuntimeError):
    """200 但空响应体（瞬时代理/CDN 异常），视为瞬态触发下载重试。"""


class DashScopeAudioBackend:
    """阿里百炼语音合成后端（同步 multimodal 端点）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        self._api_key = resolve_dashscope_api_key(api_key)
        self._base_url = dashscope_native_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return PROVIDER_DASHSCOPE

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH}

    def list_voices(self) -> list[VoiceOption]:
        return list(_VOICE_CATALOG)

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        if request.speed is not None:
            # 同步 qwen3-tts-flash 不支持 speech_rate（仅 realtime WebSocket 版可用），忽略。
            logger.debug("DashScope 同步 TTS 不支持语速参数 speed=%s，已忽略", request.speed)

        # 合成（计费）与下载分两段独立重试：下载瞬时失败只重试 GET，绝不回头重跑会再次计费的
        # 合成 POST（与 lib.retry.DOWNLOAD_* 注释「下载失败不浪费生成额度」一致）。
        url = await self._request_synthesis(request)
        await self._download_audio(url, request.output_path)

        logger.info("DashScope 语音合成完成: %s", request.output_path)

        return AudioSynthesisResult(
            provider=PROVIDER_DASHSCOPE,
            model=self._model,
            characters=len(request.text),
            output_path=request.output_path,
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _request_synthesis(self, request: AudioSynthesisRequest) -> str:
        """提交合成请求（计费段），返回 output.audio.url。"""
        payload = {
            "model": self._model,
            "input": {
                "text": request.text,
                "voice": request.voice,
                "language_type": request.language_type,
            },
        }
        # safe_body_for_log 只输出 model + parameters 白名单，不读取 input.text，
        # 合成文本不会进日志（CodeQL clear-text-logging 告警对此为误报）。
        logger.info(
            "调用 %s 语音合成 API model=%s voice=%s language=%s chars=%d body=%s",
            self.name,
            self._model,
            request.voice,
            request.language_type,
            len(request.text),
            format_kwargs_for_log(safe_body_for_log(payload)),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            # 合成是非幂等的「计费」POST：submit_post 把歧义传输错误（请求可能已送达但响应在途丢失）
            # 转 AmbiguousSubmitError 终态失败避免重复计费；>=400 落 body 日志 + 抛 HTTPStatusError
            # （保留 status_code），交 should_retry_submit 按状态码分流——4xx fail-fast、5xx/429 重试。
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{_TTS_ENDPOINT}",
                    json=payload,
                    headers=dashscope_headers(self._api_key),
                ),
                provider=PROVIDER_DASHSCOPE,
            )
            return extract_audio_url(resp.json())

    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        # 下载是幂等 GET：HTTPStatusError 按 status_code 闸门（should_retry_download，4xx 含 404 一律
        # fail-fast——预签发 URL 的 4xx 是确定性错误），5xx/传输/网络错误重试，业务错误 fail-fast；
        # 200-空体（_EmptyDownloadError）属瞬态另行重试。
        retry_if=lambda e: isinstance(e, _EmptyDownloadError) or should_retry_download(e),
    )
    async def _download_audio(self, url: str, output_path: Path) -> None:
        """下载合成音频（非计费段，可独立多次重试）。"""
        # 日志与异常只带去掉 query 的 URL：预签名参数在有效期内等同下载凭证
        safe_url = url.split("?", 1)[0]
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                logger.warning("DashScope 音频下载返回 %s: %s", resp.status_code, safe_url)
                # 不用 raise_for_status：它生成的异常文本携带完整预签名 URL；
                # 手动构造保留异常类型与 .response.status_code，消息只带脱敏 URL。
                raise httpx.HTTPStatusError(
                    f"DashScope 音频下载返回 {resp.status_code}: {safe_url}",
                    request=resp.request,
                    response=resp,
                )
            if not resp.content:
                # 200 但空体：不写 0 字节 wav
                raise _EmptyDownloadError(f"DashScope 音频下载返回空内容: {safe_url}")
            output_path.write_bytes(resp.content)
