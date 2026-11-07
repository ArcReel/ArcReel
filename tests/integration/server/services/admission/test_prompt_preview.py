"""Tests for prompt_preview."""

from pathlib import Path

import pytest

from lib.infra.api_errors import BadRequestError
from server.services.admission import prompt_preview
from server.services.tasks import generation_tasks
from tests.fakes import use_generation_context, use_project_manager, use_video_thumbnail_extractor
from tests.integration.server.services.tasks.generation_tasks_support import (
    FakeGenerator,
    _FakePM,
    ad_pm,
    async_return,
    fake_resolve_ctx,
    pm_with_eight_references,
    prepare_files,
    register_asset_sheet_claims,
    seed_current_storyboard,
    skip_artifact_registration,
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
    """Drama fixture: entries have ordered utterances and character assets have nonempty voice_style."""
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
    register_asset_sheet_claims(pm)
    return pm


def _pm_for(content_mode: str, project_path: Path) -> _FakePM:
    if content_mode == "drama":
        return _drama_pm(project_path)
    if content_mode == "ad":
        (project_path / "products" / "保温杯.png").write_bytes(b"png")
        pm = ad_pm(project_path, with_sheet=True)
        shot = pm.script["shots"][1]
        shot["image_prompt"] = STRUCTURED_IMAGE_PROMPT
        shot["video_prompt"] = STRUCTURED_VIDEO_PROMPT
        return pm
    pm = _FakePM(project_path)
    register_asset_sheet_claims(pm)
    segment = pm.script["segments"][1]
    segment["image_prompt"] = STRUCTURED_IMAGE_PROMPT
    segment["video_prompt"] = {**STRUCTURED_VIDEO_PROMPT, "dialogue": [{"speaker": "Alice", "line": "hello"}]}
    segment["novel_text"] = "旁白正文"
    return pm


def _item_of(pm: _FakePM) -> dict:
    items, id_field, _kind = generation_tasks.resolve_items(pm.script)
    return next(item for item in items if str(item.get(id_field)) == ITEM_ID)


def _patch_execution(monkeypatch, pm: _FakePM, generator: FakeGenerator, *, register_artifacts: bool = False) -> None:
    use_project_manager(monkeypatch, pm)
    use_generation_context(monkeypatch, fake_resolve_ctx(generator))
    use_video_thumbnail_extractor(monkeypatch, async_return(None))
    if not register_artifacts:
        skip_artifact_registration(monkeypatch)
    monkeypatch.setattr(prompt_preview, "get_project_manager", lambda: pm)


CONTENT_MODES = ["narration", "drama", "ad"]


class TestPreviewMatchesExecution:
    """Preview text equals actual outgoing execution prompts verbatim."""

    @pytest.mark.parametrize("content_mode", CONTENT_MODES)
    async def test_storyboard_image_prompt_is_verbatim(self, tmp_path, monkeypatch, content_mode):
        project_path = prepare_files(tmp_path)
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
        project_path = prepare_files(tmp_path)
        pm = _pm_for(content_mode, project_path)
        (project_path / "storyboards" / f"scene_{ITEM_ID}.png").write_bytes(b"png")
        seed_current_storyboard(pm, ITEM_ID)
        generator = FakeGenerator()
        _patch_execution(monkeypatch, pm, generator)

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)
        await generation_tasks.execute_video_task("demo", ITEM_ID, {"script_file": "episode_1.json"})

        assert preview.video.text == generator.video_calls[0]["prompt"]

    async def test_drama_text_form_video_prompt_keeps_utterance_speech(self, tmp_path, monkeypatch):
        """Plain text carries no dialogue itself; utterances still determine speech sequences and rendering injects them normally."""
        project_path = prepare_files(tmp_path)
        pm = _drama_pm(project_path)
        _item_of(pm)["video_prompt"] = "镜头缓缓推近，雨水顺着伞沿滑落"
        (project_path / "storyboards" / f"scene_{ITEM_ID}.png").write_bytes(b"png")
        seed_current_storyboard(pm, ITEM_ID)
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
        """Plain-text storyboard prompts are the body; rendering injects only project style and negative restrictions."""
        body = "一条被雨水打湿的老街，霓虹反射在积水上"
        project_path = prepare_files(tmp_path)
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
        assert text.startswith("Style: Anime\nVisual style: cinematic\nReference_Images: '图1")
        assert f"\n\n{body}\n\nAvoid: watermarks, extra text, logos" in text
        assert text == generator.image_calls[0]["prompt"]

    async def test_reference_numbering_covers_the_previous_storyboard_and_mentions(self, tmp_path, monkeypatch):
        """Preview and execution share reference assembly: previous storyboard image is last; body @[登记名] becomes corresponding 图N."""
        project_path = prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        seed_current_storyboard(pm, "E1S01")
        _item_of(pm)["image_prompt"] = {
            **STRUCTURED_IMAGE_PROMPT,
            "scene": "@[Alice]站在@[祠堂]门口，手里握着@[玉佩]，身后是@[无名路人]",
        }
        generator = FakeGenerator()
        _patch_execution(monkeypatch, pm, generator)

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)
        await generation_tasks.execute_storyboard_task(
            "demo", ITEM_ID, {"script_file": "episode_1.json", "prompt": _item_of(pm)["image_prompt"]}
        )

        text = preview.storyboard_image.text
        assert text is not None
        assert (
            "Reference_Images: '图1: character reference images; 图2: scene reference images; 图3: prop reference images; 图4: previous storyboard image, use only composition and color.'\nScene: 图1站在图2门口，手里握着图3，身后是无名路人\n"
        ) in text
        assert text == generator.image_calls[0]["prompt"]

    @pytest.mark.parametrize("content_mode", CONTENT_MODES)
    async def test_text_form_video_prompt_is_the_prompt_body(self, tmp_path, monkeypatch, content_mode):
        """Video likewise places plain-text bodies first, verbatim equal to outgoing execution text."""
        body = "镜头缓缓推近，雨水顺着伞沿滑落"
        project_path = prepare_files(tmp_path)
        pm = _pm_for(content_mode, project_path)
        _item_of(pm)["video_prompt"] = body
        (project_path / "storyboards" / f"scene_{ITEM_ID}.png").write_bytes(b"png")
        seed_current_storyboard(pm, ITEM_ID)
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
        """Structured-to-text starts from current rendering; rendering again never duplicates style or negative restrictions."""
        project_path = prepare_files(tmp_path)
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
        """Video likewise avoids duplicate drama Voice_Profiles / Dialogue sections already in the body."""
        project_path = prepare_files(tmp_path)
        pm = _pm_for(content_mode, project_path)
        _patch_execution(monkeypatch, pm, FakeGenerator())

        structured = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)
        seed = structured.video.text
        assert seed is not None
        _item_of(pm)["video_prompt"] = seed
        as_text = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)

        assert as_text.video.text == seed


