import pytest

from lib.reference_video.errors import (
    MissingReferenceError,
    ProviderUnsupportedFeatureError,
)


def test_missing_reference_error_carries_details():
    err = MissingReferenceError(missing=[("character", "张三"), ("scene", "酒馆")])
    assert err.missing == [("character", "张三"), ("scene", "酒馆")]
    assert "张三" in str(err)


def test_missing_reference_error_empty():
    with pytest.raises(ValueError):
        MissingReferenceError(missing=[])


def test_provider_unsupported_feature_error_carries_feature():
    err = ProviderUnsupportedFeatureError(provider="sora", feature="multi_reference")
    assert err.provider == "sora"
    assert err.feature == "multi_reference"
