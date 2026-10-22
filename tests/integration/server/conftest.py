from __future__ import annotations

import asyncio
import contextlib
import json
import re
import threading
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_queue_client import wait_for_task
from lib.generation.generation_worker import CapacityTable, GenerationWorker
from lib.project.project_change_hints import register_project_change_batch_listener
from lib.project.project_manager import ProjectManager
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import assets, usage
from server.services.tasks.generation_tasks import execute_generation_task
from server.services.tasks.resume_executor import execute_resume_video_task
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import bind_session_factory
from tests.integration.server.agent_tool_support import CLAIMED_BASIS_DIGEST, ToolHarness, fake_caps_resolver


class FakePM:
    def __init__(self, project_name: str, project_dir: Path):
        self._project_name = project_name
        self._project_dir = project_dir
        self.project_payload: dict[str, Any] = {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "content_mode": "drama",
            "generation_mode": "storyboard",
            "source_kind": "novel",
            "source_language": "中文",
            "overview": {},
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json", "source_origin": "own"}],
            "characters": {"张三": {"description": "主角"}, "李四": {"description": ""}},
            "scenes": {"村口": {"description": "黄昏的村口"}},
            "props": {},
            "products": {"保温杯": {"description": "不锈钢保温杯", "reference_images": [], "selling_points": []}},
            "style": "anime",
            "style_description": "soft pastel",
        }
        self.project_load_threads: list[int] = []
        self.script_payload: dict[str, Any] = {
            "content_mode": "narration",
            "episode": 1,
            "segments": [
                {
                    "segment_id": "E1S01",
                    "image_prompt": "村口黄昏",
                    "novel_text": "黄昏时分，风吹过村口。",
                    "video_prompt": {"action": "镜头平移", "camera_motion": "Pan", "ambiance_audio": "风声"},
                    "duration_seconds": 4,
                    "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
                },
            ],
        }

    def get_project_path(self, _name: str) -> Path:
        return self._project_dir

    def mirror_to_disk(self, script_filename: str | None = None) -> None:
        """把内存态落盘并重建产物清单：清单是读取已生成产物的唯一口径。

        ``load_script`` 每次读取都会先调用本方法；``load_project`` 与生产一样只读不写，只经它
        读项目、随后又从盘上读产物的工具，由用例在调用前显式调用本方法。

        清单按当下的项目与剧本重新激活（生产的补录路径），随后补回那些夹具不重建来源凭据的
        产物声明。用例故意构造的畸形条目激活不了，留空清单即可——工具侧本来就该按「产物不
        可用」逐条拒收。
        """

        (self._project_dir / "project.json").write_text(
            json.dumps(self.project_payload, ensure_ascii=False), encoding="utf-8"
        )
        filename = script_filename or self._canonical_script_filename()
        if filename is not None:
            scripts_dir = self._project_dir / "scripts"
            scripts_dir.mkdir(exist_ok=True)
            (scripts_dir / Path(filename).name).write_text(
                json.dumps(self.script_payload, ensure_ascii=False), encoding="utf-8"
            )
        from lib.artifacts.artifact_activation import activate_artifact_target_state

        # 用例故意构造的畸形项目/剧本激活不了；此处吞掉异常让清单留空，
        # 被测工具随后按「产物不可用」逐条拒收，这正是这些用例要断言的路径。
        with contextlib.suppress(Exception):
            activate_artifact_target_state(self._project_dir, bump_schema=False)
        self._register_claims(filename)

    def _canonical_script_filename(self) -> str | None:
        episode = self.script_payload.get("episode")
        return f"episode_{episode}.json" if isinstance(episode, bool) is False and isinstance(episode, int) else None

    def _script_episode(self, script_filename: str | None) -> int | None:
        """剧本身份取自身字段，缺字段时按规范文件名兜底——与生产的解析口径一致。"""

        episode = self.script_payload.get("episode")
        if isinstance(episode, int) and not isinstance(episode, bool) and episode >= 1:
            return episode
        match = re.fullmatch(r"episode_(\d+)\.json", Path(script_filename).name) if script_filename else None
        return int(match.group(1)) if match else None

    def _register_claims(self, script_filename: str | None = None) -> None:
        """把剧本已登记的产物补进清单：生产里它们在产出那一刻就登记过。

        激活能从来源凭据重建的条目以激活结果为准，这里只兜住夹具不重建凭据的那些
        （付费媒体的版本记录、缺 image_prompt 的历史分镜）。
        """

        from lib.artifacts.artifact_manifest import (
            ArtifactKey,
            ArtifactManifest,
            ArtifactManifestEntry,
            ProjectArtifactManifestAdapter,
        )

        adapter = ProjectArtifactManifestAdapter(self._project_dir)
        manifest = ArtifactManifest(adapter)
        recorded: dict[Any, str] = {}
        episode = self._script_episode(script_filename)
        if episode is not None:
            items = next(
                (
                    self.script_payload[field]
                    for field in ("segments", "scenes", "video_units")
                    if field in self.script_payload
                ),
                [],
            )
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict) or item.get("needs_replan") is True:
                    continue
                resource_id = next(
                    (
                        str(item[field])
                        for field in ("segment_id", "scene_id", "unit_id")
                        if isinstance(item.get(field), str) and item.get(field)
                    ),
                    "",
                )
                assets = item.get("generated_assets")
                if not resource_id or not isinstance(assets, dict):
                    continue
                for field, key_factory in (
                    ("storyboard_image", ArtifactKey.episode_storyboard),
                    ("narration_audio", ArtifactKey.episode_audio),
                    ("video_clip", ArtifactKey.episode_video),
                ):
                    artifact_path = assets.get(field)
                    if not isinstance(artifact_path, str) or not artifact_path:
                        continue
                    absolute = (self._project_dir / artifact_path).resolve()
                    if not absolute.is_file() or not absolute.is_relative_to(self._project_dir.resolve()):
                        continue
                    recorded[key_factory(episode, resource_id)] = artifact_path
        known = set(adapter.snapshot_entries())
        for key, artifact_path in recorded.items():
            if key in known:
                continue
            manifest.register_entry_transactionally(
                key,
                ArtifactManifestEntry(artifact_path=artifact_path, basis_digest=CLAIMED_BASIS_DIGEST),
            )

    def load_project(self, _name: str) -> dict[str, Any]:
        self.project_load_threads.append(threading.get_ident())
        return self.project_payload

    def load_script(self, _name: str, filename: str) -> dict[str, Any]:
        self.mirror_to_disk(filename)
        return self.script_payload

    def load_script_readonly(self, _name: str, _filename: str) -> dict[str, Any]:
        return self.script_payload

    def project_exists(self, _name: str) -> bool:
        return True


