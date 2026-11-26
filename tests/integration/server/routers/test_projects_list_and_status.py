"""Tests for projects_list_and_status."""

import asyncio
import threading

import httpx
import pytest

from lib.i18n import _
from server.dependencies import project_reads
from server.services.project.coalesce import RequestCoalescer
from tests.integration.server.routers.projects_router_support import (
    _FakePM,
    _FakeSummaries,
    build_projects_client,
    override,
)


class _ArrivalSpy(RequestCoalescer):
    """登记每个已挂上合流的请求，测试据此确认请求已到达，再放行计算。"""

    def __init__(self):
        super().__init__()
        self.arrivals: asyncio.Queue[None] = asyncio.Queue()
        self.keys = []

    async def run(self, key, compute):
        self.keys.append(key)
        self.arrivals.put_nowait(None)
        return await super().run(key, compute)


class _GatedSummaries(_FakeSummaries):
    """第一次摘要计算停在闸门前；修复原因引用剧本文件，呈现时按请求语言成文。"""

    def __init__(self):
        super().__init__()
        self._loop = asyncio.get_running_loop()
        self.started = asyncio.Event()
        self.gate = threading.Event()

    def get_project_summary(self, name, **kwargs):
        self._loop.call_soon_threadsafe(self.started.set)
        self.gate.wait()
        summary = super().get_project_summary(name, currency=kwargs.get("currency", "verified"))
        return summary.model_copy(update={"needs_repair": True, "repair_reason": "scripts/episode_1.json"})


async def _until(awaitable, *requests):
    """等到 awaitable 完成；任一请求先结束说明路由提前返回，直接失败而不是挂住。"""

    step = asyncio.ensure_future(awaitable)
    done, _pending = await asyncio.wait({step, *requests}, return_when=asyncio.FIRST_COMPLETED)
    if step not in done:
        step.cancel()
        raise AssertionError(f"request finished early: {[r.result().status_code for r in done]}")


