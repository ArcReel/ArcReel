"""广告/短片整份生成：结果直接写成正式脚本，本次新增的资产随之登记；违约时不写盘、不落草稿。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_activation import activate_artifact_target_state
from lib.project.project_manager import ProjectManager
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.script_generator import AdScriptOverwriteRequired, AdScriptRejected, ScriptGenerator
from lib.workflow.workflow_state import WorkflowStateService
from tests.fakes import FakeTextGenerator

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def ad_video_request_facts(video_request_facts) -> None:
    """分镜路线的档位来自共享 fixture 的 (4, 6, 8)。"""


def _write_ad_project(tmp_path: Path, generation_mode: str = "storyboard", **overrides: Any) -> Path:
    project_dir = tmp_path / "projects" / "ad"
    project_dir.mkdir(parents=True)
    payload = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "title": "雨夜",
        "content_mode": "ad",
        "generation_mode": generation_mode,
        "target_duration": 30,
        "brief": "雨夜里两个陌生人共用一把伞",
        "overview": {"synopsis": "创意短片"},
        "characters": {"小美": {"description": "白领，短发"}},
        "scenes": {},
        "props": {},
        "products": {},
        "style": "实拍",
        "style_description": "真实质感",
        "aspect_ratio": "9:16",
        "episodes": [{"episode": 1, "title": "", "script_file": "scripts/episode_1.json"}],
        **overrides,
    }
    (project_dir / "project.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    activate_artifact_target_state(project_dir, bump_schema=False)
    return project_dir


def _generator(project_dir: Path, *responses: object) -> ScriptGenerator:
    text_generator = FakeTextGenerator(*(json.dumps(response, ensure_ascii=False) for response in responses))
    return ScriptGenerator(project_dir, generator=text_generator)


def _shot(shot_id: str, *, characters: list[str] | None = None, scenes: list[str] | None = None) -> dict[str, Any]:
    return {
        "shot_id": shot_id,
        "section": "opening",
        "duration_seconds": 4,
        "voiceover_text": "",
        "characters_in_shot": characters or [],
        "scenes": scenes or [],
        "props": [],
        "products_in_shot": [],
        "image_prompt": {
            "scene": "雨夜街角，路灯下的积水映出霓虹",
            "composition": {"shot_type": "Medium Shot", "lighting": "冷色路灯", "ambiance": "潮湿"},
        },
        "video_prompt": {
            "action": "雨水顺着伞沿滴落，两人并肩走过街角",
            "camera_motion": "Tracking Shot",
            "ambiance_audio": "雨声",
            "dialogue": [],
        },
    }


def _new(name: str, decision: str, asset_type: str = "character", **fields: Any) -> dict[str, Any]:
    return {"type": asset_type, "name": name, "decision": decision, "reason": "短片里第一次出现", **fields}


def _project(project_dir: Path) -> dict[str, Any]:
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))


def _script(project_dir: Path) -> dict[str, Any]:
    return json.loads((project_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))


async def test_brief_without_products_writes_the_formal_script_and_registers_new_assets(tmp_path: Path) -> None:
    project_dir = _write_ad_project(tmp_path)
    response = {
        "title": "共伞",
        "shots": [
            _shot("E1S01", characters=["小美", "阿杰"], scenes=["街角"]),
            _shot("E1S02", characters=["阿杰"], scenes=["街角"]),
        ],
        "new_assets": [
            _new("阿杰", "register", description="高个青年，黑色风衣", aliases=["风衣男"]),
            _new("街角", "register", "scene", description="雨夜的十字街角，霓虹招牌"),
        ],
    }
    registered: list[dict[str, str]] = []

    await _generator(project_dir, response).generate(1, registered_assets=registered)

    script = _script(project_dir)
    assert [shot["characters_in_shot"] for shot in script["shots"]] == [["小美", "阿杰"], ["阿杰"]]
    project = _project(project_dir)
    assert project["characters"]["阿杰"]["description"] == "高个青年，黑色风衣"
    assert project["characters"]["阿杰"]["aliases"] == ["风衣男"]
    assert project["scenes"]["街角"]["description"] == "雨夜的十字街角，霓虹招牌"
    assert registered == [{"type": "character", "name": "阿杰"}, {"type": "scene", "name": "街角"}]
    status = WorkflowStateService(ProjectManager.for_project_dir(project_dir)).get_status("ad", 1)
    assert status.artifacts["script"]["state"] == "current"


async def test_a_new_asset_named_like_a_registered_one_merges_into_it(tmp_path: Path) -> None:
    project_dir = _write_ad_project(tmp_path)
    shot = _shot("E1S01", characters=["小美", "白领"])
    shot["video_prompt"]["dialogue"] = [{"speaker": "白领", "line": "一起走吧。"}]
    response = {
        "title": "共伞",
        "shots": [shot],
        "new_assets": [
            _new("小美", "register", description="长发，红色雨衣"),
            _new("白领", "merge", target="小美"),
        ],
    }

    await _generator(project_dir, response).generate(1)

    saved = _script(project_dir)["shots"][0]
    assert saved["characters_in_shot"] == ["小美"]
    assert saved["video_prompt"]["dialogue"] == [{"speaker": "小美", "line": "一起走吧。"}]
    character = _project(project_dir)["characters"]["小美"]
    assert character["description"] == "白领，短发"
    assert character["aliases"] == ["白领"]


@pytest.mark.parametrize(
    "response",
    [
        {"title": "共伞", "shots": [_shot("E1S01", characters=["阿杰"])], "new_assets": []},
        {
            "title": "共伞",
            "shots": [_shot("E1S01", characters=["阿杰"])],
            "new_assets": [_new("阿杰", "merge", target="不存在的人")],
        },
        {"title": "共伞", "shots": []},
        {"title": "共伞", "shots": [{"shot_id": "E1S01"}]},
        [{"title": "共伞", "shots": [_shot("E1S01")]}],
    ],
    ids=["unlisted-reference", "unresolvable-decision", "no-entries", "broken-structure", "not-an-object"],
)
async def test_a_rejected_output_writes_nothing(tmp_path: Path, response: object) -> None:
    project_dir = _write_ad_project(tmp_path)
    before = (project_dir / "project.json").read_bytes()

    with pytest.raises(AdScriptRejected) as caught:
        await _generator(project_dir, response).generate(1)

    assert caught.value.problems
    assert not (project_dir / "scripts" / "episode_1.json").exists()
    assert not (project_dir / "drafts").exists()
    assert (project_dir / "project.json").read_bytes() == before


async def test_reference_units_register_new_mentions_and_keep_a_skipped_speaker(tmp_path: Path) -> None:
    project_dir = _write_ad_project(tmp_path, "reference_video")
    response = {
        "title": "共伞",
        "units": [
            {"duration_seconds": 6, "text": "镜头1：@[小美] 在 @[街角] 撑开伞，@[阿杰] 跑进伞下"},
            {"duration_seconds": 5, "text": "镜头1：@[卖花人] 递来一枝花\n@[卖花人]：{送给你们。}"},
        ],
        "new_assets": [
            _new("阿杰", "register", description="高个青年，黑色风衣"),
            _new("街角", "register", "scene", description="雨夜的十字街角"),
            _new("卖花人", "skip"),
        ],
    }
    registered: list[dict[str, str]] = []

    await _generator(project_dir, response).generate(1, registered_assets=registered)

    units = _script(project_dir)["video_units"]
    assert units[0]["text"] == "镜头1：@[小美] 在 @[街角] 撑开伞，@[阿杰] 跑进伞下"
    assert units[1]["text"] == "镜头1：卖花人 递来一枝花\n@[卖花人]：{送给你们。}"
    project = _project(project_dir)
    assert "阿杰" in project["characters"]
    assert "卖花人" not in project["characters"]
    assert registered == [{"type": "character", "name": "阿杰"}, {"type": "scene", "name": "街角"}]


async def test_reference_units_with_an_unlisted_mention_are_rejected(tmp_path: Path) -> None:
    project_dir = _write_ad_project(tmp_path, "reference_video")
    response = {"title": "共伞", "units": [{"duration_seconds": 6, "text": "镜头1：@[阿杰] 跑进雨里"}]}

    with pytest.raises(AdScriptRejected):
        await _generator(project_dir, response).generate(1)

    assert not (project_dir / "scripts" / "episode_1.json").exists()


async def test_regenerating_replaces_the_formal_script_after_the_loss_list_is_acknowledged(tmp_path: Path) -> None:
    project_dir = _write_ad_project(tmp_path)
    first = {
        "title": "共伞",
        "shots": [_shot("E1S01", characters=["阿杰"]), _shot("E1S02")],
        "new_assets": [_new("阿杰", "register", description="高个青年")],
    }
    second = {"title": "雨停", "shots": [_shot("E1S01", characters=["小美"])]}
    await _generator(project_dir, first).generate(1)
    first_script = (project_dir / "scripts" / "episode_1.json").read_bytes()

    with pytest.raises(AdScriptOverwriteRequired) as caught:
        await _generator(project_dir, second).generate(1, regenerate=True)
    assert [entry.entry_id for entry in caught.value.overwrite.entries] == ["E1S01", "E1S02"]
    assert (project_dir / "scripts" / "episode_1.json").read_bytes() == first_script

    await _generator(project_dir, second).generate(
        1, regenerate=True, overwrite_revision=caught.value.overwrite.fingerprint
    )

    script = _script(project_dir)
    assert script["title"] == ""
    assert [shot["shot_id"] for shot in script["shots"]] == ["E1S01"]
    assert "阿杰" in _project(project_dir)["characters"]


@pytest.mark.parametrize("generation_mode", ["storyboard", "reference_video"])
@pytest.mark.parametrize("ledger_title", ["", "用户起的名字"])
async def test_whole_generation_keeps_the_ledger_title_over_the_model_title(
    tmp_path: Path, generation_mode: str, ledger_title: str
) -> None:
    """模型给出的标题照填、不采用：剧本与账本的集标题保持账本原值，空标题仍为空。"""
    project_dir = _write_ad_project(
        tmp_path,
        generation_mode,
        episodes=[{"episode": 1, "title": ledger_title, "script_file": "scripts/episode_1.json"}],
    )
    if generation_mode == "storyboard":
        response: dict[str, Any] = {"title": "共伞", "shots": [_shot("E1S01", characters=["小美"])]}
    else:
        response = {"title": "共伞", "units": [{"duration_seconds": 6, "text": "镜头1：@[小美] 撑开伞"}]}

    await _generator(project_dir, response).generate(1)

    assert _script(project_dir)["title"] == ledger_title
    assert _project(project_dir)["episodes"][0]["title"] == ledger_title


@pytest.mark.parametrize("generation_mode", ["storyboard", "reference_video"])
async def test_the_prompt_lists_registered_assets_with_aliases_and_asks_for_new_assets(
    tmp_path: Path, generation_mode: str
) -> None:
    project_dir = _write_ad_project(
        tmp_path, generation_mode, characters={"小美": {"description": "白领，短发", "aliases": ["姐姐"]}}
    )

    prompt = await ScriptGenerator(project_dir).build_prompt(1)

    assert "姐姐" in prompt
    assert "白领，短发" in prompt
    assert "new_assets" in prompt
