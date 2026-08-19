"""Doubao 共享工具模块。

供 audio_backends / config 复用。火山引擎豆包语音合成 v3 用新版鉴权（X-Api-Key +
X-Api-Resource-Id header），base 为 /api/v3 挂载根。
- DOUBAO_TTS_BASE_URL — 默认 base（含 /api/v3）
- doubao_base_url — 归一化 base（去末尾斜杠，容忍 host-only）
"""

from __future__ import annotations

# 默认 base（含 /api/v3）；用户可经配置覆盖 base_url 指向自建中转。
DOUBAO_TTS_BASE_URL = "https://openspeech.bytedance.com/api/v3"


def doubao_base_url(configured: str | None = None) -> str:
    """归一化 base：去末尾斜杠，容忍用户填 host 或带 /api/v3 后缀。"""
    return ((configured or "").strip() or DOUBAO_TTS_BASE_URL).rstrip("/")