class TestPreviewMatchesClampedExecution:
    """Over-limit image references share clipping/numbering in preview and execution."""

    async def test_preview_text_is_verbatim_under_clamping(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, register_artifacts=True)
        clamped_lane = fake_resolve_ctx(generator, image_max_reference_images=7)
        use_generation_context(monkeypatch, clamped_lane)
        monkeypatch.setattr(prompt_preview, "resolve_generation_context", clamped_lane)

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)
        result = await generation_tasks.execute_storyboard_task(
            "demo", ITEM_ID, {"script_file": "episode_1.json", "prompt": "queued prompt"}
        )

        assert len(generator.image_calls[0]["reference_images"]) == 7
        assert preview.storyboard_image.text == generator.image_calls[0]["prompt"]
        # Preview returns execution-equivalent clipping warnings before generation; video references are not clipped and carry no
        assert list(preview.storyboard_image.warnings) == result["warnings"]
        assert preview.storyboard_image.warnings == (
            {"key": "ref_too_many_images", "params": {"count": 8, "model": "gpt-image-2", "max_count": 7}},
        )
        assert preview.video.warnings == ()

    async def test_preview_within_the_limit_carries_no_warning(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, register_artifacts=True)
        monkeypatch.setattr(
            prompt_preview, "resolve_generation_context", fake_resolve_ctx(generator, image_max_reference_images=8)
        )

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)

        assert preview.storyboard_image.text is not None
        assert "图8: prop reference images." in preview.storyboard_image.text
        assert preview.storyboard_image.warnings == ()

    async def test_preview_falls_back_to_no_clamping_when_the_lane_cannot_resolve(self, tmp_path, monkeypatch):
        """Missing credentials and similar lookup failures disable preview clipping only; prompts still render."""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        monkeypatch.setattr(prompt_preview, "get_project_manager", lambda: pm)

        async def _unresolvable(*_args, **_kwargs):
            raise RuntimeError("no image provider configured")

        monkeypatch.setattr(prompt_preview, "resolve_generation_context", _unresolvable)

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)

        assert preview.storyboard_image.text is not None
        assert "图8: prop reference images." in preview.storyboard_image.text
        assert preview.storyboard_image.warnings == ()


