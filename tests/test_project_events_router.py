import contextlib
from types import SimpleNamespace

import pytest

from server.routers import project_events as project_events_router


class _FakeRequest:
    def __init__(self, app, *, disconnected: bool = False):
        self.app = app
        self._disconnected = disconnected

    async def is_disconnected(self):
        return self._disconnected


class _FakePM:
    def get_project_path(self, project_name: str):
        return f"/projects/{project_name}"


class _FakeService:
    def __init__(self):
        self.unsubscribed = False
        self.pm = _FakePM()

    @contextlib.asynccontextmanager
    async def stream_events(self, project_name: str, *, idle_timeout: float = 1.0):
        async def _iter():
            yield (
                "snapshot",
                {
                    "project_name": project_name,
                    "fingerprint": "fp-0",
                    "generated_at": "2026-03-01T00:00:00Z",
                },
            )
            yield (
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
            # 之后进入空闲;消费方在 _idle 上轮询 is_disconnected。
            while True:
                yield {"type": "_idle"}

        try:
            yield _iter()
        finally:
            self.unsubscribed = True


@pytest.mark.asyncio
async def test_stream_project_events_emits_snapshot_and_changes():
    service = _FakeService()
    app = SimpleNamespace(state=SimpleNamespace(project_event_service=service))
    request = _FakeRequest(app)

    resolved = await project_events_router._project_events_service("demo", request)
    assert resolved is service

    stream = project_events_router.stream_project_events("demo", request, _user={"sub": "testuser"}, service=service)

    snapshot_event = await anext(stream)
    changes_event = await anext(stream)
    await stream.aclose()

    assert snapshot_event.event == "snapshot"
    assert snapshot_event.data["fingerprint"] == "fp-0"

    assert changes_event.event == "changes"
    assert changes_event.data["batch_id"] == "batch-1"
    assert service.unsubscribed is True


@pytest.mark.asyncio
async def test_stream_project_events_breaks_on_disconnect():
    service = _FakeService()
    app = SimpleNamespace(state=SimpleNamespace(project_event_service=service))
    request = _FakeRequest(app, disconnected=True)

    stream = project_events_router.stream_project_events("demo", request, _user={"sub": "testuser"}, service=service)

    # snapshot + changes 后进入 _idle,断线被自检命中 → 流结束并注销。
    assert (await anext(stream)).event == "snapshot"
    assert (await anext(stream)).event == "changes"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert service.unsubscribed is True
