"""Definition-format JSON Schema contract: valid itself and aligned with runtime data structures."""

from __future__ import annotations

from dataclasses import fields

from jsonschema import Draft202012Validator

from arcreel_market_core.endpoint_definition import CURRENT_SCHEMA_VERSION, load_schema
from arcreel_market_core.endpoint_definition.validator import MEDIA_TYPE_RULES
from arcreel_market_core.video_backend_contract import VideoCapabilities


def test_schema_is_a_valid_2020_12_schema():
    Draft202012Validator.check_schema(load_schema())


def test_schema_id_carries_the_current_version():
    assert load_schema()["$id"].endswith(f"/{CURRENT_SCHEMA_VERSION}.json")


def test_capabilities_cover_every_media_type():
    """The capability section is the sole capability source, accepting the union of media-type fields; video names match VideoCapabilities.

    Changing VideoCapabilities without this leaves definitions missing a declarable capability.
    """
    declared = set(load_schema()["$defs"]["capabilities"]["properties"])
    assert declared == {name for rules in MEDIA_TYPE_RULES.values() for name in rules.capabilities}
    assert {field.name for field in fields(VideoCapabilities)} <= declared
