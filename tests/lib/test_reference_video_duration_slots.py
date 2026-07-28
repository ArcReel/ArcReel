"""参考视频时长向上取档规则（容量语义）。"""

import pytest

from lib.reference_video.duration_slots import resolve_duration_slot

pytestmark = pytest.mark.unit


def test_total_is_slot_member_requests_it_unchanged():
    slot = resolve_duration_slot(8, [4, 8, 12])
    assert slot.seconds == 8
    assert slot.total_seconds == 8
    assert slot.adjustment == "exact"
    assert slot.needs_confirmation is False


def test_total_between_slots_rounds_up_to_smallest_fitting():
    slot = resolve_duration_slot(5, [4, 8, 12])
    assert slot.seconds == 8
    assert slot.total_seconds == 5
    assert slot.adjustment == "up"
    assert slot.needs_confirmation is True


def test_total_below_smallest_slot_rounds_up_to_smallest():
    slot = resolve_duration_slot(2, [4, 8, 12])
    assert slot.seconds == 4
    assert slot.adjustment == "up"


def test_total_above_largest_slot_falls_back_to_largest():
    slot = resolve_duration_slot(20, [4, 8, 12])
    assert slot.seconds == 12
    assert slot.total_seconds == 20
    assert slot.adjustment == "down"
    assert slot.needs_confirmation is True


def test_empty_capability_passes_total_through_without_confirmation():
    slot = resolve_duration_slot(5, [])
    assert slot.seconds == 5
    assert slot.adjustment == "unconstrained"
    assert slot.needs_confirmation is False


def test_unsorted_capability_list_is_handled():
    slot = resolve_duration_slot(5, [12, 4, 8])
    assert slot.seconds == 8
    assert slot.adjustment == "up"


def test_duplicate_slots_do_not_affect_choice():
    slot = resolve_duration_slot(5, [4, 8, 8, 12])
    assert slot.seconds == 8
    assert slot.adjustment == "up"


def test_single_slot_capability():
    assert resolve_duration_slot(3, [6]).seconds == 6
    assert resolve_duration_slot(6, [6]).adjustment == "exact"
    assert resolve_duration_slot(9, [6]).adjustment == "down"


def test_non_integer_total_rounds_up_to_fitting_slot():
    """非整数秒总时长按容量语义取能装下它的档位，不做截断式归一化。"""
    slot = resolve_duration_slot(4.5, [4, 8, 12])
    assert slot.seconds == 8
    assert slot.adjustment == "up"


def test_slot_warning_carries_key_and_params_when_adjusted():
    up = resolve_duration_slot(5, [4, 8, 12]).warning(model="veo-3")
    assert up == {
        "key": "ref_duration_rounded_up",
        "params": {"total": 5, "duration": 8, "model": "veo-3"},
    }
    down = resolve_duration_slot(20, [4, 8, 12]).warning(model="veo-3")
    assert down == {
        "key": "ref_duration_exceeded",
        "params": {"total": 20, "duration": 12, "model": "veo-3"},
    }


def test_slot_warning_is_none_when_not_adjusted():
    assert resolve_duration_slot(8, [4, 8, 12]).warning(model="veo-3") is None
    assert resolve_duration_slot(8, []).warning(model="veo-3") is None
