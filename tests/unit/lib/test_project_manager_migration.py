"""ProjectManager 懒迁移测试。"""

import json
from pathlib import Path

import pytest

from lib.project_manager import ProjectManager
from lib.style_templates import resolve_template_prompt


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
    assert data["style"] == resolve_template_prompt("live_premium_drama")


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


def test_legacy_value_with_existing_template_id_untouched(pm: ProjectManager):
    """若 project 已经带 style_template_id，即使 style 值还是 legacy 标签也不再动。"""
    _write_project(
        pm,
        "p-existing",
        {
            "title": "PE",
            "style": "Photographic",
            "style_template_id": "anim_kyoto",
        },
    )
    data = pm.load_project("p-existing")
    assert data["style_template_id"] == "anim_kyoto"
    assert data["style"] == "Photographic"  # 原样保留


def test_migration_does_not_touch_updated_at(pm: ProjectManager, tmp_path: Path):
    """迁移写回不应污染 updated_at。"""
    original = "2020-01-01T00:00:00Z"
    _write_project(
        pm,
        "p-timestamp",
        {"title": "PT", "style": "Photographic", "updated_at": original},
    )
    pm.load_project("p-timestamp")
    raw = json.loads((tmp_path / "p-timestamp" / "project.json").read_text(encoding="utf-8"))
    assert raw["updated_at"] == original
    assert raw["style_template_id"] == "live_premium_drama"


def test_migration_emits_change_hint(pm: ProjectManager):
    """迁移写回后应触发 project change hint，供 SSE 订阅者感知。"""
    from lib.project_change_hints import register_project_change_listener

    events: list[tuple[str, str, tuple[str, ...]]] = []
    unregister = register_project_change_listener(lambda name, source, paths: events.append((name, source, paths)))
    try:
        _write_project(pm, "p-hint", {"title": "PH", "style": "Photographic"})
        pm.load_project("p-hint")
    finally:
        unregister()

    assert any(name == "p-hint" and "project.json" in paths for name, _source, paths in events)


def test_concurrent_migration_does_not_lose_data(pm: ProjectManager):
    """两线程同时触发迁移应该保持一致，不会产生竞态。"""
    import threading

    _write_project(pm, "p-concurrent", {"title": "PC", "style": "Photographic"})

    results = []
    errors = []

    def worker():
        try:
            results.append(pm.load_project("p-concurrent"))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发 load 抛异常: {errors}"
    # 两个结果一致，都完成了迁移
    assert all(r["style_template_id"] == "live_premium_drama" for r in results)
    # 磁盘上最终也是迁移后状态
    final = pm.load_project("p-concurrent")
    assert final["style_template_id"] == "live_premium_drama"
