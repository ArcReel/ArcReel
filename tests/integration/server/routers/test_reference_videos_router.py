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

from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_migrations.v7_to_v8_artifact_manifest import migrate_v7_to_v8
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.factories import make_display_names, make_video_request_facts
from tests.fakes import (
    fake_reference_request_facts,
    fake_reference_request_projector,
    use_active_tasks_lookup,
    use_generation_queue,
    use_project_manager,
    use_reference_projection,
)
from tests.speech_contract_cases import SPEECH_CONTRACT_CASES, SpeechContractCase

#: Web new-unit request fixture, compared byte-for-byte by frontend component tests.
_ADD_UNIT_CONTRACT = (
    Path(__file__).resolve().parents[4] / "frontend" / "src" / "test" / "contracts" / "reference-unit-add-requests.json"
)


@pytest.fixture
def reference_videos_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Redirect projects_root to tmp_path.
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    proj_dir = projects_root / "demo"
    proj_dir.mkdir()
    (proj_dir / "scripts").mkdir()
    (proj_dir / "project.json").write_text(
        json.dumps(
            {
                "schema_version": 7,
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

    migrate_v7_to_v8(proj_dir)
    migrate_project_dir(proj_dir)

    # Patch project_manager roots.
    from lib.project.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    custom_pm = ProjectManager(tmp_path)
    use_project_manager(monkeypatch, custom_pm)
    # Shared request-projection resolvers need DBs; inject in-process capabilities for route tests.
    use_reference_projection(monkeypatch, _projection_with_durations([3, 6, 9]))

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router_mod.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="test", role="admin")
    return TestClient(app)


def test_list_units_empty(reference_videos_client: TestClient):
    resp = reference_videos_client.get("/api/v1/projects/demo/reference-videos/episodes/1/units")
    assert resp.status_code == 200
    assert resp.json() == {"units": [], "unit_capabilities": {}}


def test_list_units_404_for_unknown_project(reference_videos_client: TestClient):
    resp = reference_videos_client.get("/api/v1/projects/missing/reference-videos/episodes/1/units")
    assert resp.status_code == 404


def test_add_unit_creates_minimal_entry(reference_videos_client: TestClient):
    resp = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：@张三 推门", "duration_seconds": 3},
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["unit"]["unit_id"].startswith("E1U")
    assert payload["unit"]["duration_seconds"] == 3
    assert payload["unit"]["text"] == "镜头1：@张三 推门"


def test_unit_added_after_deleting_the_last_one_gets_a_new_id(reference_videos_client: TestClient):
    url = "/api/v1/projects/demo/reference-videos/episodes/1/units"
    first = reference_videos_client.post(url, json={"prompt": "镜头1：@张三 推门", "duration_seconds": 3})
    second = reference_videos_client.post(url, json={"prompt": "镜头1：@张三 坐下", "duration_seconds": 3})
    removed_id = second.json()["unit"]["unit_id"]
    assert reference_videos_client.delete(f"{url}/{removed_id}").status_code == 204

    added = reference_videos_client.post(url, json={"prompt": "镜头1：@张三 起身", "duration_seconds": 3})

    assert added.status_code == 201, added.text
    assert [first.json()["unit"]["unit_id"], removed_id, added.json()["unit"]["unit_id"]] == ["E1U1", "E1U2", "E1U3"]
    units = reference_videos_client.get(url).json()["units"]
    assert [unit["unit_id"] for unit in units] == ["E1U1", "E1U3"]


def test_added_unit_written_by_the_user_is_not_pending_authoring(reference_videos_client: TestClient):
    resp = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：@张三 推门", "duration_seconds": 3},
    )
    assert resp.status_code == 201, resp.text

    units = reference_videos_client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json()["units"]
    assert [unit.get("pending_authoring", False) for unit in units] == [False]


def test_add_unit_refuses_a_blank_body(reference_videos_client: TestClient):
    """Bodies are sole unit truth; empty bodies cannot execute and creation rejects needs_replan immediately."""
    response = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "", "duration_seconds": 3},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["problems"][0]["code"] == "needs_replan"
    assert reference_videos_client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json() == {
        "units": [],
        "unit_capabilities": {},
    }


def test_add_unit_accepts_the_request_bodies_the_web_canvas_sends(reference_videos_client: TestClient):
    """Canvas creation fixtures are byte-compared by frontend contracts; prove backend admission here.

    Together prevent independently passing but opposing contracts, such as frontend empty bodies rejected by backend.
    """
    contract = json.loads(_ADD_UNIT_CONTRACT.read_text(encoding="utf-8"))
    url = "/api/v1/projects/demo/reference-videos/episodes/1/units"

    first = reference_videos_client.post(url, json=contract["append"])
    assert first.status_code == 201, first.text
    assert first.json()["unit"]["unit_id"] == contract["insert_after"]["after_unit_id"]

    inserted = reference_videos_client.post(url, json=contract["insert_after"])
    assert inserted.status_code == 201, inserted.text
    units = reference_videos_client.get(url).json()["units"]
    assert [unit["unit_id"] for unit in units] == ["E1U1", inserted.json()["unit"]["unit_id"]]
    assert [unit["text"] for unit in units] == [contract["append"]["prompt"], contract["insert_after"]["prompt"]]


def test_add_unit_rejects_a_stored_reference_list(reference_videos_client: TestClient):
    """Bodies alone derive references; legacy ``references`` submissions must return 422, never silently ignore."""
    resp = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={
            "prompt": "镜头1：@张三 推门",
            "duration_seconds": 3,
            "references": [{"type": "character", "name": "张三"}],
        },
    )
    assert resp.status_code == 422, resp.text


def test_patch_unit_rejects_a_stored_reference_list(reference_videos_client: TestClient):
    uid = _seed_unit(reference_videos_client)
    resp = reference_videos_client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"references": [{"type": "scene", "name": "酒馆"}]},
    )
    assert resp.status_code == 422, resp.text


def test_unit_writes_reject_transition_and_leave_the_script_unchanged(reference_videos_client: TestClient):
    """Transitions are no longer unit fields; creation/edits reject unknown fields without script writes."""
    uid = _seed_unit(reference_videos_client)
    before = reference_videos_client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json()

    added = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "@张三 推门", "transition_to_next": "fade"},
    )
    patched = reference_videos_client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"note": "hi", "transition_to_next": "fade"},
    )

    assert (added.status_code, patched.status_code) == (422, 422)
    assert reference_videos_client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json() == before


def test_add_unit_without_duration_falls_back_to_model_slot(
    reference_videos_client: TestClient, set_video_request_facts
):
    """Omitted durations use first project-capability tiers, matching execution requested-seconds fallback."""
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video", generation_type="r2v", supported_durations=(6, 9), allowed_durations=(6, 9)
        )
    )
    resp = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：@张三 推门"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["unit"]["duration_seconds"] == 6


@pytest.mark.parametrize(
    ("prompt", "bucket", "expected"),
    [
        ("镜头1：@[张三] 推门", "r2v", 9),
        ("镜头1：@[李四] 回头", "i2v", 5),
        ("镜头1：空镜", "i2v", 5),
    ],
)
def test_add_unit_default_duration_follows_the_bucket_of_its_available_images(
    reference_videos_client: TestClient,
    tmp_path: Path,
    set_video_request_facts,
    prompt: str,
    bucket: str,
    expected: int,
):
    """Default durations use available-reference buckets like response per-unit facts; registered-but-missing images use i2v."""
    set_video_request_facts(_bucket_facts())
    _register_character_without_sheet(tmp_path, "李四")

    response = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units", json={"prompt": prompt}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["unit_capability"]["hydrated_capability"] == bucket
    assert body["unit"]["duration_seconds"] == expected
    assert expected in body["unit_capability"]["allowed_durations"]


@pytest.mark.parametrize("duration_seconds", [0, -1])
def test_add_unit_rejects_non_positive_duration(reference_videos_client: TestClient, duration_seconds: int):
    """Explicit nonpositive durations reject at boundaries, never silently become one second."""
    resp = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：@张三 推门", "duration_seconds": duration_seconds},
    )
    assert resp.status_code == 422, resp.text


