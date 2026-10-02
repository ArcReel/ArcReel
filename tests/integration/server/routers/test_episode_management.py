"""集管理的 Web 入口：新建、调序与删除一集。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.i18n.zh import errors as zh_errors
from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import episode_management
from tests.auth_deps import AUTH_DEPENDENCIES

NOVEL = "少年下山。城里起火。夜雨。"


def _cut(episode: int, start: int, end: int) -> dict:
    return {
        "episode": episode,
        "title": "",
        "script_file": f"scripts/episode_{episode}.json",
        "source_origin": "whole_source",
        "source_range": {"source_file": "source/novel.txt", "start": start, "end": end},
        "ledger_status": "planned",
    }


def _client(monkeypatch, tmp_path: Path, episodes: list[dict]) -> tuple[TestClient, ProjectManager]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.update_project(
        "demo",
        lambda project: project.update(
            whole_source_files=[{"source_file": "source/novel.txt"}],
            episodes=episodes,
            episode_id_high_water=max([e["episode"] for e in episodes], default=0),
        ),
    )
    source_dir = pm.get_project_path("demo") / "source"
    (source_dir / "novel.txt").write_text(NOVEL, encoding="utf-8")
    for entry in episodes:
        rng = entry["source_range"]
        (source_dir / f"episode_{entry['episode']}.txt").write_text(NOVEL[rng["start"] : rng["end"]], encoding="utf-8")
    monkeypatch.setattr(episode_management, "get_project_manager", lambda: pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(episode_management.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app), pm


def _order(pm: ProjectManager) -> list[int]:
    return [entry["episode"] for entry in pm.load_project("demo")["episodes"]]


def test_create_inserts_after_the_given_episode_and_move_keeps_source_order(tmp_path, monkeypatch):
    client, pm = _client(monkeypatch, tmp_path, [_cut(1, 0, 5), _cut(2, 5, 10)])

    with client:
        created = client.post("/api/v1/projects/demo/episodes", json={"after": 1, "source_text": "番外。"})
        locked = client.post("/api/v1/projects/demo/episodes/1/move", json={"after": 2})
        moved = client.post(f"/api/v1/projects/demo/episodes/{created.json()['episode']}/move", json={"after": None})

    assert created.status_code == 201
    assert created.json()["episode"] == 3
    assert locked.status_code == 409
    assert locked.json()["detail"] == zh_errors.MESSAGES["episode_manage_cut_order_locked"]
    assert moved.status_code == 200
    assert _order(pm) == [3, 1, 2]


def test_delete_returns_the_loss_text_first_then_deletes_with_its_revision(tmp_path, monkeypatch):
    client, pm = _client(monkeypatch, tmp_path, [_cut(1, 0, 5), _cut(2, 5, 10)])

    with client:
        preview = client.post("/api/v1/projects/demo/episodes/2/delete", json={})
        body = preview.json()
        deleted = client.post("/api/v1/projects/demo/episodes/2/delete", json={"revision": body["impact"]["revision"]})

    assert body["status"] == "confirmation_required"
    assert body["impact"]["recoverable"] is True
    assert body["impact"]["text"].startswith("「第 2 集」还没有产物。")
    assert _order(pm) == [1]
    assert deleted.json()["status"] == "deleted"


def test_unknown_episode_is_not_found(tmp_path, monkeypatch):
    client, _pm = _client(monkeypatch, tmp_path, [_cut(1, 0, 5)])

    with client:
        resp = client.post("/api/v1/projects/demo/episodes/9/delete", json={})

    assert resp.status_code == 404


def test_delete_is_refused_while_the_episode_has_active_tasks(tmp_path, monkeypatch, active_episode_tasks):
    client, pm = _client(monkeypatch, tmp_path, [_cut(1, 0, 5), _cut(2, 5, 10)])

    active_episode_tasks["running"] = [{"resource_id": "E2S01", "script_file": None, "payload": {}}]

    with client:
        revision = client.post("/api/v1/projects/demo/episodes/2/delete", json={}).json()["impact"]["revision"]
        refused = client.post("/api/v1/projects/demo/episodes/2/delete", json={"revision": revision})
        other = client.post("/api/v1/projects/demo/episodes/1/delete", json={})

    assert refused.status_code == 409
    assert refused.json()["detail"] == zh_errors.MESSAGES["episode_manage_tasks_active"]
    assert _order(pm) == [1, 2]
    assert other.json()["status"] == "confirmation_required"
