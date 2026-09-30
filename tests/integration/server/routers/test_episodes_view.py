"""「分集」视图：GET /projects/{name}/episodes-view 与未登记文件的处置 POST /projects/{name}/source-files/{filename}/adopt。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.i18n.zh import errors as zh_errors
from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import episodes_view
from tests.auth_deps import AUTH_DEPENDENCIES


def _client(monkeypatch, tmp_path: Path, **fields) -> tuple[TestClient, ProjectManager, Path]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    if fields:
        pm.update_project("demo", lambda project: project.update(**fields))
    monkeypatch.setattr(episodes_view, "get_project_manager", lambda: pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(episodes_view.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app), pm, pm.get_project_path("demo") / "source"


def _entry(episode: int, origin: str, **fields) -> dict:
    return {
        "episode": episode,
        "title": f"第 {episode} 集",
        "script_file": f"scripts/episode_{episode}.json",
        "source_origin": origin,
        **fields,
    }


class TestEpisodesView:
    def test_returns_segments_volume_and_unregistered_files(self, tmp_path, monkeypatch):
        cut = _entry(1, "whole_source", source_range={"source_file": "source/novel.txt", "start": 0, "end": 5})
        client, _pm, source_dir = _client(
            monkeypatch,
            tmp_path,
            whole_source_files=[{"source_file": "source/novel.txt"}],
            episodes=[cut, _entry(2, "none")],
            episode_id_high_water=2,
        )
        (source_dir / "novel.txt").write_text("少年下山。\n城里起火。", encoding="utf-8")
        (source_dir / "episode_1.txt").write_text("少年下山。", encoding="utf-8")
        (source_dir / "stray.txt").write_text("没有登记", encoding="utf-8")

        with client:
            resp = client.get("/api/v1/projects/demo/episodes-view")

        assert resp.status_code == 200
        body = resp.json()
        assert body["unit"] == "chars"
        assert (body["units"], body["cut_units"]) == (10, 5)
        (novel,) = body["files"]
        assert [(s["kind"], s["episode"], s["gap"]) for s in novel["segments"]] == [
            ("episode", 1, False),
            ("unsplit", None, False),
        ]
        assert [(e["episode"], e["origin"], e["placed"]) for e in body["episodes"]] == [
            (1, "whole_source", True),
            (2, "none", False),
        ]
        assert body["unregistered"] == [{"name": "stray.txt", "size": 12, "can_join_whole_source": True}]

    def test_unknown_project_is_not_found(self, tmp_path, monkeypatch):
        client, _pm, _source_dir = _client(monkeypatch, tmp_path)

        with client:
            assert client.get("/api/v1/projects/missing/episodes-view").status_code == 404


class TestAdoptSourceFile:
    def test_joining_the_whole_source_appends_it_to_the_file_list(self, tmp_path, monkeypatch):
        client, pm, source_dir = _client(monkeypatch, tmp_path, whole_source_files=[{"source_file": "source/a.txt"}])
        (source_dir / "a.txt").write_text("甲", encoding="utf-8")
        (source_dir / "b.md").write_text("乙", encoding="utf-8")

        with client:
            resp = client.post("/api/v1/projects/demo/source-files/b.md/adopt", json={"target": "whole_source"})

        assert resp.status_code == 200
        assert pm.load_project("demo")["whole_source_files"] == [
            {"source_file": "source/a.txt"},
            {"source_file": "source/b.md"},
        ]

    def test_using_a_file_as_a_new_episode_moves_its_text_into_the_episode_file(self, tmp_path, monkeypatch):
        client, pm, source_dir = _client(monkeypatch, tmp_path, episodes=[_entry(3, "none")], episode_id_high_water=3)
        (source_dir / "番外.txt").write_text("番外原文\r\n第二行", encoding="utf-8")

        with client:
            resp = client.post("/api/v1/projects/demo/source-files/番外.txt/adopt", json={"target": "episode"})

        assert resp.status_code == 200
        assert resp.json()["episode"] == 4
        assert [(e["episode"], e["source_origin"]) for e in pm.load_project("demo")["episodes"]] == [
            (3, "none"),
            (4, "own"),
        ]
        assert (source_dir / "episode_4.txt").read_text(encoding="utf-8") == "番外原文\n第二行"
        assert not (source_dir / "番外.txt").exists()

    def test_an_orphan_episode_file_can_fill_the_no_source_episode_with_the_same_id(self, tmp_path, monkeypatch):
        client, pm, source_dir = _client(monkeypatch, tmp_path, episodes=[_entry(5, "none")], episode_id_high_water=5)
        (source_dir / "episode_5.txt").write_text("账本外的旧集文件", encoding="utf-8")

        with client:
            resp = client.post(
                "/api/v1/projects/demo/source-files/episode_5.txt/adopt", json={"target": "episode", "episode": 5}
            )

        assert resp.status_code == 200
        assert pm.load_project("demo")["episodes"][0]["source_origin"] == "own"
        assert (source_dir / "episode_5.txt").read_text(encoding="utf-8") == "账本外的旧集文件"
        assert not list(source_dir.glob("_episode_5*"))

    def test_refusals_leave_the_file_and_the_ledger_unchanged(self, tmp_path, monkeypatch):
        client, pm, source_dir = _client(
            monkeypatch,
            tmp_path,
            whole_source_files=[{"source_file": "source/a.txt"}],
            episodes=[_entry(2, "own")],
            episode_id_high_water=2,
        )
        (source_dir / "a.txt").write_text("甲", encoding="utf-8")
        (source_dir / "episode_2.txt").write_text("自带原文", encoding="utf-8")
        (source_dir / "_remaining.txt").write_text("剩余", encoding="utf-8")
        (source_dir / "gbk.txt").write_bytes("乱码".encode("gbk"))
        before = pm.load_project("demo")

        cases = [
            ("a.txt", {"target": "whole_source"}, 409, "source_file_registered"),
            ("missing.txt", {"target": "whole_source"}, 404, "source_file_not_found"),
            ("_remaining.txt", {"target": "whole_source"}, 422, "source_name_not_whole_source"),
            ("gbk.txt", {"target": "episode"}, 422, "source_file_unreadable"),
            ("_remaining.txt", {"target": "episode", "episode": 2}, 409, "episode_source_present"),
        ]
        with client:
            for filename, body, status, key in cases:
                resp = client.post(f"/api/v1/projects/demo/source-files/{filename}/adopt", json=body)
                assert resp.status_code == status, filename
                assert resp.json()["detail"] == zh_errors.MESSAGES[key].format(
                    filename=filename, episode=body.get("episode")
                )

            missing_episode = client.post(
                "/api/v1/projects/demo/source-files/_remaining.txt/adopt", json={"target": "episode", "episode": 9}
            )
            assert missing_episode.status_code == 404

        assert pm.load_project("demo") == before
        assert (source_dir / "_remaining.txt").read_text(encoding="utf-8") == "剩余"
