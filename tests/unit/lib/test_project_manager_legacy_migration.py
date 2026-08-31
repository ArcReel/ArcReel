"""测试 project.video_model_settings[model].resolution 在写 model_settings 时自动迁移。"""

import json

import pytest

from lib.project_manager import ProjectManager


@pytest.fixture
def pm_tmp(tmp_path):
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "project.json").write_text(
        json.dumps(
            {
                "video_model_settings": {"veo-3.1": {"resolution": "1080p"}},
            }
        )
    )
    return ProjectManager(tmp_path), tmp_path


def test_writing_model_settings_migrates_legacy(pm_tmp):
    pm, root = pm_tmp
    # 写入 new-style 配置
    project = json.loads((root / "demo" / "project.json").read_text())
    project["model_settings"] = {"gemini-aistudio/veo-3.1": {"resolution": "720p"}}
    pm.save_project("demo", project)

    saved = json.loads((root / "demo" / "project.json").read_text())
    # legacy 应被清空（特定模型那条）
    assert saved.get("video_model_settings", {}).get("veo-3.1") in (None, {})
    # new-style 保持
    assert saved["model_settings"]["gemini-aistudio/veo-3.1"]["resolution"] == "720p"


def test_save_without_model_settings_preserves_legacy(pm_tmp):
    """若本次保存未改动 model_settings，legacy 字段保留（读路径仍然兼容）。"""
    pm, root = pm_tmp
    project = json.loads((root / "demo" / "project.json").read_text())
    project["title"] = "hello"
    pm.save_project("demo", project)

    saved = json.loads((root / "demo" / "project.json").read_text())
    assert saved["video_model_settings"]["veo-3.1"]["resolution"] == "1080p"


def test_writing_model_settings_for_different_model_preserves_unrelated_legacy(pm_tmp):
    """迁移只影响命中的 model_id，未命中的 legacy 条目保留。"""
    pm, root = pm_tmp
    project = json.loads((root / "demo" / "project.json").read_text())
    # 添加一个不相关 legacy 项
    project["video_model_settings"]["some-other-model"] = {"resolution": "480p"}
    project["model_settings"] = {"gemini-aistudio/veo-3.1": {"resolution": "720p"}}
    pm.save_project("demo", project)

    saved = json.loads((root / "demo" / "project.json").read_text())
    assert saved.get("video_model_settings", {}).get("veo-3.1") in (None, {})
    # 不相关项保留
    assert saved["video_model_settings"]["some-other-model"]["resolution"] == "480p"


def test_legacy_dict_cleared_when_all_entries_migrated(pm_tmp):
    """legacy dict 完全为空时整个字段被删除（干净）。"""
    pm, root = pm_tmp
    project = json.loads((root / "demo" / "project.json").read_text())
    project["model_settings"] = {"gemini-aistudio/veo-3.1": {"resolution": "720p"}}
    pm.save_project("demo", project)

    saved = json.loads((root / "demo" / "project.json").read_text())
    # legacy dict 已整个删除
    assert "video_model_settings" not in saved


def test_empty_resolution_in_new_does_not_migrate_legacy(pm_tmp):
    """新 model_settings 里的 resolution 为空串/None 视为未配置，不触发迁移。"""
    pm, root = pm_tmp
    project = json.loads((root / "demo" / "project.json").read_text())
    project["model_settings"] = {"gemini-aistudio/veo-3.1": {"resolution": ""}}
    pm.save_project("demo", project)

    saved = json.loads((root / "demo" / "project.json").read_text())
    # legacy 保留（因为新字段没真正配置）
    assert saved["video_model_settings"]["veo-3.1"]["resolution"] == "1080p"
