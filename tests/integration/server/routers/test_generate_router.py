import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.artifacts.artifact_activation import ArtifactKey, register_current_artifact_if_provable
from lib.i18n import _ as i18n_message
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import generate
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.factories import make_display_names
from tests.fakes import use_generation_queue, use_project_manager
from tests.speech_contract_cases import SPEECH_CONTRACT_CASES, SpeechContractCase


class _FakeQueue:
    """Mock GenerationQueue that records enqueue calls."""

    def __init__(self):
        self.calls = []
        self.active_by_user: dict[str, list[dict[str, str]]] = {}
        self.active_queries: list[dict[str, object]] = []

    async def enqueue_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"task_id": f"task-{len(self.calls)}", "deduped": False}

    async def get_active_tasks_for_resources(self, **kwargs):
        self.active_queries.append(kwargs)
        return self.active_by_user.get(str(kwargs.get("user_id")), [])


class _FakePM:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.project = {
            # Production projects always use current schemas with episodes-bound scripts;
            # manifests alone determine generated artifacts; both are required for realistic projects.
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
            "generation_mode": "storyboard",
            "style": "Anime",
            "style_description": "cinematic",
            "content_mode": "narration",
            "characters": {
                "Alice": {
                    "character_sheet": "characters/Alice.png",
                    "reference_image": "characters/refs/Alice_ref.png",
                    "description": "hero",
                }
            },
            "scenes": {
                "祠堂": {
                    "scene_sheet": "scenes/祠堂.png",
                    "description": "scene",
                }
            },
            "props": {
                "玉佩": {
                    "prop_sheet": "props/玉佩.png",
                    "description": "prop",
                }
            },
            "products": {
                "保温杯": {
                    "product_sheet": "",
                    "brand": "",
                    "reference_images": ["products/refs/保温杯_1.jpg"],
                    "selling_points": [],
                    "description": "product",
                }
            },
        }
        self.script = {
            "episode": 1,
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "novel_text": "风吹过旷野。",
                    "image_prompt": {
                        "scene": "旷野",
                        "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"},
                    },
                    "video_prompt": {},
                    "segment_break": False,
                    "characters_in_segment": [],
                    "scenes": [],
                    "props": [],
                    "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
                },
                {
                    "segment_id": "E1S02",
                    "duration_seconds": 4,
                    "novel_text": "风吹过旷野。",
                    "image_prompt": {
                        "scene": "旷野",
                        "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"},
                    },
                    "video_prompt": {},
                    "segment_break": False,
                    "characters_in_segment": ["Alice"],
                    "scenes": ["祠堂"],
                    "props": ["玉佩"],
                    "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png"},
                },
                {
                    "segment_id": "E1S03",
                    "duration_seconds": 4,
                    "novel_text": "风吹过旷野。",
                    "image_prompt": {
                        "scene": "旷野",
                        "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"},
                    },
                    "video_prompt": {},
                    "segment_break": True,
                    "characters_in_segment": ["Alice"],
                    "scenes": ["祠堂"],
                    "props": ["玉佩"],
                    "generated_assets": {"storyboard_image": "storyboards/scene_E1S03.png"},
                },
            ],
        }

    def sync_disk(self):
        """Persist in-memory projects/scripts; manifests compare real disk projects."""

        (self.project_path / "scripts").mkdir(parents=True, exist_ok=True)
        (self.project_path / "project.json").write_text(json.dumps(self.project), encoding="utf-8")
        (self.project_path / "scripts" / "episode_1.json").write_text(json.dumps(self.script), encoding="utf-8")

    def register_storyboards(self) -> None:
        """Register persisted asset images before storyboards; unregistered artifacts fail production admission.

        Storyboard bases reference images, so absent reference claims invalidate storyboard artifacts.
        """

        self.sync_disk()
        for asset_type, bucket in (("character", "characters"), ("scene", "scenes"), ("prop", "props")):
            for name in self.project.get(bucket) or {}:
                register_current_artifact_if_provable(self.project_path, ArtifactKey.asset_sheet(asset_type, name))
        for container in ("segments", "shots", "scenes", "units"):
            for item in self.script.get(container) or []:
                unit_id = item.get("segment_id") or item.get("shot_id") or item.get("scene_id") or item.get("unit_id")
                if unit_id:
                    register_current_artifact_if_provable(
                        self.project_path,
                        ArtifactKey.episode_storyboard(1, str(unit_id)),
                    )

    def load_project(self, project_name):
        self.sync_disk()
        return self.project

    def get_project_path(self, project_name):
        return self.project_path

    def load_script(self, project_name, script_file):
        self.sync_disk()
        return self.script