class TestProjectsRouter:
    def test_list_projects_shares_script_preload_with_status(self, tmp_path, monkeypatch):
        """list_projects 一次性加载 episode scripts，传给项目摘要投影，去除 cover + status 双重 I/O。"""
        fake_pm = _FakePM(tmp_path)
        orig_load_script = fake_pm.load_script
        calls: list[tuple[str, str]] = []

        def _counting_load(name, script_file):
            calls.append((name, script_file))
            return orig_load_script(name, script_file)

        fake_pm.load_script = _counting_load

        fake_summaries = _FakeSummaries()
        client = build_projects_client(monkeypatch, fake_pm, fake_summaries)
        with client:
            resp = client.get("/api/v1/projects")
            assert resp.status_code == 200

        ready_calls = [c for c in calls if c[0] == "ready"]
        assert len(ready_calls) == 1, f"expected 1 shared load, got {ready_calls}"

        assert fake_summaries.last_preloaded_scripts is not None
        assert "scripts/episode_1.json" in fake_summaries.last_preloaded_scripts
        assert set(fake_summaries.currencies) == {"registered"}

    def test_get_project_status_verifies_artifact_currency(self, tmp_path, monkeypatch):
        """单个项目的详情走完整比对口径：剧集卡要区分 current 与 stale。"""
        fake_summaries = _FakeSummaries()
        client = build_projects_client(monkeypatch, _FakePM(tmp_path), fake_summaries)
        with client:
            resp = client.get("/api/v1/projects/ready")

        assert resp.status_code == 200
        assert fake_summaries.currencies == ["verified"]

    def test_list_projects_status_comes_from_the_project_summary(self, tmp_path, monkeypatch):
        """列表页的计数一律来自项目摘要：项目阶段已取消，phase 与 current_phase 都不在。"""
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get("/api/v1/projects")

        assert resp.status_code == 200
        status = next(item for item in resp.json()["projects"] if item["name"] == "ready")["status"]
        assert status["episodes_summary"]["total"] == 1
        assert "phase" not in status
        assert "current_phase" not in status
        assert status["assets"]["character"] == {"total": 1, "available": 0, "stale": 0}
        assert "episodes" not in status

    def test_get_project_status_comes_from_the_project_summary(self, tmp_path, monkeypatch):
        """全局头读的项目级状态与列表同源。"""
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get("/api/v1/projects/ready")

        assert resp.status_code == 200
        status = resp.json()["project"]["status"]
        assert status["episodes_summary"]["completed"] == 0
        assert "phase" not in status
        assert status["needs_repair"] is False
        assert "current_phase" not in status

    def test_get_project_episode_fields_come_from_the_project_summary(self, tmp_path, monkeypatch):
        """剧集卡 / 剧集头读的每集字段由项目摘要提供，project.json 侧字段原样保留。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["episodes"] = [
            {"episode": 1, "title": "第一集", "script_file": "scripts/ep1.json"},
        ]
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.get("/api/v1/projects/ready")

        assert resp.status_code == 200
        episode = resp.json()["project"]["episodes"][0]
        assert (episode["title"], episode["script_file"]) == ("第一集", "scripts/ep1.json")
        assert episode["script_status"] == "generated"
        assert episode["status"] == "in_production"
        assert episode["item_count"] == 1
        assert "scenes_count" not in episode
        assert episode["duration_seconds"] == 8
        assert episode["storyboards"] == {"total": 1, "available": 1, "stale": 0}
        assert episode["videos"] == {"total": 1, "available": 0, "stale": 0}
        assert "episodes" not in resp.json()["project"]["status"]

    def test_get_project_scripts_carry_no_derived_totals(self, tmp_path, monkeypatch):
        """剧本响应不再注入条目数 / 总时长 / 出场名单：这些派生值只有项目摘要一个来源。"""
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get("/api/v1/projects/ready")

        assert resp.status_code == 200
        script = next(iter(resp.json()["scripts"].values()))
        assert "duration_seconds" not in script
        assert "characters_in_episode" not in script
        assert "total_scenes" not in script.get("metadata", {})
        assert "estimated_duration_seconds" not in script.get("metadata", {})

    def test_list_projects_returns_style_image_field(self, tmp_path, monkeypatch):
        """列表端点需返回 style_image：否则前端无法区分"自定义风格"与"未设置"。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["style_image"] = "style_reference.png"
        fake_pm.project_data["ready"].pop("style_template_id", None)
        fake_pm.project_data["ready"]["style"] = ""

        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.get("/api/v1/projects")
            assert resp.status_code == 200
            ready = next(p for p in resp.json()["projects"] if p["name"] == "ready")
            assert ready["style_image"] == "style_reference.png"
            assert ready.get("style_template_id") is None

    def test_get_project_includes_asset_fingerprints(self, tmp_path, monkeypatch):
        """项目 API 应返回 asset_fingerprints 字段"""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            resp = client.get("/api/v1/projects/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert "asset_fingerprints" in data
            assert "storyboards/scene_E1S01.png" in data["asset_fingerprints"]
            assert isinstance(data["asset_fingerprints"]["storyboards/scene_E1S01.png"], int)

    @pytest.mark.parametrize("suffix", ["", "/workspace"])
    async def test_get_project_does_not_share_results_across_languages(self, tmp_path, monkeypatch, suffix):
        """合流按语言分开：中文计算在跑、中文请求已占等待位时到达的英文请求，拿到英文文案。"""
        summaries = _GatedSummaries()
        spy = _ArrivalSpy()
        sync_client = build_projects_client(monkeypatch, _FakePM(tmp_path), summaries)
        override(sync_client, project_reads, lambda: spy)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=sync_client.app), base_url="http://test"
        ) as client:

            def get(locale):
                return asyncio.create_task(
                    client.get(f"/api/v1/projects/ready{suffix}", headers={"Accept-Language": locale})
                )

            running = get("zh")
            try:
                await _until(summaries.started.wait(), running)
                waiting, english = get("zh"), get("en")
                for _arrival in range(3):
                    await _until(spy.arrivals.get(), running, waiting, english)
            finally:
                # 断言失败时也放行闸门，不留阻塞的工作线程。
                summaries.gate.set()
            responses = await asyncio.gather(running, waiting, english)

        def expected(locale):
            episode = _("episode_position_name", locale=locale, position=1)
            return _("episode_script_name", locale=locale, name=episode)

        payloads = [r.json() for r in responses]
        reasons = [(p["summary"] if suffix else p["project"]["status"])["repair_reason"] for p in payloads]
        assert reasons == [expected("zh"), expected("zh"), expected("en")]


