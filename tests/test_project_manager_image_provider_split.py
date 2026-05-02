"""读取一个含旧 image_backend 字段的项目时，
返回的 dict 应同时包含 image_provider_t2i / image_provider_i2i（不写盘）。"""

import json


def test_load_legacy_project_lazy_upgrades(tmp_path):
    from lib.project_manager import ProjectManager

    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    (proj_root / "demo").mkdir()
    project_file = proj_root / "demo" / "project.json"
    project_file.write_text(
        json.dumps(
            {
                "title": "demo",
                "image_backend": "openai/gpt-image-1",
            }
        ),
        encoding="utf-8",
    )

    pm = ProjectManager(projects_root=str(proj_root))
    data = pm.load_project("demo")
    assert data.get("image_provider_t2i") == "openai/gpt-image-1"
    assert data.get("image_provider_i2i") == "openai/gpt-image-1"
    # 旧字段保留（lazy upgrade 不删除）
    assert data.get("image_backend") == "openai/gpt-image-1"

    # 不写盘：磁盘文件未被改动（无 _t2i / _i2i key）
    on_disk = json.loads(project_file.read_text(encoding="utf-8"))
    assert "image_provider_t2i" not in on_disk
    assert "image_provider_i2i" not in on_disk


def test_load_project_with_split_fields_no_change(tmp_path):
    from lib.project_manager import ProjectManager

    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    (proj_root / "demo").mkdir()
    project_file = proj_root / "demo" / "project.json"
    project_file.write_text(
        json.dumps(
            {
                "title": "demo",
                "image_provider_t2i": "openai/gpt-image-1",
                "image_provider_i2i": "openai/gpt-image-1-edit",
            }
        ),
        encoding="utf-8",
    )

    pm = ProjectManager(projects_root=str(proj_root))
    data = pm.load_project("demo")
    assert data.get("image_provider_t2i") == "openai/gpt-image-1"
    assert data.get("image_provider_i2i") == "openai/gpt-image-1-edit"


def test_load_project_no_image_backend_at_all(tmp_path):
    """无 image_backend 字段也不应崩。"""
    from lib.project_manager import ProjectManager

    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    (proj_root / "demo").mkdir()
    (proj_root / "demo" / "project.json").write_text(
        json.dumps({"title": "demo"}),
        encoding="utf-8",
    )

    pm = ProjectManager(projects_root=str(proj_root))
    data = pm.load_project("demo")
    assert "image_provider_t2i" not in data
    assert "image_provider_i2i" not in data


def test_load_project_image_backend_invalid_format_skipped(tmp_path):
    """legacy image_backend 不含 / → 不进行 lazy upgrade。"""
    from lib.project_manager import ProjectManager

    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    (proj_root / "demo").mkdir()
    (proj_root / "demo" / "project.json").write_text(
        json.dumps({"title": "demo", "image_backend": "garbage-no-slash"}),
        encoding="utf-8",
    )

    pm = ProjectManager(projects_root=str(proj_root))
    data = pm.load_project("demo")
    assert "image_provider_t2i" not in data
    assert "image_provider_i2i" not in data
