from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from arcreel_market_core.endpoint_definition import build_context
from lib.backends.artifact_download_guard import artifact_http_client
from lib.custom_provider.declarative_runtime import DeclarativeRuntime, extract_text
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http


class _FileBackend:
    """A non-video backend assembled from the declarative runtime engine."""

    def __init__(self, *, definition: Mapping[str, Any]) -> None:
        self._definition = definition
        self._runtime = DeclarativeRuntime(
            api_key="secret",
            base_url="https://relay.test",
            definition=definition,
            provider="file-provider",
        )

    async def generate(self, output_path: Path) -> tuple[str, str]:
        context = build_context(
            {
                "api_key": "secret",
                "base_url": "https://relay.test",
                "model": "file-model",
                "prompt": "make a file",
            },
            {},
            None,
        )

        async def record(_stage, _body) -> None:
            return None

        async with artifact_http_client(follow_redirects=True) as client:
            job_id = await self._runtime.submit(client, context, on_response=record)
            outcome = await self._runtime.poll_for_result(
                client,
                job_id,
                context=context,
                poll_timeout_seconds=3600,
                is_resume=False,
                on_response=record,
            )
            artifact_url = extract_text(
                self._definition["result"]["extract"]["artifact_url"],
                outcome.final_state.body,
            )
            assert artifact_url is not None
            await self._runtime.download_artifact(
                client,
                artifact_url,
                output_path,
                context,
                max_wait=3600,
                trusted_origins=outcome.trusted_origins,
                max_bytes=1024,
                label="file-provider artifact",
            )
            return job_id, artifact_url


def _definition() -> dict:
    return {
        "auth": {"headers": {"Authorization": "Bearer {{ api_key }}"}},
        "submit": {
            "method": "POST",
            "url": "{{ base_url }}/files",
            "body": {"model": "{{ model }}", "prompt": "{{ prompt }}"},
            "extract": {"task_id": ["$.id"]},
        },
        "poll": {
            "method": "GET",
            "url": "{{ base_url }}/files/{{ task_id }}",
            "extract": {"status": ["$.status"], "result_id": ["$.result_id"]},
        },
        "result": {
            "method": "GET",
            "url": "{{ base_url }}/files/{{ task_id }}/result/{{ result_id }}",
            "extract": {"artifact_url": ["$.url"]},
        },
        "status_map": {"queued": "queued", "running": "running", "done": "succeeded", "failed": "failed"},
    }


async def test_a_media_neutral_backend_can_be_assembled_on_the_runtime_engine(tmp_path: Path):
    with capture_http() as router, bounded_poll_clock():
        router.post("https://relay.test/files").mock(return_value=httpx.Response(200, json={"id": "file-1"}))
        router.get("https://relay.test/files/file-1").mock(
            return_value=httpx.Response(200, json={"status": "done", "result_id": "result-2"})
        )
        router.get("https://relay.test/files/file-1/result/result-2").mock(
            return_value=httpx.Response(200, json={"url": "https://relay.test/download/file-1.bin"})
        )
        router.get("https://relay.test/download/file-1.bin").mock(
            return_value=httpx.Response(200, content=b"file bytes")
        )

        job_id, artifact_url = await _FileBackend(definition=_definition()).generate(tmp_path / "result.bin")

    assert job_id == "file-1"
    assert artifact_url == "https://relay.test/download/file-1.bin"
    assert (tmp_path / "result.bin").read_bytes() == b"file bytes"
