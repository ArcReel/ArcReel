import contextlib
from types import SimpleNamespace

import pytest

from lib.infra.api_errors import BadRequestError, NotFoundError
from server.routers import project_events as project_events_router


class _FakeRequest:
    def __init__(self, app, *, disconnect_after: int | None = None):
        """Check disconnection at the loop top, calling is_disconnected once before each anext.

        ``disconnect_after`` = None  → never disconnect
        ``disconnect_after`` = N     → first N calls False, then True from call N+1
        """
        self.app = app
        self._disconnect_after = disconnect_after
        self._calls = 0

    async def is_disconnected(self):
        self._calls += 1
        if self._disconnect_after is None:
            return False
        return self._calls > self._disconnect_after


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
            # Then idle; consumers poll is_disconnected on _idle.
            while True:
                yield {"type": "_idle"}

        try:
            yield _iter()
        finally:
            self.unsubscribed = True


@pytest.mark.asyncio
async def test_project_events_service_raises_not_found_when_project_missing():
    """Missing projects (FileNotFoundError) become NotFoundError, raised before streaming begins."""

    class _MissingPM:
        def get_project_path(self, project_name: str):
            raise FileNotFoundError(project_name)

    service = SimpleNamespace(pm=_MissingPM())
    app = SimpleNamespace(state=SimpleNamespace(project_event_service=service))
    request = _FakeRequest(app)

    with pytest.raises(NotFoundError):
        await project_events_router._project_events_service("missing", request)


@pytest.mark.asyncio
async def test_project_events_service_raises_bad_request_for_invalid_project_name():
    """Invalid project names (path traversal, etc., ValueError) become BadRequestError, not missing projects."""

    class _InvalidNamePM:
        def get_project_path(self, project_name: str):
            raise ValueError(project_name)

    service = SimpleNamespace(pm=_InvalidNamePM())
    app = SimpleNamespace(state=SimpleNamespace(project_event_service=service))
    request = _FakeRequest(app)

    with pytest.raises(BadRequestError):
        await project_events_router._project_events_service("../etc", request)


@pytest.mark.asyncio
async def test_stream_project_events_emits_snapshot_and_changes():
    service = _FakeService()
    app = SimpleNamespace(state=SimpleNamespace(project_event_service=service))
    request = _FakeRequest(app)

    resolved = await project_events_router._project_events_service("demo", request)
    assert resolved is service

    stream = project_events_router.stream_project_events("demo", request, service=service)

    snapshot_event = await anext(stream)
    changes_event = await anext(stream)
    await stream.aclose()

    assert snapshot_event.event == "snapshot"
    assert snapshot_event.data["fingerprint"] == "fp-0"

    assert changes_event.event == "changes"
    assert changes_event.data["batch_id"] == "batch-1"
    assert service.unsubscribed is True


@pytest.mark.asyncio
async def test_stream_project_events_breaks_on_disconnect_in_idle():
    """Idle disconnection: the router detects it on _idle, breaks, ends the stream and unregisters."""
    service = _FakeService()
    app = SimpleNamespace(state=SimpleNamespace(project_event_service=service))
    # First 2 is_disconnected calls (snapshot/changes iteration tops) return False;
    # From the third call (entering _idle), return True.
    request = _FakeRequest(app, disconnect_after=2)

    stream = project_events_router.stream_project_events("demo", request, service=service)

    assert (await anext(stream)).event == "snapshot"
    assert (await anext(stream)).event == "changes"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert service.unsubscribed is True


@pytest.mark.asyncio
async def test_stream_project_events_breaks_on_disconnect_during_continuous_events():
    """Disconnection during continuous events (no _idle): the loop-top check also breaks immediately.

    Regression guard: the old implementation checked disconnection inside _idle; continuous events
    spaced <idle_timeout apart never triggered _idle, postponing detection until events stopped. Now check every iteration's top.
    """

    class _BusyService:
        def __init__(self):
            self.unsubscribed = False
            self.pm = _FakePM()

        @contextlib.asynccontextmanager
        async def stream_events(self, project_name: str, *, idle_timeout: float = 1.0):
            async def _iter():
                yield ("snapshot", {"project_name": project_name, "fingerprint": "fp-0"})
                # Continuously emit real events, never _idle.
                i = 0
                while True:
                    i += 1
                    yield ("changes", {"batch_id": f"b{i}", "fingerprint": f"fp-{i}"})

            try:
                yield _iter()
            finally:
                self.unsubscribed = True

    service = _BusyService()
    app = SimpleNamespace(state=SimpleNamespace(project_event_service=service))
    # First call (snapshot top) False; second onward (first changes top) True.
    request = _FakeRequest(app, disconnect_after=1)

    stream = project_events_router.stream_project_events("demo", request, service=service)

    # snapshot passes (first is_disconnected returns False)
    assert (await anext(stream)).event == "snapshot"
    # Next iteration's top detects disconnection and breaks, even as _iter() keeps emitting changes.
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert service.unsubscribed is True


@pytest.mark.asyncio
async def test_stream_project_events_ends_naturally_after_project_deleted_event():
    """Streams end naturally after terminal project_deleted events, not client disconnects; routers forward them unchanged.

    ``_FakeRequest`` never disconnects (``disconnect_after=None``); streams can end only because
    service ``_iter()`` explicitly returns after terminal events, verifying no special router interception.
    """

    class _DeletedService:
        def __init__(self):
            self.unsubscribed = False
            self.pm = _FakePM()

        @contextlib.asynccontextmanager
        async def stream_events(self, project_name: str, *, idle_timeout: float = 1.0):
            async def _iter():
                yield ("snapshot", {"project_name": project_name, "fingerprint": "fp-0"})
                yield ("project_deleted", {"project_name": project_name})

            try:
                yield _iter()
            finally:
                self.unsubscribed = True

    service = _DeletedService()
    app = SimpleNamespace(state=SimpleNamespace(project_event_service=service))
    request = _FakeRequest(app)

    stream = project_events_router.stream_project_events("demo", request, service=service)

    assert (await anext(stream)).event == "snapshot"
    deleted_event = await anext(stream)
    assert deleted_event.event == "project_deleted"
    assert deleted_event.data == {"project_name": "demo"}

    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert service.unsubscribed is True
