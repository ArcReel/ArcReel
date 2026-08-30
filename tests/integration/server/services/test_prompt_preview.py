"""Tests for prompt_preview."""

from pathlib import Path

import pytest

from server.services import generation_tasks, prompt_preview
from tests.integration.server.services.generation_tasks_support import (
    FakeGenerator,
    _ad_pm,
    _async_return,
    _fake_resolve_ctx,
    _FakePM,
    _prepare_files,
    _register_asset_sheet_claims,
    _seed_current_storyboard,
)

ITEM_ID = "E1S02"

STRUCTURED_IMAGE_PROMPT = {
    "scene": "在雨夜街道",
    "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
}
STRUCTURED_VIDEO_PROMPT = {
    "action": "撑伞走过",
    "camera_motion": "Static",
    "ambiance_audio": "雨声",
}


def _drama_pm(project_path: Path) -> _FakePM:
    """drama 项目：条目携带有序 utterances，角色资产带非空 voice_style。"""
    pm = _FakePM(project_path)
    pm.project["content_mode"] = "drama"
    pm.project["characters"]["Alice"]["voice_style"] = "低沉沙哑"
    pm.script = {
        "episode": 1,
        "content_mode": "drama",
        "scenes": [
            {
                "scene_id": ITEM_ID,
                "duration_seconds": 4,
                "segment_break": False,
                "characters_in_scene": ["Alice"],
                "scenes": ["祠堂"],
                "props": [],
                "image_prompt": STRUCTURED_IMAGE_PROMPT,
                "video_prompt": STRUCTURED_VIDEO_PROMPT,
                "utterances": [
                    {"kind": "voiceover", "speaker": None, "text": "那是命运的开端。"},
                    {"kind": "dialogue", "speaker": "Alice", "text": "你来了。"},
                ],
            }
        ],
    }
    _register_asset_sheet_claims(pm)
    return pm


def _pm_for(content_mode: str, project_path: Path) -> _FakePM:
    if content_mode == "drama":
        return _drama_pm(project_path)
    if content_mode == "ad":
        (project_path / "products" / "保温杯.png").write_bytes(b"png")
        pm = _ad_pm(project_path, with_sheet=True)
        shot = pm.script["shots"][1]
        shot["image_prompt"] = STRUCTURED_IMAGE_PROMPT
        shot["video_prompt"] = STRUCTURED_VIDEO_PROMPT
        return pm
    pm = _FakePM(project_path)
    _register_asset_sheet_claims(pm)
    segment = pm.script["segments"][1]
    segment["image_prompt"] = STRUCTURED_IMAGE_PROMPT
    segment["video_prompt"] = {**STRUCTURED_VIDEO_PROMPT, "dialogue": [{"speaker": "Alice", "line": "hello"}]}
    segment["novel_text"] = "旁白正文"
    return pm


def _item_of(pm: _FakePM) -> dict:
    items, id_field, _kind = generation_tasks.resolve_items(pm.script)
    return next(item for item in items if str(item.get(id_field)) == ITEM_ID)


def _patch_execution(monkeypatch, pm: _FakePM, generator: FakeGenerator, *, register_artifacts: bool = False) -> None:
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(generator))
    monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
    monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)
    if not register_artifacts:
        monkeypatch.setattr(generation_tasks, "register_current_resource_artifact", lambda *_a, **_kw: True)
    monkeypatch.setattr(prompt_preview, "get_project_manager", lambda: pm)


CONTENT_MODES = ["narration", "drama", "ad"]


