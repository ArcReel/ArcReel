"""端点测试 API：预览请求、验证响应、测试连接三个端点。

三者共用保存接口那一个校验器，因此错误码集一致——本文件用同一份非法定义打四个入口来锁住这一点。
"""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
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

    def test_a_fixed_url_definition_previews_with_only_an_api_key(self, client: TestClient):
        """三节 URL 都写死绝对地址时，只带 api_key 的凭证不得被丢弃成占位符。"""
        definition = custom_endpoint_definition()
        definition["submit"] = {**definition["submit"], "url": "https://fixed.test/v1/video/create"}
        definition["poll"] = {**definition["poll"], "url": "https://fixed.test/v1/video/fetch/{{ task_id }}"}

        resp = _post(
            client,
            "preview-request",
            {
                "definition": definition,
                "parameters": PARAMETERS,
                "credentials": {"api_key": "sk-secret-key-1234"},
            },
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["submit"]["headers"]["Authorization"] == "Bearer ****1234"

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


class TestAssetLimits:
    def test_more_files_than_the_cap_are_rejected(self, client: TestClient):
        files = [("reference_images", (f"r{i}.png", b"x", "image/png")) for i in range(17)]
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
            files=files,
        )

        assert resp.status_code == 400
        # 响应体是渲染后的产品文案（错误码不下发）；断言上限数字在场，锁住命中的是数量上限而非别的 400。
        assert "16" in resp.json()["detail"]

    def test_files_under_unknown_field_names_still_count(self, client: TestClient):
        """换个字段名的文件同样已被解析缓冲；不数进去，上限就是换个 key 即可绕开的摆设。"""
        files = [(f"junk_{i}", (f"r{i}.png", b"x", "image/png")) for i in range(17)]
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
            files=files,
        )

        assert resp.status_code == 400
        assert "16" in resp.json()["detail"]


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
            _drain(client, trial_runs, run_id)

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
            third = _post(client, "trial-runs", payload)
            assert third.status_code == 201
            # 这一笔也要在离开 http 替身与时钟替身之前停掉，否则它会带着真实网络继续轮询。
            third_id = third.json()["id"]
            assert client.post(f"/api/v1/custom-endpoints/trial-runs/{third_id}/cancel").status_code == 204

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

    def test_a_definition_with_fixed_urls_runs_without_a_base_url(
        self, client: TestClient, trial_runs: TrialRunManager
    ):
        """三节 URL 都写死绝对地址的定义没有一处会用到接口地址，不必逼调用方编一个假地址。"""
        definition = custom_endpoint_definition()
        definition["submit"] = {**definition["submit"], "url": "https://fixed.test/v1/video/create"}
        definition["poll"] = {**definition["poll"], "url": "https://fixed.test/v1/video/fetch/{{ task_id }}"}

        with capture_http() as router, bounded_poll_clock():
            router.post("https://fixed.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://fixed.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(200, json={"status": "processing"})
            )
            created = _post(
                client,
                "trial-runs",
                {
                    "definition": definition,
                    "parameters": PARAMETERS,
                    "credentials": {"api_key": "sk-secret-key-1234"},
                },
            )
            assert created.status_code == 201, created.text
            # 内联凭证没有供应商身份，账本落提交地址的 host——这笔钱实际打给谁。
            assert created.json()["provider"] == "fixed.test"
            client.post(f"/api/v1/custom-endpoints/trial-runs/{created.json()['id']}/cancel")

    def test_a_credential_free_definition_runs_without_credentials(
        self, client: TestClient, trial_runs: TrialRunManager
    ):
        """auth 为空且三节 URL 全写死绝对地址的定义没有一处用得上凭证，不带凭证即可发起。"""
        definition = custom_endpoint_definition(auth={})
        definition["submit"] = {**definition["submit"], "url": "https://fixed.test/v1/video/create"}
        definition["poll"] = {**definition["poll"], "url": "https://fixed.test/v1/video/fetch/{{ task_id }}"}

        with capture_http() as router, bounded_poll_clock():
            router.post("https://fixed.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://fixed.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(200, json={"status": "processing"})
            )
            created = _post(client, "trial-runs", {"definition": definition, "parameters": PARAMETERS})
            assert created.status_code == 201, created.text
            client.post(f"/api/v1/custom-endpoints/trial-runs/{created.json()['id']}/cancel")

    def test_a_model_ref_to_a_builtin_declarative_endpoint_reports_diagnostics(
        self, client: TestClient, trial_runs: TrialRunManager, stored_builtin_endpoint_model_row: dict[str, Any]
    ):
        """定义随版发布不该让结果体缺渲染请求与逐阶段提取——与 ce-* 走同一条诊断路。"""
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/generations").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/generations/job-42").mock(
                return_value=httpx.Response(
                    200, json={"status": "succeeded", "url": "https://relay.test/files/job-42.mp4"}
                )
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))
            created = _post(
                client,
                "trial-runs",
                {
                    "model_ref": {
                        "provider_id": stored_builtin_endpoint_model_row["provider_id"],
                        "model_id": stored_builtin_endpoint_model_row["model_id"],
                    },
                    "parameters": PARAMETERS,
                },
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["id"]
            _drain(client, trial_runs, run_id)

        fetched = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}").json()
        assert fetched["status"] == "succeeded", fetched["error"]
        assert fetched["request"]["url"] == "https://relay.test/v1/video/generations"
        assert fetched["extractions"]["submit"]["task_id"] == "job-42"

    def test_a_builtin_minimax_model_ref_reports_declarative_diagnostics(
        self, client: TestClient, trial_runs: TrialRunManager, stored_minimax_config: None
    ):
        """内置模型行走声明式装配：结果体同样给出渲染请求与逐阶段提取，不因定义随版发布而缺段。"""
        with capture_http() as router, bounded_poll_clock():
            router.post("https://api.minimaxi.com/v2/video_generation").mock(
                return_value=httpx.Response(200, json={"task_id": "t-1"})
            )
            router.get("https://api.minimaxi.com/v2/query/video_generation/t-1").mock(
                return_value=httpx.Response(
                    200, json={"task": {"status": "succeeded", "content": {"url": "https://cdn.test/mm/h3.mp4"}}}
                )
            )
            router.get("https://cdn.test/mm/h3.mp4").mock(return_value=httpx.Response(200, content=b"video"))
            created = _post(
                client,
                "trial-runs",
                {"model_ref": {"provider_id": "minimax", "model_id": "MiniMax-H3"}, "parameters": PARAMETERS},
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["id"]
            _drain(client, trial_runs, run_id)

        fetched = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}").json()
        assert fetched["status"] == "succeeded", fetched["error"]
        assert fetched["request"]["url"] == "https://api.minimaxi.com/v2/video_generation"
        # 未指定分辨率：定义的 defaults.resolution 渲进请求，结果体的渲染请求里同样可见。
        assert fetched["request"]["body"]["resolution"] == "768P"
        assert fetched["extractions"]["submit"]["task_id"] == "t-1"

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

    def test_mixed_credential_sources_are_rejected(self, client: TestClient, stored_provider):
        """provider_id 与内联字段并存时不静默取库里那份——付费目标必须是调用方明确选的。"""
        resp = _post(
            client,
            "preview-request",
            {
                "definition": custom_endpoint_definition(),
                "parameters": PARAMETERS,
                "credentials": {"provider_id": stored_provider["provider_id"], "api_key": "sk-inline"},
            },
        )

        assert resp.status_code == 400

    def test_a_model_ref_run_records_the_referenced_model(
        self, client: TestClient, trial_runs: TrialRunManager, stored_model_row: dict[str, Any]
    ):
        """parameters.model 与 model_ref 不一致时以模型行为准：结果体记录的就是实际执行、记账的那个。"""
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
                    "parameters": {**PARAMETERS, "model": "some-other-model"},
                },
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["id"]
            _drain(client, trial_runs, run_id)

        fetched = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}").json()
        assert fetched["status"] == "succeeded", fetched["error"]
        assert fetched["request"]["body"]["model"] == stored_model_row["model_id"]

    def test_supplying_both_definition_and_model_ref_is_rejected(
        self, client: TestClient, trial_runs: TrialRunManager, stored_model_row: dict[str, Any]
    ):
        """两种目标形态并存时不静默取其一——付费请求不能打到调用方没选中的目标上。"""
        resp = _post(
            client,
            "trial-runs",
            {
                "definition": custom_endpoint_definition(),
                "credentials": INLINE_CREDENTIALS,
                "model_ref": {
                    "provider_id": stored_model_row["provider_id"],
                    "model_id": stored_model_row["model_id"],
                },
                "parameters": PARAMETERS,
            },
        )

        assert resp.status_code == 400

    def test_a_model_ref_to_a_disabled_model_is_rejected(
        self, client: TestClient, trial_runs: TrialRunManager, stored_disabled_model_row: dict[str, Any]
    ):
        """只查存在不够：禁用行会让装配层回退默认模型，付费测试打到并计费给另一个模型。"""
        resp = _post(
            client,
            "trial-runs",
            {
                "model_ref": {
                    "provider_id": stored_disabled_model_row["provider_id"],
                    "model_id": stored_disabled_model_row["model_id"],
                },
                "parameters": PARAMETERS,
            },
        )

        assert resp.status_code == 400

    def test_a_model_ref_without_a_provider_base_url_is_rejected(
        self, client: TestClient, trial_runs: TrialRunManager, stored_model_row_without_base_url: dict[str, Any]
    ):
        """装配层缺 base_url 只抛一句不可翻译的中文，且落在脱离请求的后台任务里；请求线程先拒。"""
        resp = _post(
            client,
            "trial-runs",
            {
                "model_ref": {
                    "provider_id": stored_model_row_without_base_url["provider_id"],
                    "model_id": stored_model_row_without_base_url["model_id"],
                },
                "parameters": PARAMETERS,
            },
        )

        assert resp.status_code == 400
        assert "base_url" in resp.json()["detail"]

    def test_stored_provider_credentials_missing_the_required_api_key_are_rejected(
        self, client: TestClient, trial_runs: TrialRunManager, stored_provider_without_api_key: dict[str, Any]
    ):
        """provider_id 读出的空 api_key 与内联缺字段同判：请求线程上 400，不进后台任务。"""
        resp = _post(
            client,
            "trial-runs",
            {
                "definition": custom_endpoint_definition(),
                "parameters": PARAMETERS,
                "credentials": {"provider_id": stored_provider_without_api_key["provider_id"]},
            },
        )

        assert resp.status_code == 400

    def test_a_model_ref_to_a_provider_without_an_api_key_is_rejected(
        self, client: TestClient, trial_runs: TrialRunManager, stored_model_row_without_api_key: dict[str, Any]
    ):
        """定义的 auth 节非空而供应商没存 api_key：放行会带着空鉴权头付费打一个注定被拒的调用。"""
        resp = _post(
            client,
            "trial-runs",
            {
                "model_ref": {
                    "provider_id": stored_model_row_without_api_key["provider_id"],
                    "model_id": stored_model_row_without_api_key["model_id"],
                },
                "parameters": PARAMETERS,
            },
        )

        assert resp.status_code == 400

    def test_a_builtin_model_ref_to_a_non_video_model_is_rejected(
        self, client: TestClient, trial_runs: TrialRunManager
    ):
        """内置供应商的文本档若放行，会被派发到视频端点，付费打给另一个模型。"""
        resp = _post(
            client,
            "trial-runs",
            {"model_ref": {"provider_id": "minimax", "model_id": "MiniMax-M3"}, "parameters": PARAMETERS},
        )

        assert resp.status_code == 400

    def test_a_builtin_model_ref_to_an_unregistered_model_is_not_found(
        self, client: TestClient, trial_runs: TrialRunManager
    ):
        """未登记的 model 会让装配层落到该供应商的默认视频模型，同样是付费打给另一个模型。"""
        resp = _post(
            client,
            "trial-runs",
            {"model_ref": {"provider_id": "minimax", "model_id": "no-such-model"}, "parameters": PARAMETERS},
        )

        assert resp.status_code == 404

    def test_a_builtin_model_ref_without_credentials_is_rejected(self, client: TestClient, trial_runs: TrialRunManager):
        """装配层缺凭证时抛的是 backend 自己写的中文句子，且落在后台任务里；请求线程先拒。"""
        resp = _post(
            client,
            "trial-runs",
            {"model_ref": {"provider_id": "minimax", "model_id": "MiniMax-H3"}, "parameters": PARAMETERS},
        )

        assert resp.status_code == 400

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
            _drain(client, trial_runs, run_id)

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
async def stored_minimax_config(db_engine) -> None:
    """内置 minimax 的凭证配置，供内置直连 ``model_ref`` 用例引用。"""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        await ConfigService(session).set_provider_config("minimax", "api_key", "sk-minimax-key-0001")
        await session.commit()


