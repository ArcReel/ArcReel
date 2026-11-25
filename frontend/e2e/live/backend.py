"""Run the production ASGI app and worker with deterministic local generation results."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import wave
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import uvicorn
from pydantic import BaseModel
from starlette.responses import Response

HISTORY_COUNT = 205


class Release(BaseModel):
    task_ids: list[str]
    outcome: Literal["success", "failure"] = "success"


class TextResponses(BaseModel):
    responses: list[str]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    for key in ("DATABASE_URL", "AI_ANIME_PROJECTS"):
        os.environ.pop(key, None)

    with tempfile.TemporaryDirectory(prefix="arcreel-playwright-live-") as data_dir:
        os.environ.update(
            ARCREEL_DATA_DIR=data_dir,
            ARCREEL_SKIP_CATALOG_SYNC="1",
            AUTH_ENABLED="false",
            PYTHON_DOTENV_DISABLED="1",
        )
        serve(args.port, data_dir)


def serve(port: int, data_dir: str) -> None:
    from fastapi import APIRouter, FastAPI, HTTPException

    from lib.config.env_keys import PROVIDER_SECRET_KEYS

    for key in PROVIDER_SECRET_KEYS:
        os.environ.pop(key, None)

    from lib.db import async_session_factory
    from lib.db.base import DEFAULT_USER_ID
    from lib.db.models.task import Task
    from lib.episode.episode_paths import NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME, episode_drafts_dir
    from lib.generation.generation_worker import GenerationWorker
    from lib.infra.app_data_dir import app_data_dir
    from lib.infra.json_io import atomic_write_json
    from lib.project.project_manager import get_project_manager
    from server import app as backend
    from server.services.tasks.generation_tasks import voice_sample_resource_id
    from server.tool_runtime import execute_queued_text_task

    if app_data_dir() != Path(data_dir).resolve():
        raise RuntimeError("Playwright backend data directory is not isolated")

    releases: dict[str, asyncio.Future[str]] = {}
    text_responses: deque[str] = deque()
    control = APIRouter(prefix="/__playwright", include_in_schema=False)

    @control.post("/text-responses/{project_name}")
    async def queue_text_responses(project_name: str, request: TextResponses) -> None:
        del project_name
        text_responses.clear()
        text_responses.extend(request.responses)

    @control.post("/gemini/v1beta/models/{model_action:path}")
    async def gemini_generate_content(model_action: str, payload: dict[str, Any]) -> Response:
        if not model_action.endswith(":streamGenerateContent"):
            raise HTTPException(status_code=404, detail=f"Unsupported Gemini action: {model_action}")
        if not payload.get("contents"):
            raise HTTPException(status_code=400, detail="Missing contents in Gemini request")
        if not text_responses:
            raise HTTPException(status_code=500, detail="No scripted Gemini text response queued")
        frame = {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": text_responses.popleft()}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 20,
                "candidatesTokenCount": 10,
                "totalTokenCount": 30,
            },
        }

        return Response(f"data: {json.dumps(frame)}\n\n", media_type="text/event-stream")

    @control.post("/downgrade-v20/{project_name}")
    async def downgrade_v20(project_name: str) -> None:
        pm = get_project_manager()

        def _downgrade() -> None:
            project_dir = pm.get_project_path(project_name)
            drafts_dir = episode_drafts_dir(project_dir, 1)
            drafts_dir.mkdir(parents=True, exist_ok=True)
            (drafts_dir / NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME).write_text(
                '{"legacy": "retired-quarantine-bytes"}', encoding="utf-8"
            )
            project_file = project_dir / "project.json"
            data = json.loads(project_file.read_bytes())
            data["schema_version"] = 20
            atomic_write_json(project_file, data)

        await asyncio.to_thread(_downgrade)

    @control.post("/history/{project_name}")
    async def seed_history(project_name: str) -> None:
        queued_at = datetime.now(UTC) - timedelta(days=1)
        updated_at = datetime.now(UTC) + timedelta(hours=1)
        async with async_session_factory() as session:
            session.add_all(
                Task(
                    task_id=f"{project_name}-history-{index}",
                    user_id=DEFAULT_USER_ID,
                    project_name=project_name,
                    task_type="character",
                    media_type="image",
                    resource_id=f"历史角色 {index}",
                    status="succeeded",
                    queued_at=queued_at,
                    finished_at=updated_at,
                    updated_at=updated_at,
                )
                for index in range(HISTORY_COUNT)
            )
            await session.commit()

    def release_signal(task_id: str) -> asyncio.Future[str]:
        if task_id not in releases:
            releases[task_id] = asyncio.get_running_loop().create_future()
        return releases[task_id]

    @control.post("/release")
    async def release(request: Release) -> None:
        for task_id in request.task_ids:
            release_signal(task_id).set_result(request.outcome)

    def write_voice_sample(project_name: str, resource_id: str, task_id: str) -> str:
        path = f"audio/{voice_sample_resource_id(resource_id, task_id)}.wav"
        destination = get_project_manager().get_project_path(project_name) / path
        destination.parent.mkdir(exist_ok=True)
        with wave.open(str(destination), "wb") as sample:
            sample.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            sample.writeframes(bytes(48_000))
        return path

    async def execute(task: dict[str, Any], *, claimed_provider_id: str) -> dict[str, Any]:
        if str(task.get("task_type") or "").startswith("text_"):
            return await execute_queued_text_task(task)
        task_id = task["task_id"]
        outcome = await release_signal(task_id)
        if outcome == "failure":
            raise RuntimeError("Local fixture generation failed")
        if task["task_type"] == "voice_sample":
            path = await asyncio.to_thread(write_voice_sample, task["project_name"], task["resource_id"], task_id)
            return {"file_path": path}
        return {}

    async def resume(_task: dict[str, Any], *, job_id: str) -> dict[str, Any]:
        raise AssertionError(f"Unexpected provider job in isolated smoke suite: {job_id}")

    lifespan = backend.app.router.lifespan_context

    @asynccontextmanager
    async def fixture_lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with lifespan(app):
            try:
                yield
            finally:
                for signal in releases.values():
                    if not signal.done():
                        signal.set_result("failure")

    backend.app.router.lifespan_context = fixture_lifespan
    backend.app.include_router(control)
    backend.create_generation_worker = lambda: GenerationWorker(executor=execute, resume_executor=resume)
    uvicorn.run(backend.app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
