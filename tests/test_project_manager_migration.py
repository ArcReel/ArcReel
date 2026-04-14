"""ProjectManager 懒迁移测试。"""

import json
from pathlib import Path

import pytest

from lib.project_manager import ProjectManager


@pytest.fixture
def pm(tmp_path: Path) -> ProjectManager:
    return ProjectManager(tmp_path)


def _write_project(pm: ProjectManager, name: str, data: dict) -> Path:
    project_dir = pm.projects_root / name
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return project_dir


def test_migrates_photographic_to_live_premium_drama(pm: ProjectManager):
    _write_project(pm, "p1", {"title": "P1", "style": "Photographic"})
    data = pm.load_project("p1")
    assert data["style_template_id"] == "live_premium_drama"
    assert "真人电视剧" in data["style"] or "精品短剧" in data["style"]


def test_migrates_anime_to_kyoto(pm: ProjectManager):
    _write_project(pm, "p2", {"title": "P2", "style": "Anime"})
    data = pm.load_project("p2")
    assert data["style_template_id"] == "anim_kyoto"


def test_migrates_3d_animation_to_3d_cg(pm: ProjectManager):
    _write_project(pm, "p3", {"title": "P3", "style": "3D Animation"})
    data = pm.load_project("p3")
    assert data["style_template_id"] == "anim_3d_cg"


def test_prefers_style_image_over_template_when_both_present(pm: ProjectManager):
    _write_project(
        pm,
        "p4",
        {
            "title": "P4",
            "style": "Photographic",
            "style_image": "reference.png",
            "style_description": "已分析",
        },
    )
    data = pm.load_project("p4")
    assert data["style_template_id"] is None
    assert data["style"] == ""
    assert data["style_image"] == "reference.png"


def test_unknown_legacy_value_untouched(pm: ProjectManager):
    _write_project(pm, "p5", {"title": "P5", "style": "某种自由文本"})
    data = pm.load_project("p5")
    assert "style_template_id" not in data  # 未写入
    assert data["style"] == "某种自由文本"


def test_already_migrated_project_idempotent(pm: ProjectManager):
    _write_project(
        pm,
        "p6",
        {
            "title": "P6",
            "style": "画风：真人电视剧风格，精品短剧画风，大师级构图",
            "style_template_id": "live_premium_drama",
        },
    )
    data = pm.load_project("p6")
    assert data["style_template_id"] == "live_premium_drama"
    # 二次 load 不变
    data2 = pm.load_project("p6")
    assert data2 == data


def test_migration_persists_to_disk(pm: ProjectManager, tmp_path: Path):
    _write_project(pm, "p7", {"title": "P7", "style": "Photographic"})
    pm.load_project("p7")
    raw = json.loads((tmp_path / "p7" / "project.json").read_text(encoding="utf-8"))
    assert raw["style_template_id"] == "live_premium_drama"
