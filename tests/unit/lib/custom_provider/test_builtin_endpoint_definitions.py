"""随版声明式定义的装载闸门：随包分发的每份定义都过共享校验器，非法定义 import 期即失败。

遍历用例是随版定义的常驻回归：定义文件是代码的一部分，改坏了要在 CI 里当场暴露，而不是等某个
用户挑中该端点发起生成。前端的示例模板同样随版分发，故一并跨层纳入遍历（沿 i18n 一致性测试
读前端源码的先例）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

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
from lib.custom_provider.endpoint_definition import validate_definition
from lib.custom_provider.endpoints import (
    ENDPOINT_REGISTRY,
    EndpointSpec,
    declarative_endpoint_spec,
    endpoint_spec_to_dict,
    merge_builtin_definitions,
)
from lib.video_backends.base import ReferenceAudioMode, VideoAudioMode

REPO_ROOT = Path(__file__).resolve().parents[4]
EXAMPLE_TEMPLATES_DIR = REPO_ROOT / "frontend" / "src" / "data" / "example-templates"


def _shipped_definition_files() -> list[Path]:
    """随包分发的全部定义：内置端点 + 前端示例模板。"""
    return sorted(BUILTIN_DEFINITIONS_DIR.glob("*.json")) + sorted(EXAMPLE_TEMPLATES_DIR.glob("*.json"))


def _example_template() -> dict[str, Any]:
    return json.loads((EXAMPLE_TEMPLATES_DIR / "generic-submit-poll.json").read_text(encoding="utf-8"))


def _write(directory: Path, key: str, document: object) -> Path:
    path = directory / f"{key}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 遍历闸门


@pytest.mark.parametrize("path", _shipped_definition_files(), ids=lambda p: p.name)
def test_shipped_definition_passes_the_shared_validator(path: Path):
    diagnostics = validate_definition(json.loads(path.read_text(encoding="utf-8")))
    assert diagnostics.valid, [issue.to_payload() for issue in diagnostics.errors]


def test_example_template_ships_exactly_one_file():
    """示例模板首期只留「通用提交+轮询」一份：多一份就要回答「新建表单该预填哪一份」。"""
    assert [p.name for p in sorted(EXAMPLE_TEMPLATES_DIR.glob("*.json"))] == ["generic-submit-poll.json"]


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
    with pytest.raises(BuiltinDefinitionError, match="meta.author"):
        load_builtin_definitions(tmp_path)


def test_missing_directory_fails_loading(tmp_path: Path):
    with pytest.raises(BuiltinDefinitionError, match="目录不存在"):
        load_builtin_definitions(tmp_path / "absent")


def test_key_colliding_with_a_python_endpoint_fails_merging(tmp_path: Path):
    """同键换实现是 Python→声明式收编那一刻的迁移动作，不能靠往目录里丢一份文件悄悄发生。"""
    _write(tmp_path, "newapi-video", _example_template())
    registry: dict[str, EndpointSpec] = {"newapi-video": ENDPOINT_REGISTRY["newapi-video"]}
    with pytest.raises(BuiltinDefinitionError, match="重复"):
        merge_builtin_definitions(registry, tmp_path)


def test_shipped_definitions_are_authored_by_arcreel():
    for definition in load_builtin_definitions().values():
        assert definition["meta"]["author"] == BUILTIN_DEFINITION_AUTHOR


# ---------------------------------------------------------------- 派生的端点元数据


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


def test_python_endpoints_stay_python_in_the_catalog():
    descriptor = endpoint_spec_to_dict(ENDPOINT_REGISTRY["newapi-video"])
    assert descriptor["kind"] == "python"
    assert descriptor["display_name"] is None
    assert descriptor["display_name_key"]


def test_declarative_capabilities_follow_the_definition():
    spec = declarative_endpoint_spec("demo-video", _example_template())
    assert spec.video_caps_for_model is not None
    caps = spec.video_caps_for_model("any-model")
    assert caps.first_frame is True
    assert caps.last_frame is False
    assert caps.max_reference_images == 0
    assert caps.reference_audio_mode is ReferenceAudioMode.NONE
    assert caps.audio_track is VideoAudioMode.CONTROLLABLE
    assert caps.reference_route_audio_track is None
    assert spec.end_image_capable is False
    assert spec.reference_audio_capable is False


def test_undeclared_capabilities_take_the_schema_defaults_not_the_dataclass_ones():
    """格式契约是「能力全显式声明」：省略 capabilities 即无素材输入，不是 dataclass 的首帧默认开。"""
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
        ("https://vendor.example/v1/video", "https://vendor.example/v1/video"),
    ],
)
def test_request_path_strips_the_base_url_placeholder(url: str, expected: str):
    assert declarative_request_path({"submit": {"url": url}}) == expected
