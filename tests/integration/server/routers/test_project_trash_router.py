"""项目回收站：文件视图只能删除未引用文件，删除成为一条记录；恢复从不覆盖现有文件；过期记录被清除。"""

from __future__ import annotations

import json
import unicodedata
from datetime import timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.version_manager import VersionManager
from lib.infra.data_root_layout import DataRootLayout
from lib.project import project_trash
from lib.project.project_manager import ProjectManager
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.auth import EMBEDDED_AGENT_SUB, CurrentUserInfo, get_current_user
from server.dependencies import require_project_migration_ok
from server.error_handlers import register_error_handlers
from server.routers import file_view
from server.routers import project_trash as trash_router
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.fakes import use_project_manager

FILES = "/api/v1/projects/demo/file-view"
TRASH = "/api/v1/projects/demo/trash"


@pytest.fixture
def trash_project_dir(tmp_path):
    root = DataRootLayout(tmp_path / "data").projects_dir / "demo"
    (root / "storyboards").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "project.json").write_text(
        json.dumps(
            {
                "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
                "title": "demo",
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
            }
        ),
        encoding="utf-8",
    )
    script = {
        "segments": [{"segment_id": "E1S01", "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"}}]
    }
    (root / "scripts/episode_1.json").write_text(json.dumps(script), encoding="utf-8")
    (root / "storyboards/scene_E1S01.png").write_bytes(b"in-use")
    (root / "storyboards/scene_E1S02.png").write_bytes(b"leftover")
    (root / "notes.txt").write_text("agent notes", encoding="utf-8")
    (root / "source").mkdir()
    (root / "source/extra.txt").write_text("not in the whole source", encoding="utf-8")
    return root


@pytest.fixture
def trash_client(trash_project_dir, monkeypatch):
    manager = ProjectManager(str(trash_project_dir.parents[1]))
    monkeypatch.setattr(file_view, "get_project_manager", lambda: manager)
    use_project_manager(monkeypatch, manager)
    app = FastAPI()
    for module in (file_view, trash_router):
        app.include_router(
            module.router, prefix="/api/v1", dependencies=[*AUTH_DEPENDENCIES, Depends(require_project_migration_ok)]
        )
    register_error_handlers(app)
    override_auth(app)
    return TestClient(app)


def _records(trash_client) -> list[dict]:
    response = trash_client.get(TRASH)
    assert response.status_code == 200, response.text
    return response.json()["records"]


def test_deleting_a_referenced_file_is_rejected(trash_client, trash_project_dir):
    # 未纳入整本源文的源文件同样拒绝：它交给分集视图处理
    for path in ("storyboards/scene_E1S01.png", "project.json", "scripts/episode_1.json", "source/extra.txt"):
        response = trash_client.delete(FILES, params={"path": path})
        assert response.status_code == 409, path

    assert (trash_project_dir / "storyboards/scene_E1S01.png").read_bytes() == b"in-use"
    assert _records(trash_client) == []


def test_deleting_an_unreferenced_file_creates_one_record(trash_client, trash_project_dir):
    response = trash_client.delete(FILES, params={"path": "storyboards/scene_E1S02.png"})
    assert response.status_code == 200, response.text

    assert not (trash_project_dir / "storyboards/scene_E1S02.png").exists()
    [record] = _records(trash_client)
    assert record["object"] == {"kind": "file", "path": "storyboards/scene_E1S02.png"}
    assert record["files"] == ["storyboards/scene_E1S02.png"]
    assert record["origin"] == "user"
    assert record["size"] == len(b"leftover")
    assert trash_client.get(TRASH).json()["total_size"] == len(b"leftover")


@pytest.mark.parametrize("reference_alias", [False, True])
@pytest.mark.parametrize("alias_kind", ["unicode", "case"])
def test_file_alias_cannot_bypass_reference_guard(trash_client, trash_project_dir, reference_alias, alias_kind):
    canonical = "characters/Café.png"
    alias = unicodedata.normalize("NFD", canonical) if alias_kind == "unicode" else canonical.upper()
    target = trash_project_dir / canonical
    target.parent.mkdir()
    target.write_bytes(b"in-use")
    if not (trash_project_dir / alias).exists() or not target.samefile(trash_project_dir / alias):
        pytest.skip("filesystem distinguishes these file names")
    project = json.loads((trash_project_dir / "project.json").read_text(encoding="utf-8"))
    project["characters"] = {"Café": {"character_sheet": alias if reference_alias else canonical}}
    (trash_project_dir / "project.json").write_text(json.dumps(project), encoding="utf-8")

    listing = trash_client.get(FILES, params={"path": "characters"}).json()["entries"]
    assert listing[0]["role"] == "referenced"
    response = trash_client.delete(FILES, params={"path": listing[0]["path"] if reference_alias else alias})

    assert response.status_code == 409, response.text
    assert target.read_bytes() == b"in-use"
    assert _records(trash_client) == []


