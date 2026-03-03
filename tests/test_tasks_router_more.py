import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers import tasks as tasks_router


class _FakeRequest:
    def __init__(self, disconnect_after: int):
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self):
        self._calls += 1
        return self._calls > self._disconnect_after


class _FakeQueue:
    def __init__(self, *, latest=0, snapshot=None, stats=None, events=None, task=None):
        self.latest = latest
        self.snapshot = snapshot or []
        self.stats = stats or {"pending": 0}
        self.events = list(events or [])
        self.task = task
        self.cursors = []

    def get_latest_event_id(self, project_name=None):
        return self.latest

    def get_recent_tasks_snapshot(self, project_name=None, limit=1000):
        return self.snapshot

    def get_task_stats(self, project_name=None):
        return self.stats

    def get_events_since(self, last_event_id, project_name=None, limit=200):
        self.cursors.append(last_event_id)
        if self.events:
            events = self.events
            self.events = []
            return events
        return []

    def get_task(self, task_id):
        return self.task


class TestTasksRouterMore:
    def test_parse_last_event_id_and_format(self):
        assert tasks_router._parse_last_event_id(None) is None
        assert tasks_router._parse_last_event_id("  ") is None
        assert tasks_router._parse_last_event_id("oops") is None
        assert tasks_router._parse_last_event_id("-10") == 0
        assert tasks_router._parse_last_event_id("7") == 7

    @pytest.mark.asyncio
    async def test_stream_tasks_emits_snapshot_and_task_event(self, monkeypatch):
        queue = _FakeQueue(
            latest=10,
            snapshot=[{"task_id": "t1"}],
            stats={"running": 1},
            events=[{"id": 11, "event_type": "running", "task_id": "t1", "data": {"task_id": "t1", "status": "running"}}],
        )
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: queue)
        monkeypatch.setattr(tasks_router, "read_queue_poll_interval", lambda: 0.0)

        request = _FakeRequest(disconnect_after=2)
        stream = tasks_router.stream_tasks(
            request=request,
            project_name="demo",
            last_event_id=None,
            last_event_header=" 7 ",
        )
        events = []
        async for event in stream:
            events.append(event)

        assert len(events) >= 2
        snapshot_event = events[0]
        assert snapshot_event.event == "snapshot"
        assert snapshot_event.data["last_event_id"] == 10
        assert snapshot_event.data["stats"]["running"] == 1

        task_event = events[1]
        assert task_event.event == "task"
        assert task_event.id == "11"
        assert task_event.data["action"] == "updated"
        assert task_event.data["task"]["task_id"] == "t1"
        assert task_event.data["stats"] == {"running": 1}
        assert queue.cursors[0] == 10

    @pytest.mark.asyncio
    async def test_stream_tasks_emits_only_snapshot_when_idle(self, monkeypatch):
        queue = _FakeQueue(latest=0)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: queue)
        monkeypatch.setattr(tasks_router, "read_queue_poll_interval", lambda: 0.0)

        request = _FakeRequest(disconnect_after=1)
        stream = tasks_router.stream_tasks(
            request=request,
            project_name="demo",
            last_event_id=0,
            last_event_header=None,
        )

        events = []
        async for event in stream:
            events.append(event)

        assert len(events) == 1
        assert events[0].event == "snapshot"

    def test_get_task_not_found(self, monkeypatch):
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: _FakeQueue(task=None))
        app = FastAPI()
        app.include_router(tasks_router.router, prefix="/api/v1")

        with TestClient(app) as client:
            resp = client.get("/api/v1/tasks/missing-task")
            assert resp.status_code == 404
            assert "不存在" in resp.json()["detail"]

    def test_transform_task_event_queued_maps_to_created(self):
        raw = {"event_type": "queued", "data": {"task_id": "t1", "status": "queued"}}
        stats = {"queued": 1, "running": 0, "succeeded": 0, "failed": 0, "total": 1}
        result = tasks_router._transform_task_event(raw, stats)
        assert result["action"] == "created"
        assert result["task"]["task_id"] == "t1"
        assert result["stats"] is stats

    def test_transform_task_event_non_queued_maps_to_updated(self):
        for event_type in ("running", "succeeded", "failed", "requeued"):
            raw = {"event_type": event_type, "data": {"task_id": "t1", "status": event_type}}
            stats = {"queued": 0, "running": 1, "succeeded": 0, "failed": 0, "total": 1}
            result = tasks_router._transform_task_event(raw, stats)
            assert result["action"] == "updated", f"expected 'updated' for {event_type}"
            assert result["task"]["task_id"] == "t1"
