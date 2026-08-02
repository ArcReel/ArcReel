"""step1→step2 审核 gate 路由测试：审阅读取、内容编辑、确认动作的可测状态流转。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.json_io import atomic_write_json
from lib.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import script_review as router_mod
from tests.auth_deps import AUTH_DEPENDENCIES


def _drama_step1() -> dict:
    return {
        "title": "第一集",
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "segment_break": False,
                "characters_in_scene": ["阿离"],
                "scenes": [],
                "props": [],
                "scene_description": "雨夜，阿离立于屋檐下",
                "utterances": [
                    {"kind": "voiceover", "speaker": None, "text": "三年后。"},
                    {"kind": "dialogue", "speaker": "阿离", "text": "你终于回来了。"},
                ],
                "source_text": "三年后，阿离立于屋檐下：你终于回来了。",
            }
        ],
    }


def _rv_step1() -> dict:
    return {
        "units": [
            {
                "unit_id": "E1U01",
                "shots": [{"text": "@[阿离] 立于屋檐下。"}],
                "duration_seconds": 4,
                "references": [{"type": "character", "name": "阿离"}],
            }
        ],
    }


def _client(monkeypatch, tmp_path: Path, *, generation_mode: str | None = None) -> tuple[TestClient, ProjectManager]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "drama")
    pm.add_character("demo", "阿离", "少女")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    if generation_mode is not None:
        pm.update_project("demo", lambda p: p.__setitem__("generation_mode", generation_mode))

    monkeypatch.setattr(router_mod, "get_project_manager", lambda: pm)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(router_mod.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app), pm


def _write_step1(pm: ProjectManager, content: dict) -> None:
    drafts = pm.get_project_path("demo") / "drafts" / "episode_1"
    drafts.mkdir(parents=True, exist_ok=True)
    atomic_write_json(drafts / "step1_normalized_script.json", content)


def _write_rv_step1(pm: ProjectManager, content: dict) -> None:
    drafts = pm.get_project_path("demo") / "drafts" / "episode_1"
    drafts.mkdir(parents=True, exist_ok=True)
    atomic_write_json(drafts / "step1_reference_units.json", content)


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
            from lib import script_review

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


class TestReferenceVideoRouter:
    def test_full_gate_flow(self, tmp_path, monkeypatch):
        """rv 走同一 HTTP gate：结构化 units 可读、可编辑、web 确认放行 step2（与 web 确认等价）。"""
        from lib import script_review

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"

            no_step1_body = client.get(base).json()
            assert no_step1_body["status"] == "no_step1"
            assert no_step1_body["quarantine"] is None

            _write_rv_step1(pm, _rv_step1())
            body = client.get(base).json()
            assert body["status"] == "pending_review"
            assert body["content"]["units"][0]["unit_id"] == "E1U01"
            assert body["quarantine"] is None
            assert script_review.gate_blocks_step2(pm.get_project_path("demo"), pm.load_project("demo"), 1) is True

            # 编辑 shot 文本 → 重新待审
            edited = _rv_step1()
            edited["units"][0]["shots"][0]["text"] = "@[阿离] 转身离去。"
            put = client.put(f"{base}/content", json=edited)
            assert put.status_code == 200
            assert put.json()["status"] == "pending_review"

            confirmed = client.post(f"{base}/confirm")
            assert confirmed.status_code == 200
            assert confirmed.json()["status"] == "confirmed"
            assert script_review.gate_blocks_step2(pm.get_project_path("demo"), pm.load_project("demo"), 1) is False

    def test_quarantine_surfaced_with_recomputed_line_anchored_violations(self, tmp_path, monkeypatch):
        """隔离草稿在场时 GET 附带 ``quarantine`` 字段：违约按产出时那套校验器读时重算，
        不信任草稿里上一轮的快照（这里把快照消息故意写成 "stale" 来验证）。"""
        from lib.reference_video.draft_validation import DraftViolation
        from lib.reference_video.quarantine import QUARANTINE_KIND_STEP1, write_quarantine
        from server.agent_runtime.sdk_tools import text_generation as mod

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        novel = "阿离站在屋檐下。"
        (project_path / "source").mkdir(parents=True, exist_ok=True)
        (project_path / "source" / "episode_1.txt").write_text(novel, encoding="utf-8")

        async def _fake_caps(_project):
            return mod.ReferenceSplitCaps(
                default_duration=4,
                durations=[4, 6, 8],
                reference_durations=[4, 6, 8],
                text_durations=[4, 6, 8],
                max_duration=8,
                max_refs=3,
                raw={},
            )

        monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", _fake_caps)

        flat_units = [{"duration_seconds": 4, "source_text": novel, "text": "镜头1：门开了\n@[阿离]：｛我来了。｝"}]
        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_STEP1,
            content={"units": flat_units},
            violations=[DraftViolation("stale", code="fullwidth_braces", label="unit E1U01", line=1)],
            meta={"source": "source/episode_1.txt"},
        )

        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            body = client.get(base).json()
            assert body["status"] == "pending_review"
            assert body["quarantine"] is not None
            violations = body["quarantine"]["violations"]
            assert len(violations) == 1
            assert violations[0]["code"] == "fullwidth_braces"
            assert violations[0]["line"] == 1
            assert violations[0]["message"] != "stale"

            # 隔离草稿在场时确认被拒：正式 step1 还没有一份可放行的内容。
            confirmed = client.post(f"{base}/confirm")
            assert confirmed.status_code == 409
