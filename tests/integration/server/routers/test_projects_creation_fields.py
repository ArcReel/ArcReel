"""Tests for projects_creation_fields."""

import pytest

from tests.integration.server.routers.projects_router_support import (
    _client,
    _FakePM,
)


class TestProjectsRouter:
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

    def test_create_project_persists_episode_target_duration(self, tmp_path, monkeypatch):
        """单集目标时长在创建向导写入；越界值 422 且不留下半成品项目。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

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

    def test_create_requires_binary_generation_mode(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            # 缺失 generation_mode：必填无默认值，不被悄悄锁进某种生成模式
            missing = client.post(
                "/api/v1/projects",
                json={"name": "no-mode", "title": "X", "content_mode": "narration"},
            )
            assert missing.status_code == 422

            # 旧三值 grid 不再是合法创建值
            legacy_grid = client.post(
                "/api/v1/projects",
                json={"name": "old-grid", "title": "X", "content_mode": "narration", "generation_mode": "grid"},
            )
            assert legacy_grid.status_code == 422

            # 两种生成模式均可创建
            for mode in ("storyboard", "reference_video"):
                created = client.post(
                    "/api/v1/projects",
                    json={"name": f"m-{mode.replace('_', '-')}", "title": "X", "generation_mode": mode},
                )
                assert created.status_code == 200, created.text
                assert created.json()["project"]["generation_mode"] == mode

    def test_create_persists_grid_storyboard(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            # 缺省 false 也落盘为显式值
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
