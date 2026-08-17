from __future__ import annotations

import json
import unicodedata
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import fake_reference_request_projector
from tests.speech_contract_cases import SPEECH_CONTRACT_CASES, SpeechContractCase


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # 重定向 projects_root 到 tmp_path
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    proj_dir = projects_root / "demo"
    proj_dir.mkdir()
    (proj_dir / "scripts").mkdir()
    (proj_dir / "project.json").write_text(
        json.dumps(
            {
                "title": "T",
                "content_mode": "narration",
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
    (proj_dir / "characters").mkdir()
    (proj_dir / "characters" / "张三.png").write_bytes(b"image")
    (proj_dir / "scenes").mkdir()
    (proj_dir / "scenes" / "酒馆.png").write_bytes(b"image")
    (proj_dir / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "title": "E1",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "summary": "x",
                "novel": {"title": "t", "chapter": "c"},
                "duration_seconds": 0,
                "video_units": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Patch project_manager 的根目录
    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    custom_pm = ProjectManager(projects_root)
    monkeypatch.setattr(router_mod, "get_project_manager", lambda: custom_pm)
    monkeypatch.setattr(router_mod, "tts_task_in_progress", AsyncMock(return_value=False))
    # 公共 request projection 的 resolver 需要 DB；路由测试注入 in-process 能力适配器。
    monkeypatch.setattr(router_mod, "project_reference_unit_request", _projection_with_durations([3, 6, 9]))

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router_mod.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="test", role="admin")
    return TestClient(app)


@pytest.mark.unit
def test_list_units_empty(client: TestClient):
    resp = client.get("/api/v1/projects/demo/reference-videos/episodes/1/units")
    assert resp.status_code == 200
    assert resp.json() == {"units": []}


@pytest.mark.unit
def test_list_units_404_for_unknown_project(client: TestClient):
    resp = client.get("/api/v1/projects/missing/reference-videos/episodes/1/units")
    assert resp.status_code == 404


@pytest.mark.unit
def test_add_unit_creates_minimal_entry(client: TestClient):
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={
            "prompt": "镜头1：@张三 推门",
            "duration_seconds": 3,
            "references": [{"type": "character", "name": "张三"}],
        },
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["unit"]["unit_id"].startswith("E1U")
    assert payload["unit"]["duration_seconds"] == 3
    assert payload["unit"]["references"] == [{"type": "character", "name": "张三"}]


@pytest.mark.integration
def test_add_unit_allows_blank_editor_draft(client: TestClient):
    response = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "", "duration_seconds": 3, "references": []},
    )

    assert response.status_code == 201, response.text
    assert response.json()["unit"]["shots"] == [{"text": ""}]


@pytest.mark.integration
def test_add_unit_without_duration_falls_back_to_model_slot(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """请求不给时长 → 取项目能力解析出的档位首项（与执行层解析申请秒数的回退序同源）。"""
    _patch_supported_durations(monkeypatch, [6, 9])
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：@张三 推门", "references": [{"type": "character", "name": "张三"}]},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["unit"]["duration_seconds"] == 6


@pytest.mark.integration
def test_add_unit_derives_omitted_references_before_selecting_duration_bucket(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """省略 references 时先从正文派生，默认时长按最终 r2v 单元定桶。"""
    from server.routers import reference_videos as router_mod
    from server.services.reference_video_tasks import ProjectDurationContext

    ctx = ProjectDurationContext(supported_durations=(6, 9), resolution=None, provider_id="", model_name=None)
    resolve_context = AsyncMock(return_value=ctx)
    monkeypatch.setattr(router_mod, "resolve_project_duration_context", resolve_context)

    response = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：@[张三] 推门"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["unit"]["references"] == [{"type": "character", "name": "张三"}]
    assert response.json()["unit"]["duration_seconds"] == 6
    assert resolve_context.await_args.kwargs["capability"] == "r2v"


@pytest.mark.integration
@pytest.mark.parametrize("duration_seconds", [0, -1])
def test_add_unit_rejects_non_positive_duration(client: TestClient, duration_seconds: int):
    """显式非正时长须在请求边界被拒，不静默改写成 1 秒。"""
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={
            "prompt": "镜头1：@张三 推门",
            "duration_seconds": duration_seconds,
            "references": [{"type": "character", "name": "张三"}],
        },
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.unit
def test_add_unit_rejects_unknown_asset_reference(client: TestClient):
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：@未知角色 出现", "references": [{"type": "character", "name": "未知角色"}]},
    )
    assert resp.status_code == 400
    assert "未知角色" in resp.json()["detail"]


@pytest.mark.unit
def test_add_unit_atomically_rejects_mixed_speech(client: TestClient):
    response = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={
            "prompt": "镜头1：张三推门\n@[张三]：{快走。}\n{风吹过旷野。}",
            "duration_seconds": 3,
            "references": [{"type": "character", "name": "张三"}],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["problems"][0]["code"] == "mixed_speech"
    assert client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json() == {"units": []}


def _seed_unit(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={
            "prompt": "镜头1：@张三 推门",
            "duration_seconds": 3,
            "references": [{"type": "character", "name": "张三"}],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["unit"]["unit_id"]


@pytest.mark.integration
def test_patch_unit_prompt_keeps_duration(client: TestClient):
    """时长与正文互不牵连；只传正文时 references 用当前资产表重新派生。"""
    uid = _seed_unit(client)
    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"prompt": "镜头1：@酒馆 门口\n镜头2：@酒馆 全景"},
    )
    assert resp.status_code == 200, resp.text
    unit = resp.json()["unit"]
    assert len(unit["shots"]) == 2
    assert unit["duration_seconds"] == 3
    assert unit["references"] == [{"type": "scene", "name": "酒馆"}]


@pytest.mark.integration
def test_patch_unit_rederives_non_character_references_before_speech_admission(client: TestClient):
    uid = _seed_unit(client)

    response = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"prompt": "镜头1：@[酒馆]：木门被风吹开"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["unit"]["references"] == [{"type": "scene", "name": "酒馆"}]


@pytest.mark.integration
@pytest.mark.parametrize(
    "case",
    [case for case in SPEECH_CONTRACT_CASES if case.generation_mode == "reference_video"],
    ids=lambda case: case.route_id,
)
def test_three_reference_route_web_manual_edits_atomically_reject_mixed_speech_on_save(
    client: TestClient, tmp_path: Path, case: SpeechContractCase
):
    uid = _seed_unit(client)
    before = client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json()["units"][0]
    before["generated_assets"] = {"video_clip": f"reference_videos/{uid}.mp4", "status": "completed"}
    # 模拟已有付费生成历史；人工正文修改失败时 locked_script 不得写回任何候选字段。
    from server.routers import reference_videos as router_mod

    pm = router_mod.get_project_manager()
    project_file = tmp_path / "projects" / "demo" / "project.json"
    project = json.loads(project_file.read_text(encoding="utf-8"))
    project["content_mode"] = case.content_mode
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    script = pm.load_script("demo", "episode_1.json")
    script["content_mode"] = case.content_mode
    script["video_units"][0]["generated_assets"] = before["generated_assets"]
    pm.save_script("demo", script, "episode_1.json")

    response = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"prompt": "镜头1：张三推门\n@[张三]：{快走。}\n{风吹过旷野。}"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["problems"][0]["code"] == "mixed_speech"
    after = client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json()["units"][0]
    assert after["shots"] == before["shots"]
    assert after["generated_assets"] == before["generated_assets"]


@pytest.mark.integration
def test_patch_allows_unchanged_legacy_mixed_prompt(client: TestClient):
    uid = _seed_unit(client)
    prompt = "镜头1：张三推门\n@[张三]：{快走。}\n{风吹过旷野。}"
    from server.routers import reference_videos as router_mod

    pm = router_mod.get_project_manager()
    script = pm.load_script("demo", "episode_1.json")
    script["video_units"][0]["shots"] = [{"text": "张三推门\n@[张三]：{快走。}\n{风吹过旷野。}"}]
    script["video_units"][0]["needs_replan"] = True
    pm.save_script("demo", script, "episode_1.json")

    response = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"prompt": prompt, "note": "保留历史媒体"},
    )

    assert response.status_code == 200
    assert response.json()["unit"]["note"] == "保留历史媒体"
    assert response.json()["unit"]["needs_replan"] is True


@pytest.mark.integration
def test_patch_unit_duration_only(client: TestClient):
    """只改时长：镜头正文原样保留。"""
    uid = _seed_unit(client)
    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"duration_seconds": 9},
    )
    assert resp.status_code == 200, resp.text
    unit = resp.json()["unit"]
    assert unit["duration_seconds"] == 9
    assert unit["shots"] == [{"text": "@张三 推门"}]


