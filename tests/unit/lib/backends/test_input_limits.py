"""Effective input-count caps use lower channel/entry limits, whole-dimension overrides, and unsupported blocked paths."""

from __future__ import annotations

import pytest

from lib.backends.input_limits import InputLimit, effective_input_limit


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ((3, 5), InputLimit.at_most(3)),
        ((None, 5), InputLimit.at_most(5)),
        ((0, 5), InputLimit.at_most(0)),
        ((None, None), InputLimit.unrecorded()),
    ],
)
def test_the_tighter_declared_side_wins(declared: tuple[int | None, int | None], expected: InputLimit[int]):
    assert effective_input_limit(path_supported=True, declared=declared) == expected


def test_an_override_replaces_the_two_sided_result_but_cannot_open_an_unsupported_path():
    assert effective_input_limit(path_supported=True, declared=(3, 5), override=9) == InputLimit.at_most(9)
    assert effective_input_limit(path_supported=False, declared=(3, 5), override=9) == InputLimit.unsupported()


@pytest.mark.parametrize(
    ("limit", "accepts", "bound", "shown"),
    [
        (InputLimit.unrecorded(), True, None, None),
        (InputLimit.at_most(4), True, 4, 4),
        (InputLimit.at_most(0), False, None, 0),
        (InputLimit.unsupported(), False, None, 0),
    ],
)
def test_each_state_reads_the_same_way_everywhere(
    limit: InputLimit[int], accepts: bool, bound: int | None, shown: int | None
):
    assert (limit.accepts_input, limit.clamp_bound(), limit.to_json()) == (accepts, bound, shown)


def test_seconds_limits_take_the_same_rules():
    assert effective_input_limit(path_supported=True, declared=(15.0, 12.5)) == InputLimit.at_most(12.5)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, InputLimit.unrecorded()), (0, InputLimit.at_most(0)), (4, InputLimit.at_most(4))],
)
def test_from_json_reads_back_the_interface_shape(value: int | None, expected: InputLimit[int]):
    """Persisted API-shaped effective values preserve decisions: unsupported/zero both ``0``, rejecting without cropping."""
    limit = InputLimit.from_json(value)
    assert limit == expected
    assert limit.to_json() == value
    assert InputLimit.from_json(InputLimit.unsupported().to_json()).accepts_input is False
