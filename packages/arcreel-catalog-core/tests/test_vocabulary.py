"""封闭集合的自洽：每个选择维度都登记了适用的媒体类型。"""

from arcreel_catalog_core import DIMENSION_MEDIA_TYPES, DIMENSIONS, MEDIA_TYPES


def test_every_dimension_has_applicable_media_types():
    assert set(DIMENSION_MEDIA_TYPES) == set(DIMENSIONS)
    assert all(media_types and media_types <= set(MEDIA_TYPES) for media_types in DIMENSION_MEDIA_TYPES.values())
