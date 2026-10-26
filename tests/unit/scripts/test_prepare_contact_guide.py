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
    head_point,
    performed_reaction_sequence,
    render_depth,
    segment_box_distance,
    solve_elbow,
    supported_reaction_sequence,
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


def test_reaction_leads_with_creature_and_keeps_contact_and_planted_feet():
    frames = contact_sequence(reaction=True)
    assert frames[2].creature_dip > 0
    assert frames[2].body_angle == 0
    assert frames[2].bag == frames[0].bag
    assert frames[10].body_angle > 0
    assert frames[10].bag[1] > frames[0].bag[1]
    assert frames[29].creature_dip == 0
    assert frames[29].body_angle > 0
    assert frames[-1] == contact_sequence()[-1]
    metrics = validate_sequence(frames)
    assert metrics["max_contact_gap"] == 0
    assert metrics["min_arm_bag_clearance"] > 0
    first = render_depth(frames[0])
    middle = render_depth(frames[18])
    assert np.array_equal(first[180:], middle[180:])
    assert not np.array_equal(first[:120], middle[:120])


def test_expressive_source_yields_then_supports_before_looking_at_culprit():
    frames = contact_sequence(expressive=True)
    assert frames[2].creature_dip > 0
    assert frames[2].body_angle == 0
    assert frames[16].shoulder[0] < frames[0].shoulder[0] - 10
    assert frames[16].head_angle == frames[CONTACT_FRAME].head_angle == 0
    assert frames[44].head_angle < 0
    assert frames[-1].body_angle == 0
    assert frames[-1].palm == contact_sequence()[-1].palm
    metrics = validate_sequence(frames)
    assert metrics["max_contact_gap"] == 0
    assert metrics["min_arm_bag_clearance"] > 0
    first = render_depth(frames[0])
    assert np.array_equal(first[180:], render_depth(frames[16])[180:])
    assert np.array_equal(first[180:], render_depth(frames[-1])[180:])
    assert not np.array_equal(render_depth(frames[-1])[:70], render_depth(contact_sequence()[-1])[:70])


def test_head_tilt_preserves_its_neck_pivot_and_distances():
    pivot, face = (478, 108), (475, 65)
    assert head_point(pivot, -0.12) == pivot
    assert math.dist(head_point(face, -0.12), pivot) == pytest.approx(math.dist(face, pivot))


def test_supported_continuation_preserves_join_and_contact_before_reaction():
    frames = supported_reaction_sequence()
    assert frames[0] == contact_sequence(expressive=True)[-1]
    assert frames[33].creature_dip == 9
    assert frames[33].body_angle == 0
    assert frames[41].body_angle > 0
    assert frames[-1].body_angle == 0
    metrics = validate_sequence(frames, continuation=True)
    assert metrics["contact_frames"] == [0, 96]
    assert metrics["max_contact_gap"] == 0
    assert metrics["min_arm_bag_clearance"] > 0
    assert all(frame.bag == frames[0].bag and frame.palm == frames[0].palm for frame in frames)


def test_continuation_rejects_contact_loss_at_its_first_frame():
    frames = supported_reaction_sequence()
    frames[0] = replace(frames[0], bag=(397, 204))
    with pytest.raises(ValueError, match="loses underside contact"):
        validate_sequence(frames, continuation=True)


def test_performed_reaction_moves_supported_load_after_creature_impulse():
    frames = performed_reaction_sequence()
    assert frames[0] == contact_sequence(expressive=True)[-1]
    assert frames[7].creature_angle < 0 < frames[12].creature_angle
    assert frames[7].creature_dip < 0 < frames[12].creature_dip
    assert frames[12].body_angle == 0
    assert frames[17].shoulder[0] - frames[0].shoulder[0] > 13
    assert frames[17].head_angle > frames[0].head_angle
    assert frames[-1].body_angle == frames[-1].creature_angle == frames[-1].creature_dip == 0
    metrics = validate_sequence(frames, performed=True)
    assert metrics["contact_frames"] == [0, 48]
    assert metrics["max_contact_gap"] < 1e-10
    assert metrics["max_bone_length_error"] < 1e-10
    for frame in frames:
        assert np.subtract(frame.bag, frame.palm) == pytest.approx(np.subtract(frames[0].bag, frames[0].palm))
    first, peak = render_depth(frames[0]), render_depth(frames[17])
    assert np.array_equal(first[180:], peak[180:])
    assert not np.array_equal(first[:130], peak[:130])


def test_performed_recoil_cannot_leave_the_supported_load_behind():
    frames = performed_reaction_sequence()
    frames[17] = replace(frames[17], bag=frames[0].bag)
    with pytest.raises(ValueError, match=r"penetrates|loses underside contact"):
        validate_sequence(frames, performed=True)


def test_viewport_matches_same_camera_crop_without_moving_geometry():
    frame = supported_reaction_sequence()[0]
    full = render_depth(frame, width=768, height=448)
    cropped = render_depth(frame, width=480, height=280, viewport=(240, 0, 480, 280))
    assert np.array_equal(cropped, full[:280, 240:720])


@pytest.mark.parametrize(
    "change",
    [
        {"body_angle": math.nan},
        {"body_angle": 0.1},
        {"creature_dip": -1},
        {"body_angle": -0.3},
        {"head_angle": math.nan},
        {"head_angle": -0.3},
        {"creature_angle": math.nan},
        {"creature_angle": 0.5},
    ],
)
def test_unsupported_reaction_cannot_be_rendered_as_valid_geometry(change):
    frames = contact_sequence(reaction=True)
    frames[18] = replace(frames[18], **change)
    with pytest.raises(ValueError, match="outside the diagnostic"):
        validate_sequence(frames)


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
