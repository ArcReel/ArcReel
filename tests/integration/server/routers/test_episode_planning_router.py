"""「AI 规划分集」与重新规划的 Web 入口：规划的准入拒绝，候选的采纳与放弃。"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.episode.episode_replan import create_replan_candidate
from lib.i18n.zh import errors as zh_errors
from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import episode_planning
from tests.auth_deps import AUTH_DEPENDENCIES


@pytest.fixture
def planning_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    pm = ProjectManager(tmp_path / "projects")
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(pm.data_root))
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    monkeypatch.setattr(episode_planning, "get_project_manager", lambda: pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(episode_planning.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


def test_planning_without_a_whole_source_is_refused(planning_client: TestClient) -> None:
    with planning_client:
        resp = planning_client.post("/api/v1/projects/demo/episode-planning", json={"instructions": "按章节"})

    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("分集规划未能提交：项目还没有整本源文")


def test_instructions_are_the_only_accepted_field(planning_client: TestClient) -> None:
    with planning_client:
        resp = planning_client.post("/api/v1/projects/demo/episode-planning", json={"continue_to_end": False})

    # 请求体校验在准入之前：detail 是逐字段的校验错误列表，不是准入拒绝的文案
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, list)
    assert any("continue_to_end" in item["loc"] for item in detail)


NOVEL = "少年下山。城里起火。夜雨。"


def _cut(episode: int, start: int, end: int) -> dict:
    return {
        "episode": episode,
        "title": f"旧{episode}",
        "script_file": f"scripts/episode_{episode}.json",
        "source_origin": "whole_source",
        "source_range": {"source_file": "source/novel.txt", "start": start, "end": end},
        "ledger_status": "planned",
    }


@pytest.fixture
def replan_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, ProjectManager, str]:
    """两集的项目带一份从第 2 集起、已生成完的候选：把后两句拆成两集。"""
    pm = ProjectManager(tmp_path / "projects")
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(pm.data_root))
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    episodes = [_cut(1, 0, 5), _cut(2, 5, 13)]
    pm.update_project(
        "demo",
        lambda project: project.update(
            whole_source_files=[{"source_file": "source/novel.txt"}], episodes=episodes, episode_id_high_water=2
        ),
    )
    source_dir = pm.get_project_path("demo") / "source"
    (source_dir / "novel.txt").write_text(NOVEL, encoding="utf-8")
    for entry in episodes:
        rng = entry["source_range"]
        (source_dir / f"episode_{entry['episode']}.txt").write_text(NOVEL[rng["start"] : rng["end"]], encoding="utf-8")
    candidate_id = create_replan_candidate(pm.get_project_path("demo"), episode=2, instructions=None)

    def fill(project: dict) -> None:
        project["episode_replan"]["episodes"] = [
            {"title": "新1", "hook": "", "source_range": {"source_file": "source/novel.txt", "start": 5, "end": 10}},
            {"title": "新2", "hook": "", "source_range": {"source_file": "source/novel.txt", "start": 10, "end": 13}},
        ]
        project["episode_replan"]["complete"] = True

    pm.update_project("demo", fill)

    async def idle(*_args) -> bool:
        return False

    monkeypatch.setattr(episode_planning, "get_project_manager", lambda: pm)
    monkeypatch.setattr(episode_planning, "episode_planning_active", idle)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(episode_planning.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app), pm, candidate_id


def test_adopting_returns_the_consequences_first_then_adopts_with_their_revision(replan_client) -> None:
    client, pm, candidate_id = replan_client

    with client:
        view = client.post("/api/v1/projects/demo/episode-replan/adopt", json={"candidate_id": candidate_id})
        impact = view.json()["impact"]
        adopted = client.post(
            "/api/v1/projects/demo/episode-replan/adopt",
            json={"candidate_id": candidate_id, "revision": impact["revision"]},
        )

    assert view.json()["status"] == "confirmation_required"
    assert "「旧2」" in impact["text"]
    assert adopted.json() == {"status": "adopted", "episodes": [3, 4], "deleted": []}
    assert [entry["episode"] for entry in pm.load_project("demo")["episodes"]] == [1, 3, 4]


def test_adopting_is_refused_while_a_replaced_episode_has_active_tasks(replan_client, monkeypatch) -> None:
    client, pm, candidate_id = replan_client

    async def busy(_project_name: str, episode: int) -> bool:
        return episode == 2

    monkeypatch.setattr(episode_planning, "episode_has_active_tasks", busy)

    with client:
        revision = client.post(
            "/api/v1/projects/demo/episode-replan/adopt", json={"candidate_id": candidate_id}
        ).json()["impact"]["revision"]
        refused = client.post(
            "/api/v1/projects/demo/episode-replan/adopt", json={"candidate_id": candidate_id, "revision": revision}
        )

    assert refused.status_code == 409
    assert refused.json()["detail"] == zh_errors.MESSAGES["episode_replan_tasks_active"]
    assert [entry["episode"] for entry in pm.load_project("demo")["episodes"]] == [1, 2]


def test_discarding_a_stale_id_is_refused_and_the_current_one_is_removed(replan_client) -> None:
    client, pm, candidate_id = replan_client

    with client:
        stale = client.post("/api/v1/projects/demo/episode-replan/discard", json={"candidate_id": "other"})
        discarded = client.post("/api/v1/projects/demo/episode-replan/discard", json={"candidate_id": candidate_id})

    assert stale.status_code == 404
    assert discarded.json() == {"status": "discarded"}
    assert "episode_replan" not in pm.load_project("demo")


def test_continuing_a_finished_or_missing_plan_is_refused(replan_client) -> None:
    client, _pm, candidate_id = replan_client

    with client:
        finished = client.post("/api/v1/projects/demo/episode-replan/continue", json={"candidate_id": candidate_id})
        missing = client.post("/api/v1/projects/demo/episode-replan/continue", json={"candidate_id": "other"})

    assert finished.status_code == 409
    assert finished.json()["detail"] == zh_errors.MESSAGES["episode_replan_candidate_complete"]
    assert missing.status_code == 404
