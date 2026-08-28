"""端点测试 API：预览请求、验证响应、测试连接三个端点。

三者共用保存接口那一个校验器，因此错误码集一致——本文件用同一份非法定义打四个入口来锁住这一点。
"""

from __future__ import annotations

import json
import time
from collections.abc import Generator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.config.resolver import ConfigResolver
from lib.custom_provider import make_endpoint_key, make_provider_id
from lib.custom_provider.endpoint_test import TrialRunManager
from lib.db import get_async_session
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.ledger import Ledger
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import custom_endpoints
from server.routers.endpoint_tests import get_config_resolver, get_trial_run_manager
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.factories import custom_endpoint_definition
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http

PARAMETERS = {"model": "video-x", "prompt": "纸船顺流而下", "duration_seconds": 5}
INLINE_CREDENTIALS = {"base_url": "https://relay.test", "api_key": "sk-secret-key-1234"}


@pytest.fixture()
def trial_runs(tmp_path, db_engine) -> TrialRunManager:
    """隔离到 tmp_path 与内存库的登记处，经依赖覆盖注入。"""
    return TrialRunManager(
        root=tmp_path / "trial_runs",
        ledger=Ledger(session_factory=async_sessionmaker(db_engine, expire_on_commit=False)),
        read_poll_timeout=_fixed_timeout,
    )


@pytest.fixture()
def endpoint_tests_app(db_engine, trial_runs: TrialRunManager) -> FastAPI:
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    app = FastAPI()

    async def _override_session():
        async with session_factory() as db_session:
            yield db_session

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[get_trial_run_manager] = lambda: trial_runs
    app.dependency_overrides[get_config_resolver] = lambda: ConfigResolver(session_factory)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="test", sub="test", role="admin")
    app.include_router(custom_endpoints.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    return app


@pytest.fixture()
def client(endpoint_tests_app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(endpoint_tests_app) as test_client:
        yield test_client


async def _fixed_timeout() -> int:
    return 600


def _mock_successful_run(router) -> None:
    router.post("https://relay.test/v1/video/create").mock(return_value=httpx.Response(200, json={"task_id": "job-42"}))
    router.get("https://relay.test/v1/video/fetch/job-42").mock(
        return_value=httpx.Response(
            200, json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"}
        )
    )
    router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))


def _post(client: TestClient, path: str, payload: dict[str, Any]):
    return client.post(f"/api/v1/custom-endpoints/{path}", json=payload)