class TestPreviewMatchesExecution:
    """预览文本与执行期实际发出的提示词逐字一致。"""

    @pytest.mark.parametrize("content_mode", CONTENT_MODES)
    async def test_storyboard_image_prompt_is_verbatim(self, tmp_path, monkeypatch, content_mode):
        project_path = _prepare_files(tmp_path)
        pm = _pm_for(content_mode, project_path)
        generator = FakeGenerator()
        _patch_execution(monkeypatch, pm, generator)

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)
        await generation_tasks.execute_storyboard_task(
            "demo", ITEM_ID, {"script_file": "episode_1.json", "prompt": STRUCTURED_IMAGE_PROMPT}
        )

        assert preview.storyboard_image.text == generator.image_calls[0]["prompt"]

    @pytest.mark.parametrize("content_mode", CONTENT_MODES)
    async def test_video_prompt_is_verbatim(self, tmp_path, monkeypatch, content_mode):
        project_path = _prepare_files(tmp_path)
        pm = _pm_for(content_mode, project_path)
        (project_path / "storyboards" / f"scene_{ITEM_ID}.png").write_bytes(b"png")
        _seed_current_storyboard(pm, ITEM_ID)
        generator = FakeGenerator()
        _patch_execution(monkeypatch, pm, generator)

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)
        await generation_tasks.execute_video_task("demo", ITEM_ID, {"script_file": "episode_1.json"})

        assert preview.video.text == generator.video_calls[0]["prompt"]

    async def test_drama_text_form_video_prompt_keeps_utterance_speech(self, tmp_path, monkeypatch):
        """文本形态不承载台词：发声序列仍由 utterances 决定，渲染层照常注入。"""
        project_path = _prepare_files(tmp_path)
        pm = _drama_pm(project_path)
        _item_of(pm)["video_prompt"] = "镜头缓缓推近，雨水顺着伞沿滑落"
        (project_path / "storyboards" / f"scene_{ITEM_ID}.png").write_bytes(b"png")
        _seed_current_storyboard(pm, ITEM_ID)
        generator = FakeGenerator()
        _patch_execution(monkeypatch, pm, generator)

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)
        await generation_tasks.execute_video_task("demo", ITEM_ID, {"script_file": "episode_1.json"})

        text = preview.video.text
        assert text is not None
        assert preview.video.is_text_form
        assert text.startswith("镜头缓缓推近，雨水顺着伞沿滑落")
        assert text.count("Line: 你来了。") == 1
        assert text.count("Voice_Style: 低沉沙哑") == 1
        assert "那是命运的开端。" not in text
        assert text == generator.video_calls[0]["prompt"]

    @pytest.mark.parametrize("content_mode", CONTENT_MODES)
    async def test_text_form_image_prompt_is_the_prompt_body(self, tmp_path, monkeypatch, content_mode):
        """文本形态的分镜图提示词就是提示词主体，渲染层只注入项目风格与反向约束。"""
        body = "一条被雨水打湿的老街，霓虹反射在积水上"
        project_path = _prepare_files(tmp_path)
        pm = _pm_for(content_mode, project_path)
        _item_of(pm)["image_prompt"] = body
        generator = FakeGenerator()
        _patch_execution(monkeypatch, pm, generator)

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)
        await generation_tasks.execute_storyboard_task(
            "demo", ITEM_ID, {"script_file": "episode_1.json", "prompt": body}
        )

        text = preview.storyboard_image.text
        assert text is not None
        assert preview.storyboard_image.is_text_form
        assert text.startswith(f"Style: Anime\nVisual style: cinematic\n\n{body}")
        assert text == generator.image_calls[0]["prompt"]

    @pytest.mark.parametrize("content_mode", CONTENT_MODES)
    async def test_text_form_video_prompt_is_the_prompt_body(self, tmp_path, monkeypatch, content_mode):
        """视频侧同理：文本形态正文领先，逐字等于执行期实发文本。"""
        body = "镜头缓缓推近，雨水顺着伞沿滑落"
        project_path = _prepare_files(tmp_path)
        pm = _pm_for(content_mode, project_path)
        _item_of(pm)["video_prompt"] = body
        (project_path / "storyboards" / f"scene_{ITEM_ID}.png").write_bytes(b"png")
        _seed_current_storyboard(pm, ITEM_ID)
        generator = FakeGenerator()
        _patch_execution(monkeypatch, pm, generator)

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)
        await generation_tasks.execute_video_task("demo", ITEM_ID, {"script_file": "episode_1.json"})

        text = preview.video.text
        assert text is not None
        assert preview.video.is_text_form
        assert text.startswith(body)
        assert text == generator.video_calls[0]["prompt"]

    async def test_rendering_a_preview_text_again_does_not_duplicate_injections(self, tmp_path, monkeypatch):
        """结构化 → 文本以当前渲染结果为初值：再渲染一次不叠出第二份风格与反向约束。"""
        project_path = _prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        generator = FakeGenerator()
        _patch_execution(monkeypatch, pm, generator)

        structured = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)
        seed = structured.storyboard_image.text
        assert seed is not None
        _item_of(pm)["image_prompt"] = seed
        as_text = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)

        assert as_text.storyboard_image.text == seed

    @pytest.mark.parametrize("content_mode", CONTENT_MODES)
    async def test_rendering_a_preview_video_text_again_does_not_duplicate_injections(
        self, tmp_path, monkeypatch, content_mode
    ):
        """视频侧同理：drama 的 Voice_Profiles / Dialogue 声明段已在正文里时不再追加第二份。"""
        project_path = _prepare_files(tmp_path)
        pm = _pm_for(content_mode, project_path)
        _patch_execution(monkeypatch, pm, FakeGenerator())

        structured = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)
        seed = structured.video.text
        assert seed is not None
        _item_of(pm)["video_prompt"] = seed
        as_text = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)

        assert as_text.video.text == seed


