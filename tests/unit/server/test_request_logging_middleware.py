"""验证 request_logging_middleware 对高频轮询接口的静默策略。"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from server.app import request_logging_middleware

_ACCESS_LOG_FMT = "%s %s %d %.0fms"


def _access_log_records(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [r for r in records if r.name == "server.app" and r.msg == _ACCESS_LOG_FMT]


def _build_app_with_ok_routes() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(request_logging_middleware)

    @app.get("/api/v1/tasks")
    async def _tasks() -> dict:
        return {"tasks": []}

    @app.get("/api/v1/tasks/stats")
    async def _tasks_stats() -> dict:
        return {"stats": {}}

    @app.get("/api/v1/projects")
    async def _projects() -> dict:
        return {"projects": []}

    return app


@pytest.mark.asyncio
async def test_quiet_endpoint_fast_200_is_debug(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="server.app")
    app = _build_app_with_ok_routes()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_list = await client.get("/api/v1/tasks")
        resp_stats = await client.get("/api/v1/tasks/stats")

    assert resp_list.status_code == 200
    assert resp_stats.status_code == 200

    records = _access_log_records(caplog.records)
    assert len(records) == 2
    assert all(r.levelno == logging.DEBUG for r in records)


@pytest.mark.asyncio
async def test_quiet_endpoint_5xx_still_info(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="server.app")
    app = FastAPI()
    app.middleware("http")(request_logging_middleware)

    @app.get("/api/v1/tasks")
    async def _tasks_error() -> dict:
        raise HTTPException(status_code=500, detail="boom")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/tasks")

    assert resp.status_code == 500
    records = _access_log_records(caplog.records)
    assert len(records) == 1
    assert records[0].levelno == logging.INFO


@pytest.mark.asyncio
async def test_non_quiet_endpoint_200_still_info(caplog: pytest.LogCaptureFixture) -> None:
    """回归保护：非静默路径的行为不变。"""
    caplog.set_level(logging.DEBUG, logger="server.app")
    app = _build_app_with_ok_routes()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/projects")

    assert resp.status_code == 200
    records = _access_log_records(caplog.records)
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
