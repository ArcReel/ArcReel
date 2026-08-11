"""参考生视频 unit 定桶判据与查找（lib/reference_video/units.py）。"""

import pytest

from lib.reference_video.units import find_reference_unit, reference_unit_video_bucket, reference_video_bucket

pytestmark = pytest.mark.unit


def test_reference_video_bucket_splits_by_references():
    assert reference_video_bucket(with_references=True) == "r2v"
    assert reference_video_bucket(with_references=False) == "i2v"


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ({"references": [{"type": "character", "name": "A"}]}, "r2v"),
        ({"references": []}, "i2v"),
        ({}, "i2v"),
        (None, "i2v"),
    ],
)
def test_reference_unit_video_bucket_by_declared_references(unit, expected):
    assert reference_unit_video_bucket(unit) == expected


def test_find_reference_unit_always_reads_video_units():
    script = {
        "video_units": [{"unit_id": "E1U1"}],
        "reference_units": [{"unit_id": "E1U2"}],
    }
    assert find_reference_unit(script, "E1U1") == {"unit_id": "E1U1"}
    assert find_reference_unit(script, "E1U2") is None
    assert find_reference_unit(script, "E9U9") is None


def test_find_reference_unit_skips_non_dict_entries():
    script = {"video_units": ["oops", {"unit_id": "E1U1"}]}
    assert find_reference_unit(script, "E1U1") == {"unit_id": "E1U1"}
