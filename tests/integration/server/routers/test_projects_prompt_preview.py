"""Tests for projects_prompt_preview."""

from server.routers import projects
from server.services.prompt_preview import (
    UNAVAILABLE_MISSING,
    ItemPromptPreview,
    RenderedPrompt,
    ScriptItemNotFound,
)
from tests.integration.server.routers.projects_router_support import _client, _FakePM


class TestPromptPreviewEndpoint:
    def _pm(self, tmp_path) -> _FakePM:
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "episode_1.json")] = {
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 4}],
        }
        return fake_pm

    def test_returns_both_sides_with_unavailable_reason_rendered(self, tmp_path, monkeypatch):
        """两侧各自作答：可渲染的给文本，不可渲染的给已翻译成品文案，不整体失败。"""

        async def _preview(project_name: str, script_file: str, item_id: str) -> ItemPromptPreview:
            assert (project_name, script_file, item_id) == ("ready", "episode_1.json", "E1S01")
            return ItemPromptPreview(
                item_id=item_id,
                content_mode="narration",
                storyboard_image=RenderedPrompt(text="Style: Anime\n\n最终提示词", is_text_form=True),
                video=RenderedPrompt(unavailable=UNAVAILABLE_MISSING),
            )

        monkeypatch.setattr(projects, "preview_item_prompts", _preview)
        client = _client(monkeypatch, self._pm(tmp_path))

        with client:
            response = client.get(
                "/api/v1/projects/ready/script-items/E1S01/prompt-preview",
                params={"script_file": "episode_1.json"},
                headers={"Accept-Language": "zh"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["content_mode"] == "narration"
        assert body["storyboard_image"] == {
            "text": "Style: Anime\n\n最终提示词",
            "unavailable": None,
            "is_text_form": True,
        }
        assert body["video"]["text"] is None
        # 不可用原因是后端按请求语言渲染的成品文案，不把裸 key 推给前端
        assert body["video"]["unavailable"] == "该分镜还没有填写提示词"

    def test_unknown_item_is_404(self, tmp_path, monkeypatch):
        async def _preview(project_name: str, script_file: str, item_id: str) -> ItemPromptPreview:
            raise ScriptItemNotFound(item_id)

        monkeypatch.setattr(projects, "preview_item_prompts", _preview)
        client = _client(monkeypatch, self._pm(tmp_path))

        with client:
            response = client.get(
                "/api/v1/projects/ready/script-items/E9S99/prompt-preview",
                params={"script_file": "episode_1.json"},
            )

        assert response.status_code == 404

    def test_script_file_is_required(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, self._pm(tmp_path))

        with client:
            response = client.get("/api/v1/projects/ready/script-items/E1S01/prompt-preview")

        assert response.status_code == 422
