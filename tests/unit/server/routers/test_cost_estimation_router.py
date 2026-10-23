"""Tests for cost estimation router."""

import asyncio
import gc
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import cost_estimation
from server.routers._coalesce import RequestCoalescer
from tests.auth_deps import AUTH_DEPENDENCIES


def _make_app():
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(cost_estimation.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return app


def _mock_pm(**overrides):
    """Create a mock to replace the ``get_project_manager()`` singleton getter."""
    mock = MagicMock()
    for k, v in overrides.items():
        setattr(mock, k, MagicMock(return_value=v))
    return mock


class TestCostEstimationRouter:
    def test_project_not_found_returns_404(self):
        with (
            patch.object(cost_estimation, "get_project_manager", lambda: _mock_pm(project_exists=False)),
            TestClient(_make_app()) as client,
        ):
            resp = client.get("/api/v1/projects/nonexistent/cost-estimate")
        assert resp.status_code == 404
        assert "不存在" in resp.json()["detail"]

    def test_success_returns_correct_structure(self):
        fake_result = {
            "project_name": "demo",
            "models": {
                "image": {"provider": "gemini", "model": "m"},
                "video": {"provider": "gemini", "model": "m"},
            },
            "episodes": [],
            "project_totals": {"estimate": {}, "actual": {}},
        }

        mock_pm = _mock_pm(project_exists=True, load_project={"episodes": []})

        with (
            patch.object(cost_estimation, "get_project_manager", lambda: mock_pm),
            patch.object(cost_estimation, "CostEstimationService") as MockService,
        ):
            MockService.return_value.compute = AsyncMock(return_value=fake_result)

            with TestClient(_make_app()) as client:
                resp = client.get("/api/v1/projects/demo/cost-estimate")

        assert resp.status_code == 200
        body = resp.json()
        assert body["project_name"] == "demo"
        assert "models" in body
        assert "episodes" in body
        assert "project_totals" in body

    @pytest.mark.parametrize(
        ("params", "rejected"),
        [
            (
                {"reference_unit_id": "E1U1", "narration_delivery": "use_tts"},
                ["narration_delivery", "reference_unit_id"],
            ),
            ({"narration_delivery": "post_production"}, ["narration_delivery"]),
            ({"reference_unit_id": "missing"}, ["reference_unit_id"]),
        ],
    )
    def test_rejects_the_retired_query_params(self, params, rejected):
        """旁白交付方式与单元参数都不参与估价：旧客户端仍带着它们查询时拒收，不计算估价。"""
        mock_pm = _mock_pm(
            project_exists=True,
            load_project={"episodes": [{"script_file": "ep1.json"}]},
            load_script={"video_units": [{"unit_id": "E1U1"}]},
        )

        with (
            patch.object(cost_estimation, "get_project_manager", lambda: mock_pm),
            patch.object(cost_estimation, "CostEstimationService") as mock_service,
        ):
            mock_service.return_value.compute = AsyncMock(return_value={})
            with TestClient(_make_app()) as client:
                response = client.get("/api/v1/projects/demo/cost-estimate", params=params)

        assert response.status_code == 422, response.text
        assert [(item["type"], item["loc"]) for item in response.json()["detail"]] == [
            ("extra_forbidden", ["query", name]) for name in rejected
        ]
        mock_service.return_value.compute.assert_not_awaited()

    def test_no_auth_returns_401(self, monkeypatch):
        # AUTH_ENABLED=false 时 get_current_user 直接返回匿名 admin，这里就测不到拒绝。
        monkeypatch.setenv("AUTH_ENABLED", "true")
        app = FastAPI()
        register_error_handlers(app)
        # Do NOT override the auth dependency — real auth should reject.
        # 认证依赖挂在注册处，这里须与 server/app.py 的挂法一致。
        app.include_router(cost_estimation.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/v1/projects/demo/cost-estimate")
        assert resp.status_code == 401


class _ArrivalSpy(RequestCoalescer):
    """登记每个已挂上合流的请求，测试据此确认请求已到达，再放行计算。"""

    def __init__(self):
        super().__init__()
        self.arrivals: asyncio.Queue[None] = asyncio.Queue()

    async def run(self, key, compute):
        self.arrivals.put_nowait(None)
        return await super().run(key, compute)

    async def wait_arrivals(self, count):
        for _ in range(count):
            await self.arrivals.get()


class _GatedEstimates:
    """费用估算替身：开算时记下输入版本，等测试开闸后返回该版本；版本为 boom 时出错。"""

    def __init__(self):
        self.gate = asyncio.Event()
        self.started: asyncio.Queue[str] = asyncio.Queue()
        self.finished: list[str] = []
        self.active = 0
        self.peak = 0

    async def compute(self, project_data, scripts, *, project_name):
        version = project_data["version"]
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.put_nowait(version)
        try:
            await self.gate.wait()
            if version == "boom":
                raise RuntimeError("boom")
            self.finished.append(version)
            return {"version": version}
        finally:
            self.active -= 1


class TestCostEstimationCoalescing:
    """同一项目的并发估算请求合流：同时最多 1 份在算、1 份等待，结果不早于请求到达时刻。"""

    @pytest.fixture
    def harness(self):
        inputs = {"version": "v1", "episodes": []}
        pm = _mock_pm(project_exists=True)
        pm.load_project = MagicMock(side_effect=lambda _name: dict(inputs))
        estimates = _GatedEstimates()
        spy = _ArrivalSpy()
        with (
            patch.object(cost_estimation, "get_project_manager", lambda: pm),
            patch.object(cost_estimation, "CostEstimationService") as service,
        ):
            service.return_value.compute = estimates.compute
            app = _make_app()
            app.dependency_overrides[cost_estimation.get_cost_estimate_coalescer] = lambda: spy
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
            yield inputs, estimates, spy, client

    @staticmethod
    def _get(client):
        return asyncio.create_task(client.get("/api/v1/projects/demo/cost-estimate"))

    async def test_concurrent_requests_run_at_most_two_computations(self, harness):
        _inputs, estimates, spy, client = harness
        async with client:
            first = self._get(client)
            await estimates.started.get()
            rest = [self._get(client) for _ in range(7)]
            await spy.wait_arrivals(8)
            estimates.gate.set()
            responses = await asyncio.gather(first, *rest)

        assert [r.status_code for r in responses] == [200] * 8
        assert estimates.peak <= 2
        assert len(estimates.finished) == 2

    async def test_request_arriving_mid_computation_sees_the_newer_input(self, harness):
        inputs, estimates, spy, client = harness
        async with client:
            first = self._get(client)
            await estimates.started.get()
            inputs["version"] = "v2"
            second = self._get(client)
            await spy.wait_arrivals(2)
            estimates.gate.set()
            first_resp, second_resp = await asyncio.gather(first, second)

        assert first_resp.json() == {"version": "v1"}
        assert second_resp.json() == {"version": "v2"}

    async def test_failed_computation_fails_every_sharer_and_next_request_recomputes(self, harness):
        inputs, estimates, spy, client = harness
        async with client:
            first = self._get(client)
            await estimates.started.get()
            inputs["version"] = "boom"
            sharers = [self._get(client) for _ in range(2)]
            await spy.wait_arrivals(3)
            estimates.gate.set()
            first_resp, *sharer_resps = await asyncio.gather(first, *sharers)
            inputs["version"] = "v3"
            retry = await client.get("/api/v1/projects/demo/cost-estimate")

        assert first_resp.status_code == 200
        assert [(r.status_code, r.json()["detail"]) for r in sharer_resps] == [(500, "费用估算失败，请稍后重试")] * 2
        assert retry.json() == {"version": "v3"}

    async def test_abandoned_requests_do_not_cancel_the_shared_computation(self, harness):
        inputs, estimates, spy, client = harness
        async with client:
            running = self._get(client)
            await estimates.started.get()
            inputs["version"] = "v2"
            abandoned = self._get(client)
            kept = self._get(client)
            await spy.wait_arrivals(3)
            for task in (running, abandoned):
                task.cancel()
            await asyncio.gather(running, abandoned, return_exceptions=True)
            estimates.gate.set()
            kept_resp = await kept

        assert kept_resp.json() == {"version": "v2"}
        assert estimates.finished == ["v1", "v2"]

    async def test_failure_after_every_request_disconnected_logs_no_unretrieved_exception(self, caplog):
        coalescer = RequestCoalescer()
        gate, started = asyncio.Event(), asyncio.Event()

        async def boom():
            started.set()
            await gate.wait()
            raise RuntimeError("boom")

        waiter = asyncio.create_task(coalescer.run("k", boom))
        await started.wait()
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        (task,) = coalescer._running.values()
        gate.set()
        await asyncio.wait([task])
        del task, waiter
        gc.collect()

        assert "never retrieved" not in caplog.text
        assert await coalescer.run("k", AsyncMock(return_value="again")) == "again"
