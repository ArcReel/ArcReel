"""Reference limits fail with actionable, backward-compatible messages in every locale."""

import pytest

from lib.i18n import SUPPORTED_LOCALES, _


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
@pytest.mark.parametrize("code", ["image_reference_images_exceeded", "video_reference_images_exceeded"])
def test_limit_explanation_supports_new_and_historical_task_params(locale, code):
    params = {"model": "test-model", "limit": 1, "count": 2}
    assert "Alice" in _(code, locale, **params, names="Alice")
    assert "#2" in _(code, locale, **params)