def _prepare_files(tmp_path: Path) -> Path:
    project_path = tmp_path / "projects" / "demo"
    for folder in ("storyboards", "characters/refs", "scenes", "props", "products/refs", "source", "scripts"):
        (project_path / folder).mkdir(parents=True, exist_ok=True)

    (project_path / "characters" / "refs" / "Alice_ref.png").write_bytes(b"png")
    (project_path / "products" / "refs" / "保温杯_1.jpg").write_bytes(b"jpg")

    for segment_id in ("E1S01", "E1S02", "E1S03"):
        (project_path / "storyboards" / f"scene_{segment_id}.png").write_bytes(b"png")
    (project_path / "characters" / "Alice.png").write_bytes(b"png")
    (project_path / "scenes" / "祠堂.png").write_bytes(b"png")
    (project_path / "props" / "玉佩.png").write_bytes(b"png")
    return project_path


async def _noop_bucket_precheck(project, generation_type):
    return None


def _client(monkeypatch, fake_pm, fake_queue, *, register_storyboards=True, user_id="default"):
    if register_storyboards:
        fake_pm.register_storyboards()
    else:
        fake_pm.sync_disk()
    use_project_manager(monkeypatch, fake_pm)
    monkeypatch.setattr("lib.generation.generation_queue.get_generation_queue", lambda: fake_queue)
    use_generation_queue(monkeypatch, fake_queue)
    # Video-bucket preflight needs system_settings DBs; router units lack DBs, so capability behavior lives in
    # test_config_resolver/test_validators_video_bucket, keeping happy-path admission here.
    monkeypatch.setattr(generate, "require_video_bucket_capability", _noop_bucket_precheck)
    # Likewise audio preflight behavior lives in test_validators_audio_switch.
    monkeypatch.setattr(generate, "require_audio_switch_supported", _noop_bucket_precheck)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id=user_id, sub="testuser", role="admin")
    app.include_router(generate.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    # raise_server_exceptions=False: app Exception handlers form 500 responses, then
    # Starlette reraises; defaults would raise into tests instead of returning responses.
    return TestClient(app, raise_server_exceptions=False)


class TestGenerateRouter:
    def test_storyboard_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            sb = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S02",
                json={
                    "script_file": "episode_1.json",
                    "prompt": {
                        "scene": "雨夜",
                        "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
                    },
                },
            )
            assert sb.status_code == 200
            body = sb.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"
            assert "message" in body

            # Verify enqueue was called correctly
            call = fake_queue.calls[0]
            assert call["project_name"] == "demo"
            assert call["task_type"] == "storyboard"
            assert call["media_type"] == "image"
            assert call["resource_id"] == "E1S02"
            assert call["source"] == "webui"

    def test_storyboard_refuses_a_pending_image_prompt_before_enqueue(self, tmp_path, monkeypatch):
        """Formal image_prompt None returns single-entry 409 without enqueue."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][1]["image_prompt"] = None
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            response = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S02",
                json={"script_file": "episode_1.json", "prompt": "请求体里带的提示词不算数"},
            )
            assert response.status_code == 409
            assert "未命名集 · S02" in str(response.json()["detail"])
            assert fake_queue.calls == []

    def test_video_refuses_a_pending_video_prompt_before_enqueue(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["video_prompt"] = None
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            response = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "duration_seconds": 5, "prompt": "跑"},
            )
            assert response.status_code == 409
            assert "未命名集 · S01" in str(response.json()["detail"])
            assert fake_queue.calls == []

    def test_video_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "duration_seconds": 5,
                    "prompt": {
                        "action": "奔跑",
                        "camera_motion": "Static",
                        "ambiance_audio": "雨声",
                    },
                },
            )
            assert video.status_code == 200
            body = video.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"

            call = fake_queue.calls[0]
            assert call["task_type"] == "video"
            assert call["media_type"] == "video"
            assert call["payload"]["duration_seconds"] == 5

    def test_video_payload_is_identical_for_tts_and_post_production_projects(self, tmp_path, monkeypatch):
        """Narration delivery never enters video requests; queued durations/facts derive only from requests/scripts."""
        payloads = []
        for index, narration in enumerate(
            (
                {"narration_delivery": "post_production"},
                {
                    "narration_delivery": "use_tts",
                    "audio_backend": "dashscope/qwen3-tts-flash",
                    "narration_voice": "Cherry",
                },
            )
        ):
            project_path = _prepare_files(tmp_path / str(index))
            fake_pm = _FakePM(project_path)
            fake_pm.project.update(narration)
            fake_queue = _FakeQueue()
            client = _client(monkeypatch, fake_pm, fake_queue)
            with client:
                response = client.post(
                    "/api/v1/projects/demo/generate/video/E1S01",
                    json={"script_file": "episode_1.json", "duration_seconds": 5, "prompt": "跑"},
                )
            assert response.status_code == 200, response.text
            assert "narration_delivery" not in response.json()
            payloads.append(fake_queue.calls[0]["payload"])

        post_production, use_tts = payloads
        assert use_tts == post_production
        assert use_tts["duration_seconds"] == 5
        assert "narration_delivery_options" not in use_tts

    @pytest.mark.parametrize("delivery", ["post_production", "use_tts"])
    def test_video_rejects_the_retired_narration_delivery_field(self, tmp_path, monkeypatch, delivery):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            response = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "跑", "narration_delivery": delivery},
            )

        assert response.status_code == 422, response.text
        assert [(item["type"], item["loc"][-1]) for item in response.json()["detail"]] == [
            ("extra_forbidden", "narration_delivery")
        ]
        assert fake_queue.calls == []

    def test_legacy_drama_dialogue_can_enqueue_single_video(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["content_mode"] = "drama"
        fake_pm.script = {
            "episode": 1,
            "content_mode": "drama",
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "image_prompt": {
                        "scene": "旷野",
                        "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"},
                    },
                    "video_prompt": {
                        "action": "阿离转身",
                        "camera_motion": "Static",
                        "ambiance_audio": "风声",
                        "dialogue": [{"speaker": "Alice", "line": "跟紧我。"}],
                    },
                    "voiceover": [],
                    "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
                }
            ],
        }
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            response = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "阿离转身"},
            )

        assert response.status_code == 200, response.text
        assert len(fake_queue.calls) == 1

    def test_speech_free_legacy_drama_can_enqueue_single_video(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["content_mode"] = "drama"
        fake_pm.script = {
            "episode": 1,
            "content_mode": "drama",
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "image_prompt": {
                        "scene": "旷野",
                        "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"},
                    },
                    "video_prompt": {
                        "action": "阿离转身",
                        "camera_motion": "Static",
                        "ambiance_audio": "风声",
                    },
                    "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
                }
            ],
        }
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            response = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "阿离转身"},
            )

        assert response.status_code == 200, response.text
        assert len(fake_queue.calls) == 1

    def test_legacy_narration_string_prompt_can_enqueue_single_video(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["video_prompt"] = "Slow pan across the field"
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            response = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "Slow pan across the field"},
            )

        assert response.status_code == 200, response.text
        assert len(fake_queue.calls) == 1

    @pytest.mark.parametrize(
        ("content_mode", "root", "id_field", "narrator_field"),
        [
            ("narration", "segments", "segment_id", "novel_text"),
            ("ad", "shots", "shot_id", "voiceover_text"),
        ],
    )
    def test_narrator_video_request_admits_the_saved_unit_not_the_request_prompt(
        self, tmp_path, monkeypatch, content_mode, root, id_field, narrator_field
    ):
        """Workers use disk video_prompt; request-prompt dialogue never blocks enqueue when disk units contain voiceover only."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["content_mode"] = content_mode
        fake_pm.script = {
            "episode": 1,
            "content_mode": content_mode,
            root: [
                {
                    id_field: "E1S01",
                    narrator_field: "风吹过旷野。",
                    "image_prompt": {
                        "scene": "旷野",
                        "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"},
                    },
                    "video_prompt": {"action": "风吹草动", "camera_motion": "Static"},
                    "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
                }
            ],
        }
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            response = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "prompt": {
                        "action": "阿离回头",
                        "camera_motion": "Static",
                        "dialogue": [{"speaker": "阿离", "line": "快走。"}],
                    },
                },
            )

        assert response.status_code == 200, response.text
        assert len(fake_queue.calls) == 1

    @pytest.mark.parametrize(
        "case",
        [case for case in SPEECH_CONTRACT_CASES if case.generation_mode == "storyboard"],
        ids=lambda case: case.route_id,
    )
    def test_three_storyboard_web_video_entries_return_structured_speech_admission_without_enqueuing(
        self, tmp_path, monkeypatch, case: SpeechContractCase
    ):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project.update({"content_mode": case.content_mode, "generation_mode": "storyboard"})
        fake_pm.script = case.script()
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            response = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "奔跑"},
            )

        assert response.status_code == 409
        detail = response.json()["detail"]
        problem = detail["problems"][0]
        assert detail["allowed"] is False
        assert detail["unit_id"] == "E1S01"
        assert problem["code"] == "mixed_speech"
        assert [tuple(location["path"]) for location in problem["locations"]] == list(case.expected_locations)
        assert problem["reason"] == "character_and_narrator_mixed"
        assert problem["action"] == "replan_unit"
        assert fake_queue.calls == []

    def test_video_enqueue_bucket_capability_error_returns_400(self, tmp_path, monkeypatch):
        """Failed i2v preflight, such as default models lacking first frames, returns submission 400/recovery guidance without enqueue."""
        from lib.infra.api_errors import BadRequestError

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        async def _reject(project, generation_type):
            assert generation_type == "i2v"
            raise BadRequestError("video_capability_missing_i2v", provider="dashscope", model="happyhorse-1.0-r2v")

        monkeypatch.setattr(generate, "require_video_bucket_capability", _reject)

        with client:
            res = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
        assert res.status_code == 400
        assert res.json()["detail"] == i18n_message(
            "video_capability_missing_i2v",
            **make_display_names().apply({"provider": "dashscope", "model": "happyhorse-1.0-r2v"}),
        )
        assert fake_queue.calls == []

    @pytest.mark.parametrize("delivery", ["post_production", "use_tts"])
    def test_video_enqueue_rejected_when_audio_switch_unsupported(self, tmp_path, monkeypatch, delivery):
        """Always-audible models with audio-off return submission 400 without enqueue; impossible silent-crop intentions must not execute.

        Narration delivery never changes this gate; both project types share preflight.
        """
        from lib.infra.api_errors import BadRequestError

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project.update(
            {
                "video_provider_i2v": "dashscope/wan2.7-i2v",
                "video_generate_audio": False,
                "narration_delivery": delivery,
            }
        )
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        async def _reject(_project, _generation_type):
            raise BadRequestError("video_audio_switch_not_supported", provider="dashscope", model="wan2.7-i2v")

        monkeypatch.setattr(generate, "require_audio_switch_supported", _reject)

        with client:
            res = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
        assert res.status_code == 400
        assert res.json()["detail"] == i18n_message(
            "video_audio_switch_not_supported",
            **make_display_names().apply({"provider": "dashscope", "model": "wan2.7-i2v"}),
        )
        assert fake_queue.calls == []

    def test_video_enqueue_grid_mode_uses_first_frame(self, tmp_path, monkeypatch):
        """Grid modes write storyboard _first.png into generated_assets; routes must recognize it."""
        project_path = _prepare_files(tmp_path)
        (project_path / "storyboards" / "scene_E1S02_first.png").write_bytes(b"png")

        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][1]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S02_first.png"}
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S02",
                json={
                    "script_file": "episode_1.json",
                    "prompt": "宫格切片后的动作",
                },
            )
            assert video.status_code == 200, video.text
            assert video.json()["success"] is True

    def test_video_generated_assets_non_dict_is_refused_without_attribute_error(self, tmp_path, monkeypatch):
        """Nondict externally corrupted generated_assets, such as lists, reject as missing registered storyboards,
        never uncaught AttributeError from absent .get()."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = ["bad"]
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert video.status_code == 400, video.text
            assert video.json()["detail"] == i18n_message("generate_storyboard_first", segment_id="未命名集 · S01")
            assert fake_queue.calls == []

    def test_video_does_not_infer_storyboard_from_same_name_file(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = {}
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )

        assert video.status_code == 400, video.text
        assert video.json()["detail"] == i18n_message("generate_storyboard_first", segment_id="未命名集 · S01")
        assert fake_queue.calls == []

    def test_storyboard_rejects_an_unbound_script_before_enqueue(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["episodes"] = []
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            response = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == i18n_message("invalid_script_file", name="「未命名集」的剧本")
        assert fake_queue.calls == []

    def test_video_reports_an_unbound_script_before_storyboard_validation(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["episodes"] = []
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            response = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == i18n_message("invalid_script_file", name="「未命名集」的剧本")
        assert fake_queue.calls == []

    def test_video_rejects_an_explicit_but_unregistered_storyboard(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        # Disk storyboard images without manifest claims fail admission, which requires registration.
        client = _client(monkeypatch, fake_pm, fake_queue, register_storyboards=False)

        with client:
            response = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )

        assert response.status_code == 400
        assert response.json()["detail"] == i18n_message("generate_storyboard_first", segment_id="未命名集 · S01")
        assert fake_queue.calls == []

    def test_video_invalid_end_frame_has_its_own_error_message(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            response = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )

        assert response.status_code == 400
        assert response.json()["detail"] == i18n_message("invalid_end_frame_image_path", segment_id="未命名集 · S01")
        assert fake_queue.calls == []

    def test_video_storyboard_image_non_string_returns_400(self, tmp_path, monkeypatch):
        """Nonstrings in script storyboard_image produce readable 400,
        never unhandled TypeError/500 from `project_path / storyboard_rel`."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": 123}
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert video.status_code == 400, video.text
            assert video.json()["detail"] == i18n_message("invalid_storyboard_image_path", segment_id="未命名集 · S01")
            assert fake_queue.calls == []

    def test_video_storyboard_image_absolute_path_returns_400(self, tmp_path, monkeypatch):
        """Absolute storyboard_image paths produce readable 400 without unauthorized external references."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "/etc/passwd"}
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert video.status_code == 400, video.text
            assert video.json()["detail"] == i18n_message("invalid_storyboard_image_path", segment_id="未命名集 · S01")
            assert fake_queue.calls == []

    def test_video_storyboard_image_path_traversal_returns_400(self, tmp_path, monkeypatch):
        """storyboard_image `..` escaping projects produces readable 400 without external references."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "../../outside.png"}
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert video.status_code == 400, video.text
            assert video.json()["detail"] == i18n_message("invalid_storyboard_image_path", segment_id="未命名集 · S01")
            assert fake_queue.calls == []

    def test_video_dirty_script_fail_fast_400(self, tmp_path, monkeypatch):
        """Corrupted storyboard-array keys make /generate/video fail at routes with 4xx,
        never silently use default images and enqueue, which reports successful submission
        before inevitable worker script failure, breaking submission/execution expectations.

        Keep default `storyboards/scene_E1S01.png` present so line 192's generate-storyboards-first
        branch cannot hide this unexpected route.
        """
        from lib.script.script_editor import ScriptEditError

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)

        def _raise_dirty(*args, **kwargs):
            raise ScriptEditError("segments 必须是列表，当前为 NoneType")

        fake_pm.load_script = _raise_dirty
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "duration_seconds": 5,
                    "prompt": "fail fast",
                },
            )
            assert video.status_code == 400, video.text
            # Localized detail never exposes internal str(e).
            assert (
                "segments" not in video.json()["detail"]
                or "script" in video.json()["detail"].lower()
                or "kịch bản" in video.json()["detail"]
                or "损坏" in video.json()["detail"]
            )
            # Tasks not enqueued.
            assert fake_queue.calls == []

    def test_video_missing_script_fail_fast_404(self, tmp_path, monkeypatch):
        """Missing scripts FileNotFoundError makes /generate/video fail fast with 404,
        never silently default to storyboards and enqueue, reporting success
        before worker failure, breaking submission/execution expectations.

        Keep default `storyboards/scene_E1S01.png`, a legacy-grid case,
        proving existing defaults cannot silently bypass missing scripts.
        """
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        missing_script_path = project_path / "scripts" / "episode_1.json"

        def _raise_missing(*args, **kwargs):
            raise FileNotFoundError(f"剧本文件不存在: {missing_script_path}")

        fake_pm.load_script = _raise_missing
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "duration_seconds": 5,
                    "prompt": "fail fast",
                },
            )
            assert video.status_code == 404, video.text
            assert str(missing_script_path) not in video.text
            # Tasks not enqueued.
            assert fake_queue.calls == []

    def test_video_missing_segment_404(self, tmp_path, monkeypatch):
        """Absent segment_id returns /generate/video 404 rather than silently
        using default storyboards; existing defaults, such as legacy filename collisions,
        never validate/enqueue nonexistent segments.
        """
        project_path = _prepare_files(tmp_path)
        # Existing default-path collision with nonexistent segments' default filenames
        (project_path / "storyboards" / "scene_E1S99.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S99",
                json={
                    "script_file": "episode_1.json",
                    "duration_seconds": 5,
                    "prompt": "fail fast",
                },
            )
            assert video.status_code == 404, video.text
            # Tasks not enqueued.
            assert fake_queue.calls == []

    def test_character_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            character = client.post(
                "/api/v1/projects/demo/generate/character/Alice",
            )
            assert character.status_code == 200
            body = character.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"

            call = fake_queue.calls[0]
            assert call["task_type"] == "character"
            assert call["media_type"] == "image"
            assert call["resource_id"] == "Alice"
            # Descriptions come from stored entries only; enqueue carries no prompt snapshots.
            assert call["payload"] == {}

    @pytest.mark.parametrize(
        ("path", "bucket", "name"),
        [
            ("character/Alice", "characters", "Alice"),
            ("scene/祠堂", "scenes", "祠堂"),
            ("prop/玉佩", "props", "玉佩"),
            ("product/保温杯", "products", "保温杯"),
        ],
    )
    def test_asset_without_a_stored_description_is_refused_before_enqueue(
        self, tmp_path, monkeypatch, path, bucket, name
    ):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project[bucket][name]["description"] = "  "
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(f"/api/v1/projects/demo/generate/{path}", headers={"Accept-Language": "zh"})

        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == f"资产「{name}」还没有填写描述，无法生成资产图"
        assert fake_queue.calls == []

    def test_character_enqueue_resolves_nfd_registered_key(self, tmp_path, monkeypatch):
        """Path parameters may differ from stored keys: NFC registration still allows NFD generation,
        enqueueing actual persisted resource_id keys without new encoding variants."""
        import unicodedata

        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        name_nfd = unicodedata.normalize("NFD", "Hiếu")

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"] = {name_nfd: {"description": "存量 NFD 角色"}}
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                f"/api/v1/projects/demo/generate/character/{name_nfc}",
            )
            assert resp.status_code == 200, resp.text
            assert fake_queue.calls[0]["resource_id"] == name_nfd

    def test_scene_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            scene = client.post(
                "/api/v1/projects/demo/generate/scene/祠堂",
            )
            assert scene.status_code == 200
            body = scene.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"

            call = fake_queue.calls[0]
            assert call["task_type"] == "scene"
            assert call["media_type"] == "image"
            assert call["resource_id"] == "祠堂"

    def test_prop_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            prop = client.post(
                "/api/v1/projects/demo/generate/prop/玉佩",
            )
            assert prop.status_code == 200
            body = prop.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"

            call = fake_queue.calls[0]
            assert call["task_type"] == "prop"
            assert call["media_type"] == "image"
            assert call["resource_id"] == "玉佩"

    def test_product_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            product = client.post(
                "/api/v1/projects/demo/generate/product/保温杯",
            )
            assert product.status_code == 200
            body = product.json()
            assert body["success"] is True

            call = fake_queue.calls[0]
            assert call["task_type"] == "product"
            assert call["media_type"] == "image"
            assert call["resource_id"] == "保温杯"

    def test_product_enqueue_unknown_product_404(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/product/不存在",
            )
            assert resp.status_code == 404
            assert fake_queue.calls == []

    def test_error_paths(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            # Bad storyboard prompt (structured but missing scene)
            bad_prompt = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S02",
                json={"script_file": "episode_1.json", "prompt": {"composition": {}}},
            )
            assert bad_prompt.status_code == 400

            # Nonexistent segment
            not_found = client.post(
                "/api/v1/projects/demo/generate/storyboard/MISSING",
                json={"script_file": "episode_1.json", "prompt": "test"},
            )
            assert not_found.status_code == 404

            # Video without storyboard
            (project_path / "storyboards" / "scene_E1S01.png").unlink()
            no_storyboard = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "text"},
            )
            assert no_storyboard.status_code == 400

            # Bad video prompt
            bad_video_prompt = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": {"action": ""}},
            )
            assert bad_video_prompt.status_code in (400, 500)

            # Empty string prompt for storyboard route (segment exists, prompt is empty str)
            empty_storyboard_prompt = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S02",
                json={"script_file": "episode_1.json", "prompt": ""},
            )
            assert empty_storyboard_prompt.status_code == 400

            # Whitespace-only string prompt for video route — ensure storyboard exists first
            # so we hit the prompt check, not the missing-storyboard check
            (project_path / "storyboards" / "scene_E1S02.png").write_bytes(b"png")
            empty_video_prompt = client.post(
                "/api/v1/projects/demo/generate/video/E1S02",
                json={"script_file": "episode_1.json", "prompt": "   "},
            )
            assert empty_video_prompt.status_code == 400

            # Missing character
            fake_pm.project["characters"] = {}
            missing_char = client.post(
                "/api/v1/projects/demo/generate/character/Alice",
            )
            assert missing_char.status_code == 404

            # Missing scene
            fake_pm.project["scenes"] = {}
            missing_scene = client.post(
                "/api/v1/projects/demo/generate/scene/祠堂",
            )
            assert missing_scene.status_code == 404

            # Missing prop
            fake_pm.project["props"] = {}
            missing_prop = client.post(
                "/api/v1/projects/demo/generate/prop/玉佩",
            )
            assert missing_prop.status_code == 404


