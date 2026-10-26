"""资产只改名：分镜图与宫格的模型输入不含资产名，改名后保持现行；参考生视频与资产图的输入含名，改名后判过期。

选角项目 fixture 见同目录 ``conftest.py``。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.artifacts.artifact_activation import active_artifact_currency_resolver, register_current_artifact
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.generation.generation_result import observe_artifact_status
from lib.project.project_manager import ProjectManager
from lib.script.grid.models import GridGeneration, ReferenceImage, build_frame_chain

#: 与 ``conftest.py`` 选角项目的项目名一致。
PROJECT = "demo"

_GRID_ID = "grid_123456789abc"


def _status(pm: ProjectManager, key: ArtifactKey, artifact_path: str) -> str:
    project_dir = pm.get_project_path(PROJECT)
    resolver = active_artifact_currency_resolver(project_dir, pm.load_project(PROJECT))
    return observe_artifact_status(resolver=resolver, key=key, artifact_path=artifact_path)[0].value


def test_renaming_a_character_keeps_its_storyboards_current_and_marks_its_reference_video_stale(
    current_cast_artifacts: ProjectManager,
) -> None:
    pm = current_cast_artifacts
    storyboard = (ArtifactKey.episode_storyboard(1, "E1S01"), "storyboards/scene_E1S01.png")
    derivative_storyboard = (ArtifactKey.episode_storyboard(1, "E1S04"), "storyboards/scene_E1S04.png")
    video = (ArtifactKey.episode_video(2, "E2U1"), "reference_videos/E2U1.mp4")
    untouched_video = (ArtifactKey.episode_video(2, "E2U2"), "reference_videos/E2U2.mp4")
    for probe in (storyboard, derivative_storyboard, video, untouched_video):
        assert _status(pm, *probe) == "current"

    pm.rename_asset(PROJECT, "characters", "Alice", "Alicia")

    assert _status(pm, storyboard[0], storyboard[1]) == "current"
    assert _status(pm, *derivative_storyboard) == "current"
    # 参考生视频的声明行写作「<名称>的…」，模型输入含名：改名后过期是正确的。
    assert _status(pm, *video) == "stale"
    assert _status(pm, *untouched_video) == "current"
    # 资产图的提示词含资产名，同样判过期。
    assert _status(pm, ArtifactKey.asset_sheet("character", "Alicia"), "characters/Alicia.png") == "stale"


def _script(project_dir: Path) -> dict:
    return json.loads((project_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))


def _write_script(project_dir: Path, script: dict) -> None:
    (project_dir / "scripts" / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


def test_a_storyboard_that_mentions_the_renamed_character_stays_current(
    current_cast_artifacts: ProjectManager,
) -> None:
    """正文的 ``@[名称]`` 发给模型时是「图N」，改名把它改写成新名也不改变模型输入。"""

    pm = current_cast_artifacts
    project_dir = pm.get_project_path(PROJECT)
    script = _script(project_dir)
    script["segments"][0]["image_prompt"] = "@[Alice] 在庭院中行走"
    _write_script(project_dir, script)
    key = ArtifactKey.episode_storyboard(1, "E1S01")
    register_current_artifact(project_dir, key)

    pm.rename_asset(PROJECT, "characters", "Alice", "Alicia")

    assert "@[Alicia]" in _script(project_dir)["segments"][0]["image_prompt"]
    assert _status(pm, key, "storyboards/scene_E1S01.png") == "current"


@pytest.fixture
def current_grid(cast_projects: ProjectManager) -> ProjectManager:
    """一张引用 Alice 的 2×2 宫格，两格已切出分镜图，宫格与切格分镜都是现行。"""

    pm = cast_projects
    project_dir = pm.get_project_path(PROJECT)
    pm.update_project(PROJECT, lambda project: project.update(generation_mode="storyboard", grid_storyboard=True))
    (project_dir / "characters").mkdir(exist_ok=True)
    (project_dir / "characters" / "Alice.png").write_bytes(b"alice-sheet")
    pm.update_project_character_sheet(PROJECT, "Alice", "characters/Alice.png")
    register_current_artifact(project_dir, ArtifactKey.asset_sheet("character", "Alice"))
    members = ("E1S01", "E1S02")
    segments = [
        {
            "segment_id": resource_id,
            "novel_text": "庭院中行走",
            "image_prompt": {"scene": f"@[Alice] {resource_id} 的画面", "composition": {"shot_type": "Medium Shot"}},
            "video_prompt": {"action": f"{resource_id} 的动作"},
            "characters_in_segment": ["Alice"],
            "generated_assets": {
                "storyboard_image": f"storyboards/scene_{resource_id}.png",
                "grid_id": _GRID_ID,
                "grid_cell_index": index,
            },
        }
        for index, resource_id in enumerate(members)
    ]
    (project_dir / "scripts").mkdir(exist_ok=True)
    _write_script(project_dir, {"episode": 1, "segments": segments})
    pm.add_episode(PROJECT, 1, "第1集", "scripts/episode_1.json")
    grid = GridGeneration(
        id=_GRID_ID,
        episode=1,
        script_file="episode_1.json",
        scene_ids=list(members),
        grid_image_path=f"grids/{_GRID_ID}.png",
        rows=2,
        cols=2,
        cell_count=4,
        frame_chain=build_frame_chain(list(members), 2, 2),
        status="completed",
        prompt="grid",
        provider="provider",
        model="model",
        grid_size="grid_4",
        created_at="2026-01-01T00:00:00Z",
        split_at="2026-01-01T00:01:00Z",
        video_aspect_ratio="9:16",
        reference_images=[ReferenceImage(path="characters/Alice.png", name="Alice", ref_type="character")],
    )
    (project_dir / "grids").mkdir(exist_ok=True)
    (project_dir / "grids" / f"{_GRID_ID}.json").write_text(json.dumps(grid.to_dict()), encoding="utf-8")
    (project_dir / "grids" / f"{_GRID_ID}.png").write_bytes(b"composite")
    (project_dir / "storyboards").mkdir(exist_ok=True)
    register_current_artifact(project_dir, ArtifactKey.episode_grid(1, _GRID_ID))
    for resource_id in members:
        (project_dir / "storyboards" / f"scene_{resource_id}.png").write_bytes(resource_id.encode())
        register_current_artifact(project_dir, ArtifactKey.episode_storyboard(1, resource_id))
    return pm


_GRID_PROBES = (
    (ArtifactKey.episode_grid(1, _GRID_ID), f"grids/{_GRID_ID}.png"),
    (ArtifactKey.episode_storyboard(1, "E1S01"), "storyboards/scene_E1S01.png"),
    (ArtifactKey.episode_storyboard(1, "E1S02"), "storyboards/scene_E1S02.png"),
)


def test_renaming_a_character_keeps_its_grid_and_split_storyboards_current(current_grid: ProjectManager) -> None:
    pm = current_grid
    for probe in _GRID_PROBES:
        assert _status(pm, *probe) == "current"

    pm.rename_asset(PROJECT, "characters", "Alice", "Alicia")

    for probe in _GRID_PROBES:
        assert _status(pm, *probe) == "current"
    record = json.loads((pm.get_project_path(PROJECT) / "grids" / f"{_GRID_ID}.json").read_text(encoding="utf-8"))
    assert record["reference_images"] == [{"path": "characters/Alicia.png", "name": "Alicia", "ref_type": "character"}]


def test_replacing_the_grid_reference_image_still_marks_the_grid_stale(current_grid: ProjectManager) -> None:
    pm = current_grid
    pm.rename_asset(PROJECT, "characters", "Alice", "Alicia")

    (pm.get_project_path(PROJECT) / "characters" / "Alicia.png").write_bytes(b"regenerated-sheet")

    for probe in _GRID_PROBES:
        assert _status(pm, *probe) == "stale"


def test_renaming_the_owner_moves_a_derivative_grid_reference_along(current_grid: ProjectManager) -> None:
    pm = current_grid
    project_dir = pm.get_project_path(PROJECT)
    derivative_sheet = project_dir / "characters" / "derivatives" / "Alice" / "战损.png"
    derivative_sheet.parent.mkdir(parents=True)
    derivative_sheet.write_bytes(b"battle-worn")
    record_path = project_dir / "grids" / f"{_GRID_ID}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["reference_images"].append(
        {"path": "characters/derivatives/Alice/战损.png", "name": "Alice/战损", "ref_type": "character"}
    )
    record_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    for key, _path in _GRID_PROBES:
        register_current_artifact(project_dir, key)

    pm.rename_asset(PROJECT, "characters", "Alice", "Alicia")

    for probe in _GRID_PROBES:
        assert _status(pm, *probe) == "current"
    assert json.loads(record_path.read_text(encoding="utf-8"))["reference_images"][1] == {
        "path": "characters/derivatives/Alicia/战损.png",
        "name": "Alicia/战损",
        "ref_type": "character",
    }
