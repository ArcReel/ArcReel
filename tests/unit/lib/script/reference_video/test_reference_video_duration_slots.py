"""视频时长向上取档规则（容量语义）与两条路线共用的时长投影。"""

import pytest

from lib.script.reference_video.duration_slots import project_request_duration, resolve_duration_slot


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


def test_projection_narrows_to_the_fitting_tier_and_asks_for_confirmation_once():
    asked = project_request_duration(planned_duration_seconds=4, supported_durations=(4, 8, 12))
    accepted = project_request_duration(
        planned_duration_seconds=4,
        supported_durations=(4, 8, 12),
        narration_duration_floor=6.2,
        confirmed_request_duration_seconds=8,
    )
    exact = project_request_duration(planned_duration_seconds=8, supported_durations=(4, 8, 12))

    assert (asked.slot is not None and asked.slot.seconds, asked.problem) == (4, None)
    assert accepted.slot is not None
    assert (accepted.slot.seconds, accepted.duration_input, accepted.problem) == (8, 6.2, None)
    assert exact.problem is None
    floor_only = project_request_duration(
        planned_duration_seconds=4,
        supported_durations=(4, 8, 12),
        narration_duration_floor=6.2,
    )
    assert floor_only.problem == "confirmation_required"


def test_projection_reports_replanning_instead_of_truncating():
    result = project_request_duration(planned_duration_seconds=20, supported_durations=(4, 8, 12))

    assert result.slot is not None
    assert (result.slot.seconds, result.slot.adjustment, result.problem) == (12, "down", "needs_replan")


def test_projection_treats_an_unflagged_empty_tier_set_as_a_declaration_gap():
    result = project_request_duration(planned_duration_seconds=8, supported_durations=())

    assert (result.slot, result.endpoint_fixed, result.problem) == (None, False, "supported_durations_missing")


def test_projection_passes_the_planned_duration_through_endpoint_fixed_durations():
    result = project_request_duration(
        planned_duration_seconds=10,
        supported_durations=(),
        duration_endpoint_fixed=True,
    )

    assert result.problem is None
    assert result.endpoint_fixed is True
    assert result.slot is not None
    assert (result.slot.seconds, result.slot.adjustment) == (10, "unconstrained")
    assert result.slot.needs_confirmation is False


def test_projection_refuses_tts_delivery_when_the_endpoint_fixes_the_duration():
    result = project_request_duration(
        planned_duration_seconds=10,
        supported_durations=(),
        narration_duration_floor=14.5,
        duration_endpoint_fixed=True,
        uses_tts=True,
    )

    assert (result.slot, result.problem) == (None, "tts_duration_endpoint_fixed")
    assert result.duration_input == 14.5


def test_projection_waives_confirmation_for_option_less_legacy_requests():
    result = project_request_duration(
        planned_duration_seconds=4,
        supported_durations=(4, 8, 12),
        narration_duration_floor=6.2,
        confirmation_waived=True,
    )

    assert result.problem is None


def test_projection_ignores_non_positive_and_boolean_tier_values():
    result = project_request_duration(planned_duration_seconds=4, supported_durations=(0, -8, True, 12))

    assert result.slot is not None
    assert result.slot.seconds == 12


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"planned_duration_seconds": 0}, "planned_duration_seconds"),
        ({"planned_duration_seconds": True}, "planned_duration_seconds"),
        ({"planned_duration_seconds": 8, "narration_duration_floor": 0.0}, "narration_duration_floor"),
        ({"planned_duration_seconds": 8, "narration_duration_floor": float("inf")}, "narration_duration_floor"),
        ({"planned_duration_seconds": 8, "current_visual_duration_seconds": 0}, "current_visual_duration_seconds"),
    ],
)
def test_projection_rejects_non_positive_duration_inputs(kwargs: dict[str, object], expected: str):
    with pytest.raises(ValueError, match=expected):
        project_request_duration(supported_durations=(4, 8), **kwargs)
