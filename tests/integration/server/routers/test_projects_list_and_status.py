"""Tests for projects_list_and_status."""

import asyncio
import threading

import httpx

from lib.i18n import _
from server.routers import projects
from server.routers._coalesce import RequestCoalescer
from tests.integration.server.routers.projects_router_support import (
    _FakePM,
    _FakeSummaries,
    build_projects_client,
    override,
)


class _ArrivalSpy(RequestCoalescer):
    """Record coalesced requests so tests verify arrival before releasing computation."""

    def __init__(self):
        super().__init__()
        self.arrivals: asyncio.Queue[None] = asyncio.Queue()

    async def run(self, key, compute):
        self.arrivals.put_nowait(None)
        return await super().run(key, compute)


class _GatedSummaries(_FakeSummaries):
    """First summaries wait at gates; repair reasons reference scripts and render in request languages."""

    def __init__(self):
        super().__init__()
        self._loop = asyncio.get_running_loop()
        self.started = asyncio.Event()
        self.gate = threading.Event()

    def get_project_summary(self, name, **kwargs):
        self._loop.call_soon_threadsafe(self.started.set)
        self.gate.wait()
        summary = super().get_project_summary(name, **kwargs)
        return summary.model_copy(update={"needs_repair": True, "repair_reason": "scripts/episode_1.json"})


async def _until(awaitable, *requests):
    """Wait for awaitables; any prematurely finished request fails immediately instead of hanging."""

    step = asyncio.ensure_future(awaitable)
    done, _pending = await asyncio.wait({step, *requests}, return_when=asyncio.FIRST_COMPLETED)
    if step not in done:
        step.cancel()
        raise AssertionError(f"request finished early: {[r.result().status_code for r in done]}")


class TestProjectsRouter:
    def test_list_projects_shares_script_preload_with_status(self, tmp_path, monkeypatch):
        """list_projects preloads episode scripts once for summaries, removing duplicate cover/status IO."""
        fake_pm = _FakePM(tmp_path)
        # Count load_script; shared preloading should load ready once.
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

        # ready has one episode script_file="scripts/episode_1.json", preloaded once.
        # Separate cover/status loads would count twice.
        ready_calls = [c for c in calls if c[0] == "ready"]
        assert len(ready_calls) == 1, f"expected 1 shared load, got {ready_calls}"

        # Pass preloaded maps into summary projections.
        assert fake_summaries.last_preloaded_scripts is not None
        assert "scripts/episode_1.json" in fake_summaries.last_preloaded_scripts
        # Lists check claims/file existence only, without per-artifact canonical comparisons.
        assert set(fake_summaries.currencies) == {"registered"}

    def test_get_project_status_verifies_artifact_currency(self, tmp_path, monkeypatch):
        """Individual project details fully compare artifacts, distinguishing current/stale episode cards."""
        fake_summaries = _FakeSummaries()
        client = build_projects_client(monkeypatch, _FakePM(tmp_path), fake_summaries)
        with client:
            resp = client.get("/api/v1/projects/ready")

        assert resp.status_code == 200
        assert fake_summaries.currencies == ["verified"]

    def test_list_projects_status_comes_from_the_project_summary(self, tmp_path, monkeypatch):
        """List counts come solely from summaries; retired stages omit phase/current_phase."""
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get("/api/v1/projects")

        assert resp.status_code == 200
        status = next(item for item in resp.json()["projects"] if item["name"] == "ready")["status"]
        assert status["episodes_summary"]["total"] == 1
        assert "phase" not in status
        assert "current_phase" not in status
        assert status["assets"]["character"] == {"total": 1, "available": 0, "stale": 0}
        # Episode details belong to project-detail endpoints, not N-project lists.
        assert "episodes" not in status

    def test_get_project_status_comes_from_the_project_summary(self, tmp_path, monkeypatch):
        """Global header project states share list truth."""
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
        """Episode cards/headers get episode fields from summaries while preserving project.json fields."""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["episodes"] = [
            {"episode": 1, "title": "第一集", "script_file": "scripts/ep1.json"},
        ]
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.get("/api/v1/projects/ready")

        assert resp.status_code == 200
        episode = resp.json()["project"]["episodes"][0]
        # Retain project.json fields unchanged.
        assert (episode["title"], episode["script_file"]) == ("第一集", "scripts/ep1.json")
        # Summary fields use manifests: available = current ∪ stale, stale counted separately.
        assert episode["script_status"] == "generated"
        assert episode["status"] == "in_production"
        assert episode["item_count"] == 1
        # Responses exclude retired aggregate fields.
        assert "scenes_count" not in episode
        assert episode["duration_seconds"] == 8
        assert episode["storyboards"] == {"total": 1, "available": 1, "stale": 0}
        assert episode["videos"] == {"total": 1, "available": 0, "stale": 0}
        # Episode details appear in episodes[] only, never duplicated under project status.
        assert "episodes" not in resp.json()["project"]["status"]

    def test_get_project_scripts_carry_no_derived_totals(self, tmp_path, monkeypatch):
        """Script responses no longer inject counts/durations/appearance lists; summaries alone derive them."""
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
        """Lists return style_image, distinguishing custom styles from unset styles."""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["style_image"] = "style_reference.png"
        # Custom images require empty style_template_id by mutual exclusion.
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
        """Project APIs return asset_fingerprints"""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            resp = client.get("/api/v1/projects/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert "asset_fingerprints" in data
            assert "storyboards/scene_E1S01.png" in data["asset_fingerprints"]
            assert isinstance(data["asset_fingerprints"]["storyboards/scene_E1S01.png"], int)

    # ---------------------------------------------------------------------------

    async def test_get_project_does_not_share_results_across_languages(self, tmp_path, monkeypatch):
        """Coalesce separately by language: English requests arriving while Chinese computes/has waiters still receive English."""
        summaries = _GatedSummaries()
        spy = _ArrivalSpy()
        sync_client = build_projects_client(monkeypatch, _FakePM(tmp_path), summaries)
        override(sync_client, projects.get_project_detail_coalescer, lambda: spy)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=sync_client.app), base_url="http://test"
        ) as client:

            def get(locale):
                return asyncio.create_task(client.get("/api/v1/projects/ready", headers={"Accept-Language": locale}))

            running = get("zh")
            try:
                await _until(summaries.started.wait(), running)
                waiting, english = get("zh"), get("en")
                for _arrival in range(3):
                    await _until(spy.arrivals.get(), running, waiting, english)
            finally:
                # Release gates even after assertion failures, leaving no blocked workers.
                summaries.gate.set()
            responses = await asyncio.gather(running, waiting, english)

        def expected(locale):
            episode = _("episode_position_name", locale=locale, position=1)
            return _("episode_script_name", locale=locale, name=episode)

        reasons = [r.json()["project"]["status"]["repair_reason"] for r in responses]
        assert reasons == [expected("zh"), expected("zh"), expected("en")]
