from __future__ import annotations

import pytest

from lib.artifact_manifest import ArtifactBasis, ArtifactBasisDescriptor
from lib.artifact_provenance import (
    build_ad_episode_script_basis,
    build_episode_script_basis,
    build_script_plan_basis,
)


def test_artifact_basis_has_deterministic_canonical_json() -> None:
    first = ArtifactBasis.build(
        "structured-content-test",
        kind_version=2,
        inputs={"z": "雪", "a": [1, True, None]},
    )
    second = ArtifactBasis.build(
        "structured-content-test",
        kind_version=2,
        inputs={"a": [1, True, None], "z": "雪"},
    )

    assert (
        first.normalized_bytes()
        == ('{"inputs":{"a":[1,true,null],"z":"雪"},"kind":"structured-content-test","kind_version":2}').encode()
    )
    assert second.normalized_bytes() == first.normalized_bytes()
    assert second.digest == first.digest


def test_artifact_basis_descriptor_round_trips_strict_source_fact() -> None:
    basis = ArtifactBasis.build("artifact-visual/video-storyboard", kind_version=3, inputs={"frame": "v1"})

    descriptor = ArtifactBasisDescriptor.from_basis(basis)

    assert descriptor.to_dict() == {
        "kind": "artifact-visual/video-storyboard",
        "kind_version": 3,
        "digest": basis.digest,
    }
    assert ArtifactBasisDescriptor.from_dict(descriptor.to_dict()) == descriptor


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"kind": "visual", "kind_version": 1, "digest": "sha256-v1:" + "a" * 64, "extra": True},
        {"kind": "", "kind_version": 1, "digest": "sha256-v1:" + "a" * 64},
        {"kind": "visual", "kind_version": True, "digest": "sha256-v1:" + "a" * 64},
        {"kind": "visual", "kind_version": 1, "digest": "a" * 64},
    ],
)
def test_artifact_basis_descriptor_rejects_noncanonical_source_fact(value: object) -> None:
    with pytest.raises(ValueError):
        ArtifactBasisDescriptor.from_dict(value)


def test_structured_content_basis_tracks_only_the_direct_formal_chain() -> None:
    first_project = {
        "content_mode": "drama",
        "generation_mode": "storyboard",
        "source_kind": "screenplay",
        "source_language": "zh",
        "provider": "first-provider",
        "model": "first-model",
        "credentials": {"api_key": "first-secret"},
        "endpoint": "https://first.invalid",
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "prompt_builder_version": 1,
        "voice": "first-voice",
        "speed": 1.0,
    }
    changed_execution_project = {
        **first_project,
        "provider": "second-provider",
        "model": "second-model",
        "credentials": {"api_key": "second-secret"},
        "endpoint": "https://second.invalid",
        "resolution": "4k",
        "prompt_builder_version": 99,
        "voice": "second-voice",
        "speed": 2.0,
    }

    script_plan = build_script_plan_basis("第一场\n对白", episode=1, project=first_project)
    same_script_plan = build_script_plan_basis("第一场\n对白", episode=1, project=changed_execution_project)
    changed_source = build_script_plan_basis("第一场\n另一句对白", episode=1, project=first_project)
    script = build_episode_script_basis({"scenes": [{"scene_id": "E1S01"}]}, project=first_project)
    same_script = build_episode_script_basis(
        {"scenes": [{"scene_id": "E1S01"}]},
        project=changed_execution_project,
    )
    changed_script_plan = build_episode_script_basis(
        {"scenes": [{"scene_id": "E1S01", "source_text": "changed"}]},
        project=first_project,
    )
    changed_prompt_context = build_episode_script_basis(
        {"scenes": [{"scene_id": "E1S01"}]},
        project={
            **first_project,
            "overview": {"synopsis": "新的项目概述"},
            "style": "水墨",
            "style_description": "留白",
            "aspect_ratio": "9:16",
            "source_language": "en",
            "characters": {"阿黎": {"description": "红衣"}},
            "scenes": {"屋顶": {"description": "雨夜"}},
            "props": {"钥匙": {"description": "黄铜"}},
        },
    )

    assert same_script_plan.digest == script_plan.digest
    assert changed_source.digest != script_plan.digest
    assert same_script.digest == script.digest
    assert changed_script_plan.digest != script.digest
    assert changed_prompt_context.digest != script.digest


