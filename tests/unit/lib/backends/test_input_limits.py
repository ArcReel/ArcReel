"""输入数量类能力的生效上限：两侧取较小值，能力覆盖整维替换，路径不通时为不支持。"""

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
    """存成接口形状的生效值读回后判定不变：不支持与上限为 0 同为 ``0``，都不接受、不裁剪。"""
    limit = InputLimit.from_json(value)
    assert limit == expected
    assert limit.to_json() == value
    assert InputLimit.from_json(InputLimit.unsupported().to_json()).accepts_input is False