def test_distinct_case_sensitive_file_can_be_deleted(trash_client, trash_project_dir):
    canonical = trash_project_dir / "storyboards/scene_E1S01.png"
    distinct = trash_project_dir / "storyboards/SCENE_E1S01.PNG"
    if distinct.exists():
        pytest.skip("filesystem does not distinguish case")
    distinct.write_bytes(b"unused")

    response = trash_client.delete(FILES, params={"path": distinct.relative_to(trash_project_dir).as_posix()})

    assert response.status_code == 200, response.text
    assert canonical.read_bytes() == b"in-use"
    assert not distinct.exists()


@pytest.mark.parametrize(
    ("stored", "standard"),
    [
        ("claude.md", "CLAUDE.md"),
        ("SOURCE/extra.txt", "source/extra.txt"),
        ("EDIT_TIMELINES/EPISODE_1/TL-0123ABCD.JSON", "edit_timelines/episode_1/tl-0123abcd.json"),
    ],
)
def test_storage_roles_protect_case_aliases(trash_client, trash_project_dir, stored, standard):
    target = trash_project_dir / stored
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("protected", encoding="utf-8")
    expected = trash_project_dir / standard
    if not expected.exists() or not target.samefile(expected):
        pytest.skip("filesystem distinguishes case")

    response = trash_client.delete(FILES, params={"path": stored})

    assert response.status_code == 409, response.text
    assert target.read_text(encoding="utf-8") == "protected"
    assert _records(trash_client) == []


def test_workflow_draft_cannot_be_deleted_from_file_view(trash_client, trash_project_dir):
    from lib.episode.episode_paths import episode_drafts_dir
    from lib.script.draft_quarantine import QUARANTINE_FILENAMES

    directory = episode_drafts_dir(trash_project_dir, 1)
    directory.mkdir(parents=True)
    for filename in QUARANTINE_FILENAMES:
        draft = directory / filename
        draft.write_text("{}", encoding="utf-8")
        response = trash_client.delete(FILES, params={"path": draft.relative_to(trash_project_dir).as_posix()})
        assert response.status_code == 409, response.text
        assert draft.read_text(encoding="utf-8") == "{}"
    assert _records(trash_client) == []


def test_separately_deleted_files_can_be_restored_independently(trash_client, trash_project_dir):
    paths = ("notes.txt", "storyboards/scene_E1S02.png")
    records = [trash_client.delete(FILES, params={"path": path}).json()["record"] for path in paths]
    assert len({record["id"] for record in records}) == 2
    assert [record["files"] for record in records] == [[path] for path in paths]

    response = trash_client.post(f"{TRASH}/{records[0]['id']}/restore")

    assert response.status_code == 200, response.text
    assert (trash_project_dir / paths[0]).read_text(encoding="utf-8") == "agent notes"
    assert not (trash_project_dir / paths[1]).exists()
    assert [record["id"] for record in _records(trash_client)] == [records[1]["id"]]


def test_restore_returns_to_original_path_when_free(trash_client, trash_project_dir):
    trash_client.delete(FILES, params={"path": "notes.txt"})
    [record] = _records(trash_client)

    response = trash_client.post(f"{TRASH}/{record['id']}/restore")

    assert response.status_code == 200, response.text
    assert response.json()["restored"] == [{"original": "notes.txt", "path": "notes.txt", "as_history": False}]
    assert (trash_project_dir / "notes.txt").read_text(encoding="utf-8") == "agent notes"
    assert _records(trash_client) == []