@pytest.mark.integration
@pytest.mark.parametrize("duration_seconds", [0, -1])
def test_patch_unit_rejects_non_positive_duration(client: TestClient, duration_seconds: int):
    """显式非正时长须在请求边界被拒，不静默改写成 1 秒。"""
    uid = _seed_unit(client)
    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"duration_seconds": duration_seconds},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.unit
def test_patch_unit_references_only(client: TestClient):
    uid = _seed_unit(client)
    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={
            "references": [
                {"type": "character", "name": "张三"},
                {"type": "scene", "name": "酒馆"},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["unit"]["references"]) == 2


@pytest.mark.integration
def test_add_nonblank_unit_derives_registered_references_when_omitted(client: TestClient):
    response = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "@[酒馆]：木门被风吹开", "duration_seconds": 5},
    )

    assert response.status_code == 201, response.text
    assert response.json()["unit"]["references"] == [{"type": "scene", "name": "酒馆"}]


@pytest.mark.integration
def test_patch_unit_references_atomically_rejects_new_parse_failure(client: TestClient):
    uid = _seed_unit(client)
    from server.routers import reference_videos as router_mod

    pm = router_mod.get_project_manager()
    script = pm.load_script("demo", "episode_1.json")
    script["video_units"][0].update(
        {
            "shots": [{"text": "@[酒馆]：木门被风吹开"}],
            "references": [{"type": "scene", "name": "酒馆"}],
        }
    )
    pm.save_script("demo", script, "episode_1.json")

    response = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"references": []},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["problems"][0]["code"] == "parse_failed"
    saved = client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json()["units"][0]
    assert saved["references"] == [{"type": "scene", "name": "酒馆"}]


@pytest.mark.integration
def test_patch_unit_references_can_repair_parse_failure(client: TestClient):
    uid = _seed_unit(client)
    from server.routers import reference_videos as router_mod

    pm = router_mod.get_project_manager()
    script = pm.load_script("demo", "episode_1.json")
    script["video_units"][0].update(
        {
            "shots": [{"text": "@[酒馆]：木门被风吹开"}],
            "references": [],
            "needs_replan": True,
        }
    )
    pm.save_script("demo", script, "episode_1.json")

    response = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"references": [{"type": "scene", "name": "酒馆"}]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["unit"].get("needs_replan") is not True


@pytest.mark.unit
def test_patch_unit_rejects_unknown_reference(client: TestClient):
    uid = _seed_unit(client)
    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"references": [{"type": "prop", "name": "不存在"}]},
    )
    assert resp.status_code == 400


@pytest.mark.integration
def test_patch_unit_accepts_nfc_reference_for_nfd_registered_name(client: TestClient):
    """资产以 NFD 形式登记、PATCH 请求携带解析器已归一的 NFC 名字：_validate_references_exist
    须按归一形式比对判「已登记」放行，不能因编码形式不同误判未登记。"""
    from server.routers import reference_videos as router_mod

    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    assert name_nfd != name_nfc
    pm = router_mod.get_project_manager()
    project = pm.load_project("demo")
    project["characters"][name_nfd] = {"description": "x"}
    pm.save_project("demo", project)

    uid = _seed_unit(client)
    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"references": [{"type": "character", "name": name_nfc}]},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.integration
def test_unit_references_persisted_in_asset_comparison_form(client: TestClient):
    """add/patch 落盘的 reference name 统一 strip + NFC。"""
    from server.routers import reference_videos as router_mod

    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    pm = router_mod.get_project_manager()
    project = pm.load_project("demo")
    project["characters"][name_nfd] = {"description": "x"}
    pm.save_project("demo", project)

    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={
            "prompt": "镜头1：推门",
            "duration_seconds": 3,
            "references": [{"type": "character", "name": f" {name_nfd} "}],
        },
    )
    assert resp.status_code == 201, resp.text
    unit = resp.json()["unit"]
    assert unit["references"] == [{"type": "character", "name": name_nfc}]

    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{unit['unit_id']}",
        json={
            "references": [
                {"type": "character", "name": f" {name_nfd} "},
                {"type": "character", "name": " 张三 "},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["unit"]["references"] == [
        {"type": "character", "name": name_nfc},
        {"type": "character", "name": "张三"},
    ]


@pytest.mark.unit
def test_patch_unknown_unit_404(client: TestClient):
    resp = client.patch(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/E9U9",
        json={"note": "hi"},
    )
    assert resp.status_code == 404


@pytest.mark.unit
def test_delete_unit_removes_entry(client: TestClient):
    uid = _seed_unit(client)
    resp = client.delete(f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}")
    assert resp.status_code == 204
    resp = client.get("/api/v1/projects/demo/reference-videos/episodes/1/units")
    assert resp.json()["units"] == []


@pytest.mark.unit
def test_delete_unknown_unit_404(client: TestClient):
    resp = client.delete("/api/v1/projects/demo/reference-videos/episodes/1/units/E9U9")
    assert resp.status_code == 404


@pytest.mark.unit
def test_reorder_units_applies_new_order(client: TestClient):
    uid1 = _seed_unit(client)
    uid2 = _seed_unit(client)
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/reorder",
        json={"unit_ids": [uid2, uid1]},
    )
    assert resp.status_code == 200, resp.text
    units = client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json()["units"]
    assert [u["unit_id"] for u in units] == [uid2, uid1]


@pytest.mark.unit
def test_reorder_units_rejects_length_mismatch(client: TestClient):
    uid = _seed_unit(client)
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/reorder",
        json={"unit_ids": [uid, "E1U999"]},
    )
    assert resp.status_code == 400


@pytest.mark.unit
def test_reorder_units_rejects_duplicates(client: TestClient):
    uid = _seed_unit(client)
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/reorder",
        json={"unit_ids": [uid, uid]},
    )
    assert resp.status_code == 400