def test_workspace_registered_counts_and_legacy_script_reuse(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from lib.artifacts.artifact_planner import TargetStatePlanner
    from lib.workflow.workflow_state import WorkflowStateService
    from server.routers import projects
    from tests.integration.lib.artifacts.test_content_digest_currency import _count_content_reads
    from tests.integration.lib.project.test_project_summary import (
        _make_project,
        _plan_one_episode,
        _valid_video_unit,
        _write_registered_script,
        _write_source,
    )

    manager, project_dir = _make_project(tmp_path, "ad", generation_mode="reference_video")
    _write_source(manager, project_dir, "原文")
    _plan_one_episode(manager, project_dir, "原文")
    _write_registered_script(
        project_dir,
        {
            "episode": 1,
            "title": "Test",
            "content_mode": "ad",
            "video_units": [_valid_video_unit(text="", duration_seconds=0, needs_replan=True)],
        },
    )
    with build_projects_client(monkeypatch, manager, WorkflowStateService(manager)) as client:
        reads = _ArrivalSpy()
        override(client, project_reads, lambda: reads)
        with monkeypatch.context() as guards:
            for owner, method in [(TargetStatePlanner, "__init__"), (projects, "annotate_derivative_references")]:
                guards.setattr(owner, method, Mock(side_effect=AssertionError(method)))
            load = Mock(wraps=manager.load_project)
            guards.setattr(manager, "load_project", load)
            response = client.get("/api/v1/projects/demo/workspace")
            assert load.call_count == 1
        data = response.json()
        assert response.status_code == 200
        assert set(data) == {"project", "summary", "asset_fingerprints"}
        assert '"video_units":' not in response.text
        assert '"stale":' not in response.text
        row = data["summary"]["episodes"][0]
        assert data["summary"]["currency"] == "registered"
        assert (row["needs_replan"], row["videos"], row["status"]) == (1, {"total": 1, "available": 0}, "scripted")
        manager.update_project(
            "demo",
            lambda project: project["episodes"].append(
                {**project["episodes"][0], "episode": 2, "script_file": "scripts/other.json"}
            ),
        )
        _write_registered_script(
            project_dir,
            {**manager.load_script("demo", "episode_1.json"), "episode": 2},
            episode=2,
            filename="other.json",
        )
        with monkeypatch.context() as guards:
            read_script = Mock(wraps=manager.load_script_readonly)
            guards.setattr(manager, "load_script_readonly", read_script)
            assert client.get("/api/v1/projects/demo/workspace").status_code == 200
            assert any(call.args[1] == "scripts/other.json" for call in read_script.call_args_list)
            read_script.reset_mock()
            scoped = client.get("/api/v1/projects/demo/workspace", params={"episodes": 1})
            assert scoped.status_code == 200
            partial = scoped.json()["summary"]
            assert partial["episodes_summary"] is None
            assert partial["episodes"] == [row]
            assert reads.keys[-2] != reads.keys[-1]
            assert all("other.json" not in str(call) for call in read_script.call_args_list)
            for selection in ([1, 1], [0], [3]):
                assert client.get("/api/v1/projects/demo/workspace", params={"episodes": selection}).status_code == 400
            for malformed in ("", "1,1", "abc"):
                assert client.get("/api/v1/projects/demo/workspace", params={"episodes": malformed}).status_code == 422
        script_reads = [
            _count_content_reads(monkeypatch, project_dir / "scripts" / name)
            for name in ("episode_1.json", "other.json")
        ]
        assert client.get("/api/v1/projects/demo").status_code == 200
        assert [counter["content_reads"] for counter in script_reads] == [1, 1]
