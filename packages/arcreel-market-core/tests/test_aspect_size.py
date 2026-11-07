"""Test the unified sizing mechanism arcreel_market_core/aspect_size.py:

Core invariants: zero ratio deviation + dimensions divisible by round_to + ratio-preserving clamping (t≥1 floor).
"""

import logging

import pytest

from arcreel_market_core.aspect_size import (
    IMAGE_TIER_SHORT_EDGE,
    VIDEO_TIER_SHORT_EDGE,
    aspect_size,
    parse_aspect_ratio,
    resolution_to_short_edge,
    short_edge_to_resolution,
)

ALL_ASPECTS = ["9:16", "16:9", "1:1", "3:4", "4:3", "2:3", "3:2", "21:9"]


# ---------------- parse_aspect_ratio ----------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9:16", (9, 16)),
        ("16:9", (16, 9)),
        ("1:1", (1, 1)),
        ("3:4", (3, 4)),
        ("21:9", (7, 3)),  # Reduce
        ("18:32", (9, 16)),  # Reduce to coprime terms
        ("16：9", (16, 9)),  # Full-width colon
    ],
)
def test_parse_aspect_ratio_reduces(raw, expected):
    assert parse_aspect_ratio(raw) == expected


@pytest.mark.parametrize("bad", ["", "abc", "9:0", "0:16", "-9:16", "9", "9:16:1"])
def test_parse_aspect_ratio_invalid_falls_back(bad, caplog):
    with caplog.at_level(logging.WARNING):
        assert parse_aspect_ratio(bad) == (9, 16)
    assert any("aspect_ratio" in r.message for r in caplog.records)


# ---------------- aspect_size exact ratio + divisibility ----------------


@pytest.mark.parametrize("aspect", ALL_ASPECTS)
@pytest.mark.parametrize("short", [480, 720, 1024, 1440, 2160])
def test_aspect_size_exact_ratio_and_divisible(aspect, short):
    w, h = aspect_size(aspect, short, round_to=16)
    aw, ah = parse_aspect_ratio(aspect)
    # Zero ratio deviation: w/h == aw/ah ⇔ w*ah == h*aw
    assert w * ah == h * aw
    # Both divisible by 16
    assert w % 16 == 0
    assert h % 16 == 0
    # Dimension orientation matches the ratio
    assert (w >= h) == (aw >= ah)


@pytest.mark.parametrize(
    ("aspect", "short", "expected"),
    [
        ("9:16", 720, (720, 1280)),
        ("9:16", 1024, (1008, 1792)),
        ("9:16", 1440, (1440, 2560)),
        ("9:16", 2160, (2160, 3840)),
        ("3:4", 768, (768, 1024)),
        ("16:9", 720, (1280, 720)),
        ("1:1", 720, (720, 720)),
    ],
)
def test_aspect_size_known_samples(aspect, short, expected):
    assert aspect_size(aspect, short, round_to=16) == expected


def test_aspect_size_t_floor_never_zero():
    # Even a short-edge target below one unit yields the minimum valid size (t≥1), never 0
    w, h = aspect_size("9:16", 10, round_to=16)
    assert (w, h) == (144, 256)


def test_aspect_size_max_long_edge_clamps_keeping_ratio():
    # gpt-image-2: maximum long edge 3840
    w, h = aspect_size("9:16", 4000, round_to=16, max_long_edge=3840)
    assert h <= 3840
    assert w * 16 == h * 9  # Still exactly 9:16
    assert (w, h) == (2160, 3840)


def test_aspect_size_max_long_edge_floor_keeps_min_legal():
    # Long-edge limit below one unit: retain minimum valid size, never 0
    w, h = aspect_size("9:16", 720, round_to=16, max_long_edge=100)
    assert (w, h) == (144, 256)


@pytest.mark.parametrize(
    ("aspect", "expected"),
    [
        # DashScope standard budget 2048²=4194304: maximal and exact for every ratio
        ("9:16", (1440, 2560)),
        ("16:9", (2560, 1440)),
        ("1:1", (2048, 2048)),
        ("4:3", (2304, 1728)),
        ("3:4", (1728, 2304)),
    ],
)
def test_aspect_size_max_total_pixels_standard_budget(aspect, expected):
    w, h = aspect_size(aspect, 2048, round_to=16, max_total_pixels=2048 * 2048)
    assert (w, h) == expected
    assert w * h <= 2048 * 2048
    aw, ah = parse_aspect_ratio(aspect)
    assert w * ah == h * aw  # Exact