@pytest.mark.unit
def test_generate_unit_enqueues_task(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    uid = _seed_unit(client)

    enqueued: list[dict] = []

    class _FakeQueue:
        async def enqueue_task(self, **kwargs):
            enqueued.append(kwargs)
            return {"task_id": "task-xyz", "deduped": False}

    from server.routers import reference_videos as router_mod

    monkeypatch.setattr(router_mod, "get_generation_queue", lambda: _FakeQueue())

    resp = client.post(f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate")
    assert resp.status_code == 202, resp.text
    assert resp.json()["task_id"] == "task-xyz"
    assert enqueued[0]["task_type"] == "reference_video"
    assert enqueued[0]["media_type"] == "video"
    assert enqueued[0]["resource_id"] == uid
    # 当前文本只作结构守卫，任务只保定位与请求选项；worker 执行前重读最新剧本。
    assert "prompt" not in enqueued[0]["payload"]
    assert enqueued[0]["payload"]["reference_request_options"] == {"narration_delivery": "post_production"}


@pytest.mark.integration
def test_generate_unit_requires_and_persists_explicit_duration_confirmation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = _seed_unit(client)  # 3s
    _patch_supported_durations(monkeypatch, [4, 8])
    enqueued: list[dict[str, object]] = []

    class _FakeQueue:
        async def enqueue_task(self, **kwargs):
            enqueued.append(kwargs)
            return {"task_id": "task-confirmed", "deduped": False}

    from server.routers import reference_videos as router_mod

    monkeypatch.setattr(router_mod, "get_generation_queue", lambda: _FakeQueue())

    unconfirmed = client.post(f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate")
    assert unconfirmed.status_code == 400
    assert enqueued == []

    confirmed = client.post(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate",
        json={"confirmed_request_duration_seconds": 4},
    )
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json()["projection"]["request_duration"] == 4
    payload = enqueued[0]["payload"]
    assert isinstance(payload, dict)
    assert payload == {
        "script_file": "scripts/episode_1.json",
        "reference_request_options": {
            "narration_delivery": "post_production",
            "confirmed_request_duration_seconds": 4,
        },
    }


@pytest.mark.integration
def test_generate_unit_use_tts_returns_the_current_cross_tier_quote_before_enqueue(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.routers import reference_videos as router_mod
    from server.services.cost_estimation import VideoRequestQuote

    unit_id = _seed_unit(client)
    _patch_supported_durations(monkeypatch, [4, 8, 12])
    enqueued: list[dict[str, object]] = []

    class _FakeQueue:
        async def enqueue_task(self, **kwargs):
            enqueued.append(kwargs)
            return {"task_id": "task-confirmed", "deduped": False}

    async def _current_options(**kwargs):
        return replace(
            kwargs["options"],
            current_tts_duration_seconds=8.0,
            current_visual_duration_seconds=4,
        )

    monkeypatch.setattr(router_mod, "prepare_current_reference_video_request_options", _current_options)
    monkeypatch.setattr(
        router_mod,
        "quote_video_request",
        AsyncMock(return_value=VideoRequestQuote(0.8, "USD", "fake", "fake-model", 8)),
    )
    monkeypatch.setattr(router_mod, "get_generation_queue", lambda: _FakeQueue())
    endpoint = f"/api/v1/projects/demo/reference-videos/episodes/1/units/{unit_id}/generate"

    pending = client.post(endpoint, json={"narration_delivery": "use_tts"})
    assert pending.status_code == 400
    assert pending.json()["detail"]["request_cost"] == {
        "amount": 0.8,
        "currency": "USD",
        "provider_id": "fake",
        "model_id": "fake-model",
        "request_duration_seconds": 8,
    }
    assert enqueued == []

    accepted = client.post(
        endpoint,
        json={"narration_delivery": "use_tts", "confirmed_request_duration_seconds": 8},
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["projection"]["request_cost"]["amount"] == 0.8
    assert enqueued[0]["payload"] == {
        "script_file": "scripts/episode_1.json",
        "reference_request_options": {
            "narration_delivery": "use_tts",
            "confirmed_request_duration_seconds": 8,
        },
    }


@pytest.mark.integration
@pytest.mark.parametrize(
    "case",
    [case for case in SPEECH_CONTRACT_CASES if case.generation_mode == "reference_video"],
    ids=lambda case: case.route_id,
)
def test_three_reference_route_web_video_entries_share_structured_speech_admission(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: SpeechContractCase,
):
    project_path = tmp_path / "projects" / "demo"
    project_file = project_path / "project.json"
    project = json.loads(project_file.read_text(encoding="utf-8"))
    project["content_mode"] = case.content_mode
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    script_file = project_path / "scripts" / "episode_1.json"
    script = json.loads(script_file.read_text(encoding="utf-8"))
    script.update(case.script())
    script_file.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.routers import reference_videos as router_mod

    enqueue = AsyncMock()

    def get_generation_queue():
        return type("Queue", (), {"enqueue_task": enqueue})()

    monkeypatch.setattr(router_mod, "get_generation_queue", get_generation_queue)

    response = client.post("/api/v1/projects/demo/reference-videos/episodes/1/units/E1U1/generate")

    assert response.status_code == 409
    detail = response.json()["detail"]
    problem = detail["problems"][0]
    assert detail["allowed"] is False
    assert detail["unit_id"] == "E1U1"
    assert problem["code"] == "mixed_speech"
    assert [tuple(location["path"]) for location in problem["locations"]] == list(case.expected_locations)
    assert [location["line"] for location in problem["locations"]] == [0, 1]
    assert problem["reason"] == "character_and_narrator_mixed"
    assert problem["action"] == "replan_unit"
    enqueue.assert_not_awaited()


@pytest.mark.unit
def test_generate_unit_bucket_capability_error_returns_400(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """公共投影返回 r2v 能力 blocker 时提交入口不入队。"""
    from lib.i18n import _ as i18n_message
    from lib.reference_video.request_projection import ProjectionProblem

    uid = _seed_unit(client)
    enqueued: list[dict] = []

    class _FakeQueue:
        async def enqueue_task(self, **kwargs):
            enqueued.append(kwargs)
            return {"task_id": "task-xyz", "deduped": False}

    from server.routers import reference_videos as router_mod

    monkeypatch.setattr(router_mod, "get_generation_queue", lambda: _FakeQueue())

    base_project = _projection_with_durations([3, 6, 9])

    async def _reject(**kwargs):
        projection = await base_project(**kwargs)
        assert projection.hydrated_capability == "r2v"
        return replace(
            projection,
            problems=(
                ProjectionProblem(
                    code="video_capability_missing_r2v",
                    blocking=True,
                    params=(("provider", "minimax"), ("model", "MiniMax-Hailuo-2.3")),
                ),
            ),
        )

    monkeypatch.setattr(router_mod, "project_reference_unit_request", _reject)

    resp = client.post(f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate")
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail == {
        "allowed": False,
        "kind": "reference_request_projection",
        "advisory": True,
        "unit_id": uid,
        "declared_capability": "r2v",
        "hydrated_capability": "r2v",
        "provider_id": "fake",
        "model_id": "fake-model",
        "planned_duration": 3,
        "current_visual_duration": None,
        "duration_input": 3,
        "request_duration": 3,
        "problems": [
            {
                "code": "video_capability_missing_r2v",
                "blocking": True,
                "unit_id": uid,
                "locations": [{"path": ["references"], "line": None}],
                "params": {"provider": "minimax", "model": "MiniMax-Hailuo-2.3"},
                "action": "configure_video_model",
                "message": i18n_message("video_capability_missing_r2v", provider="minimax", model="MiniMax-Hailuo-2.3"),
            }
        ],
    }
    assert enqueued == []


@pytest.mark.unit
def test_generate_unit_degenerate_precheck_uses_i2v_bucket(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """无参考图退化 unit 的入队预检按降级后的 i2v 桶过解析闸，与执行侧分流同口径。"""
    script_path = tmp_path / "projects" / "demo" / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"] = [
        {"unit_id": "E1U1", "shots": [{"text": "空镜头"}], "references": [], "duration_seconds": 3}
    ]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.routers import reference_videos as router_mod

    class _FakeQueue:
        async def enqueue_task(self, **kwargs):
            return {"task_id": "task-xyz", "deduped": False}

    monkeypatch.setattr(router_mod, "get_generation_queue", lambda: _FakeQueue())

    checked: list[str] = []
    base_project = _projection_with_durations([3, 6, 9])

    async def _record(**kwargs):
        projection = await base_project(**kwargs)
        checked.append(projection.hydrated_capability)
        return projection

    monkeypatch.setattr(router_mod, "project_reference_unit_request", _record)

    resp = client.post("/api/v1/projects/demo/reference-videos/episodes/1/units/E1U1/generate")
    assert resp.status_code == 202, resp.text
    assert checked == ["i2v"]


@pytest.mark.unit
def test_generate_unit_rejects_blank_prompt(client: TestClient, tmp_path: Path):
    """shots 文本全空白的 unit 在入队时被守卫点拒绝（400），不再漏到执行层失败。"""
    script_path = tmp_path / "projects" / "demo" / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"] = [{"unit_id": "E1U1", "shots": [{"text": "  "}], "references": [], "duration_seconds": 3}]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = client.post("/api/v1/projects/demo/reference-videos/episodes/1/units/E1U1/generate")
    assert resp.status_code == 400, resp.text


@pytest.mark.unit
def test_generate_unit_missing_returns_404(client: TestClient):
    resp = client.post("/api/v1/projects/demo/reference-videos/episodes/1/units/E9U9/generate")
    assert resp.status_code == 404


def _projection_with_durations(durations: list[int]):
    return fake_reference_request_projector(durations=tuple(durations))


def _patch_supported_durations(monkeypatch: pytest.MonkeyPatch, durations: list[int]) -> None:
    from server.routers import reference_videos as router_mod
    from server.services.reference_video_tasks import ProjectDurationContext

    monkeypatch.setattr(router_mod, "project_reference_unit_request", _projection_with_durations(durations))
    monkeypatch.setattr(
        router_mod,
        "resolve_project_duration_context",
        AsyncMock(
            return_value=ProjectDurationContext(
                supported_durations=tuple(durations),
                resolution="1080p",
                provider_id="fake",
                model_name="fake-model",
            )
        ),
    )


def _precheck(client: TestClient, unit_id: str):
    return client.get(f"/api/v1/projects/demo/reference-videos/episodes/1/units/{unit_id}/duration-precheck")


@pytest.mark.integration
def test_precheck_slot_member_needs_no_confirmation(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """总时长本身是档位成员 → 直接入队，无确认。"""
    uid = _seed_unit(client)  # shots 求和 = 3s
    _patch_supported_durations(monkeypatch, [3, 6, 9])

    body = _precheck(client, uid).json()
    assert body == {
        "allowed": True,
        "kind": "reference_request_projection",
        "advisory": True,
        "unit_id": uid,
        "planned_duration": 3,
        "needs_confirmation": False,
        "script_duration": 3,
        "current_visual_duration": None,
        "duration_input": 3,
        "request_duration": 3,
        "adjustment": "exact",
        "declared_capability": "r2v",
        "hydrated_capability": "r2v",
        "provider_id": "fake",
        "model_id": "fake-model",
        "problems": [],
    }


@pytest.mark.integration
def test_precheck_rounds_up_and_needs_confirmation(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """总时长非档位成员且有档位能装下 → 需确认，申请能装下它的最小档位。"""
    uid = _seed_unit(client)  # 3s
    _patch_supported_durations(monkeypatch, [4, 8, 12])

    body = _precheck(client, uid).json()
    assert body["needs_confirmation"] is True
    assert body["script_duration"] == 3
    assert body["request_duration"] == 4
    assert body["adjustment"] == "up"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("current_visual_tier", "reusable_visual_tier", "needs_confirmation", "expected_amount"),
    [(8, 8, False, 0.0), (8, None, False, 0.8), (4, None, True, 0.8)],
)
def test_precheck_prices_the_latest_tts_tier_against_the_selected_visual(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    current_visual_tier: int,
    reusable_visual_tier: int | None,
    needs_confirmation: bool,
    expected_amount: float,
) -> None:
    from server.routers import reference_videos as router_mod
    from server.services.cost_estimation import VideoRequestQuote

    unit_id = _seed_unit(client)
    _patch_supported_durations(monkeypatch, [4, 8, 12])

    async def _current_options(**kwargs):
        return replace(
            kwargs["options"],
            current_tts_duration_seconds=8.0,
            current_visual_duration_seconds=current_visual_tier,
            current_reusable_visual_duration_seconds=reusable_visual_tier,
        )

    monkeypatch.setattr(router_mod, "prepare_current_reference_video_request_options", _current_options)
    monkeypatch.setattr(
        router_mod,
        "quote_video_request",
        AsyncMock(
            return_value=VideoRequestQuote(
                amount=0.8,
                currency="USD",
                provider_id="fake",
                model_id="fake-model",
                request_duration_seconds=8,
            )
        ),
    )

    response = client.get(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{unit_id}/duration-precheck",
        params={"narration_delivery": "use_tts"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request_duration"] == 8
    assert body["needs_confirmation"] is needs_confirmation
    assert body["request_cost"] == {
        "amount": expected_amount,
        "currency": "USD",
        "provider_id": "fake",
        "model_id": "fake-model",
        "request_duration_seconds": 8,
    }


@pytest.mark.integration
def test_precheck_blocks_cross_tier_tts_when_exact_cost_is_unavailable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.routers import reference_videos as router_mod

    unit_id = _seed_unit(client)
    _patch_supported_durations(monkeypatch, [4, 8, 12])

    async def _current_options(**kwargs):
        return replace(
            kwargs["options"],
            current_tts_duration_seconds=8.0,
            current_visual_duration_seconds=4,
        )

    monkeypatch.setattr(router_mod, "prepare_current_reference_video_request_options", _current_options)
    monkeypatch.setattr(router_mod, "quote_video_request", AsyncMock(return_value=None))

    response = client.get(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{unit_id}/duration-precheck",
        params={"narration_delivery": "use_tts"},
    )

    assert response.status_code == 400
    assert [problem["code"] for problem in response.json()["detail"]["problems"]] == [
        "reference_duration_confirmation_required",
        "video_request_cost_unavailable",
    ]


@pytest.mark.integration
def test_precheck_use_tts_while_regenerating_returns_canonical_problem(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An active explicit regeneration shadows the old formal audio for Web requests."""
    from lib.reference_video.request_projection import ProjectionProblem
    from server.routers import reference_videos as router_mod

    unit_id = _seed_unit(client)
    base_project = _projection_with_durations([3, 6, 9])

    async def _project(**kwargs):
        assert kwargs["tts_in_progress"] is True
        projection = await base_project(**kwargs)
        return replace(
            projection,
            problems=(
                ProjectionProblem(
                    code="tts_generating",
                    blocking=True,
                    reason="tts_generation_in_progress",
                    action="wait_for_tts",
                    locations=(("generated_assets", "narration_audio"),),
                ),
            ),
        )

    monkeypatch.setattr(router_mod, "tts_task_in_progress", AsyncMock(return_value=True))
    monkeypatch.setattr(router_mod, "project_reference_unit_request", _project)

    response = client.get(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{unit_id}/duration-precheck",
        params={"narration_delivery": "use_tts"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["problems"][0] == {
        "code": "tts_generating",
        "blocking": True,
        "unit_id": unit_id,
        "locations": [{"path": ["generated_assets", "narration_audio"], "line": None}],
        "params": {},
        "action": "wait_for_tts",
        "reason": "tts_generation_in_progress",
        "message": "旁白音频仍在生成；请等待完成后再使用 TTS 交付",
    }


@pytest.mark.integration
def test_precheck_uses_actual_tts_duration_as_floor(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    created = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：海面\n{旁白正文。}", "duration_seconds": 3, "references": []},
    )
    assert created.status_code == 201, created.text
    uid = created.json()["unit"]["unit_id"]  # 剧本 3s，实际旁白 9.5s
    _patch_supported_durations(monkeypatch, [4, 8, 12])

    from lib.artifact_manifest import ArtifactComparison, ArtifactStatus
    from lib.narration_delivery import NarrationAudioEvidence, TtsSynthesisSettings, prepare_narration_delivery

    async def _project_with_current_tts(**kwargs):
        preparation = prepare_narration_delivery(
            delivery="use_tts",
            preparation=admit_script_unit("video_units", kwargs["unit"]).preparation,
            artifact_path=f"audio/segment_{uid}.wav",
            settings=TtsSynthesisSettings("fake-audio", "tts-model", "voice", None),
            evidence=NarrationAudioEvidence(
                comparison=ArtifactComparison(
                    status=ArtifactStatus.CURRENT,
                    artifact_path=f"audio/segment_{uid}.wav",
                ),
                present=True,
                duration_seconds=9.5,
            ),
        )
        kwargs["options"] = replace(
            kwargs["options"],
            current_tts_duration_seconds=9.5,
            narration_preparation=preparation,
        )
        return await _projection_with_durations([4, 8, 12])(**kwargs)

    from lib.speech_composition import admit_script_unit
    from server.routers import reference_videos as router_mod

    async def _options_identity(**kwargs):
        return kwargs["options"]

    monkeypatch.setattr(router_mod, "prepare_current_reference_video_request_options", _options_identity)
    monkeypatch.setattr(router_mod, "project_reference_unit_request", _project_with_current_tts)

    response = client.get(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/duration-precheck",
        params={"narration_delivery": "use_tts"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["script_duration"] == 3
    assert body["duration_input"] == 9.5
    assert body["request_duration"] == 12
    assert body["adjustment"] == "up"


@pytest.mark.integration
def test_precheck_over_largest_slot_requires_replan(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """总时长超过最大档位时不得截断，返回结构化重新规划 blocker。"""
    uid = _seed_unit(client)  # 3s
    _patch_supported_durations(monkeypatch, [1, 2])

    response = _precheck(client, uid)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["request_duration"] == 2
    assert detail["problems"][0]["code"] == "needs_replan"
    assert detail["problems"][0]["action"] == "replan_unit"


@pytest.mark.integration
def test_precheck_empty_duration_metadata_returns_structured_blocker(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """档位集为空时 fail loud，不返回伪可执行的 unconstrained 结果。"""
    from lib.i18n import _ as i18n_message

    uid = _seed_unit(client)
    _patch_supported_durations(monkeypatch, [])

    response = _precheck(client, uid)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["kind"] == "reference_request_projection"
    assert detail["unit_id"] == uid
    assert detail["problems"][0] == {
        "code": "reference_supported_durations_missing",
        "blocking": True,
        "unit_id": uid,
        "locations": [{"path": ["duration_seconds"], "line": None}],
        "params": {"provider": "fake", "model": "fake-model"},
        "action": "configure_video_model",
        "message": i18n_message("reference_supported_durations_missing", provider="fake", model="fake-model"),
    }


@pytest.mark.integration
def test_precheck_formats_missing_asset_message_for_people(client: TestClient, tmp_path: Path) -> None:
    from lib.i18n import _ as i18n_message

    uid = _seed_unit(client)
    (tmp_path / "projects" / "demo" / "characters" / "张三.png").unlink()

    response = _precheck(client, uid)

    assert response.status_code == 400, response.text
    problems = response.json()["detail"]["problems"]
    missing = next(problem for problem in problems if problem["code"] == "reference_asset_missing")
    assert missing["params"]["missing"] == [["character", "张三"]]
    assert missing["params"]["missing_text"] == "character: 张三"
    assert missing["message"] == i18n_message("reference_asset_missing", missing_text="character: 张三")


@pytest.mark.integration
def test_precheck_missing_unit_returns_404(client: TestClient):
    assert _precheck(client, "E9U9").status_code == 404


@pytest.mark.unit
def test_add_unit_stale_script_file_returns_404(client: TestClient, tmp_path: Path):
    """project.json 残留指向已删除文件的 script_file 时，写端点应返回 404 而非 500。"""
    (tmp_path / "projects" / "demo" / "scripts" / "episode_1.json").unlink()
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：@张三 出现", "references": [{"type": "character", "name": "张三"}]},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.unit
def test_add_unit_unknown_project_returns_404(client: TestClient):
    resp = client.post(
        "/api/v1/projects/missing/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：@张三 出现", "references": []},
    )
    assert resp.status_code == 404


@pytest.mark.unit
def test_add_unit_unknown_episode_returns_404(client: TestClient):
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/99/units",
        json={"prompt": "镜头1：空镜", "references": []},
    )
    assert resp.status_code == 404


@pytest.mark.unit
def test_write_endpoint_rejects_non_reference_video_mode(client: TestClient, tmp_path: Path):
    """episode 非 reference_video 模式时，写端点应返回 409。"""
    script_path = tmp_path / "projects" / "demo" / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["generation_mode"] = "image"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    proj_path = tmp_path / "projects" / "demo" / "project.json"
    proj = json.loads(proj_path.read_text(encoding="utf-8"))
    proj["generation_mode"] = "image"
    proj_path.write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")

    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：空镜", "references": []},
    )
    assert resp.status_code == 409


@pytest.mark.unit
def test_patch_unit_duration_override_without_header(client: TestClient):
    """无 header 的 prompt → override=True，duration_seconds 直接生效。"""
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "@张三 推门", "references": [{"type": "character", "name": "张三"}], "duration_seconds": 5},
    )
    assert resp.status_code == 201, resp.text
    uid = resp.json()["unit"]["unit_id"]
    assert resp.json()["unit"]["duration_seconds"] == 5

    # 仅改 duration_seconds（无 prompt）：走 elif 分支按已有 override 直接覆盖时长
    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"duration_seconds": 8, "transition_to_next": "fade", "note": "hi"},
    )
    assert resp.status_code == 200, resp.text
    unit = resp.json()["unit"]
    assert unit["duration_seconds"] == 8
    assert unit["transition_to_next"] == "fade"
    assert unit["note"] == "hi"

    # 带无 header 的新 prompt + duration_seconds：走 prompt 分支并对单镜头 override 时长
    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"prompt": "@张三 转身离开", "duration_seconds": 7},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["unit"]["duration_seconds"] == 7


@pytest.mark.unit
def test_reorder_units_rejects_true_duplicate(client: TestClient):
    """长度匹配但含重复 ID → 命中 duplicate 校验分支。"""
    uid1 = _seed_unit(client)
    _uid2 = _seed_unit(client)
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/reorder",
        json={"unit_ids": [uid1, uid1]},
    )
    assert resp.status_code == 400
    assert "重复" in resp.json()["detail"]


@pytest.mark.unit
def test_reorder_units_rejects_unknown_id_set_mismatch(client: TestClient):
    """长度匹配、无重复，但 ID 集合与现有不一致 → set mismatch 分支。"""
    uid1 = _seed_unit(client)
    _uid2 = _seed_unit(client)
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/reorder",
        json={"unit_ids": [uid1, "E1U999"]},
    )
    assert resp.status_code == 400
    assert "不匹配" in resp.json()["detail"]


@pytest.mark.unit
def test_add_unit_concurrent_rebind_returns_409(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """加锁前后 episode→script_file 被并发改绑 → 写端点返回 409（前端可重试）。"""
    from server.routers import reference_videos as router_mod

    pm = router_mod.get_project_manager()

    # 模拟并发 PATCH 改绑：持锁复核读到 episode 1 已指向另一个脚本
    def _rebound(_project_name: str) -> dict:
        return {
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_2.json"}],
        }

    monkeypatch.setattr(pm, "_read_project_raw_unlocked", _rebound)

    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：空镜", "references": []},
    )
    assert resp.status_code == 409, resp.text


# ============ 解析预览 ============


def _patch_video_caps(monkeypatch: pytest.MonkeyPatch, caps: dict) -> None:
    from server.routers import reference_videos as router_mod

    monkeypatch.setattr(router_mod, "project_video_caps", AsyncMock(return_value=caps))


def _preview(client: TestClient, prompt: str):
    return client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/script-preview",
        json={"prompt": prompt},
    )


@pytest.mark.integration
def test_script_preview_derives_shots_references_and_utterances(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _patch_video_caps(monkeypatch, {})
    body = _preview(client, "镜头1：@[酒馆] 内景。\n@[张三]：{我来了}\n{那年冬天格外冷}").json()

    assert [s["index"] for s in body["shots"]] == [1]
    # speaker 位不计入参考图
    assert body["references"] == [{"type": "scene", "name": "酒馆"}]
    assert body["utterances"] == [
        {"shot_index": 1, "kind": "dialogue", "speaker": "张三", "text": "我来了"},
        {"shot_index": 1, "kind": "voiceover", "speaker": None, "text": "那年冬天格外冷"},
    ]
    assert body["warnings"] == []


@pytest.mark.integration
def test_script_preview_returns_localized_warnings(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _patch_video_caps(monkeypatch, {})
    body = _preview(client, "镜头1：@[王五] 推门。").json()
    assert [w["key"] for w in body["warnings"]] == ["ref_warn_unregistered_mention"]
    assert "王五" in body["warnings"][0]["message"]


@pytest.mark.integration
def test_script_preview_uses_project_voice_capabilities(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _patch_video_caps(monkeypatch, {"voice_consistency": "none", "model": "silent-01"})
    body = _preview(client, "镜头1：开场。\n@[张三]：{我来了}").json()
    assert [w["key"] for w in body["warnings"]] == ["ref_warn_silent_model"]
    assert "silent-01" in body["warnings"][0]["message"]


@pytest.mark.integration
def test_script_preview_warns_when_episode_is_silent(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """本集设为无声视频时，预览面板告知声音一致性不生效（模型仍是 A 类）。"""
    _patch_video_caps(
        monkeypatch,
        {
            "voice_consistency": "native",
            "max_reference_audio_count": 3,
            "requested_generate_audio": False,
            "model": "doubao-seedance-2-0",
        },
    )
    body = _preview(client, "镜头1：开场。\n@[张三]：{我来了}").json()
    assert [w["key"] for w in body["warnings"]] == ["ref_warn_silent_episode"]
    assert body["warnings"][0]["message"] != "ref_warn_silent_episode"
    # 台词照常派生：无声只影响参考音频，不影响下发给供应商的台词文本
    assert [u["text"] for u in body["utterances"]] == ["我来了"]


@pytest.mark.integration
def test_script_preview_404_for_unknown_episode(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _patch_video_caps(monkeypatch, {})
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/9/script-preview",
        json={"prompt": "镜头1：开场。"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 批量准入：整批要么全建、要么零任务
# ---------------------------------------------------------------------------

BATCH_ENDPOINT = "/api/v1/projects/demo/reference-videos/episodes/1/units/generate-batch"


def _seed_second_unit(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={
            "prompt": "镜头1：@酒馆 全景",
            "duration_seconds": 3,
            "references": [{"type": "scene", "name": "酒馆"}],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["unit"]["unit_id"]


def _patch_batch_admission(
    monkeypatch: pytest.MonkeyPatch,
    *,
    durations: list[int],
    active_tasks: list[dict[str, object]] | None = None,
    active_tts: frozenset[str] = frozenset(),
    quote_amount: float | None = None,
    fail_enqueue_after: int | None = None,
) -> list[dict[str, object]]:
    """把批量准入的当前状态查询接到进程内替身，返回入队记录。

    准入要读任务库、TTS 在途状态与报价；路由测试不带这些依赖，逐个注入替身。

    ``fail_enqueue_after`` 让第 N 次之后的入队抛错，用来复现顺序入队中途中断。
    """

    from lib.reference_video.request_projection import ReferenceRequestOptions
    from server.services import video_batch_admission as admission_mod
    from server.services.cost_estimation import VideoRequestQuote

    async def _no_active(**_kwargs):
        return list(active_tasks or [])

    async def _tts(**_kwargs):
        return active_tts

    async def _current_options(*, options: ReferenceRequestOptions, **_kwargs):
        return options

    async def _quote(facts, _session_factory):
        if quote_amount is None:
            return None
        return VideoRequestQuote(
            amount=quote_amount,
            currency="USD",
            provider_id=facts.provider_id,
            model_id=facts.model_id,
            request_duration_seconds=facts.duration_seconds,
        )

    monkeypatch.setattr(admission_mod, "project_reference_unit_request", _projection_with_durations(durations))
    monkeypatch.setattr(admission_mod, "get_active_tasks_for_resources", _no_active)
    monkeypatch.setattr(admission_mod, "active_tts_resource_ids", _tts)
    monkeypatch.setattr(admission_mod, "prepare_current_reference_video_request_options", _current_options)
    monkeypatch.setattr(admission_mod, "quote_video_request", _quote)

    enqueued: list[dict[str, object]] = []

    async def _enqueue_task_only(**kwargs):
        if fail_enqueue_after is not None and len(enqueued) >= fail_enqueue_after:
            raise RuntimeError("queue unavailable")
        enqueued.append(kwargs)
        return {"task_id": f"task-{len(enqueued)}", "deduped": False}

    monkeypatch.setattr("lib.generation_queue_client.enqueue_task_only", _enqueue_task_only)
    return enqueued


@pytest.mark.integration
def test_generate_batch_creates_the_whole_task_set_in_one_admission(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _seed_unit(client)
    second = _seed_second_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "admitted"
    assert len(body["task_ids"]) == 2
    # 逐 unit 的任务行让调用方各等各的，不必等到全批落库。
    assert sorted(body["task_ids_by_unit"]) == sorted([first, second])
    assert sorted(body["task_ids_by_unit"].values()) == sorted(body["task_ids"])
    assert [item["unit_id"] for item in body["units"]] == [first, second]
    assert all(item["admitted"] for item in body["units"])
    assert [call["resource_id"] for call in enqueued] == [first, second]
    # 请求只落定位与本次选项：执行内容由 worker 起跑时重读最新剧本。
    assert enqueued[0]["payload"] == {
        "script_file": "scripts/episode_1.json",
        "reference_request_options": {"narration_delivery": "post_production"},
    }
    assert enqueued[0]["source"] == "webui"


@pytest.mark.integration
def test_generate_batch_keeps_created_tasks_when_the_enqueue_is_interrupted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """入队中断不撤销已创建的任务：它们是准入通过的完整付费单元，照常执行。

    没轮到的 unit 逐 ID 报出来，界面据此只释放它自己的占用标记。
    """

    first = _seed_unit(client)
    second = _seed_second_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9], fail_enqueue_after=1)

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "admitted"
    assert [call["resource_id"] for call in enqueued] == [first]
    assert body["task_ids"] == ["task-1"]
    assert body["task_ids_by_unit"] == {first: "task-1"}
    assert [entry["unit_id"] for entry in body["enqueue_failures"]] == [second]
    problem = body["enqueue_failures"][0]["problem"]
    assert problem["code"] == "generation_enqueue_interrupted"
    assert problem["action"] == "retry"
    assert problem["message"]


@pytest.mark.integration
def test_generate_batch_after_an_interruption_only_queues_what_never_reached_the_queue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """下次「缺失即生成」只补没入队的那个：已建任务产出的成片是现行产物，不重复付费。"""

    first = _seed_unit(client)
    second = _seed_second_unit(client)
    _patch_batch_admission(monkeypatch, durations=[3, 6, 9], fail_enqueue_after=1)

    interrupted = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"}).json()
    assert [entry["unit_id"] for entry in interrupted["enqueue_failures"]] == [second]

    # 第一个 unit 的任务照常跑完并落盘，第二个从未创建任务、也从未计费。
    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    for unit in script["video_units"]:
        if unit["unit_id"] == first:
            unit["generated_assets"] = {"video_clip": f"reference_videos/{first}.mp4"}
    pm.save_script("demo", script, "scripts/episode_1.json")
    clip = pm.get_project_path("demo") / "reference_videos"
    clip.mkdir(parents=True, exist_ok=True)
    (clip / f"{first}.mp4").write_bytes(b"\x00")

    retried = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])
    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "admitted"
    assert [item["unit_id"] for item in body["units"]] == [second]
    assert [call["resource_id"] for call in retried] == [second]
    assert body["enqueue_failures"] == []


@pytest.mark.integration
def test_generate_batch_creates_zero_tasks_when_one_unit_is_blocked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一个单元有问题即整批不成立，另一个单元如实报告是被谁扣下的。"""

    first = _seed_unit(client)
    second = _seed_second_unit(client)
    enqueued = _patch_batch_admission(
        monkeypatch,
        durations=[3, 6, 9],
        active_tasks=[{"resource_id": second, "id": "task-running", "status": "running"}],
    )

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert body["task_ids"] == []
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes[second] == ["generation_active_task_conflict"]
    assert codes[first] == ["generation_batch_admission_withheld"]


@pytest.mark.integration
def test_generate_batch_reports_every_gap_not_only_the_first(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UI 要一次看到全部缺口；只报第一个会让用户逐轮试错。"""

    first = _seed_unit(client)
    second = _seed_second_unit(client)
    enqueued = _patch_batch_admission(
        monkeypatch,
        durations=[3, 6, 9],
        active_tasks=[{"resource_id": second, "id": "task-running", "status": "running"}],
    )

    resp = client.post(
        BATCH_ENDPOINT, json={"narration_delivery": "post_production", "unit_ids": [first, second, "E9U9"]}
    )

    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes["E9U9"] == ["generation_unit_not_found"]
    assert codes[second] == ["generation_active_task_conflict"]
    assert codes[first] == ["generation_batch_admission_withheld"]
    # 每条缺口都带已本地化的可读原因，调用方不必自己拼文案。
    assert all(problem["message"] for item in body["units"] for problem in item["problems"])


@pytest.mark.integration
def test_generate_batch_aggregates_the_confirmation_by_tier_then_enqueues_on_consent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _seed_unit(client)
    second = _seed_second_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[4, 8], quote_amount=0.8)

    pending = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert pending.status_code == 200, pending.text
    body = pending.json()
    assert body["decision"] == "confirmation_required"
    assert body["task_ids"] == []
    assert enqueued == []
    tiers = body["confirmation"]["tiers"]
    assert len(tiers) == 1
    assert tiers[0]["request_duration_seconds"] == 4
    assert tiers[0]["unit_count"] == 2
    assert sorted(tiers[0]["unit_ids"]) == sorted([first, second])
    assert tiers[0]["cost_amount"] == pytest.approx(1.6)
    assert tiers[0]["cost_currency"] == "USD"

    accepted = client.post(
        BATCH_ENDPOINT,
        json={"narration_delivery": "post_production", "confirmed_request_durations": {first: 4, second: 4}},
    )

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["decision"] == "admitted"
    assert [call["resource_id"] for call in enqueued] == [first, second]
    # 确认只对本次请求有效，不冻结执行内容。
    options = cast(dict[str, Any], enqueued[0]["payload"])["reference_request_options"]
    assert options == {
        "narration_delivery": "post_production",
        "confirmed_request_duration_seconds": 4,
    }


@pytest.mark.integration
@pytest.mark.parametrize("invalid", [0, -4, 4.5])
def test_generate_batch_rejects_non_positive_confirmed_duration(client: TestClient, invalid: object) -> None:
    """确认的档位是秒数，0 / 负数在边界拒绝，不落到请求选项构造里变成 500。"""

    first = _seed_unit(client)

    resp = client.post(
        BATCH_ENDPOINT, json={"narration_delivery": "post_production", "confirmed_request_durations": {first: invalid}}
    )

    assert resp.status_code == 422, resp.text


@pytest.mark.integration
def test_generate_batch_partial_consent_still_creates_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只确认了一半的档位不算通过：剩下那个仍在等用户拍板。"""

    first = _seed_unit(client)
    second = _seed_second_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[4, 8], quote_amount=0.8)

    resp = client.post(
        BATCH_ENDPOINT, json={"narration_delivery": "post_production", "confirmed_request_durations": {first: 4}}
    )

    body = resp.json()
    assert body["decision"] == "confirmation_required"
    assert enqueued == []
    assert body["confirmation"]["tiers"][0]["unit_ids"] == [second]


@pytest.mark.integration
def test_generate_batch_repeating_an_admitted_request_reports_dedup(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重复提交不产生第二批任务：队列去重命中即如实报告。"""

    _seed_unit(client)
    _patch_batch_admission(monkeypatch, durations=[3, 6, 9])
    created: list[str] = []

    async def _enqueue_task_only(**kwargs):
        deduped = bool(created)
        created.append(str(kwargs["resource_id"]))
        return {"task_id": "task-1", "deduped": deduped}

    monkeypatch.setattr("lib.generation_queue_client.enqueue_task_only", _enqueue_task_only)

    first = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})
    second = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert first.json()["deduped"] is False
    assert second.json()["deduped"] is True
    assert first.json()["task_ids"] == second.json()["task_ids"] == ["task-1"]


@pytest.mark.integration
def test_generate_batch_post_production_does_not_consult_tts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """后期配音不以 TTS 为输入：未配置或过期都不该拦住这批。"""

    _seed_unit(client)
    _patch_batch_admission(monkeypatch, durations=[3, 6, 9])
    probed: list[object] = []

    from server.services import video_batch_admission as admission_mod

    async def _tts(**kwargs):
        probed.append(kwargs)
        return frozenset()

    monkeypatch.setattr(admission_mod, "active_tts_resource_ids", _tts)

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.json()["decision"] == "admitted"
    assert probed == []


@pytest.mark.integration
def test_generate_batch_use_tts_resolves_every_target_against_current_tts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """选了「使用当前 TTS」就按当前 TTS 取档：整批一次探明在途 TTS，逐 unit 按该口径投影。"""

    unit_id = _seed_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    from server.services import video_batch_admission as admission_mod

    probed: list[list[str]] = []
    deliveries: list[str] = []
    base_projection = _projection_with_durations([3, 6, 9])

    async def _tts(*, resource_ids, **_kwargs):
        probed.append(list(resource_ids))
        return frozenset()

    async def _project(**kwargs):
        deliveries.append(kwargs["options"].narration_delivery)
        return await base_projection(**kwargs)

    monkeypatch.setattr(admission_mod, "active_tts_resource_ids", _tts)
    monkeypatch.setattr(admission_mod, "project_reference_unit_request", _project)

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "use_tts"})

    body = resp.json()
    assert resp.status_code == 200, resp.text
    assert body["narration_delivery"] == "use_tts"
    assert probed == [[unit_id]]
    assert deliveries == ["use_tts"]
    options = cast(dict[str, Any], enqueued[0]["payload"])["reference_request_options"]
    assert options["narration_delivery"] == "use_tts"


@pytest.mark.unit
def test_generate_batch_rejects_an_empty_selection(client: TestClient) -> None:
    """空数组不是「全部」：它是一次没有目标的请求，静默按全集处理会超出用户本意。"""

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production", "unit_ids": []})

    assert resp.status_code == 400
    assert resp.json()["detail"]


@pytest.mark.integration
def test_generate_batch_skips_units_that_already_have_a_clip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """陈旧但可用的旧产物不自动重生：缺失即生成的语义里它本就不是目标。"""

    first = _seed_unit(client)
    second = _seed_second_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    for unit in script["video_units"]:
        if unit["unit_id"] == first:
            unit["generated_assets"] = {"video_clip": f"reference_videos/{first}.mp4"}
    pm.save_script("demo", script, "scripts/episode_1.json")
    clip = pm.get_project_path("demo") / "reference_videos"
    clip.mkdir(parents=True, exist_ok=True)
    (clip / f"{first}.mp4").write_bytes(b"\x00")

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    body = resp.json()
    assert body["decision"] == "admitted"
    assert [item["unit_id"] for item in body["units"]] == [second]
    assert [call["resource_id"] for call in enqueued] == [second]


@pytest.mark.integration
def test_generate_batch_regenerates_a_unit_whose_recorded_clip_is_gone(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧 schema 项目里剧本仍登记着成片路径、文件却已被删：该 unit 判为缺失重新入队。

    这条腿上「另一条可复用的判定」（手动上传/登记路径可用）曾能越过存在性核实，
    用户删掉文件后整批永远补不回这一个 unit。
    """

    first = _seed_unit(client)
    second = _seed_second_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    for unit in script["video_units"]:
        if unit["unit_id"] == first:
            unit["generated_assets"] = {"video_clip": f"reference_videos/{first}.mp4"}
    pm.save_script("demo", script, "scripts/episode_1.json")
    # 登记了路径但目录里没有这个文件——正是用户在文件系统里删掉成片后的形态。
    assert not (pm.get_project_path("demo") / "reference_videos" / f"{first}.mp4").exists()

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    body = resp.json()
    assert body["decision"] == "admitted"
    assert sorted(item["unit_id"] for item in body["units"]) == sorted([first, second])
    assert sorted(call["resource_id"] for call in enqueued) == sorted([first, second])


@pytest.mark.integration
def test_generate_batch_creates_zero_tasks_when_one_artifact_state_is_unreadable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """产物状态读不出的 unit 属于这次请求：它带着自己的问题进准入，整批停下，健康的 unit 不计费。"""

    from lib.artifact_manifest import ArtifactBlocker, ArtifactStatus
    from lib.generation_result import GenerationCandidate, GenerationTargetState
    from server.routers import reference_videos as router_mod

    first = _seed_unit(client)
    second = _seed_second_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    resolve_targets = router_mod.resolve_reference_batch_targets

    def _one_unavailable(**kwargs: Any):
        targets, selection, states = resolve_targets(**kwargs)
        blocked = GenerationTargetState(
            candidate=GenerationCandidate(unit_id=second),
            status=ArtifactStatus.BLOCKED,
            blocker=ArtifactBlocker(code="artifact_manifest_unreadable", path="", detail="侧车读不出"),
        )
        return (
            [unit for unit in targets if unit["unit_id"] != second],
            replace(selection, targets=(states[first],), unavailable=(blocked,)),
            states,
        )

    monkeypatch.setattr(router_mod, "resolve_reference_batch_targets", _one_unavailable)

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert body["task_ids"] == []
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes[second] == ["generation_artifact_state_unavailable"]
    assert codes[first] == ["generation_batch_admission_withheld"]


@pytest.mark.unit
def test_reference_unit_task_spec_rejects_a_non_list_shots() -> None:
    """脏值 shots 报可入队性问题，而不是在拼接镜头文本时把整批打成 500。"""

    from server.services.video_batch_admission import reference_unit_task_spec

    with pytest.raises(ValueError, match="shots 必须是数组"):
        reference_unit_task_spec({"unit_id": "E1U1", "shots": 42}, "scripts/episode_1.json")


@pytest.mark.integration
def test_generate_batch_reports_malformed_units_instead_of_shrinking_the_batch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺 id、重复 id、脏 duration 的 unit 都进结论：把它们丢出目标集合，健康的 unit 会独自计费。"""

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [
        healthy,
        {**healthy, "unit_id": ""},
        {**healthy},
        {**healthy, "unit_id": "E1U9", "duration_seconds": "abc"},
    ]
    # 直接写盘：脏数据是外部编辑或 Agent 裸写产生的，写入校验本就不会放它过去。
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert body["task_ids"] == []
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    invalid = [
        unit_id for unit_id, problem_codes in codes.items() if problem_codes == ["generation_unit_request_invalid"]
    ]
    assert len(invalid) == 3, codes
    assert codes[first] == ["generation_batch_admission_withheld"]


@pytest.mark.integration
def test_generate_batch_explicit_ids_ignore_unrelated_malformed_units(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点名重做的目标集合由调用方给定：剧本别处的坏条目不参与判定，不否决这次点名。"""

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [healthy, {**healthy, "unit_id": ""}]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production", "unit_ids": [first]})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "admitted"
    assert [call["resource_id"] for call in enqueued] == [first]


@pytest.mark.integration
def test_generate_batch_reports_non_object_units_instead_of_dropping_them(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非对象条目同样成不了目标：它被记名报告，健康的 unit 不会独自入队计费。"""

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [42, healthy]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes["video_units[0]"] == ["generation_unit_request_invalid"]
    assert codes[first] == ["generation_batch_admission_withheld"]


@pytest.mark.integration
def test_generate_batch_reports_a_non_scalar_unit_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """unit_id 是对象/数组时在入队前拒收：它能混过字符串化进队列，执行期比对原值才找不到 unit。"""

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    broken = {**healthy, "unit_id": {"id": "U9"}}
    script["video_units"] = [broken, healthy]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes["video_units[0]"] == ["generation_unit_request_invalid"]
    assert codes[first] == ["generation_batch_admission_withheld"]


@pytest.mark.integration
def test_generate_batch_reports_a_duplicated_unit_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一个 unit_id 出现两次时无从判定要做哪一条：整批拒收，不默认拿第一份去计费。"""

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [healthy, {**healthy}]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes[f"{first}#1"] == ["generation_unit_request_invalid"]


@pytest.mark.integration
def test_generate_batch_requires_an_explicit_narration_delivery(client: TestClient) -> None:
    """不声明旁白交付方式的批量请求直接拒收：默认成后期配音等于替调用方做了这个选择。"""

    _seed_unit(client)

    resp = client.post(BATCH_ENDPOINT, json={})

    assert resp.status_code == 422, resp.text
    assert any(item["loc"][-1] == "narration_delivery" for item in resp.json()["detail"])


@pytest.mark.integration
def test_generate_batch_reports_a_numeric_unit_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """数字 unit_id 同样在入队前拒收：字符串化后执行期按原值比对找不到 unit。"""

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [{**healthy, "unit_id": 0}, healthy]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes["video_units[0]"] == ["generation_unit_request_invalid"]
    assert codes[first] == ["generation_batch_admission_withheld"]


@pytest.mark.integration
def test_a_duplicate_marker_never_shadows_a_requested_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """点名了一个剧本里没有的名字时，重复条目的诊断名不能与它撞上：两条结论各占一行。"""

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [healthy, {**healthy}]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = client.post(
        BATCH_ENDPOINT,
        json={"narration_delivery": "post_production", "unit_ids": [first, f"{first}#1"]},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes[f"{first}#1"] == ["generation_unit_not_found"]
    assert codes[f"{first}#1*"] == ["generation_unit_request_invalid"]


@pytest.mark.integration
def test_a_duplicate_marker_never_shadows_a_real_unit_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """重复条目按 `id#序号` 记名，剧本里恰好有同名 unit 时另起一个名字。"""

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [healthy, {**healthy}, {**healthy, "unit_id": f"{first}#1"}]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    unit_ids = [item["unit_id"] for item in body["units"]]
    assert len(unit_ids) == len(set(unit_ids)), unit_ids
    assert set(unit_ids) == {first, f"{first}#1", f"{first}#1*"}
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes[first] == ["generation_batch_admission_withheld"]


@pytest.mark.integration
def test_generate_batch_refuses_a_path_like_unit_id_before_enqueue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """unit_id 带路径片段的条目在建任务之前拒收：交给 worker 拼路径时才拒，健康的兄弟已经在跑并计费。"""

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [healthy, {**healthy, "unit_id": "../bad"}]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes["../bad"] == ["generation_unit_request_invalid"]


@pytest.mark.integration
def test_generate_batch_reports_a_non_list_video_units_container(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """video_units 不是数组时报成结构问题：遍历它会把请求打成 500，假值又会被当作空批次报成通过。"""

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    _seed_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    script["video_units"] = 42
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes["video_units"] == ["generation_unit_request_invalid"]


@pytest.mark.unit
def test_generate_unit_rejects_a_path_like_unit_id(client: TestClient, tmp_path: Path):
    """unit_id 带路径分隔符时当场拒绝（400），不漏到执行层拼产物路径时才失败。"""
    script_path = tmp_path / "projects" / "demo" / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"] = [
        {"unit_id": "a\\b", "shots": [{"text": "镜头平移"}], "references": [], "duration_seconds": 3}
    ]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = client.post("/api/v1/projects/demo/reference-videos/episodes/1/units/a%5Cb/generate")
    assert resp.status_code == 400, resp.text


@pytest.mark.integration
def test_generate_batch_reports_a_falsy_video_units_container(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """video_units 是假值（如 false）时同样报成结构问题，不被当作空批次报成通过。"""

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    _seed_unit(client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    script["video_units"] = False
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = client.post(BATCH_ENDPOINT, json={"narration_delivery": "post_production"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes["video_units"] == ["generation_unit_request_invalid"]