def test_add_unit_atomically_rejects_mixed_speech(reference_videos_client: TestClient):
    response = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：张三推门\n@[张三]：{快走。}\n{风吹过旷野。}", "duration_seconds": 3},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["problems"][0]["code"] == "mixed_speech"
    assert reference_videos_client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json() == {
        "units": [],
        "unit_capabilities": {},
    }


def _seed_unit(reference_videos_client: TestClient) -> str:
    resp = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：@张三 推门", "duration_seconds": 3},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["unit"]["unit_id"]


def _derived_references(reference_videos_client: TestClient, unit: dict[str, Any]) -> list[tuple[str, str]]:
    """Execution references derive through sole body outlet without persistence."""
    from lib.script.reference_video.request_projection import unit_reference_declarations
    from server.routers import reference_videos as router_mod

    project = router_mod.get_project_manager().load_project("demo")
    return [(ref.type, ref.name) for ref in unit_reference_declarations(project, unit)]


def test_patch_unit_prompt_keeps_duration(reference_videos_client: TestClient):
    """Durations/bodies are independent; new bodies rederive references."""
    uid = _seed_unit(reference_videos_client)
    resp = reference_videos_client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"prompt": "镜头1：@酒馆 门口\n镜头2：@酒馆 全景"},
    )
    assert resp.status_code == 200, resp.text
    unit = resp.json()["unit"]
    assert unit["text"] == "镜头1：@酒馆 门口\n镜头2：@酒馆 全景"
    assert unit["duration_seconds"] == 3
    assert _derived_references(reference_videos_client, unit) == [("scene", "酒馆")]


def test_patch_unit_derives_non_character_references_before_speech_admission(reference_videos_client: TestClient):
    uid = _seed_unit(reference_videos_client)

    response = reference_videos_client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"prompt": "镜头1：@[酒馆]：木门被风吹开"},
    )

    assert response.status_code == 200, response.text
    assert _derived_references(reference_videos_client, response.json()["unit"]) == [("scene", "酒馆")]


@pytest.mark.parametrize(
    "case",
    [case for case in SPEECH_CONTRACT_CASES if case.generation_mode == "reference_video"],
    ids=lambda case: case.route_id,
)
def test_three_reference_route_web_manual_edits_atomically_reject_mixed_speech_on_save(
    reference_videos_client: TestClient, tmp_path: Path, case: SpeechContractCase
):
    uid = _seed_unit(reference_videos_client)
    before = reference_videos_client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json()["units"][0]
    before["generated_assets"] = {"video_clip": f"reference_videos/{uid}.mp4", "status": "completed"}
    # Simulate paid generation history; failed body edits must leave all candidate fields unpersisted through locked_script.
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

    response = reference_videos_client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"prompt": "镜头1：张三推门\n@[张三]：{快走。}\n{风吹过旷野。}"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["problems"][0]["code"] == "mixed_speech"
    after = reference_videos_client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json()["units"][0]
    assert after["text"] == before["text"]
    assert after["generated_assets"] == before["generated_assets"]


def test_patch_allows_unchanged_legacy_mixed_prompt(reference_videos_client: TestClient):
    uid = _seed_unit(reference_videos_client)
    prompt = "镜头1：张三推门\n@[张三]：{快走。}\n{风吹过旷野。}"
    from server.routers import reference_videos as router_mod

    pm = router_mod.get_project_manager()
    script = pm.load_script("demo", "episode_1.json")
    script["video_units"][0]["text"] = "张三推门\n@[张三]：{快走。}\n{风吹过旷野。}"
    script["video_units"][0]["needs_replan"] = True
    pm.save_script("demo", script, "episode_1.json")

    response = reference_videos_client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"prompt": prompt, "note": "保留历史媒体"},
    )

    assert response.status_code == 200
    assert response.json()["unit"]["note"] == "保留历史媒体"
    assert response.json()["unit"]["needs_replan"] is True


def test_patch_unit_duration_only(reference_videos_client: TestClient):
    """Duration-only edits preserve bodies unchanged."""
    uid = _seed_unit(reference_videos_client)
    resp = reference_videos_client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"duration_seconds": 9},
    )
    assert resp.status_code == 200, resp.text
    unit = resp.json()["unit"]
    assert unit["duration_seconds"] == 9
    assert unit["text"] == "镜头1：@张三 推门"


@pytest.mark.parametrize("duration_seconds", [0, -1])
def test_patch_unit_rejects_non_positive_duration(reference_videos_client: TestClient, duration_seconds: int):
    """Explicit nonpositive durations reject at boundaries, never silently become one second."""
    uid = _seed_unit(reference_videos_client)
    resp = reference_videos_client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"duration_seconds": duration_seconds},
    )
    assert resp.status_code == 422, resp.text


def test_add_nonblank_unit_derives_registered_references_from_text(reference_videos_client: TestClient):
    response = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "@[酒馆]：木门被风吹开", "duration_seconds": 5},
    )

    assert response.status_code == 201, response.text
    assert _derived_references(reference_videos_client, response.json()["unit"]) == [("scene", "酒馆")]


def test_patch_unit_derives_nfc_reference_for_nfd_registered_name(reference_videos_client: TestClient):
    """NFD asset registration/NFC body names match canonical forms as registered."""
    from server.routers import reference_videos as router_mod

    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    assert name_nfd != name_nfc
    pm = router_mod.get_project_manager()
    project = pm.load_project("demo")
    project["characters"][name_nfd] = {"description": "x"}
    pm.save_project("demo", project)

    uid = _seed_unit(reference_videos_client)
    resp = reference_videos_client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"prompt": f"镜头1：@[{name_nfc}] 推门"},
    )
    assert resp.status_code == 200, resp.text
    assert _derived_references(reference_videos_client, resp.json()["unit"]) == [("character", name_nfc)]


def _seed_unit_with_source_text(reference_videos_client: TestClient) -> str:
    from server.routers import reference_videos as router_mod

    uid = _seed_unit(reference_videos_client)
    pm = router_mod.get_project_manager()
    script = pm.load_script("demo", "episode_1.json")
    script["video_units"][0]["source_text"] = "张三推开了门。"
    pm.save_script("demo", script, "episode_1.json")
    return uid


def test_unit_responses_carry_source_text(reference_videos_client: TestClient):
    uid = _seed_unit_with_source_text(reference_videos_client)

    listed = reference_videos_client.get("/api/v1/projects/demo/reference-videos/episodes/1/units")
    patched = reference_videos_client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"prompt": "镜头1：@张三 关门"},
    )

    assert listed.json()["units"][0]["source_text"] == "张三推开了门。"
    assert patched.status_code == 200, patched.text
    assert patched.json()["unit"]["source_text"] == "张三推开了门。"


def test_patch_unit_rejects_source_text(reference_videos_client: TestClient):
    uid = _seed_unit_with_source_text(reference_videos_client)

    resp = reference_videos_client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"source_text": "改写的原文"},
    )

    assert resp.status_code == 422
    listed = reference_videos_client.get("/api/v1/projects/demo/reference-videos/episodes/1/units")
    assert listed.json()["units"][0]["source_text"] == "张三推开了门。"


def test_patch_unknown_unit_404(reference_videos_client: TestClient):
    resp = reference_videos_client.patch(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/E9U9",
        json={"note": "hi"},
    )
    assert resp.status_code == 404


def test_delete_unit_removes_entry(reference_videos_client: TestClient):
    uid = _seed_unit(reference_videos_client)
    resp = reference_videos_client.delete(f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}")
    assert resp.status_code == 204
    resp = reference_videos_client.get("/api/v1/projects/demo/reference-videos/episodes/1/units")
    assert resp.json()["units"] == []


def test_delete_unknown_unit_404(reference_videos_client: TestClient):
    resp = reference_videos_client.delete("/api/v1/projects/demo/reference-videos/episodes/1/units/E9U9")
    assert resp.status_code == 404


