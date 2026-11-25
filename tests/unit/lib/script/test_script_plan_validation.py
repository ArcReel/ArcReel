import json
from pathlib import Path

import pytest

from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.script_models import DramaNormalizedScript, NarrationScriptPlanDraft, ReferenceScriptPlanDraft
from lib.script.script_plan_validation import EpisodePlanSource, read_episode_plan_source, validate_script_plan


@pytest.mark.parametrize("origin", ["own", "whole_source"])
def test_declared_source_fault_blocks_but_none_ignores_residue(tmp_path: Path, origin: str):
    path = tmp_path / "source" / "episode_1.txt"
    path.parent.mkdir()
    project = {"content_mode": "narration", "episodes": [{"episode": 1, "source_origin": origin}]}
    candidate = NarrationScriptPlanDraft.model_validate({"segments": []}).model_dump()
    for data in (None, b" \n", b"\xff"):
        if data is not None:
            path.write_bytes(data)
        source = read_episode_plan_source(tmp_path, project, 1)
        result = validate_script_plan(
            "narration",
            candidate,
            project=project,
            project_path=tmp_path,
            episode=1,
            source=source,
            capabilities=[4, 8],
        )
        assert {issue.code for issue in result.blocking_issues} == {"episode_source_unavailable", "empty_script_plan"}

    path.write_text("residue", encoding="utf-8")
    project["episodes"][0]["source_origin"] = "none"
    source = read_episode_plan_source(tmp_path, project, 1)
    assert source.text is None
    assert source.problem is None


@pytest.mark.parametrize(
    ("kind", "model", "root"),
    [
        ("drama", DramaNormalizedScript, "scenes"),
        ("narration", NarrationScriptPlanDraft, "segments"),
        ("reference_video", ReferenceScriptPlanDraft, "units"),
    ],
)
def test_empty_plan_is_persistable_and_blocks_confirmation(tmp_path: Path, kind, model, root):
    candidate = model.model_validate({"title": "第一集", root: []}).model_dump()
    result = validate_script_plan(
        kind, candidate, project={}, project_path=tmp_path, episode=1, source=EpisodePlanSource(), capabilities=None
    )
    assert [issue.code for issue in result.blocking_issues] == ["empty_script_plan"]
    assert not result.soft_violations


def test_storyboard_recheck_preserves_asset_and_coverage_diagnostics(tmp_path: Path):
    project = {"characters": {"甲": {"description": "主角"}}}
    assets = [{"type": "character", "name": "乙", "decision": "merge", "target": "甲", "reason": "同一人物"}]
    source = EpisodePlanSource("甲走了。", "source/episode_1.txt")
    scene = {
        "scene_id": "E1S07",
        "duration_seconds": 5,
        "characters_in_scene": ["乙", "丙"],
        "scene_description": "长街",
        "source_text": "他跑了。",
        "utterances": [
            {"kind": "dialogue", "speaker": "甲", "text": "走吧。"},
            {"kind": "voiceover", "text": "片刻之后。"},
        ],
    }
    segment = {
        "segment_id": "E1S08",
        "duration_seconds": 5,
        "novel_text": "他跑了。",
        "characters_in_segment": ["乙", "丙"],
        "scenes": [],
        "props": [],
    }
    drama = DramaNormalizedScript.model_validate({"title": "第一集", "scenes": [scene], "new_assets": assets})
    narration = NarrationScriptPlanDraft.model_validate({"segments": [segment], "new_assets": assets})
    drama_result = validate_script_plan(
        "drama",
        drama.model_dump(),
        project=project,
        project_path=tmp_path,
        episode=1,
        source=source,
        capabilities=[4, 8],
    )
    narration_result = validate_script_plan(
        "narration",
        narration.model_dump(),
        project=project,
        project_path=tmp_path,
        episode=1,
        source=source,
        capabilities=[4, 8],
    )

    drama_issues = drama_result.blocking_issues
    assert [issue.code for issue in drama_issues[:3]] == [
        "unregistered_asset",
        "duration_off_tier",
        "source_text_not_verbatim",
    ]
    assert len(drama_issues) == 4
    assert drama_issues[-1].reason is not None
    assert all((issue.item_index, issue.item_id) == (0, "E1S07") for issue in drama_issues)
    assert drama_issues[0].params == {"label": "scene E1S07", "field": "characters_in_scene", "names": ["丙"]}
    assert str(drama_issues[0]) == (
        "scene E1S07 的 characters_in_scene 引用了未登记的资产名: ['丙']；"
        "资产名必须逐字取自已登记资产，或把它列进 new_assets 并给出处理决定"
    )
    narration_issues = narration_result.blocking_issues
    assert [(issue.code, issue.item_id) for issue in narration_issues] == [
        ("duration_off_tier", "E1S08"),
        ("unregistered_asset", "E1S08"),
        ("novel_text_coverage", None),
    ]
    assert narration_issues[0].params == {"label": "segment E1S08", "duration": 5, "tiers": [4, 8]}
    assert narration_issues[-1].params == {"source": "source/episode_1.txt"}
    assert not drama_result.soft_violations
    assert not narration_result.soft_violations


