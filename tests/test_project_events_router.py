import asyncio
import json
from types import SimpleNamespace

import pytest

from server.routers import project_events as project_events_router


class _FakeRequest:
    def __init__(self, app, disconnect_after: int):
        self.app = app
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self):
        self._calls += 1
        return self._calls > self._disconnect_after


class _FakeService:
    def __init__(self):
        self.unsubscribed = False
        self.queue = None

    async def subscribe(self, project_name: str):
        queue = asyncio.Queue()
        await queue.put(
            (
                "changes",
                {
                    "project_name": project_name,
                    "batch_id": "batch-1",
                    "fingerprint": "fp-1",
                    "generated_at": "2026-03-01T00:00:00Z",
                    "source": "filesystem",
                    "changes": [],
                },
            )
        )
        self.queue = queue
        return queue, {
            "project_name": project_name,
            "fingerprint": "fp-0",
            "generated_at": "2026-03-01T00:00:00Z",
        }

    async def unsubscribe(self, project_name: str, queue):
        self.unsubscribed = True


def _decode_sse(chunk):
    text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
    event = ""
    payload = None
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            payload = json.loads(line[len("data: "):])
    return event, payload


@pytest.mark.asyncio
async def test_stream_project_events_emits_snapshot_and_changes():
    service = _FakeService()
    app = SimpleNamespace(state=SimpleNamespace(project_event_service=service))
    request = _FakeRequest(app, disconnect_after=1)

    response = await project_events_router.stream_project_events("demo", request)

    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)

    assert len(chunks) >= 2
    snapshot_event, snapshot_payload = _decode_sse(chunks[0])
    assert snapshot_event == "snapshot"
    assert snapshot_payload["fingerprint"] == "fp-0"

    changes_event, changes_payload = _decode_sse(chunks[1])
    assert changes_event == "changes"
    assert changes_payload["batch_id"] == "batch-1"
    assert service.unsubscribed is True
