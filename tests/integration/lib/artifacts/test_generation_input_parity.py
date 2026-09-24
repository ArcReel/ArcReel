"""登记依据与期望依据同源：执行器经 ``freeze()`` 得到的依据等于目标态规划器经 ``expected_basis()`` 投影的依据。

同一份项目覆盖衍生引用、商品（资产图 + 原图）与上一分镜图；激活模式下规划器看得到本轮已规划的
资产图与上一分镜图，引用了未能登记资产图的分镜图不登记、进迁移报告。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from lib.artifacts.artifact_activation import (
    activate_artifact_target_state,
    plan_artifact_target_state,
    resolve_current_artifact_basis,
)
from lib.artifacts.artifact_manifest import ArtifactBasis, ArtifactInputClaim, ArtifactKey
from lib.artifacts.generation_input import StoryboardImageInput, project_input_observation, storyboard_image_input
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION

TARGET_KEY = ArtifactKey.episode_storyboard(1, "E1S02")

_IMAGES = (
    "characters/张三.png",
    "characters/derivatives/张三/劲装.png",
    "scenes/祠堂.png",
    "products/保温杯.png",
    "products/refs/保温杯_1.jpg",
    "storyboards/scene_E1S01.png",
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_image(path: Path, shade: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), (shade * 30 % 256, 90, 160)).save(path, format="PNG")


def _shot(shot_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "shot_id": shot_id,
        "section": "hook",
        "duration_seconds": 4,
        "voiceover_text": "旁白",
        "image_prompt": "@[保温杯]在@[祠堂]里，@[张三/劲装]端详",
        "video_prompt": {"action": "端详", "camera_motion": "Static"},
        "characters_in_shot": [],
        "scenes": [],
        "props": [],
        "products_in_shot": [],
        **fields,
    }


def _project_dir(tmp_path: Path, *, target_generated: bool = False) -> Path:
    project_dir = tmp_path / "demo"
    for index, relative in enumerate(_IMAGES):
        _write_image(project_dir / relative, index)
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "title": "demo",
            "content_mode": "ad",
            "generation_mode": "storyboard",
            "aspect_ratio": "9:16",
            "style": "Anime",
            "style_description": "cinematic",
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
            "characters": {
                "张三": {
                    "description": "主角",
                    "character_sheet": "characters/张三.png",
                    "derivatives": {
                        "劲装": {"description": "换上劲装", "character_sheet": "characters/derivatives/张三/劲装.png"}
                    },
                }
            },
            "scenes": {"祠堂": {"description": "古旧祠堂", "scene_sheet": "scenes/祠堂.png"}},
            "props": {},
            "products": {
                "保温杯": {
                    "description": "不锈钢保温杯",
                    "product_sheet": "products/保温杯.png",
                    "reference_images": ["products/refs/保温杯_1.jpg"],
                }
            },
        },
    )
    target = _shot(
        "E1S02",
        characters_in_shot=["张三/劲装"],
        scenes=["祠堂"],
        products_in_shot=["保温杯"],
    )
    if target_generated:
        _write_image(project_dir / "storyboards" / "scene_E1S02.png", 9)
        target["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S02.png"}
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "content_mode": "ad",
            "shots": [
                _shot("E1S01", generated_assets={"storyboard_image": "storyboards/scene_E1S01.png"}),
                target,
            ],
        },
    )
    return project_dir


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _unbound(claims: Sequence[ArtifactInputClaim], _digests: Mapping[str, str]) -> Sequence[ArtifactInputClaim]:
    return claims


def _executor_input(project_dir: Path) -> StoryboardImageInput:
    generation_input = storyboard_image_input(
        _read_json(project_dir / "project.json"),
        _read_json(project_dir / "scripts" / "episode_1.json"),
        episode=1,
        resource_id="E1S02",
        observation=project_input_observation(project_dir),
    )
    assert isinstance(generation_input, StoryboardImageInput), generation_input
    return generation_input


def _registration_basis(project_dir: Path) -> ArtifactBasis:
    generation_input = _executor_input(project_dir)
    assert [(ref.visual.role, ref.visual.logical_id) for ref in generation_input.references] == [
        ("asset_sheet", "保温杯"),
        ("source", "保温杯"),
        ("asset_sheet", "张三/劲装"),
        ("asset_sheet", "祠堂"),
        ("previous_storyboard", "E1S01"),
    ]
    with generation_input.freeze(max_reference_images=2, model="m", bind_claims=_unbound) as frozen:
        return frozen.basis


def test_executor_registration_basis_equals_the_planner_expected_basis(tmp_path: Path) -> None:
    """提交后模式：规划器按磁盘清单判定资产图与上一分镜图可用。"""

    project_dir = _project_dir(tmp_path, target_generated=True)
    activate_artifact_target_state(project_dir, bump_schema=False)

    expected = resolve_current_artifact_basis(project_dir, TARGET_KEY)

    assert expected is not None
    assert _registration_basis(project_dir) == expected


def test_activation_sees_the_sheets_and_previous_storyboard_planned_in_the_same_round(tmp_path: Path) -> None:
    """激活模式：清单尚未写入，规划器按本轮已规划的条目判定可用。"""

    project_dir = _project_dir(tmp_path, target_generated=True)

    plan = plan_artifact_target_state(project_dir)
    activate_artifact_target_state(project_dir, bump_schema=False, plan=plan)

    assert plan.entries[TARGET_KEY].basis_digest == _registration_basis(project_dir).digest


def test_activation_skips_a_storyboard_whose_referenced_sheet_cannot_be_registered(tmp_path: Path) -> None:
    project_dir = _project_dir(tmp_path, target_generated=True)
    project = _read_json(project_dir / "project.json")
    project["scenes"]["祠堂"]["description"] = ""
    _write_json(project_dir / "project.json", project)

    plan = plan_artifact_target_state(project_dir)

    assert ArtifactKey.asset_sheet("scene", "祠堂") not in plan.entries
    assert TARGET_KEY not in plan.entries
    assert ArtifactKey.episode_storyboard(1, "E1S01") in plan.entries
    [skipped] = [item for item in plan.skipped if item.resource_id == "E1S02"]
    assert skipped.artifact_path == "storyboards/scene_E1S02.png"
    assert "reference_asset_missing" in skipped.reason


def test_activation_reports_pending_prompt_and_reference_gap_together(tmp_path: Path) -> None:
    project_dir = _project_dir(tmp_path, target_generated=True)
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["shots"][1]["image_prompt"] = None
    script["shots"][1]["characters_in_shot"] = ["未登记角色"]
    _write_json(script_path, script)

    plan = plan_artifact_target_state(project_dir)

    [skipped] = [item for item in plan.skipped if item.resource_id == "E1S02"]
    assert "script_prompt_pending" in skipped.reason
    assert "reference_asset_unregistered" in skipped.reason


@pytest.mark.parametrize(
    "invalid_fields",
    [
        pytest.param({"products_in_shot": "保温杯"}, id="malformed-reference-field"),
        pytest.param({"image_prompt": 42}, id="malformed-image-prompt"),
    ],
)
def test_activation_reports_a_storyboard_with_invalid_generation_input(tmp_path: Path, invalid_fields) -> None:
    project_dir = _project_dir(tmp_path, target_generated=True)
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["shots"][1].update(invalid_fields)
    _write_json(script_path, script)

    plan = plan_artifact_target_state(project_dir)

    assert TARGET_KEY not in plan.entries
    [skipped] = [item for item in plan.skipped if item.resource_id == "E1S02"]
    assert skipped.artifact_path == "storyboards/scene_E1S02.png"
    assert next(iter(invalid_fields)) in skipped.reason