def test_reference_recheck_preserves_old_text_and_capability_diagnostics(tmp_path: Path):
    from copy import deepcopy

    from lib.script.reference_video.voice_settings import VoiceRenderSettings
    from lib.script.script_plan_validation import ReferenceSplitCaps

    voice = VoiceRenderSettings(voice_consistency="native", max_reference_audio=1)
    caps = ReferenceSplitCaps(None, [4, 8], [8], [4, 8], None, 8, 1, voice, voice)
    project = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "generation_mode": "reference_video",
        "characters": {"甲": {"description": "主角"}, "乙": {"description": "助手"}},
        "scenes": {"车站": {"description": "旧车站"}},
        "source_language": "中文",
    }
    project_file = tmp_path / "project.json"
    project_file.write_text(json.dumps(project), encoding="utf-8")
    original_bytes = project_file.read_bytes()
    units = [
        {"text": "甲等车。", "duration_seconds": 5, "source_text": "改写"},
        {"text": "甲等车。\n@[甲]｛到了。｝"},
        {"text": "@[甲]与@[乙]等车。"},
        {"text": "车门开了。\n@[甲]{到了。}\n@[乙]{走吧。}"},
        {"text": "车门开了。\n{" + "快走" * 80 + "}"},
        {"text": "镜头扫过 @[不存在]。"},
        {"text": "车门开了。\n@[甲]{到了。}\n{过了三年。}"},
    ]
    candidate = ReferenceScriptPlanDraft.model_validate(
        {
            "units": [
                {"unit_id": f"E1U{index + 10}", "duration_seconds": 4, "source_text": "甲等车。", **unit}
                for index, unit in enumerate(units)
            ]
        }
    ).model_dump()
    before = deepcopy(candidate), deepcopy(project)
    result = validate_script_plan(
        "reference_video",
        candidate,
        project=project,
        project_path=tmp_path,
        episode=1,
        source=EpisodePlanSource("甲等车。"),
        capabilities=caps,
    )

    assert [issue.code for issue in result.blocking_issues[:-1]] == [
        "source_text_not_verbatim",
        "duration_off_tier",
        "fullwidth_braces",
        "refs_over_limit",
        "speakers_over_limit",
        "dialogue_overload",
        "unregistered_asset",
    ]
    assert [issue.item_id for issue in result.blocking_issues] == [
        "E1U10",
        "E1U10",
        "E1U11",
        "E1U12",
        "E1U13",
        "E1U14",
        "E1U15",
        "E1U16",
    ]
    assert result.blocking_issues[2].line == 1
    assert result.blocking_issues[-1].reason is not None
    assert [
        (issue.item_index, issue.item_id)
        for issue in result.soft_violations
        if issue.code == "ref_warn_unit_without_scene"
    ] == [(index, f"E1U{10 + index}") for index in range(len(units))]
    assert (candidate, project) == before
    assert list(tmp_path.iterdir()) == [project_file]
    assert project_file.read_bytes() == original_bytes


def test_ledger_none_skips_coverage_even_with_a_leftover_file(tmp_path: Path):
    path = tmp_path / "source" / "episode_1.txt"
    path.parent.mkdir()
    path.write_text("旧原文", encoding="utf-8")
    project = {"content_mode": "narration", "episodes": [{"episode": 1, "source_origin": "none"}]}
    candidate = NarrationScriptPlanDraft.model_validate(
        {
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "novel_text": "手写旁白",
                    "characters_in_segment": [],
                    "scenes": [],
                    "props": [],
                }
            ]
        }
    ).model_dump()
    result = validate_script_plan(
        "narration",
        candidate,
        project=project,
        project_path=tmp_path,
        episode=1,
        source=read_episode_plan_source(tmp_path, project, 1),
        capabilities=[4, 8],
    )
    assert not result.blocking_issues

    project["episodes"][0]["source_origin"] = "own"
    result = validate_script_plan(
        "narration",
        candidate,
        project=project,
        project_path=tmp_path,
        episode=1,
        source=read_episode_plan_source(tmp_path, project, 1),
        capabilities=[4, 8],
    )
    assert [issue.code for issue in result.blocking_issues] == ["novel_text_coverage"]


def test_duplicate_scene_and_unit_ids_block_confirmation_before_confirm(tmp_path: Path):
    drama_candidate = DramaNormalizedScript.model_validate(
        {
            "title": "第一集",
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "duration_seconds": 4,
                    "characters_in_scene": [],
                    "scene_description": "雨夜",
                },
                {
                    "scene_id": "E2S01",
                    "duration_seconds": 4,
                    "characters_in_scene": [],
                    "scene_description": "清晨",
                },
            ],
        }
    ).model_dump()
    drama_result = validate_script_plan(
        "drama",
        drama_candidate,
        project={},
        project_path=tmp_path,
        episode=1,
        source=EpisodePlanSource(),
        capabilities=[4, 8],
    )
    assert [issue.code for issue in drama_result.blocking_issues] == ["duplicate_scene_id"]
    assert drama_result.blocking_issues[0].params == {"ids": ["E1S01"]}

    rv_project = {"schema_version": CURRENT_PROJECT_SCHEMA_VERSION, "generation_mode": "reference_video"}
    (tmp_path / "project.json").write_text(json.dumps(rv_project), encoding="utf-8")
    rv_candidate = ReferenceScriptPlanDraft.model_validate(
        {
            "units": [
                {"unit_id": "E1U01", "duration_seconds": 4, "text": "雨夜长街。"},
                {"unit_id": "E1U01", "duration_seconds": 4, "text": "清晨巷口。"},
            ]
        }
    ).model_dump()
    rv_result = validate_script_plan(
        "reference_video",
        rv_candidate,
        project=rv_project,
        project_path=tmp_path,
        episode=1,
        source=EpisodePlanSource(),
        capabilities=None,
    )
    assert [issue.code for issue in rv_result.blocking_issues] == ["duplicate_unit_id"]
    assert rv_result.blocking_issues[0].params == {"ids": ["E1U01"]}
