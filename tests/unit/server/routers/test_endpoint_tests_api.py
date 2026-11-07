"""Endpoint-test API: request previews, response validation, connection tests.

All three share save validators/error-code sets; submit one invalid definition to all four entries to lock this.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from lib.billing.ledger import Ledger
from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from lib.custom_provider import make_endpoint_key, make_provider_id
from lib.custom_provider.endpoint_test import TrialRunManager
from lib.db import get_async_session
from lib.db.models.api_call import ApiCall
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import custom_endpoints
from server.routers.endpoint_tests import get_config_resolver, get_trial_run_manager
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.factories import comfyui_endpoint_definition, custom_endpoint_definition
from tests.fakes import MP4_BYTES, PNG_BYTES, bounded_poll_clock
from tests.http_capture import capture_http

PARAMETERS = {"model": "video-x", "prompt": "纸船顺流而下", "duration_seconds": 5}
INLINE_CREDENTIALS = {"base_url": "https://relay.test", "api_key": "sk-secret-key-1234"}
COMFYUI_CREDENTIALS = {"base_url": "https://comfy.test", "api_key": ""}


@pytest.fixture
def file_db_engine(file_db_factory: async_sessionmaker[AsyncSession]) -> AsyncEngine:
    """File-database engines shared by fixtures, with independent connections.

    ``TestClient`` returns after response bodies; request-session cleanup and background trial billing write concurrently.
    Independent connections keep their transactions separate.
    """
    return file_db_factory.kw["bind"]


@pytest.fixture
def trial_runs(tmp_path, file_db_engine) -> TrialRunManager:
    """Registries isolated to tmp_path/file databases, injected through dependency overrides."""
    return TrialRunManager(
        root=tmp_path / "trial_runs",
        ledger=Ledger(session_factory=async_sessionmaker(file_db_engine, expire_on_commit=False)),
        read_poll_timeout=_fixed_timeout,
    )


@pytest.fixture
def endpoint_tests_app(file_db_engine, trial_runs: TrialRunManager) -> FastAPI:
    session_factory = async_sessionmaker(file_db_engine, expire_on_commit=False)
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


@pytest.fixture
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


def _mock_successful_comfyui_run(router) -> None:
    """Working fake ComfyUI: submissions return prompt_id, second history reads become terminal, /view retrieves outputs.

    Supply ``/queue`` too: empty history rounds use it to distinguish lost tasks; queue membership proves
    tasks remain queued.
    """
    router.post("https://comfy.test/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
    router.get("https://comfy.test/queue").mock(
        return_value=httpx.Response(200, json={"queue_running": [[0, "p-1", {}, [], {}]], "queue_pending": []})
    )
    router.get("https://comfy.test/history/p-1").mock(
        side_effect=[
            httpx.Response(200, json={}),
            httpx.Response(
                200,
                json={
                    "p-1": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {"9": {"gifs": [{"filename": "final.mp4", "subfolder": "", "type": "output"}]}},
                    }
                },
            ),
        ]
    )
    router.get("https://comfy.test/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))


def _mock_successful_comfyui_image_run(router) -> None:
    """As above, but image outputs: ``images`` contains artifacts, retrieved bytes carry PNG headers."""
    router.post("https://comfy.test/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
    router.get("https://comfy.test/queue").mock(
        return_value=httpx.Response(200, json={"queue_running": [[0, "p-1", {}, [], {}]], "queue_pending": []})
    )
    router.get("https://comfy.test/history/p-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "p-1": {
                    "status": {"completed": True, "status_str": "success"},
                    "outputs": {
                        "9": {"images": [{"filename": "ArcReel_00001_.png", "subfolder": "", "type": "output"}]}
                    },
                }
            },
        )
    )
    router.get("https://comfy.test/view").mock(return_value=httpx.Response(200, content=PNG_BYTES))


def _comfyui_image_definition() -> dict[str, Any]:
    """Image ComfyUI endpoint definitions lack frame-rate and negative-input semantic keys."""
    definition = comfyui_endpoint_definition(media_type="image")
    definition["bindings"] = {
        key: value for key, value in definition["bindings"].items() if key not in ("fps", "negative_prompt")
    }
    return definition


def _mock_comfyui_run_stuck_in_polling(router) -> asyncio.Event:
    """Fake ComfyUI accepts submissions but never reaches terminal states; returned events set on first history reads.

    Awaiting this handshake proves remote jobs are running and local polling has begun, exactly the state
    cancellation must stop, without guessing how often to yield event loops.
    """
    polling = asyncio.Event()
    gate = asyncio.Event()  # Never set: only cancel can stop this run.

    async def _never_settles(_request: httpx.Request) -> httpx.Response:
        polling.set()
        await gate.wait()
        raise AssertionError("闸口只由取消解除，不该走到这里")

    router.post("https://comfy.test/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
    router.get("https://comfy.test/history/p-1").mock(side_effect=_never_settles)
    router.get("https://comfy.test/system_stats").mock(
        return_value=httpx.Response(200, json={"system": {"comfyui_version": "0.26.1"}})
    )
    return polling


def _post(client: TestClient, path: str, payload: dict[str, Any]):
    return client.post(f"/api/v1/custom-endpoints/{path}", json=payload)


def _hold_submit(router) -> asyncio.Queue[None]:
    """Block submissions behind cancellation-only gates, ensuring runs remain ongoing until tests stop them.

    Maintaining ongoing states through ``status: processing`` polling creates races: fake-clock polls take almost
    no real time, so background tasks may exhaust ``poll_timeout`` between assertions, become terminal and release slots. Gates
    place lifecycle control in tests, keeping conclusions independent of machine load.

    Each submission reaching gates adds an item to returned queues. Before cancellation, await it through ``client.portal.call(queue.get)``:
    after ``create`` returns, background tasks may still insert presubmission billing records; cancellation there makes SQLAlchemy
    invalidate connections, losing in-memory tables with the sole connection. Next runs fail billing insertion and end themselves, yielding cancellation 404.
    """
    arrived: asyncio.Queue[None] = asyncio.Queue()
    gate = asyncio.Event()  # Never set: only cancel can stop this run.

    async def _never_responds(_request: httpx.Request) -> httpx.Response:
        arrived.put_nowait(None)
        await gate.wait()
        raise AssertionError("闸口只由取消解除，不该走到这里")

    router.post("https://relay.test/v1/video/create").mock(side_effect=_never_responds)
    return arrived


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
        """Definitions with absolute URLs in all three sections must retain api_key-only credentials, never discard them as placeholders."""
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
        # Bodies contain rendered product copy, not error codes; assert limit numbers to distinguish count-limit failures from other 400s.
        assert "16" in resp.json()["detail"]

    def test_files_under_unknown_field_names_still_count(self, client: TestClient):
        """Files under other field names are also parsed/buffered; excluding them makes limits bypassable by renaming keys."""
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
    """Invalid definitions return identical error-code sets from three test endpoints and saving."""

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

        resp = client.post(
            "/api/v1/custom-endpoints/preview-request",
            json={"definition": definition, "parameters": PARAMETERS, "credentials": INLINE_CREDENTIALS},
            headers={"Accept-Language": "en"},
        )

        assert resp.status_code == 422
        assert resp.json()["diagnostic"]["errors"][0] == {
            "path": "submit",
            "code": "enum_map_value_missing",
            "message": "enum_maps.duration has no entry for '5'",
        }


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

    def test_failed_run_diagnostic_is_rendered_in_the_read_request_locale(
        self, client: TestClient, trial_runs: TrialRunManager
    ):
        definition = custom_endpoint_definition()
        definition["submit"]["extract"]["task_id"] = ["$[?@.a == 1e400]"]
        with capture_http() as router:
            router.post("https://relay.test/v1/video/create").mock(return_value=httpx.Response(200, json={"a": 1}))
            created = _post(
                client,
                "trial-runs",
                {
                    "definition": definition,
                    "parameters": PARAMETERS,
                    "credentials": INLINE_CREDENTIALS,
                },
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["id"]
            _drain(client, trial_runs, run_id)

        en = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}", headers={"Accept-Language": "en"}).json()
        vi = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}", headers={"Accept-Language": "vi"}).json()
        assert en["error"] == (
            "Endpoint response extraction failed: Could not evaluate extraction path: $[?@.a == 1e400]"
        )
        assert vi["error"] == (
            "Không thể trích xuất phản hồi endpoint: Không thể đánh giá đường dẫn trích xuất: $[?@.a == 1e400]"
        )

    def test_a_second_concurrent_run_is_refused(self, client: TestClient, trial_runs: TrialRunManager):
        with capture_http() as router:
            submits = _hold_submit(router)
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
            client.portal.call(submits.get)
            cancelled = client.post(f"/api/v1/custom-endpoints/trial-runs/{run_id}/cancel")
            assert cancelled.status_code == 204
            # Cancellation releases slots; the same definition can run again.
            third = _post(client, "trial-runs", payload)
            assert third.status_code == 201
            # Stop this run before leaving HTTP fakes, or it remains blocked until event-loop destruction.
            third_id = third.json()["id"]
            client.portal.call(submits.get)
            assert client.post(f"/api/v1/custom-endpoints/trial-runs/{third_id}/cancel").status_code == 204

    def test_a_cancelled_run_leaves_nothing_to_read(self, client: TestClient, trial_runs: TrialRunManager):
        with capture_http() as router:
            submits = _hold_submit(router)
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
            client.portal.call(submits.get)
            # 204 itself proves cancellation stopped an ongoing run, rather than one already finished naturally.
            assert client.post(f"/api/v1/custom-endpoints/trial-runs/{run_id}/cancel").status_code == 204

        assert client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}").status_code == 404

    def test_requires_credentials(self, client: TestClient, trial_runs: TrialRunManager):
        resp = _post(client, "trial-runs", {"definition": custom_endpoint_definition(), "parameters": PARAMETERS})

        assert resp.status_code == 400

    def test_a_definition_with_fixed_urls_runs_without_a_base_url(
        self, client: TestClient, trial_runs: TrialRunManager
    ):
        """All-absolute URLs in three sections never use API addresses; do not force callers to invent fake addresses."""
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
            # Inline credentials lack provider identities; ledgers use submission hosts, the actual payment destinations.
            assert created.json()["provider"] == "fixed.test"
            client.post(f"/api/v1/custom-endpoints/trial-runs/{created.json()['id']}/cancel")

    def test_a_credential_free_definition_runs_without_credentials(
        self, client: TestClient, trial_runs: TrialRunManager
    ):
        """Empty auth plus absolute URLs in all three sections never uses credentials; allow credential-free initiation."""
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
        """Bundled definitions must retain rendered requests/per-stage extraction, sharing ce-* diagnostics."""
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
        assert fetched["status"] == "succeeded"
        assert fetched["request"]["url"] == "https://relay.test/v1/video/generations"
        assert fetched["extractions"]["submit"]["task_id"] == "job-42"

    def test_a_builtin_minimax_model_ref_reports_declarative_diagnostics(
        self, client: TestClient, trial_runs: TrialRunManager, stored_minimax_config: None
    ):
        """Built-in model rows use declarative assembly; result bodies retain rendered requests/per-stage extraction despite bundled definitions."""
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
        # Unspecified resolution renders definition defaults.resolution into requests, also visible in result rendered requests.
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
        """Coexisting provider_id/inline fields must not silently choose database values; callers must explicitly select paid targets."""
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
        """parameters.model differing from model_ref follows model rows; results record actual execution/billing models."""
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
        """Coexisting target forms must not silently choose one; paid requests cannot hit targets callers did not select."""
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
        """Existence alone is insufficient: disabled rows make assembly fall back to defaults, executing/billing another model."""
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
        """Missing assembly base_url raises untranslatable Chinese in detached background tasks; reject on request threads first."""
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
        """Empty database api_key from provider_id equals missing inline fields: request-thread 400, no background tasks."""
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
        """Nonempty definition auth without saved provider api_key would bill doomed requests with empty authorization headers."""
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
        """Allowing built-in text tiers dispatches them to video endpoints, billing another model."""
        resp = _post(
            client,
            "trial-runs",
            {"model_ref": {"provider_id": "minimax", "model_id": "MiniMax-M3"}, "parameters": PARAMETERS},
        )

        assert resp.status_code == 400

    def test_a_builtin_model_ref_to_an_unregistered_model_is_not_found(
        self, client: TestClient, trial_runs: TrialRunManager
    ):
        """Unregistered models make assembly fall back to provider default video models, again billing another model."""
        resp = _post(
            client,
            "trial-runs",
            {"model_ref": {"provider_id": "minimax", "model_id": "no-such-model"}, "parameters": PARAMETERS},
        )

        assert resp.status_code == 404

    def test_a_builtin_model_ref_without_credentials_is_rejected(self, client: TestClient, trial_runs: TrialRunManager):
        """Missing assembly credentials raise backend-authored Chinese in background tasks; reject on request threads first."""
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
        """Model-row entry uses backends constructed through production seams, never separate test-only objects."""
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
        # Billing identities come from model rows, unlike inline-definition base_url host fallback.
        assert (fetched["provider"], fetched["model"]) == (
            stored_model_row["provider_id"],
            stored_model_row["model_id"],
        )
        # Custom-endpoint model rows produce both rendered requests and per-stage extraction in results.
        assert fetched["request"]["url"] == "https://relay.test/v1/video/create"
        assert fetched["extractions"]["submit"]["task_id"] == "job-42"

    def test_a_model_ref_run_sends_and_records_the_overridden_path(
        self, client: TestClient, trial_runs: TrialRunManager, stored_model_row_with_submit_override: dict[str, Any]
    ):
        row = stored_model_row_with_submit_override
        with capture_http() as router, bounded_poll_clock():
            submit = router.post("https://relay.test/v2/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            _mock_successful_run(router)
            created = _post(
                client,
                "trial-runs",
                {
                    "model_ref": {"provider_id": row["provider_id"], "model_id": row["model_id"]},
                    "parameters": PARAMETERS,
                },
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["id"]
            _drain(client, trial_runs, run_id)

        fetched = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}").json()
        assert fetched["status"] == "succeeded", fetched["error"]
        assert submit.call_count == 1
        assert fetched["request"]["url"] == "https://relay.test/v2/create"


@pytest.fixture
async def stored_model_row(file_db_engine) -> dict[str, Any]:
    """Video model rows attached to custom endpoints, for ``model_ref`` tests."""
    return await _store_model_row(file_db_engine)


@pytest.fixture
async def stored_model_row_with_submit_override(file_db_engine) -> dict[str, Any]:
    """Like ``stored_model_row``, with submission paths overridden to ``/v2/create``."""
    return await _store_model_row(file_db_engine, path_overrides={"submit": "/v2/create"})


async def _store_model_row(file_db_engine, *, path_overrides: dict[str, str] | None = None) -> dict[str, Any]:
    session_factory = async_sessionmaker(file_db_engine, expire_on_commit=False)
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
                    "path_overrides": path_overrides,
                }
            ],
        )
        await session.commit()
        return {"provider_id": make_provider_id(provider.id), "model_id": "video-x"}


@pytest.fixture
async def stored_minimax_config(file_db_engine) -> None:
    """Built-in minimax credentials for direct built-in ``model_ref`` tests."""
    session_factory = async_sessionmaker(file_db_engine, expire_on_commit=False)
    async with session_factory() as session:
        await ConfigService(session).set_provider_config("minimax", "api_key", "sk-minimax-key-0001")
        await session.commit()


@pytest.fixture
async def stored_provider_without_api_key(file_db_engine) -> dict[str, Any]:
    """Provider rows without stored api_key, for database-credential missing-field tests."""
    session_factory = async_sessionmaker(file_db_engine, expire_on_commit=False)
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


@pytest.fixture
async def stored_model_row_without_api_key(file_db_engine) -> dict[str, Any]:
    """Same model rows without provider api_key: nonempty definition auth cannot produce valid requests with empty credentials."""
    session_factory = async_sessionmaker(file_db_engine, expire_on_commit=False)
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


@pytest.fixture
async def stored_builtin_endpoint_model_row(file_db_engine) -> dict[str, Any]:
    """Model rows on built-in declarative endpoints (newapi-video): definitions ship bundled, absent from custom_endpoint tables."""
    session_factory = async_sessionmaker(file_db_engine, expire_on_commit=False)
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


@pytest.fixture
async def stored_model_row_without_base_url(file_db_engine) -> dict[str, Any]:
    """Same model rows without provider API addresses, required by declarative submission URLs."""
    session_factory = async_sessionmaker(file_db_engine, expire_on_commit=False)
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


@pytest.fixture
async def stored_disabled_model_row(file_db_engine) -> dict[str, Any]:
    """Disabled video model rows for rejection tests."""
    session_factory = async_sessionmaker(file_db_engine, expire_on_commit=False)
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


class TestComfyuiEndpoints:
    """ComfyUI dispatch across three entries: previews/connections supported, response validation structurally unsupported."""

    def test_checking_a_response_is_refused_as_unsupported(self, client: TestClient):
        resp = _post(
            client,
            "check-response",
            {"definition": comfyui_endpoint_definition(), "stage": "poll", "response_body": {}},
        )

        assert resp.status_code == 400
        assert "comfyui" in resp.json()["detail"]

    def test_previewing_returns_the_prompt_body_and_the_conversions(self, client: TestClient):
        resp = _post(
            client,
            "preview-request",
            {
                "definition": comfyui_endpoint_definition(),
                "parameters": {**PARAMETERS, "resolution": "720p"},
                "credentials": COMFYUI_CREDENTIALS,
            },
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["submit"]["url"] == "https://comfy.test/prompt"
        assert body["submit"]["body"]["prompt"]["6"]["inputs"]["text"] == "纸船顺流而下"
        assert body["conversions"]["width"] == 720
        assert body["result"] is None

    def test_previewing_a_declarative_endpoint_has_nothing_to_convert(self, client: TestClient):
        resp = _post(
            client,
            "preview-request",
            {
                "definition": custom_endpoint_definition(),
                "parameters": PARAMETERS,
                "credentials": INLINE_CREDENTIALS,
            },
        )

        assert resp.json()["conversions"] is None

    def test_an_empty_api_key_is_enough_to_preview(self, client: TestClient):
        """ComfyUI is natively unauthenticated; definitions without ``auth`` must not be blocked for missing API Keys."""
        resp = _post(
            client,
            "preview-request",
            {
                "definition": comfyui_endpoint_definition(),
                "parameters": PARAMETERS,
                "credentials": COMFYUI_CREDENTIALS,
            },
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["submit"]["headers"] == {}

    def test_a_workflow_that_cannot_be_edited_down_is_refused_with_its_failure_text(self, client: TestClient):
        """Image-edit cascading reaches output nodes: definitions are valid, but cannot render requests with these materials."""
        # Three slots; the last two merge directly into output nodes. Uploading one image deletes that entire route, cascading to outputs.
        definition = comfyui_endpoint_definition()
        workflow = definition["workflow"]
        workflow["20"] = {"class_type": "LoadImage", "inputs": {"image": "a.png"}}
        workflow["21"] = {"class_type": "LoadImage", "inputs": {"image": "b.png"}}
        workflow["23"] = {"class_type": "LoadImage", "inputs": {"image": "c.png"}}
        workflow["22"] = {"class_type": "ImageBatch", "inputs": {"image1": ["21", 0], "image2": ["23", 0]}}
        workflow["30"] = {
            "class_type": "WanVaceToVideo",
            "inputs": {"positive": ["6", 0], "reference_image": ["20", 0]},
        }
        workflow["3"]["inputs"]["latent_image"] = ["30", 0]
        workflow["9"]["inputs"]["images"] = ["22", 0]
        definition["bindings"]["reference_images"] = [
            {
                "node": "20",
                "input": "image",
                "class_type": "LoadImage",
                "consumer": {"node": "30", "input": "reference_image", "class_type": "WanVaceToVideo"},
            },
            {
                "node": "21",
                "input": "image",
                "class_type": "LoadImage",
                "consumer": {"node": "22", "input": "image1", "class_type": "ImageBatch"},
            },
            {
                "node": "23",
                "input": "image",
                "class_type": "LoadImage",
                "consumer": {"node": "22", "input": "image2", "class_type": "ImageBatch"},
            },
        ]

        resp = client.post(
            "/api/v1/custom-endpoints/preview-request",
            data={
                "payload": json.dumps(
                    {"definition": definition, "parameters": PARAMETERS, "credentials": COMFYUI_CREDENTIALS}
                )
            },
            files={"reference_images": ("ref.png", b"x" * 16, "image/png")},
        )

        assert resp.status_code == 400
        # Use failure codes' own trilingual explanations, never separate wording.
        assert "无法改图" in resp.json()["detail"]

    def test_a_trial_run_reports_the_prompt_id_stages_and_artifact(
        self, client: TestClient, trial_runs: TrialRunManager
    ):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_comfyui_run(router)
            created = _post(
                client,
                "trial-runs",
                {
                    "definition": comfyui_endpoint_definition(),
                    "parameters": PARAMETERS,
                    "credentials": COMFYUI_CREDENTIALS,
                },
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["id"]
            _drain(client, trial_runs, run_id)

        fetched = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}").json()
        assert fetched["status"] == "succeeded", fetched["error"]
        assert fetched["error_code"] is None
        assert fetched["error_action"] is None
        assert fetched["provider_job_id"] == "p-1"
        assert fetched["stages"] == {"submit": "done", "poll": "done", "result": "done", "artifact": "done"}
        # Actual workflow seeds/material reference names resolve only at submission; never substitute result-body previews.
        assert fetched["request"] is None
        # Output extraction is fixed code, with no configurable extraction paths to report.
        assert fetched["extractions"] == {}
        artifact = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}/artifact")
        assert artifact.content == MP4_BYTES
        assert artifact.headers["content-type"] == "video/mp4"

    def test_the_queue_shows_a_readable_name_for_a_trial_run(self, client: TestClient, trial_runs: TrialRunManager):
        """Users must recognize newly clicked connection tests among their own manually run ComfyUI queue jobs."""
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_comfyui_run(router)
            created = _post(
                client,
                "trial-runs",
                {
                    "definition": comfyui_endpoint_definition(),
                    "parameters": PARAMETERS,
                    "credentials": COMFYUI_CREDENTIALS,
                },
            )
            run_id = created.json()["id"]
            _drain(client, trial_runs, run_id)
            submitted = json.loads(
                next(call.request for call in router.calls if call.request.url.path == "/prompt").content
            )

        assert submitted["client_id"].startswith("arcreel-endpoint-test-")

    def test_a_refused_workflow_comes_back_as_a_localised_failure_code(
        self, client: TestClient, trial_runs: TrialRunManager
    ):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://comfy.test/prompt").mock(
                return_value=httpx.Response(
                    400,
                    json={"node_errors": {"3": {"class_type": "KSampler", "errors": [{"message": "value too large"}]}}},
                )
            )
            created = _post(
                client,
                "trial-runs",
                {
                    "definition": comfyui_endpoint_definition(),
                    "parameters": PARAMETERS,
                    "credentials": COMFYUI_CREDENTIALS,
                },
            )
            run_id = created.json()["id"]
            _drain(client, trial_runs, run_id)

        fetched = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}").json()
        assert fetched["status"] == "failed"
        assert "KSampler: value too large" in fetched["error"]
        # Accept-Language changes copy, not codes; readers classify failures/next actions by code.
        assert fetched["error_code"] == "comfyui_node_errors"
        # Actions reuse project generation-failure mappings, never separate wording; three of eight ComfyUI codes indicate retry.
        assert fetched["error_action"] == "configure_provider"
        assert fetched["provider_job_id"] is None
        assert fetched["stages"]["submit"] == "done"

    def test_a_lost_job_points_at_retrying_rather_than_at_the_provider_settings(
        self, client: TestClient, trial_runs: TrialRunManager
    ):
        """Cards distinguish transient failures/configuration errors, with actions determined by failure codes themselves."""
        with capture_http() as router, bounded_poll_clock():
            router.post("https://comfy.test/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            # Neither queues nor history contain this run: ComfyUI likely restarted; retries can recover.
            router.get("https://comfy.test/queue").mock(
                return_value=httpx.Response(200, json={"queue_running": [], "queue_pending": []})
            )
            router.get("https://comfy.test/history/p-1").mock(return_value=httpx.Response(200, json={}))
            created = _post(
                client,
                "trial-runs",
                {
                    "definition": comfyui_endpoint_definition(),
                    "parameters": PARAMETERS,
                    "credentials": COMFYUI_CREDENTIALS,
                },
            )
            run_id = created.json()["id"]
            _drain(client, trial_runs, run_id)

        fetched = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}").json()
        assert fetched["status"] == "failed"
        assert fetched["error_code"] == "comfyui_job_lost"
        assert fetched["error_action"] == "retry"

    def test_a_trial_run_needs_the_service_address(self, client: TestClient, trial_runs: TrialRunManager):
        """All ComfyUI routes live under service roots; definitions contain no absolute URLs."""
        resp = _post(
            client,
            "trial-runs",
            {
                "definition": comfyui_endpoint_definition(),
                "parameters": PARAMETERS,
                "credentials": {"base_url": "", "api_key": ""},
            },
        )

        assert resp.status_code == 400

    def test_giving_up_stops_the_remote_job(self, client: TestClient, trial_runs: TrialRunManager):
        """Ongoing remote runs still occupy users' GPUs, while nobody wants this run's outputs anymore."""
        assert client.portal is not None
        with capture_http() as router:
            polling = _mock_comfyui_run_stuck_in_polling(router)
            job_cancel = router.post("https://comfy.test/api/jobs/p-1/cancel").mock(
                return_value=httpx.Response(200, json={})
            )
            created = _post(
                client,
                "trial-runs",
                {
                    "definition": comfyui_endpoint_definition(),
                    "parameters": PARAMETERS,
                    "credentials": COMFYUI_CREDENTIALS,
                },
            )
            run_id = created.json()["id"]
            client.portal.call(polling.wait)

            assert client.post(f"/api/v1/custom-endpoints/trial-runs/{run_id}/cancel").status_code == 204

        assert job_cancel.called

    def test_an_image_endpoint_can_still_be_previewed(self, client: TestClient):
        """Request previews are media-independent; endpoints unable to test connections must still show outgoing requests."""
        resp = _post(
            client,
            "preview-request",
            {
                "definition": _comfyui_image_definition(),
                "parameters": PARAMETERS,
                "credentials": COMFYUI_CREDENTIALS,
            },
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["submit"]["url"] == "https://comfy.test/prompt"

    def test_an_image_endpoint_runs_a_trial_run_and_serves_the_image(
        self, client: TestClient, trial_runs: TrialRunManager, file_db_engine
    ):
        """Image endpoints share connection testing: all four stage indicators, with image MIME outputs."""
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_comfyui_image_run(router)
            created = _post(
                client,
                "trial-runs",
                {
                    "definition": _comfyui_image_definition(),
                    "parameters": PARAMETERS,
                    "credentials": COMFYUI_CREDENTIALS,
                },
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["id"]
            _drain(client, trial_runs, run_id)

        fetched = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}").json()
        assert fetched["status"] == "succeeded", fetched["error"]
        assert fetched["media_type"] == "image"
        assert fetched["stages"] == {"submit": "done", "poll": "done", "result": "done", "artifact": "done"}
        # Images lack durations; recording seconds invents nonexistent usage-page durations.
        assert fetched["duration_seconds"] is None
        artifact = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}/artifact")
        assert artifact.content == PNG_BYTES
        assert artifact.headers["content-type"] == "image/png"
        # Ledger entries record actual calls: image requests lack duration and audio-track dimensions.
        assert client.portal is not None
        row = client.portal.call(_only_api_call, async_sessionmaker(file_db_engine, expire_on_commit=False))
        assert (row.call_type, row.duration_seconds, row.generate_audio) == ("image", None, False)

    def test_an_image_model_row_with_a_reference_image_passes_the_capability_gate(
        self, client: TestClient, trial_runs: TrialRunManager, stored_comfyui_image_model_row: dict[str, Any]
    ):
        """Model-row entry uses image capability gates; video gates must not misclassify reference-image image-to-image endpoints as unsupported."""
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_comfyui_image_run(router)
            router.post("https://comfy.test/upload/image").mock(
                return_value=httpx.Response(200, json={"name": "ref.png", "subfolder": "arcreel"})
            )
            created = client.post(
                "/api/v1/custom-endpoints/trial-runs",
                data={
                    "payload": json.dumps(
                        {
                            "model_ref": {
                                "provider_id": stored_comfyui_image_model_row["provider_id"],
                                "model_id": stored_comfyui_image_model_row["model_id"],
                            },
                            "parameters": PARAMETERS,
                        }
                    )
                },
                files={"reference_images": ("ref.png", PNG_BYTES, "image/png")},
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["id"]
            _drain(client, trial_runs, run_id)

        fetched = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}").json()
        assert fetched["status"] == "succeeded", fetched["error"]
        assert fetched["media_type"] == "image"

    def test_an_image_model_row_without_its_required_reference_image_is_rejected_in_words(
        self, client: TestClient, trial_runs: TrialRunManager, stored_comfyui_image_model_row: dict[str, Any]
    ):
        """Image-to-image without reference images is rejected by image gates, with localized capability reasons rather than failure-code strings."""
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_comfyui_image_run(router)
            created = _post(
                client,
                "trial-runs",
                {
                    "model_ref": {
                        "provider_id": stored_comfyui_image_model_row["provider_id"],
                        "model_id": stored_comfyui_image_model_row["model_id"],
                    },
                    "parameters": PARAMETERS,
                },
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["id"]
            _drain(client, trial_runs, run_id)

        fetched = client.get(f"/api/v1/custom-endpoints/trial-runs/{run_id}").json()
        assert fetched["status"] == "failed"
        assert fetched["error_code"] == "image_capability_missing_t2i"
        assert "不支持文生图" in fetched["error"]
        # Block before submission: not one byte reaches ComfyUI.
        assert router.calls.call_count == 0


@pytest.fixture
async def stored_comfyui_image_model_row(file_db_engine) -> dict[str, Any]:
    """Image model rows on ComfyUI image-to-image endpoints for image ``model_ref`` tests."""
    definition = _comfyui_image_definition()
    definition["workflow"]["20"] = {"class_type": "LoadImage", "inputs": {"image": "draft.png"}}
    definition["bindings"]["reference_images"] = [{"node": "20", "input": "image", "class_type": "LoadImage"}]
    session_factory = async_sessionmaker(file_db_engine, expire_on_commit=False)
    async with session_factory() as session:
        endpoint = await CustomEndpointRepository(session).create(
            definition=definition,
            kind="comfyui",
            schema_version="1.0.0",
            media_type="image",
            display_name="示例图像 workflow",
        )
        provider = await CustomProviderRepository(session).create_provider(
            display_name="本地 ComfyUI",
            discovery_format="openai",
            base_url="https://comfy.test",
            api_key="sk-secret-key-1234",
            models=[
                {
                    "model_id": "flux-workflow",
                    "display_name": "flux-workflow",
                    "endpoint": make_endpoint_key(endpoint.id),
                    "is_enabled": True,
                    "is_default": True,
                }
            ],
        )
        await session.commit()
        return {"provider_id": make_provider_id(provider.id), "model_id": "flux-workflow"}


@pytest.fixture
async def stored_provider(file_db_engine) -> dict[str, Any]:
    """Custom-provider rows for database-credential tests."""
    session_factory = async_sessionmaker(file_db_engine, expire_on_commit=False)
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


async def _only_api_call(session_factory: async_sessionmaker) -> ApiCall:
    """The single ledger entry recorded by this run."""
    async with session_factory() as session:
        rows = (await session.execute(select(ApiCall))).scalars().all()
    assert len(rows) == 1
    return rows[0]


def _drain(client: TestClient, trial_runs: TrialRunManager, run_id: str) -> None:
    """Wait for background runs to become terminal.

    ``TestClient`` runs applications on another thread's event loop; use its portal on that loop to
    ``await manager.wait()``, synchronizing events rather than polling wall clocks.
    """
    assert client.portal is not None
    run = client.portal.call(trial_runs.wait, run_id)
    assert run is not None
    assert run.status.value in ("succeeded", "failed")
