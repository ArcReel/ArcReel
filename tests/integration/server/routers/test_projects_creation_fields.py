"""Tests for projects_creation_fields."""

import asyncio

import pytest

from lib.config.service import ConfigService
from tests.integration.server.routers.projects_router_support import (
    _FakePM,
    build_projects_client,
)


class TestProjectsRouter:
    def test_create_project_with_style_template_id_expands_prompt(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "title": "模版项目",
                    "name": "tpl-1",
                    "style_template_id": "live_premium_drama",
                    "content_mode": "drama",
                    "aspect_ratio": "9:16",
                },
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["tpl-1"]
            assert data["style_template_id"] == "live_premium_drama"
            assert "真人电视剧" in data["style"] or "精品短剧" in data["style"]

    def test_create_project_with_unknown_template_id_returns_400(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "title": "坏模版",
                    "name": "bad-1",
                    "style_template_id": "no_such",
                },
            )
            assert resp.status_code == 400

    def test_create_project_with_model_fields_persists(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "title": "模型项目",
                    "name": "m-1",
                    "video_backend": "gemini-aistudio/veo-3",
                    "image_provider_t2i": "gemini-aistudio/nano-banana",
                    "text_backend_simple": "gemini-aistudio/gemini-2.5",
                    "text_backend_complex": "gemini-aistudio/gemini-2.5-pro",
                    "default_text_backend": "gemini-aistudio/gemini-2.5",
                    "default_duration": 8,
                },
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["m-1"]
            assert data["video_backend"] == "gemini-aistudio/veo-3"
            assert data["image_provider_t2i"] == "gemini-aistudio/nano-banana"
            assert data["text_backend_simple"] == "gemini-aistudio/gemini-2.5"
            assert data["text_backend_complex"] == "gemini-aistudio/gemini-2.5-pro"
            assert data["default_text_backend"] == "gemini-aistudio/gemini-2.5"
            assert data["default_duration"] == 8

    def test_create_project_persists_episode_target_duration(self, tmp_path, monkeypatch):
        """Wizard creation writes episode targets; invalid ranges return 422 without partial projects."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "name": "etd-ok",
                    "title": "目标时长",
                    "episode_target_duration": 120,
                },
            )
            assert resp.status_code == 200
            assert fake_pm.project_data["etd-ok"]["episode_target_duration"] == 120

            rejected = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "name": "etd-bad",
                    "title": "越界",
                    "episode_target_duration": 5,
                },
            )
            assert rejected.status_code == 422
            assert "etd-bad" not in fake_pm.project_data

    def test_create_project_with_image_default_layer(self, tmp_path, monkeypatch):
        """Creation may set default_image_backend without bucket configuration."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "title": "只配默认",
                    "name": "img-default",
                    "default_image_backend": "gemini-aistudio/nano-banana",
                },
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["img-default"]
            assert data["default_image_backend"] == "gemini-aistudio/nano-banana"
            assert "image_provider_t2i" not in data
            assert "image_provider_i2i" not in data

    def test_video_bucket_fields_create_patch_and_clear(self, tmp_path, monkeypatch):
        """Project video_provider_i2v/r2v buckets support creation/PATCH; empty clears and falls back to defaults."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            created = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "title": "视频桶项目",
                    "name": "vb-1",
                    "video_provider_i2v": "minimax/MiniMax-Hailuo-2.3",
                    "video_provider_r2v": "minimax/S2V-01",
                },
            )
            assert created.status_code == 200
            data = fake_pm.project_data["vb-1"]
            assert data["video_provider_i2v"] == "minimax/MiniMax-Hailuo-2.3"
            assert data["video_provider_r2v"] == "minimax/S2V-01"

            updated = client.patch(
                "/api/v1/projects/ready",
                json={"video_provider_r2v": "openai/sora-2"},
            )
            assert updated.status_code == 200
            assert fake_pm.project_data["ready"]["video_provider_r2v"] == "openai/sora-2"

            cleared = client.patch(
                "/api/v1/projects/ready",
                json={"video_provider_r2v": ""},
            )
            assert cleared.status_code == 200
            assert "video_provider_r2v" not in fake_pm.project_data["ready"]

    def test_create_project_ignores_legacy_image_backend(self, tmp_path, monkeypatch):
        """Retired image_backend is removed from write models and silently ignored on submission."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "title": "旧字段项目",
                    "name": "legacy-1",
                    "image_backend": "gemini-aistudio/nano-banana",
                },
            )
            assert resp.status_code == 200
            # Retired fields must never persist to project.json, where ignored resolution would silently select wrong providers.
            assert "image_backend" not in fake_pm.project_data["legacy-1"]

    def test_create_project_empty_model_fields_not_written(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "title": "空字段项目",
                    "name": "e-1",
                    "video_backend": "",
                    "image_backend": None,
                },
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["e-1"]
            assert "video_backend" not in data
            assert "image_backend" not in data

    def test_create_project_persists_speech_rate(self, tmp_path, monkeypatch):
        """Creation optionally sets estimated speaking rates: valid ranges persist, omission stays unpersisted with language fallback."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "title": "语速项目",
                    "name": "sr-1",
                    "speech_rate_units_per_second": 6.5,
                },
            )
            assert resp.status_code == 200
            assert fake_pm.project_data["sr-1"]["speech_rate_units_per_second"] == 6.5

            resp = client.post(
                "/api/v1/projects",
                json={"generation_mode": "storyboard", "title": "默认语速", "name": "sr-2"},
            )
            assert resp.status_code == 200
            assert "speech_rate_units_per_second" not in fake_pm.project_data["sr-2"]

    @pytest.mark.parametrize("bad", [0, -1, 20.5])
    def test_create_project_rejects_out_of_range_speech_rate(self, tmp_path, monkeypatch, bad):
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "title": "越界语速",
                    "name": "sr-bad",
                    "speech_rate_units_per_second": bad,
                },
            )
            assert resp.status_code == 422

    @pytest.mark.parametrize("value", [True, False])
    def test_create_project_rejects_boolean_speech_rate(self, tmp_path, monkeypatch, value):
        """Pydantic must not coerce JSON booleans into 1.0/0.0 rate overrides; both return 422 without directories."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "title": "布尔语速",
                    "name": "sr-bool",
                    "speech_rate_units_per_second": value,
                },
            )
            assert resp.status_code == 422
            assert "sr-bool" not in fake_pm.project_data

    def test_create_project_with_invalid_backend_returns_400(self, tmp_path, monkeypatch):
        """Invalid backend strings fail validators."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "title": "Bad Backend",
                    "name": "bad-bk",
                    "video_backend": "garbage",  # No / and absent from PROVIDER_REGISTRY
                },
            )
            assert resp.status_code == 400

    def test_create_requires_binary_generation_mode(self, tmp_path, monkeypatch):
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            # generation_mode required without defaults; missing values never silently fix generation modes.
            missing = client.post(
                "/api/v1/projects",
                json={"name": "no-mode", "title": "X", "content_mode": "narration"},
            )
            assert missing.status_code == 422

            # Legacy third grid value no longer creates valid projects.
            legacy_grid = client.post(
                "/api/v1/projects",
                json={"name": "old-grid", "title": "X", "content_mode": "narration", "generation_mode": "grid"},
            )
            assert legacy_grid.status_code == 422

            # Both generation modes create projects.
            for mode in ("storyboard", "reference_video"):
                created = client.post(
                    "/api/v1/projects",
                    json={"name": f"m-{mode.replace('_', '-')}", "title": "X", "generation_mode": mode},
                )
                assert created.status_code == 200, created.text
                assert created.json()["project"]["generation_mode"] == mode

    def test_create_persists_grid_storyboard(self, tmp_path, monkeypatch):
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            # Default false also persists explicitly.
            default_off = client.post(
                "/api/v1/projects",
                json={"name": "grid-off", "title": "X", "generation_mode": "storyboard"},
            )
            assert default_off.status_code == 200
            assert default_off.json()["project"]["grid_storyboard"] is False

            enabled = client.post(
                "/api/v1/projects",
                json={"name": "grid-on", "title": "X", "generation_mode": "storyboard", "grid_storyboard": True},
            )
            assert enabled.status_code == 200
            assert enabled.json()["project"]["grid_storyboard"] is True


class TestProjectCreationNarrationDelivery:
    """Narration delivery is required project configuration: default postproduction dubbing; TTS snapshots prefill global defaults."""

    def _create(self, client, **fields):
        return client.post(
            "/api/v1/projects",
            json={"generation_mode": "storyboard", "title": "旁白", "name": "n-1", **fields},
        )

    def test_defaults_to_post_production(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        with build_projects_client(monkeypatch, fake_pm) as client:
            assert self._create(client).status_code == 200
        data = fake_pm.project_data["n-1"]
        assert data["narration_delivery"] == "post_production"
        assert "audio_backend" not in data

    def test_tts_prefills_snapshot_from_global_defaults(self, tmp_path, monkeypatch, db_factory):
        async def _seed():
            async with db_factory() as session:
                svc = ConfigService(session)
                await svc.set_setting("default_audio_backend", "dashscope/qwen3-tts-flash")
                await svc.set_setting("narration_voice", "Ethan")
                await svc.set_setting("narration_speed", "1.1")
                await session.commit()

        asyncio.run(_seed())
        fake_pm = _FakePM(tmp_path)
        with build_projects_client(monkeypatch, fake_pm, session_factory=db_factory) as client:
            resp = self._create(client, narration_delivery="use_tts", narration_voice="Cherry")
            assert resp.status_code == 200
        data = fake_pm.project_data["n-1"]
        assert data["narration_delivery"] == "use_tts"
        assert data["audio_backend"] == "dashscope/qwen3-tts-flash"
        assert data["narration_voice"] == "Cherry"
        assert data["narration_speed"] == 1.1

    def test_tts_without_model_is_rejected(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        with build_projects_client(monkeypatch, fake_pm) as client:
            resp = self._create(
                client,
                narration_delivery="use_tts",
                audio_backend=None,
                narration_voice="Cherry",
                narration_speed=None,
            )
            assert resp.status_code == 422
        assert "n-1" not in fake_pm.project_data
