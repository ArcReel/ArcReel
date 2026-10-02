"""「分集」视图：GET /projects/{name}/episodes-view 与未登记文件的处置 POST /projects/{name}/source-files/{filename}/adopt。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.episode.episode_ledger import SourceDoc, compute_source_fingerprints
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

    def test_using_a_file_as_an_episode_rolls_back_when_the_ledger_cannot_be_written(self, tmp_path, monkeypatch):
        import lib.project.project_manager as project_manager_module

        client, pm, source_dir = _client(monkeypatch, tmp_path, episodes=[_entry(3, "none")], episode_id_high_water=3)
        (source_dir / "番外.txt").write_bytes("番外原文\r\n第二行".encode())
        (source_dir / "episode_4.txt").write_text("账本外的同号旧文件", encoding="utf-8")
        before = pm.load_project("demo")
        project_file = pm.get_project_path("demo") / "project.json"
        real_atomic_write = project_manager_module.atomic_write_json

        def _fail_project_write(path, data):
            if path == project_file:
                raise OSError("injected project write failure")
            return real_atomic_write(path, data)

        monkeypatch.setattr(project_manager_module, "atomic_write_json", _fail_project_write)

        with client:
            resp = client.post("/api/v1/projects/demo/source-files/番外.txt/adopt", json={"target": "episode"})

        assert resp.status_code == 500
        assert pm.load_project("demo") == before
        assert (source_dir / "番外.txt").read_bytes() == "番外原文\r\n第二行".encode()
        assert (source_dir / "episode_4.txt").read_text(encoding="utf-8") == "账本外的同号旧文件"
        assert not list(source_dir.glob("_episode_4*"))

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


class TestManualSplit:
    def test_split_with_products_returns_the_confirmation_text_before_writing(self, tmp_path, monkeypatch):
        cut = _entry(1, "whole_source", source_range={"source_file": "source/novel.txt", "start": 0, "end": 10})
        cut["title"] = "雨夜"
        client, pm, source_dir = _client(
            monkeypatch,
            tmp_path,
            whole_source_files=[{"source_file": "source/novel.txt"}],
            episodes=[cut],
            episode_id_high_water=1,
        )
        (source_dir / "novel.txt").write_text("少年下山。\n城里起火。", encoding="utf-8")
        scripts = pm.get_project_path("demo") / "scripts"
        scripts.mkdir(exist_ok=True)
        (scripts / "episode_1.json").write_text("{}", encoding="utf-8")
        body = {"action": "split", "episode": 1, "at": 5}

        with client:
            pending = client.post("/api/v1/projects/demo/episodes-view/manual-split", json=body)
            applied = client.post(
                "/api/v1/projects/demo/episodes-view/manual-split", json={**body, "confirm_episodes": [1]}
            )

        assert pending.status_code == 200
        assert pending.json()["status"] == "confirmation_required"
        assert pending.json()["impact"]["restaled"] == [1]
        assert "雨夜" in pending.json()["impact"]["text"]
        assert applied.json() == {
            "status": "applied",
            "episode": 2,
            "impact": {"restaled": [1], "retired": [], "removed": [], "merged_units": 0},
        }
        assert [e["episode"] for e in pm.load_project("demo")["episodes"]] == [1, 2]

    def test_merge_over_unsplit_text_states_its_volume_and_applies_once_confirmed(self, tmp_path, monkeypatch):
        novel = {"source_file": "source/novel.txt"}
        client, pm, source_dir = _client(
            monkeypatch,
            tmp_path,
            whole_source_files=[novel],
            episodes=[
                _entry(1, "whole_source", source_range={**novel, "start": 0, "end": 5}),
                _entry(2, "whole_source", source_range={**novel, "start": 10, "end": 15}),
            ],
            episode_id_high_water=2,
        )
        (source_dir / "novel.txt").write_text("少年下山。城里起火。夜雨未停。", encoding="utf-8")
        body = {"action": "merge_next", "episode": 1}

        with client:
            pending = client.post("/api/v1/projects/demo/episodes-view/manual-split", json=body)
            applied = client.post(
                "/api/v1/projects/demo/episodes-view/manual-split", json={**body, "confirm_merged_units": 5}
            )

        assert pending.json()["status"] == "confirmation_required"
        assert pending.json()["impact"]["merged_units"] == 5
        assert pending.json()["impact"]["text"].splitlines()[0] == "两集之间有 5 字未切分的原文，合并后会并入这一集。"
        assert applied.json()["status"] == "applied"
        merged = pm.load_project("demo")["episodes"]
        assert [(e["episode"], e["source_range"]["end"]) for e in merged] == [(1, 15)]

    def test_change_is_refused_while_a_displaced_episode_has_active_tasks(
        self, tmp_path, monkeypatch, active_episode_tasks
    ):
        novel = {"source_file": "source/novel.txt"}
        client, pm, source_dir = _client(
            monkeypatch,
            tmp_path,
            whole_source_files=[novel],
            episodes=[
                _entry(1, "whole_source", source_range={**novel, "start": 0, "end": 5}),
                _entry(2, "whole_source", source_range={**novel, "start": 5, "end": 10}),
            ],
            episode_id_high_water=2,
        )
        (source_dir / "novel.txt").write_text("少年下山。城里起火。", encoding="utf-8")
        before = (pm.get_project_path("demo") / "project.json").read_bytes()

        active_episode_tasks["queued"] = [
            {"resource_id": "script_plan", "script_file": None, "payload": {"episode": 2}}
        ]

        with client:
            merge = client.post(
                "/api/v1/projects/demo/episodes-view/manual-split", json={"action": "merge_next", "episode": 1}
            )
            clear = client.post(
                "/api/v1/projects/demo/episodes-view/manual-split", json={"action": "clear_after", "episode": 1}
            )
            preview = client.post(
                "/api/v1/projects/demo/episodes-view/manual-split",
                json={"action": "merge_next", "episode": 1, "dry_run": True},
            )

        for resp in (merge, clear):
            assert resp.status_code == 409
            assert resp.json()["detail"] == zh_errors.MESSAGES["manual_split_tasks_active"]
        assert (pm.get_project_path("demo") / "project.json").read_bytes() == before
        assert preview.json()["impact"]["removed"] == [2]

    def test_refusal_maps_to_a_status_code(self, tmp_path, monkeypatch):
        client, _pm, source_dir = _client(
            monkeypatch, tmp_path, whole_source_files=[{"source_file": "source/novel.txt"}]
        )
        (source_dir / "novel.txt").write_text("少年下山。", encoding="utf-8")

        with client:
            resp = client.post(
                "/api/v1/projects/demo/episodes-view/manual-split",
                json={"action": "cut", "source_file": "source/novel.txt", "end": 99},
            )

        assert resp.status_code == 422
        assert resp.json()["detail"] == zh_errors.MESSAGES["manual_split_position_invalid"]


class TestSourceFileKind:
    def _drama(self, monkeypatch, tmp_path: Path, *, with_script_plan: bool):
        client, pm, source_dir = _client(
            monkeypatch,
            tmp_path,
            content_mode="drama",
            whole_source_files=[
                {"source_file": "source/a.txt", "source_kind": "novel"},
                {"source_file": "source/b.txt", "source_kind": "novel"},
            ],
            episodes=[
                _entry(1, "whole_source", source_range={"source_file": "source/a.txt", "start": 0, "end": 5}),
                _entry(2, "whole_source", source_range={"source_file": "source/b.txt", "start": 0, "end": 5}),
            ],
            source_fingerprints=compute_source_fingerprints([SourceDoc("source/a.txt", "少年下山。")]),
            episode_id_high_water=2,
        )
        (source_dir / "a.txt").write_text("少年下山。", encoding="utf-8")
        (source_dir / "b.txt").write_text("城里起火。", encoding="utf-8")
        if with_script_plan:
            for episode in (1, 2):
                plan = (
                    pm.get_project_path("demo") / "drafts" / f"episode_{episode}" / "script_plan_normalized_script.json"
                )
                plan.parent.mkdir(parents=True)
                plan.write_text("{}", encoding="utf-8")
        return client, pm

    def test_change_without_started_episodes_applies_directly(self, tmp_path, monkeypatch):
        client, pm = self._drama(monkeypatch, tmp_path, with_script_plan=False)
        before = pm.load_project("demo")

        with client:
            resp = client.put(
                "/api/v1/projects/demo/source-files/a.txt/source-kind", json={"source_kind": "screenplay"}
            )
            layout = client.get("/api/v1/projects/demo/episodes-view").json()

        assert resp.json() == {"success": True, "applied": True, "needs_confirmation": False, "affected_episodes": []}
        after = pm.load_project("demo")
        assert after["whole_source_files"][0] == {"source_file": "source/a.txt", "source_kind": "screenplay"}
        assert after["episodes"] == before["episodes"]
        assert after["source_fingerprints"] == before["source_fingerprints"]
        assert [f["source_kind"] for f in layout["files"]] == ["screenplay", "novel"]
        assert [e["source_kind"] for e in layout["episodes"]] == ["screenplay", "novel"]

    def test_change_that_stales_started_episodes_waits_for_confirmation(self, tmp_path, monkeypatch):
        client, pm = self._drama(monkeypatch, tmp_path, with_script_plan=True)

        with client:
            asked = client.put(
                "/api/v1/projects/demo/source-files/a.txt/source-kind", json={"source_kind": "screenplay"}
            )
            unchanged = pm.load_project("demo")["whole_source_files"][0]["source_kind"]
            confirmed = client.put(
                "/api/v1/projects/demo/source-files/a.txt/source-kind",
                json={"source_kind": "screenplay", "confirm": True},
            )

        assert asked.json() == {"success": True, "applied": False, "needs_confirmation": True, "affected_episodes": [1]}
        assert unchanged == "novel"
        assert confirmed.json()["applied"] is True
        assert pm.load_project("demo")["whole_source_files"][0]["source_kind"] == "screenplay"

    def test_same_kind_is_a_no_op(self, tmp_path, monkeypatch):
        client, _pm = self._drama(monkeypatch, tmp_path, with_script_plan=True)

        with client:
            resp = client.put("/api/v1/projects/demo/source-files/a.txt/source-kind", json={"source_kind": "novel"})

        assert resp.json() == {"success": True, "applied": False, "needs_confirmation": False, "affected_episodes": []}

    def test_non_drama_project_and_unknown_file_are_refused(self, tmp_path, monkeypatch):
        client, _pm, source_dir = _client(monkeypatch, tmp_path, whole_source_files=[{"source_file": "source/a.txt"}])
        (source_dir / "a.txt").write_text("正文", encoding="utf-8")

        with client:
            narration = client.put(
                "/api/v1/projects/demo/source-files/a.txt/source-kind", json={"source_kind": "screenplay"}
            )
        drama_client, _drama_pm = self._drama(monkeypatch, tmp_path / "drama", with_script_plan=False)
        with drama_client:
            unknown = drama_client.put(
                "/api/v1/projects/demo/source-files/c.txt/source-kind", json={"source_kind": "screenplay"}
            )

        assert narration.status_code == 409
        assert narration.json()["detail"] == zh_errors.MESSAGES["source_kind_not_applicable"]
        assert unknown.status_code == 404

    def test_change_is_paused_while_the_file_was_changed_outside(self, tmp_path, monkeypatch):
        client, pm = self._drama(monkeypatch, tmp_path, with_script_plan=False)
        (pm.get_project_path("demo") / "source" / "a.txt").write_text("少年走下山。", encoding="utf-8")

        with client:
            resp = client.put(
                "/api/v1/projects/demo/source-files/a.txt/source-kind", json={"source_kind": "screenplay"}
            )

        assert (resp.status_code, resp.json()["detail"]) == (409, zh_errors.MESSAGES["source_changed_outside"])
        assert pm.load_project("demo")["whole_source_files"][0]["source_kind"] == "novel"


def _two_episode_project(monkeypatch, tmp_path):
    text = "第一章。少年下山。\n第二章。城里起火。\n"
    second = text.index("第二章")
    client, pm, source_dir = _client(
        monkeypatch,
        tmp_path,
        whole_source_files=[{"source_file": "source/a.txt"}, {"source_file": "source/b.txt"}],
        episodes=[
            _entry(1, "whole_source", source_range={"source_file": "source/a.txt", "start": 0, "end": second}),
            _entry(2, "whole_source", source_range={"source_file": "source/a.txt", "start": second, "end": len(text)}),
        ],
        episode_id_high_water=2,
    )
    (source_dir / "a.txt").write_text(text, encoding="utf-8")
    (source_dir / "b.txt").write_text("第三章。夜雨。\n", encoding="utf-8")
    # 源文写在登记之后：经源文登记通道记下源文是否还有未规划的原文，与账本命令留下的状态一致
    with pm.locked_source_registration("demo"):
        pass
    return client, pm, source_dir, text


class TestSourceFileChanges:
    def test_edit_returns_the_grouped_impact_then_applies_with_its_revision(self, tmp_path, monkeypatch):
        client, pm, source_dir, text = _two_episode_project(monkeypatch, tmp_path)
        new_text = text.replace("少年下山", "少年走下山")

        with client:
            preview = client.put("/api/v1/projects/demo/source-files/a.txt/text", json={"text": new_text})
            body = preview.json()
            applied = client.put(
                "/api/v1/projects/demo/source-files/a.txt/text",
                json={"text": new_text, "revision": body["revision"]},
            )

        assert preview.status_code == 200
        assert body["status"] == "confirmation_required"
        assert (body["impact"]["changed_without_products"], body["impact"]["shifted"]) == ([1], [2])
        assert body["impact"]["text"].split("\n") == [
            zh_errors.MESSAGES["source_file_impact_shifted"].format(episodes="第 2 集"),
            zh_errors.MESSAGES["source_file_impact_changed_without_products"].format(episodes="第 1 集"),
        ]
        assert applied.json()["status"] == "applied"
        assert (source_dir / "a.txt").read_text(encoding="utf-8") == new_text
        assert pm.load_project("demo")["episodes"][0]["ledger_status"] == "stale"

    def test_move_delete_and_replace_go_through_the_same_protocol(self, tmp_path, monkeypatch):
        client, pm, source_dir, _text = _two_episode_project(monkeypatch, tmp_path)

        with client:
            moved = client.post("/api/v1/projects/demo/source-files/b.txt/move", json={"direction": "up"})
            replaced = client.post(
                "/api/v1/projects/demo/source-files/b.txt/replace",
                files={"file": ("新的第三章.txt", "第三章。大雨。\n".encode(), "text/plain")},
            )
            replaced_text = (source_dir / "b.txt").read_text(encoding="utf-8")
            deleted = client.post("/api/v1/projects/demo/source-files/b.txt/delete", json={})

        # b.txt 里没有切出集，调序不改变任何集的先后，直接执行
        assert moved.json()["status"] == "applied"
        assert replaced.json()["status"] == "applied"
        assert replaced_text == "第三章。大雨。\n"
        assert deleted.json()["status"] == "applied"
        assert [item["source_file"] for item in pm.load_project("demo")["whole_source_files"]] == ["source/a.txt"]
        assert [entry["episode"] for entry in pm.load_project("demo")["episodes"]] == [1, 2]

    def test_change_is_refused_while_a_displaced_episode_has_active_tasks(
        self, tmp_path, monkeypatch, active_episode_tasks
    ):
        client, pm, source_dir, text = _two_episode_project(monkeypatch, tmp_path)
        before = (pm.get_project_path("demo") / "project.json").read_bytes()

        active_episode_tasks["running"] = [
            {"resource_id": "script_plan", "script_file": None, "payload": {"episode": 2}}
        ]

        with client:
            preview = client.post("/api/v1/projects/demo/source-files/a.txt/delete", json={})
            revision = preview.json()["revision"]
            deleted = client.post("/api/v1/projects/demo/source-files/a.txt/delete", json={"revision": revision})
            edited = client.put("/api/v1/projects/demo/source-files/a.txt/text", json={"text": "第一章。少年下山。\n"})
            edited_applied = client.put(
                "/api/v1/projects/demo/source-files/a.txt/text",
                json={"text": "第一章。少年下山。\n", "revision": edited.json()["revision"]},
            )

        assert preview.json()["impact"]["removed"] == [1, 2]
        for resp in (deleted, edited_applied):
            assert resp.status_code == 409
            assert resp.json()["detail"] == zh_errors.MESSAGES["source_file_change_tasks_active"]
        assert (pm.get_project_path("demo") / "project.json").read_bytes() == before
        assert (source_dir / "a.txt").read_text(encoding="utf-8") == text

    def test_refusals_map_to_status_codes(self, tmp_path, monkeypatch):
        client, _pm, _source_dir, _text = _two_episode_project(monkeypatch, tmp_path)

        with client:
            first_up = client.post("/api/v1/projects/demo/source-files/a.txt/move", json={"direction": "up"})
            unknown = client.put("/api/v1/projects/demo/source-files/x.txt/text", json={"text": "x"})
            empty = client.put("/api/v1/projects/demo/source-files/a.txt/text", json={"text": "  "})

        assert (first_up.status_code, first_up.json()["detail"]) == (
            409,
            zh_errors.MESSAGES["source_file_change_move_out_of_range"],
        )
        assert unknown.status_code == 404
        assert empty.status_code == 422


class TestExternalSourceChange:
    def _changed(self, monkeypatch, tmp_path, *, snapshot: bool = True):
        client, pm, source_dir, text = _two_episode_project(monkeypatch, tmp_path)
        if snapshot:
            (source_dir / "snapshots").mkdir()
            (source_dir / "snapshots" / "a.txt").write_text(text, encoding="utf-8")
        new_text = text.replace("少年下山", "少年走下山")
        (source_dir / "a.txt").write_text(new_text, encoding="utf-8")
        return client, pm, source_dir, new_text

    def test_the_view_lists_affected_episodes_and_accepting_updates_the_ledger(self, tmp_path, monkeypatch):
        client, pm, source_dir, new_text = self._changed(monkeypatch, tmp_path)

        with client:
            view = client.get("/api/v1/projects/demo/episodes-view").json()
            (change,) = view["external_changes"]
            accepted = client.post(
                "/api/v1/projects/demo/source-files/a.txt/accept-external", json={"revision": change["revision"]}
            )
            after = client.get("/api/v1/projects/demo/episodes-view").json()

        assert [f["changed_outside"] for f in view["files"]] == [True, False]
        assert change["source_file"] == "source/a.txt"
        assert change["problem"] is None
        assert (change["impact"]["changed_without_products"], change["impact"]["shifted"]) == ([1], [2])
        assert change["impact"]["text"].split("\n") == [
            zh_errors.MESSAGES["source_file_impact_shifted"].format(episodes="第 2 集"),
            zh_errors.MESSAGES["source_file_impact_changed_without_products"].format(episodes="第 1 集"),
        ]
        assert accepted.json()["status"] == "applied"
        assert pm.load_project("demo")["episodes"][0]["ledger_status"] == "stale"
        assert (source_dir / "snapshots" / "a.txt").read_text(encoding="utf-8") == new_text
        assert after["external_changes"] == []
        assert [f["changed_outside"] for f in after["files"]] == [False, False]

    def test_a_file_that_cannot_be_aligned_states_why(self, tmp_path, monkeypatch):
        client, pm, _source_dir, _text = self._changed(monkeypatch, tmp_path, snapshot=False)
        pm.update_project("demo", lambda project: project.update(source_fingerprints={"source/a.txt": "0" * 64}))

        with client:
            (change,) = client.get("/api/v1/projects/demo/episodes-view").json()["external_changes"]
            accepted = client.post("/api/v1/projects/demo/source-files/a.txt/accept-external", json={})

        assert change["impact"] is None
        assert change["problem"] == zh_errors.MESSAGES["source_file_change_source_snapshot_missing"]
        assert accepted.status_code == 409

    def test_accepting_an_unchanged_file_is_refused(self, tmp_path, monkeypatch):
        client, _pm, _source_dir, _text = _two_episode_project(monkeypatch, tmp_path)

        with client:
            resp = client.post("/api/v1/projects/demo/source-files/a.txt/accept-external", json={})

        assert (resp.status_code, resp.json()["detail"]) == (
            409,
            zh_errors.MESSAGES["source_file_change_source_not_changed"],
        )