class TestPreviewIsReadOnly:
    async def test_preview_calls_no_provider_and_writes_no_manifest(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        pm = _pm_for("ad", project_path)
        (project_path / "storyboards" / f"scene_{ITEM_ID}.png").write_bytes(b"png")
        seed_current_storyboard(pm, ITEM_ID)
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
    async def test_pending_image_prompt_reports_reference_gaps_together(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        _item_of(pm)["image_prompt"] = None
        _item_of(pm)["characters_in_segment"] = ["Alice", "未登记角色"]
        _patch_execution(monkeypatch, pm, FakeGenerator())

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)

        assert preview.storyboard_image.unavailable == "script_prompt_pending"
        gaps = preview.storyboard_image.unavailable_params["gaps"]
        assert isinstance(gaps, list)
        assert [gap["code"] for gap in gaps] == [
            "script_prompt_pending",
            "reference_asset_unregistered",
        ]

    async def test_pending_prompt_reports_that_side_only(self, tmp_path, monkeypatch):
        """Mechanically converted ``None`` and legacy absent fields both mean pending generation; the other side still renders."""
        project_path = prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        _item_of(pm)["video_prompt"] = None
        _patch_execution(monkeypatch, pm, FakeGenerator())

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)

        assert preview.video.text is None
        assert preview.video.unavailable == prompt_preview.UNAVAILABLE_PENDING
        assert preview.storyboard_image.text

        _item_of(pm).pop("video_prompt")
        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)
        assert preview.video.unavailable == prompt_preview.UNAVAILABLE_PENDING

    async def test_blank_prompt_reports_missing(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        _item_of(pm)["video_prompt"] = "   "
        _patch_execution(monkeypatch, pm, FakeGenerator())

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)

        assert preview.video.unavailable == prompt_preview.UNAVAILABLE_MISSING

    async def test_malformed_prompt_reports_invalid(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        _item_of(pm)["image_prompt"] = {"composition": {}}
        _patch_execution(monkeypatch, pm, FakeGenerator())

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)

        assert preview.storyboard_image.text is None
        assert preview.storyboard_image.unavailable == prompt_preview.UNAVAILABLE_INVALID

    async def test_refused_generation_input_reports_its_gaps_and_matches_execution(self, tmp_path, monkeypatch):
        """When generation would reject, preview reports unavailable with the same gap code; video side still renders."""
        project_path = prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        (project_path / "props" / "玉佩.png").unlink()
        _item_of(pm)["characters_in_segment"] = ["Alice", "Bob"]
        generator = FakeGenerator()
        _patch_execution(monkeypatch, pm, generator)

        preview = await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)
        with pytest.raises(BadRequestError) as refused:
            await generation_tasks.execute_storyboard_task(
                "demo", ITEM_ID, {"script_file": "episode_1.json", "prompt": STRUCTURED_IMAGE_PROMPT}
            )

        assert preview.storyboard_image.text is None
        assert preview.storyboard_image.unavailable == "reference_asset_unregistered"
        assert preview.storyboard_image.unavailable_params == refused.value.params
        assert preview.storyboard_image.unavailable_params["gaps"] == [
            {"code": "reference_asset_unregistered", "asset_type": "character", "name": "Bob"},
            {"code": "reference_asset_missing", "asset_type": "prop", "name": "玉佩"},
        ]
        assert preview.video.text
        assert generator.image_calls == []

    async def test_unknown_item_id_is_reported(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        _patch_execution(monkeypatch, pm, FakeGenerator())

        with pytest.raises(prompt_preview.ScriptItemNotFound):
            await prompt_preview.preview_item_prompts("demo", "episode_1.json", "E9S99")


class TestPromptPreviewTool:
    """MCP tools thinly wrap services: same preview and same entry point for both hosts."""

    def _services(self, pm: _FakePM):
        from typing import Any, cast

        from server.tool_runtime import Services

        return Services(projects=cast(Any, pm), workflow_planner=cast(Any, None), capabilities=cast(Any, None))

    def _scope(self, project_path: Path):
        from server.tool_runtime import ProjectScope

        return ProjectScope(project_name="demo", data_root=project_path.parent)

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
        project_path = prepare_files(tmp_path)
        pm = _pm_for("drama", project_path)
        _patch_execution(monkeypatch, pm, FakeGenerator())

        outcome = await self._call(pm, project_path)

        assert outcome.problem is None
        assert outcome.value == await prompt_preview.preview_item_prompts("demo", "episode_1.json", ITEM_ID)

    async def test_unknown_item_is_a_typed_problem(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        _patch_execution(monkeypatch, pm, FakeGenerator())

        outcome = await self._call(pm, project_path, item_id="E9S99")

        assert outcome.value is None
        assert outcome.problem is not None
        assert outcome.problem.code == "item_not_found"

    async def test_script_must_be_a_bare_filename(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        pm = _pm_for("narration", project_path)
        _patch_execution(monkeypatch, pm, FakeGenerator())

        outcome = await self._call(pm, project_path, script="../other/episode_1.json")

        assert outcome.problem is not None
        assert outcome.problem.code == "invalid_request"

    def test_registered_in_the_tool_catalogue(self):
        """Embedded-host tool catalog; test_remote_mcp toolset-equality assertions protect remote equivalence."""
        from server.agent_toolset.toolset import ARCREEL_MCP_TOOL_IDS

        assert "get_prompt_preview" in ARCREEL_MCP_TOOL_IDS
