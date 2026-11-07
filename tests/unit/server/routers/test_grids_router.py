"""Grid-image route tests: success paths and unexpected exception → generic 500 without internal leaks.

For unexpected exceptions, monkeypatch earliest endpoint get_project_manager() calls to raise
RuntimeError with unique sentinels; app exception handlers uniformly return generic 500.
Assert 500 responses without sentinels, proving internal details stay in server logs rather than leaking to clients.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.artifacts.artifact_activation import register_current_resource_artifact
from lib.i18n import _ as i18n_message
from lib.project.project_change_hints import get_project_change_source
from lib.project.project_manager import ProjectManager
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.grid.grid_manager import GridManager
from lib.script.grid.models import GridGeneration
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import grids
from server.routers import versions as versions_router
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import use_project_manager


def _narration_script(count: int = 4):
    """``count`` segments without segment_break form one group, default four, exactly filling grid_4."""
    return {
        "episode": 1,
        "content_mode": "narration",
        "segments": [
            {
                "segment_id": f"E1S{i:02d}",
                "episode": 1,
                "segment_break": False,
                "duration_seconds": 4,
                "novel_text": "text",
                "characters_in_segment": [],
                "scenes": [],
                "props": [],
                "image_prompt": {
                    "scene": f"scene{i}",
                    "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"},
                },
                "video_prompt": {
                    "action": f"action{i}",
                    "camera_motion": "static",
                    "ambiance_audio": "quiet",
                    "dialogue": [],
                },
                "generated_assets": {"storyboard_image": None, "video_clip": None, "status": "pending"},
            }
            for i in range(1, count + 1)
        ],
    }


def _materialize_project(project_path, project: dict) -> None:
    """Persist project states claimed by fake ProjectManager to disk.

    Artifact manifests inspect canonical disk files only; route fakes cannot establish evidence for them.
    """
    (project_path / "scripts").mkdir(parents=True, exist_ok=True)
    (project_path / "project.json").write_text(json.dumps(project), encoding="utf-8")
    (project_path / "scripts" / "episode_1.json").write_text(json.dumps(_narration_script()), encoding="utf-8")


class _FakeQueue:
    """Fake queues recording enqueue calls."""

    def __init__(self, active_resource_ids: set[str] | None = None):
        self.calls = []
        self.active_resource_ids = active_resource_ids

    async def enqueue_task(self, **kwargs):
        self.calls.append(kwargs)
        if self.active_resource_ids is not None:
            self.active_resource_ids.add(kwargs["resource_id"])
        return {"task_id": f"task-{len(self.calls)}", "deduped": False}

    async def get_active_tasks_for_resources(self, *, resource_ids, **_kwargs):
        # Unspecified active_resource_ids retains old fake semantics: every probed resource has active tasks.
        active = set(resource_ids) if self.active_resource_ids is None else self.active_resource_ids
        return [
            {"task_id": f"active-{resource_id}", "resource_id": resource_id}
            for resource_id in resource_ids
            if resource_id in active
        ]


def _client(monkeypatch, **patches):
    for name, fn in patches.items():
        monkeypatch.setattr(grids, name, fn)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(grids.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    # App Exception handlers already convert unexpected failures to 500; disable TestClient default reraising
    # to assert handled response bodies rather than exceptions escaping into tests.
    return TestClient(app, raise_server_exceptions=False)


def test_generate_grid_unexpected_error_no_leak(monkeypatch):
    # generate_grid final catch-all: unexpected load_project exceptions never leak internals.
    client = _client(
        monkeypatch,
        get_project_manager=lambda: (_ for _ in ()).throw(RuntimeError("LEAK_generate")),
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )
        assert resp.status_code == 500
        assert "LEAK_generate" not in resp.text


def test_list_grids_unexpected_error_no_leak(monkeypatch):
    # list_grids final catch-all: unexpected get_project_path exceptions never leak internals.
    client = _client(
        monkeypatch,
        get_project_manager=lambda: (_ for _ in ()).throw(RuntimeError("LEAK_list")),
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grids")
        assert resp.status_code == 500
        assert "LEAK_list" not in resp.text


def test_get_grid_unexpected_error_no_leak(monkeypatch):
    # get_grid final catch-all: unexpected get_project_path exceptions never leak internals.
    client = _client(
        monkeypatch,
        get_project_manager=lambda: (_ for _ in ()).throw(RuntimeError("LEAK_get")),
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grids/grid-123")
        assert resp.status_code == 500
        assert "LEAK_get" not in resp.text


def test_regenerate_grid_unexpected_error_no_leak(monkeypatch):
    # regenerate_grid final catch-all: unexpected load_project exceptions never leak internals.
    client = _client(
        monkeypatch,
        get_project_manager=lambda: (_ for _ in ()).throw(RuntimeError("LEAK_regen")),
    )
    with client:
        resp = client.post("/api/v1/projects/demo/grids/grid-123/regenerate")
        assert resp.status_code == 500
        assert "LEAK_regen" not in resp.text


class _FakeGMNotFound:
    """GridManager fake: get() always returns None, simulating missing grid_id."""

    def __init__(self, project_path):
        pass

    def get(self, grid_id):
        return None


class _FakePMPathOnly:
    """ProjectManager fake exposing get_project_path only, for missing grid_id tests."""

    def get_project_path(self, name):
        return "/fake/path"


class _FakePMNarration(_FakePMPathOnly):
    """ProjectManager fake additionally exposing load_project, passing regeneration project validation."""

    def load_project(self, name):
        return {"content_mode": "narration", "generation_mode": "storyboard", "grid_storyboard": True}


class _FakePMGridDisabled(_FakePMPathOnly):
    """ProjectManager fake with valid generation modes but disabled grids."""

    def load_project(self, name):
        return {"content_mode": "narration", "generation_mode": "storyboard", "grid_storyboard": False}


class _FakePMReferenceVideo(_FakePMPathOnly):
    """ProjectManager fake using reference video; retained grid_storyboard=true never activates grids."""

    def load_project(self, name):
        return {"content_mode": "narration", "generation_mode": "reference_video", "grid_storyboard": True}


def _assert_grid_switch_rejected(resp, queue) -> None:
    """Assert grid-switch-specific rejections before enqueue, creating no billed tasks."""
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == i18n_message("grid_storyboard_not_enabled")
    assert queue.calls == []


@pytest.mark.parametrize("fake_pm", [_FakePMGridDisabled, _FakePMReferenceVideo])
def test_generate_grid_rejected_when_switch_off(monkeypatch, fake_pm):
    # Grid switches gate enqueue: disabled projects return 400 directly, creating no billed tasks.
    fake_queue = _FakeQueue()
    client = _client(monkeypatch, get_project_manager=fake_pm, get_generation_queue=lambda: fake_queue)
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )
        _assert_grid_switch_rejected(resp, fake_queue)


@pytest.mark.parametrize("fake_pm", [_FakePMGridDisabled, _FakePMReferenceVideo])
def test_regenerate_grid_rejected_when_switch_off(monkeypatch, fake_pm):
    # Disabled switches also prevent historical grid records reentering queues.
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=fake_pm,
        GridManager=_FakeGMNotFound,
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post("/api/v1/projects/demo/grids/grid-123/regenerate")
        _assert_grid_switch_rejected(resp, fake_queue)


def test_get_grid_not_found(monkeypatch):
    # gm.get() returns None: raise NotFoundError("grid_not_found", ...) → 404
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMPathOnly,
        GridManager=_FakeGMNotFound,
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grids/grid-missing")
        assert resp.status_code == 404


def test_regenerate_grid_not_found(monkeypatch):
    # After ad project validation, gm.get() returns None: raise NotFoundError("grid_not_found", ...) → 404
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMNarration,
        GridManager=_FakeGMNotFound,
    )
    with client:
        resp = client.post("/api/v1/projects/demo/grids/grid-missing/regenerate")
        assert resp.status_code == 404


class _FakePMInvalidName:
    """ProjectManager fake: load_project / get_project_path simulate invalid names, including traversal."""

    def load_project(self, name):
        raise ValueError(f"非法项目名称: '{name}'")

    def get_project_path(self, name):
        raise ValueError(f"非法项目名称: '{name}'")


def test_generate_grid_invalid_project_name(monkeypatch):
    # load_project ValueError: invalid project names are bad requests, not missing projects → 400
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMInvalidName,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )
        assert resp.status_code == 400


def test_list_grids_invalid_project_name(monkeypatch):
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMInvalidName,
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grids")
        assert resp.status_code == 400


def test_get_grid_invalid_project_name(monkeypatch):
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMInvalidName,
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grids/grid-123")
        assert resp.status_code == 400


def test_regenerate_grid_invalid_project_name(monkeypatch):
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMInvalidName,
    )
    with client:
        resp = client.post("/api/v1/projects/demo/grids/grid-123/regenerate")
        assert resp.status_code == 400


class _FakePMCorrupted:
    """ProjectManager fake: load_project simulates corrupted project.json via JSONDecodeError."""

    def load_project(self, name):
        raise json.JSONDecodeError("Expecting value", "", 0)


def test_generate_grid_corrupted_project_maps_to_500_not_invalid_name(monkeypatch):
    # JSONDecodeError subclasses ValueError; corrupted project.json must not be misclassified by except ValueError
    # as invalid project names. Intercept it first, mapping to generic 500.
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMCorrupted,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )
        assert resp.status_code == 500
        assert "非法项目名称" not in resp.text


def test_regenerate_grid_corrupted_project_maps_to_500_not_invalid_name(monkeypatch):
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMCorrupted,
    )
    with client:
        resp = client.post("/api/v1/projects/demo/grids/grid-123/regenerate")
        assert resp.status_code == 500
        assert "非法项目名称" not in resp.text


class _FakePMInvalidScriptFile:
    """ProjectManager fake: load_script simulates invalid traversal script_file."""

    def load_project(self, name):
        return {
            "content_mode": "narration",
            "aspect_ratio": "9:16",
            "style": "anime",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
        }

    def load_script(self, name, script_file):
        raise ValueError(f"非法文件名: '{script_file}'")


def test_generate_grid_invalid_script_file(monkeypatch):
    # Invalid script_file, such as traversal, is bad request 400, never the later fallback 500.
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMInvalidScriptFile,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "../../etc/passwd"},
        )
        assert resp.status_code == 400


class _FakePMGenerate:
    """ProjectManager fake drives successful generate_grid, with script/project_path under tmp_path."""

    def __init__(self, project_path):
        self._project_path = project_path

    def load_project(self, name):
        return {
            "content_mode": "narration",
            "aspect_ratio": "9:16",
            "style": "anime",
            "style_description": "赛璐璐平涂，高对比描边",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
        }

    def load_script(self, name, script_file):
        return _narration_script()

    def get_project_path(self, name):
        # Missing-only generation uses manifests reading canonical disk files; persist fake-claimed states.
        (self._project_path / "scripts").mkdir(parents=True, exist_ok=True)
        (self._project_path / "project.json").write_text(json.dumps(self.load_project(name)), encoding="utf-8")
        script = json.dumps(self.load_script(name, "episode_1.json"))
        (self._project_path / "scripts" / "episode_1.json").write_text(script, encoding="utf-8")
        return self._project_path


class _FakePMUnboundGrid(_FakePMGenerate):
    def load_project(self, name):
        return {**super().load_project(name), "episodes": []}


class _FakePMMismatchedGrid(_FakePMGenerate):
    def load_project(self, name):
        return {
            **super().load_project(name),
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }


def test_generate_grid_rejects_an_unbound_script_before_enqueue(monkeypatch, tmp_path):
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMUnboundGrid(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )

    with client:
        response = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == i18n_message("invalid_script_file", name="「未命名集」的剧本")
    assert fake_queue.calls == []


def test_generate_grid_rejects_an_episode_path_that_mismatches_the_bound_script(monkeypatch, tmp_path):
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMMismatchedGrid(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )

    with client:
        response = client.post(
            "/api/v1/projects/demo/generate/grid/2",
            json={"script_file": "episode_1.json"},
        )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == i18n_message("invalid_script_file", name="「未命名集」的剧本")
    assert fake_queue.calls == []


def test_generate_grid_success(monkeypatch, tmp_path):
    # Cover grouping → layout → prompt → enqueue, asserting 200 and one grid_id/task_id per group.
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMGenerate(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["grid_ids"]) == 1
        assert len(body["task_ids"]) == 1
        assert body["deduped"] is False
        # message uses i18n, default Chinese, no hardcoding.
        assert body["message"] == "已提交 1 个多宫格分镜生成任务"
    assert len(fake_queue.calls) == 1
    saved = json.loads((tmp_path / "grids" / f"{body['grid_ids'][0]}.json").read_text(encoding="utf-8"))
    assert saved["scene_ids"] == ["E1S01", "E1S02", "E1S03", "E1S04"]
    prompt_lines = fake_queue.calls[0]["payload"]["prompt"].splitlines()
    assert "Style: anime" in prompt_lines
    assert "Visual style: 赛璐璐平涂，高对比描边" in prompt_lines
    assert saved["prompt"] == fake_queue.calls[0]["payload"]["prompt"]


class _FakePMBlockedReference(_FakePMGenerate):
    """Shots reference unregistered scenes: grids are the most expensive batch images, so block gaps before submission."""

    def load_script(self, name, script_file):
        script = _narration_script()
        script["segments"][2]["scenes"] = ["未登记的场景"]
        return script


class _FakePMSheetlessReference(_FakePMGenerate):
    def load_project(self, name):
        return {**super().load_project(name), "characters": {"Alice": {"reference_image": "characters/a.png"}}}

    def load_script(self, name, script_file):
        script = _narration_script()
        script["segments"][1]["characters_in_segment"] = ["Alice"]
        return script


class _FakePMPendingPrompt(_FakePMGenerate):
    """One mechanically converted shot still has image_prompt None: reject entire batches, never pay to generate from None prompts."""

    def load_script(self, name, script_file):
        script = _narration_script()
        script["segments"][2]["image_prompt"] = None
        return script


def test_generate_grid_blocks_the_whole_batch_on_a_pending_prompt(monkeypatch, tmp_path):
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMPendingPrompt(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post("/api/v1/projects/demo/generate/grid/1", json={"script_file": "episode_1.json"})

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == i18n_message("script_prompt_pending", segment_id="未命名集 · S03")
    assert fake_queue.calls == []
    assert not (tmp_path / "grids").exists()


def test_generate_grid_blocks_the_whole_batch_on_an_unregistered_reference(monkeypatch, tmp_path):
    """Whole-batch admission: one incomplete shot rejects everything, preventing healthy groups being billed alone."""
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMBlockedReference(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )

    with client:
        resp = client.post("/api/v1/projects/demo/generate/grid/1", json={"script_file": "episode_1.json"})

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == i18n_message("reference_asset_unregistered", missing_text="未登记的场景")
    assert fake_queue.calls == []


def test_generate_grid_blocks_a_character_without_an_asset_sheet(monkeypatch, tmp_path):
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMSheetlessReference(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )

    with client:
        resp = client.post("/api/v1/projects/demo/generate/grid/1", json={"script_file": "episode_1.json"})

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == i18n_message("reference_asset_missing", missing_text="character: Alice")
    assert fake_queue.calls == []


def test_generate_grid_success_message_localized_en(monkeypatch, tmp_path):
    # Accept-Language=en renders English messages, proving success copy uses Translator.
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMGenerate(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
            headers={"Accept-Language": "en"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["message"] == "Submitted 1 multi-grid storyboard generation tasks"


class _FakePMScenes(_FakePMGenerate):
    """``_FakePMGenerate`` variant with configurable script segment counts for cross-tier step assertions."""

    def __init__(self, project_path, scene_count: int):
        super().__init__(project_path)
        self._scene_count = scene_count

    def load_script(self, name, script_file):
        return _narration_script(self._scene_count)


def _generate_with_gate(monkeypatch, tmp_path, *, scene_count: int, allow_large_grid: bool):
    """Run generate_grid once, returning enqueued payload lists; inject 4K-gate results directly."""

    async def _gate(_project):
        return allow_large_grid

    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMScenes(tmp_path, scene_count),
        get_generation_queue=lambda: fake_queue,
        resolve_large_grid_allowed=_gate,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )
        assert resp.status_code == 200
    return [call["payload"] for call in fake_queue.calls]


@pytest.mark.parametrize(
    ("scene_count", "grid_size", "side"),
    [(10, "grid_16", 4), (16, "grid_16", 4), (17, "grid_25", 5), (25, "grid_25", 5)],
)
def test_generate_grid_uses_large_grid_when_4k(monkeypatch, tmp_path, scene_count, grid_size, side):
    payloads = _generate_with_gate(monkeypatch, tmp_path, scene_count=scene_count, allow_large_grid=True)
    assert len(payloads) == 1
    assert payloads[0]["grid_size"] == grid_size
    assert (payloads[0]["rows"], payloads[0]["cols"]) == (side, side)
    # Square-tier whole-image ratios equal project video ratios.
    assert payloads[0]["grid_aspect_ratio"] == payloads[0]["video_aspect_ratio"] == "9:16"


def test_generate_grid_above_25_chunks_at_25(monkeypatch, tmp_path):
    payloads = _generate_with_gate(monkeypatch, tmp_path, scene_count=30, allow_large_grid=True)
    assert [len(p["scene_ids"]) for p in payloads] == [25, 5]
    assert [p["grid_size"] for p in payloads] == ["grid_25", "grid_9"]


@pytest.mark.parametrize("scene_count", [10, 17, 30])
def test_generate_grid_caps_at_9_without_4k(monkeypatch, tmp_path, scene_count):
    payloads = _generate_with_gate(monkeypatch, tmp_path, scene_count=scene_count, allow_large_grid=False)
    # Enabled gates cap chunks at 9; remainders use smaller tiers, never 4×4 / 5×5.
    assert all(p["grid_size"] in {"grid_4", "grid_9"} for p in payloads)
    assert all(p["rows"] * p["cols"] <= 9 for p in payloads)
    assert sum(len(p["scene_ids"]) for p in payloads) == scene_count


@pytest.mark.parametrize("scene_count", [5, 6])
def test_generate_grid_5_and_6_scenes_use_grid_9(monkeypatch, tmp_path, scene_count):
    # grid_6 removed: 5~6 scenes use grid_9, filling remaining cells with placeholders.
    payloads = _generate_with_gate(monkeypatch, tmp_path, scene_count=scene_count, allow_large_grid=False)
    assert len(payloads) == 1
    assert payloads[0]["grid_size"] == "grid_9"
    assert (payloads[0]["rows"], payloads[0]["cols"]) == (3, 3)


def test_generate_grid_maps_each_grid_to_the_task_it_enqueued(monkeypatch, tmp_path):
    """Per-grid mapping: each grid_id points to its own enqueue-returned task_id."""

    async def _gate(_project):
        return False

    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        # Thirty segments split into multiple grids under nine-cell caps, yielding distinguishable mappings.
        get_project_manager=lambda: _FakePMScenes(tmp_path, 30),
        get_generation_queue=lambda: fake_queue,
        resolve_large_grid_allowed=_gate,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    enqueued = {call["resource_id"]: f"task-{index}" for index, call in enumerate(fake_queue.calls, start=1)}
    assert len(enqueued) > 1
    assert body["task_ids_by_grid"] == enqueued
    assert body["grid_ids"] == list(enqueued)
    assert body["task_ids"] == list(enqueued.values())


def test_generate_grid_rejects_an_unknown_scene_without_enqueueing(monkeypatch, tmp_path):
    """Named shots absent from scripts reject whole batches, creating zero tasks."""
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMGenerate(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json", "scene_ids": ["E1S01", "E9S99"]},
        )

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == i18n_message("segment_not_found", id="未命名集 · S99")
    assert fake_queue.calls == []


def test_generate_grid_resubmitted_while_in_flight_reuses_the_pending_grid(monkeypatch, tmp_path):
    """Repeated generation clicks reuse in-flight records; queues dedupe resources, avoiding new billed grids."""
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMGenerate(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        first = client.post("/api/v1/projects/demo/generate/grid/1", json={"script_file": "episode_1.json"})
        second = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json", "scene_ids": ["E1S01", "E1S02", "E1S03", "E1S04"]},
        )

    assert first.status_code == second.status_code == 200, second.text
    assert second.json()["grid_ids"] == first.json()["grid_ids"]
    assert [call["resource_id"] for call in fake_queue.calls] == first.json()["grid_ids"] * 2
    assert [g.id for g in GridManager(tmp_path).list_all()] == first.json()["grid_ids"]


def test_generate_grid_refuses_a_batch_overlapping_another_in_flight_grid(monkeypatch, tmp_path):
    fake_queue = _FakeQueue()
    other = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S03", "E1S04"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="",
        model="",
        video_aspect_ratio="9:16",
    )
    GridManager(tmp_path).save(other)
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMGenerate(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post("/api/v1/projects/demo/generate/grid/1", json={"script_file": "episode_1.json"})

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == i18n_message("grid_generation_in_progress", grid_id=other.id)
    assert fake_queue.calls == []


def test_generate_all_skips_a_ready_but_unsplit_composite(monkeypatch, tmp_path):
    """All fills missing only: ready joint images awaiting cell splitting wait for user review, never regenerate or bill twice."""
    fake_queue = _FakeQueue()
    unsplit = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S01", "E1S02", "E1S03", "E1S04"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="",
        model="",
        video_aspect_ratio="9:16",
    )
    unsplit.status = "completed"
    unsplit.grid_image_path = f"grids/{unsplit.id}.png"
    GridManager(tmp_path).save(unsplit)
    (tmp_path / "grids" / f"{unsplit.id}.png").write_bytes(b"png")
    _materialize_project(tmp_path, _FakePMGenerate(tmp_path).load_project("demo"))
    assert register_current_resource_artifact(tmp_path, resource_type="grids", resource_id=unsplit.id)
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMGenerate(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post("/api/v1/projects/demo/generate/grid/1", json={"script_file": "episode_1.json"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["grid_ids"] == []
    assert body["unsplit_grid_ids"] == [unsplit.id]
    assert body["message"] == i18n_message("grid_unsplit_awaiting_split", unsplit=1)
    assert fake_queue.calls == []


class _FakePMReferenceRoute(_FakePMGenerate):
    """Reference-video script skeletons lack arrays required by storyboard image-to-video."""

    def load_script(self, name, script_file):
        return {"episode": 1, "content_mode": "narration", "video_units": []}


def test_generate_grid_rejects_a_script_of_the_other_route(monkeypatch, tmp_path):
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMReferenceRoute(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post("/api/v1/projects/demo/generate/grid/1", json={"script_file": "episode_1.json"})

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == i18n_message("grid_script_route_mismatch")
    assert fake_queue.calls == []


def test_grid_capability_reports_gate(monkeypatch, tmp_path):
    async def _gate(_project):
        return True

    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMGenerate(tmp_path),
        resolve_large_grid_allowed=_gate,
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grid-capability")
        assert resp.status_code == 200
        assert resp.json() == {"large_grid_allowed": True, "max_cell_count": 25}


def test_grid_capability_gated_max_cell_count(monkeypatch, tmp_path):
    async def _gate(_project):
        return False

    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMGenerate(tmp_path),
        resolve_large_grid_allowed=_gate,
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grid-capability")
        assert resp.json() == {"large_grid_allowed": False, "max_cell_count": 9}


class _FakePMPath:
    """ProjectManager fake exposing get_project_path pointing to tmp_path only."""

    def __init__(self, project_path):
        self._project_path = project_path

    def get_project_path(self, name):
        return self._project_path


def test_list_grids_success(monkeypatch, tmp_path):
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["a", "b"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="",
        model="",
        video_aspect_ratio="9:16",
    )
    GridManager(tmp_path).save(grid)
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMPath(tmp_path))
    with client:
        resp = client.get("/api/v1/projects/demo/grids")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["id"] == grid.id


def test_get_grid_success(monkeypatch, tmp_path):
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["a", "b"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="",
        model="",
        video_aspect_ratio="9:16",
    )
    GridManager(tmp_path).save(grid)
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMPath(tmp_path))
    with client:
        resp = client.get(f"/api/v1/projects/demo/grids/{grid.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == grid.id


@pytest.mark.parametrize(
    "bad_id",
    [
        "..%2F..%2Fetc%2Fpasswd",  # URL-encoded ../../etc/passwd
        "grid_..%2F..%2Fsecret",  # Valid prefixes containing traversal segments
        "grid_ABCDEF123456",  # Uppercase hex fails whitelists.
        "not-a-grid-id",
    ],
)
def test_get_grid_malformed_id_returns_404(monkeypatch, tmp_path, bad_id):
    """URL grid_id parameters with invalid formats always return 404, never reading filesystem paths out of bounds."""
    outside = tmp_path.parent / "secret.json"
    outside.write_text('{"leak": true}', encoding="utf-8")
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMPath(tmp_path))
    with client:
        resp = client.get(f"/api/v1/projects/demo/grids/{bad_id}")
        assert resp.status_code == 404
        assert "leak" not in resp.text


class _FakePMRegenerate(_FakePMPath):
    """ProjectManager fake drives successful regenerate_grid."""

    def load_project(self, name):
        return {
            "content_mode": "narration",
            "aspect_ratio": "9:16",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
        }

    def load_script(self, name, script_file):
        return _narration_script()


class _FakePMRegenerateUnbound(_FakePMRegenerate):
    def load_project(self, name):
        return {**super().load_project(name), "episodes": []}


def test_regenerate_grid_rejects_an_unbound_script_without_mutating_the_record(monkeypatch, tmp_path):
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["a", "b", "c", "d"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="stale-provider",
        model="stale-model",
        video_aspect_ratio="9:16",
    )
    grid.status = "failed"
    grid.error_message = "boom"
    GridManager(tmp_path).save(grid)

    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegenerateUnbound(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        response = client.post(f"/api/v1/projects/demo/grids/{grid.id}/regenerate")

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == i18n_message("invalid_script_file", name="「未命名集」的剧本")
    assert fake_queue.calls == []
    assert GridManager(tmp_path).get(grid.id) == grid


def test_regenerate_grid_success(monkeypatch, tmp_path):
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["a", "b", "c", "d"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="stale-provider",
        model="stale-model",
        video_aspect_ratio="9:16",
    )
    grid.status = "failed"
    grid.error_message = "boom"
    GridManager(tmp_path).save(grid)

    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegenerate(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post(f"/api/v1/projects/demo/grids/{grid.id}/regenerate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["task_id"] == "task-1"
    assert len(fake_queue.calls) == 1
    saved = GridManager(tmp_path).get(grid.id)
    assert saved is not None
    assert saved.status == "pending"
    assert saved.error_message is None
    assert saved.provider == ""


def test_regenerate_replaces_an_orphan_with_an_active_task(monkeypatch, tmp_path):
    """Orphan records enqueue on regeneration; real active tasks still block later splitting."""
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["a", "b", "c", "d"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="",
        model="",
        video_aspect_ratio="9:16",
    )
    grid.status = "generating"
    GridManager(tmp_path).save(grid)
    queue = _FakeQueue(active_resource_ids=set())
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegenerate(tmp_path),
        get_generation_queue=lambda: queue,
    )

    with client:
        regenerate = client.post(f"/api/v1/projects/demo/grids/{grid.id}/regenerate")
        split = client.post(f"/api/v1/projects/demo/grids/{grid.id}/split")

    assert regenerate.status_code == 200, regenerate.text
    assert split.status_code == 409, split.text
    assert queue.active_resource_ids == {grid.id}


class _FakePMRegeneratePendingPrompt(_FakePMRegenerate):
    def load_script(self, name, script_file):
        script = _narration_script()
        script["segments"][0]["image_prompt"] = None
        return script


def test_regenerate_grid_blocks_on_a_pending_prompt_without_mutating_the_record(monkeypatch, tmp_path):
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S01", "E1S02", "E1S03", "E1S04"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="stale-provider",
        model="stale-model",
        video_aspect_ratio="9:16",
    )
    grid.status = "failed"
    GridManager(tmp_path).save(grid)

    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegeneratePendingPrompt(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post(f"/api/v1/projects/demo/grids/{grid.id}/regenerate")

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == i18n_message("script_prompt_pending", segment_id="未命名集 · S01")
    assert fake_queue.calls == []
    assert GridManager(tmp_path).get(grid.id) == grid


class _FakePMRegenerateBlockedReference(_FakePMRegenerate):
    def load_script(self, name, script_file):
        script = _narration_script()
        script["segments"][0]["props"] = ["未登记的道具"]
        return script


def test_regenerate_grid_blocks_on_a_reference_gap_without_mutating_the_record(monkeypatch, tmp_path):
    """Regeneration bills again: same admission rules as initial generation; rejected records retain status."""
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S01", "E1S02", "E1S03", "E1S04"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="stale-provider",
        model="stale-model",
        video_aspect_ratio="9:16",
    )
    grid.status = "failed"
    grid.error_message = "boom"
    GridManager(tmp_path).save(grid)

    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegenerateBlockedReference(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post(f"/api/v1/projects/demo/grids/{grid.id}/regenerate")

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == i18n_message("reference_asset_unregistered", missing_text="未登记的道具")
    assert fake_queue.calls == []
    assert GridManager(tmp_path).get(grid.id) == grid


def _regenerate_with_frozen_ratio(monkeypatch, tmp_path, frozen: str | None) -> tuple[GridGeneration, dict]:
    """Create records with supplied frozen values and regenerate, returning persisted records/enqueued payloads."""
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["a", "b", "c", "d"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="p",
        model="m",
        video_aspect_ratio="16:9",
    )
    grid.video_aspect_ratio = frozen
    grid.status = "completed"
    GridManager(tmp_path).save(grid)

    queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegenerate(tmp_path),
        get_generation_queue=lambda: queue,
    )
    with client:
        assert client.post(f"/api/v1/projects/demo/grids/{grid.id}/regenerate").status_code == 200

    saved = GridManager(tmp_path).get(grid.id)
    assert saved is not None
    return saved, queue.calls[0]["payload"]


def test_regenerate_grid_keeps_frozen_aspect_ratio(monkeypatch, tmp_path):
    """Regeneration retains frozen record ratios, never current project ratios."""
    saved, payload = _regenerate_with_frozen_ratio(monkeypatch, tmp_path, "16:9")

    assert saved.video_aspect_ratio == "16:9"
    assert payload["video_aspect_ratio"] == "16:9"
    assert payload["grid_aspect_ratio"] == "16:9"


def test_regenerate_grid_backfills_missing_aspect_ratio(monkeypatch, tmp_path):
    """Legacy records without frozen values fall back to current project ratios, backfilling in place."""
    saved, payload = _regenerate_with_frozen_ratio(monkeypatch, tmp_path, None)

    assert saved.video_aspect_ratio == "9:16"
    assert payload["video_aspect_ratio"] == "9:16"


# ==================== Split endpoints ====================


def _make_completed_grid(tmp_path, *, with_image: bool = True) -> GridGeneration:
    _materialize_project(tmp_path, _FakePMRegenerate(tmp_path).load_project("demo"))
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["a", "b", "c", "d"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="p",
        model="m",
        video_aspect_ratio="9:16",
    )
    grid.status = "completed"
    grid.grid_image_path = f"grids/{grid.id}.png"
    if with_image:
        from PIL import Image

        (tmp_path / "grids").mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 100)).save(tmp_path / "grids" / f"{grid.id}.png")
    GridManager(tmp_path).save(grid)
    return grid


def test_split_grid_success(monkeypatch, tmp_path):
    grid = _make_completed_grid(tmp_path)

    from server.services.grid.grid_split import GridSplitResult

    calls = []

    async def fake_split(project_name, g):
        calls.append((project_name, g.id))
        g.split_at = "2026-01-01T00:00:00+00:00"
        return GridSplitResult(
            updated_scene_ids=["a", "b"],
            missing_scene_ids=["c"],
            asset_fingerprints={"storyboards/scene_a.png": 1},
        )

    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegenerate(tmp_path),
        apply_grid_split=fake_split,
    )
    with client:
        resp = client.post(f"/api/v1/projects/demo/grids/{grid.id}/split")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["updated_scene_ids"] == ["a", "b"]
        assert body["missing_scene_ids"] == ["c"]
        assert body["asset_fingerprints"] == {"storyboards/scene_a.png": 1}
        assert body["split_at"] == "2026-01-01T00:00:00+00:00"
    assert calls == [("demo", grid.id)]


def test_split_grid_rejected_when_switch_off(monkeypatch, tmp_path):
    grid = _make_completed_grid(tmp_path)
    client = _client(monkeypatch, get_project_manager=_FakePMGridDisabled)
    with client:
        resp = client.post(f"/api/v1/projects/demo/grids/{grid.id}/split")
        assert resp.status_code == 400
        assert resp.json()["detail"] == i18n_message("grid_storyboard_not_enabled")


def test_split_grid_conflict_while_generating(monkeypatch, tmp_path):
    # Reject splitting generating grids: workers overwrite joint images on completion, invalidating old-image splits.
    grid = _make_completed_grid(tmp_path)
    grid.status = "generating"
    GridManager(tmp_path).save(grid)
    queue = _FakeQueue(active_resource_ids={grid.id})

    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegenerate(tmp_path),
        get_generation_queue=lambda: queue,
    )
    with client:
        resp = client.post(f"/api/v1/projects/demo/grids/{grid.id}/split")
        assert resp.status_code == 409


def test_split_grid_allows_record_left_generating_after_cancel_or_restart(monkeypatch, tmp_path):
    """Generating records without active queue tasks pass in-flight splitting gates, then fail image-not-ready checks."""
    grid = _make_completed_grid(tmp_path, with_image=False)
    grid.status = "generating"
    GridManager(tmp_path).save(grid)

    queue = _FakeQueue(active_resource_ids=set())
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegenerate(tmp_path),
        get_generation_queue=lambda: queue,
    )
    with client:
        resp = client.post(f"/api/v1/projects/demo/grids/{grid.id}/split")

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == i18n_message("grid_image_not_ready", grid_id=grid.id)


def test_split_grid_image_not_ready(monkeypatch, tmp_path):
    # Missing joint images, neither generated nor uploaded, return explicit business 400, never generic 500.
    grid = _make_completed_grid(tmp_path, with_image=False)

    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))
    with client:
        resp = client.post(f"/api/v1/projects/demo/grids/{grid.id}/split")
        assert resp.status_code == 400
        assert resp.json()["detail"] == i18n_message("grid_image_not_ready", grid_id=grid.id)


def test_split_grid_not_found(monkeypatch, tmp_path):
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMNarration,
        GridManager=_FakeGMNotFound,
    )
    with client:
        resp = client.post("/api/v1/projects/demo/grids/grid-missing/split")
        assert resp.status_code == 404


# ==================== Joint-image uploads ====================


def _png_bytes(size=(64, 64), color=(1, 2, 3)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(size=(64, 64), color=(9, 9, 9)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def test_upload_grid_image_normalizes_to_png_and_versions(monkeypatch, tmp_path):
    """Normalize non-PNG inputs to PNG, register versions, reset grids to joint-image-ready awaiting splitting."""
    from lib.artifacts.version_manager import VersionManager

    grid = _make_completed_grid(tmp_path)
    grid.status = "failed"
    grid.error_message = "boom"
    grid.split_at = "2026-01-01T00:00:00+00:00"
    GridManager(tmp_path).save(grid)

    monkeypatch.setattr(
        "server.services.tasks.generation_tasks.emit_generation_success_batch",
        lambda **kw: {f"grids/{grid.id}.png": 123},
    )
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["path"] == f"grids/{grid.id}.png"
        assert body["asset_fingerprints"] == {f"grids/{grid.id}.png": 123}

    # Persist valid PNGs.
    from io import BytesIO

    from PIL import Image

    saved_bytes = (tmp_path / "grids" / f"{grid.id}.png").read_bytes()
    with Image.open(BytesIO(saved_bytes)) as img:
        assert img.format == "PNG"

    # Backfill old joint images + register new versions (source=upload).
    versions = VersionManager(tmp_path).get_versions("grids", grid.id)
    assert len(versions["versions"]) >= 2

    saved = GridManager(tmp_path).get(grid.id)
    assert saved is not None
    assert saved.status == "completed"
    assert saved.error_message is None
    assert saved.split_at is None
    assert saved.grid_image_path == f"grids/{grid.id}.png"


def test_upload_grid_image_marks_stage_and_emit_as_webui(monkeypatch, tmp_path):
    """Upload persistence and success events must both run in webui contexts, preventing false filesystem-change classification."""
    grid = _make_completed_grid(tmp_path)
    sources: list[tuple[str, str]] = []
    original_stage = grids.stage_uploaded_bytes

    def _stage(*args, **kwargs):
        sources.append(("stage", get_project_change_source()))
        return original_stage(*args, **kwargs)

    def _emit(**_kwargs):
        sources.append(("emit", get_project_change_source()))
        return {}

    monkeypatch.setattr(grids, "stage_uploaded_bytes", _stage)
    monkeypatch.setattr("server.services.tasks.generation_tasks.emit_generation_success_batch", _emit)
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))

    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("photo.png", _png_bytes(), "image/png")},
        )

    assert resp.status_code == 200, resp.text
    assert sources == [("stage", "webui"), ("emit", "webui")]


def test_restoring_an_uploaded_grid_version_preserves_its_manifest_claim(monkeypatch, tmp_path):
    from io import BytesIO

    from PIL import Image

    from lib.artifacts.artifact_activation import ArtifactCurrencyResolver
    from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus
    from lib.artifacts.version_manager import VersionManager
    from server.services.currency.upload_finalize import UPLOAD_VERSION_SOURCE

    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata(
        "demo",
        "Demo",
        "Anime",
        "narration",
        extras={"generation_mode": "storyboard", "grid_storyboard": True},
    )
    script = {"episode": 1, "title": "E1", **_narration_script()}
    pm.save_script("demo", script, "episode_1.json", validate=False)
    project_path = pm.get_project_path("demo")
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S01", "E1S02", "E1S03", "E1S04"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="p",
        model="m",
        video_aspect_ratio="9:16",
    )
    grid.status = "completed"
    grid.grid_image_path = f"grids/{grid.id}.png"
    target = project_path / grid.grid_image_path
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color=(1, 2, 3)).save(target)
    GridManager(project_path).save(grid)

    use_project_manager(monkeypatch, pm)
    monkeypatch.setattr("server.services.tasks.generation_tasks.emit_generation_success_batch", lambda **_kwargs: {})
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(grids.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(versions_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)

    with TestClient(app) as client:
        first = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("first.png", BytesIO(_png_bytes(color=(10, 20, 30))), "image/png")},
        )
        second = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("second.png", BytesIO(_png_bytes(color=(40, 50, 60))), "image/png")},
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text

        uploaded = [
            record
            for record in VersionManager(project_path).get_versions("grids", grid.id)["versions"]
            if record.get("source") == UPLOAD_VERSION_SOURCE
        ]
        assert len(uploaded) == 2
        assert "artifact_image_basis" in uploaded[0], uploaded[0]
        restored = client.post(f"/api/v1/projects/demo/versions/grids/{grid.id}/restore/{uploaded[0]['version']}")
        assert restored.status_code == 200, restored.text

    comparison = ArtifactCurrencyResolver(project_path).compare(
        ArtifactKey.episode_grid(1, grid.id),
        artifact_path=f"grids/{grid.id}.png",
    )
    assert comparison.status is ArtifactStatus.CURRENT


def test_upload_grid_image_refreshes_frozen_aspect_ratio(monkeypatch, tmp_path):
    """Manual supplemental images use current project layouts, updating frozen cell ratios accordingly.

    Retaining old frozen values center-crops supplemental images to old ratios after project ratio changes.
    """
    grid = _make_completed_grid(tmp_path)
    grid.video_aspect_ratio = "16:9"
    GridManager(tmp_path).save(grid)

    monkeypatch.setattr(
        "server.services.tasks.generation_tasks.emit_generation_success_batch",
        lambda **kw: {},
    )
    # _FakePMRegenerate project ratio 9:16 differs from frozen record 16:9.
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("a.png", _png_bytes(), "image/png")},
        )
        assert resp.status_code == 200, resp.text

    saved = GridManager(tmp_path).get(grid.id)
    assert saved is not None
    assert saved.video_aspect_ratio == "9:16"


def test_upload_grid_image_registration_failure_restores_file_version_and_record(monkeypatch, tmp_path):
    from lib.artifacts.version_manager import VersionManager

    grid = _make_completed_grid(tmp_path)
    grid.split_at = "2026-01-01T00:00:00+00:00"
    GridManager(tmp_path).save(grid)
    target = tmp_path / "grids" / f"{grid.id}.png"
    old_bytes = target.read_bytes()
    versions = VersionManager(tmp_path)
    versions.add_version("grids", grid.id, "old", source_file=target)
    versions_bytes = versions.versions_file.read_bytes()
    record = tmp_path / "grids" / f"{grid.id}.json"
    record_bytes = record.read_bytes()

    monkeypatch.setattr(
        grids,
        "register_current_resource_artifact",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("manifest commit failed")),
    )
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("replacement.png", _png_bytes(color=(200, 1, 2)), "image/png")},
        )

    assert resp.status_code == 500
    assert target.read_bytes() == old_bytes
    assert versions.versions_file.read_bytes() == versions_bytes
    assert record.read_bytes() == record_bytes


def test_upload_grid_image_does_not_downscale(monkeypatch, tmp_path):
    """Joint-image uploads never resize: retain dimensions beyond storyboard 2048 caps, preserving 4K cell-split quality."""
    grid = _make_completed_grid(tmp_path)
    monkeypatch.setattr(
        "server.services.tasks.generation_tasks.emit_generation_success_batch",
        lambda **kw: {},
    )
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("big.png", _png_bytes(size=(4096, 64)), "image/png")},
        )
        assert resp.status_code == 200, resp.text

    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO((tmp_path / "grids" / f"{grid.id}.png").read_bytes())) as img:
        assert img.size == (4096, 64)


def test_upload_grid_image_rejects_invalid_image(monkeypatch, tmp_path):
    grid = _make_completed_grid(tmp_path)
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("bad.png", b"not-an-image", "image/png")},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == i18n_message("invalid_image_file")


def test_upload_grid_image_conflict_while_generating(monkeypatch, tmp_path):
    grid = _make_completed_grid(tmp_path)
    grid.status = "pending"
    GridManager(tmp_path).save(grid)
    queue = _FakeQueue(active_resource_ids={grid.id})
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegenerate(tmp_path),
        get_generation_queue=lambda: queue,
    )
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("a.png", _png_bytes(), "image/png")},
        )
        assert resp.status_code == 409


def test_upload_grid_image_recovers_record_left_pending_with_no_active_task(monkeypatch, tmp_path):
    """Grids with pending records only after restarts allow manual image uploads; success restores completed."""
    grid = _make_completed_grid(tmp_path)
    grid.status = "pending"
    GridManager(tmp_path).save(grid)
    queue = _FakeQueue(active_resource_ids=set())
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegenerate(tmp_path),
        get_generation_queue=lambda: queue,
    )
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("a.png", _png_bytes(), "image/png")},
        )

    assert resp.status_code == 200, resp.text
    saved = GridManager(tmp_path).get(grid.id)
    assert saved is not None
    assert saved.status == "completed"


def test_upload_grid_image_rejected_when_switch_off(monkeypatch, tmp_path):
    grid = _make_completed_grid(tmp_path)
    client = _client(monkeypatch, get_project_manager=_FakePMGridDisabled)
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("a.png", _png_bytes(), "image/png")},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == i18n_message("grid_storyboard_not_enabled")


async def test_upload_grid_image_cancelled_while_staging_leaves_no_staged_file(monkeypatch, tmp_path):
    """Cancellation during staging writes waits for threads before propagating, allowing registered staged-file cleanup."""
    import asyncio
    import threading
    from io import BytesIO

    from fastapi import UploadFile

    grid = _make_completed_grid(tmp_path)
    grids_dir = tmp_path / "grids"
    before = set(grids_dir.iterdir())
    started = threading.Event()
    release = threading.Event()
    staged = threading.Event()
    original_stage = grids.stage_uploaded_bytes

    def _stage(*args, **kwargs):
        started.set()
        release.wait(5)
        try:
            return original_stage(*args, **kwargs)
        finally:
            staged.set()

    monkeypatch.setattr(grids, "stage_uploaded_bytes", _stage)
    use_project_manager(monkeypatch, _FakePMRegenerate(tmp_path))
    content = _png_bytes()
    task = asyncio.create_task(
        grids.upload_grid_image(
            project_name="demo",
            grid_id=grid.id,
            user=CurrentUserInfo(id="default", sub="testuser", role="admin"),
            _t=lambda key, **_kwargs: key,
            file=UploadFile(file=BytesIO(content), filename="photo.png", size=len(content)),
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(staged.wait, 5)

    assert set(grids_dir.iterdir()) == before
