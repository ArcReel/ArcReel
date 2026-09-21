"""广告/短片的提示词编写：已有正式脚本时只填待编写的分镜 / 单元，尚无正式脚本时整份生成。"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.artifacts.artifact_activation import activate_artifact_target_state
from lib.config.resolver import ConfigResolver
from lib.project.project_manager import ProjectManager
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.script_batch_edit import ScriptBatchEditCommand, ScriptBatchEditor, blank_item_after, script_revision
from lib.script.script_generator import ScriptGenerator
from lib.script.script_models import PENDING_AUTHORING_FIELD
from tests.fakes import FakeConfigResolver

pytestmark = pytest.mark.asyncio


def _write_ad_project(tmp_path: Path, generation_mode: str) -> Path:
    project_dir = tmp_path / "ad"
    project_dir.mkdir()
    payload = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "title": "速干杯",
        "content_mode": "ad",
        "generation_mode": generation_mode,
        "target_duration": 30,
        "brief": "突出速干卖点",
        "overview": {"synopsis": "带货短片"},
        "characters": {"小美": {"description": "白领"}},
        "scenes": {},
        "props": {},
        "products": {"速干杯": {"description": "随行杯", "selling_points": ["30 秒速干"]}},
        "style": "实拍",
        "style_description": "真实质感",
        "aspect_ratio": "9:16",
        "episodes": [{"episode": 1, "title": "", "script_file": "scripts/episode_1.json"}],
    }
    (project_dir / "project.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    activate_artifact_target_state(project_dir, bump_schema=False)
    return project_dir


def _generator(project_dir: Path, responses: list[str]) -> ScriptGenerator:
    """按调用次序吐出预置响应；多调一次即耗尽，用例据此断言「本轮没有调用模型」。"""
    text_generator = MagicMock()
    text_generator.model = "mock"
    text_generator.generate = AsyncMock(side_effect=[MagicMock(text=text) for text in responses])
    return ScriptGenerator(
        project_dir,
        generator=text_generator,
        config_resolver=cast(ConfigResolver, FakeConfigResolver(supported_durations=(4, 6, 8))),
    )


def _script_path(project_dir: Path) -> Path:
    return project_dir / "scripts" / "episode_1.json"


def _items(project_dir: Path, key: str, id_field: str) -> dict[str, dict[str, Any]]:
    script = json.loads(_script_path(project_dir).read_text(encoding="utf-8"))
    return {item[id_field]: item for item in script[key]}


def _item_json(project_dir: Path, key: str, id_field: str, item_id: str) -> str:
    return json.dumps(_items(project_dir, key, id_field)[item_id], ensure_ascii=False, sort_keys=True)


def _rewrite_script(project_dir: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    script = json.loads(_script_path(project_dir).read_text(encoding="utf-8"))
    mutate(script)
    _script_path(project_dir).write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


def _shot(shot_id: str, *, voiceover: str) -> dict[str, Any]:
    return {
        "shot_id": shot_id,
        "section": "hook",
        "duration_seconds": 4,
        "voiceover_text": voiceover,
        "characters_in_shot": [],
        "scenes": [],
        "props": [],
        "products_in_shot": ["速干杯"],
        "image_prompt": {
            "scene": "速干杯特写，水珠挂在杯壁上",
            "composition": {"shot_type": "Close-up", "lighting": "柔和顶光", "ambiance": "清爽"},
        },
        "video_prompt": {
            "action": "水珠从杯壁滑落，杯身迅速恢复干爽",
            "camera_motion": "Static",
            "ambiance_audio": "水声",
            "dialogue": [],
        },
    }


def _shot_visual(*shot_ids: str, mark: str) -> str:
    return json.dumps(
        {
            "shots": [
                {
                    "shot_id": shot_id,
                    "image_prompt": {
                        "scene": f"{mark}-{shot_id}",
                        "composition": {"shot_type": "Medium Shot", "lighting": "自然光", "ambiance": "明亮"},
                    },
                    "video_prompt": {
                        "action": "小美拧开杯盖喝水",
                        "camera_motion": "Static",
                        "ambiance_audio": "办公室环境声",
                        "dialogue": [],
                    },
                }
                for shot_id in shot_ids
            ]
        },
        ensure_ascii=False,
    )


async def _storyboard_script(tmp_path: Path) -> Path:
    """尚无正式脚本时整份生成两个分镜，得到一份无待编写标记的 ad 正式脚本。"""
    project_dir = _write_ad_project(tmp_path, "storyboard")
    response = {
        "title": "速干杯短片",
        "shots": [_shot("E1S01", voiceover="还在等杯子干？"), _shot("E1S02", voiceover="30 秒，倒扣即干。")],
    }
    await _generator(project_dir, [json.dumps(response, ensure_ascii=False)]).generate(1)
    return project_dir


async def _reference_script(tmp_path: Path) -> Path:
    project_dir = _write_ad_project(tmp_path, "reference_video")
    response = {
        "title": "速干杯短片",
        "units": [
            {"duration_seconds": 5, "text": "@[小美] 在工位上举起 @[速干杯]，杯壁挂满水珠。"},
            {"duration_seconds": 6, "text": "@[速干杯] 倒扣在桌面，水珠迅速消失。"},
        ],
    }
    await _generator(project_dir, [json.dumps(response, ensure_ascii=False)]).generate(1)
    return project_dir


class TestAdShotAuthoring:
    async def test_manually_added_shot_is_the_only_one_authored(self, tmp_path: Path) -> None:
        project_dir = await _storyboard_script(tmp_path)

        def add_pending_shot(script: dict[str, Any]) -> None:
            shot = copy.deepcopy(script["shots"][1])
            shot.update(shot_id="E1S03", voiceover_text="下单就送杯刷。", image_prompt=None, video_prompt=None)
            shot[PENDING_AUTHORING_FIELD] = True
            script["shots"].append(shot)

        _rewrite_script(project_dir, add_pending_shot)
        before = {shot_id: _item_json(project_dir, "shots", "shot_id", shot_id) for shot_id in ("E1S01", "E1S02")}

        rewritten: list[str] = []
        authoring = _generator(project_dir, [_shot_visual("E1S03", mark="补写")])
        prompt = await authoring.build_prompt(1)
        await authoring.generate(1, rewritten_entry_ids=rewritten)

        assert "【待编写】" in prompt
        assert "下单就送杯刷。" in prompt
        assert rewritten == ["E1S03"]
        assert {
            shot_id: _item_json(project_dir, "shots", "shot_id", shot_id) for shot_id in ("E1S01", "E1S02")
        } == before
        added = _items(project_dir, "shots", "shot_id")["E1S03"]
        assert added["image_prompt"]["scene"] == "补写-E1S03"
        assert added["voiceover_text"] == "下单就送杯刷。"
        assert PENDING_AUTHORING_FIELD not in added

    async def test_timeline_insert_and_remove_survive_default_authoring(self, tmp_path: Path) -> None:
        """时间线新增 / 移除分镜之后编写：新增的空分镜被填充，移除的分镜不会被生成回来。"""
        project_dir = await _storyboard_script(tmp_path)
        pm = ProjectManager(str(project_dir.parent))
        editor = ScriptBatchEditor(pm)

        def edit(operation: dict[str, Any]) -> None:
            current = pm.load_script(project_dir.name, "episode_1.json")
            command = ScriptBatchEditCommand.model_validate(
                {"script": "episode_1.json", "expected_revision": script_revision(current), "operations": [operation]}
            )
            result = editor.execute(project_dir.name, command)
            assert result.success is True, result.problems

        item = blank_item_after(pm.load_script(project_dir.name, "episode_1.json"), "E1S01")
        edit({"op": "insert_after", "after_id": "E1S01", "item": item})
        edit({"op": "remove", "id": "E1S02"})
        before_first = _item_json(project_dir, "shots", "shot_id", "E1S01")

        rewritten: list[str] = []
        await _generator(project_dir, [_shot_visual("E1S03", mark="补写")]).generate(1, rewritten_entry_ids=rewritten)

        assert rewritten == ["E1S03"]
        assert list(_items(project_dir, "shots", "shot_id")) == ["E1S01", "E1S03"]
        added = _items(project_dir, "shots", "shot_id")["E1S03"]
        assert added["image_prompt"]["scene"] == "补写-E1S03"
        assert PENDING_AUTHORING_FIELD not in added
        assert _item_json(project_dir, "shots", "shot_id", "E1S01") == before_first

    async def test_nothing_pending_leaves_the_script_untouched(self, tmp_path: Path) -> None:
        project_dir = await _storyboard_script(tmp_path)
        before = _script_path(project_dir).read_bytes()

        rewritten: list[str] = []
        await _generator(project_dir, []).generate(1, rewritten_entry_ids=rewritten)

        assert rewritten == []
        assert _script_path(project_dir).read_bytes() == before

    async def test_entry_ids_rewrite_only_the_visual_layer(self, tmp_path: Path) -> None:
        project_dir = await _storyboard_script(tmp_path)
        _rewrite_script(project_dir, lambda script: script["shots"][0].__setitem__("note", "用户备注"))
        before_first = _items(project_dir, "shots", "shot_id")["E1S01"]
        before_second = _item_json(project_dir, "shots", "shot_id", "E1S02")

        await _generator(project_dir, [_shot_visual("E1S01", mark="点名")]).generate(1, entry_ids=["E1S01"])

        after_first = _items(project_dir, "shots", "shot_id")["E1S01"]
        assert after_first["image_prompt"]["scene"] == "点名-E1S01"
        visual = {"image_prompt", "video_prompt"}
        assert {k: v for k, v in after_first.items() if k not in visual} == {
            k: v for k, v in before_first.items() if k not in visual
        }
        assert _item_json(project_dir, "shots", "shot_id", "E1S02") == before_second


class TestAdReferenceUnitAuthoring:
    async def test_manually_added_unit_is_the_only_one_rewritten(self, tmp_path: Path) -> None:
        project_dir = await _reference_script(tmp_path)
        existing_ids = list(_items(project_dir, "video_units", "unit_id"))

        def add_pending_unit(script: dict[str, Any]) -> None:
            unit = copy.deepcopy(script["video_units"][1])
            unit.update(unit_id="E1U03", text="@[小美] 把 @[速干杯] 放进包里")
            unit[PENDING_AUTHORING_FIELD] = True
            script["video_units"].append(unit)

        _rewrite_script(project_dir, add_pending_unit)
        before = {unit_id: _item_json(project_dir, "video_units", "unit_id", unit_id) for unit_id in existing_ids}
        added_before = _items(project_dir, "video_units", "unit_id")["E1U03"]
        response = {"title": "速干杯短片", "units": [{"text": "中景，平视。@[小美] 把 @[速干杯] 塞进通勤包侧袋。"}]}

        rewritten: list[str] = []
        await _generator(project_dir, [json.dumps(response, ensure_ascii=False)]).generate(
            1, rewritten_entry_ids=rewritten
        )

        assert rewritten == ["E1U03"]
        assert {
            unit_id: _item_json(project_dir, "video_units", "unit_id", unit_id) for unit_id in existing_ids
        } == before
        added = _items(project_dir, "video_units", "unit_id")["E1U03"]
        assert added["text"] == "中景，平视。@[小美] 把 @[速干杯] 塞进通勤包侧袋。"
        assert added["duration_seconds"] == added_before["duration_seconds"]
        assert PENDING_AUTHORING_FIELD not in added
