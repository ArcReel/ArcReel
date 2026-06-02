"""测试统一尺寸机制 lib/aspect_size.py：

核心不变量——比例零偏差 + 长宽被 round_to 整除 + 夹取保持比例（t≥1 floor）。
"""

import logging

import pytest

from lib.aspect_size import (
    IMAGE_TIER_SHORT_EDGE,
    VIDEO_TIER_SHORT_EDGE,
    aspect_size,
    parse_aspect_ratio,
    resolution_to_short_edge,
)

ALL_ASPECTS = ["9:16", "16:9", "1:1", "3:4", "4:3", "2:3", "3:2", "21:9"]


# ---------------- parse_aspect_ratio ----------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9:16", (9, 16)),
        ("16:9", (16, 9)),
        ("1:1", (1, 1)),
        ("3:4", (3, 4)),
        ("21:9", (7, 3)),  # 约简
        ("18:32", (9, 16)),  # 约简到互质
        ("16：9", (16, 9)),  # 全角冒号
    ],
)
def test_parse_aspect_ratio_reduces(raw, expected):
    assert parse_aspect_ratio(raw) == expected


@pytest.mark.parametrize("bad", ["", "abc", "9:0", "0:16", "-9:16", "9", "9:16:1"])
def test_parse_aspect_ratio_invalid_falls_back(bad, caplog):
    with caplog.at_level(logging.WARNING):
        assert parse_aspect_ratio(bad) == (9, 16)
    assert any("aspect_ratio" in r.message for r in caplog.records)


# ---------------- aspect_size 精确比例 + 整除 ----------------


@pytest.mark.parametrize("aspect", ALL_ASPECTS)
@pytest.mark.parametrize("short", [480, 720, 1024, 1440, 2160])
def test_aspect_size_exact_ratio_and_divisible(aspect, short):
    w, h = aspect_size(aspect, short, round_to=16)
    aw, ah = parse_aspect_ratio(aspect)
    # 比例零偏差：w/h == aw/ah ⇔ w*ah == h*aw
    assert w * ah == h * aw
    # 均被 16 整除
    assert w % 16 == 0 and h % 16 == 0
    # 宽高方向与比例一致
    assert (w >= h) == (aw >= ah)


@pytest.mark.parametrize(
    "aspect,short,expected",
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
    # 短边目标小于一个单位也至少给出最小合法尺寸（t≥1），不取 0
    w, h = aspect_size("9:16", 10, round_to=16)
    assert (w, h) == (144, 256)


def test_aspect_size_max_long_edge_clamps_keeping_ratio():
    # gpt-image-2：长边上限 3840
    w, h = aspect_size("9:16", 4000, round_to=16, max_long_edge=3840)
    assert h <= 3840
    assert w * 16 == h * 9  # 仍精确 9:16
    assert (w, h) == (2160, 3840)


def test_aspect_size_max_long_edge_floor_keeps_min_legal():
    # 长边上限比一个单位还小 → 不取 0，保留最小合法尺寸
    w, h = aspect_size("9:16", 720, round_to=16, max_long_edge=100)
    assert (w, h) == (144, 256)


@pytest.mark.parametrize(
    "aspect,expected",
    [
        # DashScope 标准预算 2048²=4194304：各比例最大且精确
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
    assert w * ah == h * aw  # 精确


def test_aspect_size_4k_total_budget():
    # wan 4K 预算 4096²=16777216
    w, h = aspect_size("9:16", 2160, round_to=16, max_total_pixels=4096 * 4096)
    assert (w, h) == (2160, 3840)
    assert w * h <= 4096 * 4096


def test_aspect_size_takes_stricter_of_two_clamps():
    # max_long_edge 与 max_total_pixels 同时给定时取更严者
    w, h = aspect_size("9:16", 2160, round_to=16, max_long_edge=2000, max_total_pixels=4096 * 4096)
    assert h <= 2000
    assert w * 16 == h * 9


def test_aspect_size_extreme_ratio_warns(caplog):
    with caplog.at_level(logging.WARNING):
        aspect_size("21:9", 720, round_to=16, max_ratio=3.0)  # 7:3≈2.33 < 3，不 warn
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
    "tier,expected",
    [("512px", 512), ("1K", 1024), ("2K", 1440), ("4K", 2160), ("2k", 1440), ("4K ", 2160)],
)
def test_resolution_tier_word(tier, expected):
    assert resolution_to_short_edge(tier, tier_map=IMAGE_TIER_SHORT_EDGE) == expected


@pytest.mark.parametrize(
    "tier,expected",
    [("480p", 480), ("720p", 720), ("1080p", 1080), ("4K", 2160), ("1080P", 1080)],
)
def test_resolution_video_tier_word(tier, expected):
    assert resolution_to_short_edge(tier, tier_map=VIDEO_TIER_SHORT_EDGE) == expected


@pytest.mark.parametrize(
    "custom,expected",
    [("1920x1080", 1080), ("1080x1920", 1080), ("2688*1536", 1536), ("1024×1024", 1024)],
)
def test_resolution_custom_wh_takes_min(custom, expected):
    # 自定义值剥离其自带比例，取 min 当短边
    assert resolution_to_short_edge(custom, tier_map=IMAGE_TIER_SHORT_EDGE) == expected


def test_resolution_pure_number():
    assert resolution_to_short_edge("720", tier_map=IMAGE_TIER_SHORT_EDGE) == 720


def test_resolution_unparseable_falls_back_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert resolution_to_short_edge("garbage", tier_map=IMAGE_TIER_SHORT_EDGE) == 720
    assert any("无法解析 resolution" in r.message for r in caplog.records)