@pytest.mark.parametrize(
    ("project", "changed"),
    [
        (
            {
                "content_mode": "drama",
                "generation_mode": "storyboard",
                "overview": {"synopsis": "旧梗概", "genre": "剧情", "theme": "成长", "world_setting": "古城"},
                "style": "写实",
                "characters": {"阿黎": {"description": "蓝衣"}},
                "scenes": {"屋顶": {"description": "晴日"}},
                "props": {"钥匙": {"description": "白银"}},
                "episodes": [
                    {
                        "episode": 1,
                        "title": "第一集",
                        "hook": "旧钩子",
                        "outline": {"story_beats": ["旧节点"], "next_episode_teaser": "旧预告"},
                    },
                    {"episode": 2, "title": "第二集", "hook": "旧承接"},
                ],
            },
            {"style": "水墨"},
        ),
        (
            {
                "content_mode": "drama",
                "generation_mode": "storyboard",
                "overview": {"synopsis": "旧梗概"},
                "style": "写实",
                "characters": {"阿黎": {}},
                "scenes": {},
                "props": {},
                "episodes": [{"episode": 1, "hook": "旧钩子"}],
            },
            {"overview": {"synopsis": "新梗概"}},
        ),
        (
            {
                "content_mode": "drama",
                "generation_mode": "storyboard",
                "overview": {},
                "style": "写实",
                "characters": {"阿黎": {}},
                "scenes": {},
                "props": {},
                "episodes": [{"episode": 1, "hook": "旧钩子"}],
            },
            {"characters": {"阿黎": {}, "小满": {}}},
        ),
        (
            {
                "content_mode": "drama",
                "generation_mode": "storyboard",
                "overview": {},
                "style": "写实",
                "characters": {},
                "scenes": {},
                "props": {},
                "episodes": [{"episode": 1, "hook": "旧钩子"}],
            },
            {"episodes": [{"episode": 1, "hook": "新钩子"}]},
        ),
        (
            {
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "overview": {},
                "characters": {"阿黎": {"description": "蓝衣"}},
                "scenes": {},
                "props": {},
                "episodes": [{"episode": 1, "hook": "旧钩子"}],
            },
            {"characters": {"阿黎": {"description": "红衣"}}},
        ),
    ],
    ids=("style", "overview", "asset-names", "episode-outline", "reference-asset-description"),
)
def test_script_plan_basis_tracks_each_persisted_prompt_context(
    project: dict[str, object],
    changed: dict[str, object],
) -> None:
    baseline = build_script_plan_basis("同一份原文", episode=1, project=project)
    updated = build_script_plan_basis("同一份原文", episode=1, project={**project, **changed})

    assert updated.digest != baseline.digest


def test_script_plan_basis_tracks_rendered_asset_order_but_ignores_unrendered_fields() -> None:
    project = {
        "content_mode": "drama",
        "generation_mode": "storyboard",
        "overview": {},
        "style": "写实",
        "style_description": "自然光",
        "characters": {
            "阿黎": {"description": "蓝衣", "character_sheet": "characters/old.png"},
            "小满": {"description": "红衣"},
        },
        "scenes": {},
        "props": {},
        "episodes": [],
    }

    baseline = build_script_plan_basis("同一份原文", episode=1, project=project)
    unrendered_changes = build_script_plan_basis(
        "同一份原文",
        episode=1,
        project={
            **project,
            "style_description": "高反差",
            "characters": {
                "阿黎": {"description": "金衣", "character_sheet": "characters/new.png"},
                "小满": {"description": "绿衣"},
            },
        },
    )
    reordered = build_script_plan_basis(
        "同一份原文",
        episode=1,
        project={
            **project,
            "characters": {
                "小满": {"description": "红衣"},
                "阿黎": {"description": "蓝衣"},
            },
        },
    )

    assert unrendered_changes.digest == baseline.digest
    assert reordered.digest != baseline.digest


def test_reference_script_plan_basis_ignores_asset_fields_not_rendered_by_the_prompt() -> None:
    project = {
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "overview": {},
        "characters": {
            "阿黎": {"description": "蓝衣", "character_sheet": "characters/old.png"},
        },
        "scenes": {},
        "props": {},
        "episodes": [],
    }

    baseline = build_script_plan_basis("同一份原文", episode=1, project=project)
    updated = build_script_plan_basis(
        "同一份原文",
        episode=1,
        project={
            **project,
            "characters": {
                "阿黎": {"description": "蓝衣", "character_sheet": "characters/new.png"},
            },
        },
    )

    assert updated.digest == baseline.digest


