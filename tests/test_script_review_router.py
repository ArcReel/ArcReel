"""step1→step2 审核 gate 路由测试：审阅读取、内容编辑、确认动作的可测状态流转。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.json_io import atomic_write_json
from lib.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.routers import script_review as router_mod


def _drama_step1() -> dict:
    return {
        "title": "第一集",
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "segment_break": False,
                "characters_in_scene": ["阿离"],
                "scenes": ["屋檐"],
                "props": ["信纸"],
                "scene_description": "雨夜，阿离立于屋檐下",
                "utterances": [
                    {"kind": "voiceover", "speaker": None, "text": "三年后。"},
                    {"kind": "dialogue", "speaker": "阿离", "text": "你终于回来了。"},
                ],
                "source_text": "三年后，阿离立于屋檐下：你终于回来了。",
            }
        ],
    }


def _client(monkeypatch, tmp_path: Path) -> tuple[TestClient, ProjectManager]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "drama")
    pm.add_character("demo", "阿离", "少女")
    pm.add_project_scene("demo", "屋檐", "雨夜屋檐")
    pm.add_prop("demo", "信纸", "关键证据")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")

    monkeypatch.setattr(router_mod, "get_project_manager", lambda: pm)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(router_mod.router, prefix="/api/v1")
    return TestClient(app), pm


def _write_step1(pm: ProjectManager, content: dict) -> None:
    drafts = pm.get_project_path("demo") / "drafts" / "episode_1"
    drafts.mkdir(parents=True, exist_ok=True)
    atomic_write_json(drafts / "step1_normalized_script.json", content)


class TestScriptReviewRouter:
    def test_full_gate_flow(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"

            # step1 未产出
            got = client.get(base)
            assert got.status_code == 200
            assert got.json()["status"] == "no_step1"

            # step1 产出 → pending_review，结构化内容可见
            _write_step1(pm, _drama_step1())
            got = client.get(base)
            body = got.json()
            assert body["status"] == "pending_review"
            assert body["content"]["scenes"][0]["utterances"][1]["speaker"] == "阿离"

            # 确认前 step2 被阻塞
            import lib.script_review as script_review

            assert script_review.gate_blocks_step2(pm.get_project_path("demo"), pm.load_project("demo"), 1) is True

            # 确认 → confirmed，step2 放行
            confirmed = client.post(f"{base}/confirm")
            assert confirmed.status_code == 200
            assert confirmed.json()["status"] == "confirmed"
            assert script_review.gate_blocks_step2(pm.get_project_path("demo"), pm.load_project("demo"), 1) is False

    def test_edit_content_repends(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            _write_step1(pm, _drama_step1())
            client.post(f"{base}/confirm")

            edited = _drama_step1()
            edited["scenes"][0]["utterances"][1]["text"] = "你怎么才回来。"
            put = client.put(f"{base}/content", json=edited)
            assert put.status_code == 200
            assert put.json()["status"] == "pending_review"

            got = client.get(base)
            assert got.json()["content"]["scenes"][0]["utterances"][1]["text"] == "你怎么才回来。"

    def test_put_invalid_content_422(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            _write_step1(pm, _drama_step1())
            bad = _drama_step1()
            bad["scenes"][0]["utterances"][1] = {"kind": "dialogue", "speaker": None, "text": "无人"}
            put = client.put(f"{base}/content", json=bad)
            assert put.status_code == 422

    def test_confirm_without_step1_409(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            confirmed = client.post(f"{base}/confirm")
            assert confirmed.status_code == 409

    def test_get_unregistered_episode_404(self, tmp_path, monkeypatch):
        """未在 project.json 登记的分集 → GET 返回 404，而非误报 no_step1 的 200。"""
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            got = client.get("/api/v1/projects/demo/episodes/99/script-review")
            assert got.status_code == 404

    def test_confirm_with_qa_block_returns_409_payload(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            content = _drama_step1()
            content["scenes"][0]["props"] = ["信纸", "玉佩"]
            _write_step1(pm, content)

            resp = client.post(f"{base}/confirm")
            assert resp.status_code == 409
            detail = resp.json()["detail"]
            assert detail["code"] == "qa_gate_blocked"
            assert detail["qa_summary"]["block_count"] == 1
            assert detail["qa_findings"][0]["code"] == "missing_prop_reference"

    def test_confirm_with_unsupported_duration_returns_409_payload(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        pm.update_project("demo", lambda project: project.update({"_supported_durations": [4, 6, 8]}))
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            content = _drama_step1()
            content["scenes"][0]["duration_seconds"] = 7
            _write_step1(pm, content)

            resp = client.post(f"{base}/confirm")

            assert resp.status_code == 409
            detail = resp.json()["detail"]
            assert detail["code"] == "qa_gate_blocked"
            assert detail["qa_findings"][0]["code"] == "unsupported_duration"
