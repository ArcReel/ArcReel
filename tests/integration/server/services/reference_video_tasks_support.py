"""server.services.reference_video_tasks 测试共享的替身与 helper。"""

from __future__ import annotations

import json
from pathlib import Path

from lib.project_migrations.runner import migrate_project_dir


def load_project_and_unit(proj_dir: Path, unit_id: str) -> tuple[dict, dict]:
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    script = json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    unit = next(u for u in script["video_units"] if u["unit_id"] == unit_id)
    return project, unit


_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x04\x00\x00\x00\x04"
    b"\x08\x02\x00\x00\x00&\x93\t)\x00\x00\x00\x13IDATx\x9cc<\x91b\xc4\x00"
    b"\x03Lp\x16^\x0e\x00E\xf6\x01f\xac\xf5\x15\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
)


def write_project(tmp_path: Path, *, register_script: bool = True) -> Path:
    project = {
        "title": "T",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "style": "s",
        "characters": {"张三": {"description": "x", "character_sheet": "characters/张三.png"}},
        "scenes": {"酒馆": {"description": "x", "scene_sheet": "scenes/酒馆.png"}},
        "props": {},
        "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
    }
    script = {
        "episode": 1,
        "title": "E1",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "summary": "x",
        "novel": {"title": "t", "chapter": "c"},
        "duration_seconds": 8,
        "video_units": [
            {
                "unit_id": "E1U1",
                "text": "@张三 推门，走进 @酒馆",
                "duration_seconds": 3,
                "transition_to_next": "cut",
                "note": None,
                "generated_assets": {
                    "storyboard_image": None,
                    "storyboard_last_image": None,
                    "grid_id": None,
                    "grid_cell_index": None,
                    "video_clip": None,
                    "video_uri": None,
                    "status": "pending",
                },
            },
        ],
    }
    proj_dir = tmp_path / "demo"
    proj_dir.mkdir()
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (proj_dir / "scripts").mkdir()
    (proj_dir / "scripts" / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    (proj_dir / "characters").mkdir()
    (proj_dir / "characters" / "张三.png").write_bytes(_TINY_PNG)
    (proj_dir / "scenes").mkdir()
    (proj_dir / "scenes" / "酒馆.png").write_bytes(_TINY_PNG)
    _activate_project_manifest(proj_dir, register_script=register_script)
    return proj_dir


def register_asset_sheet(proj_dir: Path, asset_type: str, name: str, relative_path: str) -> None:
    """把新增资产补成生产形态：sheet 文件在盘上，且在产物清单里登记。

    调用前 project.json 必须已经写盘并带上该资产的 sheet 指针——清单登记的依据来自
    project.json 的当前指针，只改内存里的 project 副本不构成一个可登记的资产。
    """

    from lib.artifact_activation import register_current_artifact
    from lib.artifact_manifest import ArtifactKey

    path = proj_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(_TINY_PNG)
    register_current_artifact(proj_dir, ArtifactKey.asset_sheet(asset_type, name))


def _activate_project_manifest(proj_dir: Path, *, register_script: bool = True) -> None:
    """Activate the fixture through the production v7 -> v8 boundary, then finish the chain."""

    from lib.artifact_activation import activate_artifact_target_state
    from lib.artifact_manifest import (
        ArtifactBasis,
        ArtifactKey,
        ArtifactManifest,
        ProjectArtifactManifestAdapter,
    )

    project_path = proj_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["schema_version"] = 7
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert activate_artifact_target_state(proj_dir, bump_schema=True) is True
    # 清单激活只落到 v8；产物读写要求当前 schema，故补齐剩余迁移链。
    migrate_project_dir(proj_dir)
    if register_script:
        ArtifactManifest(ProjectArtifactManifestAdapter(proj_dir)).register(
            ArtifactKey.episode_script(1),
            artifact_path="scripts/episode_1.json",
            basis=ArtifactBasis.build("test/episode-script", kind_version=1, inputs={}),
        )
