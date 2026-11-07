"""Connection tests: execute one real generation path, account, write files, enforce TTL, and cancel.

respx intercepts all outbound traffic; real ``Ledger`` plus an in-memory DB handles accounting. This issue requires using the production path;
replacing backend or ledger would reduce that criterion to a substitute having been called.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from arcreel_market_core.video_backend_contract import VideoCapabilities, VideoGenerationRequest
from lib.billing.ledger import Ledger
from lib.custom_provider.declarative_backend import DeclarativeVideoBackend
from lib.custom_provider.endpoint_test import (
    TRIAL_RUN_TTL_SECONDS,
    EndpointTestCredentials,
    EndpointTestParameters,
    TrialRunBusyError,
    TrialRunManager,
    TrialRunStatus,
    TrialRunTarget,
    declarative_target,
    model_ref_target,
    provider_from_base_url,
)
from lib.db.models.api_call import ApiCall
from tests.factories import custom_endpoint_definition, image_endpoint_definition
from tests.fakes import PNG_BYTES, bounded_poll_clock
from tests.http_capture import capture_http

PARAMETERS = EndpointTestParameters(model="video-x", prompt="纸船顺流而下", duration_seconds=5)
CREDENTIALS = EndpointTestCredentials(base_url="https://relay.test", api_key="sk-secret-key-1234")


@pytest.fixture
def trial_runs(tmp_path: Path, db_factory: async_sessionmaker) -> TrialRunManager:
    return TrialRunManager(
        root=tmp_path / "trial_runs",
        ledger=Ledger(session_factory=db_factory),
        read_poll_timeout=_fixed_timeout,
    )


async def _fixed_timeout() -> int:
    return 600


def _target():
    return declarative_target(custom_endpoint_definition(), CREDENTIALS, PARAMETERS)


def _mock_successful_run(router) -> None:
    router.post("https://relay.test/v1/video/create").mock(return_value=httpx.Response(200, json={"task_id": "job-42"}))
    router.get("https://relay.test/v1/video/fetch/job-42").mock(
        side_effect=[
            httpx.Response(200, json={"status": "processing"}),
            httpx.Response(200, json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"}),
        ]
    )
    router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))


async def _await_terminal(trial_runs: TrialRunManager, run_id: str):
    """Wait for the background run to stop and retrieve terminal state."""
    run = await trial_runs.wait(run_id)
    assert run is not None
    assert run.status in (TrialRunStatus.SUCCEEDED, TrialRunStatus.FAILED)
    return run


def _mock_pending_run(router) -> asyncio.Event:
    """A run that stays processing; the returned event is set when submission is sent.

    The pending accounting row exists before this request (``run.api_call_id`` is assigned then too), so this handshake
    establishes that the row cancellation must update exists, without guessing how many event-loop yields are needed.

    The first poll returns processing; later polls block on the request until run cancellation. ``bounded_poll_clock`` replaces polling
    intervals with nonyielding waits and global ``time.monotonic``. Repeated processing responses would keep polling at other test yield points,
    consuming timeout budget together with event-loop table reads and failing prematurely with timeout.
    """
    submitted = asyncio.Event()
    parked = asyncio.Event()
    fetches: list[httpx.Request] = []

    def _create(_request: httpx.Request) -> httpx.Response:
        submitted.set()
        return httpx.Response(200, json={"task_id": "job-42"})

    async def _fetch(request: httpx.Request) -> httpx.Response:
        if fetches:
            await parked.wait()
        fetches.append(request)
        return httpx.Response(200, json={"status": "processing"})

    router.post("https://relay.test/v1/video/create").mock(side_effect=_create)
    router.get("https://relay.test/v1/video/fetch/job-42").mock(side_effect=_fetch)
    return submitted


def _hold_result_write(monkeypatch) -> tuple[asyncio.Event, threading.Event]:
    """Pause terminal-result disk writing inside its thread; return write-started and release-write signals.

    Real disk-writing windows last only milliseconds; pausing makes cancellation reliably land within that window.
    """
    loop = asyncio.get_running_loop()
    writing = asyncio.Event()
    release = threading.Event()
    write_text = Path.write_text

    def _held(path: Path, *args, **kwargs) -> int:
        if path.name == "result.json":
            loop.call_soon_threadsafe(writing.set)
            if not release.wait(timeout=10):
                raise TimeoutError("result write was not released")
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _held)
    return writing, release


class _TierAwareBackend(DeclarativeVideoBackend):
    """A declarative endpoint with narrowed 1080p capabilities: no last frame, adaptive-only first-frame ratio, otherwise defined tier capabilities.

    Outbound traffic still follows the real declarative-endpoint path; only ``video_capabilities_for_tier`` is added, precisely the optional
    member production ``resolve_video_capabilities`` probes.
    """

    def video_capabilities_for_tier(self, service_tier: str, resolution: str | None = None) -> VideoCapabilities:
        caps = self.video_capabilities
        if resolution == "1080p":
            return replace(caps, last_frame=False, first_frame_ratio_adaptive_only=True)
        return caps


def _tier_aware_target(parameters: EndpointTestParameters) -> TrialRunTarget:
    definition = custom_endpoint_definition(
        inputs={
            "first_frame": {"source": "start_image", "encoding": "data_uri"},
            "last_frame": {"source": "end_image", "encoding": "data_uri"},
        },
        capabilities={"first_frame": True, "last_frame": True},
    )
    definition["submit"]["body"]["last_image"] = "{{ inputs.last_frame }}"
    definition["submit"]["body"]["ratio"] = "{{ aspect_ratio }}"
    target = declarative_target(definition, CREDENTIALS, parameters)

    async def build() -> _TierAwareBackend:
        return _TierAwareBackend(
            api_key=CREDENTIALS.api_key,
            base_url=CREDENTIALS.base_url,
            model=parameters.model,
            definition=definition,
            provider=target.provider,
        )

    return replace(target, build_backend=build)


IMAGE_PARAMETERS = EndpointTestParameters(model="gpt-image-2", prompt="黄昏的灯塔")


def _image_target():
    return declarative_target(image_endpoint_definition(), CREDENTIALS, IMAGE_PARAMETERS)


def _image_task(status: str, **data: object) -> httpx.Response:
    return httpx.Response(200, json={"code": 200, "data": {"status": status, **data}})


def _mock_image_submit(router) -> None:
    router.post("https://relay.test/v1/images/generations").mock(
        return_value=httpx.Response(200, json={"code": 200, "data": [{"status": "submitted", "task_id": "task_9"}]})
    )


class TestImageDefinitionTrialRun:
    async def test_an_image_definition_runs_to_an_image_artifact(
        self, trial_runs: TrialRunManager, db_factory: async_sessionmaker
    ):
        with capture_http() as router, bounded_poll_clock():
            _mock_image_submit(router)
            router.get("https://relay.test/v1/tasks/task_9").mock(
                side_effect=[
                    _image_task("processing"),
                    _image_task("completed", result={"images": [{"url": ["https://cdn.test/img/a.png"]}]}),
                ]
            )
            router.get("https://cdn.test/img/a.png").mock(return_value=httpx.Response(200, content=PNG_BYTES))
            started = await trial_runs.start(_image_target(), IMAGE_PARAMETERS)
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED
        assert run.media_type == "image"
        assert run.has_artifact
        assert trial_runs.artifact_path(run.id).read_bytes() == PNG_BYTES
        assert run.extractions["poll"]["image_url"] == "https://cdn.test/img/a.png"
        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall))).scalars().all()
        assert [row.call_type for row in rows] == ["image"]

    async def test_a_base64_image_lands_as_the_artifact_and_is_reported_by_size(self, trial_runs: TrialRunManager):
        definition = image_endpoint_definition()
        definition["poll"]["extract"]["image_b64"] = ["$.data.result.images[0].b64_json"]
        encoded = base64.b64encode(PNG_BYTES * 64).decode("ascii")
        with capture_http() as router, bounded_poll_clock():
            _mock_image_submit(router)
            router.get("https://relay.test/v1/tasks/task_9").mock(
                return_value=_image_task("completed", result={"images": [{"b64_json": encoded}]})
            )
            started = await trial_runs.start(
                declarative_target(definition, CREDENTIALS, IMAGE_PARAMETERS), IMAGE_PARAMETERS
            )
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED
        assert trial_runs.artifact_path(run.id).read_bytes() == PNG_BYTES * 64
        assert run.extractions["poll"]["image_bytes"] == len(PNG_BYTES * 64)
        assert run.video_url is None
        assert encoded not in json.dumps(run.extractions)

    @pytest.mark.parametrize(
        ("poll_body", "reason"),
        [
            (_image_task("failed", error={"message": "content policy violation"}), "content policy violation"),
            (_image_task("cancelled"), "cancelled"),
        ],
    )
    async def test_a_provider_side_failure_lands_as_a_failed_image_run(
        self, trial_runs: TrialRunManager, poll_body: httpx.Response, reason: str
    ):
        with capture_http() as router, bounded_poll_clock():
            _mock_image_submit(router)
            router.get("https://relay.test/v1/tasks/task_9").mock(return_value=poll_body)
            started = await trial_runs.start(_image_target(), IMAGE_PARAMETERS)
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.FAILED
        assert run.media_type == "image"
        assert not run.has_artifact
        assert reason in (run.error or "")

    async def test_reference_images_beyond_the_effective_limit_are_rejected_before_billing(
        self, trial_runs: TrialRunManager, db_factory: async_sessionmaker, tmp_path: Path
    ):
        """Connection tests share production image reference limits: qwen-image-2.0 declares 3 images; reject 4 before submission/accounting."""
        from lib.backends.image_backends.dashscope import DashScopeImageBackend

        parameters = EndpointTestParameters(model="qwen-image-2.0", prompt="黄昏的灯塔")

        async def build() -> DashScopeImageBackend:
            return DashScopeImageBackend(api_key="sk-test", model=parameters.model)

        refs = []
        for index in range(4):
            ref = tmp_path / f"ref{index}.png"
            ref.write_bytes(PNG_BYTES)
            refs.append(ref)
        target = TrialRunTarget(provider="dashscope", model=parameters.model, build_backend=build, media_type="image")
        with capture_http() as router, bounded_poll_clock():
            started = await trial_runs.start(target, parameters, assets={"reference_images": refs})
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.FAILED
        assert "image_reference_images_exceeded" in (run.error or "")
        assert router.calls.call_count == 0
        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall))).scalars().all()
        assert rows == []


class TestTrialRun:
    async def test_runs_the_production_path_to_a_terminal_state(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED
        assert run.video_url == "https://relay.test/files/job-42.mp4"
        assert trial_runs.artifact_path(run.id) is not None
        assert trial_runs.artifact_path(run.id).read_bytes() == b"video"

    async def test_records_the_call_in_the_ledger(self, trial_runs: TrialRunManager, db_factory: async_sessionmaker):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            run = await _await_terminal(trial_runs, started.id)

        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall))).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == run.api_call_id
        assert rows[0].status == "success"
        assert rows[0].call_type == "video"
        # Endpoint trials have no task backlink; purpose identifies the source.
        assert rows[0].task_id is None
        assert rows[0].purpose == "endpoint_trial"
        # Inline credentials have no provider identity; account under the base_url host so bills identify where money went.
        assert rows[0].provider == "relay.test"

    async def test_result_carries_the_rendered_request_responses_and_extractions(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(
                _target(), PARAMETERS, request_preview={"method": "POST", "url": "https://relay.test/v1/video/create"}
            )
            run = await _await_terminal(trial_runs, started.id)

        assert run.request == {"method": "POST", "url": "https://relay.test/v1/video/create"}
        assert run.submit_response == {"task_id": "job-42"}
        assert len(run.poll_responses) == 2
        assert run.extractions["submit"]["task_id"] == "job-42"
        assert run.extractions["poll"]["status"] == "succeeded"

    async def test_submit_retry_keeps_the_successful_submit_response(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                side_effect=[
                    httpx.Response(429, json={"error": "busy"}),
                    httpx.Response(200, json={"task_id": "job-42"}),
                ]
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                side_effect=[
                    httpx.Response(200, json={"status": "processing"}),
                    httpx.Response(
                        200,
                        json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"},
                    ),
                ]
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            started = await trial_runs.start(_target(), PARAMETERS)
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED, run.error
        assert run.submit_response == {"task_id": "job-42"}
        assert run.poll_responses == [
            {"status": "processing"},
            {"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"},
        ]

    async def test_result_response_is_not_counted_as_polling(self, trial_runs: TrialRunManager):
        definition = custom_endpoint_definition()
        definition["poll"]["extract"] = {"status": ["$.status"], "result_id": ["$.result_id"]}
        definition["result"] = {
            "method": "GET",
            "url": "{{ base_url }}/v1/video/result/{{ result_id }}",
            "extract": {"video_url": ["$.video_url"]},
        }
        poll_body = {"status": "completed", "result_id": "result-9"}
        result_body = {"video_url": "https://relay.test/files/job-42.mp4"}

        with capture_http() as router, bounded_poll_clock(step=1.0):
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(200, json=poll_body)
            )
            router.get("https://relay.test/v1/video/result/result-9").mock(
                return_value=httpx.Response(200, json=result_body)
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            target = declarative_target(definition, CREDENTIALS, PARAMETERS)
            started = await trial_runs.start(target, PARAMETERS)
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED, run.error
        assert run.poll_responses == [poll_body]
        assert run.result_response == result_body
        assert run.extractions["poll"]["status"] == "succeeded"
        assert run.extractions["result"]["video_url"] == result_body["video_url"]

    async def test_terminal_result_is_read_from_disk(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, started.id)

        stored = json.loads((trial_runs.root / started.id / "result.json").read_text(encoding="utf-8"))
        assert stored["status"] == "succeeded"
        assert trial_runs.get(started.id).video_url == stored["video_url"]

    async def test_a_declared_capability_violation_is_rejected_before_billing(
        self, trial_runs: TrialRunManager, db_factory: async_sessionmaker, tmp_path: Path
    ):
        """Production gates capabilities before accounting; connection tests share that gate, sending/accounting nothing for invalid requests."""
        face = tmp_path / "face.png"
        face.write_bytes(b"png")
        with capture_http() as router, bounded_poll_clock():
            # The fixture declares only first_frame; references are unavailable (max_reference_images=0).
            started = await trial_runs.start(_target(), PARAMETERS, assets={"reference_images": [face]})
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.FAILED
        assert router.calls.call_count == 0
        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall))).scalars().all()
        assert rows == []

    async def test_a_reference_driven_endpoint_passes_the_gate_without_a_first_frame(
        self, tmp_path: Path, trial_runs: TrialRunManager
    ):
        """Reference-driven endpoints such as S2V lack first frames but carry references; gates must inspect the whole slot plan instead of classifying as pure text-to-video."""
        definition = custom_endpoint_definition(
            inputs={"refs": {"source": "reference_images", "encoding": "data_uri"}},
            capabilities={"text_to_video": False, "max_reference_images": 3},
        )
        definition["submit"]["body"] = {
            "model": "{{ model }}",
            "prompt": "{{ prompt }}",
            "subject_reference": "{{ inputs.refs }}",
        }
        face = tmp_path / "face.png"
        face.write_bytes(b"png")

        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(
                declarative_target(definition, CREDENTIALS, PARAMETERS),
                PARAMETERS,
                assets={"reference_images": [face]},
            )
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED, run.error

    async def test_a_first_frame_request_sends_the_adaptive_ratio_when_the_endpoint_requires_it(
        self, tmp_path: Path, trial_runs: TrialRunManager, db_factory: async_sessionmaker
    ):
        """Endpoints declaring first_frame_ratio_adaptive_only accept only adaptive, while the ledger still records user-entered ratio intent."""
        definition = custom_endpoint_definition(
            capabilities={"first_frame": True, "first_frame_ratio_adaptive_only": True}
        )
        definition["submit"]["body"]["ratio"] = "{{ aspect_ratio }}"
        frame = tmp_path / "frame.png"
        frame.write_bytes(b"png")

        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            submit = router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            started = await trial_runs.start(
                declarative_target(definition, CREDENTIALS, PARAMETERS),
                PARAMETERS,
                assets={"start_image": frame},
            )
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED, run.error
        assert json.loads(submit.calls.last.request.content)["ratio"] == "adaptive"
        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall))).scalars().all()
        assert rows[0].aspect_ratio == PARAMETERS.aspect_ratio

    @pytest.mark.parametrize(
        ("resolution", "expected", "error_code"),
        [("1080p", "failed", "video_last_frame_unsupported"), ("720p", "succeeded", None)],
    )
    async def test_the_gate_reads_the_capabilities_narrowed_to_the_requested_resolution(
        self,
        tmp_path: Path,
        trial_runs: TrialRunManager,
        db_factory: async_sessionmaker,
        resolution: str,
        expected: str,
        error_code: str | None,
    ):
        """Tier-aware endpoints narrow by requested resolution; connection tests reject the same requests production rejects under narrowed capabilities."""
        parameters = replace(PARAMETERS, resolution=resolution)
        frame = tmp_path / "frame.png"
        frame.write_bytes(b"png")
        last = tmp_path / "last.png"
        last.write_bytes(b"png")

        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(
                _tier_aware_target(parameters), parameters, assets={"start_image": frame, "end_image": last}
            )
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus(expected), run.error
        if error_code is not None:
            assert (run.error or "").startswith(f"[{error_code}]")
        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall))).scalars().all()
        assert len(rows) == (1 if expected == "succeeded" else 0)

    async def test_the_adaptive_ratio_follows_the_capabilities_narrowed_to_the_requested_resolution(
        self, tmp_path: Path, trial_runs: TrialRunManager
    ):
        """Ratio overrides and capability gates share the narrowed result; endpoints declaring adaptive only at that resolution must still send adaptive."""
        parameters = replace(PARAMETERS, resolution="1080p")
        frame = tmp_path / "frame.png"
        frame.write_bytes(b"png")

        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            submit = router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            started = await trial_runs.start(_tier_aware_target(parameters), parameters, assets={"start_image": frame})
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED, run.error
        assert json.loads(submit.calls.last.request.content)["ratio"] == "adaptive"

    async def test_a_provider_failure_lands_as_a_failed_run(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"error": {"message": "quota exceeded"}})
            )
            started = await trial_runs.start(_target(), PARAMETERS)
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.FAILED
        assert "quota exceeded" in (run.error or "")

    async def test_a_backend_that_cannot_be_built_is_not_billed(
        self, trial_runs: TrialRunManager, db_factory: async_sessionmaker
    ):
        """Assembly failure sends no bytes and must not add a ledger call."""

        async def _explode():
            raise ValueError("自定义供应商 custom-9 不存在")

        target = TrialRunTarget(provider="custom-9", model="video-x", build_backend=_explode)
        started = await trial_runs.start(target, PARAMETERS)
        run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.FAILED
        assert run.api_call_id is None
        async with db_factory() as session:
            assert (await session.execute(select(ApiCall))).scalars().all() == []


class _RecordingBackend(DeclarativeVideoBackend):
    """Record the request actually received by the backend, retaining the real declarative outbound path."""

    requests: ClassVar[list[VideoGenerationRequest]] = []

    async def generate(self, request: VideoGenerationRequest):
        type(self).requests.append(request)
        return await super().generate(request)


class TestBuiltinUserAddedOverrides:
    """User-added models under built-in providers overlay user capability overrides in both connection tests and production gates (docs/adr/0104)."""

    @pytest.fixture
    def builtin_target(self):
        from lib.catalog import query as catalog

        def install(overrides: dict[str, object], *, declared_last_frame: bool) -> TrialRunTarget:
            catalog.install_user_added_models(
                [catalog.UserAddedModel("ark", PARAMETERS.model, "video", capability_overrides=overrides)]
            )
            definition = custom_endpoint_definition(
                inputs={
                    "first_frame": {"source": "start_image", "encoding": "data_uri"},
                    "last_frame": {"source": "end_image", "encoding": "data_uri"},
                },
                capabilities={"first_frame": True, "last_frame": declared_last_frame},
            )
            definition["submit"]["body"]["last_image"] = "{{ inputs.last_frame }}"
            _RecordingBackend.requests = []

            async def build() -> _RecordingBackend:
                return _RecordingBackend(
                    api_key=CREDENTIALS.api_key,
                    base_url=CREDENTIALS.base_url,
                    model=PARAMETERS.model,
                    definition=definition,
                    provider="ark",
                )

            return replace(model_ref_target("ark", PARAMETERS.model, resolver=None), build_backend=build)

        yield install
        catalog.install_user_added_models(())

    def _frames(self, tmp_path: Path) -> dict[str, Path]:
        frame = tmp_path / "frame.png"
        frame.write_bytes(b"png")
        last = tmp_path / "last.png"
        last.write_bytes(b"png")
        return {"start_image": frame, "end_image": last}

    async def test_relaxed_override_passes_and_rides_the_request(
        self, tmp_path: Path, trial_runs: TrialRunManager, builtin_target
    ):
        target = builtin_target({"last_frame": True, "max_reference_images": 4}, declared_last_frame=False)

        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(target, PARAMETERS, assets=self._frames(tmp_path))
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED, run.error
        caps = _RecordingBackend.requests[0].video_capabilities
        assert caps.last_frame is True
        assert caps.max_reference_images == 4

    async def test_narrowed_override_is_rejected_before_billing(
        self, tmp_path: Path, trial_runs: TrialRunManager, db_factory: async_sessionmaker, builtin_target
    ):
        target = builtin_target({"last_frame": False}, declared_last_frame=True)

        with capture_http() as router, bounded_poll_clock():
            started = await trial_runs.start(target, PARAMETERS, assets=self._frames(tmp_path))
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.FAILED
        assert (run.error or "").startswith("[video_last_frame_unsupported]")
        assert router.calls.call_count == 0
        async with db_factory() as session:
            assert (await session.execute(select(ApiCall))).scalars().all() == []


class TestConcurrencyAndCancel:
    async def test_a_second_run_for_the_same_user_is_refused(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            first = await trial_runs.start(_target(), PARAMETERS)
            with pytest.raises(TrialRunBusyError):
                await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, first.id)

    async def test_a_new_run_is_allowed_after_the_previous_one_finishes(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            first = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, first.id)
            _mock_successful_run(router)
            second = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, second.id)

        assert second.id != first.id

    async def test_cancel_settles_the_call_as_cancelled_and_frees_the_slot(
        self, trial_runs: TrialRunManager, db_factory: async_sessionmaker
    ):
        with capture_http() as router, bounded_poll_clock():
            submitted = _mock_pending_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            await submitted.wait()

            assert await trial_runs.cancel(started.id) is True
            # Release the slot immediately after cancellation so users can adjust parameters and retry.
            _mock_successful_run(router)
            retried = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, retried.id)

        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall).order_by(ApiCall.id))).scalars().all()
        assert [row.status for row in rows] == ["cancelled", "success"]
        # Canceled runs retain no result files; read APIs return 404 accordingly.
        assert trial_runs.get(started.id) is None

    async def test_cancelling_an_unknown_run_reports_nothing_to_cancel(self, trial_runs: TrialRunManager):
        assert await trial_runs.cancel("no-such-run") is False

    async def test_a_failed_settlement_still_frees_the_slot(self, trial_runs: TrialRunManager, monkeypatch):
        """If settlement raises after the run stops and task removal, unreleased slots stay busy forever, while repeated cancellation only returns 404."""

        async def _boom(*, call_id: int) -> None:
            raise RuntimeError("db down")

        with capture_http() as router, bounded_poll_clock():
            submitted = _mock_pending_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            await submitted.wait()

            monkeypatch.setattr(trial_runs._ledger, "resume_cancelled", _boom)
            with pytest.raises(RuntimeError):
                await trial_runs.cancel(started.id)

            monkeypatch.undo()
            _mock_successful_run(router)
            retried = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, retried.id)

    async def test_a_failed_directory_frees_the_slot(self, trial_runs: TrialRunManager, monkeypatch):
        """Result-directory creation failure must release the slot; otherwise later trials stay busy forever, with no task available to cancel."""

        def _boom(*_args, **_kwargs) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "mkdir", _boom)
        with pytest.raises(OSError, match="disk full"):
            await trial_runs.start(_target(), PARAMETERS)

        monkeypatch.undo()
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            retried = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, retried.id)

    async def test_shutdown_keeps_draining_after_a_failed_settlement(
        self, tmp_path: Path, file_db_factory: async_sessionmaker, monkeypatch
    ):
        """One run settlement failure must not interrupt shutdown, leaving later runs unstopped and skipping DB closure and subsequent steps."""
        # Two runs account concurrently: one shared in-memory connection lets one commit/rollback affect the other's uncommitted writes.
        # Use a file DB with separate connections here.
        trial_runs = TrialRunManager(
            root=tmp_path / "trial_runs",
            ledger=Ledger(session_factory=file_db_factory),
            read_poll_timeout=_fixed_timeout,
        )
        settled: list[int] = []
        original = trial_runs._ledger.resume_cancelled

        async def _boom_once(*, call_id: int) -> None:
            if not settled:
                settled.append(call_id)
                raise RuntimeError("db down")
            settled.append(call_id)
            await original(call_id=call_id)

        with capture_http() as router, bounded_poll_clock():
            submitted = _mock_pending_run(router)
            first = await trial_runs.start(_target(), PARAMETERS, user_id="user-a")
            await submitted.wait()
            submitted.clear()
            second = await trial_runs.start(_target(), PARAMETERS, user_id="user-b")
            await submitted.wait()

            monkeypatch.setattr(trial_runs._ledger, "resume_cancelled", _boom_once)
            await trial_runs.shutdown()

        assert len(settled) == 2
        assert trial_runs.get(first.id) is None
        assert trial_runs.get(second.id) is None

    async def test_shutdown_settles_every_active_run_as_cancelled(
        self, trial_runs: TrialRunManager, db_factory: async_sessionmaker
    ):
        """Process shutdown must not leave pending accounting rows owned by a process that will never return."""
        with capture_http() as router, bounded_poll_clock():
            submitted = _mock_pending_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            await submitted.wait()

            await trial_runs.shutdown()

        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall))).scalars().all()
        assert [row.status for row in rows] == ["cancelled"]
        assert trial_runs.get(started.id) is None

    async def test_a_cancel_after_the_provider_succeeded_keeps_the_result(
        self, trial_runs: TrialRunManager, db_factory: async_sessionmaker, monkeypatch
    ):
        """Cancellation arriving during disk writing after provider success must not delete results: payment occurred and accounting already succeeded."""
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            writing, release = _hold_result_write(monkeypatch)
            started = await trial_runs.start(_target(), PARAMETERS)
            await writing.wait()

            assert await trial_runs.cancel(started.id) is False
            release.set()
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED
        assert trial_runs.artifact_path(started.id) is not None
        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall))).scalars().all()
        assert [row.status for row in rows] == ["success"]

    async def test_shutdown_keeps_a_run_that_is_already_settling(
        self, trial_runs: TrialRunManager, db_factory: async_sessionmaker, monkeypatch
    ):
        """Shutdown draining a writing run waits for completion instead of deleting its directory as canceled."""
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            writing, release = _hold_result_write(monkeypatch)
            started = await trial_runs.start(_target(), PARAMETERS)
            await writing.wait()

            shutdown = asyncio.create_task(trial_runs.shutdown())
            await asyncio.sleep(0)
            release.set()
            await shutdown

        run = trial_runs.get(started.id)
        assert run is not None
        assert run.status is TrialRunStatus.SUCCEEDED
        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall))).scalars().all()
        assert [row.status for row in rows] == ["success"]

    @pytest.mark.parametrize("stop", ["cancel", "shutdown"])
    async def test_cancellation_during_backend_artifact_write_keeps_the_success(
        self, trial_runs: TrialRunManager, db_factory: async_sessionmaker, monkeypatch, stop: str
    ):
        loop = asyncio.get_running_loop()
        writing = asyncio.Event()
        release = threading.Event()
        write_bytes = Path.write_bytes

        def held_write(path: Path, data: bytes) -> int:
            loop.call_soon_threadsafe(writing.set)
            if not release.wait(timeout=10):
                raise TimeoutError("artifact write was not released")
            return write_bytes(path, data)

        monkeypatch.setattr(Path, "write_bytes", held_write)
        definition = image_endpoint_definition()
        definition["poll"]["extract"]["image_b64"] = ["$.data.result.images[0].b64_json"]
        with capture_http() as router, bounded_poll_clock():
            _mock_image_submit(router)
            router.get("https://relay.test/v1/tasks/task_9").mock(
                return_value=_image_task(
                    "completed", result={"images": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}
                )
            )
            started = await trial_runs.start(
                declarative_target(definition, CREDENTIALS, IMAGE_PARAMETERS), IMAGE_PARAMETERS
            )
            try:
                await writing.wait()
                stopping = asyncio.create_task(
                    trial_runs.cancel(started.id) if stop == "cancel" else trial_runs.shutdown()
                )
                await asyncio.sleep(0)
            finally:
                release.set()
            stopped = await stopping

        if stop == "cancel":
            assert stopped is False
        run = trial_runs.get(started.id)
        assert run is not None
        assert run.status is TrialRunStatus.SUCCEEDED
        assert trial_runs.artifact_path(started.id).read_bytes() == PNG_BYTES
        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall))).scalars().all()
        assert [row.status for row in rows] == ["success"]


class TestRunIdShape:
    """run_id is a caller-supplied path segment; nongenerated formats must never reach disk paths."""

    def test_a_traversal_shaped_run_id_reads_nothing(self, trial_runs: TrialRunManager):
        # Put result.json above root: ``root/../result.json`` resolves to it, exposing out-of-bounds reads.
        (trial_runs.root.parent / "result.json").write_text("{}", encoding="utf-8")

        assert trial_runs.get("..") is None
        assert trial_runs.artifact_path("..") is None

    async def test_a_generated_run_id_still_round_trips(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, started.id)

        assert trial_runs.get(started.id) is not None


class TestRetention:
    def test_results_older_than_the_ttl_are_purged(self, trial_runs: TrialRunManager):
        stale = trial_runs.root / "stale"
        stale.mkdir(parents=True)
        (stale / "result.json").write_text("{}", encoding="utf-8")
        fresh = trial_runs.root / "fresh"
        fresh.mkdir(parents=True)

        removed = trial_runs.purge_expired(now=time.time() + TRIAL_RUN_TTL_SECONDS + 1)

        assert removed == 2
        assert not stale.exists()

    def test_a_result_file_with_ill_typed_fields_reads_as_missing(self, trial_runs: TrialRunManager):
        """Malformed result files use controlled failure, yielding read-side 404 instead of response validation exploding into 500."""
        run_id = "0" * 32
        (trial_runs.root / run_id).mkdir(parents=True)
        (trial_runs.root / run_id / "result.json").write_text(
            json.dumps({"id": run_id, "status": "succeeded", "finished_at": "invalid"}), encoding="utf-8"
        )

        assert trial_runs.get(run_id) is None

    async def test_a_result_past_the_ttl_is_no_longer_readable(self, trial_runs: TrialRunManager):
        """The read API checks expiry itself; servers without new runs for a long time do not trigger cleanup."""
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, started.id)

        assert trial_runs.get(started.id) is not None
        aged = time.time() - TRIAL_RUN_TTL_SECONDS - 1
        os.utime(trial_runs.root / started.id, (aged, aged))

        assert trial_runs.get(started.id) is None
        assert trial_runs.artifact_path(started.id) is None

    def test_a_result_within_the_ttl_survives(self, trial_runs: TrialRunManager):
        recent = trial_runs.root / "recent"
        recent.mkdir(parents=True)

        assert trial_runs.purge_expired() == 0
        assert recent.exists()

    def test_provider_falls_back_to_the_host_of_the_base_url(self):
        assert provider_from_base_url("https://relay.test/v1") == "relay.test"
