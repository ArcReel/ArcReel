"""定义格式的 JSON Schema 契约：自身合法，且与运行时的数据结构对齐。"""

from __future__ import annotations

from dataclasses import fields

from jsonschema import Draft202012Validator

from lib.custom_provider.endpoint_definition import CURRENT_SCHEMA_VERSION, load_schema
from lib.video_backends.base import VideoCapabilities


def test_schema_is_a_valid_2020_12_schema():
    Draft202012Validator.check_schema(load_schema())


def test_schema_id_carries_the_current_version():
    assert load_schema()["$id"].endswith(f"/{CURRENT_SCHEMA_VERSION}.json")


def test_capabilities_mirror_video_capabilities():
    """能力节是能力的唯一来源，改 VideoCapabilities 而不改这里会让定义少一位可声明的能力。"""
    declared = set(load_schema()["$defs"]["capabilities"]["properties"])
    assert declared == {field.name for field in fields(VideoCapabilities)}
