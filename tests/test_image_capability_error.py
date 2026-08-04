"""ImageCapabilityError 携带稳定 code + 上下文 params。"""

import pytest

from lib.image_backends import ImageCapabilityError

pytestmark = pytest.mark.unit


def test_carries_code_and_params():
    err = ImageCapabilityError("image_endpoint_mismatch_no_i2i", model="dall-e-3")
    assert err.code == "image_endpoint_mismatch_no_i2i"
    assert err.params == {"model": "dall-e-3"}
    assert isinstance(err, RuntimeError)


def test_str_is_code_for_logging():
    err = ImageCapabilityError("image_capability_missing_t2i", provider="x", model="y")
    assert str(err) == "image_capability_missing_t2i"