def test_restore_onto_an_occupied_resource_path_adds_a_history_version(trash_client, trash_project_dir):
    trash_client.delete(FILES, params={"path": "storyboards/scene_E1S02.png"})
    (trash_project_dir / "storyboards/scene_E1S02.png").write_bytes(b"regenerated")
    [record] = _records(trash_client)

    [restored] = trash_client.post(f"{TRASH}/{record['id']}/restore").json()["restored"]

    assert restored["as_history"] is True
    assert (trash_project_dir / "storyboards/scene_E1S02.png").read_bytes() == b"regenerated"
    assert (trash_project_dir / restored["path"]).read_bytes() == b"leftover"
    history = json.loads((trash_project_dir / "versions/versions.json").read_text(encoding="utf-8"))["storyboards"][
        "E1S02"
    ]
    current = next(v for v in history["versions"] if v["version"] == history["current_version"])
    assert (trash_project_dir / current["file"]).read_bytes() == b"regenerated"


def test_restore_onto_an_occupied_plain_path_takes_a_new_name(trash_client, trash_project_dir):
    trash_client.delete(FILES, params={"path": "notes.txt"})
    (trash_project_dir / "notes.txt").write_text("newer", encoding="utf-8")
    [record] = _records(trash_client)

    [restored] = trash_client.post(f"{TRASH}/{record['id']}/restore").json()["restored"]

    assert restored["path"] == "notes (1).txt"
    assert (trash_project_dir / "notes.txt").read_text(encoding="utf-8") == "newer"
    assert (trash_project_dir / "notes (1).txt").read_text(encoding="utf-8") == "agent notes"


def test_a_multi_file_record_is_restored_whole_or_not_at_all(trash_client, trash_project_dir, tmp_path):
    (trash_project_dir / "extras").mkdir()
    (trash_project_dir / "extras/b.txt").write_text("b", encoding="utf-8")
    with project_metadata_lock(trash_project_dir):
        record = project_trash.record_deletion(trash_project_dir, {"kind": "bundle"}, ["notes.txt", "extras/b.txt"])
    # 删除之后途经目录被换成符号链接：第二个文件无处可落
    (tmp_path / "outside").mkdir()
    (trash_project_dir / "extras").rmdir()
    (trash_project_dir / "extras").symlink_to(tmp_path / "outside")

    blocked = trash_client.post(f"{TRASH}/{record.id}/restore")

    assert blocked.status_code == 409
    assert not (trash_project_dir / "notes.txt").exists()
    assert [item["id"] for item in _records(trash_client)] == [record.id]

    (trash_project_dir / "extras").unlink()
    restored = trash_client.post(f"{TRASH}/{record.id}/restore")

    assert restored.status_code == 200, restored.text
    assert (trash_project_dir / "notes.txt").read_text(encoding="utf-8") == "agent notes"
    assert (trash_project_dir / "extras/b.txt").read_text(encoding="utf-8") == "b"
    assert _records(trash_client) == []


@pytest.mark.parametrize("tracked", [False, True])
def test_failed_multi_file_restore_rolls_back_history_and_current_registration(trash_project_dir, monkeypatch, tracked):
    target = trash_project_dir / "storyboards/scene_E1S02.png"
    manager = VersionManager(trash_project_dir)
    if tracked:
        manager.add_version("storyboards", "E1S02", "existing", source_file=target)
    with project_metadata_lock(trash_project_dir):
        record = project_trash.record_deletion(
            trash_project_dir, {"kind": "bundle"}, ["storyboards/scene_E1S02.png", "notes.txt"]
        )
    target.write_bytes(b"new current")
    versions = trash_project_dir / "versions/versions.json"
    before = versions.read_bytes() if versions.exists() else None
    snapshots = {path for path in (trash_project_dir / "versions").rglob("*") if path.is_file()}
    replace = project_trash.os.replace
    stored_notes = project_trash.trash_dir(trash_project_dir) / record.id / "files/notes.txt"

    def fail_second_file(source, destination):
        if source == stored_notes:
            raise OSError("disk full")
        return replace(source, destination)

    monkeypatch.setattr(project_trash.os, "replace", fail_second_file)
    with pytest.raises(OSError, match="disk full"):
        project_trash.restore(trash_project_dir, record.id)

    assert (versions.read_bytes() if versions.exists() else None) == before
    assert {path for path in (trash_project_dir / "versions").rglob("*") if path.is_file()} == snapshots
    assert target.read_bytes() == b"new current"
    assert project_trash.require_record(trash_project_dir, record.id).files == record.files
    monkeypatch.setattr(project_trash.os, "replace", replace)
    assert len(project_trash.restore(trash_project_dir, record.id).restored) == 2