def test_move_unit_applies_new_order(reference_videos_client: TestClient):
    uid1 = _seed_unit(reference_videos_client)
    uid2 = _seed_unit(reference_videos_client)
    resp = reference_videos_client.post(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid2}/move",
        json={"after_unit_id": None},
    )
    assert resp.status_code == 200, resp.text
    assert [u["unit_id"] for u in resp.json()["units"]] == [uid2, uid1]
    units = reference_videos_client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json()["units"]
    assert [u["unit_id"] for u in units] == [uid2, uid1]


@pytest.mark.parametrize("target", ["unit", "anchor"])
def test_move_unit_with_unknown_id_404(reference_videos_client: TestClient, target: str):
    uid = _seed_unit(reference_videos_client)
    unit_id, anchor = (uid, "E1U999") if target == "anchor" else ("E1U999", uid)
    resp = reference_videos_client.post(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{unit_id}/move",
        json={"after_unit_id": anchor},
    )
    assert resp.status_code == 404


def test_generate_unit_enqueues_task(reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    uid = _seed_unit(reference_videos_client)

    enqueued: list[dict] = []

    class _FakeQueue:
        async def enqueue_task(self, **kwargs):
            enqueued.append(kwargs)
            return {"task_id": "task-xyz", "deduped": False}

    use_generation_queue(monkeypatch, _FakeQueue())

    resp = reference_videos_client.post(f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate")
    assert resp.status_code == 202, resp.text
    assert resp.json()["task_id"] == "task-xyz"
    assert enqueued[0]["task_type"] == "reference_video"
    assert enqueued[0]["media_type"] == "video"
    assert enqueued[0]["resource_id"] == uid
    # Current text guards structure only; tasks retain locations/options, workers reread newest scripts before execution.
    assert "prompt" not in enqueued[0]["payload"]
    assert enqueued[0]["payload"]["reference_request_options"] == {}


def test_generate_unit_requires_and_persists_explicit_duration_confirmation(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = _seed_unit(reference_videos_client)  # 3s
    _patch_supported_durations(monkeypatch, [4, 8])
    enqueued: list[dict[str, object]] = []

    class _FakeQueue:
        async def enqueue_task(self, **kwargs):
            enqueued.append(kwargs)
            return {"task_id": "task-confirmed", "deduped": False}

    use_generation_queue(monkeypatch, _FakeQueue())

    unconfirmed = reference_videos_client.post(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate"
    )
    assert unconfirmed.status_code == 400
    assert enqueued == []

    confirmed = reference_videos_client.post(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate",
        json={"confirmed_request_duration_seconds": 4},
    )
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json()["projection"]["request_duration"] == 4
    payload = enqueued[0]["payload"]
    assert isinstance(payload, dict)
    assert payload == {
        "script_file": "scripts/episode_1.json",
        "reference_request_options": {"confirmed_request_duration_seconds": 4},
    }


@pytest.mark.parametrize(
    "case",
    [case for case in SPEECH_CONTRACT_CASES if case.generation_mode == "reference_video"],
    ids=lambda case: case.route_id,
)
def test_three_reference_route_web_video_entries_share_structured_speech_admission(
    reference_videos_client: TestClient,
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

    response = reference_videos_client.post("/api/v1/projects/demo/reference-videos/episodes/1/units/E1U1/generate")

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


@pytest.mark.parametrize("delivery", ["post_production", "use_tts"])
def test_generate_unit_rejects_the_retired_narration_delivery_field(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch, delivery: str
) -> None:
    uid = _seed_unit(reference_videos_client)
    enqueue = AsyncMock()

    use_generation_queue(monkeypatch, type("Queue", (), {"enqueue_task": enqueue})())

    response = reference_videos_client.post(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate",
        json={"narration_delivery": delivery},
    )

    assert response.status_code == 422, response.text
    assert [(item["type"], item["loc"][-1]) for item in response.json()["detail"]] == [
        ("extra_forbidden", "narration_delivery")
    ]
    enqueue.assert_not_awaited()


def test_generate_unit_bucket_capability_error_returns_400(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Shared-projection r2v blockers prevent submission enqueue."""
    from lib.i18n import _ as i18n_message
    from lib.script.reference_video.request_projection import ProjectionProblem

    uid = _seed_unit(reference_videos_client)
    enqueued: list[dict] = []

    class _FakeQueue:
        async def enqueue_task(self, **kwargs):
            enqueued.append(kwargs)
            return {"task_id": "task-xyz", "deduped": False}

    use_generation_queue(monkeypatch, _FakeQueue())

    base_project = _projection_with_durations([3, 6, 9])

    async def _reject(**kwargs):
        projection = await base_project(**kwargs)
        assert projection.hydrated_generation_type == "r2v"
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

    use_reference_projection(monkeypatch, _reject)

    resp = reference_videos_client.post(f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate")
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
        "duration_input": 3,
        "request_duration": 3,
        "problems": [
            {
                "code": "video_capability_missing_r2v",
                "blocking": True,
                "unit_id": uid,
                "locations": [{"path": ["text"], "line": None}],
                "params": {"provider": "minimax", "model": "MiniMax-Hailuo-2.3"},
                "action": "configure_video_model",
                "message": i18n_message(
                    "video_capability_missing_r2v",
                    **make_display_names().apply({"provider": "minimax", "model": "MiniMax-Hailuo-2.3"}),
                ),
            }
        ],
    }
    assert enqueued == []


def test_generate_unit_degenerate_precheck_uses_i2v_bucket(
    reference_videos_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Referenceless units use downgraded i2v bucket resolution before enqueue, matching execution routing."""
    script_path = tmp_path / "projects" / "demo" / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"] = [{"unit_id": "E1U1", "text": "空镜头", "duration_seconds": 3}]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    class _FakeQueue:
        async def enqueue_task(self, **kwargs):
            return {"task_id": "task-xyz", "deduped": False}

    use_generation_queue(monkeypatch, _FakeQueue())

    checked: list[str] = []
    base_project = _projection_with_durations([3, 6, 9])

    async def _record(**kwargs):
        projection = await base_project(**kwargs)
        checked.append(projection.hydrated_generation_type)
        return projection

    use_reference_projection(monkeypatch, _record)

    resp = reference_videos_client.post("/api/v1/projects/demo/reference-videos/episodes/1/units/E1U1/generate")
    assert resp.status_code == 202, resp.text
    assert checked == ["i2v"]


def test_generate_unit_rejects_text_only_request_for_image_only_model(
    reference_videos_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    script_path = tmp_path / "projects" / "demo" / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"] = [{"unit_id": "E1U1", "text": "空镜头", "duration_seconds": 3}]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.routers import reference_videos as router_mod

    use_reference_projection(monkeypatch, fake_reference_request_projector(durations=(3, 6, 9), text_to_video=False))
    enqueue = AsyncMock()

    class _FakeQueue:
        enqueue_task = enqueue

    def _fake_generation_queue() -> _FakeQueue:
        return _FakeQueue()

    monkeypatch.setattr(router_mod, "get_generation_queue", _fake_generation_queue)

    response = reference_videos_client.post("/api/v1/projects/demo/reference-videos/episodes/1/units/E1U1/generate")

    assert response.status_code == 400
    assert response.json()["detail"]["problems"][0]["code"] == "video_capability_missing_t2v"
    enqueue.assert_not_called()


def test_generate_unit_rejects_blank_prompt(reference_videos_client: TestClient, tmp_path: Path):
    """Whitespace-only bodies fail enqueue guards with 400, never reach execution failures."""
    script_path = tmp_path / "projects" / "demo" / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"] = [{"unit_id": "E1U1", "text": "  ", "duration_seconds": 3}]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = reference_videos_client.post("/api/v1/projects/demo/reference-videos/episodes/1/units/E1U1/generate")
    assert resp.status_code == 400, resp.text


def test_generate_unit_missing_returns_404(reference_videos_client: TestClient):
    resp = reference_videos_client.post("/api/v1/projects/demo/reference-videos/episodes/1/units/E9U9/generate")
    assert resp.status_code == 404


def _projection_with_durations(durations: list[int]):
    return fake_reference_request_projector(durations=tuple(durations))


def _patch_supported_durations(monkeypatch: pytest.MonkeyPatch, durations: list[int]) -> None:

    use_reference_projection(monkeypatch, _projection_with_durations(durations))


def _precheck(reference_videos_client: TestClient, unit_id: str):
    return reference_videos_client.get(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{unit_id}/duration-precheck"
    )


def test_precheck_slot_member_needs_no_confirmation(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Tier-member totals enqueue directly without confirmation."""
    uid = _seed_unit(reference_videos_client)  # shots sum = 3s
    _patch_supported_durations(monkeypatch, [3, 6, 9])

    body = _precheck(reference_videos_client, uid).json()
    assert body == {
        "allowed": True,
        "kind": "reference_request_projection",
        "advisory": True,
        "unit_id": uid,
        "planned_duration": 3,
        "needs_confirmation": False,
        "script_duration": 3,
        "duration_input": 3,
        "request_duration": 3,
        "adjustment": "exact",
        "declared_capability": "r2v",
        "hydrated_capability": "r2v",
        "provider_id": "fake",
        "model_id": "fake-model",
        "problems": [],
    }


def test_precheck_rounds_up_and_needs_confirmation(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Nontier totals fitting available tiers require confirmation of the smallest fitting tier."""
    uid = _seed_unit(reference_videos_client)  # 3s
    _patch_supported_durations(monkeypatch, [4, 8, 12])

    body = _precheck(reference_videos_client, uid).json()
    assert body["needs_confirmation"] is True
    assert body["script_duration"] == 3
    assert body["request_duration"] == 4
    assert body["adjustment"] == "up"


def _set_project_narration(tmp_path: Path, **fields: object) -> None:
    project_file = tmp_path / "projects" / "demo" / "project.json"
    project = json.loads(project_file.read_text(encoding="utf-8"))
    project.update(fields)
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("durations", [[3, 6, 9], [4, 8, 12]])
def test_precheck_is_identical_for_tts_and_post_production_projects(
    reference_videos_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    durations: list[int],
) -> None:
    """Narration delivery never affects video tiers/confirmation; planned duration alone determines both."""

    uid = _seed_unit(reference_videos_client)  # 3s
    _patch_supported_durations(monkeypatch, durations)

    _set_project_narration(tmp_path, narration_delivery="post_production")
    post_production = _precheck(reference_videos_client, uid)
    _set_project_narration(
        tmp_path,
        narration_delivery="use_tts",
        audio_backend="fake-audio/tts-model",
        narration_voice="voice",
        narration_speed=1.0,
    )
    use_tts = _precheck(reference_videos_client, uid)

    assert post_production.status_code == use_tts.status_code == 200
    assert use_tts.json() == post_production.json()
    assert use_tts.json()["duration_input"] == 3


def test_precheck_rejects_the_retired_narration_delivery_query(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = _seed_unit(reference_videos_client)
    _patch_supported_durations(monkeypatch, [3, 6, 9])

    response = reference_videos_client.get(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/duration-precheck",
        params={"narration_delivery": "use_tts"},
    )

    assert response.status_code == 422, response.text
    assert [(item["type"], item["loc"]) for item in response.json()["detail"]] == [
        ("extra_forbidden", ["query", "narration_delivery"])
    ]


def test_precheck_over_largest_slot_requires_replan(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Totals exceeding maximum tiers never truncate; return structured replan blockers."""
    uid = _seed_unit(reference_videos_client)  # 3s
    _patch_supported_durations(monkeypatch, [1, 2])

    response = _precheck(reference_videos_client, uid)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["request_duration"] == 2
    assert detail["problems"][0]["code"] == "needs_replan"
    assert detail["problems"][0]["action"] == "replan_unit"


def test_precheck_empty_duration_metadata_returns_structured_blocker(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Empty tier sets fail loudly, never invent executable unconstrained results."""
    from lib.i18n import _ as i18n_message

    uid = _seed_unit(reference_videos_client)
    _patch_supported_durations(monkeypatch, [])

    response = _precheck(reference_videos_client, uid)
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
        "message": i18n_message(
            "reference_supported_durations_missing",
            **make_display_names().apply({"provider": "fake", "model": "fake-model"}),
        ),
    }


def test_precheck_formats_missing_asset_message_for_people(reference_videos_client: TestClient, tmp_path: Path) -> None:
    from lib.i18n import _ as i18n_message

    uid = _seed_unit(reference_videos_client)
    (tmp_path / "projects" / "demo" / "characters" / "张三.png").unlink()

    response = _precheck(reference_videos_client, uid)

    assert response.status_code == 400, response.text
    problems = response.json()["detail"]["problems"]
    missing = next(problem for problem in problems if problem["code"] == "reference_asset_missing")
    assert missing["params"]["missing"] == [["character", "张三"]]
    assert missing["params"]["missing_text"] == "character: 张三"
    assert missing["message"] == i18n_message("reference_asset_missing", missing_text="character: 张三")


def test_precheck_missing_unit_returns_404(reference_videos_client: TestClient):
    assert _precheck(reference_videos_client, "E9U9").status_code == 404


def test_add_unit_stale_script_file_returns_404(reference_videos_client: TestClient, tmp_path: Path):
    """project.json stale script_file pointing to deleted files returns write 404, never 500."""
    (tmp_path / "projects" / "demo" / "scripts" / "episode_1.json").unlink()
    resp = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：@张三 出现"},
    )
    assert resp.status_code == 404, resp.text


def test_add_unit_unknown_project_returns_404(reference_videos_client: TestClient):
    resp = reference_videos_client.post(
        "/api/v1/projects/missing/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：@张三 出现"},
    )
    assert resp.status_code == 404


def test_add_unit_unknown_episode_returns_404(reference_videos_client: TestClient):
    resp = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/99/units",
        json={"prompt": "镜头1：空镜"},
    )
    assert resp.status_code == 404


def test_write_endpoint_rejects_non_reference_video_mode(reference_videos_client: TestClient, tmp_path: Path):
    """Nonreference episodes return write 409."""
    script_path = tmp_path / "projects" / "demo" / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["generation_mode"] = "image"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    proj_path = tmp_path / "projects" / "demo" / "project.json"
    proj = json.loads(proj_path.read_text(encoding="utf-8"))
    proj["generation_mode"] = "image"
    proj_path.write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")

    resp = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：空镜"},
    )
    assert resp.status_code == 409


def test_patch_unit_duration_override_without_header(reference_videos_client: TestClient):
    """Headerless prompts imply override=True, applying duration_seconds directly."""
    resp = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "@张三 推门", "duration_seconds": 5},
    )
    assert resp.status_code == 201, resp.text
    uid = resp.json()["unit"]["unit_id"]
    assert resp.json()["unit"]["duration_seconds"] == 5

    # duration_seconds-only without prompts uses elif to overwrite existing override durations.
    resp = reference_videos_client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"duration_seconds": 8, "note": "hi"},
    )
    assert resp.status_code == 200, resp.text
    unit = resp.json()["unit"]
    assert unit["duration_seconds"] == 8
    assert unit["note"] == "hi"

    # Headerless new prompt + duration_seconds uses prompt branches and single-shot duration overrides.
    resp = reference_videos_client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"prompt": "@张三 转身离开", "duration_seconds": 7},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["unit"]["duration_seconds"] == 7


