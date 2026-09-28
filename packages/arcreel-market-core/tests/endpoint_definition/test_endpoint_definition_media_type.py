"""定义的媒体类型按 ``kind`` 读：声明式恒为视频，ComfyUI 由定义自己声明，名录外的 kind 拒绝。"""

from __future__ import annotations

import pytest
from definition_factories import comfyui_endpoint_definition, custom_endpoint_definition

from arcreel_market_core.endpoint_definition import definition_media_type


def test_declarative_definitions_are_video():
    assert definition_media_type(custom_endpoint_definition()) == "video"


def test_comfyui_definitions_declare_their_own_media_type():
    assert definition_media_type(comfyui_endpoint_definition(media_type="image")) == "image"


def test_media_type_of_an_unsupported_kind_is_refused():
    definition = custom_endpoint_definition(kind="unregistered")

    with pytest.raises(ValueError, match="unsupported endpoint definition kind"):
        definition_media_type(definition)