def test_purge_and_empty_remove_records(trash_client):
    trash_client.delete(FILES, params={"path": "notes.txt"})
    trash_client.delete(FILES, params={"path": "storyboards/scene_E1S02.png"})
    first, second = _records(trash_client)

    assert trash_client.delete(f"{TRASH}/{first['id']}").status_code == 200
    assert [r["id"] for r in _records(trash_client)] == [second["id"]]
    assert trash_client.delete(TRASH).json() == {"purged": 1}
    assert _records(trash_client) == []
    assert trash_client.post(f"{TRASH}/{first['id']}/restore").status_code == 404


def test_embedded_agent_token_cannot_purge_or_empty(trash_client):
    """内嵌 Agent 持有本地服务的令牌：彻底删除与清空在服务端拒绝它，不只靠不提供 MCP 工具。"""
    trash_client.delete(FILES, params={"path": "notes.txt"})
    [record] = _records(trash_client)
    trash_client.app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(
        id="default", sub=EMBEDDED_AGENT_SUB, role="admin"
    )

    assert trash_client.delete(f"{TRASH}/{record['id']}").status_code == 403
    assert trash_client.delete(TRASH).status_code == 403
    assert [r["id"] for r in _records(trash_client)] == [record["id"]]


def test_records_are_kept_until_the_retention_period_ends(trash_client, trash_project_dir):
    trash_client.delete(FILES, params={"path": "notes.txt"})
    [record] = project_trash.list_records(trash_project_dir)
    expires_at = record.deleted_at + project_trash.RETENTION

    assert project_trash.purge_expired(trash_project_dir, now=expires_at - timedelta(seconds=1)) == 0
    assert [r["id"] for r in _records(trash_client)] == [record.id]
    assert project_trash.purge_expired(trash_project_dir, now=expires_at) == 1
    assert _records(trash_client) == []


def test_trash_is_hidden_from_the_file_view(trash_client):
    trash_client.delete(FILES, params={"path": "notes.txt"})

    names = {entry["name"] for entry in trash_client.get(FILES, params={"path": ".arcreel"}).json()["entries"]}
    assert "trash" not in names
    assert trash_client.get(FILES, params={"path": ".arcreel/trash"}).status_code == 404


def _history(project_dir) -> dict:
    return json.loads((project_dir / "versions/versions.json").read_text(encoding="utf-8"))["storyboards"]["E1S01"]


def test_history_version_moves_to_trash_and_restores_into_version_history(trash_client, trash_project_dir):
    current = trash_project_dir / "storyboards/scene_E1S01.png"
    manager = VersionManager(trash_project_dir)
    manager.add_version("storyboards", "E1S01", "old prompt", source_file=current, aspect_ratio="9:16")
    current.write_bytes(b"in-use v2")
    manager.add_version("storyboards", "E1S01", "new prompt", source_file=current)
    old, latest = _history(trash_project_dir)["versions"]

    refused = trash_client.delete(FILES, params={"path": latest["file"]})
    assert refused.status_code == 409, refused.text
    response = trash_client.delete(FILES, params={"path": old["file"]})
    assert response.status_code == 200, response.text

    assert not (trash_project_dir / old["file"]).exists()
    assert [v["version"] for v in _history(trash_project_dir)["versions"]] == [latest["version"]]
    [record] = _records(trash_client)
    assert record["object"]["kind"] == "version"

    [restored] = trash_client.post(f"{TRASH}/{record['id']}/restore").json()["restored"]

    assert restored["as_history"] is True
    history = _history(trash_project_dir)
    assert history["current_version"] == latest["version"]
    back = next(v for v in history["versions"] if v["file"] == restored["path"])
    assert back["prompt"] == "old prompt"
    # 原版本的元数据（生成依据等）随版本加回，否则视频、音频版本加回后无法选回。
    assert back["aspect_ratio"] == "9:16"
    assert back["created_at"] == old["created_at"]
    assert (trash_project_dir / back["file"]).read_bytes() == b"in-use"
    assert current.read_bytes() == b"in-use v2"
