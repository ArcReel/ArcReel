"""跨版本恢复：升级前写下的删除记录经临时项目的迁移链恢复；迁移失败时降级为只恢复文件，结构文件只能下载。

删除记录用旧形态项目样本在旧 schema 下构造，再把当前项目升级到最新 schema，模拟「删除后应用升级」。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.edit_timeline.store import read_timeline_document
from lib.infra.data_root_layout import DataRootLayout
from lib.project import project_trash
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations.runner import migrate_project_dir
from server.error_handlers import register_error_handlers
from server.routers import project_trash as trash_router
from tests.auth_deps import override_auth
from tests.fakes import use_project_manager
from tests.legacy_project_shapes import write_legacy_storyboard_project, write_legacy_timeline_agent_turn_project

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
    # 与删除路径一样撤掉只剩空壳的渲染目录
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
    migrate_project_dir(project_dir)  # 应用升级

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
    # 修订按当前 schema 落回：v17→v18 去掉了 Agent 轮次字段
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
    # 迁移链里会搬动媒体的只有 v0→v1（线索图分流到 scenes/、props/）与 v5→v6：用 v0 样本构造记录。
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
    # 没有只能下载的结构文件：记录随之消失
    assert _trash(cross_version_client, project_dir.name) == []
