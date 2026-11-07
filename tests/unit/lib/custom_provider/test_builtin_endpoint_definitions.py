"""Load gate for shipped declarative definitions: every packaged definition passes the shared validator; invalid definitions fail at import.

Traversal cases permanently guard shipped definitions, which are code: CI must expose malformed changes immediately, rather than wait for a user
to select the endpoint and generate. Frontend example templates are also shipped, so include them in cross-layer traversal, following i18n consistency tests
that read frontend source.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from arcreel_market_core.endpoint_definition import validate_definition
from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoAudioMode
from lib.custom_provider import CUSTOM_ENDPOINT_KEY_PREFIX
from lib.custom_provider.builtin_definitions import (
    BUILTIN_DEFINITION_AUTHOR,
    BUILTIN_DEFINITIONS_DIR,
    BuiltinDefinitionError,
    declarative_family,
    declarative_request_path,
    declarative_video_capabilities,
    load_builtin_definitions,
)
from lib.custom_provider.endpoints import (
    ENDPOINT_REGISTRY,
    EndpointSpec,
    declarative_endpoint_spec,
    endpoint_spec_to_dict,
    merge_builtin_definitions,
)
from lib.infra.validation_messages import default_translate

REPO_ROOT = Path(__file__).resolve().parents[4]
EXAMPLE_TEMPLATES_DIR = REPO_ROOT / "frontend" / "src" / "data" / "example-templates"


def _shipped_definition_files() -> list[Path]:
    """All packaged definitions: built-in endpoints plus frontend example templates."""
    return sorted(BUILTIN_DEFINITIONS_DIR.glob("*.json")) + sorted(EXAMPLE_TEMPLATES_DIR.glob("*.json"))


def _example_template() -> dict[str, Any]:
    return json.loads((EXAMPLE_TEMPLATES_DIR / "generic-submit-poll.json").read_text(encoding="utf-8"))


def _write(directory: Path, key: str, document: object) -> Path:
    path = directory / f"{key}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# Traversal gate.


@pytest.mark.parametrize("path", _shipped_definition_files(), ids=lambda p: p.name)
def test_shipped_definition_passes_the_shared_validator(path: Path):
    diagnostics = validate_definition(json.loads(path.read_text(encoding="utf-8")))
    assert diagnostics.valid, [issue.to_payload(default_translate) for issue in diagnostics.errors]


def test_example_templates_ship_one_video_and_one_image_skeleton():
    """Example templates provide one generic submit-and-poll definition per media type, each listed in the new-form template selector."""
    assert [p.name for p in sorted(EXAMPLE_TEMPLATES_DIR.glob("*.json"))] == [
        "generic-image-submit-poll.json",
        "generic-submit-poll.json",
    ]


def test_shipped_builtin_definitions_are_registered_as_declarative_endpoints():
    for key in load_builtin_definitions():
        spec = ENDPOINT_REGISTRY[key]
        assert spec.kind == "declarative"
        assert spec.definition is not None


# ---------------------------------------------------------------- fail-fast


def test_invalid_definition_fails_loading(tmp_path: Path):
    document = _example_template()
    del document["submit"]["extract"]
    _write(tmp_path, "broken-video", document)
    with pytest.raises(BuiltinDefinitionError, match="未通过校验"):
        load_builtin_definitions(tmp_path)


def test_malformed_json_fails_loading(tmp_path: Path):
    (tmp_path / "broken-video.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(BuiltinDefinitionError, match="不是合法 JSON"):
        load_builtin_definitions(tmp_path)


def test_key_taking_the_custom_endpoint_prefix_fails_loading(tmp_path: Path):
    _write(tmp_path, f"{CUSTOM_ENDPOINT_KEY_PREFIX}7", _example_template())
    with pytest.raises(BuiltinDefinitionError, match="自定义端点前缀"):
        load_builtin_definitions(tmp_path)


def test_foreign_author_fails_loading(tmp_path: Path):
    document = _example_template()
    document["meta"]["author"] = "someone-else"
    _write(tmp_path, "foreign-video", document)
    with pytest.raises(BuiltinDefinitionError, match=re.escape("meta.author")):
        load_builtin_definitions(tmp_path)


def test_missing_directory_fails_loading(tmp_path: Path):
    with pytest.raises(BuiltinDefinitionError, match="目录不存在"):
        load_builtin_definitions(tmp_path / "absent")


def test_key_colliding_with_a_python_endpoint_fails_merging(tmp_path: Path):
    """Replacing an implementation under the same key is a migration when absorbing Python into declarations; it must not happen silently by adding a directory file."""
    _write(tmp_path, "newapi-video", _example_template())
    registry: dict[str, EndpointSpec] = {"newapi-video": ENDPOINT_REGISTRY["newapi-video"]}
    with pytest.raises(BuiltinDefinitionError, match="重复"):
        merge_builtin_definitions(registry, tmp_path)


def test_shipped_definitions_are_authored_by_arcreel():
    for definition in load_builtin_definitions().values():
        assert definition["meta"]["author"] == BUILTIN_DEFINITION_AUTHOR


# Derived endpoint metadata.


def test_declarative_spec_derives_catalog_fields():
    spec = declarative_endpoint_spec("newapi-video", _example_template())
    descriptor = endpoint_spec_to_dict(spec)
    assert descriptor["kind"] == "declarative"
    assert descriptor["display_name"] == "Generic Submit + Poll"
    assert descriptor["display_name_key"] == ""
    assert descriptor["media_type"] == "video"
    assert descriptor["family"] == "newapi"
    assert descriptor["request_method"] == "POST"
    assert descriptor["request_path_template"] == "/v1/video/generations"
    assert "definition" not in descriptor


@pytest.mark.parametrize(
    "key",
    [
        "newapi-video",
        "v2-video-generations",
        "minimax-hailuo-v1",
        "minimax-hailuo-v1-fast",
        "minimax-s2v-01",
        "minimax-h3",
    ],
)
def test_migrated_builtin_endpoints_are_declarative(key: str):
    descriptor = endpoint_spec_to_dict(ENDPOINT_REGISTRY[key])
    assert descriptor["kind"] == "declarative"
    assert descriptor["display_name"]
    assert descriptor["display_name_key"] == ""


def test_spec_kind_is_read_from_the_definition():
    """Read ``kind`` from the definition itself, not from whether a definition exists; endpoints of the second kind also hold a definition."""
    template = _example_template()
    template["kind"] = "comfyui"
    template["media_type"] = "video"

    assert declarative_endpoint_spec("demo-video", template).kind == "comfyui"


def test_unmigrated_endpoints_stay_python_in_the_catalog():
    descriptor = endpoint_spec_to_dict(ENDPOINT_REGISTRY["openai-video"])
    assert descriptor["kind"] == "python"
    assert descriptor["display_name"] is None
    assert descriptor["display_name_key"]


def test_declarative_capabilities_follow_the_definition():
    spec = declarative_endpoint_spec("demo-video", _example_template())
    assert spec.video_caps_for_model is not None
    caps = spec.video_caps_for_model("any-model", None)
    assert caps.first_frame is True
    assert caps.last_frame is False
    assert caps.max_reference_images == 0
    assert caps.reference_audio_mode is ReferenceAudioMode.NONE
    assert caps.audio_track is VideoAudioMode.CONTROLLABLE
    assert caps.reference_route_audio_track is None
    assert spec.end_image_capable is False
    assert spec.reference_audio_capable is False


@pytest.mark.parametrize(
    ("required", "expected_text_to_video"),
    [(True, False), (False, True)],
)
def test_text_to_video_is_derived_from_required_image_inputs(required: bool, expected_text_to_video: bool):
    """Do not use the schema default for this flag: definitions can legally declare only required image inputs without ``text_to_video``.
    Using defaults would claim pure text-to-video support and admit a request shape that cannot render."""
    document = _example_template()
    document["inputs"]["start_image"]["required"] = required
    document["capabilities"].pop("text_to_video", None)

    spec = declarative_endpoint_spec("demo-video", document)

    assert spec.video_caps_for_model is not None
    assert spec.video_caps_for_model("any-model", None).text_to_video is expected_text_to_video


def test_undeclared_capabilities_take_the_schema_defaults_not_the_dataclass_ones():
    """The format contract requires explicit capabilities: omitted capabilities means no material inputs, not the dataclass first-frame-enabled default."""
    document = _example_template()
    del document["capabilities"]
    del document["inputs"]
    del document["submit"]["body"]["image"]
    caps = declarative_video_capabilities(document)
    assert caps.first_frame is False
    assert caps.max_reference_images == 0


def test_declared_capabilities_reach_the_endpoint_flags():
    document = _example_template()
    document["capabilities"] = {
        "first_frame": True,
        "last_frame": True,
        "reference_audio_mode": "direct",
        "max_reference_audio_count": 2,
    }
    spec = declarative_endpoint_spec("demo-video", document)
    assert spec.end_image_capable is True
    assert spec.reference_audio_capable is True


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("newapi-video", "newapi"),
        ("v2-video-generations", "v2"),
        ("minimax", "minimax"),
    ],
)
def test_family_is_the_first_key_segment(key: str, expected: str):
    assert declarative_family(key) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("{{ base_url }}/v1/video/generations", "/v1/video/generations"),
        ("{{base_url}}/v1/video/generations", "/v1/video/generations"),
        ("https://vendor.example/v1/video?mode=fast", "/v1/video?mode=fast"),
    ],
)
def test_request_path_strips_the_base_url_placeholder(url: str, expected: str):
    assert declarative_request_path({"submit": {"url": url}}) == expected
