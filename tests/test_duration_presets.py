"""验证 duration_presets 启发式表覆盖排行榜 Top-20 模型 + 未匹配回退。"""

from __future__ import annotations

import pytest

from lib.custom_provider.duration_presets import (
    DEFAULT_FALLBACK,
    infer_supported_durations,
)


@pytest.mark.parametrize(
    "model_id, expected",
    [
        # OpenAI Sora 第一方
        ("sora-2", [4, 8, 12]),
        ("sora-2-pro", [4, 8, 12]),
        ("sora-2-pro-2026-01-15", [4, 8, 12]),
        # 第三方聚合 sora-pro 变体（命名含 sora 与 pro 但不匹配第一方严格 regex）
        ("aggregator-sora-pro-v2", [6, 10, 12, 16, 20]),
        # Veo 系列
        ("veo-3.1-generate-001", [4, 6, 8]),
        ("veo-3.1-fast-generate-preview", [4, 6, 8]),
        ("veo3-lite", [4, 6, 8]),
        # Kling 全系
        ("kling-v3.0", [5, 10]),
        ("kling-3.0-omni-pro", [5, 10]),
        ("kling-2.5-turbo", [5, 10]),
        ("kling-o1-pro", [5, 10]),
        # Runway Gen
        ("runway-gen-4.5", [5, 8, 10]),
        ("gen-4.5", [5, 8, 10]),
        # Luma Ray
        ("ray-3", [5, 10]),
        # ByteDance Seedance / Dreamina（4-15 全展开）
        ("dreamina-seedance-2-0-260128", list(range(4, 16))),
        ("doubao-seedance-1-5-pro-251215", list(range(4, 16))),
        # 即梦
        ("jimeng-video-3.0", list(range(4, 16))),
        # HappyHorse
        ("happyhorse-1.0", list(range(3, 16))),
        # Grok Imagine
        ("grok-imagine-video", list(range(1, 16))),
        # Vidu
        ("viduq3-pro", list(range(1, 17))),
        # PixVerse
        ("pixverse-v6", list(range(1, 16))),
        ("v5.6", list(range(1, 16))),
        # Hailuo / MiniMax
        ("hailuo-02", [6]),
        ("minimax-video", [6]),
        # Wan
        ("wan-2.1", [4, 5]),
        # Pika
        ("pika-2.0", [3, 5, 10]),
        # 未知模型 → fallback
        ("totally-unknown-model", DEFAULT_FALLBACK),
        ("", DEFAULT_FALLBACK),
    ],
)
def test_infer_supported_durations_known_and_unknown(model_id: str, expected: list[int]):
    assert infer_supported_durations(model_id) == expected


def test_returned_list_is_independent_copy():
    """连续两次调用返回的列表应是独立对象（防止外部修改污染预设表）。"""
    a = infer_supported_durations("sora-2")
    b = infer_supported_durations("sora-2")
    assert a == b
    a.append(999)
    assert infer_supported_durations("sora-2") == [4, 8, 12]


def test_default_fallback_constant_shape():
    assert DEFAULT_FALLBACK == [4, 8]
    assert all(isinstance(x, int) and x > 0 for x in DEFAULT_FALLBACK)