@pytest.fixture
def assets_env(db_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """全局资产库路由的测试客户端（``client``）与指向 ``tmp_path/projects`` 的 ``ProjectManager``（``pm``）。"""

    pm = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(assets, "async_session_factory", db_factory)
    monkeypatch.setattr(assets, "get_project_manager", lambda: pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(assets.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return {"client": TestClient(app), "pm": pm}


@pytest.fixture
def usage_app(monkeypatch: pytest.MonkeyPatch) -> Callable[[async_sessionmaker[AsyncSession]], FastAPI]:
    """使用记录路由的应用工厂：路由按传入的 session factory 读库，以管理员身份免鉴权。"""

    def build(session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
        monkeypatch.setattr(usage, "async_session_factory", session_factory)
        app = FastAPI()
        register_error_handlers(app)
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(usage.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        return app

    return build


def _build_fake_ctx(tmp_path: Path, session_factory, monkeypatch: pytest.MonkeyPatch) -> ToolHarness:
    bind_session_factory(monkeypatch, session_factory)
    monkeypatch.setattr("server.services.admission.video_batch_admission.async_session_factory", session_factory)
    monkeypatch.setattr("server.services.tasks.video_caps.async_session_factory", session_factory)
    monkeypatch.setattr("server.services.admission.video_quote.async_session_factory", session_factory)
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "storyboards").mkdir()
    (project_dir / "storyboards" / "scene_E1S01.png").write_bytes(b"")
    (project_dir / "audio").mkdir()
    (project_dir / "audio" / "segment_E1S01.wav").write_bytes(b"")
    (project_dir / "audio" / "segment_E1S02.wav").write_bytes(b"")

    queue = GenerationQueue(session_factory=session_factory)
    return ToolHarness(
        project_name="demo",
        data_root=tmp_path,
        pm=FakePM("demo", project_dir),
        queue=queue,
        config_resolver=fake_caps_resolver(),
    )


@pytest.fixture
def idle_fake_ctx(tmp_path: Path, concurrent_session_factory, monkeypatch: pytest.MonkeyPatch) -> ToolHarness:
    return _build_fake_ctx(tmp_path, concurrent_session_factory, monkeypatch)


@pytest.fixture
async def fake_ctx(
    tmp_path: Path,
    concurrent_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[ToolHarness]:
    ctx = _build_fake_ctx(tmp_path, concurrent_session_factory, monkeypatch)
    queue = ctx.queue

    async def text_provider(_task: dict[str, Any]) -> str:
        return "text"

    worker = GenerationWorker(
        queue=queue,
        capacity=CapacityTable(_limits={}, _defaults={"text": 1}),
        provider_projection=text_provider,
        lanes=("text",),
        executor=execute_generation_task,
        resume_executor=execute_resume_video_task,
    )
    worker.poll_interval = 60
    worker.heartbeat_interval = 60
    terminal_events: dict[str, asyncio.Event] = {}

    def record_terminal_events(_project_name, _source, changes) -> None:
        for change in changes:
            if change.get("entity_type") == "task":
                terminal_events.setdefault(str(change["entity_id"]), asyncio.Event()).set()

    async def wait_for_worker_task(task_id: str, *, queue: GenerationQueue) -> dict[str, Any]:
        terminal = terminal_events.setdefault(task_id, asyncio.Event())
        worker.wake()
        await asyncio.wait_for(terminal.wait(), timeout=5)
        return await wait_for_task(task_id, queue=queue)

    unregister = register_project_change_batch_listener(record_terminal_events)
    monkeypatch.setattr("server.tool_runtime.wait_for_task", wait_for_worker_task)
    assert await queue.acquire_or_renew_worker_lease(
        name=worker.lease_name,
        owner_id=worker.owner_id,
        ttl_seconds=worker.lease_ttl,
    )
    await worker.start()
    try:
        yield ctx
    finally:
        await worker.stop()
        unregister()


@pytest.fixture
def active_episode_tasks(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    """在途任务检查读到的生成队列：按状态（``queued`` / ``running``）回放写进返回字典的任务。"""
    from server.routers import episode_management

    tasks: dict[str, list[dict[str, Any]]] = {}

    class _Queue:
        async def list_tasks(self, *, project_name: str, status: str, page: int, page_size: int) -> dict[str, Any]:
            del project_name, page, page_size
            return {"items": tasks.get(status, [])}

    monkeypatch.setattr(episode_management, "get_generation_queue", lambda: _Queue())
    return tasks
