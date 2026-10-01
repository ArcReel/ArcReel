"""声明式图片定义的校验：变量、素材来源、能力与产物提取按媒体类型分集合，越界即报错。"""

from __future__ import annotations

from typing import Any

import pytest
from definition_factories import custom_endpoint_definition, image_endpoint_definition

from arcreel_market_core.endpoint_definition import DefinitionDiagnostics, validate_definition


def _codes(diagnostics: DefinitionDiagnostics) -> list[tuple[str, str]]:
    return [(issue.path, issue.code.value) for issue in diagnostics.errors]


def test_image_definition_of_the_async_task_protocol_is_accepted():
    diagnostics = validate_definition(image_endpoint_definition())

    assert diagnostics.errors == ()
    assert diagnostics.warnings == ()


def test_unknown_media_type_is_rejected():
    definition = image_endpoint_definition(media_type="audio")

    assert ("media_type", "invalid_enum_value") in _codes(validate_definition(definition))


def _with_duration_in_body(definition: dict[str, Any]) -> None:
    definition["submit"]["body"]["duration"] = "{{ duration }}"


def _with_audio_flag_in_body(definition: dict[str, Any]) -> None:
    definition["submit"]["body"]["audio"] = "{{ generate_audio }}"


def _with_first_frame_input(definition: dict[str, Any]) -> None:
    definition["inputs"] = {"first_frame": {"source": "start_image", "encoding": "data_uri"}}
    definition["submit"]["body"]["image"] = "{{ inputs.first_frame }}"


def _with_first_frame_capability(definition: dict[str, Any]) -> None:
    definition["capabilities"]["first_frame"] = True


def _with_video_url_extract(definition: dict[str, Any]) -> None:
    definition["poll"]["extract"]["video_url"] = ["$.data.result.video"]


def _with_duration_enum_map(definition: dict[str, Any]) -> None:
    definition["enum_maps"] = {"duration": {"5": 5}}


def _with_audio_default(definition: dict[str, Any]) -> None:
    definition["defaults"] = {"generate_audio": False}


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (_with_duration_in_body, "submit.body.duration"),
        (_with_audio_flag_in_body, "submit.body.audio"),
        (_with_first_frame_input, "inputs.first_frame.source"),
        (_with_first_frame_capability, "capabilities.first_frame"),
        (_with_video_url_extract, "poll.extract.video_url"),
        (_with_duration_enum_map, "enum_maps.duration"),
        (_with_audio_default, "defaults.generate_audio"),
    ],
)
def test_video_only_fields_in_an_image_definition_are_rejected(mutate, path: str):
    definition = image_endpoint_definition()
    mutate(definition)

    assert (path, "media_type_field_not_allowed") in _codes(validate_definition(definition))


def _with_text_to_image_capability(definition: dict[str, Any]) -> None:
    definition["capabilities"]["text_to_image"] = True


def _with_image_url_extract(definition: dict[str, Any]) -> None:
    definition["poll"]["extract"]["image_url"] = ["$.image"]


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (_with_text_to_image_capability, "capabilities.text_to_image"),
        (_with_image_url_extract, "poll.extract.image_url"),
    ],
)
def test_image_only_fields_in_a_video_definition_are_rejected(mutate, path: str):
    definition = custom_endpoint_definition()
    mutate(definition)

    assert (path, "media_type_field_not_allowed") in _codes(validate_definition(definition))


def test_image_definition_without_an_image_path_in_poll_is_rejected():
    definition = image_endpoint_definition()
    del definition["poll"]["extract"]["image_url"]

    assert ("poll.extract", "artifact_extract_missing") in _codes(validate_definition(definition))


def test_image_definition_reads_the_image_from_the_result_request_when_it_has_one():
    definition = image_endpoint_definition()
    del definition["poll"]["extract"]["image_url"]
    definition["poll"]["extract"]["result_id"] = ["$.data.result_id"]
    definition["result"] = {
        "method": "GET",
        "url": "{{ base_url }}/v1/results/{{ result_id }}",
        "extract": {"error": ["$.error"]},
    }
    assert ("result.extract", "artifact_extract_missing") in _codes(validate_definition(definition))

    definition["result"]["extract"]["image_url"] = ["$.url"]
    assert validate_definition(definition).valid


@pytest.mark.parametrize("capabilities", [{}, {"text_to_image": False}])
def test_image_definition_must_declare_an_image_capability(capabilities: dict[str, object]):
    definition = image_endpoint_definition(capabilities=capabilities)

    assert ("capabilities", "capability_not_declared") in _codes(validate_definition(definition))


def test_image_definition_may_default_its_image_variables():
    definition = image_endpoint_definition()
    definition["defaults"] = {"aspect_ratio": "1:1", "resolution": "1K", "seed": 7}
    definition["enum_maps"] = {"resolution": {"1K": "1k", "2K": "2k"}}

    assert validate_definition(definition).valid
