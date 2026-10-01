"""集页填写本集原文：PUT /projects/{name}/episodes/{episode}/source。"""

from pathlib import Path

from lib.i18n.zh import errors as zh_errors
from lib.project.project_manager import ProjectManager
from lib.workflow.workflow_state import WorkflowStateService
from tests.integration.server.routers.projects_router_support import build_projects_client


def _project(tmp_path: Path, episodes: list[dict]) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.update_project("demo", lambda project: project.update(episodes=episodes, episode_id_high_water=9))
    return pm


def _entry(episode: int, origin: str, **fields) -> dict:
    return {
        "episode": episode,
        "title": f"第 {episode} 集",
        "script_file": f"scripts/episode_{episode}.json",
        "source_origin": origin,
        **fields,
    }


class TestEpisodeSourceEndpoint:
    def test_filling_a_no_source_episode_makes_it_own_source_and_admits_script_planning(self, tmp_path, monkeypatch):
        pm = _project(tmp_path, [_entry(3, "none")])
        assert WorkflowStateService(pm).get_status("demo", 3).operations["prepare_script_plan"].state == "refused"

        with build_projects_client(monkeypatch, pm) as client:
            resp = client.put("/api/v1/projects/demo/episodes/3/source", json={"text": "粘贴进来的原文\r\n第二行"})

        assert resp.status_code == 200
        assert resp.json() == {
            "success": True,
            "episode": 3,
            "source_origin": "own",
            "applied": True,
            "needs_confirmation": False,
            "affected_episodes": [],
        }
        project = pm.load_project("demo")
        assert project["episodes"][0]["source_origin"] == "own"
        episode_file = pm.get_project_path("demo") / "source" / "episode_3.txt"
        assert episode_file.read_text(encoding="utf-8") == "粘贴进来的原文\n第二行"
        status = WorkflowStateService(pm).get_status("demo", 3)
        assert status.content is not None
        assert status.content.episode_source == "present"
        assert status.operations["prepare_script_plan"].state == "admitted"

    def test_own_source_episode_text_can_be_rewritten(self, tmp_path, monkeypatch):
        pm = _project(tmp_path, [_entry(2, "own")])
        (pm.get_project_path("demo") / "source" / "episode_2.txt").write_text("旧原文", encoding="utf-8")

        with build_projects_client(monkeypatch, pm) as client:
            resp = client.put("/api/v1/projects/demo/episodes/2/source", json={"text": "新原文"})

        assert resp.status_code == 200
        assert (pm.get_project_path("demo") / "source" / "episode_2.txt").read_text(encoding="utf-8") == "新原文"

    def test_filling_a_no_source_episode_keeps_a_stray_file_with_the_same_name(self, tmp_path, monkeypatch):
        pm = _project(tmp_path, [_entry(3, "none")])
        source_dir = pm.get_project_path("demo") / "source"
        (source_dir / "episode_3.txt").write_text("不是本集原文的旧文件", encoding="utf-8")

        with build_projects_client(monkeypatch, pm) as client:
            resp = client.put("/api/v1/projects/demo/episodes/3/source", json={"text": "填写的原文"})

        assert resp.status_code == 200
        assert (source_dir / "episode_3.txt").read_text(encoding="utf-8") == "填写的原文"
        assert (source_dir / "_episode_3.txt.bak").read_text(encoding="utf-8") == "不是本集原文的旧文件"

    def test_cut_episode_source_is_derived_and_refused(self, tmp_path, monkeypatch):
        cut = _entry(1, "whole_source", source_range={"source_file": "source/novel.txt", "start": 0, "end": 2})
        pm = _project(tmp_path, [cut])
        episode_file = pm.get_project_path("demo") / "source" / "episode_1.txt"
        episode_file.write_text("切出", encoding="utf-8")

        with build_projects_client(monkeypatch, pm) as client:
            resp = client.put("/api/v1/projects/demo/episodes/1/source", json={"text": "改写"})

        assert resp.status_code == 409
        assert resp.json()["detail"] == zh_errors.MESSAGES["episode_source_derived"]
        assert episode_file.read_text(encoding="utf-8") == "切出"
        assert pm.load_project("demo")["episodes"] == [cut]

    def test_blank_text_and_unknown_episode_are_refused(self, tmp_path, monkeypatch):
        pm = _project(tmp_path, [_entry(3, "none")])

        with build_projects_client(monkeypatch, pm) as client:
            blank = client.put("/api/v1/projects/demo/episodes/3/source", json={"text": " \n "})
            missing = client.put("/api/v1/projects/demo/episodes/8/source", json={"text": "原文"})

        assert blank.status_code == 422
        assert blank.json()["detail"] == zh_errors.MESSAGES["episode_source_empty"]
        assert missing.status_code == 404
        assert pm.load_project("demo")["episodes"] == [_entry(3, "none")]
        assert not (pm.get_project_path("demo") / "source" / "episode_3.txt").exists()
        assert not (pm.get_project_path("demo") / "source" / "episode_8.txt").exists()

    def test_drama_episode_records_the_chosen_source_kind_and_keeps_it_when_omitted(self, tmp_path, monkeypatch):
        pm = _project(tmp_path, [_entry(3, "none")])
        pm.update_project("demo", lambda project: project.update(content_mode="drama"))

        with build_projects_client(monkeypatch, pm) as client:
            filled = client.put(
                "/api/v1/projects/demo/episodes/3/source", json={"text": "剧本原文", "source_kind": "screenplay"}
            )
            rewritten = client.put("/api/v1/projects/demo/episodes/3/source", json={"text": "改过的剧本原文"})

        assert (filled.status_code, rewritten.status_code) == (200, 200)
        assert pm.load_project("demo")["episodes"][0]["source_kind"] == "screenplay"

    def test_non_drama_episode_records_no_source_kind(self, tmp_path, monkeypatch):
        pm = _project(tmp_path, [_entry(3, "none")])

        with build_projects_client(monkeypatch, pm) as client:
            resp = client.put(
                "/api/v1/projects/demo/episodes/3/source", json={"text": "原文", "source_kind": "screenplay"}
            )

        assert resp.status_code == 200
        assert "source_kind" not in pm.load_project("demo")["episodes"][0]

    def test_kind_change_that_stales_this_episodes_script_plan_waits_for_confirmation(self, tmp_path, monkeypatch):
        pm = _project(tmp_path, [_entry(2, "own", source_kind="novel")])
        pm.update_project("demo", lambda project: project.update(content_mode="drama"))
        project_dir = pm.get_project_path("demo")
        (project_dir / "source" / "episode_2.txt").write_text("旧原文", encoding="utf-8")
        plan = project_dir / "drafts" / "episode_2" / "script_plan_normalized_script.json"
        plan.parent.mkdir(parents=True)
        plan.write_text("{}", encoding="utf-8")

        with build_projects_client(monkeypatch, pm) as client:
            text_only = client.put("/api/v1/projects/demo/episodes/2/source", json={"text": "改过的原文"})
            asked = client.put(
                "/api/v1/projects/demo/episodes/2/source", json={"text": "剧本原文", "source_kind": "screenplay"}
            )
            untouched = (
                pm.load_project("demo")["episodes"][0]["source_kind"],
                (project_dir / "source" / "episode_2.txt").read_text(encoding="utf-8"),
            )
            confirmed = client.put(
                "/api/v1/projects/demo/episodes/2/source",
                json={"text": "剧本原文", "source_kind": "screenplay", "confirm": True},
            )

        assert text_only.json()["applied"] is True
        assert asked.json() == {
            "success": True,
            "episode": 2,
            "source_origin": "own",
            "applied": False,
            "needs_confirmation": True,
            "affected_episodes": [2],
        }
        assert untouched == ("novel", "改过的原文")
        assert confirmed.json()["applied"] is True
        assert pm.load_project("demo")["episodes"][0]["source_kind"] == "screenplay"
        assert (project_dir / "source" / "episode_2.txt").read_text(encoding="utf-8") == "剧本原文"

    def test_kind_change_without_a_script_plan_applies_directly(self, tmp_path, monkeypatch):
        pm = _project(tmp_path, [_entry(2, "own", source_kind="novel")])
        pm.update_project("demo", lambda project: project.update(content_mode="drama"))
        (pm.get_project_path("demo") / "source" / "episode_2.txt").write_text("旧原文", encoding="utf-8")

        with build_projects_client(monkeypatch, pm) as client:
            resp = client.put(
                "/api/v1/projects/demo/episodes/2/source", json={"text": "剧本原文", "source_kind": "screenplay"}
            )

        assert resp.json()["applied"] is True
        assert pm.load_project("demo")["episodes"][0]["source_kind"] == "screenplay"
