"""Test automatic migration of project.video_model_settings[model].resolution when writing model_settings."""

import json

import pytest

from lib.project.project_manager import ProjectManager


@pytest.fixture
def pm_tmp(tmp_path):
    pm = ProjectManager(tmp_path)
    (pm.projects_dir / "demo").mkdir()
    (pm.projects_dir / "demo" / "project.json").write_text(
        json.dumps(
            {
                "video_model_settings": {"veo-3.1": {"resolution": "1080p"}},
            }
        )
    )
    return pm, pm.projects_dir


def test_writing_model_settings_migrates_legacy(pm_tmp):
    pm, root = pm_tmp
    # Write new-style configuration.
    project = json.loads((root / "demo" / "project.json").read_text())
    project["model_settings"] = {"gemini-aistudio/veo-3.1": {"resolution": "720p"}}
    pm.save_project("demo", project)

    saved = json.loads((root / "demo" / "project.json").read_text())
    # Clear legacy configuration for this particular model.
    assert saved.get("video_model_settings", {}).get("veo-3.1") in (None, {})
    # Preserve new-style configuration.
    assert saved["model_settings"]["gemini-aistudio/veo-3.1"]["resolution"] == "720p"


def test_save_without_model_settings_preserves_legacy(pm_tmp):
    """If this save does not change model_settings, retain legacy fields, still supported by reads."""
    pm, root = pm_tmp
    project = json.loads((root / "demo" / "project.json").read_text())
    project["title"] = "hello"
    pm.save_project("demo", project)

    saved = json.loads((root / "demo" / "project.json").read_text())
    assert saved["video_model_settings"]["veo-3.1"]["resolution"] == "1080p"


def test_writing_model_settings_for_different_model_preserves_unrelated_legacy(pm_tmp):
    """Migrate only the matching model_id; preserve unrelated legacy entries."""
    pm, root = pm_tmp
    project = json.loads((root / "demo" / "project.json").read_text())
    # Add an unrelated legacy entry.
    project["video_model_settings"]["some-other-model"] = {"resolution": "480p"}
    project["model_settings"] = {"gemini-aistudio/veo-3.1": {"resolution": "720p"}}
    pm.save_project("demo", project)

    saved = json.loads((root / "demo" / "project.json").read_text())
    assert saved.get("video_model_settings", {}).get("veo-3.1") in (None, {})
    # Preserve the unrelated entry.
    assert saved["video_model_settings"]["some-other-model"]["resolution"] == "480p"


def test_legacy_dict_cleared_when_all_entries_migrated(pm_tmp):
    """Remove the whole legacy field once its dict is completely empty."""
    pm, root = pm_tmp
    project = json.loads((root / "demo" / "project.json").read_text())
    project["model_settings"] = {"gemini-aistudio/veo-3.1": {"resolution": "720p"}}
    pm.save_project("demo", project)

    saved = json.loads((root / "demo" / "project.json").read_text())
    # The entire legacy dict is deleted.
    assert "video_model_settings" not in saved


def test_empty_resolution_in_new_does_not_migrate_legacy(pm_tmp):
    """Empty-string/None resolution in new model_settings means unconfigured and does not trigger migration."""
    pm, root = pm_tmp
    project = json.loads((root / "demo" / "project.json").read_text())
    project["model_settings"] = {"gemini-aistudio/veo-3.1": {"resolution": ""}}
    pm.save_project("demo", project)

    saved = json.loads((root / "demo" / "project.json").read_text())
    # Retain legacy because the new field is not truly configured.
    assert saved["video_model_settings"]["veo-3.1"]["resolution"] == "1080p"