class TestUnexpectedErrorMapsTo500:
    """Unexpected exceptions → generic 500 without internal detail leaks.

    Monkeypatch earliest get_project_manager calls (storyboard/video/tts inside _sync;
    character/scene/prop/product through _enqueue_asset_generation _sync)
    to raise RuntimeError, bypassing FileNotFoundError/ScriptEditError handlers
    into app Exception handlers. Assert 500 without response sentinels.
    """

    def _client_with_leak(self, monkeypatch, sentinel: str) -> TestClient:
        def _boom():
            raise RuntimeError(sentinel)

        monkeypatch.setattr(generate, "get_project_manager", _boom)
        app = FastAPI()
        register_error_handlers(app)
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(generate.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        return TestClient(app, raise_server_exceptions=False)

    def test_storyboard_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_storyboard")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert resp.status_code == 500
            assert "LEAK_storyboard" not in resp.text

    def test_video_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_video")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert resp.status_code == 500
            assert "LEAK_video" not in resp.text

    def test_tts_segment_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_tts_segment")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/tts/E1S01",
                json={"script_file": "episode_1.json"},
            )
            assert resp.status_code == 500
            assert "LEAK_tts_segment" not in resp.text

    def test_tts_batch_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_tts_batch")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/tts",
                json={"script_file": "episode_1.json"},
            )
            assert resp.status_code == 500
            assert "LEAK_tts_batch" not in resp.text

    def test_character_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_character")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/character/Alice",
            )
            assert resp.status_code == 500
            assert "LEAK_character" not in resp.text

    def test_scene_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_scene")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/scene/祠堂",
            )
            assert resp.status_code == 500
            assert "LEAK_scene" not in resp.text

    def test_prop_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_prop")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/prop/玉佩",
            )
            assert resp.status_code == 500
            assert "LEAK_prop" not in resp.text

    def test_product_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_product")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/product/保温杯",
            )
            assert resp.status_code == 500
            assert "LEAK_product" not in resp.text


