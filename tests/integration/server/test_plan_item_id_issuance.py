"""脚本规划落盘前由服务端发号：模型或草稿写的编号不算数，晋升时已发过的编号沿用。"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from lib.project import project_manager
from lib.project.project_manager import ProjectManager
from server.text_generation import reissue_plan_item_ids
from tests.fakes import use_project_json_write


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


async def test_cancelled_plan_issuance_waits_for_the_project_write(tmp_path: Path, monkeypatch) -> None:
    pm = _project(tmp_path)
    plan: dict[str, Any] = {"scenes": [{"scene_id": "model-id"}]}
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    write = project_manager.atomic_write_json

    def blocked_write(path, content):
        loop.call_soon_threadsafe(started.set)
        if not release.wait(5):
            raise TimeoutError("test did not release project write")
        return write(path, content)

    use_project_json_write(monkeypatch, blocked_write)
    issuing = asyncio.create_task(reissue_plan_item_ids(pm, "demo", 1, plan))
    try:
        await asyncio.wait_for(started.wait(), 5)
        issuing.cancel()
        tick = asyncio.Event()
        loop.call_soon(tick.set)
        await tick.wait()
        assert not issuing.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await issuing
    assert pm.load_project("demo")["episodes"][0]["item_id_high_water"] == 4
    assert plan == {"scenes": [{"scene_id": "model-id"}]}
