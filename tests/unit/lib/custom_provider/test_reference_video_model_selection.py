"""纯选择函数 :func:`select_reference_video_model` 的单测（无 DB）。

映射真相源不是字符串模板：family 以真实 model_id 构造，I3 的 model_id 无 ``_shot`` 后缀。
严格 N → I{N}：命中返回对应 model_id，不命中（含 n=0、n>9、区间内缺档）一律 fail loud、
点名所缺槽数，绝不就近 / 大槽回退。
"""

from __future__ import annotations

import pytest

from lib.config.resolver import VideoBucketCapabilityError
from lib.custom_provider.reference_video_model_selection import select_reference_video_model

# §1 权威事实：provider-2 的 I 系真实 model_id（I3 无 ``_shot``）。
_REAL_FAMILY = {
    1: "HONGYU-MinimaxH3-I1-A1-First_shot-AutoPrompt",
    2: "HONGYU-MinimaxH3-I2-A2-First_shot-AutoPrompt",
    3: "HONGYU-MinimaxH3-I3-A3-First-AutoPrompt",
    4: "HONGYU-MinimaxH3-I4-A3-First_shot-AutoPrompt",
    5: "HONGYU-MinimaxH3-I5-A3-First_shot-AutoPrompt",
    6: "HONGYU-MinimaxH3-I6-A3-First_shot-AutoPrompt",
    7: "HONGYU-MinimaxH3-I7-A3-First_shot-AutoPrompt",
    8: "HONGYU-MinimaxH3-I8-A3-First_shot-AutoPrompt",
    9: "HONGYU-MinimaxH3-I9-A3-First_shot-AutoPrompt",
}


@pytest.mark.parametrize("n", list(range(1, 10)))
def test_exact_match_returns_real_model_id(n: int) -> None:
    assert select_reference_video_model(n, _REAL_FAMILY) == _REAL_FAMILY[n]


def test_i3_has_no_shot_suffix() -> None:
    """I3 的真实 model_id 无 ``_shot``——锁死「真相源不是字符串模板」。"""
    selected = select_reference_video_model(3, _REAL_FAMILY)
    assert selected == "HONGYU-MinimaxH3-I3-A3-First-AutoPrompt"
    assert "_shot" not in selected


def test_zero_references_fails_loud() -> None:
    with pytest.raises(VideoBucketCapabilityError) as excinfo:
        select_reference_video_model(0, _REAL_FAMILY)
    assert excinfo.value.code == "video_capability_missing_r2v"
    assert excinfo.value.params["required_slots"] == 0


def test_above_nine_does_not_fall_back_to_i9() -> None:
    with pytest.raises(VideoBucketCapabilityError) as excinfo:
        select_reference_video_model(10, _REAL_FAMILY)
    assert excinfo.value.params["required_slots"] == 10


def test_missing_default_tier_fails_loud_not_nearest() -> None:
    """默认档（I3）缺失时 n=3 fail loud，不就近取 I4。"""
    family = {k: v for k, v in _REAL_FAMILY.items() if k != 3}
    with pytest.raises(VideoBucketCapabilityError) as excinfo:
        select_reference_video_model(3, family)
    assert excinfo.value.params["required_slots"] == 3


def test_missing_mid_tier_in_range_fails_loud_not_clamp() -> None:
    """区间内非默认档缺失（I5 被禁用，n=5 落在 1..9 内）同样 fail loud，不 clamp 到 I4/I6/I9。"""
    family = {k: v for k, v in _REAL_FAMILY.items() if k != 5}
    with pytest.raises(VideoBucketCapabilityError) as excinfo:
        select_reference_video_model(5, family)
    assert excinfo.value.params["required_slots"] == 5


def test_disabled_tier_equivalent_to_missing() -> None:
    """被禁用而未进 family 的 N 等价于缺档：fail loud。"""
    family = {1: _REAL_FAMILY[1], 2: _REAL_FAMILY[2]}
    with pytest.raises(VideoBucketCapabilityError) as excinfo:
        select_reference_video_model(2 + 1, family)  # n=3，family 里没有
    assert excinfo.value.params["required_slots"] == 3
