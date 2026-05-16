"""测试 issue #542：旧 ``content_mode == "reference_video"`` 自动迁移到
``content_mode + generation_mode`` 两条独立维度。
"""

import json
from pathlib import Path

from lib.project_manager import ProjectManager


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_load_project_migrates_legacy_reference_video_content_mode(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "project.json",
        {
            "title": "Demo",
            "content_mode": "reference_video",  # 旧混维度值
            "style": "s",
            "episodes": [],
            "characters": {},
            "scenes": {},
            "props": {},
        },
    )

    pm = ProjectManager(projects_root=tmp_path)
    project = pm.load_project("demo")

    assert project["content_mode"] == "narration"
    assert project["generation_mode"] == "reference_video"

    # 落盘也已迁移
    saved = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    assert saved["content_mode"] == "narration"
    assert saved["generation_mode"] == "reference_video"


def test_load_project_preserves_existing_generation_mode(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "project.json",
        {
            "title": "Demo",
            "content_mode": "reference_video",
            "generation_mode": "reference_video",  # 已存在，不应被覆盖
            "style": "s",
            "episodes": [],
            "characters": {},
            "scenes": {},
            "props": {},
        },
    )

    pm = ProjectManager(projects_root=tmp_path)
    project = pm.load_project("demo")

    assert project["content_mode"] == "narration"
    assert project["generation_mode"] == "reference_video"


def test_load_script_migrates_legacy_reference_video_content_mode(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "project.json",
        {
            "title": "Demo",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "style": "s",
            "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
            "characters": {},
            "scenes": {},
            "props": {},
        },
    )
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "title": "E1",
            "content_mode": "reference_video",  # 旧混维度值
            "video_units": [],
        },
    )

    pm = ProjectManager(projects_root=tmp_path)
    script = pm.load_script("demo", "episode_1.json")

    assert script["content_mode"] == "narration"
    assert script["generation_mode"] == "reference_video"

    # 落盘也已迁移：data_validator 等旁路读取应直接拿到新结构
    saved = json.loads((project_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    assert saved["content_mode"] == "narration"
    assert saved["generation_mode"] == "reference_video"


def test_script_generator_load_project_json_applies_migration(tmp_path: Path) -> None:
    """ScriptGenerator 不走 ProjectManager.load_project，需自行触发迁移以确保
    self.content_mode 不会被旧值 "reference_video" 污染（PR #543 评审）。
    """
    from lib.script_generator import ScriptGenerator

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "title": "t",
                "content_mode": "reference_video",  # 旧混维度值
                "style": "s",
                "characters": {},
                "scenes": {},
                "props": {},
                "episodes": [],
            }
        ),
        encoding="utf-8",
    )

    gen = ScriptGenerator(project_dir)
    assert gen.content_mode == "narration"
    assert gen.project_json["generation_mode"] == "reference_video"


def test_load_project_no_migration_when_content_mode_is_narration(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    payload = {
        "title": "Demo",
        "content_mode": "narration",
        "style": "s",
        "episodes": [],
        "characters": {},
        "scenes": {},
        "props": {},
    }
    _write_json(project_dir / "project.json", payload)

    pm = ProjectManager(projects_root=tmp_path)
    project = pm.load_project("demo")

    assert project["content_mode"] == "narration"
    assert "generation_mode" not in project  # 不强行注入默认值
