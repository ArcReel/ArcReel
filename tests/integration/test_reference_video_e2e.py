"""参考生视频完整端到端集成测试（PR7 M6）。

覆盖：
  1. 路由 POST /reference-videos/episodes/{ep}/units → unit 创建
  2. POST .../generate → GenerationQueue enqueue（mock）
  3. dispatch 到 execute_reference_video_task
  4. executor 解析 3 bucket 的 references（character + scene + prop）
  5. shot_parser 多 shot 解析 + `@名称` → `[图N]` 渲染正确性
  6. mp4 + thumbnail 落盘
  7. generated_assets.status / video_clip / video_thumbnail 写回
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
def three_bucket_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    proj_dir = projects_root / "demo"
    proj_dir.mkdir()
    for sub in ("scripts", "characters", "scenes", "props"):
        (proj_dir / sub).mkdir()
    (proj_dir / "characters" / "张三.png").write_bytes(_TINY_PNG)
    (proj_dir / "scenes" / "酒馆.png").write_bytes(_TINY_PNG)
    (proj_dir / "props" / "长剑.png").write_bytes(_TINY_PNG)

    (proj_dir / "project.json").write_text(
        json.dumps(
            {
                "title": "Demo",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "style": "唐风水墨",
                "characters": {
                    "张三": {"description": "主角", "character_sheet": "characters/张三.png"},
                },
                "scenes": {
                    "酒馆": {"description": "旧木酒馆", "scene_sheet": "scenes/酒馆.png"},
                },
                "props": {
                    "长剑": {"description": "铁铸长剑", "prop_sheet": "props/长剑.png"},
                },
                "episodes": [{"episode": 1, "title": "江湖夜话", "script_file": "scripts/episode_1.json"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (proj_dir / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "title": "江湖夜话",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "summary": "主角手持长剑进酒馆",
                "novel": {"title": "N", "chapter": "1"},
                "duration_seconds": 0,
                "video_units": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod
    from server.services import generation_tasks as gt_mod
    from server.services import reference_video_tasks as rvt_mod

    custom_pm = ProjectManager(projects_root)
    monkeypatch.setattr(router_mod, "pm", custom_pm)
    monkeypatch.setattr(router_mod, "get_project_manager", lambda: custom_pm)
    monkeypatch.setattr(gt_mod, "pm", custom_pm, raising=False)
    monkeypatch.setattr(gt_mod, "get_project_manager", lambda: custom_pm)
    monkeypatch.setattr(rvt_mod, "get_project_manager", lambda: custom_pm)

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="test", role="admin")
    return TestClient(app), proj_dir, monkeypatch


@pytest.mark.asyncio
async def test_e2e_three_bucket_mentions_with_multi_shot(three_bucket_client):
    client, proj_dir, monkeypatch = three_bucket_client

    # 1) 新建 unit：混合 3 bucket mention + 多 shot
    prompt = "Shot 1 (3s): @张三 推门进 @酒馆\nShot 2 (4s): 近景 @张三 握紧 @长剑\n"
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={
            "prompt": prompt,
            "references": [
                {"type": "character", "name": "张三"},
                {"type": "scene", "name": "酒馆"},
                {"type": "prop", "name": "长剑"},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    unit = resp.json()["unit"]
    uid = unit["unit_id"]

    # shot_parser 落地 shots[]
    assert len(unit["shots"]) == 2
    assert unit["shots"][0]["duration"] == 3
    assert unit["shots"][1]["duration"] == 4
    assert unit["duration_seconds"] == 7
    ref_names = {r["name"] for r in unit["references"]}
    assert ref_names == {"张三", "酒馆", "长剑"}
    ref_types = {(r["type"], r["name"]) for r in unit["references"]}
    assert ref_types == {("character", "张三"), ("scene", "酒馆"), ("prop", "长剑")}

    # 2) generate 入队（mock queue）
    captured: dict = {}

    async def _fake_enqueue(**kwargs):
        captured.update(kwargs)
        return {"task_id": "t-e2e", "deduped": False}

    from server.routers import reference_videos as router_mod

    fake_queue = MagicMock()
    fake_queue.enqueue_task = AsyncMock(side_effect=_fake_enqueue)
    monkeypatch.setattr(router_mod, "get_generation_queue", lambda: fake_queue)

    resp = client.post(f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate")
    assert resp.status_code == 202
    assert captured["task_type"] == "reference_video"
    assert captured["resource_id"] == uid

    # 3) mock backend：校验 prompt 里 @ 已替换为 [图N]，references 顺序决定编号
    captured_backend_kwargs: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured_backend_kwargs.update(kwargs)
        out = proj_dir / "reference_videos" / f"{uid}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-20T12:00:00"}]}
    fake_video_backend = MagicMock()
    fake_video_backend.name = "ark"
    fake_video_backend.model = "doubao-seedance-2-0-260128"
    fake_generator._video_backend = fake_video_backend

    async def _fake_get_media_generator(*_a, **_k):
        return fake_generator

    from server.services import reference_video_tasks as rvt_mod

    monkeypatch.setattr(rvt_mod, "get_media_generator", _fake_get_media_generator)

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt_mod, "extract_video_thumbnail", _fake_extract)

    # 4) 直接调 executor（绕过真实 worker 轮询）
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

    # 5) 断言 prompt 渲染：@张三 → [图1]、@酒馆 → [图2]、@长剑 → [图3]
    rendered = captured_backend_kwargs["prompt"]
    assert "[图1]" in rendered  # 张三
    assert "[图2]" in rendered  # 酒馆
    assert "[图3]" in rendered  # 长剑
    assert "@张三" not in rendered  # 所有 @ 已替换
    assert "@酒馆" not in rendered
    assert "@长剑" not in rendered

    # 6) 断言 reference_images 传了 3 个临时文件
    ref_images = captured_backend_kwargs["reference_images"]
    assert len(ref_images) == 3

    # 7) 断言 mp4 + thumbnail 落盘 + generated_assets 写回
    assert result["file_path"].endswith(f"{uid}.mp4")
    assert (proj_dir / "reference_videos" / f"{uid}.mp4").exists()

    script = json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    u = next(x for x in script["video_units"] if x["unit_id"] == uid)
    ga = u["generated_assets"]
    assert ga["status"] == "completed"
    assert ga["video_clip"] == f"reference_videos/{uid}.mp4"
    assert ga["video_thumbnail"] == f"reference_videos/thumbnails/{uid}.jpg"


@pytest.mark.asyncio
async def test_e2e_missing_reference_raises(three_bucket_client):
    """把 scenes/酒馆.png 删掉，executor 应抛 MissingReferenceError。"""
    client, proj_dir, monkeypatch = three_bucket_client
    (proj_dir / "scenes" / "酒馆.png").unlink()

    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={
            "prompt": "Shot 1 (3s): @张三 进 @酒馆",
            "references": [
                {"type": "character", "name": "张三"},
                {"type": "scene", "name": "酒馆"},
            ],
        },
    )
    uid = resp.json()["unit"]["unit_id"]

    from lib.reference_video.errors import MissingReferenceError
    from server.services.generation_tasks import execute_generation_task

    with pytest.raises(MissingReferenceError) as exc:
        await execute_generation_task(
            {
                "task_type": "reference_video",
                "project_name": "demo",
                "resource_id": uid,
                "payload": {"script_file": "scripts/episode_1.json"},
                "user_id": "u1",
            }
        )
    assert any(name == "酒馆" for _, name in exc.value.missing)
