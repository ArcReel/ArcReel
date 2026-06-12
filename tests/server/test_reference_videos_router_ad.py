"""ad 模式参考直出路由（reference-videos router 的 ad 分支）测试。

ad 的 video_unit 是从 shots 派生的轻量索引：派生端点负责（重）派生并持久化，
手工增删改单元被拒绝（shots 是内容唯一真相），生成端点按持久化索引入队。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user


def _shot(shot_id: str, duration: int, **overrides) -> dict:
    base = {
        "shot_id": shot_id,
        "section": "hook",
        "duration_seconds": duration,
        "voiceover_text": "口播",
        "characters_in_shot": [],
        "scenes": [],
        "props": [],
        "products_in_shot": [],
        "image_prompt": {
            "scene": f"{shot_id} 画面",
            "composition": {"shot_type": "Close-up", "lighting": "自然光", "ambiance": "明亮"},
        },
        "video_prompt": {
            "action": f"{shot_id} 动作",
            "camera_motion": "Static",
            "ambiance_audio": "",
            "dialogue": [],
        },
    }
    base.update(overrides)
    return base


@pytest.fixture
def ad_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    proj_dir = projects_root / "ad-demo"
    proj_dir.mkdir()
    (proj_dir / "scripts").mkdir()
    (proj_dir / "project.json").write_text(
        json.dumps(
            {
                "title": "带货短片",
                "content_mode": "ad",
                "generation_mode": "reference_video",
                "style": "明亮写实",
                "target_duration": 30,
                "brief": "卖按摩仪",
                "characters": {"小美": {"description": "x"}},
                "scenes": {},
                "props": {},
                "products": {"按摩仪": {"description": "颈部按摩仪", "reference_images": []}},
                "episodes": [{"episode": 1, "title": "短片", "script_file": "scripts/episode_1.json"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (proj_dir / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "title": "短片",
                "content_mode": "ad",
                "shots": [
                    _shot("E1S1", 3, products_in_shot=["按摩仪"]),
                    _shot("E1S2", 2, characters_in_shot=["小美"]),
                ],
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

    # 供应商时长上限解析与队列都打桩：路由测试只看入参与持久化结果
    monkeypatch.setattr(router_mod, "resolve_max_unit_duration", AsyncMock(return_value=15))
    fake_queue = AsyncMock()
    fake_queue.enqueue_task = AsyncMock(return_value={"task_id": "t1", "deduped": False})
    monkeypatch.setattr(router_mod, "get_generation_queue", lambda: fake_queue)

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="test", role="admin")
    client = TestClient(app)
    client.fake_queue = fake_queue  # type: ignore[attr-defined]
    client.proj_dir = proj_dir  # type: ignore[attr-defined]
    return client


def _read_script(client: TestClient) -> dict:
    path: Path = client.proj_dir / "scripts" / "episode_1.json"  # type: ignore[attr-defined]
    return json.loads(path.read_text(encoding="utf-8"))


class TestDeriveUnits:
    def test_derive_persists_index_into_script(self, ad_client: TestClient):
        resp = ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/derive-units")

        assert resp.status_code == 200, resp.text
        units = resp.json()["units"]
        assert [u["shot_ids"] for u in units] == [["E1S1", "E1S2"]]
        assert units[0]["references"][0] == {"type": "product", "name": "按摩仪"}

        script = _read_script(ad_client)
        assert script["reference_units"] == units

    def test_rederive_is_reproducible_and_keeps_assets(self, ad_client: TestClient):
        ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/derive-units")
        script = _read_script(ad_client)
        script["reference_units"][0]["generated_assets"]["video_clip"] = "reference_videos/E1U1.mp4"
        path: Path = ad_client.proj_dir / "scripts" / "episode_1.json"  # type: ignore[attr-defined]
        path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

        resp = ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/derive-units")

        units = resp.json()["units"]
        assert units[0]["generated_assets"]["video_clip"] == "reference_videos/E1U1.mp4"

    def test_derive_rejected_for_non_ad_project(self, ad_client: TestClient):
        proj_dir: Path = ad_client.proj_dir  # type: ignore[attr-defined]
        project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
        project["content_mode"] = "narration"
        (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        script = _read_script(ad_client)
        script["content_mode"] = "narration"
        script["generation_mode"] = "reference_video"
        del script["shots"]
        script["video_units"] = []
        (proj_dir / "scripts" / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

        resp = ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/derive-units")

        assert resp.status_code == 409


class TestAdUnitListing:
    def test_list_returns_persisted_index(self, ad_client: TestClient):
        ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/derive-units")

        resp = ad_client.get("/api/v1/projects/ad-demo/reference-videos/episodes/1/units")

        assert resp.status_code == 200
        units = resp.json()["units"]
        assert [u["unit_id"] for u in units] == ["E1U1"]
        assert units[0]["shot_ids"] == ["E1S1", "E1S2"]

    def test_list_empty_before_derive(self, ad_client: TestClient):
        resp = ad_client.get("/api/v1/projects/ad-demo/reference-videos/episodes/1/units")
        assert resp.status_code == 200
        assert resp.json() == {"units": []}


class TestAdMutationsRejected:
    def test_add_unit_rejected(self, ad_client: TestClient):
        resp = ad_client.post(
            "/api/v1/projects/ad-demo/reference-videos/episodes/1/units",
            json={"prompt": "Shot 1 (3s): 画面"},
        )
        assert resp.status_code == 409

    def test_patch_unit_rejected(self, ad_client: TestClient):
        ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/derive-units")
        resp = ad_client.patch(
            "/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U1",
            json={"note": "x"},
        )
        assert resp.status_code == 409

    def test_delete_unit_rejected(self, ad_client: TestClient):
        ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/derive-units")
        resp = ad_client.delete("/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U1")
        assert resp.status_code == 409

    def test_reorder_rejected(self, ad_client: TestClient):
        ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/derive-units")
        resp = ad_client.post(
            "/api/v1/projects/ad-demo/reference-videos/episodes/1/units/reorder",
            json={"unit_ids": ["E1U1"]},
        )
        assert resp.status_code == 409


class TestAdGenerate:
    def test_generate_enqueues_reference_video_task(self, ad_client: TestClient):
        ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/derive-units")

        resp = ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U1/generate")

        assert resp.status_code == 202, resp.text
        assert resp.json()["task_id"] == "t1"
        kwargs = ad_client.fake_queue.enqueue_task.call_args.kwargs  # type: ignore[attr-defined]
        assert kwargs["task_type"] == "reference_video"
        assert kwargs["resource_id"] == "E1U1"

    def test_generate_unknown_unit_404(self, ad_client: TestClient):
        ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/derive-units")
        resp = ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U9/generate")
        assert resp.status_code == 404

    def test_generate_with_blank_shot_prompts_rejected(self, ad_client: TestClient):
        ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/derive-units")
        proj_dir: Path = ad_client.proj_dir  # type: ignore[attr-defined]
        script = _read_script(ad_client)
        for shot in script["shots"]:
            shot["image_prompt"]["scene"] = ""
            shot["video_prompt"]["action"] = ""
            shot["video_prompt"]["camera_motion"] = ""
            shot["video_prompt"]["ambiance_audio"] = ""
        (proj_dir / "scripts" / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

        resp = ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U1/generate")

        assert resp.status_code == 400

    def test_generate_with_stale_index_409(self, ad_client: TestClient):
        ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/derive-units")
        proj_dir: Path = ad_client.proj_dir  # type: ignore[attr-defined]
        script = _read_script(ad_client)
        script["shots"] = [s for s in script["shots"] if s["shot_id"] != "E1S2"]
        (proj_dir / "scripts" / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

        resp = ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U1/generate")

        assert resp.status_code == 409
