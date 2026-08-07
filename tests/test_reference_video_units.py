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


@pytest.mark.parametrize(
    ("ad_shots", "expected"),
    [
        ([{"shot_id": "E1S01", "products_in_shot": ["口红"]}], "r2v"),
        ([{"shot_id": "E1S01"}], "i2v"),
        ([], "i2v"),
    ],
)
def test_ad_bucket_follows_member_shots_not_index_cache(ad_shots, expected):
    """ad 定桶以成员镜头现算的参考集为准，索引缓存与镜头冲突时以镜头为准。"""
    stale_cache = {"references": [{"type": "character", "name": "落后于镜头的缓存"}]}
    assert reference_unit_video_bucket(stale_cache, ad_shots=ad_shots) == expected


def test_find_reference_unit_selects_list_by_content_mode():
    script = {
        "video_units": [{"unit_id": "E1U1"}],
        "reference_units": [{"unit_id": "E1U2"}],
    }
    assert find_reference_unit(script, "E1U1", is_ad=False) == {"unit_id": "E1U1"}
    assert find_reference_unit(script, "E1U2", is_ad=True) == {"unit_id": "E1U2"}
    assert find_reference_unit(script, "E1U2", is_ad=False) is None
    assert find_reference_unit(script, "E9U9", is_ad=True) is None


def test_find_reference_unit_skips_non_dict_entries():
    script = {"video_units": ["oops", {"unit_id": "E1U1"}]}
    assert find_reference_unit(script, "E1U1", is_ad=False) == {"unit_id": "E1U1"}
