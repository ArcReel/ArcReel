from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import lib.gemini_client as gemini_client_module
from server.routers import system_config as system_config_router


@pytest.fixture()
def env_guard():
    keys = [
        "GEMINI_IMAGE_BACKEND",
        "GEMINI_VIDEO_BACKEND",
        "GEMINI_BACKEND",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_IMAGE_MODEL",
        "GEMINI_VIDEO_MODEL",
        "GEMINI_VIDEO_GENERATE_AUDIO",
        "GEMINI_IMAGE_RPM",
        "GEMINI_VIDEO_RPM",
        "GEMINI_REQUEST_GAP",
        "STORYBOARD_MAX_WORKERS",
        "VIDEO_MAX_WORKERS",
    ]
    snapshot = {k: os.environ.get(k) for k in keys}
    gemini_client_module._shared_rate_limiter = None
    yield
    for key, value in snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    gemini_client_module._shared_rate_limiter = None


class _FakeWorker:
    def __init__(self):
        self.reload_calls = 0

    def reload_limits_from_env(self) -> None:
        self.reload_calls += 1


def _client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(system_config_router, "PROJECT_ROOT", tmp_path)
    app = FastAPI()
    app.include_router(system_config_router.router, prefix="/api/v1")
    app.state.generation_worker = _FakeWorker()
    return TestClient(app)


class TestSystemConfigRouter:
    def test_get_returns_options_from_cost_calculator(self, tmp_path, monkeypatch, env_guard):
        client = _client(tmp_path, monkeypatch)
        with client:
            res = client.get("/api/v1/system/config")
            assert res.status_code == 200
            payload = res.json()
            assert "config" in payload
            assert payload["options"]["image_models"] == list(
                system_config_router.cost_calculator.IMAGE_COST.keys()
            )
            assert payload["options"]["video_models"] == list(
                system_config_router.cost_calculator.VIDEO_COST.keys()
            )

    def test_patch_validates_models_and_refreshes_rate_limiter(self, tmp_path, monkeypatch, env_guard):
        client = _client(tmp_path, monkeypatch)
        with client:
            bad = client.patch(
                "/api/v1/system/config",
                json={"image_model": "not-a-model"},
            )
            assert bad.status_code == 400

            ok = client.patch(
                "/api/v1/system/config",
                json={
                    "image_model": "gemini-3-pro-image-preview",
                    "gemini_image_rpm": 12,
                },
            )
            assert ok.status_code == 200

            limiter = gemini_client_module.get_shared_rate_limiter()
            assert limiter.limits["gemini-3-pro-image-preview"] == 12

    def test_vertex_credentials_upload_and_backend_validation(self, tmp_path, monkeypatch, env_guard):
        client = _client(tmp_path, monkeypatch)
        with client:
            missing = client.patch(
                "/api/v1/system/config",
                json={"video_backend": "vertex"},
            )
            assert missing.status_code == 400

            payload = {"project_id": "demo-project", "type": "service_account"}
            upload = client.post(
                "/api/v1/system/config/vertex-credentials",
                files={"file": ("vertex_credentials.json", json.dumps(payload), "application/json")},
            )
            assert upload.status_code == 200
            assert upload.json()["config"]["vertex_credentials"]["is_set"] is True
            assert upload.json()["config"]["vertex_credentials"]["project_id"] == "demo-project"

            ok = client.patch(
                "/api/v1/system/config",
                json={"video_backend": "vertex"},
            )
            assert ok.status_code == 200
            assert ok.json()["config"]["video_backend"] == "vertex"

    def test_audio_toggle_effective_only_on_vertex(self, tmp_path, monkeypatch, env_guard):
        client = _client(tmp_path, monkeypatch)
        with client:
            # Store a "disabled" choice while on AI Studio.
            res = client.patch(
                "/api/v1/system/config",
                json={"video_backend": "aistudio", "video_generate_audio": False},
            )
            assert res.status_code == 200
            cfg = res.json()["config"]
            assert cfg["video_backend"] == "aistudio"
            assert cfg["video_generate_audio"] is False
            assert cfg["video_generate_audio_editable"] is False
            assert cfg["video_generate_audio_effective"] is True

            # Upload creds, then switch to Vertex - stored preference becomes effective.
            payload = {"project_id": "demo-project", "type": "service_account"}
            upload = client.post(
                "/api/v1/system/config/vertex-credentials",
                files={"file": ("vertex_credentials.json", json.dumps(payload), "application/json")},
            )
            assert upload.status_code == 200

            res2 = client.patch(
                "/api/v1/system/config",
                json={"video_backend": "vertex"},
            )
            assert res2.status_code == 200
            cfg2 = res2.json()["config"]
            assert cfg2["video_backend"] == "vertex"
            assert cfg2["video_generate_audio_editable"] is True
            assert cfg2["video_generate_audio_effective"] is False

    def test_patch_triggers_worker_reload(self, tmp_path, monkeypatch, env_guard):
        client = _client(tmp_path, monkeypatch)
        with client:
            app = client.app
            worker = app.state.generation_worker
            assert worker.reload_calls == 0

            res = client.patch(
                "/api/v1/system/config",
                json={"video_max_workers": 5},
            )
            assert res.status_code == 200
            assert worker.reload_calls == 1

    def test_secrets_are_masked_in_response(self, tmp_path, monkeypatch, env_guard):
        client = _client(tmp_path, monkeypatch)
        with client:
            secret = "AIza-test-secret-123456"
            res = client.patch(
                "/api/v1/system/config",
                json={"gemini_api_key": secret},
            )
            assert res.status_code == 200
            cfg = res.json()["config"]
            assert cfg["gemini_api_key"]["is_set"] is True
            assert secret not in json.dumps(cfg)
            assert cfg["gemini_api_key"]["masked"] is not None

