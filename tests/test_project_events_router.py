import asyncio
from types import SimpleNamespace

import pytest

from server.routers import project_events as project_events_router


class _FakeRequest:
    def __init__(self, app):
        self.app = app

    async def is_disconnected(self):
        return False


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


@pytest.mark.asyncio
async def test_stream_project_events_emits_snapshot_and_changes():
    service = _FakeService()
    app = SimpleNamespace(state=SimpleNamespace(project_event_service=service))
    request = _FakeRequest(app)

    subscription = await project_events_router._project_events_subscription("demo", request)
    stream = project_events_router.stream_project_events("demo", request, subscription=subscription)

    snapshot_event = await anext(stream)
    changes_event = await anext(stream)
    await stream.aclose()

    assert snapshot_event.event == "snapshot"
    assert snapshot_event.data["fingerprint"] == "fp-0"

    assert changes_event.event == "changes"
    assert changes_event.data["batch_id"] == "batch-1"
    assert service.unsubscribed is True
