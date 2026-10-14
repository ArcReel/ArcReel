"""图片生效能力：模型条目与调用通道都满足的部分。"""

from __future__ import annotations

import pytest

from lib.backends.image_backends.base import BOTH_IMAGE_CAPABILITIES, ImageCapability
from lib.backends.image_capabilities import effective_image_capabilities
from lib.catalog import query
from tests.catalog_support import install_catalog, with_entry_facts

T2I = ImageCapability.TEXT_TO_IMAGE
I2I = ImageCapability.IMAGE_TO_IMAGE


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [("viduq2", {T2I, I2I}), ("viduq1", {I2I})],
)
def test_builtin_model_takes_entry_facts_within_channel(model_id, expected):
    caps = effective_image_capabilities(BOTH_IMAGE_CAPABILITIES, model_id=model_id, builtin_provider="vidu")
    assert caps == expected


def test_builtin_entry_facts_follow_catalog(monkeypatch: pytest.MonkeyPatch):
    install_catalog(monkeypatch, with_entry_facts(query.factory_snapshot(), "vidu", "viduq2", text_to_image=False))
    caps = effective_image_capabilities(BOTH_IMAGE_CAPABILITIES, model_id="viduq2", builtin_provider="vidu")
    assert caps == {I2I}


@pytest.mark.parametrize(
    ("channel", "model_id", "expected"),
    [
        (BOTH_IMAGE_CAPABILITIES, "qwen-image-edit-plus", {I2I}),
        ({T2I}, "qwen-image-edit-plus", set()),
        ({T2I}, "relay-image-x", {T2I}),
    ],
)
def test_custom_model_matches_entry_by_name(channel, model_id, expected):
    caps = effective_image_capabilities(channel, model_id=model_id, builtin_provider=None)
    assert caps == expected
