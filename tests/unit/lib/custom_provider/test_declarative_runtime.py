from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from arcreel_market_core.endpoint_definition import build_context
from arcreel_market_core.video_backend_contract import ProviderJobStatus, ProviderResponseStage, ResumeExpiredError
from lib.backends.artifact_download_guard import artifact_http_client
from lib.custom_provider.declarative_runtime import (
    DeclarativeRuntime,
    DeclarativeRuntimeError,
    extract_text,
)
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http, request_json


def _definition() -> dict:
    return {
        "auth": {"headers": {"Authorization": "Bearer {{ api_key }}"}},
        "submit": {
            "method": "POST",
            "url": "{{ base_url }}/v1/jobs",
            "body": {"model": "{{ model }}", "prompt": "{{ prompt }}"},
            "extract": {"task_id": ["$.operation_id"], "error": ["$.error"]},
        },
        "poll": {
            "method": "GET",
            "url": "{{ base_url }}/v1/jobs/{{ task_id }}",
            "extract": {"status": ["$.state"], "error": ["$.error"], "result_id": ["$.result_id"]},
        },
        "result": {
            "method": "GET",
            "url": "{{ base_url }}/v1/results/{{ result_id }}",
            "extract": {"artifact_url": ["$.artifact.url"]},
        },
        "status_map": {
            "pending": "queued",
            "working": "running",
            "done": "succeeded",
            "failed": "failed",
        },
    }


def _context() -> dict[str, object]:
    return build_context(
        {
            "api_key": "secret",
            "base_url": "https://relay.test",
            "model": "generic-model",
            "prompt": "make an artifact",
        },
        {},
        None,
    )


class TestDeclarativeRuntimeContract:
    async def test_submit_poll_result_and_download_do_not_require_media_semantics(self, tmp_path: Path):
        recorded: list[tuple[ProviderResponseStage, object]] = []

        async def record(stage: ProviderResponseStage, body: object) -> None:
            recorded.append((stage, body))

        with capture_http() as router, bounded_poll_clock():
            submit = router.post("https://relay.test/v1/jobs").mock(
                return_value=httpx.Response(200, json={"operation_id": "op-7"})
            )
            poll = router.get("https://relay.test/v1/jobs/op-7").mock(
                side_effect=[
                    httpx.Response(200, json={"state": "working"}),
                    httpx.Response(200, json={"state": "done", "result_id": "result-9"}),
                ]
            )
            result = router.get("https://relay.test/v1/results/result-9").mock(
                return_value=httpx.Response(200, json={"artifact": {"url": "https://relay.test/files/artifact.bin"}})
            )
            download = router.get("https://relay.test/files/artifact.bin").mock(
                return_value=httpx.Response(200, content=b"artifact")
            )

            runtime = DeclarativeRuntime(
                api_key="secret",
                base_url="https://relay.test",
                definition=_definition(),
                provider="generic-provider",
            )
            context = _context()
            async with artifact_http_client(follow_redirects=True) as client:
                job_id = await runtime.submit(client, context, on_response=record)
                outcome = await runtime.poll_for_result(
                    client,
                    job_id,
                    context=context,
                    poll_timeout_seconds=3600,
                    is_resume=False,
                    on_response=record,
                )
                artifact_url = extract_text(
                    _definition()["result"]["extract"]["artifact_url"],
                    outcome.final_state.body,
                )
                assert artifact_url is not None
                await runtime.download_artifact(
                    client,
                    artifact_url,
                    tmp_path / "artifact.bin",
                    context,
                    max_wait=3600,
                    trusted_origins=outcome.trusted_origins,
                    max_bytes=1024,
                    label="generic-provider artifact",
                )

        assert job_id == "op-7"
        assert outcome.poll_state.body == {"state": "done", "result_id": "result-9"}
        assert outcome.final_state.status is ProviderJobStatus.SUCCEEDED
        assert outcome.final_stage == "result"
        assert (tmp_path / "artifact.bin").read_bytes() == b"artifact"
        assert request_json(submit.calls.last.request) == {
            "model": "generic-model",
            "prompt": "make an artifact",
        }
        assert poll.call_count == 2
        assert result.call_count == 1
        assert download.call_count == 1
        assert [stage for stage, _body in recorded] == ["submit", "poll", "poll", "result"]

    async def test_resume_poll_404_is_classified_as_expired(self, tmp_path: Path):
        async def record(_stage: ProviderResponseStage, _body: object) -> None:
            return None

        with capture_http() as router:
            poll = router.get("https://relay.test/v1/jobs/op-old").mock(
                return_value=httpx.Response(404, json={"error": "gone"})
            )
            runtime = DeclarativeRuntime(
                api_key="secret",
                base_url="https://relay.test",
                definition=_definition(),
                provider="generic-provider",
            )
            async with artifact_http_client(follow_redirects=True) as client:
                with pytest.raises(ResumeExpiredError):
                    await runtime.poll_for_result(
                        client,
                        "op-old",
                        context=_context(),
                        poll_timeout_seconds=3600,
                        is_resume=True,
                        on_response=record,
                    )

        assert poll.call_count == 1

    async def test_result_request_404_is_retried_before_success(self, tmp_path: Path):
        async def record(_stage: ProviderResponseStage, _body: object) -> None:
            return None

        with capture_http() as router, bounded_poll_clock():
            router.get("https://relay.test/v1/jobs/op-7").mock(
                return_value=httpx.Response(200, json={"state": "done", "result_id": "result-9"})
            )
            result = router.get("https://relay.test/v1/results/result-9").mock(
                side_effect=[
                    httpx.Response(404, json={"error": "not ready"}),
                    httpx.Response(200, json={"artifact": {"url": "https://relay.test/files/artifact.bin"}}),
                ]
            )
            runtime = DeclarativeRuntime(
                api_key="secret",
                base_url="https://relay.test",
                definition=_definition(),
                provider="generic-provider",
            )
            async with artifact_http_client(follow_redirects=True) as client:
                outcome = await runtime.poll_for_result(
                    client,
                    "op-7",
                    context=_context(),
                    poll_timeout_seconds=3600,
                    is_resume=True,
                    on_response=record,
                )

        assert result.call_count == 2
        assert outcome.final_state.status is ProviderJobStatus.SUCCEEDED
        assert outcome.final_state.body == {"artifact": {"url": "https://relay.test/files/artifact.bin"}}

    async def test_invalid_json_is_classified_as_response_extract_failure(self, tmp_path: Path):
        async def record(_stage: ProviderResponseStage, _body: object) -> None:
            return None

        with capture_http() as router:
            router.post("https://relay.test/v1/jobs").mock(
                return_value=httpx.Response(200, text="<html>not json</html>")
            )
            runtime = DeclarativeRuntime(
                api_key="secret",
                base_url="https://relay.test",
                definition=_definition(),
                provider="generic-provider",
            )
            async with artifact_http_client(follow_redirects=True) as client:
                with pytest.raises(DeclarativeRuntimeError) as caught:
                    await runtime.submit(client, _context(), on_response=record)

        assert caught.value.code == "declarative_response_extract_failed"
