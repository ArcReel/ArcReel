import json
from pathlib import Path

from lib.project.data_validator import DataValidator
from lib.project.project_migrations.backups import versioned_backup_name


def test_tree_diagnostics_ignore_migration_backups_but_report_unknown_entries(tmp_path: Path):
    (tmp_path / "project.json").write_text(json.dumps({"title": "Demo", "content_mode": "narration"}))
    (tmp_path / versioned_backup_name("project.json", 1, 1712345678)).write_text("{}")
    (tmp_path / versioned_backup_name("clues", 0, 1712345678)).mkdir()
    (tmp_path / "extra_dir").mkdir()
    unknown = ["extra.txt", "extra_dir", "project.json.bak.v1-final", "project.json.bak.vx-1712345678"]
    for name in unknown:
        if name != "extra_dir":
            (tmp_path / name).touch()

    result = DataValidator(projects_dir=tmp_path).validate_project_tree(tmp_path)

    assert [
        message.params["name"] for message in result.warning_messages if message.key == "val_unrecognized_entry"
    ] == sorted(unknown)