@pytest.fixture()
async def stored_provider_without_api_key(db_engine) -> dict[str, Any]:
    """一条没存 api_key 的供应商行，供「凭证读库缺字段」用例引用。"""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="中转站",
            discovery_format="openai",
            base_url="https://relay.test",
            api_key="",
            models=[],
        )
        await session.commit()
        return {"provider_id": make_provider_id(provider.id)}


@pytest.fixture()
async def stored_model_row_without_api_key(db_engine) -> dict[str, Any]:
    """同样的模型行，但供应商没存 api_key——定义的 auth 节非空，空凭证发不出合法请求。"""
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
            api_key="",
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
async def stored_builtin_endpoint_model_row(db_engine) -> dict[str, Any]:
    """挂内置声明式端点（newapi-video）的模型行：定义随版发布，不落 custom_endpoint 表。"""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="中转站",
            discovery_format="openai",
            base_url="https://relay.test",
            api_key="sk-secret-key-1234",
            models=[
                {
                    "model_id": "video-nv",
                    "display_name": "video-nv",
                    "endpoint": "newapi-video",
                    "is_enabled": True,
                    "is_default": True,
                }
            ],
        )
        await session.commit()
        return {"provider_id": make_provider_id(provider.id), "model_id": "video-nv"}


@pytest.fixture()
async def stored_model_row_without_base_url(db_engine) -> dict[str, Any]:
    """同样的模型行，但供应商没填接口地址——声明式端点的提交 URL 需要它。"""
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
            base_url="",
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
async def stored_disabled_model_row(db_engine) -> dict[str, Any]:
    """一条已禁用的视频模型行，供拒绝用例引用。"""
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
                    "is_enabled": False,
                    "is_default": False,
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


def _drain(client: TestClient, trial_runs: TrialRunManager, run_id: str) -> None:
    """等后台 run 走到终态。

    ``TestClient`` 把应用跑在另一个线程的事件循环上；经它的 portal 在那个循环里
    ``await manager.wait()``，按事件同步而不是按挂钟轮询。
    """
    assert client.portal is not None
    run = client.portal.call(trial_runs.wait, run_id)
    assert run is not None and run.status.value in ("succeeded", "failed")
