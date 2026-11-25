import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from lib.artifacts.sqlite_history import SQLiteHistory
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_report import load_migration_report
from lib.project.project_migrations import v20_to_v21_retired_drafts as step
from lib.project.project_migrations.runner import migrate_project_dir
from server.services.project.project_archive import ProjectArchiveService, ProjectArchiveValidationError


def test_history_is_verified_and_installed_before_the_schema_commit(tmp_path, monkeypatch):
    project = tmp_path / "project.json"
    project.write_text('{"schema_version":20,"metadata":{"updated_at":"2020-06-01"}}')
    store = SQLiteHistory(tmp_path)
    store.path.parent.mkdir()
    store.path.write_bytes(b"pre-existing database")
    source = store.path.with_name("versions.json")
    records = {
        "videos": {
            "E1S1": {
                "current_version": 1,
                "extension": {"value": 2},
                "versions": [
                    {"version": 3, "file": "versions/videos/missing.mp4"},
                    {"version": 1, "proof": "original"},
                ],
            }
        },
        "audio": {},
    }
    source.write_text(json.dumps(records))
    write = step.atomic_write_json

    def interrupt(path, payload):
        if path == project:
            assert store.snapshot() == records
            assert not source.exists()
            raise RuntimeError("before schema commit")
        write(path, payload)

    with monkeypatch.context() as fault:
        fault.setattr(step, "atomic_write_json", interrupt)
        with pytest.raises(RuntimeError, match="before schema commit"):
            migrate_project_dir(tmp_path)
    assert json.loads(project.read_bytes())["schema_version"] == 20
    assert migrate_project_dir(tmp_path)
    assert json.loads(project.read_bytes()) == {"schema_version": 21, "metadata": {"updated_at": "2020-06-01"}}
    assert store.snapshot() == records
    report = load_migration_report(tmp_path)
    assert report is not None
    kept = next(item.backup for item in report.adjusted if item.code == "history_conflict_preserved")
    assert kept
    assert (tmp_path / kept).read_bytes() == b"pre-existing database"
    assert [json.loads(p.read_bytes()) for p in source.parent.glob("versions.json.bak.v20-*")] == [records]


@pytest.mark.parametrize("payload", [None, "{}", "null", "{"])
def test_empty_absent_and_corrupt_legacy_history(tmp_path: Path, payload):
    (tmp_path / "project.json").write_text('{"schema_version":20}')
    store = SQLiteHistory(tmp_path)
    store.path.parent.mkdir()
    source = store.path.with_name("versions.json")
    if payload is not None:
        source.write_text(payload)
    if payload in {"null", "{"}:
        with pytest.raises(ValueError, match=r"invalid version history|Expecting property name"):
            migrate_project_dir(tmp_path)
        assert not store.path.exists()
        assert json.loads((tmp_path / "project.json").read_bytes())["schema_version"] == 20
        source.write_text("{}")
    assert migrate_project_dir(tmp_path)
    assert store.snapshot() == {}
    assert bool(list(source.parent.glob("versions.json.bak.v20-*"))) == (payload is not None)


@pytest.mark.parametrize(
    "history",
    [
        None,
        "[",
        "[]",
        '{"unknown":7}',
        '{"videos":{"a":{"versions":[],"current_version":"invalid"}}}',
        '{"videos":{"a":{"versions":[{"file":"versions/videos/a.mp4"}]}}}',
    ],
)
def test_current_archive_requires_valid_exchange_history(tmp_path, history):
    archive = tmp_path / "broken.zip"
    with ZipFile(archive, "w") as output:
        output.writestr("demo/project.json", '{"schema_version":21,"content_mode":"drama"}')
        if history is not None:
            output.writestr("demo/versions/versions.json", history)
    projects = ProjectManager(tmp_path / "target")
    with pytest.raises(ProjectArchiveValidationError) as failure:
        ProjectArchiveService(projects).import_project_archive(archive)
    assert failure.value.status_code == 400
    assert projects.list_projects() == []


def test_current_native_project_prefers_its_database_and_retires_conflicting_exchange(tmp_path, monkeypatch):
    (tmp_path / "project.json").write_text('{"schema_version":21}')
    store = SQLiteHistory(tmp_path)
    store.path.parent.mkdir()
    source = store.path.with_name("versions.json")
    source.write_text('{"videos":{}}')
    assert migrate_project_dir(tmp_path)
    assert store.snapshot() == {"videos": {}}
    source.write_text('{"audio":{}}')

    def report_unavailable(*args, **kwargs):
        raise OSError("report unavailable")

    with monkeypatch.context() as fault:
        fault.setattr(step, "extend_migration_report", report_unavailable)
        with pytest.raises(OSError, match="report unavailable"):
            migrate_project_dir(tmp_path)
    assert json.loads(source.read_bytes()) == {"audio": {}}

    assert migrate_project_dir(tmp_path)
    assert not source.exists()
    assert store.snapshot() == {"videos": {}}
    report = load_migration_report(tmp_path)
    assert report is not None
    assert report.adjusted[-1].backup
    assert json.loads((tmp_path / report.adjusted[-1].backup).read_bytes()) == {"audio": {}}
