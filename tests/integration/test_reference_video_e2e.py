"""参考生视频完整端到端集成测试。

覆盖：
  1. 路由 POST /reference-videos/episodes/{ep}/units → unit 创建
  2. POST .../generate → GenerationQueue enqueue（mock）
  3. dispatch 到 execute_reference_video_task
  4. executor 从正文派生 3 bucket 的参考图（character + scene + prop）
  5. 正文多行解析 + `@mention` → 主体记号渲染正确性
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

from lib.artifact_activation import activate_artifact_target_state
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.auth import CurrentUserInfo, get_current_user
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import fake_reference_request_projector

pytestmark = pytest.mark.unit

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
    for sub in ("scripts", "characters", "scenes", "props", "source", "drafts/episode_1"):
        (proj_dir / sub).mkdir(parents=True)
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
                "schema_version": 7,
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

    # 生产项目一律处于当前 schema，剧本连同其取证链（分集原文 → step1）都已登记进产物清单。
    (proj_dir / "source" / "episode_1.txt").write_text("原文", encoding="utf-8")
    (proj_dir / "drafts" / "episode_1" / "step1_reference_units.json").write_text(
        json.dumps({"episode": 1, "video_units": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert activate_artifact_target_state(proj_dir, bump_schema=True) is True
    activated = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    activated["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION
    (proj_dir / "project.json").write_text(json.dumps(activated, ensure_ascii=False), encoding="utf-8")

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod
    from server.services import generation_tasks as gt_mod
    from server.services import reference_video_tasks as rvt_mod

    custom_pm = ProjectManager(projects_root)
    monkeypatch.setattr(router_mod, "get_project_manager", lambda: custom_pm)
    monkeypatch.setattr(gt_mod, "get_project_manager", lambda: custom_pm)
    monkeypatch.setattr(rvt_mod, "get_project_manager", lambda: custom_pm)
    # 保留真实资产水合、定桶与时长投影，只隔离本用例不关心的 DB 能力查询。
    monkeypatch.setattr(
        router_mod,
        "project_reference_unit_request",
        fake_reference_request_projector(durations=(3, 7)),
    )

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="test", role="admin")
    return TestClient(app), proj_dir, monkeypatch


def _confirm_visual_gate(proj_dir: Path, unit_id: str) -> None:
    """Seed the mandatory Sheet → keyframe gate for executor-focused cases."""

    storyboard = proj_dir / "storyboard_sheets" / f"{unit_id}.png"
    keyframe = proj_dir / "keyframes" / f"{unit_id}K01.png"
    storyboard.parent.mkdir(parents=True, exist_ok=True)
    keyframe.parent.mkdir(parents=True, exist_ok=True)
    storyboard.write_bytes(_TINY_PNG)
    keyframe.write_bytes(_TINY_PNG)
    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    unit = next(item for item in script["video_units"] if item["unit_id"] == unit_id)
    unit["storyboard_sheet"] = {
        "image_path": f"storyboard_sheets/{unit_id}.png",
        "status": "confirmed",
        "confirmed_at": "2026-08-24T00:00:00Z",
    }
    unit["keyframes"][0]["image_path"] = f"keyframes/{unit_id}K01.png"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


@pytest.mark.asyncio
async def test_e2e_three_bucket_mentions_with_multi_line_body(three_bucket_client):
    client, proj_dir, monkeypatch = three_bucket_client

    # 1) 新建 unit：正文混合 3 bucket mention、跨多行
    prompt = "镜头1：@张三 推门进 @酒馆\n镜头2：近景 @张三 握紧 @长剑\n"
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": prompt, "duration_seconds": 7},
    )
    assert resp.status_code == 201, resp.text
    unit = resp.json()["unit"]
    uid = unit["unit_id"]

    # 手工新建 unit 也自动获得至少一个可审阅的关键帧计划与正文 tag。
    assert unit["text"] == f"@[关键分镜 {uid}K01] {prompt}".strip()
    assert unit["duration_seconds"] == 7
    _confirm_visual_gate(proj_dir, uid)

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

    # 3) mock backend：校验 prompt 里 @ 已替换为主体记号，正文提及顺序决定编号
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

    from lib.config.resolver import ProviderModel
    from server.services import reference_video_tasks as rvt_mod
    from server.services.generation_context import GenerationContext, VideoLaneResult

    ctx = GenerationContext(
        generator=fake_generator,
        video_lane=VideoLaneResult(
            provider_model=ProviderModel(provider_id="ark", model_id="doubao-seedance-2-0-260128"),
            backend_name="ark",
            backend_model="doubao-seedance-2-0-260128",
            resolution=None,
            resolution_or_fallback="1080p",
            supported_durations=(7,),
            max_duration=7,
            max_reference_images=None,
            generate_audio=True,
        ),
    )

    async def _fake_resolve(*_a, **_k):
        return ctx

    monkeypatch.setattr(rvt_mod, "resolve_generation_context", _fake_resolve)

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

    # 5) 断言三段论渲染：第一段按正文提及顺序绑定，正文 @mention 全部替成 <X>
    rendered = captured_backend_kwargs["prompt"]
    assert rendered.startswith(
        f"<{uid} 的 Video Unit Storyboard Sheet（整段 Video Unit 的镜头顺序与场景变化参考，不是单一目标帧）>"
        f"@图片1、<关键分镜 {uid}K01>@图片2、<张三>@图片3、<酒馆>@图片4、<长剑>@图片5。"
    )
    assert "@张三" not in rendered  # 所有 @ 已替换
    assert "@酒馆" not in rendered
    assert "@长剑" not in rendered
    assert "[图" not in rendered  # 对照表编号已废除
    assert "保持无字幕" in rendered  # 第三段约束包

    # 6) Sheet 是 Picture 1，关键帧和三个正文资产依次跟随。
    ref_images = captured_backend_kwargs["reference_images"]
    assert len(ref_images) == 5

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
    """把 scenes/酒馆.png 删掉，executor 应保留 projector 的结构化 blocker。"""
    client, proj_dir, monkeypatch = three_bucket_client
    (proj_dir / "scenes" / "酒馆.png").unlink()

    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "@张三 进 @酒馆", "duration_seconds": 3},
    )
    uid = resp.json()["unit"]["unit_id"]
    _confirm_visual_gate(proj_dir, uid)

    from lib.config.resolver import ProviderModel
    from lib.reference_video.request_projection import ReferenceProjectionBlockedError
    from server.services import reference_video_tasks as rvt_mod
    from server.services.generation_context import GenerationContext, VideoLaneResult
    from server.services.generation_tasks import execute_generation_task

    context = GenerationContext(
        generator=MagicMock(),
        video_lane=VideoLaneResult(
            provider_model=ProviderModel(provider_id="ark", model_id="doubao-seedance-2-0-260128"),
            backend_name="ark",
            backend_model="doubao-seedance-2-0-260128",
            resolution=None,
            resolution_or_fallback="1080p",
            supported_durations=(3,),
            max_duration=3,
            max_reference_images=None,
            generate_audio=True,
        ),
    )

    async def _fake_resolve(*_args, **_kwargs):
        return context

    monkeypatch.setattr(rvt_mod, "resolve_generation_context", _fake_resolve)

    with pytest.raises(ReferenceProjectionBlockedError) as exc:
        await execute_generation_task(
            {
                "task_type": "reference_video",
                "project_name": "demo",
                "resource_id": uid,
                "payload": {"script_file": "scripts/episode_1.json"},
                "user_id": "u1",
            }
        )
    assert exc.value.code == "reference_asset_missing"
    missing = exc.value.params["missing"]
    assert isinstance(missing, tuple)
    assert ("scene", "酒馆") in missing
