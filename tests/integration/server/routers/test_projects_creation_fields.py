"""Tests for projects_creation_fields (split from test_projects_router.py)."""

import pytest

from tests.integration.server.routers.projects_router_support import (
    _client,
    _FakePM,
)


class TestProjectsRouter:
    def test_get_project_includes_asset_fingerprints(self, tmp_path, monkeypatch):
        """项目 API 应返回 asset_fingerprints 字段"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

        with client:
            resp = client.get("/api/v1/projects/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert "asset_fingerprints" in data
            assert "storyboards/scene_E1S01.png" in data["asset_fingerprints"]
            assert isinstance(data["asset_fingerprints"]["storyboards/scene_E1S01.png"], int)

    def test_create_project_with_style_template_id_expands_prompt(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

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
        client = _client(monkeypatch, fake_pm)

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
        client = _client(monkeypatch, fake_pm)

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

    def test_create_project_with_image_default_layer(self, tmp_path, monkeypatch):
        """项目默认图片模型（default_image_backend）可在创建时写入，不必配桶。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

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

    def test_patch_image_default_layer_set_and_clear(self, tmp_path, monkeypatch):
        """项目默认图片模型可设置 / 清除；格式非法与非图片模型均 400。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)
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
            # 校验在写盘闭包内抛出，若 router 的兜底分支不透传领域异常会退化成 500
            assert rejected.json()["diagnostic"] == "field: default_image_backend"

            wrong_media = client.patch(
                "/api/v1/projects/ready",
                json={"default_image_backend": "gemini-aistudio/veo-3.1-generate-preview"},
            )
            assert wrong_media.status_code == 400

    def test_patch_text_tier_fields_set_and_clear(self, tmp_path, monkeypatch):
        """项目级档位 / 默认模型三字段可设置；空值 = 清除、继承全局。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)
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

            # 非法 backend 值被 400 拒绝
            rejected = client.patch(
                "/api/v1/projects/ready",
                json={"text_backend_complex": "no-slash"},
            )
            assert rejected.status_code == 400

    def test_video_bucket_fields_create_patch_and_clear(self, tmp_path, monkeypatch):
        """项目级视频桶键（video_provider_i2v/r2v）可创建时写入、PATCH 设置；空值 = 清除、回退默认层。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

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

    def test_video_bucket_field_rejects_non_video_model(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

        with client:
            rejected = client.patch(
                "/api/v1/projects/ready",
                json={"video_provider_i2v": "gemini-aistudio/gemini-3.1-flash-image-preview"},
            )
            assert rejected.status_code == 400

    def test_create_project_ignores_legacy_image_backend(self, tmp_path, monkeypatch):
        """退役的 image_backend 字段已从写模型移除，传入时被静默忽略。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

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
            # 关键保证：退役字段不得落进 project.json，否则解析链会忽略它、静默错配供应商
            assert "image_backend" not in fake_pm.project_data["legacy-1"]

    def test_create_project_empty_model_fields_not_written(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

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
        """创建时可选填口播语速估算：区间内落盘，未填不落盘（回退语言默认）。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

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
        client = _client(monkeypatch, fake_pm)

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
        """JSON 布尔不得被 Pydantic 折成 1.0 / 0.0 混进语速覆盖，两个取值都应 422 且不建目录。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

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
        """非法 backend 字符串应被校验器拒绝。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "title": "Bad Backend",
                    "name": "bad-bk",
                    "video_backend": "garbage",  # 无 "/"，且不在 PROVIDER_REGISTRY
                },
            )
            assert resp.status_code == 400