def test_aspect_size_4k_total_budget():
    # wan 4K budget 4096²=16777216
    w, h = aspect_size("9:16", 2160, round_to=16, max_total_pixels=4096 * 4096)
    assert (w, h) == (2160, 3840)
    assert w * h <= 4096 * 4096


def test_aspect_size_takes_stricter_of_two_clamps():
    # With both max_long_edge and max_total_pixels, enforce the stricter constraint
    w, h = aspect_size("9:16", 2160, round_to=16, max_long_edge=2000, max_total_pixels=4096 * 4096)
    assert h <= 2000
    assert w * 16 == h * 9


def test_aspect_size_extreme_ratio_warns(caplog):
    with caplog.at_level(logging.WARNING):
        aspect_size("21:9", 720, round_to=16, max_ratio=3.0)  # 7:3≈2.33 < 3, no warning
    assert not any("超出后端支持上限" in r.message for r in caplog.records)
    with caplog.at_level(logging.WARNING):
        aspect_size("32:9", 720, round_to=16, max_ratio=3.0)  # 32:9≈3.56 > 3，warn
    assert any("超出后端支持上限" in r.message for r in caplog.records)


# ---------------- resolution_to_short_edge ----------------


def test_resolution_none_returns_default():
    assert resolution_to_short_edge(None, tier_map=IMAGE_TIER_SHORT_EDGE) == 720
    assert resolution_to_short_edge(None, tier_map=IMAGE_TIER_SHORT_EDGE, default_short=1440) == 1440
    assert resolution_to_short_edge("  ", tier_map=IMAGE_TIER_SHORT_EDGE) == 720


@pytest.mark.parametrize(
    ("tier", "expected"),
    [("512px", 512), ("1K", 1024), ("2K", 1440), ("4K", 2160), ("2k", 1440), ("4K ", 2160)],
)
def test_resolution_tier_word(tier, expected):
    assert resolution_to_short_edge(tier, tier_map=IMAGE_TIER_SHORT_EDGE) == expected


@pytest.mark.parametrize(
    ("tier", "expected"),
    [("480p", 480), ("720p", 720), ("1080p", 1080), ("4K", 2160), ("1080P", 1080)],
)
def test_resolution_video_tier_word(tier, expected):
    assert resolution_to_short_edge(tier, tier_map=VIDEO_TIER_SHORT_EDGE) == expected


@pytest.mark.parametrize(
    ("custom", "expected"),
    [("1920x1080", 1080), ("1080x1920", 1080), ("2688*1536", 1536), ("1024×1024", 1024)],
)
def test_resolution_custom_wh_takes_min(custom, expected):
    # Discard custom values' own ratios; use min as the short edge
    assert resolution_to_short_edge(custom, tier_map=IMAGE_TIER_SHORT_EDGE) == expected


def test_resolution_pure_number():
    assert resolution_to_short_edge("720", tier_map=IMAGE_TIER_SHORT_EDGE) == 720


def test_resolution_unparseable_falls_back_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert resolution_to_short_edge("garbage", tier_map=IMAGE_TIER_SHORT_EDGE) == 720
    assert any("无法解析 resolution" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    ("short_edge", "expected"),
    [(480, "480p"), (720, "720p"), (1080, "1080p"), (2160, "4K")],
)
def test_a_short_edge_that_lands_on_a_tier_says_that_tier(short_edge, expected):
    assert short_edge_to_resolution(short_edge, tier_map=VIDEO_TIER_SHORT_EDGE) == expected


@pytest.mark.parametrize("short_edge", [848, 600, 1100, 4000])
def test_a_short_edge_between_tiers_says_the_pixels_it_really_is(short_edge):
    # A nearest-tier label misrepresents output without a selected tier: selecting 720p gives short edge 720, not 848.
    assert short_edge_to_resolution(short_edge, tier_map=VIDEO_TIER_SHORT_EDGE) == f"{short_edge}px"


def test_the_tier_table_decides_the_vocabulary():
    # The same short edge matches another label in image tiers and no tier in the video table.
    assert short_edge_to_resolution(1024, tier_map=IMAGE_TIER_SHORT_EDGE) == "1K"
    assert short_edge_to_resolution(1024, tier_map=VIDEO_TIER_SHORT_EDGE) == "1024px"
