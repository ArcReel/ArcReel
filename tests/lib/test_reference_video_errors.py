import pytest

from lib.reference_video.errors import ProviderUnsupportedFeatureError

pytestmark = pytest.mark.unit


def test_provider_unsupported_feature_error_carries_feature():
    err = ProviderUnsupportedFeatureError(provider="sora", feature="multi_reference")
    assert err.provider == "sora"
    assert err.feature == "multi_reference"