def test_reference_script_plan_basis_preserves_rendered_outline_whitespace() -> None:
    project = {
        "content_mode": "drama",
        "generation_mode": "reference_video",
        "overview": {},
        "characters": {},
        "scenes": {},
        "props": {},
        "episodes": [{"episode": 1, "outline": {"story_beats": [" 旧节点 "]}}],
    }

    baseline = build_script_plan_basis("同一份原文", episode=1, project=project)
    trimmed = build_script_plan_basis(
        "同一份原文",
        episode=1,
        project={
            **project,
            "episodes": [{"episode": 1, "outline": {"story_beats": ["旧节点"]}}],
        },
    )

    assert trimmed.digest != baseline.digest


@pytest.mark.parametrize(
    "changed",
    [
        {"overview": {"synopsis": "另一段概述", "genre": "悬疑", "theme": "选择", "world_setting": "雨城"}},
        {"style": "水墨"},
        {"style_description": "高反差留白"},
        {"aspect_ratio": "9:16"},
        {"source_language": "en"},
        {"characters": {"阿黎": {"description": "红衣"}}},
        {"scenes": {"屋顶": {"description": "雨夜"}}},
        {"props": {"钥匙": {"description": "黄铜"}}},
    ],
    ids=("overview", "style", "style-description", "aspect-ratio", "language", "characters", "scenes", "props"),
)
def test_episode_script_basis_tracks_each_durable_prompt_context_field(changed: dict[str, object]) -> None:
    project: dict[str, object] = {
        "content_mode": "drama",
        "generation_mode": "storyboard",
        "source_language": "zh",
        "overview": {"synopsis": "概述", "genre": "剧情", "theme": "成长", "world_setting": "古城"},
        "style": "写实",
        "style_description": "自然光",
        "aspect_ratio": "16:9",
        "characters": {"阿黎": {"description": "蓝衣"}},
        "scenes": {"屋顶": {"description": "晴日"}},
        "props": {"钥匙": {"description": "白银"}},
    }
    script_plan = {"scenes": [{"scene_id": "E1S01"}]}

    baseline = build_episode_script_basis(script_plan, project=project)
    updated = build_episode_script_basis(script_plan, project={**project, **changed})

    assert updated.digest != baseline.digest


def test_episode_script_basis_ignores_asset_fields_not_rendered_into_prompt_authoring_prompt() -> None:
    project = {
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "characters": {"阿黎": {"description": "蓝衣", "character_sheet": "characters/old.png"}},
        "scenes": {},
        "props": {},
    }
    script_plan = {"segments": [{"segment_id": "E1S01"}]}

    baseline = build_episode_script_basis(script_plan, project=project)
    updated = build_episode_script_basis(
        script_plan,
        project={
            **project,
            "characters": {"阿黎": {"description": "蓝衣", "character_sheet": "characters/new.png"}},
        },
    )

    assert updated.digest == baseline.digest


def test_structured_basis_rejects_malformed_formal_inputs() -> None:
    with pytest.raises(ValueError, match="content_mode"):
        build_script_plan_basis(
            "source",
            episode=1,
            project={"content_mode": [], "generation_mode": "storyboard"},
        )
    with pytest.raises(ValueError, match="non-finite"):
        build_episode_script_basis(
            {"duration": float("nan")},
            project={"content_mode": "narration", "generation_mode": "storyboard"},
        )


def test_script_plan_basis_treats_null_source_kind_as_default() -> None:
    project = {
        "content_mode": "drama",
        "generation_mode": "storyboard",
        "source_kind": None,
    }

    defaulted = build_script_plan_basis("source", episode=1, project=project)
    explicit = build_script_plan_basis("source", episode=1, project={**project, "source_kind": "novel"})

    assert defaulted.digest == explicit.digest


@pytest.mark.parametrize("source_language", [None, "", False, 0, [], {}])
def test_script_plan_basis_canonicalizes_default_source_language(source_language: object) -> None:
    project = {
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "source_language": source_language,
    }

    defaulted = build_script_plan_basis("source", episode=1, project=project)
    explicit = build_script_plan_basis("source", episode=1, project={**project, "source_language": "中文"})

    assert defaulted.digest == explicit.digest


