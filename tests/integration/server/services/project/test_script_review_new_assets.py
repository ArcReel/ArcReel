"""内容确认时登记本集新增资产：按处理决定登记、改写引用，同名归并，「不登记」的改写。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from lib.artifacts.artifact_activation import activate_artifact_target_state
from lib.config.resolver import ConfigResolver
from lib.infra.json_io import atomic_write_json
from lib.project.project_manager import ProjectManager
from server.services.project.script_review import ScriptReviewError, ScriptReviewService
from tests.fakes import FakeConfigResolver

_PLAN_FILES = {
    "drama": "script_plan_normalized_script.json",
    "narration": "script_plan_segments.json",
    "reference_video": "script_plan_reference_units.json",
}


def _project(tmp_path: Path, content_mode: str, *, generation_mode: str | None = None) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", content_mode)
    pm.add_character("demo", "阿离", "少女，青衣")
    pm.add_character("demo", "裴与", "将军，银甲")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    if generation_mode is not None:
        pm.update_project("demo", lambda project: project.update(generation_mode=generation_mode))
    return pm


def _write_plan(pm: ProjectManager, route: str, content: dict[str, Any]) -> None:
    project_path = pm.get_project_path("demo")
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True, exist_ok=True)
    atomic_write_json(drafts / _PLAN_FILES[route], content)
    source = project_path / "source" / "episode_1.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("原文", encoding="utf-8")
    activate_artifact_target_state(project_path, bump_schema=False)


def _narration_plan(characters: list[str], new_assets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "episode": 1,
        "segments": [
            {
                "segment_id": "E1S01",
                "novel_text": "裴与身边跟着一个小丫头。",
                "duration_seconds": 6,
                "segment_break": False,
                "characters_in_segment": characters,
                "scenes": [],
                "props": [],
            }
        ],
        "new_assets": new_assets,
    }


def _new(name: str, decision: str, **fields: Any) -> dict[str, Any]:
    return {"type": "character", "name": name, "decision": decision, "reason": "依据", **fields}


async def _confirm(pm: ProjectManager) -> None:
    service = ScriptReviewService(pm, config_resolver=cast(ConfigResolver, FakeConfigResolver()))
    await service.confirm("demo", 1)


def _formal(pm: ProjectManager) -> dict[str, Any]:
    return json.loads((pm.get_project_path("demo") / "scripts" / "episode_1.json").read_text(encoding="utf-8"))


async def test_a_new_character_is_registered_with_its_aliases(tmp_path: Path, video_request_facts) -> None:
    pm = _project(tmp_path, "narration")
    _write_plan(
        pm,
        "narration",
        _narration_plan(["裴与", "小桃"], [_new("小桃", "register", description="十岁女童，双髻", aliases=["桃丫头"])]),
    )

    await _confirm(pm)

    character = pm.load_project("demo")["characters"]["小桃"]
    assert character["description"] == "十岁女童，双髻"
    assert character["aliases"] == ["桃丫头"]
    assert _formal(pm)["segments"][0]["characters_in_segment"] == ["裴与", "小桃"]


async def test_a_renamed_new_asset_keeps_the_planned_name_as_an_alias(tmp_path: Path, video_request_facts) -> None:
    pm = _project(tmp_path, "narration")
    _write_plan(
        pm,
        "narration",
        _narration_plan(["桃丫头"], [_new("桃丫头", "register", description="十岁女童", asset_name="小桃")]),
    )

    await _confirm(pm)

    characters = pm.load_project("demo")["characters"]
    assert "桃丫头" not in characters
    assert characters["小桃"]["aliases"] == ["桃丫头"]
    assert _formal(pm)["segments"][0]["characters_in_segment"] == ["小桃"]


async def test_merging_into_an_existing_asset_rewrites_references_and_records_the_alias(
    tmp_path: Path, video_request_facts
) -> None:
    pm = _project(tmp_path, "narration")
    _write_plan(
        pm,
        "narration",
        _narration_plan(["将军"], [_new("将军", "merge", target="裴与", description="被忽略的描述")]),
    )

    await _confirm(pm)

    characters = pm.load_project("demo")["characters"]
    assert "将军" not in characters
    assert characters["裴与"]["description"] == "将军，银甲"
    assert characters["裴与"]["aliases"] == ["将军"]
    assert _formal(pm)["segments"][0]["characters_in_segment"] == ["裴与"]


async def test_a_new_asset_named_like_a_registered_one_merges_without_changing_its_description(
    tmp_path: Path, video_request_facts
) -> None:
    pm = _project(tmp_path, "narration")
    _write_plan(
        pm,
        "narration",
        _narration_plan(["裴与"], [_new(" 裴与", "register", description="另一份描述", aliases=["裴将军"])]),
    )

    await _confirm(pm)

    characters = pm.load_project("demo")["characters"]
    assert list(characters) == ["阿离", "裴与"]
    assert characters["裴与"]["description"] == "将军，银甲"
    assert characters["裴与"]["aliases"] == ["裴将军"]


async def test_same_named_new_assets_register_once(tmp_path: Path, video_request_facts) -> None:
    pm = _project(tmp_path, "narration")
    _write_plan(
        pm,
        "narration",
        _narration_plan(
            ["小桃"],
            [
                _new("小桃", "register", description="十岁女童"),
                _new("小桃", "register", description="重复的一项"),
            ],
        ),
    )

    await _confirm(pm)

    assert pm.load_project("demo")["characters"]["小桃"]["description"] == "十岁女童"


async def test_merging_into_another_new_asset_follows_its_registration(tmp_path: Path, video_request_facts) -> None:
    pm = _project(tmp_path, "narration")
    _write_plan(
        pm,
        "narration",
        _narration_plan(
            ["小桃", "桃丫头"],
            [_new("小桃", "register", description="十岁女童"), _new("桃丫头", "merge", target="小桃")],
        ),
    )

    await _confirm(pm)

    assert pm.load_project("demo")["characters"]["小桃"]["aliases"] == ["桃丫头"]
    assert _formal(pm)["segments"][0]["characters_in_segment"] == ["小桃"]


async def test_merging_into_another_new_asset_by_its_registered_name(tmp_path: Path, video_request_facts) -> None:
    pm = _project(tmp_path, "narration")
    _write_plan(
        pm,
        "narration",
        _narration_plan(
            ["桃丫头", "小桃儿"],
            [
                _new("桃丫头", "register", description="十岁女童", asset_name="小桃"),
                _new("小桃儿", "merge", target="小桃"),
            ],
        ),
    )

    await _confirm(pm)

    assert pm.load_project("demo")["characters"]["小桃"]["aliases"] == ["桃丫头", "小桃儿"]
    assert _formal(pm)["segments"][0]["characters_in_segment"] == ["小桃"]


@pytest.mark.parametrize("decision", ["skip", "derivative"])
async def test_a_new_asset_named_like_a_registered_one_merges_whatever_its_decision(
    tmp_path: Path, video_request_facts, decision: str
) -> None:
    pm = _project(tmp_path, "narration")
    _write_plan(
        pm,
        "narration",
        _narration_plan(["裴与"], [_new("裴与", decision, target="阿离", asset_name="少年", description="少年")]),
    )

    await _confirm(pm)

    characters = pm.load_project("demo")["characters"]
    assert characters["阿离"]["derivatives"] == {}
    assert characters["裴与"]["description"] == "将军，银甲"
    assert _formal(pm)["segments"][0]["characters_in_segment"] == ["裴与"]


async def test_a_derivative_is_registered_under_its_base_character(tmp_path: Path, video_request_facts) -> None:
    pm = _project(tmp_path, "drama")
    scene = {
        "scene_id": "E1S01",
        "duration_seconds": 8,
        "segment_break": False,
        "characters_in_scene": ["少年裴与"],
        "scenes": [],
        "props": [],
        "scene_description": "少年裴与练枪",
        "utterances": [{"kind": "dialogue", "speaker": "少年裴与", "text": "再来！"}],
        "source_text": "少年裴与练枪。",
    }
    _write_plan(
        pm,
        "drama",
        {
            "title": "第一集",
            "scenes": [scene],
            "new_assets": [
                _new("少年裴与", "derivative", target="裴与", asset_name="少年", description="十五岁，布衣束发")
            ],
        },
    )

    await _confirm(pm)

    base = pm.load_project("demo")["characters"]["裴与"]
    assert base["derivatives"]["少年"]["description"] == "十五岁，布衣束发"
    assert base["description"] == "将军，银甲"
    [formal_scene] = _formal(pm)["scenes"]
    assert formal_scene["characters_in_scene"] == ["裴与/少年"]
    assert formal_scene["utterances"][0]["speaker"] == "裴与"


async def test_a_skipped_drama_character_leaves_the_references_and_keeps_its_lines(
    tmp_path: Path, video_request_facts
) -> None:
    pm = _project(tmp_path, "drama")
    scene = {
        "scene_id": "E1S01",
        "duration_seconds": 8,
        "segment_break": False,
        "characters_in_scene": ["阿离", "路人"],
        "scenes": [],
        "props": [],
        "scene_description": "路人拦住阿离",
        "utterances": [{"kind": "dialogue", "speaker": "路人", "text": "借过。"}],
        "source_text": "路人拦住阿离。",
    }
    _write_plan(
        pm,
        "drama",
        {"title": "第一集", "scenes": [scene], "new_assets": [_new("路人", "skip", description="挑夫")]},
    )

    await _confirm(pm)

    assert "路人" not in pm.load_project("demo")["characters"]
    [formal_scene] = _formal(pm)["scenes"]
    assert formal_scene["characters_in_scene"] == ["阿离"]
    assert formal_scene["utterances"][0]["speaker"] == "路人"


async def test_a_skipped_reference_video_asset_becomes_plain_text_but_keeps_its_speaker_mark(
    tmp_path: Path, video_request_facts
) -> None:
    pm = _project(tmp_path, "drama", generation_mode="reference_video")
    _write_plan(
        pm,
        "reference_video",
        {
            "units": [
                {
                    "unit_id": "E1U01",
                    "text": "@[路人] 拦住 @[阿离]。\n@[路人]{借过。}",
                    "duration_seconds": 8,
                    "source_text": "路人拦住阿离。",
                }
            ],
            "new_assets": [_new("路人", "skip")],
        },
    )

    await _confirm(pm)

    [unit] = _formal(pm)["video_units"]
    assert unit["text"] == "路人 拦住 @[阿离]。\n@[路人]{借过。}"
    assert "路人" not in pm.load_project("demo")["characters"]


async def test_reference_video_mentions_follow_merges_and_derivatives(tmp_path: Path, video_request_facts) -> None:
    pm = _project(tmp_path, "drama", generation_mode="reference_video")
    _write_plan(
        pm,
        "reference_video",
        {
            "units": [
                {
                    "unit_id": "E1U01",
                    "text": "@[将军] 看着 @[小离]。\n@[小离]{你回来了。}",
                    "duration_seconds": 8,
                    "source_text": "将军看着小离。",
                }
            ],
            "new_assets": [
                _new("将军", "merge", target="裴与"),
                _new("小离", "derivative", target="阿离", asset_name="幼年", description="六岁"),
            ],
        },
    )

    await _confirm(pm)

    [unit] = _formal(pm)["video_units"]
    assert unit["text"] == "@[裴与] 看着 @[阿离/幼年]。\n@[阿离/幼年]{你回来了。}"


@pytest.mark.parametrize(
    "item",
    [
        _new("将军", "merge", target="不存在的人"),
        _new("少年", "derivative", target="不存在的人", description="十五岁"),
        {"type": "scene", "name": "旧宅", "decision": "derivative", "reason": "依据", "target": "阿离"},
    ],
    ids=["unknown-merge-target", "unknown-derivative-base", "derivative-of-a-scene"],
)
async def test_an_unresolvable_decision_refuses_the_confirmation_untouched(
    tmp_path: Path, video_request_facts, item: dict[str, Any]
) -> None:
    pm = _project(tmp_path, "narration")
    _write_plan(pm, "narration", _narration_plan([], [item]))
    before = pm.load_project("demo")

    with pytest.raises(ScriptReviewError) as exc_info:
        await _confirm(pm)

    assert exc_info.value.code == "invalid_new_assets"
    assert pm.load_project("demo")["characters"] == before["characters"]
    assert not (pm.get_project_path("demo") / "scripts" / "episode_1.json").exists()


def _drama_plan(scene: dict[str, Any], new_assets: list[dict[str, Any]]) -> dict[str, Any]:
    base = {
        "scene_id": "E1S01",
        "duration_seconds": 8,
        "segment_break": False,
        "characters_in_scene": [],
        "scenes": [],
        "props": [],
        "scene_description": "画面",
        "utterances": [],
        "source_text": "原文。",
    }
    return {"title": "第一集", "scenes": [{**base, **scene}], "new_assets": new_assets}


def _assert_untouched(pm: ProjectManager, before: dict[str, Any]) -> None:
    after = pm.load_project("demo")
    assert after["characters"] == before["characters"]
    assert after.get("scenes", {}) == before.get("scenes", {})
    assert not (pm.get_project_path("demo") / "scripts" / "episode_1.json").exists()


async def test_a_reference_to_a_name_neither_registered_nor_registered_this_episode_refuses_the_confirmation(
    tmp_path: Path, video_request_facts
) -> None:
    pm = _project(tmp_path, "narration")
    _write_plan(pm, "narration", _narration_plan(["裴与", "王五"], [_new("小桃", "register", description="女童")]))
    before = pm.load_project("demo")

    with pytest.raises(ScriptReviewError) as exc_info:
        await _confirm(pm)

    assert exc_info.value.code == "unregistered_references"
    assert "E1S01" in exc_info.value.message
    assert "王五" in exc_info.value.message
    assert "裴与" not in exc_info.value.message
    _assert_untouched(pm, before)


async def test_unknown_scene_and_prop_references_are_listed_per_item(tmp_path: Path, video_request_facts) -> None:
    pm = _project(tmp_path, "drama")
    _write_plan(pm, "drama", _drama_plan({"scenes": ["旧宅"], "props": ["铜镜"]}, []))
    before = pm.load_project("demo")

    with pytest.raises(ScriptReviewError) as exc_info:
        await _confirm(pm)

    assert exc_info.value.code == "unregistered_references"
    assert "旧宅" in exc_info.value.message
    assert "铜镜" in exc_info.value.message
    _assert_untouched(pm, before)


async def test_references_to_this_episodes_registered_new_assets_and_known_derivatives_confirm(
    tmp_path: Path, video_request_facts
) -> None:
    pm = _project(tmp_path, "drama")
    pm.update_project(
        "demo",
        lambda project: project["characters"]["阿离"].update(
            derivatives={"幼年": {"description": "六岁", "character_sheet": ""}}
        ),
    )
    _write_plan(
        pm,
        "drama",
        _drama_plan(
            {
                "characters_in_scene": ["阿离/幼年", "小桃", "将军"],
                "scenes": ["旧宅"],
                "utterances": [{"kind": "dialogue", "speaker": "店小二", "text": "客官里边请。"}],
            },
            [
                _new("小桃", "register", description="女童"),
                _new("将军", "merge", target="裴与"),
                {"type": "scene", "name": "旧宅", "decision": "register", "reason": "依据", "description": "破败院落"},
            ],
        ),
    )

    await _confirm(pm)

    [scene] = _formal(pm)["scenes"]
    assert scene["characters_in_scene"] == ["阿离/幼年", "小桃", "裴与"]
    assert scene["scenes"] == ["旧宅"]
    assert scene["utterances"][0]["speaker"] == "店小二"


async def test_a_derivative_the_character_does_not_have_refuses_the_confirmation(
    tmp_path: Path, video_request_facts
) -> None:
    pm = _project(tmp_path, "narration")
    _write_plan(pm, "narration", _narration_plan(["阿离/不存在"], []))

    with pytest.raises(ScriptReviewError) as exc_info:
        await _confirm(pm)

    assert exc_info.value.code == "unregistered_references"
    assert "阿离/不存在" in exc_info.value.message


async def test_a_reference_video_mention_of_an_unknown_name_refuses_the_confirmation(
    tmp_path: Path, video_request_facts
) -> None:
    pm = _project(tmp_path, "drama", generation_mode="reference_video")
    _write_plan(
        pm,
        "reference_video",
        {
            "units": [
                {
                    "unit_id": "E1U01",
                    "text": "@[王五] 拦住 @[阿离]。\n@[阿离]{借过。}",
                    "duration_seconds": 8,
                    "source_text": "王五拦住阿离。",
                }
            ],
        },
    )
    before = pm.load_project("demo")

    with pytest.raises(ScriptReviewError) as exc_info:
        await _confirm(pm)

    assert exc_info.value.code == "unregistered_references"
    assert "E1U01" in exc_info.value.message
    assert "王五" in exc_info.value.message
    _assert_untouched(pm, before)
