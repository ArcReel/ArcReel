"""Tests for projects_update_fields (split from test_projects_router.py)."""

import pytest

from tests.integration.server.routers.conftest import (
    _client,
    _FakePM,
)


class TestProjectsRouter:
    def test_update_project_with_style_template_id_expands_and_clears_image(self, tmp_path, monkeypatch):
        """PATCH style_template_id：写入 id + 展开 prompt 到 style，并清掉 style_image/description。"""
        fake_pm = _FakePM(tmp_path)
        # 预置一个带参考图的项目
        fake_pm.project_data["ready"]["style_image"] = "style_reference.png"
        fake_pm.project_data["ready"]["style_description"] = "old desc"

        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"style_template_id": "live_zhang_yimou"},
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert data["style_template_id"] == "live_zhang_yimou"
            assert "张艺谋" in data["style"]
            assert "style_image" not in data
            assert "style_description" not in data

    def test_update_project_with_unknown_template_id_returns_400(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"style_template_id": "no_such_template"},
            )
            assert resp.status_code == 400

    def test_update_project_clear_style_template(self, tmp_path, monkeypatch):
        """PATCH style_template_id=null：同时清掉 id 与派生的 style 长文本。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["style_template_id"] = "live_premium_drama"
        fake_pm.project_data["ready"]["style"] = "画风：真人电视剧风格，精品短剧画风，大师级构图"

        client = _client(monkeypatch, fake_pm)
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
        """PATCH clear_style_image=true：清掉 style_image 与 style_description。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["style_image"] = "style_reference.png"
        fake_pm.project_data["ready"]["style_description"] = "some desc"

        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"clear_style_image": True},
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert "style_image" not in data
            assert "style_description" not in data

    def test_update_project_persists_narration_overrides(self, tmp_path, monkeypatch):
        """PATCH 旁白配音项目级覆盖：audio_backend / narration_voice / narration_speed 写入 project.json。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={
                    "audio_backend": "dashscope/qwen3-tts-flash",
                    "narration_voice": "Cherry",
                    "narration_speed": 1.2,
                },
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert data["audio_backend"] == "dashscope/qwen3-tts-flash"
            assert data["narration_voice"] == "Cherry"
            assert data["narration_speed"] == 1.2

    def test_update_project_clears_narration_overrides(self, tmp_path, monkeypatch):
        """PATCH 空值/null：旁白配音覆盖回落全局默认（从 project.json 移除）。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["audio_backend"] = "dashscope/qwen3-tts-flash"
        fake_pm.project_data["ready"]["narration_voice"] = "Cherry"
        fake_pm.project_data["ready"]["narration_speed"] = 1.2

        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"audio_backend": None, "narration_voice": "", "narration_speed": None},
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert "audio_backend" not in data
            assert "narration_voice" not in data
            assert "narration_speed" not in data

            # 纯空白音色值同样按清除处理（后端 .strip() 判空），防重构回退“空白即清除”语义
            fake_pm.project_data["ready"]["narration_voice"] = "Cherry"
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"narration_voice": "   "},
            )
            assert resp.status_code == 200
            assert "narration_voice" not in fake_pm.project_data["ready"]

    def test_update_project_rejects_non_positive_narration_speed(self, tmp_path, monkeypatch):
        """语速 0/负数应 422，且不写回 project.json。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"narration_speed": 0})
            assert resp.status_code == 422
            assert "narration_speed" not in fake_pm.project_data["ready"]

    def test_update_project_persists_and_clears_speech_rate(self, tmp_path, monkeypatch):
        """PATCH 口播语速估算：区间内写入 project.json，null 清除回落语言默认。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"speech_rate_units_per_second": 6.5})
            assert resp.status_code == 200
            assert fake_pm.project_data["ready"]["speech_rate_units_per_second"] == 6.5

            resp = client.patch("/api/v1/projects/ready", json={"speech_rate_units_per_second": None})
            assert resp.status_code == 200
            assert "speech_rate_units_per_second" not in fake_pm.project_data["ready"]

    @pytest.mark.parametrize("bad", [0, -1, 20.5, 1000])
    def test_update_project_rejects_out_of_range_speech_rate(self, tmp_path, monkeypatch, bad):
        """口播语速估算越界（≤0 或 >20）应 422，且不写回 project.json。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"speech_rate_units_per_second": bad})
            assert resp.status_code == 422
            assert "speech_rate_units_per_second" not in fake_pm.project_data["ready"]

    @pytest.mark.parametrize("value", [True, False])
    def test_update_project_rejects_boolean_speech_rate(self, tmp_path, monkeypatch, value):
        """PATCH 同样拒布尔：否则 true 会作为 1.0 落盘、false 被当成未填静默跳过。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"speech_rate_units_per_second": value})
            assert resp.status_code == 422
            assert "speech_rate_units_per_second" not in fake_pm.project_data["ready"]

    def test_update_project_rejects_invalid_audio_backend(self, tmp_path, monkeypatch):
        """audio_backend 非法 provider 应 400（复用 backend 格式校验）。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"audio_backend": "garbage"})
            assert resp.status_code == 400