def test_ad_script_basis_tracks_only_persisted_prompt_inputs() -> None:
    project = {
        "content_mode": "ad",
        "generation_mode": "storyboard",
        "target_duration": 30,
        "brief": "突出耐用",
        "overview": {
            "synopsis": "新品发布",
            "genre": "广告",
            "theme": "可靠",
            "world_setting": "工作室",
            "unused": "must-not-participate",
        },
        "style": "实拍",
        "style_description": "柔和自然光",
        "aspect_ratio": "9:16",
        "source_language": "zh",
        "speech_rate_units_per_second": 6.0,
        "characters": {"小岚": {"description": "prompt only consumes the name"}},
        "scenes": {"工作室": {"description": "prompt only consumes the name"}},
        "props": {"桌子": {"description": "prompt only consumes the name"}},
        "products": {
            "水杯": {
                "brand": "Arc",
                "description": "钛合金水杯",
                "selling_points": ["耐摔", "保温"],
                "product_sheet": "products/水杯.png",
            }
        },
        "provider": "first-provider",
        "supported_durations": [4, 6, 8],
        "request_instructions": "first request only",
    }

    baseline = build_ad_episode_script_basis(1, project=project)
    execution_only = build_ad_episode_script_basis(
        1,
        project={
            **project,
            "provider": "second-provider",
            "supported_durations": [5, 10],
            "request_instructions": "another request only",
            "overview": {**project["overview"], "unused": "changed"},
            "characters": {"小岚": {"description": "changed but not rendered"}},
            "products": {
                "水杯": {
                    **project["products"]["水杯"],
                    "product_sheet": "products/replaced.png",
                }
            },
        },
    )
    changed_brief = build_ad_episode_script_basis(1, project={**project, "brief": "突出轻便"})
    changed_product = build_ad_episode_script_basis(
        1,
        project={
            **project,
            "products": {
                "水杯": {
                    **project["products"]["水杯"],
                    "selling_points": ["轻便"],
                }
            },
        },
    )
    changed_speech_rate = build_ad_episode_script_basis(
        1,
        project={**project, "speech_rate_units_per_second": 7.0},
    )

    assert execution_only.digest == baseline.digest
    assert changed_brief.digest != baseline.digest
    assert changed_product.digest != baseline.digest
    assert changed_speech_rate.digest != baseline.digest


@pytest.mark.parametrize("field", ["characters", "scenes", "props", "products"])
def test_ad_script_basis_tracks_prompt_table_order(field: str) -> None:
    project = {
        "content_mode": "ad",
        "generation_mode": "storyboard",
        "target_duration": 30,
        "brief": "突出耐用",
        "overview": {},
        "characters": {"角色甲": {}, "角色乙": {}},
        "scenes": {"场景甲": {}, "场景乙": {}},
        "props": {"道具甲": {}, "道具乙": {}},
        "products": {
            "产品甲": {"description": "甲"},
            "产品乙": {"description": "乙"},
        },
    }
    reordered = {
        **project,
        field: dict(reversed(tuple(project[field].items()))),
    }

    assert (
        build_ad_episode_script_basis(1, project=reordered).digest
        != build_ad_episode_script_basis(
            1,
            project=project,
        ).digest
    )


def test_ad_reference_script_basis_excludes_storyboard_only_inputs() -> None:
    project = {
        "content_mode": "ad",
        "generation_mode": "reference_video",
        "target_duration": 30,
        "brief": "短片",
        "overview": {"synopsis": "发布", "world_setting": "unused by reference prompt"},
        "style": "实拍",
        "style_description": "自然光",
        "aspect_ratio": "9:16",
        "source_language": "zh",
        "speech_rate_units_per_second": 6.0,
        "characters": {},
        "scenes": {},
        "props": {},
        "products": {},
    }

    baseline = build_ad_episode_script_basis(1, project=project)
    same = build_ad_episode_script_basis(
        1,
        project={
            **project,
            "speech_rate_units_per_second": 7.0,
            "overview": {**project["overview"], "world_setting": "changed"},
        },
    )

    assert same.digest == baseline.digest
