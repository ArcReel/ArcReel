"""脚本规划落盘前由服务端发号（#3430）：模型或草稿写的编号不算数，晋升时已发过的编号沿用。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.project.project_manager import ProjectManager
from server.text_generation import reissue_plan_item_ids


def _project(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo", content_mode="drama")
    pm.create_project_metadata("demo", "Demo", "Anime", "drama")
    scenes = [
        {
            "scene_id": scene_id,
            "duration_seconds": 6,
            "characters_in_scene": [],
            "utterances": [{"kind": "voiceover", "speaker": None, "text": "风吹过旷野。"}],
            "image_prompt": None,
            "video_prompt": None,
            "generated_assets": {},
        }
        for scene_id in ("E1S01", "E1S02", "E1S03")
    ]
    pm.save_script(
        "demo", {"episode": 1, "title": "第一集", "content_mode": "drama", "scenes": scenes}, "episode_1.json"
    )
    script = pm.load_script("demo", "episode_1.json")
    script["scenes"] = script["scenes"][:1]
    pm.save_script("demo", script, "episode_1.json")
    return pm


async def test_generated_plan_takes_numbers_after_every_used_one(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    plan: dict[str, Any] = {"scenes": [{"scene_id": "E1S02"}, {"scene_id": "E1S01"}, {}]}

    await reissue_plan_item_ids(pm, "demo", 1, plan)

    assert [scene["scene_id"] for scene in plan["scenes"]] == ["E1S04", "E1S05", "E1S06"]
    again: dict[str, Any] = {"scenes": [{"scene_id": "E1S04"}]}
    await reissue_plan_item_ids(pm, "demo", 1, again)
    assert again["scenes"][0]["scene_id"] == "E1S07"


async def test_promotion_keeps_ids_already_issued_to_the_plan(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    formal = pm.reserve_item_ids("demo", 1, "scenes", 2)  # 盘上正式规划此前发到的编号
    plan: dict[str, Any] = {"scenes": [{"scene_id": "E1S05"}, {"scene_id": "E1S05"}, {"scene_id": "E1S02"}]}

    await reissue_plan_item_ids(pm, "demo", 1, plan, keep=formal)

    # 重复出现的已发编号只沿用第一次；不在已发编号里的一律换新号
    assert formal == ["E1S04", "E1S05"]
    assert [scene["scene_id"] for scene in plan["scenes"]] == ["E1S05", "E1S06", "E1S07"]
