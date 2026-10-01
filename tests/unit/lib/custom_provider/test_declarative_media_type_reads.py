from __future__ import annotations

from arcreel_market_core.endpoint_definition import definition_media_type
from arcreel_market_core.market.entry import project_meta
from lib.custom_provider.endpoint_resolution import derive_mirror_columns
from lib.custom_provider.endpoints import comfyui_endpoint_spec, declarative_endpoint_spec
from tests.factories import comfyui_endpoint_definition, custom_endpoint_definition


def test_declarative_projection_mirror_and_market_use_the_shared_reader():
    definition = custom_endpoint_definition()

    assert (
        definition_media_type(definition)
        == derive_mirror_columns(definition).media_type
        == declarative_endpoint_spec("demo", definition).media_type
        == project_meta(definition)["media_type"]
    )


def test_comfyui_projection_matches_the_shared_reader():
    definition = comfyui_endpoint_definition(media_type="image")

    assert (
        definition_media_type(definition)
        == derive_mirror_columns(definition).media_type
        == comfyui_endpoint_spec("demo", definition).media_type
        == project_meta(definition)["media_type"]
    )
