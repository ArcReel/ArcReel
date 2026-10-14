"""MiniMaxAudioBackend — MiniMax 同步语音合成后端（``POST {host}/v1/t2a_v2``）。

非流式请求以 ``output_format=hex`` 取回音频：响应 ``data.audio`` 是按 ``audio_setting.format``
编码后的音频字节的 hex 串，解码即可落盘，无需二段下载。业务错误以 HTTP 200 +
``base_resp.status_code`` 非零承载。schema 依据 docs/api-docs/endpoints/minimax-tts.md
所列官方文档。内置 ``minimax`` 供应商与自定义供应商的 ``minimax-tts`` 端点共用本通道。
"""

from __future__ import annotations

import logging
from pathlib import Path

from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.audio_backends.base import (
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.backends.backend_runtime import should_retry_submit, submit_post
from lib.backends.minimax_shared import minimax_headers, minimax_text_base_url, resolve_minimax_api_key
from lib.backends.providers import PROVIDER_MINIMAX
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "speech-2.8-hd"

# 非流式 t2a_v2 可用的输出格式子集，按落盘扩展名选取，保证文件内容与扩展名一致（资源路径约定 .wav）。
_SUPPORTED_FORMATS = frozenset({"mp3", "wav", "flac", "pcm"})
_FALLBACK_FORMAT = "wav"

# 系统音色（``voice_setting.voice_id``）中最基础的一组普通话成人音色，出处见
# docs/api-docs/endpoints/minimax-tts.md 所列「系统音色列表」。完整列表数百条且含大量多语种音色，
# 这里只暴露配音最常用的子集；label 是 lib/i18n 翻译 key，由音色接口按请求 locale 渲染。
_VOICE_CATALOG: tuple[VoiceOption, ...] = tuple(
    VoiceOption(id=voice_id, label=f"voice_label_minimax_{voice_id.replace('-', '_')}")
    for voice_id in (
        "male-qn-qingse",
        "male-qn-jingying",
        "male-qn-badao",
        "male-qn-daxuesheng",
        "female-shaonv",
        "female-yujie",
        "female-chengshu",
        "female-tianmei",
    )
)


def _format_for(output_path: Path) -> str:
    suffix = output_path.suffix.lstrip(".").lower()
    return suffix if suffix in _SUPPORTED_FORMATS else _FALLBACK_FORMAT


def _decode_audio(payload: object) -> bytes:
    """取出 ``data.audio`` 并按 hex 解码；``base_resp`` 非零或音频缺失时抛 RuntimeError。"""
    body = payload if isinstance(payload, dict) else {}
    base = body.get("base_resp")
    base = base if isinstance(base, dict) else {}
    code = base.get("status_code")
    if code not in (None, 0):
        raise RuntimeError(f"MiniMax 语音合成失败 status_code={code}: {base.get('status_msg', '')}".strip())
    data = body.get("data")
    data = data if isinstance(data, dict) else {}
    audio_hex = data.get("audio")
    if not isinstance(audio_hex, str) or not audio_hex:
        raise RuntimeError("MiniMax 语音合成响应缺少 data.audio")
    try:
        return bytes.fromhex(audio_hex)
    except ValueError as exc:
        raise RuntimeError("MiniMax 语音合成响应的 data.audio 不是合法 hex") from exc


class MiniMaxAudioBackend:
    """MiniMax 同步语音合成后端（``t2a_v2``）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        provider_name: str = PROVIDER_MINIMAX,
        http_timeout: float = 120.0,
    ) -> None:
        self._api_key = resolve_minimax_api_key(api_key)
        # 与文本共用 {host}/v1 归一化：容忍用户填 host 或带 /v1 后缀
        self._base_url = minimax_text_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        # 自定义供应商挂接时用真实 provider 记账
        self._provider_name = provider_name
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH, AudioCapability.SPEECH_SPEED}

    def list_voices(self) -> list[VoiceOption]:
        return list(_VOICE_CATALOG)

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        # language_type 是 DashScope 特有字段，t2a_v2 按文本自动识别语种，不发送。
        # 计费调用与写盘分离：重试只包 API 调用，写盘失败绝不回头重跑会再次计费的合成请求。
        audio = await self._request_speech(request)
        # 供应商已计费返回，写盘不可被取消打断，结果照常成为产物
        await run_noninterruptible_sync(request.output_path.write_bytes, audio)
        logger.info("MiniMax 语音合成完成: %s", request.output_path)
        return AudioSynthesisResult(
            provider=self._provider_name,
            model=self._model,
            characters=len(request.text),
            output_path=request.output_path,
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _request_speech(self, request: AudioSynthesisRequest) -> bytes:
        voice_setting: dict = {"voice_id": request.voice}
        if request.speed is not None:
            voice_setting["speed"] = request.speed
        payload = {
            "model": self._model,
            "text": request.text,
            "stream": False,
            "output_format": "hex",
            "voice_setting": voice_setting,
            "audio_setting": {"format": _format_for(request.output_path)},
        }
        logger.info(
            "调用 %s 语音合成 API model=%s voice=%s format=%s chars=%d",
            self.name,
            self._model,
            request.voice,
            payload["audio_setting"]["format"],
            len(request.text),
        )
        async with artifact_http_client(timeout=self._http_timeout) as client:
            # 合成是计费 POST：submit_post 把歧义传输错误转终态失败避免重复计费，>=400 交状态码分流
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}/t2a_v2",
                    json=payload,
                    headers=minimax_headers(self._api_key),
                ),
                provider=self._provider_name,
                include_provider_reason=False,
            )
        # 供应商已计费返回：整段 hex 的解析与解码放进线程且不可被取消打断
        return await run_noninterruptible_sync(lambda: _decode_audio(resp.json()))
