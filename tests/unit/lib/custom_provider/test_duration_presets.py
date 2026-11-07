"""Verify duration_presets heuristics cover Top-20 ranked models and unmatched fallback."""

from __future__ import annotations

import pytest

from lib.custom_provider.duration_presets import (
    DEFAULT_FALLBACK,
    infer_supported_durations,
)


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        # First-party OpenAI Sora.
        ("sora-2", [4, 8, 12]),
        ("sora-2-pro", [4, 8, 12]),
        ("sora-2-pro-2026-01-15", [4, 8, 12]),
        # Third-party sora-pro variants contain sora and pro but fail strict first-party regexes.
        ("aggregator-sora-pro-v2", [6, 10, 12, 16, 20]),
        # Veo family.
        ("veo-3.1-generate-001", [4, 6, 8]),
        ("veo-3.1-fast-generate-preview", [4, 6, 8]),
        ("veo3-lite", [4, 6, 8]),
        # Entire Kling family.
        ("kling-v3.0", [5, 10]),
        ("kling-3.0-omni-pro", [5, 10]),
        ("kling-2.5-turbo", [5, 10]),
        ("kling-o1-pro", [5, 10]),
        # Runway Gen
        ("runway-gen-4.5", [5, 8, 10]),
        ("gen-4.5", [5, 8, 10]),
        # Luma Ray
        ("ray-3", [5, 10]),
        # ByteDance Seedance / Dreamina (full 4-15 expansion).
        ("dreamina-seedance-2-0-260128", list(range(4, 16))),
        ("doubao-seedance-1-5-pro-251215", list(range(4, 16))),
        # Jimeng.
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
        # Hailuo / MiniMax (actual video model IDs all include hailuo branding).
        ("hailuo-02", [6]),
        ("MiniMax-Hailuo-2.3", [6]),
        ("MiniMax-Hailuo-2.3-Fast", [6]),
        # MiniMax IDs without hailuo no longer select fixed 6; use defaults after removing the bare minimax token.
        ("minimax-abab-6.5", DEFAULT_FALLBACK),
        # MiniMax H3, including case/prefix variants, avoids Hailuo fixed-6 presets.
        ("minimax-h3", list(range(4, 16))),
        ("MiniMax-H3", list(range(4, 16))),
        ("proxy-minimax-h3-turbo", list(range(4, 16))),
        # Wan: hyphen/underscore/dot forms share tiers and match endpoint-routing/capability-profile breadth.
        ("wan-2.1", [4, 5]),
        ("WAN_2-T2V", [4, 5]),
        # Wan 2.7 avoids generic Wan [4, 5] presets; hyphen/underscore/dot forms share tiers.
        ("wan2.7-i2v", list(range(2, 16))),
        ("wan_2.7-i2v", list(range(2, 16))),
        ("wan-2.7-i2v", list(range(2, 16))),
        # Wan 3.0 avoids generic Wan [4, 5] presets.
        ("wan3.0-video", list(range(2, 31))),
        ("Wan3.0-Video", list(range(2, 31))),
        # Hyphenated third-party names and dot forms match equally broadly, recognizing Wan 3.0.
        ("wan-3-turbo", list(range(2, 31))),
        ("wan3-turbo", list(range(2, 31))),
        # Underscore forms match equally broadly too.
        ("wan_3_turbo", list(range(2, 31))),
        ("wan_3.0-turbo", list(range(2, 31))),
        # Identifier boundaries: nonfamily names containing "wan3" do not select Wan 3.0 tiers.
        ("swan3", [4, 5]),
        ("vendorwan3", [4, 5]),
        ("wan30", [4, 5]),
        # Pika
        ("pika-2.0", [3, 5, 10]),
        # Unknown model → fallback.
        ("totally-unknown-model", DEFAULT_FALLBACK),
        ("", DEFAULT_FALLBACK),
    ],
)
def test_infer_supported_durations_known_and_unknown(model_id: str, expected: list[int]):
    assert infer_supported_durations(model_id) == expected


def test_returned_list_is_independent_copy():
    """Successive calls return independent lists, preventing external mutation of preset tables."""
    a = infer_supported_durations("sora-2")
    b = infer_supported_durations("sora-2")
    assert a == b
    a.append(999)
    assert infer_supported_durations("sora-2") == [4, 8, 12]


def test_default_fallback_constant_shape():
    assert DEFAULT_FALLBACK == [4, 8]
    assert all(isinstance(x, int) and x > 0 for x in DEFAULT_FALLBACK)