class TestVideoRouteGate:
    """Individual video endpoints follow project generation modes, rejecting reference video at submission."""

    def _reference_pm(self, project_path: Path, *, storyboard_script: bool = False) -> _FakePM:
        """Reference-video projects.

        True ``storyboard_script`` retains ``_FakePM`` storyboard-family defaults, simulating legacy
        scripts/images inconsistent with current generation modes.
        """
        fake_pm = _FakePM(project_path)
        fake_pm.project["generation_mode"] = "reference_video"
        if not storyboard_script:
            fake_pm.script = {
                "episode": 1,
                "content_mode": "narration",
                "video_units": [{"unit_id": "E1U01", "prompt": "镜头1：奔跑"}],
            }
        return fake_pm

    def test_reference_route_rejected_with_route_guidance(self, tmp_path, monkeypatch):
        """Reference video rejects with reference-video workflow guidance, never generate-storyboards-first."""
        project_path = _prepare_files(tmp_path)
        fake_pm = self._reference_pm(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/video/E1U01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
        assert resp.status_code == 409
        assert resp.json()["detail"] == i18n_message("video_route_is_reference_video")
        assert fake_queue.calls == []

    def test_reference_route_rejected_even_with_leftover_storyboard(self, tmp_path, monkeypatch):
        """Residual storyboards never alter decisions: project.json modes alone determine routes, not disk artifacts."""
        project_path = _prepare_files(tmp_path)
        # _prepare_files writes storyboard artifact scene_E1S01.png.
        fake_pm = self._reference_pm(project_path, storyboard_script=True)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
        assert resp.status_code == 409
        assert resp.json()["detail"] == i18n_message("video_route_is_reference_video")
        assert fake_queue.calls == []

    def test_storyboard_route_enqueues_with_i2v_bucket(self, tmp_path, monkeypatch):
        """Storyboard image-to-video behavior unchanged; bucket preflight remains i2v."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["generation_mode"] = "storyboard"
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        seen: list[str] = []

        async def _record(project, generation_type):
            seen.append(generation_type)

        monkeypatch.setattr(generate, "require_video_bucket_capability", _record)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
        assert resp.status_code == 200
        assert seen == ["i2v"]
        assert fake_queue.calls[0]["task_type"] == "video"


class TestReferenceAdmissionAtGenerationEntries:
    """Storyboard/image-to-video submission rejects missing references by name without tasks or charges."""

    @pytest.mark.parametrize("endpoint", ["storyboard", "video"], ids=["分镜图", "图生视频"])
    def test_unregistered_reference_blocks_and_names_it(self, tmp_path, monkeypatch, endpoint: str):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][1]["characters_in_segment"] = ["Alice", "李四"]
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                f"/api/v1/projects/demo/generate/{endpoint}/E1S02",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )

        assert resp.status_code == 400
        assert resp.json()["detail"] == i18n_message("reference_asset_unregistered", missing_text="李四")
        assert fake_queue.calls == []

    @pytest.mark.parametrize("endpoint", ["storyboard", "video"], ids=["分镜图", "图生视频"])
    def test_deleted_asset_leaves_a_blocking_reference(self, tmp_path, monkeypatch, endpoint: str):
        """Deleted-asset references share never-registered-name recovery, never silent omission."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        del fake_pm.project["scenes"]["祠堂"]
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                f"/api/v1/projects/demo/generate/{endpoint}/E1S02",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )

        assert resp.status_code == 400
        assert resp.json()["detail"] == i18n_message("reference_asset_unregistered", missing_text="祠堂")
        assert fake_queue.calls == []

    @pytest.mark.parametrize("endpoint", ["storyboard", "video"], ids=["分镜图", "图生视频"])
    def test_character_without_sheet_blocks_even_with_an_original(self, tmp_path, monkeypatch, endpoint: str):
        """Originals only feed asset-image generation; they never substitute missing asset images for admission."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["Alice"]["character_sheet"] = ""
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                f"/api/v1/projects/demo/generate/{endpoint}/E1S02",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )

        assert resp.status_code == 400
        assert resp.json()["detail"] == i18n_message("reference_asset_missing", missing_text="character: Alice")
        assert fake_queue.calls == []

    def test_product_without_sheet_still_enqueues(self, tmp_path, monkeypatch):
        """Product originals anchor fidelity acceptance; missing sheets never block (ADR 0034)."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["products_in_shot"] = ["保温杯"]
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )

        assert resp.status_code == 200
        assert fake_queue.calls[0]["resource_id"] == "E1S01"


