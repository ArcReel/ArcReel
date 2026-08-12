"""step1→step2 审核 gate 路由测试：审阅读取、内容编辑、确认动作的可测状态流转。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.json_io import atomic_write_json
from lib.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import script_review as router_mod
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import fake_reference_caps_fetcher


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
    @pytest.mark.unit
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

    @pytest.mark.unit
    def test_edit_content_repends(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            _write_step1(pm, _drama_step1())
            client.post(f"{base}/confirm")

            edited = _drama_step1()
            edited["scenes"][0]["scene_description"] = "雨势渐急，阿离仍站在屋檐下"
            put = client.put(f"{base}/content", json=edited)
            assert put.status_code == 200
            assert put.json()["status"] == "pending_review"

            got = client.get(base)
            assert got.json()["content"]["scenes"][0]["scene_description"] == "雨势渐急，阿离仍站在屋檐下"

    @pytest.mark.unit
    def test_editing_legacy_mixed_speech_returns_structured_atomic_rejection(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            original = _drama_step1()
            _write_step1(pm, original)
            edited = _drama_step1()
            edited["scenes"][0]["utterances"][1]["text"] = "你怎么才回来。"

            put = client.put(f"{base}/content", json=edited)

            assert put.status_code == 409
            detail = put.json()["detail"]
            assert detail["unit_id"] == "E1S01"
            assert detail["problems"][0]["code"] == "mixed_speech"
            assert client.get(base).json()["content"] == original

    @pytest.mark.unit
    def test_put_empty_dialogue_speaker_returns_structured_409(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            _write_step1(pm, _drama_step1())
            bad = _drama_step1()
            bad["scenes"][0]["utterances"][1] = {"kind": "dialogue", "speaker": None, "text": "无人"}
            put = client.put(f"{base}/content", json=bad)
            assert put.status_code == 409
            detail = put.json()["detail"]
            assert detail["unit_id"] == "E1S01"
            assert detail["problems"][0]["code"] == "empty_speaker"
            assert detail["problems"][0]["locations"] == [{"path": ["utterances", 1, "speaker"], "line": None}]

    @pytest.mark.unit
    def test_put_invalid_non_speech_content_422(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            _write_step1(pm, _drama_step1())
            bad = _drama_step1()
            bad["scenes"][0]["duration_seconds"] = "invalid"
            put = client.put(f"{base}/content", json=bad)
            assert put.status_code == 422

    @pytest.mark.unit
    def test_confirm_without_step1_409(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            confirmed = client.post(f"{base}/confirm")
            assert confirmed.status_code == 409

    @pytest.mark.unit
    def test_get_unregistered_episode_404(self, tmp_path, monkeypatch):
        """未在 project.json 登记的分集 → GET 返回 404，而非误报 no_step1 的 200。"""
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            got = client.get("/api/v1/projects/demo/episodes/99/script-review")
            assert got.status_code == 404


class TestReferenceVideoRouter:
    @pytest.mark.unit
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

    @pytest.mark.unit
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

        monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", fake_reference_caps_fetcher(max_duration=8))

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

    @pytest.mark.unit
    def test_quarantine_schema_invalid_keeps_raw_content(self, tmp_path, monkeypatch):
        """草稿 units 被改成非数组：违约报 schema_invalid，``content`` 原样回传（不做收编），
        呈现层据此退回原始文本视图而非当作 units 列表遍历。"""
        from lib.reference_video.draft_validation import DraftViolation
        from lib.reference_video.quarantine import QUARANTINE_KIND_STEP1, write_quarantine
        from server.agent_runtime.sdk_tools import text_generation as mod

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        (project_path / "source").mkdir(parents=True, exist_ok=True)
        (project_path / "source" / "episode_1.txt").write_text("阿离站在屋檐下。", encoding="utf-8")

        monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", fake_reference_caps_fetcher(max_duration=8))
        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_STEP1,
            content={"units": "被改坏了"},
            violations=[DraftViolation("stale", code="schema_invalid")],
            meta={"source": "source/episode_1.txt"},
        )

        with client:
            body = client.get("/api/v1/projects/demo/episodes/1/script-review").json()
            quarantine = body["quarantine"]
            assert quarantine["content"] == {"units": "被改坏了"}
            assert [v["code"] for v in quarantine["violations"]] == ["schema_invalid"]
            assert quarantine["violations"][0]["message"] != "stale"

    @pytest.mark.unit
    def test_quarantine_meta_broken_reports_recompute_failure_not_snapshot(self, tmp_path, monkeypatch):
        """``meta.source`` 缺失 → 无从重算：报「无法重算」本身，而不是退回草稿里那份上一轮
        快照——报告一律对现值负责。"""
        from lib.reference_video.draft_validation import DraftViolation
        from lib.reference_video.quarantine import QUARANTINE_KIND_STEP1, write_quarantine

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        write_quarantine(
            pm.get_project_path("demo"),
            1,
            QUARANTINE_KIND_STEP1,
            content={"units": [{"duration_seconds": 4, "source_text": "原文", "text": "镜头1：门开了"}]},
            violations=[DraftViolation("stale", code="fullwidth_braces", label="unit E1U01", line=1)],
            meta={},
        )

        with client:
            body = client.get("/api/v1/projects/demo/episodes/1/script-review").json()
            violations = body["quarantine"]["violations"]
            assert [v["code"] for v in violations] == ["quarantine_unreadable"]
            assert "stale" not in violations[0]["message"]
            assert violations[0]["label"] == ""

    @pytest.mark.unit
    def test_supported_durations_exposed_for_reference_video_only(self, tmp_path, monkeypatch):
        """rv 变体的 GET 带出档位表供 web 渲染时长选择；drama 变体下为 None。"""
        rv_client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        with rv_client:
            _write_rv_step1(pm, _rv_step1())
            body = rv_client.get("/api/v1/projects/demo/episodes/1/script-review").json()
            durations = body["supported_durations"]
            assert durations is None or (isinstance(durations, list) and all(isinstance(d, int) for d in durations))
            # duration_tiers 字段始终存在（未收窄或无法解析型号时为 None），供前端区分「未收窄
            # 全集」与「收窄后的逐 unit 生效档位」——不能靠 KeyError 兜底。
            assert "duration_tiers" in body

        drama_client, drama_pm = _client(monkeypatch, tmp_path / "drama")
        with drama_client:
            _write_step1(drama_pm, _drama_step1())
            body = drama_client.get("/api/v1/projects/demo/episodes/1/script-review").json()
            assert body["supported_durations"] is None
            assert body["duration_tiers"] is None

    @pytest.mark.integration
    def test_quarantine_corrupted_envelope_reported_not_treated_as_clean(self, tmp_path, monkeypatch):
        """隔离草稿文件存在但信封本身损坏（非法 JSON）：``read_quarantine`` 按其自身读取口径
        返回 None，但 GET 响应不能把这等同于「无隔离草稿」——那会让面板显示干净态、放行确认，
        而 confirm() 仍会按文件存在性 409（用户点确认却总是失败，且看不到任何解释）。
        """
        from lib import script_review as lib_script_review

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        _write_rv_step1(pm, _rv_step1())

        quarantine_path = lib_script_review.step1_quarantine_path(project_path, pm.load_project("demo"), 1)
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        quarantine_path.write_text("{ 这不是合法 JSON", encoding="utf-8")

        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            body = client.get(base).json()
            quarantine = body["quarantine"]
            assert quarantine is not None
            assert quarantine["content"] is None
            assert [v["code"] for v in quarantine["violations"]] == ["quarantine_unreadable"]

            confirmed = client.post(f"{base}/confirm")
            assert confirmed.status_code == 409

    @pytest.mark.integration
    def test_quarantine_cleared_between_existence_check_and_read_is_not_reported_as_corrupted(
        self, tmp_path, monkeypatch
    ):
        """存在性检查通过之后、``read_quarantine`` 真正读取之前，晋升工具把隔离文件清掉了
        （正式内容已写入、隔离态合法结束）：这不是信封损坏，这次读跨越了「清除」那一刻，应
        按「无隔离草稿」处理，不能误报成损坏——那会让刚晋升完成的集看起来还卡在隔离态。"""
        from lib.reference_video.quarantine import QUARANTINE_KIND_STEP1, write_quarantine
        from server.services import script_review as mod

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        _write_rv_step1(pm, _rv_step1())
        quarantine_path = write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_STEP1,
            content={"units": [{"duration_seconds": 4, "source_text": "x", "text": "镜头1：门开了"}]},
            violations=[],
        )

        real_read_quarantine = mod.read_quarantine

        def _read_after_concurrent_clear(*args, **kwargs):
            # 模拟：本请求的存在性检查已经通过，但真正读取发生前，另一个请求（晋升工具）
            # 把文件清掉了。
            quarantine_path.unlink()
            return real_read_quarantine(*args, **kwargs)

        monkeypatch.setattr(mod, "read_quarantine", _read_after_concurrent_clear)

        with client:
            body = client.get("/api/v1/projects/demo/episodes/1/script-review").json()
            assert body["quarantine"] is None

    @pytest.mark.integration
    def test_quarantine_unreadable_message_localized_by_accept_language(self, tmp_path, monkeypatch):
        """``quarantine_unreadable`` 违约的 message 走 ``_t`` 按 ``Accept-Language`` 本地化。

        其它违约 code 的 message 是产出时渲染好插值的中文模板，不做本地化；``quarantine_unreadable``
        是仅有的两处不带插值的固定字符串（隔离草稿信封损坏 / 重算所需的 meta 缺失损坏），本地化
        改造范围就锁定在这两条。"""
        from lib import script_review as lib_script_review

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        _write_rv_step1(pm, _rv_step1())

        quarantine_path = lib_script_review.step1_quarantine_path(project_path, pm.load_project("demo"), 1)
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        quarantine_path.write_text("{ not valid json", encoding="utf-8")

        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            body = client.get(base, headers={"Accept-Language": "en"}).json()
            message = body["quarantine"]["violations"][0]["message"]
            assert message == (
                "The quarantined draft file is corrupted or malformed and can't be read; "
                "ask the agent to re-split this episode"
            )

    @pytest.mark.integration
    def test_duration_tiers_survive_save_and_confirm_responses(self, tmp_path, monkeypatch):
        """PUT / confirm 的响应同样带 ``duration_tiers``——它们各自独立调用 ``get_state``，
        不经过 GET 那次合并；不带的话前端 ``adopt()`` 用保存后的响应覆盖 GET 读到的收窄结果，
        退回未收窄的 ``supported_durations``，与刚加载时的呈现不一致。"""
        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        with client:
            _write_rv_step1(pm, _rv_step1())
            base = "/api/v1/projects/demo/episodes/1/script-review"
            get_body = client.get(base).json()
            assert "duration_tiers" in get_body

            put_body = client.put(f"{base}/content", json=_rv_step1()).json()
            assert "duration_tiers" in put_body

            confirm_body = client.post(f"{base}/confirm").json()
            assert "duration_tiers" in confirm_body

    @pytest.mark.integration
    def test_duration_tiers_and_supported_durations_resolved_for_custom_provider(self, tmp_path, monkeypatch):
        """自定义供应商（``custom-`` 前缀）不在 ``PROVIDER_REGISTRY``：caps 是它唯一的档位来源。

        ``supported_durations``（未收窄全集，供存量草稿的读时收编 clamp）与 ``duration_tiers``
        （收窄后的逐 unit 可选项）都要经 caps 解析出真实档位，否则这类项目的审阅门只能退回
        结构区间 clamp，读时迁移的收编对其整体失效。
        """
        from server.agent_runtime.sdk_tools import _context
        from server.services import script_review as mod

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")

        async def _fake_caps(_project, _episode=None):
            return {"provider_id": "custom-acme", "model": "acme-video", "supported_durations": [5, 10]}

        monkeypatch.setattr(mod, "resolve_video_caps", _fake_caps)

        async def _no_i2v(_project, *, capability=None):
            raise ValueError("i2v bucket unresolvable in this test")

        monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)

        with client:
            _write_rv_step1(pm, _rv_step1())
            body = client.get("/api/v1/projects/demo/episodes/1/script-review").json()
            assert body["supported_durations"] == [5, 10]
            assert body["duration_tiers"] == {"with_references": [5, 10], "without_references": [5, 10]}

    @pytest.mark.integration
    def test_quarantine_with_non_string_meta_source_degrades_gracefully(self, tmp_path, monkeypatch):
        """隔离草稿信封本身合法，但 ``meta.source`` 被改成非字符串（如数字）：重算链路要把它当作
        「无法重算」降级，而不是让 ``safe_join`` 内部的 ``TypeError`` 冒穿成未处理的 500——那样
        用户在最需要看到面板给出修复指引的时刻，看到的反而是一个空白错误页。"""
        from lib.reference_video.quarantine import QUARANTINE_KIND_STEP1, write_quarantine

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_STEP1,
            content={"units": [{"duration_seconds": 4, "source_text": "x", "text": "镜头1：门开了"}]},
            violations=[],
            meta={"source": 12345},
        )

        with client:
            resp = client.get("/api/v1/projects/demo/episodes/1/script-review")
            assert resp.status_code == 200
            violations = resp.json()["quarantine"]["violations"]
            assert [v["code"] for v in violations] == ["quarantine_unreadable"]

    @pytest.mark.integration
    def test_quarantine_with_directory_valued_meta_source_degrades_gracefully(self, tmp_path, monkeypatch):
        """``meta.source`` 类型正确（字符串）但指向一个目录：``Path.exists()`` 对目录同样为
        True，直接 ``read_text()`` 会抛 ``IsADirectoryError``——同样要降级成 quarantine_unreadable，
        不能让这个既不是 ValueError 也不是类型错误的 OSError 子类冒穿成 500。"""
        from lib.reference_video.quarantine import QUARANTINE_KIND_STEP1, write_quarantine

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        (project_path / "source").mkdir(parents=True, exist_ok=True)
        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_STEP1,
            content={"units": [{"duration_seconds": 4, "source_text": "x", "text": "镜头1：门开了"}]},
            violations=[],
            meta={"source": "source"},
        )

        with client:
            resp = client.get("/api/v1/projects/demo/episodes/1/script-review")
            assert resp.status_code == 200
            violations = resp.json()["quarantine"]["violations"]
            assert [v["code"] for v in violations] == ["quarantine_unreadable"]

    @pytest.mark.integration
    def test_put_response_includes_quarantine_created_during_the_request(self, tmp_path, monkeypatch):
        """保存作用于正式草稿，隔离草稿是另一份文件——PUT 响应缺 ``quarantine`` 字段的话，
        面板 ``adopt()`` 会把它当成「无隔离草稿」而放行确认，即使这份隔离草稿在保存前后一直
        都在（这里用「保存时隔离草稿已存在」模拟，等价于「保存在途时才产出」的时序）。"""
        from lib.reference_video.draft_validation import DraftViolation
        from lib.reference_video.quarantine import QUARANTINE_KIND_STEP1, write_quarantine

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        (project_path / "source").mkdir(parents=True, exist_ok=True)
        (project_path / "source" / "episode_1.txt").write_text("阿离站在屋檐下。", encoding="utf-8")
        _write_rv_step1(pm, _rv_step1())
        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_STEP1,
            content={"units": [{"duration_seconds": 4, "source_text": "x", "text": "镜头1：门开了"}]},
            violations=[DraftViolation("坏", code="empty_text", label="unit E1U01")],
            meta={"source": "source/episode_1.txt"},
        )

        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            put_body = client.put(f"{base}/content", json=_rv_step1()).json()
            assert put_body["quarantine"] is not None
            # meta.source 完整、重算能正常跑：断言到的是重算算出的真实违约，不是
            # meta 缺失时降级出的 quarantine_unreadable 兜底条目。
            codes = [v["code"] for v in put_body["quarantine"]["violations"]]
            assert codes and "quarantine_unreadable" not in codes

    @pytest.mark.unit
    def test_put_with_stale_base_fingerprint_conflicts_409(self, tmp_path, monkeypatch):
        """PUT 携带的 ``base_fingerprint`` 与盘上现值不一致（编辑期间另一方已保存）→ 409、
        不落盘；拿最新指纹重试放行。缺省不带指纹的调用维持原语义（不比对）。"""
        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        _write_rv_step1(pm, _rv_step1())

        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            stale = client.get(base).json()["fingerprint"]

            other = _rv_step1()
            other["units"][0]["shots"][0]["text"] = "@[阿离] 转身离开。"
            assert client.put(f"{base}/content", json=other).status_code == 200

            mine = _rv_step1()
            resp = client.put(f"{base}/content", params={"base_fingerprint": stale}, json=mine)
            assert resp.status_code == 409
            # 冲突未覆盖：盘上仍是另一方保存的内容
            assert client.get(base).json()["content"]["units"][0]["shots"][0]["text"] == "@[阿离] 转身离开。"

            fresh = client.get(base).json()["fingerprint"]
            resp = client.put(f"{base}/content", params={"base_fingerprint": fresh}, json=mine)
            assert resp.status_code == 200
            assert resp.json()["content"]["units"][0]["shots"][0]["text"] == "@[阿离] 立于屋檐下。"
