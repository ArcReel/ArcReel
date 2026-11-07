"""Tests for projects_update_fields."""

import pytest

from tests.integration.server.routers.projects_router_support import (
    _FakePM,
    build_projects_client,
)


class TestProjectsRouter:
    def test_update_project_with_style_template_id_expands_and_clears_image(self, tmp_path, monkeypatch):
        """PATCH style_template_id writes IDs/expanded style prompts, clearing style_image/description."""
        fake_pm = _FakePM(tmp_path)
        # Preload projects with reference images.
        fake_pm.project_data["ready"]["style_image"] = "style_reference.png"
        fake_pm.project_data["ready"]["style_description"] = "old desc"

        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"style_template_id": "live_zhang_yimou"},
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert data["style_template_id"] == "live_zhang_yimou"
            assert "Zhang Yimou" in data["style"]
            assert "style_image" not in data
            assert "style_description" not in data

    def test_update_project_with_unknown_template_id_returns_400(self, tmp_path, monkeypatch):
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"style_template_id": "no_such_template"},
            )
            assert resp.status_code == 400

    def test_update_project_clear_style_template(self, tmp_path, monkeypatch):
        """PATCH style_template_id=null clears IDs/derived long style text together."""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["style_template_id"] = "live_premium_drama"
        fake_pm.project_data["ready"]["style"] = "画风：真人电视剧风格，精品短剧画风，大师级构图"

        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"style_template_id": None},
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert "style_template_id" not in data
            assert data["style"] == ""

    def test_update_project_clear_style_image(self, tmp_path, monkeypatch):
        """PATCH clear_style_image=true clears style_image/style_description."""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["style_image"] = "style_reference.png"
        fake_pm.project_data["ready"]["style_description"] = "some desc"

        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"clear_style_image": True},
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert "style_image" not in data
            assert "style_description" not in data

    def test_update_project_switches_to_tts_with_snapshot(self, tmp_path, monkeypatch):
        """PATCH switches to TTS and persists model/voice/rate snapshots."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={
                    "narration_delivery": "use_tts",
                    "audio_backend": "dashscope/qwen3-tts-flash",
                    "narration_voice": "Cherry",
                    "narration_speed": 1.2,
                },
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert data["narration_delivery"] == "use_tts"
            assert data["audio_backend"] == "dashscope/qwen3-tts-flash"
            assert data["narration_voice"] == "Cherry"
            assert data["narration_speed"] == 1.2

    def test_switching_to_post_production_keeps_tts_snapshot(self, tmp_path, monkeypatch):
        """Switching postproduction changes delivery only, retaining snapshots and recomputable existing narration bases."""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"].update(
            narration_delivery="use_tts",
            audio_backend="dashscope/qwen3-tts-flash",
            narration_voice="Cherry",
            narration_speed=1.2,
        )
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"narration_delivery": "post_production"})
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert data["narration_delivery"] == "post_production"
            assert data["audio_backend"] == "dashscope/qwen3-tts-flash"
            assert data["narration_voice"] == "Cherry"
            assert data["narration_speed"] == 1.2

    @pytest.mark.parametrize(
        "patch",
        [
            {"narration_delivery": "use_tts"},
            {"narration_delivery": "use_tts", "audio_backend": "dashscope", "narration_voice": "Cherry"},
            {"narration_delivery": "use_tts", "audio_backend": "dashscope/qwen3-tts-flash"},
            {"narration_delivery": None},
        ],
    )
    def test_update_project_rejects_incomplete_tts_config(self, tmp_path, monkeypatch, patch):
        """TTS requires complete snapshots/nonempty delivery; rejection leaves entire PATCH unpersisted."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json=patch)
            assert resp.status_code == 422
            assert "narration_delivery" not in fake_pm.project_data["ready"]
            assert "audio_backend" not in fake_pm.project_data["ready"]

    def test_tts_project_cannot_clear_model_or_voice(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"].update(
            narration_delivery="use_tts",
            audio_backend="dashscope/qwen3-tts-flash",
            narration_voice="Cherry",
        )
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            assert client.patch("/api/v1/projects/ready", json={"audio_backend": None}).status_code == 422
            assert client.patch("/api/v1/projects/ready", json={"narration_voice": "  "}).status_code == 422
            assert fake_pm.project_data["ready"]["audio_backend"] == "dashscope/qwen3-tts-flash"
            assert fake_pm.project_data["ready"]["narration_voice"] == "Cherry"

    def test_update_project_clears_tts_speed(self, tmp_path, monkeypatch):
        """Rate null omits provider rates/removes snapshot values without global fallback."""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"].update(
            narration_delivery="use_tts",
            audio_backend="dashscope/qwen3-tts-flash",
            narration_voice="Cherry",
            narration_speed=1.2,
        )
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"narration_speed": None})
            assert resp.status_code == 200
            assert "narration_speed" not in fake_pm.project_data["ready"]

    def test_update_project_persists_character_voice_binding(self, tmp_path, monkeypatch):
        """PATCH voice-binding modes accepts enums; null removes project.json overrides for defaults."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"character_voice_binding": "reference_audio"})
            assert resp.status_code == 200
            assert fake_pm.project_data["ready"]["character_voice_binding"] == "reference_audio"

            resp = client.patch("/api/v1/projects/ready", json={"character_voice_binding": None})
            assert resp.status_code == 200
            assert "character_voice_binding" not in fake_pm.project_data["ready"]

    def test_update_project_rejects_unknown_character_voice_binding(self, tmp_path, monkeypatch):
        """Invalid enums return 422 without project.json writes."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"character_voice_binding": "native"})
            assert resp.status_code == 422
            assert "character_voice_binding" not in fake_pm.project_data["ready"]

    def test_update_project_rejects_non_positive_narration_speed(self, tmp_path, monkeypatch):
        """Zero/negative rates return 422 without project.json writes."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"narration_speed": 0})
            assert resp.status_code == 422
            assert "narration_speed" not in fake_pm.project_data["ready"]

    def test_update_project_persists_and_clears_speech_rate(self, tmp_path, monkeypatch):
        """PATCH estimated rates persists valid ranges; null clears to language defaults."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"speech_rate_units_per_second": 6.5})
            assert resp.status_code == 200
            assert fake_pm.project_data["ready"]["speech_rate_units_per_second"] == 6.5

            resp = client.patch("/api/v1/projects/ready", json={"speech_rate_units_per_second": None})
            assert resp.status_code == 200
            assert "speech_rate_units_per_second" not in fake_pm.project_data["ready"]

    @pytest.mark.parametrize("bad", [0, -1, 20.5, 1000])
    def test_update_project_rejects_out_of_range_speech_rate(self, tmp_path, monkeypatch, bad):
        """Estimated rates ≤0 or >20 return 422 without project.json writes."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"speech_rate_units_per_second": bad})
            assert resp.status_code == 422
            assert "speech_rate_units_per_second" not in fake_pm.project_data["ready"]

    @pytest.mark.parametrize("value", [True, False])
    def test_update_project_rejects_boolean_speech_rate(self, tmp_path, monkeypatch, value):
        """PATCH likewise rejects booleans, preventing true→1.0 persistence or false silently treated as unset."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"speech_rate_units_per_second": value})
            assert resp.status_code == 422
            assert "speech_rate_units_per_second" not in fake_pm.project_data["ready"]

    def test_update_project_persists_and_clears_episode_target_duration(self, tmp_path, monkeypatch):
        """PATCH episode targets persists valid ranges; null clears to no target."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"episode_target_duration": 120})
            assert resp.status_code == 200
            assert fake_pm.project_data["ready"]["episode_target_duration"] == 120

            resp = client.patch("/api/v1/projects/ready", json={"episode_target_duration": None})
            assert resp.status_code == 200
            assert "episode_target_duration" not in fake_pm.project_data["ready"]

    @pytest.mark.parametrize("bad", [5, 900, 0, -30])
    def test_update_project_rejects_out_of_range_episode_target_duration(self, tmp_path, monkeypatch, bad):
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"episode_target_duration": bad})
            assert resp.status_code == 422
            assert "episode_target_duration" not in fake_pm.project_data["ready"]

    def test_update_project_rejects_invalid_audio_backend(self, tmp_path, monkeypatch):
        """Invalid TTS providers return 400 through shared backend-format checks."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"audio_backend": "garbage"})
            assert resp.status_code == 400

    def test_update_project_clear_style_combined(self, tmp_path, monkeypatch):
        """Clear all styles together: style_template_id=null + clear_style_image=true."""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["style_template_id"] = "live_premium_drama"
        fake_pm.project_data["ready"]["style"] = "画风：..."
        fake_pm.project_data["ready"]["style_image"] = "style_reference.png"
        fake_pm.project_data["ready"]["style_description"] = "some desc"

        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"style_template_id": None, "clear_style_image": True},
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert "style_template_id" not in data
            assert data["style"] == ""
            assert "style_image" not in data
            assert "style_description" not in data

    def test_patch_image_default_layer_set_and_clear(self, tmp_path, monkeypatch):
        """Default image models set/clear; invalid formats/nonimage models return 400."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            updated = client.patch(
                "/api/v1/projects/ready",
                json={"default_image_backend": "gemini-aistudio/nano-banana"},
            )
            assert updated.status_code == 200
            assert fake_pm.project_data["ready"]["default_image_backend"] == "gemini-aistudio/nano-banana"

            cleared = client.patch("/api/v1/projects/ready", json={"default_image_backend": ""})
            assert cleared.status_code == 200
            assert "default_image_backend" not in fake_pm.project_data["ready"]

            rejected = client.patch("/api/v1/projects/ready", json={"default_image_backend": "no-slash"})
            assert rejected.status_code == 400
            # Validation raises inside persistence closures; router fallbacks must forward domain errors rather than degrade to 500.
            assert rejected.json()["diagnostic"] == "field: default_image_backend"

            wrong_media = client.patch(
                "/api/v1/projects/ready",
                json={"default_image_backend": "gemini-aistudio/veo-3.1-generate-preview"},
            )
            assert wrong_media.status_code == 400

    def test_patch_text_tier_fields_set_and_clear(self, tmp_path, monkeypatch):
        """Three project tier/default-model fields support setting; empty clears/inherits globals."""
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            updated = client.patch(
                "/api/v1/projects/ready",
                json={
                    "text_backend_simple": "gemini-aistudio/gemini-3-flash-preview",
                    "text_backend_complex": "gemini-aistudio/gemini-3.1-pro-preview",
                    "default_text_backend": "gemini-aistudio/gemini-3-flash-preview",
                },
            )
            assert updated.status_code == 200
            data = fake_pm.project_data["ready"]
            assert data["text_backend_simple"] == "gemini-aistudio/gemini-3-flash-preview"
            assert data["text_backend_complex"] == "gemini-aistudio/gemini-3.1-pro-preview"
            assert data["default_text_backend"] == "gemini-aistudio/gemini-3-flash-preview"

            cleared = client.patch(
                "/api/v1/projects/ready",
                json={"text_backend_simple": "", "text_backend_complex": "", "default_text_backend": ""},
            )
            assert cleared.status_code == 200
            data = fake_pm.project_data["ready"]
            assert "text_backend_simple" not in data
            assert "text_backend_complex" not in data
            assert "default_text_backend" not in data

            # Invalid backend returns 400.
            rejected = client.patch(
                "/api/v1/projects/ready",
                json={"text_backend_complex": "no-slash"},
            )
            assert rejected.status_code == 400

    def test_video_bucket_field_rejects_non_video_model(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)

        with client:
            rejected = client.patch(
                "/api/v1/projects/ready",
                json={"video_provider_i2v": "gemini-aistudio/gemini-3.1-flash-image"},
            )
            assert rejected.status_code == 400

    def test_patch_toggles_grid_storyboard_but_not_route(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["generation_mode"] = "storyboard"
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            # Grid switches remain freely toggleable after creation.
            on = client.patch("/api/v1/projects/ready", json={"grid_storyboard": True})
            assert on.status_code == 200
            assert fake_pm.project_data["ready"]["grid_storyboard"] is True

            off = client.patch("/api/v1/projects/ready", json={"grid_storyboard": False})
            assert off.status_code == 200
            assert fake_pm.project_data["ready"]["grid_storyboard"] is False

            # Creation fixes generation modes; project PATCH structurally omits generation_mode, silently discarding submissions without writes.
            route = client.patch("/api/v1/projects/ready", json={"generation_mode": "reference_video"})
            assert route.status_code == 200
            assert fake_pm.project_data["ready"]["generation_mode"] == "storyboard"
