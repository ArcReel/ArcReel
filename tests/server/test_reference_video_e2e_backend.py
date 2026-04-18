"""参考视频后端端到端：路由 → queue → executor（mock backend）。

本测试把路由 `POST .../generate` → GenerationQueue enqueue → 手动 claim →
`execute_generation_task` dispatch 到 `execute_reference_video_task` 串起来。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x04\x00\x00\x00\x04"
    b"\x08\x02\x00\x00\x00&\x93\t)\x00\x00\x00\x13IDATx\x9cc<\x91b\xc4\x00"
    b"\x03Lp\x16^\x0e\x00E\xf6\x01f\xac\xf5\x15\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def seeded_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    proj_dir = projects_root / "demo"
    proj_dir.mkdir()
    (proj_dir / "scripts").mkdir()
    (proj_dir / "characters").mkdir()
    (proj_dir / "scenes").mkdir()
    (proj_dir / "characters" / "张三.png").write_bytes(_TINY_PNG)
    (proj_dir / "scenes" / "酒馆.png").write_bytes(_TINY_PNG)

    (proj_dir / "project.json").write_text(
        json.dumps(
            {
                "title": "T",
                "content_mode": "reference_video",
                "generation_mode": "reference_video",
                "style": "s",
                "characters": {"张三": {"description": "x", "character_sheet": "characters/张三.png"}},
                "scenes": {"酒馆": {"description": "x", "scene_sheet": "scenes/酒馆.png"}},
                "props": {},
                "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (proj_dir / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "title": "E1",
                "content_mode": "reference_video",
                "summary": "x",
                "novel": {"title": "t", "chapter": "c"},
                "duration_seconds": 0,
                "video_units": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    custom_pm = ProjectManager(projects_root)
    monkeypatch.setattr(router_mod, "pm", custom_pm)
    monkeypatch.setattr(router_mod, "get_project_manager", lambda: custom_pm)

    from server.services import generation_tasks as gt_mod
    from server.services import reference_video_tasks as rvt_mod

    monkeypatch.setattr(gt_mod, "pm", custom_pm, raising=False)
    monkeypatch.setattr(gt_mod, "get_project_manager", lambda: custom_pm)
    monkeypatch.setattr(rvt_mod, "get_project_manager", lambda: custom_pm)

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="test", role="admin")
    return TestClient(app), proj_dir


@pytest.mark.asyncio
async def test_end_to_end_generate_unit_to_executor(
    seeded_client: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
):
    client, proj_dir = seeded_client

    # 1) 建 unit
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={
            "prompt": "Shot 1 (3s): @张三 推门进 @酒馆",
            "references": [
                {"type": "character", "name": "张三"},
                {"type": "scene", "name": "酒馆"},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    uid = resp.json()["unit"]["unit_id"]

    # 2) Patch GenerationQueue.enqueue_task 直接返回 task dict（跳过 DB）
    captured_payload: dict = {}

    async def _fake_enqueue(**kwargs):
        captured_payload.update(kwargs)
        return {"task_id": "t1", "deduped": False}

    from server.routers import reference_videos as router_mod

    fake_queue = MagicMock()
    fake_queue.enqueue_task = AsyncMock(side_effect=_fake_enqueue)
    monkeypatch.setattr(router_mod, "get_generation_queue", lambda: fake_queue)

    resp = client.post(f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate")
    assert resp.status_code == 202
    assert captured_payload["task_type"] == "reference_video"
    assert captured_payload["resource_id"] == uid

    # 3) Mock get_media_generator → 直接执行 executor
    from server.services import reference_video_tasks as rvt_mod

    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / f"{uid}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T12:00:00"}]}
    fake_video_backend = MagicMock()
    fake_video_backend.name = "ark"
    fake_video_backend.model = "doubao-seedance-2-0-260128"
    fake_generator._video_backend = fake_video_backend

    async def _fake_get_media_generator(*_a, **_k):
        return fake_generator

    monkeypatch.setattr(rvt_mod, "get_media_generator", _fake_get_media_generator)

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt_mod, "extract_video_thumbnail", _fake_extract)

    # 4) Dispatch 到 execute_generation_task
    from server.services.generation_tasks import execute_generation_task

    result = await execute_generation_task(
        {
            "task_type": "reference_video",
            "project_name": "demo",
            "resource_id": uid,
            "payload": {"script_file": "scripts/episode_1.json"},
            "user_id": "u1",
        }
    )
    assert result["resource_id"] == uid
    assert result["file_path"].endswith(f"{uid}.mp4")

    # 5) 校验 unit.generated_assets 已更新
    script = json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    u = next(u for u in script["video_units"] if u["unit_id"] == uid)
    assert u["generated_assets"]["status"] == "completed"
    assert u["generated_assets"]["video_clip"] == f"reference_videos/{uid}.mp4"
