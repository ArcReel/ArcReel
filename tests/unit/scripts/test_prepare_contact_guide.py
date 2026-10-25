"""Contact controls must describe reachable, attached motion before inference."""

import math
from dataclasses import replace

import numpy as np
import pytest

from scripts.prepare_contact_guide import (
    CONTACT_FRAME,
    FOREARM,
    UPPER_ARM,
    contact_sequence,
    render_depth,
    segment_box_distance,
    solve_elbow,
    validate_sequence,
)


def test_arm_hits_target_without_changing_segment_lengths():
    for target in [(18, 110), (-29, 108), (-50, 45)]:
        elbow = solve_elbow((0, 0), target, UPPER_ARM, FOREARM, (-100, 100))
        assert math.dist((0, 0), elbow) == pytest.approx(UPPER_ARM)
        assert math.dist(elbow, target) == pytest.approx(FOREARM)


@pytest.mark.parametrize("target", [(0, 0), (1, 0), (0, 124), (123, 0)])
def test_unreachable_arm_target_is_rejected_not_clamped(target):
    with pytest.raises(ValueError, match="unreachable"):
        solve_elbow((0, 0), target, UPPER_ARM, FOREARM, (-100, 100))


def test_complete_catch_preserves_arm_and_contact_through_settle():
    frames = contact_sequence()
    metrics = validate_sequence(frames)
    assert metrics["max_bone_length_error"] < 1e-10
    assert frames[10].bag[1] > frames[0].bag[1]
    assert frames[8].wrist == frames[0].wrist
    for frame in frames[CONTACT_FRAME:]:
        assert abs(frame.palm[0] - frame.bag[0]) < 32
        assert frame.palm[1] - 6 == pytest.approx(frame.bag[1] + 32)
    assert metrics["min_arm_bag_clearance"] > 0
    assert metrics["max_contact_gap"] == 0
    assert metrics["production_qualified"] is False


def test_length_drift_is_rejected_even_when_wrist_reaches_contact():
    frames = contact_sequence()
    frames[-1] = replace(frames[-1], elbow=(357, 193))
    with pytest.raises(ValueError, match="Arm length changes"):
        validate_sequence(frames)


def test_bag_motion_that_breaks_contact_is_rejected():
    frames = contact_sequence()
    frame = frames[-1]
    frames[-1] = replace(frame, bag=(frame.bag[0], frame.bag[1] - 4))
    with pytest.raises(ValueError, match="loses underside contact"):
        validate_sequence(frames)


def test_palm_penetration_cannot_be_accepted_as_contact():
    frames = contact_sequence()
    frame = frames[-1]
    frames[-1] = replace(frame, bag=(frame.bag[0], frame.bag[1] + 4))
    with pytest.raises(ValueError, match="penetrates"):
        validate_sequence(frames)


def test_depth_is_grayscale_and_static_geometry_does_not_pulse():
    frames = contact_sequence()
    first, last = render_depth(frames[0]), render_depth(frames[-1])
    assert first.shape == (224, 384, 3)
    assert np.array_equal(first[:, :, 0], first[:, :, 1])
    assert np.array_equal(first[:, :, 0], first[:, :, 2])
    assert np.array_equal(first[180:, :, :], last[180:, :, :])
    assert not np.array_equal(first, last)
    assert first[90, 190, 0] > first[0, 0, 0]


@pytest.mark.parametrize(
    ("start", "end", "distance"),
    [((-2, 5), (12, 5), 0), ((-2, -2), (-1, -1), math.sqrt(2)), ((12, 2), (12, 9), 2), ((5, 5), (12, 12), 0)],
)
def test_segment_box_distance_detects_crossings_and_near_misses(start, end, distance):
    assert segment_box_distance(start, end, (0, 0), (10, 10)) == pytest.approx(distance)


def test_valid_bone_lengths_do_not_excuse_forearm_through_the_bag():
    frames = contact_sequence()
    frame = frames[-1]
    elbow = solve_elbow(frame.shoulder, frame.wrist, UPPER_ARM, FOREARM, (300, 200))
    frames[-1] = replace(frame, elbow=elbow)
    with pytest.raises(ValueError, match="Arm penetrates"):
        validate_sequence(frames)