def test_add_unit_concurrent_rebind_returns_409(reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Concurrent episode→script_file rebinding across locks returns retryable 409."""
    from server.routers import reference_videos as router_mod

    pm = router_mod.get_project_manager()

    # Simulate concurrent binding PATCH; locked rechecks find episode 1 pointing elsewhere.
    def _rebound(_project_name: str) -> dict:
        return {
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_2.json"}],
        }

    monkeypatch.setattr(pm, "_read_project_raw_unlocked", _rebound)

    resp = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：空镜"},
    )
    assert resp.status_code == 409, resp.text


# ============ Parse previews ============


def _patch_video_caps(monkeypatch: pytest.MonkeyPatch, caps: dict) -> None:
    from server.routers import reference_videos as router_mod

    monkeypatch.setattr(router_mod, "project_video_caps", AsyncMock(return_value=caps))


def _preview(reference_videos_client: TestClient, prompt: str):
    return reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/script-preview",
        json={"prompt": prompt},
    )


def test_script_preview_derives_utterances_in_reading_order(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Previews return speech derivations only: body-order 1-based utterances plus downgrade warnings."""
    _patch_video_caps(monkeypatch, {})
    body = _preview(reference_videos_client, "镜头1：@[酒馆] 内景。\n@[张三]：{我来了}\n{那年冬天格外冷}").json()

    assert set(body) == {"utterances", "warnings"}
    assert body["utterances"] == [
        {"index": 1, "kind": "dialogue", "speaker": "张三", "text": "我来了"},
        {"index": 2, "kind": "voiceover", "speaker": None, "text": "那年冬天格外冷"},
    ]
    assert body["warnings"] == []


def test_script_preview_returns_localized_warnings(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _patch_video_caps(monkeypatch, {})
    body = _preview(reference_videos_client, "镜头1：@[酒馆] 内景，@[王五] 推门。").json()
    assert [w["key"] for w in body["warnings"]] == ["ref_warn_unregistered_mention"]
    assert "王五" in body["warnings"][0]["message"]


def test_script_preview_warns_when_unit_references_no_scene(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Registered scenes unused by units leave locations to models; explain on read."""
    _patch_video_caps(monkeypatch, {})
    body = _preview(reference_videos_client, "镜头1：中景，推门。").json()
    assert [w["key"] for w in body["warnings"]] == ["ref_warn_unit_without_scene"]
    assert body["warnings"][0]["message"] != "ref_warn_unit_without_scene"


def test_script_preview_uses_project_voice_capabilities(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _patch_video_caps(monkeypatch, {"voice_consistency": "none", "model": "silent-01"})
    body = _preview(reference_videos_client, "镜头1：@[酒馆] 开场。\n@[张三]：{我来了}").json()
    assert [w["key"] for w in body["warnings"]] == ["ref_warn_silent_model"]
    assert "silent-01" in body["warnings"][0]["message"]


def test_script_preview_warns_when_episode_is_silent(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Silent episodes warn preview voice consistency is ineffective, even with class-A models."""
    _patch_video_caps(
        monkeypatch,
        {
            "voice_consistency": "native",
            "max_reference_audio_count": 3,
            "requested_generate_audio": False,
            "model": "doubao-seedance-2-0",
        },
    )
    body = _preview(reference_videos_client, "镜头1：@[酒馆] 开场。\n@[张三]：{我来了}").json()
    assert [w["key"] for w in body["warnings"]] == ["ref_warn_silent_episode"]
    assert body["warnings"][0]["message"] != "ref_warn_silent_episode"
    # Dialogue still derives; silent affects reference audio only, never provider dialogue text.
    assert [u["text"] for u in body["utterances"]] == ["我来了"]


def test_script_preview_404_for_unknown_episode(reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _patch_video_caps(monkeypatch, {})
    resp = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/9/script-preview",
        json={"prompt": "镜头1：开场。"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Whole-batch admission: create all or zero tasks.
# ---------------------------------------------------------------------------

BATCH_ENDPOINT = "/api/v1/projects/demo/reference-videos/episodes/1/units/generate-batch"


def _record_finished_clip(unit_id: str) -> None:
    """Persist complete production unit outputs: files/script claims/manifests with frozen bases."""

    from lib.artifacts.artifact_manifest import (
        ArtifactKey,
        ArtifactManifest,
        ProjectArtifactManifestAdapter,
        compose_video_artifact_basis,
    )
    from lib.artifacts.visual_artifact_provenance import build_reference_video_artifact_visual_basis
    from lib.project.project_manager import ProjectManager
    from lib.script.reference_video.request_projection import resolve_reference_assets
    from lib.speech.speech_artifact_provenance import build_video_duration_basis, build_video_speech_basis
    from lib.speech.speech_composition import admit_script_unit
    from server.routers import reference_videos as router_mod

    pm: ProjectManager = router_mod.get_project_manager()
    project_path = pm.get_project_path("demo")
    clip_dir = project_path / "reference_videos"
    clip_dir.mkdir(parents=True, exist_ok=True)
    (clip_dir / f"{unit_id}.mp4").write_bytes(b"\x00")
    script = pm.load_script("demo", "scripts/episode_1.json")
    target = next(unit for unit in script["video_units"] if unit["unit_id"] == unit_id)
    target["generated_assets"] = {"video_clip": f"reference_videos/{unit_id}.mp4"}
    pm.save_script("demo", script, "scripts/episode_1.json")

    project = pm.load_project("demo")
    visual = build_reference_video_artifact_visual_basis(
        unit=target,
        request_assets=resolve_reference_assets(project, project_path, target),
        style=project.get("style"),
        aspect_ratio="9:16",
    )
    speech = build_video_speech_basis(admit_script_unit("video_units", target).preparation)
    duration = build_video_duration_basis(int(target["duration_seconds"]))
    ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
        ArtifactKey.episode_video(1, unit_id),
        artifact_path=f"reference_videos/{unit_id}.mp4",
        basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
    )


def _seed_second_unit(reference_videos_client: TestClient) -> str:
    resp = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：@酒馆 全景", "duration_seconds": 3},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["unit"]["unit_id"]


def _patch_batch_admission(
    monkeypatch: pytest.MonkeyPatch,
    *,
    durations: list[int],
    active_tasks: list[dict[str, object]] | None = None,
    quote_amount: float | None = None,
    fail_enqueue_after: int | None = None,
) -> list[dict[str, object]]:
    """Inject in-process batch-current-state queries, returning enqueue records.

    Admission needs task DBs/pricing; router tests inject dependencies individually.

    ``fail_enqueue_after`` raises after enqueue N, reproducing interrupted sequential enqueue.
    """

    from server.services.admission import video_batch_admission as admission_mod
    from server.services.admission.cost_estimation import VideoRequestQuote

    async def _no_active(**_kwargs):
        return list(active_tasks or [])

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

    use_reference_projection(monkeypatch, _projection_with_durations(durations))
    use_active_tasks_lookup(monkeypatch, _no_active)
    monkeypatch.setattr(admission_mod, "quote_video_request", _quote)

    enqueued: list[dict[str, object]] = []

    async def _enqueue_task_only(**kwargs):
        if fail_enqueue_after is not None and len(enqueued) >= fail_enqueue_after:
            raise RuntimeError("queue unavailable")
        enqueued.append(kwargs)
        return {"task_id": f"task-{len(enqueued)}", "deduped": False}

    monkeypatch.setattr("lib.generation.generation_queue_client.enqueue_task_only", _enqueue_task_only)
    return enqueued


def test_generate_batch_creates_the_whole_task_set_in_one_admission(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _seed_unit(reference_videos_client)
    second = _seed_second_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "admitted"
    assert len(body["task_ids"]) == 2
    # Per-unit task rows let callers await individually before whole-batch persistence.
    assert sorted(body["task_ids_by_unit"]) == sorted([first, second])
    assert sorted(body["task_ids_by_unit"].values()) == sorted(body["task_ids"])
    assert [item["unit_id"] for item in body["units"]] == [first, second]
    assert all(item["admitted"] for item in body["units"])
    assert [call["resource_id"] for call in enqueued] == [first, second]
    # Requests persist locations/current options only; workers reread newest scripts at start.
    assert enqueued[0]["payload"] == {
        "script_file": "scripts/episode_1.json",
        "reference_request_options": {},
    }
    assert enqueued[0]["source"] == "webui"


def test_generate_batch_keeps_created_tasks_when_the_enqueue_is_interrupted(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interrupted enqueue never revokes created tasks: admitted complete paid units execute normally.

    Report unreached units by ID so UI releases only their occupancy markers.
    """

    first = _seed_unit(reference_videos_client)
    second = _seed_second_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9], fail_enqueue_after=1)

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

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


def test_generate_batch_after_an_interruption_only_queues_what_never_reached_the_queue(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Next missing-only generation fills unqueued units; created-task outputs are current, avoiding repeat payment."""

    first = _seed_unit(reference_videos_client)
    second = _seed_second_unit(reference_videos_client)
    _patch_batch_admission(monkeypatch, durations=[3, 6, 9], fail_enqueue_after=1)

    interrupted = reference_videos_client.post(BATCH_ENDPOINT, json={}).json()
    assert [entry["unit_id"] for entry in interrupted["enqueue_failures"]] == [second]

    # First units finish/persist normally; second tasks never exist or bill.
    _record_finished_clip(first)

    retried = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])
    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "admitted"
    assert [item["unit_id"] for item in body["units"]] == [second]
    assert [call["resource_id"] for call in retried] == [second]
    assert body["enqueue_failures"] == []


def test_generate_batch_creates_zero_tasks_when_one_unit_is_blocked(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One problematic unit blocks entire batches; healthy units truthfully identify blockers."""

    first = _seed_unit(reference_videos_client)
    second = _seed_second_unit(reference_videos_client)
    enqueued = _patch_batch_admission(
        monkeypatch,
        durations=[3, 6, 9],
        active_tasks=[{"resource_id": second, "id": "task-running", "status": "running"}],
    )

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert body["task_ids"] == []
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes[second] == ["generation_active_task_conflict"]
    assert codes[first] == ["generation_batch_admission_withheld"]


def test_generate_batch_reports_every_gap_not_only_the_first(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UI needs all gaps at once; first-only reporting forces repeated trial/error."""

    first = _seed_unit(reference_videos_client)
    second = _seed_second_unit(reference_videos_client)
    enqueued = _patch_batch_admission(
        monkeypatch,
        durations=[3, 6, 9],
        active_tasks=[{"resource_id": second, "id": "task-running", "status": "running"}],
    )

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={"unit_ids": [first, second, "E9U9"]})

    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes["E9U9"] == ["generation_unit_not_found"]
    assert codes[second] == ["generation_active_task_conflict"]
    assert codes[first] == ["generation_batch_admission_withheld"]
    # Every gap includes localized readable reasons, requiring no caller copy assembly.
    assert all(problem["message"] for item in body["units"] for problem in item["problems"])


def test_generate_batch_aggregates_the_confirmation_by_tier_then_enqueues_on_consent(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _seed_unit(reference_videos_client)
    second = _seed_second_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[4, 8], quote_amount=0.8)

    pending = reference_videos_client.post(BATCH_ENDPOINT, json={})

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

    accepted = reference_videos_client.post(
        BATCH_ENDPOINT,
        json={"confirmed_request_durations": {first: 4, second: 4}},
    )

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["decision"] == "admitted"
    assert [call["resource_id"] for call in enqueued] == [first, second]
    # Confirmations apply to current requests only, never freeze execution content.
    options = cast(dict[str, Any], enqueued[0]["payload"])["reference_request_options"]
    assert options == {"confirmed_request_duration_seconds": 4}


PREVIEW_ENDPOINT = f"{BATCH_ENDPOINT}/preview"


def test_preview_batch_lists_the_targets_and_totals_their_quotes_without_creating_tasks(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirmation previews share submission conclusions plus priced totals, creating zero tasks."""

    first = _seed_unit(reference_videos_client)
    second = _seed_second_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9], quote_amount=0.8)

    from server.routers import reference_videos

    project_path = reference_videos.get_project_manager().get_project_path("demo")
    script_path = project_path / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["duration_override"] = 3
    script_path.write_text(json.dumps(script), encoding="utf-8")
    before = {path.relative_to(project_path): path.read_bytes() for path in project_path.rglob("*") if path.is_file()}

    resp = reference_videos_client.post(PREVIEW_ENDPOINT, json={"unit_ids": [first, second]})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "admitted"
    assert [item["unit_id"] for item in body["units"]] == [first, second]
    assert body["skipped_unit_ids"] == []
    assert body["estimated_cost"] == {"USD": pytest.approx(1.6)}
    assert body["unpriced_count"] == 0
    assert "task_ids" not in body
    assert enqueued == []
    assert {
        path.relative_to(project_path): path.read_bytes() for path in project_path.rglob("*") if path.is_file()
    } == before


def test_preview_batch_awaiting_consent_totals_the_cost_at_the_tiers_to_accept(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirmable tiers share submission groupings; quote pending tiers for one confirmation decision."""

    first = _seed_unit(reference_videos_client)
    second = _seed_second_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[4, 8], quote_amount=0.8)

    body = reference_videos_client.post(PREVIEW_ENDPOINT, json={}).json()

    assert body["decision"] == "confirmation_required"
    tiers = body["confirmation"]["tiers"]
    assert [tier["request_duration_seconds"] for tier in tiers] == [4]
    assert sorted(tiers[0]["unit_ids"]) == sorted([first, second])
    assert body["estimated_cost"] == {"USD": pytest.approx(1.6)}
    assert enqueued == []


def test_preview_batch_counts_units_without_a_quote_instead_of_guessing(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_unit(reference_videos_client)
    _seed_second_unit(reference_videos_client)
    _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    body = reference_videos_client.post(PREVIEW_ENDPOINT, json={}).json()

    assert body["decision"] == "admitted"
    assert body["estimated_cost"] is None
    assert body["unpriced_count"] == 2


def test_preview_batch_reports_a_blocked_batch_and_skipped_clips(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Previews explain blocked/already-generated skipped units before submission."""

    first = _seed_unit(reference_videos_client)
    second = _seed_second_unit(reference_videos_client)
    third = _seed_second_unit(reference_videos_client)
    enqueued = _patch_batch_admission(
        monkeypatch,
        durations=[3, 6, 9],
        active_tasks=[{"resource_id": third, "id": "task-running", "status": "running"}],
    )
    _record_finished_clip(first)

    body = reference_videos_client.post(PREVIEW_ENDPOINT, json={}).json()

    assert body["decision"] == "blocked"
    assert body["skipped_unit_ids"] == [first]
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes == {second: ["generation_batch_admission_withheld"], third: ["generation_active_task_conflict"]}
    assert all(problem["message"] for item in body["units"] for problem in item["problems"])
    assert enqueued == []


def test_preview_batch_rejects_an_empty_selection(reference_videos_client: TestClient) -> None:
    resp = reference_videos_client.post(PREVIEW_ENDPOINT, json={"unit_ids": []})

    assert resp.status_code == 400


@pytest.mark.parametrize("invalid", [0, -4, 4.5])
def test_generate_batch_rejects_non_positive_confirmed_duration(
    reference_videos_client: TestClient, invalid: object
) -> None:
    """Confirmed seconds must reject zero/negative at boundaries, never request-option construction 500s."""

    first = _seed_unit(reference_videos_client)

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={"confirmed_request_durations": {first: invalid}})

    assert resp.status_code == 422, resp.text


def test_generate_batch_partial_consent_still_creates_nothing(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half-confirmed tiers remain unapproved; remaining tiers still need user decisions."""

    first = _seed_unit(reference_videos_client)
    second = _seed_second_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[4, 8], quote_amount=0.8)

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={"confirmed_request_durations": {first: 4}})

    body = resp.json()
    assert body["decision"] == "confirmation_required"
    assert enqueued == []
    assert body["confirmation"]["tiers"][0]["unit_ids"] == [second]


def test_generate_batch_repeating_an_admitted_request_reports_dedup(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated submissions create no second batch; report queue dedupe truthfully."""

    _seed_unit(reference_videos_client)
    _patch_batch_admission(monkeypatch, durations=[3, 6, 9])
    created: list[str] = []

    async def _enqueue_task_only(**kwargs):
        deduped = bool(created)
        created.append(str(kwargs["resource_id"]))
        return {"task_id": "task-1", "deduped": deduped}

    monkeypatch.setattr("lib.generation.generation_queue_client.enqueue_task_only", _enqueue_task_only)

    first = reference_videos_client.post(BATCH_ENDPOINT, json={})
    second = reference_videos_client.post(BATCH_ENDPOINT, json={})

    assert first.json()["deduped"] is False
    assert second.json()["deduped"] is True
    assert first.json()["task_ids"] == second.json()["task_ids"] == ["task-1"]


def test_generate_batch_rejects_an_empty_selection(reference_videos_client: TestClient) -> None:
    """Empty arrays mean no targets, never All; silent whole-set expansion exceeds user intentions."""

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={"unit_ids": []})

    assert resp.status_code == 400
    assert resp.json()["detail"]


def test_generate_batch_skips_units_that_already_have_a_clip(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale usable artifacts never autoregenerate; missing-only excludes them."""

    first = _seed_unit(reference_videos_client)
    second = _seed_second_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    _record_finished_clip(first)

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

    body = resp.json()
    assert body["decision"] == "admitted"
    assert [item["unit_id"] for item in body["units"]] == [second]
    assert [call["resource_id"] for call in enqueued] == [second]


def test_generate_batch_regenerates_a_unit_whose_recorded_clip_is_gone(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old schemas retain deleted output paths; classify units missing and reenqueue.

    Alternate reusable checks for manual uploads/registered paths also verify existence:
    deleted registered files are not reusable, or whole batches never fill them.
    """

    first = _seed_unit(reference_videos_client)
    second = _seed_second_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    from lib.project.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    for unit in script["video_units"]:
        if unit["unit_id"] == first:
            unit["generated_assets"] = {"video_clip": f"reference_videos/{first}.mp4"}
    pm.save_script("demo", script, "scripts/episode_1.json")
    # Registered paths with absent files model users deleting outputs through filesystems.
    assert not (pm.get_project_path("demo") / "reference_videos" / f"{first}.mp4").exists()

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

    body = resp.json()
    assert body["decision"] == "admitted"
    assert sorted(item["unit_id"] for item in body["units"]) == sorted([first, second])
    assert sorted(call["resource_id"] for call in enqueued) == sorted([first, second])


def test_generate_batch_creates_zero_tasks_when_one_artifact_state_is_unreadable(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unresolved artifact states belong to requests; include their admission problems, blocking batches without billing healthy units."""

    from lib.artifacts.artifact_manifest import ArtifactBlocker, ArtifactStatus
    from lib.generation.generation_result import GenerationCandidate, GenerationTargetState
    from server.routers import reference_videos as router_mod

    first = _seed_unit(reference_videos_client)
    second = _seed_second_unit(reference_videos_client)
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

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert body["task_ids"] == []
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes[second] == ["generation_artifact_state_unavailable"]
    assert codes[first] == ["generation_batch_admission_withheld"]


def test_reference_unit_task_spec_rejects_a_non_string_text() -> None:
    """Dirty bodies report enqueueability problems, never whole-batch read 500s."""

    from server.services.admission.video_batch_admission import reference_unit_task_spec

    with pytest.raises(ValueError, match="text 必须是字符串"):
        reference_unit_task_spec({"unit_id": "E1U1", "text": 42}, "scripts/episode_1.json")


def test_generate_batch_reports_malformed_units_instead_of_shrinking_the_batch(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing/duplicate IDs and dirty durations enter conclusions; excluding them would bill healthy units alone."""

    from lib.project.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(reference_videos_client)
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
    # Write directly: external/Agent raw edits create dirty values, which validated writers would reject.
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

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


def test_generate_batch_explicit_ids_ignore_unrelated_malformed_units(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit redo targets come from callers; unrelated malformed script entries neither participate nor veto."""

    from lib.project.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [healthy, {**healthy, "unit_id": ""}]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={"unit_ids": [first]})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "admitted"
    assert [call["resource_id"] for call in enqueued] == [first]


def test_generate_batch_reports_non_object_units_instead_of_dropping_them(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nondict entries cannot target; report named problems so healthy units never enqueue/bill alone."""

    from lib.project.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [42, healthy]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes["video_units[0]"] == ["generation_unit_request_invalid"]
    assert codes[first] == ["generation_batch_admission_withheld"]


def test_generate_batch_reports_a_non_scalar_unit_id(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Object/array unit_id rejects before queues; stringification could enqueue but execution cannot match raw IDs."""

    from lib.project.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    broken = {**healthy, "unit_id": {"id": "U9"}}
    script["video_units"] = [broken, healthy]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes["video_units[0]"] == ["generation_unit_request_invalid"]
    assert codes[first] == ["generation_batch_admission_withheld"]


def test_generate_batch_reports_a_duplicated_unit_id(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate unit_id cannot identify which unit; reject all, never bill first matches."""

    from lib.project.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [healthy, {**healthy}]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes[f"{first}#1"] == ["generation_unit_request_invalid"]


@pytest.mark.parametrize("delivery", ["post_production", "use_tts"])
def test_generate_batch_rejects_the_retired_narration_delivery_field(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch, delivery: str
) -> None:
    """Narration delivery no longer belongs to video requests; legacy submissions reject whole batches without enqueue."""

    _seed_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={"narration_delivery": delivery})

    assert resp.status_code == 422, resp.text
    assert [(item["type"], item["loc"][-1]) for item in resp.json()["detail"]] == [
        ("extra_forbidden", "narration_delivery")
    ]
    assert enqueued == []


def test_generate_batch_reports_a_numeric_unit_id(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Numeric unit_id also rejects before enqueue, avoiding stringified/raw execution mismatches."""

    from lib.project.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [{**healthy, "unit_id": 0}, healthy]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes["video_units[0]"] == ["generation_unit_request_invalid"]
    assert codes[first] == ["generation_batch_admission_withheld"]


def test_a_duplicate_marker_never_shadows_a_requested_id(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit nonexistent names must not collide with duplicate-entry diagnostic names; retain separate conclusions."""

    from lib.project.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [healthy, {**healthy}]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = reference_videos_client.post(
        BATCH_ENDPOINT,
        json={"unit_ids": [first, f"{first}#1"]},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes[f"{first}#1"] == ["generation_unit_not_found"]
    assert codes[f"{first}#1*"] == ["generation_unit_request_invalid"]


def test_a_duplicate_marker_never_shadows_a_real_unit_id(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate entries use `id#序号`; existing same-name units require fresh diagnostic names."""

    from lib.project.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [healthy, {**healthy}, {**healthy, "unit_id": f"{first}#1"}]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    unit_ids = [item["unit_id"] for item in body["units"]]
    assert len(unit_ids) == len(set(unit_ids)), unit_ids
    assert set(unit_ids) == {first, f"{first}#1", f"{first}#1*"}
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes[first] == ["generation_batch_admission_withheld"]


def test_generate_batch_refuses_a_path_like_unit_id_before_enqueue(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path-fragment unit IDs reject before tasks; delaying until worker path construction would already bill healthy siblings."""

    from lib.project.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    first = _seed_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    healthy = next(unit for unit in script["video_units"] if unit["unit_id"] == first)
    script["video_units"] = [healthy, {**healthy, "unit_id": "../bad"}]
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes["../bad"] == ["generation_unit_request_invalid"]


def test_generate_batch_reports_a_non_list_video_units_container(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nonarray video_units reports structural problems; iteration would 500, falsy values could falsely pass empty batches."""

    from lib.project.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    _seed_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    script["video_units"] = 42
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes["video_units"] == ["generation_unit_request_invalid"]


def test_generate_unit_rejects_a_path_like_unit_id(reference_videos_client: TestClient, tmp_path: Path):
    """Separator-containing unit_id rejects immediately 400, never deferred output-path failures."""
    script_path = tmp_path / "projects" / "demo" / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"] = [{"unit_id": "a\\b", "text": "镜头平移", "duration_seconds": 3}]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = reference_videos_client.post("/api/v1/projects/demo/reference-videos/episodes/1/units/a%5Cb/generate")
    assert resp.status_code == 400, resp.text


def test_generate_batch_reports_a_falsy_video_units_container(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falsy video_units such as false still reports structure problems, never successful empty batches."""

    from lib.project.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    _seed_unit(reference_videos_client)
    enqueued = _patch_batch_admission(monkeypatch, durations=[3, 6, 9])

    pm: ProjectManager = router_mod.get_project_manager()
    script = pm.load_script("demo", "scripts/episode_1.json")
    script["video_units"] = False
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resp = reference_videos_client.post(BATCH_ENDPOINT, json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "blocked"
    assert enqueued == []
    codes = {item["unit_id"]: [problem["code"] for problem in item["problems"]] for item in body["units"]}
    assert codes["video_units"] == ["generation_unit_request_invalid"]


def test_prompt_preview_renders_draft_with_projected_references_without_saving(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
):

    use_reference_projection(monkeypatch, fake_reference_request_projector(durations=(3, 6, 9), max_reference_images=1))
    uid = _seed_unit(reference_videos_client)
    url = f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}"
    draft = "@[酒馆] 门口，@[张三] 推开门。"
    response = reference_videos_client.post(f"{url}/prompt-preview", json={"prompt": draft})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unavailable"] is None
    assert "<酒馆>@图片1" in body["text"]
    assert "@图片2" not in body["text"]
    assert "<酒馆> 门口，<张三> 推开门。" in body["text"]
    assert body["references"] == [{"type": "scene", "name": "酒馆", "path": "scenes/酒馆.png"}]
    assert any("上限 1" in warning for warning in body["warnings"])
    saved = reference_videos_client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json()
    assert saved["units"][0]["text"] == "镜头1：@张三 推门"

    reference_videos_client.patch(url, json={"prompt": draft})
    precheck = reference_videos_client.get(f"{url}/duration-precheck").json()
    assert precheck["hydrated_capability"] == "r2v"
    assert precheck["problems"][0]["code"] == "reference_images_clamped"
    assert precheck["problems"][0]["message"] in body["warnings"]


def test_prompt_preview_fails_closed_when_capabilities_cannot_resolve(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch
):

    async def _unavailable_request_facts(generation_type):
        raise RuntimeError("provider configuration unavailable")

    use_reference_projection(monkeypatch, fake_reference_request_projector(request_facts=_unavailable_request_facts))
    uid = _seed_unit(reference_videos_client)
    response = reference_videos_client.post(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/prompt-preview",
        json={"prompt": "@[张三] 推门"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] is None
    assert body["references"] == []
    assert body["unavailable"]
    assert body["unavailable"] != "reference_capability_unavailable"
    assert body["warnings"] == []


@pytest.mark.parametrize("prompt", ["", "   "])
def test_prompt_preview_empty_draft_does_not_fall_back_to_saved_text(reference_videos_client: TestClient, prompt: str):
    uid = _seed_unit(reference_videos_client)
    response = reference_videos_client.post(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/prompt-preview", json={"prompt": prompt}
    )
    assert response.status_code == 200
    assert response.json()["text"] is None
    assert response.json()["unavailable"] == "该单元还没有填写正文"


@pytest.mark.parametrize("suffix", ["/episodes/2/units/E1U1", "/episodes/1/units/missing"])
def test_prompt_preview_unknown_target_is_404(reference_videos_client: TestClient, suffix: str):
    response = reference_videos_client.post(
        f"/api/v1/projects/demo/reference-videos{suffix}/prompt-preview", json={"prompt": "正文"}
    )
    assert response.status_code == 404


def test_prompt_preview_requires_authentication(reference_videos_client: TestClient):
    cast(FastAPI, reference_videos_client.app).dependency_overrides.pop(get_current_user)
    response = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/E1U1/prompt-preview", json={"prompt": "正文"}
    )
    assert response.status_code == 401


@pytest.mark.parametrize("audio_exists", [False, True])
def test_prompt_preview_binds_only_available_audio_from_projected_voice_capabilities(
    reference_videos_client: TestClient, monkeypatch: pytest.MonkeyPatch, audio_exists: bool
):
    from server.routers import reference_videos as router_mod

    use_reference_projection(
        monkeypatch,
        fake_reference_request_projector(
            request_facts=fake_reference_request_facts(
                durations=(3,),
                voice_consistency="native",
                max_reference_audio_count=1,
                reference_audio_per_image=True,
            )
        ),
    )
    pm = router_mod.get_project_manager()
    project = pm.load_project("demo")
    project["characters"]["张三"]["reference_audio"] = "characters/refs_audio/张三.mp3"
    project["characters"]["张三"]["voice_style"] = "低沉"
    project["style_description"] = "暖色电影质感"
    pm.save_project("demo", project)
    if audio_exists:
        audio = pm.get_project_path("demo") / "characters/refs_audio/张三.mp3"
        audio.parent.mkdir()
        audio.write_bytes(b"audio")
    uid = _seed_unit(reference_videos_client)
    response = reference_videos_client.post(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/prompt-preview",
        json={"prompt": "@[张三] 推门。\n@[张三]：{进来吧}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "<张三>@图片1" in body["text"]
    assert "<张三> says {进来吧}" in body["text"]
    assert "Voice characteristics: 低沉" in body["text"]
    assert "暖色电影质感" in body["text"]
    assert ("@音频1" in body["text"]) is audio_exists
    assert any("音频当前不可用" in warning for warning in body["warnings"]) is not audio_exists


def _bucket_facts() -> dict[str, Any]:
    """Different bucket video facts require per-unit read tiers following actual buckets."""
    return {
        "i2v": make_video_request_facts(
            route="reference_video", generation_type="i2v", supported_durations=(5, 10), allowed_durations=(5, 10)
        ),
        "r2v": make_video_request_facts(
            route="reference_video", generation_type="r2v", supported_durations=(3, 6, 9), allowed_durations=(9,)
        ),
    }


def _register_character_without_sheet(tmp_path: Path, name: str) -> None:
    project_json = tmp_path / "projects" / "demo" / "project.json"
    project = json.loads(project_json.read_text(encoding="utf-8"))
    project["characters"][name] = {"description": "x"}
    project_json.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")


def test_list_units_reports_the_server_side_bucket_of_each_unit(
    reference_videos_client: TestClient, tmp_path: Path, set_video_request_facts
):
    """Canvas uses server per-unit tiers: usable images r2v, registered missing images i2v with unavailable names."""
    set_video_request_facts(_bucket_facts())
    _register_character_without_sheet(tmp_path, "李四")
    unit_ids = [
        reference_videos_client.post(
            "/api/v1/projects/demo/reference-videos/episodes/1/units",
            json={"prompt": prompt, "duration_seconds": 3},
        ).json()["unit"]["unit_id"]
        for prompt in ("镜头1：@[张三] 推门", "镜头2：@[李四] 回头", "镜头3：空镜")
    ]

    body = reference_videos_client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json()

    assert [unit["unit_id"] for unit in body["units"]] == unit_ids
    with_image, missing_image, no_reference = (body["unit_capabilities"][unit_id] for unit_id in unit_ids)
    assert (with_image["declared_capability"], with_image["hydrated_capability"]) == ("r2v", "r2v")
    assert with_image["allowed_durations"] == [9]
    assert with_image["problems"] == []
    assert (missing_image["declared_capability"], missing_image["hydrated_capability"]) == ("r2v", "i2v")
    assert missing_image["allowed_durations"] == [5, 10]
    assert missing_image["unavailable_references"] == [{"type": "character", "name": "李四"}]
    assert [problem["code"] for problem in missing_image["problems"]] == [
        "reference_asset_missing",
        "reference_capability_changed",
    ]
    assert (no_reference["declared_capability"], no_reference["hydrated_capability"]) == ("i2v", "i2v")
    assert no_reference["allowed_durations"] == [5, 10]
    assert no_reference["problem"] is None


def test_unit_writes_return_the_refreshed_capability(reference_videos_client: TestClient, set_video_request_facts):
    """Creation/body-edit responses return latest bucket facts, allowing canvas tier changes without list refetches."""
    set_video_request_facts(_bucket_facts())
    created = reference_videos_client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "镜头1：空镜", "duration_seconds": 5},
    ).json()
    assert created["unit_capability"]["hydrated_capability"] == "i2v"
    assert created["unit_capability"]["allowed_durations"] == [5, 10]

    patched = reference_videos_client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{created['unit']['unit_id']}",
        json={"prompt": "镜头1：@[张三] 推门"},
    ).json()

    assert patched["unit"]["text"] == "镜头1：@[张三] 推门"
    assert patched["unit_capability"]["hydrated_capability"] == "r2v"
    assert patched["unit_capability"]["allowed_durations"] == [9]
