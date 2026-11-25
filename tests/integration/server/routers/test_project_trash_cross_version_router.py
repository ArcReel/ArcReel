"""跨版本恢复：升级前写下的删除记录经临时项目的迁移链恢复；迁移失败时降级为只恢复文件，结构文件只能下载。

删除记录用旧形态项目样本在旧 schema 下构造，再把当前项目升级到最新 schema，模拟「删除后应用升级」。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.edit_timeline.store import read_timeline_document
from lib.episode.episode_ledger import discover_product_episode_nums, has_downstream_products
from lib.infra.data_root_layout import DataRootLayout
from lib.project import project_trash
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_report import load_migration_report
from lib.project.project_migrations.runner import migrate_project_dir
from lib.workflow.workflow_state import WorkflowStateService
from server.error_handlers import register_error_handlers
from server.routers import project_trash as trash_router
from server.services.project.project_archive import ARCHIVE_MANIFEST_NAME, ProjectArchiveService
from tests.auth_deps import override_auth
from tests.fakes import use_project_manager
from tests.legacy_project_shapes import (
    write_legacy_quarantine_project,
    write_legacy_storyboard_project,
    write_legacy_timeline_agent_turn_project,
)

TIMELINE = "tl-0000abcd"
DOCUMENT = f"edit_timelines/episode_1/{TIMELINE}.json"
RENDER = f"renders/episode_1/{TIMELINE}/final.mp4"


@pytest.fixture
def projects_dir(tmp_path: Path) -> Path:
    root = DataRootLayout(tmp_path / "data").projects_dir
    root.mkdir(parents=True)
    return root


@pytest.fixture
def cross_version_client(projects_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    manager = ProjectManager(str(projects_dir.parent))
    use_project_manager(monkeypatch, manager)
    app = FastAPI()
    app.include_router(trash_router.router, prefix="/api/v1")
    register_error_handlers(app)
    override_auth(app)
    return TestClient(app)


def _old_record(project_dir: Path, files: list[str], *, schema_version: int) -> str:
    """在旧 schema 的项目里删除一条剪辑时间线，记录标成当时的 schema 版本。"""
    obj = {"kind": "edit_timeline", "id": TIMELINE, "episode": 1, "name": "初剪"}
    record = project_trash.record_deletion(project_dir, obj, files, snapshot=True)
    for rel in files:
        if rel.startswith("renders/"):
            (project_dir / rel).parent.rmdir()
    record_file = project_trash.trash_dir(project_dir) / record.id / "record.json"
    data = json.loads(record_file.read_text(encoding="utf-8"))
    record_file.write_text(json.dumps({**data, "schema_version": schema_version}), encoding="utf-8")
    return record.id


def _break_snapshot_script(project_dir: Path, record_id: str) -> None:
    """删除前的剧本已经读不出：临时项目的迁移链必然失败。"""
    script = project_trash.trash_dir(project_dir) / record_id / "snapshot" / "scripts" / "episode_1.json"
    script.write_text('{"episode": 1, "segments": [', encoding="utf-8")


def _trash(client: TestClient, project: str) -> list[dict]:
    response = client.get(f"/api/v1/projects/{project}/trash")
    assert response.status_code == 200, response.text
    return response.json()["records"]


def _timeline_project(projects_dir: Path) -> Path:
    project_dir = write_legacy_timeline_agent_turn_project(projects_dir)
    (project_dir / RENDER).parent.mkdir(parents=True)
    (project_dir / RENDER).write_bytes(b"final-cut")
    return project_dir


def test_a_record_from_an_older_schema_is_migrated_and_restored(
    cross_version_client: TestClient, projects_dir: Path
) -> None:
    project_dir = _timeline_project(projects_dir)
    record_id = _old_record(project_dir, [DOCUMENT, RENDER], schema_version=17)
    migrate_project_dir(project_dir)

    restored = cross_version_client.post(f"/api/v1/projects/{project_dir.name}/trash/{record_id}/restore")

    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert (body["degraded"], body["download_only"], body["object"]["id"], body["object"]["name"]) == (
        False,
        [],
        TIMELINE,
        "初剪",
    )
    document = json.loads((project_dir / DOCUMENT).read_text(encoding="utf-8"))
    assert all("agent_turn" not in revision for revision in document["revisions"])
    assert read_timeline_document(project_dir, 1, TIMELINE) is not None
    assert (project_dir / RENDER).read_bytes() == b"final-cut"
    assert _trash(cross_version_client, project_dir.name) == []


def test_a_record_whose_migration_fails_restores_its_files_and_keeps_structure_for_download(
    cross_version_client: TestClient, projects_dir: Path
) -> None:
    project_dir = _timeline_project(projects_dir)
    document_bytes = (project_dir / DOCUMENT).read_bytes()
    record_id = _old_record(project_dir, [DOCUMENT, RENDER], schema_version=17)
    _break_snapshot_script(project_dir, record_id)
    migrate_project_dir(project_dir)
    base = f"/api/v1/projects/{project_dir.name}/trash/{record_id}"

    restored = cross_version_client.post(f"{base}/restore")

    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["degraded"] is True
    assert body["restored"] == [{"original": RENDER, "path": RENDER, "as_history": False}]
    assert body["download_only"] == [DOCUMENT]
    assert (project_dir / RENDER).read_bytes() == b"final-cut"
    assert not (project_dir / DOCUMENT).exists()
    [record] = _trash(cross_version_client, project_dir.name)
    assert (record["id"], record["files"], record["download_only"], record["has_snapshot"]) == (
        record_id,
        [DOCUMENT],
        True,
        False,
    )
    assert record["size"] == len(document_bytes)

    downloaded = cross_version_client.get(f"{base}/files", params={"path": DOCUMENT})
    assert downloaded.status_code == 200
    assert downloaded.content == document_bytes
    assert downloaded.headers["content-disposition"].startswith("attachment")
    assert cross_version_client.get(f"{base}/files", params={"path": RENDER}).status_code == 404
    assert cross_version_client.post(f"{base}/restore").status_code == 409


def test_a_degraded_restore_lands_files_where_the_migration_moved_them(
    cross_version_client: TestClient, projects_dir: Path
) -> None:
    project_dir = write_legacy_storyboard_project(projects_dir, schema_version=0)
    project_file = project_dir / "project.json"
    project = json.loads(project_file.read_text(encoding="utf-8"))
    project["clues"] = {"古庙": {"type": "location", "description": "", "clue_sheet": "clues/古庙.png"}}
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (project_dir / "clues").mkdir()
    (project_dir / "clues" / "古庙.png").write_bytes(b"temple")
    record_id = _old_record(project_dir, ["clues/古庙.png"], schema_version=0)
    _break_snapshot_script(project_dir, record_id)

    restored = cross_version_client.post(f"/api/v1/projects/{project_dir.name}/trash/{record_id}/restore")

    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["degraded"] is True
    assert body["restored"] == [{"original": "scenes/古庙.png", "path": "scenes/古庙.png", "as_history": False}]
    assert body["download_only"] == []
    assert (project_dir / "scenes" / "古庙.png").read_bytes() == b"temple"
    assert not (project_dir / "clues" / "古庙.png").exists()
    assert _trash(cross_version_client, project_dir.name) == []


def test_retired_drafts_from_old_and_migrated_records_restore_original_bytes(
    cross_version_client: TestClient, projects_dir: Path
) -> None:
    project_dir = write_legacy_storyboard_project(projects_dir, schema_version=20)
    rel = "drafts/episode_1/script_plan_segments.invalid.json"
    path = project_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"owner":"agent"}\r\n')
    historical = project_trash.record_deletion(
        project_dir, {"kind": "planning_draft", "episode": 1, "path": rel}, [rel], snapshot=True
    )
    record_file = project_trash.trash_dir(project_dir) / historical.id / "record.json"
    record_file.write_text(json.dumps({**json.loads(record_file.read_bytes()), "schema_version": 20}), encoding="utf-8")
    path.write_bytes(b"broken\xff")
    orphan = project_dir / "drafts/episode_02/script_plan_segments.invalid.json"
    orphan.parent.mkdir()
    orphan.write_bytes(b"padded")
    assert migrate_project_dir(project_dir)
    records = _trash(cross_version_client, project_dir.name)
    assert len(records) == 3
    assert not path.exists()
    assert not migrate_project_dir(project_dir)
    for record in sorted(records, key=lambda item: item["id"] != historical.id):
        response = cross_version_client.post(f"/api/v1/projects/{project_dir.name}/trash/{record['id']}/restore")
        assert response.status_code == 200, response.text
        assert not response.json()["degraded"]
        [restored] = response.json()["restored"]
        if restored["path"] == orphan.relative_to(project_dir).as_posix():
            assert orphan.read_bytes() == b"padded"
            continue
        assert (project_dir / restored["path"]).read_bytes() == (
            b'{"owner":"agent"}\r\n' if record["id"] == historical.id else b"broken\xff"
        )
    assert path.read_bytes() == b'{"owner":"agent"}\r\n'
    assert _trash(cross_version_client, project_dir.name) == []
    assert 2 not in discover_product_episode_nums(project_dir)
    orphan.parent.rename(project_dir / "drafts/episode_2")
    assert not has_downstream_products(project_dir, 2, {})


@pytest.mark.parametrize("from_zip", [False, True], ids=["in-place", "archive"])
def test_legacy_quarantine_files_survive_upgrade_and_public_restore(
    cross_version_client: TestClient, projects_dir: Path, tmp_path: Path, from_zip: bool
) -> None:
    manager = ProjectManager(projects_dir.parent)
    archive_service = ProjectArchiveService(manager)
    project_dir = write_legacy_quarantine_project(tmp_path / "old" if from_zip else projects_dir)
    if from_zip:
        archive_path = tmp_path / "legacy.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(ARCHIVE_MANIFEST_NAME, json.dumps({"project_name": project_dir.name}))
            for path in project_dir.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(project_dir))
        imported = archive_service.import_project_archive(archive_path, confirm_report=True)
        project_dir = manager.get_project_path(imported.project_name)
    else:
        assert migrate_project_dir(project_dir)
    report = load_migration_report(project_dir)
    assert report is not None
    assert report.complete
    assert report.to_schema_version == 21
    expected = {
        "script_plan_normalized_script.invalid.json": b'{"content":{"scenes":[{"characters_in_scene":["ALICE"]}]}}',
        "script_plan_segments.invalid.json": b"corrupt\xff",
        "script_plan_reference_units.invalid.json": b'{"owner":"model"}',
        "prompt_authoring_reference_script.invalid.json": b"prompt draft",
    }
    rels = {f"drafts/episode_1/{name}": data for name, data in expected.items()}
    assert {item.file for item in report.adjusted if item.code == "retired_draft_trashed"} == rels.keys()
    [conflict] = [item for item in report.adjusted if item.code == "draft_name_conflict_resolved"]
    assert conflict.backup
    assert (project_dir / conflict.backup).read_bytes() == b"old name conflict"
    assert json.loads((project_dir / "project.json").read_bytes())["metadata"]["updated_at"] == "2026-02-01T12:00:00Z"
    assert not migrate_project_dir(project_dir)
    records = _trash(cross_version_client, project_dir.name)
    assert {record["origin"] for record in records} == {"migration"}
    assert sorted(record["files"] for record in records) == sorted([rel] for rel in rels)
    assert all(not (project_dir / rel).exists() for rel in rels)
    if from_zip:
        exported, _ = archive_service.export_project(project_dir.name)
        try:
            with zipfile.ZipFile(exported) as archive:
                assert all(".arcreel" not in Path(name).parts for name in archive.namelist())
        finally:
            exported.unlink()
    workflow = WorkflowStateService(manager)
    before = workflow.get_status(project_dir.name, episode=1)
    assert before.content is not None
    assert not before.blockers
    for record in records:
        response = cross_version_client.post(f"/api/v1/projects/{project_dir.name}/trash/{record['id']}/restore")
        assert response.status_code == 200, response.text
        [rel] = record["files"]
        assert (project_dir / rel).read_bytes() == rels[rel]
    after = workflow.get_status(project_dir.name, episode=1)
    assert after.content == before.content
    assert after.next_action == before.next_action
    assert not migrate_project_dir(project_dir)