class TestPreviewIsReadOnly:
    async def test_preview_calls_no_provider_and_writes_no_manifest(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        pm = _pm_for("ad", project_path)
        (project_path / "storyboards" / f"scene_{ITEM_ID}.png").write_bytes(b"png")
        _seed_current_storyboard(pm, ITEM_ID)
        generator = FakeGenerator()
        _patch_execution(monkeypatch, pm, generator)
        manifest = project_path / ".arcreel_artifacts.json"
        manifest_before = manifest.read_bytes()

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)

        assert preview.storyboard_image.text
        assert preview.video.text
        assert generator.image_calls == []
        assert generator.video_calls == []
        assert manifest.read_bytes() == manifest_before


class TestPreviewUnavailableSides:
    async def test_missing_prompt_reports_that_side_only(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        _item_of(pm).pop("video_prompt")
        _patch_execution(monkeypatch, pm, FakeGenerator())

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)

        assert preview.video.text is None
        assert preview.video.unavailable == prompt_preview.UNAVAILABLE_MISSING
        assert preview.storyboard_image.text

    async def test_malformed_prompt_reports_invalid(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        _item_of(pm)["image_prompt"] = {"composition": {}}
        _patch_execution(monkeypatch, pm, FakeGenerator())

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)

        assert preview.storyboard_image.text is None
        assert preview.storyboard_image.unavailable == prompt_preview.UNAVAILABLE_INVALID

    async def test_unknown_item_id_is_reported(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        _patch_execution(monkeypatch, pm, FakeGenerator())

        with pytest.raises(prompt_preview.ScriptItemNotFound):
            await prompt_preview.preview_item_prompts("demo", "episode_1.json", "E9S99")


class TestPromptPreviewTool:
    """MCP 工具层是服务层的薄包装：同一份预览，两个 host 同一入口。"""

    def _services(self, pm: _FakePM):
        from typing import Any, cast

        from server.tool_runtime import Services

        return Services(projects=cast(Any, pm), workflow_planner=cast(Any, None), capabilities=cast(Any, None))

    def _scope(self, project_path: Path):
        from server.tool_runtime import ProjectScope

        return ProjectScope(project_name="demo", projects_root=project_path.parent)

    async def _call(self, pm: _FakePM, project_path: Path, **kwargs):
        from server.tool_runtime import CallerContext, PromptPreviewRequest, ToolRequest, get_prompt_preview

        request = PromptPreviewRequest(**{"script": "episode_1.json", "item_id": ITEM_ID, **kwargs})
        return await get_prompt_preview(
            ToolRequest(request),
            self._scope(project_path),
            CallerContext(user_id="default", source="mcp"),
            self._services(pm),
        )

    async def test_returns_the_same_preview_as_the_service(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        pm = _pm_for("drama", project_path)
        _patch_execution(monkeypatch, pm, FakeGenerator())

        outcome = await self._call(pm, project_path)

        assert outcome.problem is None
        assert outcome.value == await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)

    async def test_unknown_item_is_a_typed_problem(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        _patch_execution(monkeypatch, pm, FakeGenerator())

        outcome = await self._call(pm, project_path, item_id="E9S99")

        assert outcome.value is None
        assert outcome.problem is not None
        assert outcome.problem.code == "item_not_found"

    async def test_script_must_be_a_bare_filename(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        _patch_execution(monkeypatch, pm, FakeGenerator())

        outcome = await self._call(pm, project_path, script="../other/episode_1.json")

        assert outcome.problem is not None
        assert outcome.problem.code == "invalid_request"

    def test_registered_in_the_tool_catalogue(self):
        """内嵌 host 的工具目录；远程 host 的等价由 test_remote_mcp 的工具集相等断言兜住。"""
        from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

        assert "get_prompt_preview" in ARCREEL_MCP_TOOL_IDS