class TestPreviewRequest:
    def test_returns_the_rendered_submit_and_poll_requests(self, client: TestClient):
        resp = _post(
            client,
            "preview-request",
            {
                "definition": custom_endpoint_definition(),
                "parameters": PARAMETERS,
                "credentials": INLINE_CREDENTIALS,
            },
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["submit"]["url"] == "https://relay.test/v1/video/create"
        assert body["submit"]["headers"]["Authorization"] == "Bearer ****1234"
        assert body["poll"]["url"].endswith("{{ task_id }}")
        assert body["result"] is None

    def test_reads_credentials_from_a_stored_provider(self, client: TestClient, stored_provider):
        resp = _post(
            client,
            "preview-request",
            {
                "definition": custom_endpoint_definition(),
                "parameters": PARAMETERS,
                "credentials": {"provider_id": stored_provider["provider_id"]},
            },
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["submit"]["url"].startswith("https://api.example.com/")

    def test_an_uploaded_asset_becomes_a_size_summary(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-endpoints/preview-request",
            data={
                "payload": json.dumps(
                    {
                        "definition": custom_endpoint_definition(),
                        "parameters": PARAMETERS,
                        "credentials": INLINE_CREDENTIALS,
                    }
                )
            },
            files={"start_image": ("frame.png", b"x" * 1024, "image/png")},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["submit"]["body"]["image"] == "<data:image/png;base64, 1024 bytes>"


class TestCheckResponse:
    def test_reports_hits_misses_and_the_mapped_status(self, client: TestClient):
        definition = custom_endpoint_definition()
        definition["poll"]["extract"]["video_url"] = ["$.data.url", "$.video_url"]

        resp = _post(
            client,
            "check-response",
            {
                "definition": definition,
                "stage": "poll",
                "response_body": {"status": "completed", "video_url": "https://cdn/v.mp4"},
            },
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "succeeded"
        video = next(field for field in body["fields"] if field["key"] == "video_url")
        assert [attempt["matched"] for attempt in video["attempts"]] == [False, True]

    def test_accepts_a_pasted_response_string(self, client: TestClient):
        resp = _post(
            client,
            "check-response",
            {"definition": custom_endpoint_definition(), "stage": "poll", "response_body": '{"status": "pending"}'},
        )

        assert resp.json()["status"] == "queued"


class TestSharedValidation:
    """非法定义在三个测试端点与保存接口报出同一错误码集。"""

    @staticmethod
    def _codes(payload: dict[str, Any]) -> set[str]:
        return {error["code"] for error in payload["diagnostic"]["errors"]}

    def test_the_same_definition_yields_the_same_codes_everywhere(self, client: TestClient):
        broken = custom_endpoint_definition()
        broken["submit"]["body"]["key"] = "{{ api_key }}"

        saved = client.post("/api/v1/custom-endpoints", json=broken)
        previewed = _post(
            client,
            "preview-request",
            {"definition": broken, "parameters": PARAMETERS, "credentials": INLINE_CREDENTIALS},
        )
        checked = _post(client, "check-response", {"definition": broken, "stage": "poll", "response_body": {}})
        tried = _post(
            client,
            "trial-runs",
            {"definition": broken, "parameters": PARAMETERS, "credentials": INLINE_CREDENTIALS},
        )

        assert [saved.status_code, previewed.status_code, checked.status_code, tried.status_code] == [422] * 4
        assert [self._codes(resp.json()) for resp in (saved, previewed, checked, tried)] == [
            {"api_key_outside_auth"}
        ] * 4

    def test_a_render_failure_reports_the_shared_diagnostic_shape(self, client: TestClient):
        definition = custom_endpoint_definition()
        definition["enum_maps"] = {"duration": {"10": 10}}

        resp = _post(
            client,
            "preview-request",
            {"definition": definition, "parameters": PARAMETERS, "credentials": INLINE_CREDENTIALS},
        )

        assert resp.status_code == 422
        assert resp.json()["diagnostic"]["errors"][0]["code"] == "template_render_failed"


class TestTrialRuns:
    def test_runs_to_a_terminal_state_and_serves_the_artifact(self, client: TestClient, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            created = _post(
                client,
                "trial-runs",
                {
                    "definition": custom_endpoint_definition(),
                    "parameters": PARAMETERS,
                    "credentials": INLINE_CREDENTIALS,
                },
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["id"]
            _drain(trial_runs, run_id)

        fetched = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}")
        assert fetched.json()["status"] == "succeeded"
        assert fetched.json()["request"]["url"] == "https://relay.test/v1/video/create"
        artifact = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}/artifact")
        assert artifact.status_code == 200
        assert artifact.content == b"video"

    def test_a_second_concurrent_run_is_refused(self, client: TestClient, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(200, json={"status": "processing"})
            )
            payload = {
                "definition": custom_endpoint_definition(),
                "parameters": PARAMETERS,
                "credentials": INLINE_CREDENTIALS,
            }
            first = _post(client, "trial-runs", payload)
            second = _post(client, "trial-runs", payload)

            assert first.status_code == 201
            assert second.status_code == 409

            run_id = first.json()["id"]
            cancelled = client.post(f"/api/v1/custom-endpoints/trial-runs/{run_id}/cancel")
            assert cancelled.status_code == 204
            # 取消后名额让出，同一份定义可以再发一次。
            assert _post(client, "trial-runs", payload).status_code == 201

    def test_a_cancelled_run_leaves_nothing_to_read(self, client: TestClient, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(200, json={"status": "processing"})
            )
            created = _post(
                client,
                "trial-runs",
                {
                    "definition": custom_endpoint_definition(),
                    "parameters": PARAMETERS,
                    "credentials": INLINE_CREDENTIALS,
                },
            )
            run_id = created.json()["id"]
            client.post(f"/api/v1/custom-endpoints/trial-runs/{run_id}/cancel")

        assert client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}").status_code == 404

    def test_requires_credentials(self, client: TestClient, trial_runs: TrialRunManager):
        resp = _post(client, "trial-runs", {"definition": custom_endpoint_definition(), "parameters": PARAMETERS})

        assert resp.status_code == 400

    def test_requires_a_definition_or_a_model_ref(self, client: TestClient, trial_runs: TrialRunManager):
        resp = _post(client, "trial-runs", {"parameters": PARAMETERS, "credentials": INLINE_CREDENTIALS})

        assert resp.status_code == 400

    def test_a_model_ref_to_an_unknown_provider_is_not_found(self, client: TestClient, trial_runs: TrialRunManager):
        resp = _post(
            client,
            "trial-runs",
            {"model_ref": {"provider_id": "custom-999", "model_id": "video-x"}, "parameters": PARAMETERS},
        )

        assert resp.status_code == 404

    def test_a_model_ref_to_an_unknown_model_is_not_found(
        self, client: TestClient, trial_runs: TrialRunManager, stored_provider
    ):
        resp = _post(
            client,
            "trial-runs",
            {
                "model_ref": {"provider_id": stored_provider["provider_id"], "model_id": "no-such-model"},
                "parameters": PARAMETERS,
            },
        )

        assert resp.status_code == 404

    def test_an_unknown_run_is_not_found(self, client: TestClient, trial_runs: TrialRunManager):
        assert client.get("/api/v1/custom-endpoints/trial-runs/nope").status_code == 404
        assert client.post("/api/v1/custom-endpoints/trial-runs/nope/cancel").status_code == 404

    def test_a_model_ref_runs_the_stored_row_to_a_terminal_state(
        self, client: TestClient, trial_runs: TrialRunManager, stored_model_row: dict[str, Any]
    ):
        """模型行这条入口装的是生产那道构造缝装出来的 backend，不是另一个只在测试里存在的对象。"""
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            created = _post(
                client,
                "trial-runs",
                {
                    "model_ref": {
                        "provider_id": stored_model_row["provider_id"],
                        "model_id": stored_model_row["model_id"],
                    },
                    "parameters": PARAMETERS,
                },
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["id"]
            _drain(trial_runs, run_id)

        fetched = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}").json()
        assert fetched["status"] == "succeeded", fetched["error"]
        assert fetched["video_url"] == "https://relay.test/files/job-42.mp4"
        # 记账身份取模型行，不像内联定义那样回落到 base_url 的 host。
        assert (fetched["provider"], fetched["model"]) == (
            stored_model_row["provider_id"],
            stored_model_row["model_id"],
        )
        # 模型行挂着自定义调用端点，结果体才有渲染请求与逐阶段提取这两段。
        assert fetched["request"]["url"] == "https://relay.test/v1/video/create"
        assert fetched["extractions"]["submit"]["task_id"] == "job-42"


@pytest.fixture()
async def stored_model_row(db_engine) -> dict[str, Any]:
    """一条挂着自定义调用端点的视频模型行，供 ``model_ref`` 用例引用。"""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        endpoint = await CustomEndpointRepository(session).create(
            definition=custom_endpoint_definition(),
            kind="declarative",
            schema_version="1.0.0",
            media_type="video",
            display_name="示例端点",
        )
        provider = await CustomProviderRepository(session).create_provider(
            display_name="中转站",
            discovery_format="openai",
            base_url="https://relay.test",
            api_key="sk-secret-key-1234",
            models=[
                {
                    "model_id": "video-x",
                    "display_name": "video-x",
                    "endpoint": make_endpoint_key(endpoint.id),
                    "is_enabled": True,
                    "is_default": True,
                }
            ],
        )
        await session.commit()
        return {"provider_id": make_provider_id(provider.id), "model_id": "video-x"}


@pytest.fixture()
async def stored_provider(db_engine) -> dict[str, Any]:
    """一条自定义供应商行，供「凭证读库」用例引用。"""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="中转站",
            discovery_format="openai",
            base_url="https://api.example.com",
            api_key="sk-stored-key-9876",
            models=[],
        )
        await session.commit()
        return {"id": provider.id, "provider_id": make_provider_id(provider.id)}


def _drain(trial_runs: TrialRunManager, run_id: str, *, tries: int = 500) -> None:
    """等后台 run 走到终态。

    ``TestClient`` 把应用跑在另一个线程的事件循环上，测试线程只能真等——这也正是客户端看到的
    形态：发起后靠 ``GET`` 轮询。
    """
    for _ in range(tries):
        run = trial_runs.get(run_id)
        if run is not None and run.status.value in ("succeeded", "failed"):
            return
        time.sleep(0.01)
    raise AssertionError("测试连接未在预期内到达终态")