class TestAdStoryboardRegeneration:
    """Flat shots[] ad scripts retain existing storyboard generation/regeneration endpoints, shared after manual review."""

    def _ad_pm(self, project_path: Path) -> _FakePM:
        fake_pm = _FakePM(project_path)
        fake_pm.project["content_mode"] = "ad"
        fake_pm.script = {
            "episode": 1,
            "content_mode": "ad",
            "shots": [
                {
                    "shot_id": "E1S01",
                    "section": "product_reveal",
                    "duration_seconds": 4,
                    "voiceover_text": "商品亮相",
                    "characters_in_shot": [],
                    "scenes": [],
                    "props": [],
                    "products_in_shot": ["保温杯"],
                    "image_prompt": {
                        "scene": "旷野",
                        "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"},
                    },
                    "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
                },
            ],
        }
        return fake_pm

    def test_ad_shot_storyboard_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = self._ad_pm(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={"script_file": "episode_1.json", "prompt": "商品特写"},
            )
            assert resp.status_code == 200
            assert resp.json()["success"] is True
            call = fake_queue.calls[0]
            assert call["task_type"] == "storyboard"
            assert call["resource_id"] == "E1S01"

    def test_ad_shot_not_found_is_404(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = self._ad_pm(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E9S99",
                json={"script_file": "episode_1.json", "prompt": "商品特写"},
            )
            assert resp.status_code == 404


class TestNoServerPathLeak:
    """404/400/500 response detail excludes absolute server-path fragments.

    Library FileNotFoundError messages contain absolute paths, such as load_script
    missing-script /abs/path; app handlers redact to generic localized 404.
    """

    _PATH_SENTINELS = ("/Users", "/var", "/private", "\\Users")

    def _assert_no_path(self, resp) -> None:
        detail = resp.json()["detail"]
        assert isinstance(detail, str)
        assert detail
        for sentinel in self._PATH_SENTINELS:
            assert sentinel not in detail, f"detail 泄露路径片段 {sentinel!r}: {detail}"

    def _client_project_missing(self, tmp_path, monkeypatch) -> TestClient:
        """load_project raises absolute-path FileNotFoundError, triggered first by every endpoint."""
        fake_pm = _FakePM(tmp_path / "projects" / "demo")

        def _raise(*args, **kwargs):
            raise FileNotFoundError(f"项目元数据文件不存在: {tmp_path / 'projects' / 'demo' / 'project.json'}")

        fake_pm.load_project = _raise
        return _client(monkeypatch, fake_pm, _FakeQueue())

    def test_file_not_found_404_hides_path_for_all_endpoints(self, tmp_path, monkeypatch):
        client = self._client_project_missing(tmp_path, monkeypatch)
        requests = [
            ("/api/v1/projects/demo/generate/storyboard/E1S01", {"script_file": "episode_1.json", "prompt": "x"}),
            ("/api/v1/projects/demo/generate/video/E1S01", {"script_file": "episode_1.json", "prompt": "x"}),
            ("/api/v1/projects/demo/generate/tts/E1S01", {"script_file": "episode_1.json"}),
            ("/api/v1/projects/demo/generate/tts", {"script_file": "episode_1.json"}),
            ("/api/v1/projects/demo/generate/character/Alice", {"prompt": "x"}),
            ("/api/v1/projects/demo/generate/scene/祠堂", {"prompt": "x"}),
            ("/api/v1/projects/demo/generate/prop/玉佩", {"prompt": "x"}),
            ("/api/v1/projects/demo/generate/product/保温杯", {"prompt": "x"}),
        ]
        with client:
            for url, payload in requests:
                resp = client.post(url, json=payload)
                assert resp.status_code == 404, f"{url}: {resp.status_code} {resp.text}"
                assert "/Users" not in resp.text, f"{url} 泄露路径: {resp.text}"
                self._assert_no_path(resp)

    def test_script_file_not_found_404_hides_path(self, tmp_path, monkeypatch):
        """load_script FileNotFoundError is the original absolute-path leak source."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)

        def _raise(*args, **kwargs):
            raise FileNotFoundError(f"剧本文件不存在: {project_path / 'scripts' / 'episode_1.json'}")

        fake_pm.load_script = _raise
        client = _client(monkeypatch, fake_pm, _FakeQueue())

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert resp.status_code == 404, resp.text
            self._assert_no_path(resp)

    def test_bad_prompt_400_shape(self, tmp_path, monkeypatch):
        """TaskSpecValidationError maps through app handlers to localized path-free 400."""
        project_path = _prepare_files(tmp_path)
        client = _client(monkeypatch, _FakePM(project_path), _FakeQueue())

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S02",
                json={"script_file": "episode_1.json", "prompt": ""},
            )
            assert resp.status_code == 400, resp.text
            self._assert_no_path(resp)

    def test_unexpected_error_500_hides_path(self, tmp_path, monkeypatch):
        """Unexpected path-bearing errors still yield generic 500 text."""
        fake_pm = _FakePM(tmp_path / "projects" / "demo")

        def _raise(*args, **kwargs):
            raise RuntimeError(f"boom at {tmp_path / 'projects' / 'demo' / 'project.json'}")

        fake_pm.load_project = _raise
        client = _client(monkeypatch, fake_pm, _FakeQueue())

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert resp.status_code == 500, resp.text
            assert "boom" not in resp.text
            self._assert_no_path(resp)


class _FakeDedupeHitQueue(_FakeQueue):
    """Simulate dedupe hits returning existing task rows, never new tasks."""

    async def enqueue_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"task_id": "task-existing", "deduped": True, "existing_task_id": "task-existing"}


class TestDedupedPassthrough:
    """Enqueue responses expose queue deduped facts so frontend identifies no-new-task submissions."""

    def test_fresh_enqueue_reports_deduped_false(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        client = _client(monkeypatch, _FakePM(project_path), _FakeQueue())

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "prompt": {"scene": "雨夜", "composition": {"shot_type": "Medium Shot"}},
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["deduped"] is False

    def test_dedupe_hit_reports_deduped_true_with_existing_task_id(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        client = _client(monkeypatch, _FakePM(project_path), _FakeDedupeHitQueue())

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "prompt": {"scene": "雨夜", "composition": {"shot_type": "Medium Shot"}},
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["deduped"] is True
            assert body["task_id"] == "task-existing"

    def test_asset_generation_exposes_deduped(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        client = _client(monkeypatch, _FakePM(project_path), _FakeDedupeHitQueue())

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/character/Alice",
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["deduped"] is True
