"""验证 duration_presets 启发式表覆盖排行榜 Top-20 模型 + 未匹配回退。"""

from __future__ import annotations

import pytest

from lib.custom_provider.duration_presets import (
    DEFAULT_FALLBACK,
    infer_supported_durations,
)


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("sora-2", [4, 8, 12]),
        ("sora-2-pro", [4, 8, 12]),
        ("sora-2-pro-2026-01-15", [4, 8, 12]),
        ("aggregator-sora-pro-v2", [6, 10, 12, 16, 20]),
        ("veo-3.1-generate-001", [4, 6, 8]),
        ("veo-3.1-fast-generate-preview", [4, 6, 8]),
        ("veo3-lite", [4, 6, 8]),
        ("kling-v3.0", [5, 10]),
        ("kling-3.0-omni-pro", [5, 10]),
        ("kling-2.5-turbo", [5, 10]),
        ("kling-o1-pro", [5, 10]),
        ("runway-gen-4.5", [5, 8, 10]),
        ("gen-4.5", [5, 8, 10]),
        ("ray-3", [5, 10]),
        ("dreamina-seedance-2-0-260128", list(range(4, 16))),
        ("doubao-seedance-1-5-pro-251215", list(range(4, 16))),
        ("jimeng-video-3.0", list(range(4, 16))),
        ("happyhorse-1.0", list(range(3, 16))),
        ("grok-imagine-video", list(range(1, 16))),
        ("viduq3-pro", list(range(1, 17))),
        ("pixverse-v6", list(range(1, 16))),
        ("v5.6", list(range(1, 16))),
        ("hailuo-02", [6]),
        ("MiniMax-Hailuo-2.3", [6]),
        ("MiniMax-Hailuo-2.3-Fast", [6]),
        ("minimax-abab-6.5", DEFAULT_FALLBACK),
        ("minimax-h3", list(range(4, 16))),
        ("MiniMax-H3", list(range(4, 16))),
        ("proxy-minimax-h3-turbo", list(range(4, 16))),
        ("wan-2.1", [4, 5]),
        ("WAN_2-T2V", [4, 5]),
        ("wan2.7-i2v", list(range(2, 16))),
        ("wan_2.7-i2v", list(range(2, 16))),
        ("wan-2.7-i2v", list(range(2, 16))),
        ("wan3.0-video", list(range(2, 31))),
        ("Wan3.0-Video", list(range(2, 31))),
        ("wan-3-turbo", list(range(2, 31))),
        ("wan3-turbo", list(range(2, 31))),
        ("wan_3_turbo", list(range(2, 31))),
        ("wan_3.0-turbo", list(range(2, 31))),
        ("swan3", [4, 5]),
        ("vendorwan3", [4, 5]),
        ("wan30", [4, 5]),
        ("pika-2.0", [3, 5, 10]),
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
