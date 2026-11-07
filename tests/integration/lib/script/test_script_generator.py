import asyncio
import json
import logging
import re
import threading
from pathlib import Path
from typing import cast

import pytest

from lib.artifacts.artifact_activation import activate_artifact_target_state
from lib.config.resolver import ConfigResolver
from lib.generation.video_request_facts import (
    ENDPOINT_FIXED_PLANNING_DURATIONS,
    ExecutionVideoIdentity,
    VideoRequestFacts,
    VideoRequestFactsError,
    VideoRequestFactsFailure,
    evaluate_video_request_facts,
)
from lib.project.project_migrations import CURRENT_SCHEMA_VERSION
from lib.script.prompt_authoring_scope import select_prompt_authoring
from lib.script.script_generator import AdScriptRejected, PlanningVideoFacts, PromptAuthoringTargets, ScriptGenerator
from lib.script.script_review import content_fingerprint, script_plan_path
from lib.speech.speech_composition import SpeechAdmissionError
from tests.factories import make_video_request_facts
from tests.fakes import FakeConfigResolver
from tests.speech_contract_cases import SPEECH_CONTRACT_CASES, SpeechContractCase


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict):
    _write(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _write_project_json(project_path: Path, payload: dict) -> None:
    """Write production-shaped project.json.

    ProjectManager always writes schema_version/generation_mode, and episode ledgers bind scripts before persistence. Production projects cannot omit these; manifests/typed bases reject missing fields."""
    _write_json(
        project_path / "project.json",
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "generation_mode": "storyboard",
            "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
            **payload,
        },
    )


def _activate_project_artifacts(project_path: Path, episode: int = 1) -> None:
    """Complete episode provenance inputs and activate the whole project's artifacts.

    Manifests alone authorize generated reads; disk presence is not registration, and unregistered script_plan cannot enter paid calls. ``episode`` selects only ``source/episode_{episode}.txt`` to fill; registration covers the entire project."""
    source = project_path / "source" / f"episode_{episode}.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("原文", encoding="utf-8")
    activate_artifact_target_state(project_path, bump_schema=False)


def _valid_narration_response() -> dict:
    return {
        "episode": 1,
        "title": "第一集",
        "content_mode": "narration",
        "duration_seconds": 4,
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "1"},
        "segments": [
            {
                "segment_id": "E1S01",
                "duration_seconds": 4,
                "segment_break": False,
                "novel_text": "原文",
                "characters_in_segment": ["姜月茴"],
                "image_prompt": {
                    "scene": "场景",
                    "composition": {
                        "shot_type": "Medium Shot",
                        "lighting": "暖光",
                        "ambiance": "薄雾",
                    },
                },
                "video_prompt": {
                    "action": "转身",
                    "camera_motion": "Static",
                    "ambiance_audio": "风声",
                    "dialogue": [],
                },
            }
        ],
    }


def _write_drama_ledger_project(project_path: Path, episodes: list[dict], characters: dict | None = None) -> None:
    """Write minimal drama project.json with an episode-ledger entry."""
    _write_project_json(
        project_path,
        {
            "title": "项目",
            "content_mode": "drama",
            "overview": {},
            "characters": characters or {},
            "style": "古风",
            "style_description": "cinematic",
            "episodes": episodes,
        },
    )


def _drama_project(tmp_path):
    """Create a minimal drama project and return its path; request facts provide tiers independently of project fields."""
    project_path = tmp_path / "projects" / "demo"
    _write_drama_ledger_project(
        project_path,
        [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
    )
    return project_path


def _drama_script_plan_content() -> dict:
    """Structured drama script_plan includes utterances + source_text + scene_description visual adaptation."""
    return {
        "title": "第一集",
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "segment_break": False,
                "characters_in_scene": ["姜月茴"],
                "scenes": [],
                "props": [],
                "scene_description": "姜月茴立于庭院，目光沉静，晨光斜照。",
                "utterances": [{"kind": "dialogue", "speaker": "姜月茴", "text": "你来了。"}],
                "source_text": "姜月茴缓步走进庭院，抬眼望来。",
            }
        ],
    }


def _drama_visual_response() -> dict:
    """Drama authoring visual response contains scene_id + image_prompt + video_prompt only, without dialogue."""
    return {
        "scenes": [
            {
                "scene_id": "E1S01",
                "image_prompt": {
                    "scene": "场景",
                    "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
                },
                "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
            }
        ],
    }


class _FakeTextBackend:
    def __init__(self, response_text: str = "{}"):
        self._response_text = response_text
        self.last_request = None

    @property
    def name(self):
        return "fake"

    @property
    def model(self):
        return "fake-model"

    @property
    def capabilities(self):
        return set()

    async def generate(self, request):
        self.last_request = request
        from lib.backends.text_backends.base import TextGenerationResult

        return TextGenerationResult(text=self._response_text, provider="fake", model="fake-model")


class _FakeTextGenerator:
    """TextGenerator substitute wrapping _FakeTextBackend."""

    def __init__(self, response_text: str = "{}"):
        self.backend = _FakeTextBackend(response_text)
        self.model = self.backend.model

    async def generate(self, request, project_name=None):
        return await self.backend.generate(request)


class TestScriptGenerator:
    @pytest.fixture(autouse=True)
    def _fixed_tier_facts(self, video_request_facts) -> None:
        """These cases ignore tier branching: facts fix4/6/8; narrowing cases locally override facts."""

    async def test_build_prompt_renders_pending_formal_segments(self, tmp_path):
        """Client-free build_prompt works for dry-run; narration renders pending formal-storyboard content fields."""
        project_path = tmp_path / "projects" / "demo"
        _write_project_json(
            project_path,
            {
                "title": "项目",
                "content_mode": "narration",
                "overview": {"synopsis": "概述"},
                "characters": {"姜月茴": {}},
                "clues": {"玉佩": {}},
                "style": "古风",
                "style_description": "cinematic",
            },
        )
        _write_script_plan_json(project_path, 1, [_script_plan_seg("E1S01", "第一段原文，逐字保留。", duration=4)])
        await _materialized_script(project_path)

        generator = ScriptGenerator(project_path, config_resolver=_resolver())  # No client.
        prompt = await generator.build_prompt(1)

        assert "E1S01" in prompt
        assert "第一段原文，逐字保留。" in prompt  # Render novel_text as read-only context.
        assert "姜月茴" in prompt

    async def test_build_prompt_appends_user_instructions(self, tmp_path):
        """Append instructions at prompt end under neutral additional-instructions headings; absent instructions omit sections."""
        project_path = tmp_path / "projects" / "demo"
        _write_project_json(
            project_path,
            {
                "title": "项目",
                "content_mode": "narration",
                "overview": {"synopsis": "概述"},
                "characters": {"姜月茴": {}},
                "style": "古风",
                "style_description": "cinematic",
            },
        )
        _write_script_plan_json(project_path, 1, [_script_plan_seg("E1S01", "第一段原文，逐字保留。", duration=4)])
        await _materialized_script(project_path)

        generator = ScriptGenerator(project_path, config_resolver=_resolver())

        plain = await generator.build_prompt(1)
        assert "# Additional instructions" not in plain

        prompt = await generator.build_prompt(1, instructions="多给人物面部特写")
        assert prompt.endswith("# Additional instructions\n多给人物面部特写")

    async def test_narration_prompt_authoring_build_prompt_uses_project_source_language(self, tmp_path):
        """Narration visual-authoring prompts use project source_language like drama, never reverting non-Chinese projects to Chinese."""
        project_path = tmp_path / "projects" / "demo"
        _write_project_json(
            project_path,
            {
                "title": "项目",
                "content_mode": "narration",
                "overview": {"synopsis": "概述"},
                "characters": {"姜月茴": {}},
                "style": "古风",
                "style_description": "cinematic",
                "source_language": "English",
            },
        )
        _write_script_plan_json(project_path, 1, [_script_plan_seg("E1S01", "verbatim source line.", duration=4)])
        await _materialized_script(project_path)

        generator = ScriptGenerator(project_path, config_resolver=_resolver())
        prompt = await generator.build_prompt(1)

        # Output language follows project source_language, never default Chinese.
        assert "all string values must use English" in prompt
        assert "all string values must use 中文" not in prompt

    async def test_load_script_plan_drama_missing_raises_without_fallback(self, tmp_path):
        """Missing drama script_plan_normalized_script.json raises explicitly; never fall back to narration splitting tables."""
        project_path = tmp_path / "projects" / "demo"
        _write_project_json(
            project_path,
            {
                "title": "项目",
                "content_mode": "drama",
                "overview": {},
                "characters": {},
                "clues": {},
            },
        )
        _write(project_path / "drafts" / "episode_1" / "script_plan_segments.md", "其他模式中间文件")

        generator = ScriptGenerator(project_path)
        with pytest.raises(FileNotFoundError, match=re.escape("script_plan_normalized_script.json")):
            generator._load_script_plan(1)

    async def test_load_drama_script_plan_content_rejects_non_dict_top_level(self, tmp_path):
        """Nonobject drama plans such as JSON arrays raise ValueError, never silently become empty scripts."""
        project_path = tmp_path / "projects" / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        )
        _write(project_path / "drafts" / "episode_1" / "script_plan_normalized_script.json", "[]")
        _activate_project_artifacts(project_path)
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="顶层应为对象"):
            generator._load_drama_script_plan_content(1)

    async def test_load_drama_script_plan_content_rejects_non_list_scenes(self, tmp_path):
        """Nonlist drama scenes such as objects fail fast with ValueError, never continue as empty scripts."""
        project_path = tmp_path / "projects" / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        )
        _write_drama_script_plan_json(
            project_path,
            1,
            {"title": "第一集", "scenes": {}},
        )
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="scenes 必须是非空"):
            generator._load_drama_script_plan_content(1)

    async def test_load_drama_script_plan_content_rejects_empty_scenes(self, tmp_path):
        """Empty drama scenes fail fast with ValueError; empty scripts are invalid planning artifacts and must not persist scenes=[]."""
        project_path = tmp_path / "projects" / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        )
        _write_drama_script_plan_json(
            project_path,
            1,
            {"title": "第一集", "scenes": []},
        )
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="scenes 必须是非空"):
            generator._load_drama_script_plan_content(1)

    async def test_load_drama_script_plan_content_rejects_non_dict_scene_item(self, tmp_path):
        """Nondict drama scenes such as numbers/strings raise ValueError before rendering/merging."""
        project_path = tmp_path / "projects" / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        )
        _write_drama_script_plan_json(
            project_path,
            1,
            {"title": "第一集", "scenes": [{"scene_id": "E1S01"}, 42]},
        )
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="必须是分镜对象"):
            generator._load_drama_script_plan_content(1)

    async def test_load_drama_script_plan_content_rejects_empty_scene_id(self, tmp_path):
        """Empty/missing scene_id fails fast with ValueError rather than emerging only during merging."""
        project_path = tmp_path / "projects" / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        )
        _write_drama_script_plan_json(
            project_path,
            1,
            {"title": "第一集", "scenes": [{"scene_id": ""}]},
        )
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="scene_id 必须是非空字符串"):
            generator._load_drama_script_plan_content(1)

    async def test_load_drama_script_plan_content_rejects_rewritten_scene_id_collision(self, tmp_path):
        """Distinct IDs colliding after episode rewriting (E1S02_1 / E2S02_1 both become E2S02_1 in episode2) fail loudly, avoiding artifact filenames/keys colliding, like narration plan loading."""
        project_path = tmp_path / "projects" / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 2, "title": "第二集", "script_file": "scripts/episode_2.json"}],
        )
        _write_drama_script_plan_json(
            project_path,
            2,
            {"title": "第二集", "scenes": [{"scene_id": "E1S02_1"}, {"scene_id": "E2S02_1"}]},
        )
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="改写到 episode=2 后重复"):
            generator._load_drama_script_plan_content(2)

    async def test_drama_prompt_authoring_rejects_script_plan_duration_out_of_constrained_set(
        self, tmp_path, set_video_request_facts
    ):
        """Plans created under wider tiers fail loudly when later authoring facts narrow to eight seconds only. Authoring forwards durations unchanged and static persistence requires only positive integers; without reread validation, invalid values persist until video enqueue rejection. Match narration/reference plan checks."""
        set_video_request_facts(make_video_request_facts(supported_durations=(4, 6, 8), allowed_durations=(8,)))
        project_path = _drama_project(tmp_path)

        content = _drama_script_plan_content()
        content["scenes"][0]["duration_seconds"] = 4
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="script_plan 已定分镜时长非法"):
            await generator._assert_drama_script_plan_durations(content["scenes"])

    @pytest.mark.parametrize(
        "raw",
        ["4", 4.0],
        ids=["numeric-string", "integral-float"],
    )
    async def test_drama_prompt_authoring_rejects_out_of_range_duration_in_coercible_form(
        self, tmp_path, set_video_request_facts, raw
    ):
        """Also reject manually entered `"4"` / `4.0`, which final schemas normalize to four. An `isinstance(..., int)`-only guard would skip both and permit out-of-tier bypasses."""
        set_video_request_facts(make_video_request_facts(supported_durations=(4, 6, 8), allowed_durations=(8,)))
        project_path = _drama_project(tmp_path)

        content = _drama_script_plan_content()
        content["scenes"][0]["duration_seconds"] = raw
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="script_plan 已定分镜时长非法"):
            await generator._assert_drama_script_plan_durations(content["scenes"])

    async def test_drama_prompt_authoring_checks_declared_default_when_duration_absent(
        self, tmp_path, set_video_request_facts
    ):
        """Missing duration_seconds still validates declared defaults. Facts allow only six seconds (Hailuo1080p), while DramaSceneContent defaults eight; reject this scene rather than treating omission as exemption."""
        set_video_request_facts(
            make_video_request_facts(
                provider_id="minimax",
                model_id="MiniMax-Hailuo-2.3",
                supported_durations=(6, 10),
                allowed_durations=(6,),
            )
        )
        project_path = _drama_project(tmp_path)

        content = _drama_script_plan_content()
        del content["scenes"][0]["duration_seconds"]
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="script_plan 已定分镜时长非法"):
            await generator._assert_drama_script_plan_durations(content["scenes"])

    async def test_drama_prompt_authoring_checks_declared_default_when_duration_null(
        self, tmp_path, set_video_request_facts
    ):
        """Explicit null validates declared defaults like missing keys. `dict.get` defaults apply only to absent keys; null otherwise yields None and bypasses checks until post-authoring Pydantic rejection, wasting a full generation call."""
        set_video_request_facts(
            make_video_request_facts(
                provider_id="minimax",
                model_id="MiniMax-Hailuo-2.3",
                supported_durations=(6, 10),
                allowed_durations=(6,),
            )
        )
        project_path = _drama_project(tmp_path)

        content = _drama_script_plan_content()
        content["scenes"][0]["duration_seconds"] = None
        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match="script_plan 已定分镜时长非法"):
            await generator._assert_drama_script_plan_durations(content["scenes"])

    async def test_drama_prompt_authoring_accepts_script_plan_duration_within_constrained_set(
        self, tmp_path, set_video_request_facts
    ):
        """Eight seconds remains valid when narrowing leaves eight only; never reject members of the narrowed set."""
        set_video_request_facts(make_video_request_facts(supported_durations=(4, 6, 8), allowed_durations=(8,)))
        project_path = _drama_project(tmp_path)

        generator = ScriptGenerator(project_path)

        assert await generator._assert_drama_script_plan_durations(_drama_script_plan_content()["scenes"]) is None

    async def test_drama_prompt_authoring_build_prompt_renders_formal_scene_content(self, tmp_path):
        """Drama visual-authoring build_prompt renders pending formal-storyboard content and requests visual fields only."""
        project_path = tmp_path / "projects" / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
            characters={"姜月茴": {}},
        )
        _write_drama_script_plan_json(project_path, 1, _drama_script_plan_content())
        await _materialized_script(project_path)

        generator = ScriptGenerator(project_path, config_resolver=_resolver())
        prompt = await generator.build_prompt(1)

        # Formal content fields enter prompts: scene_id + visual adaptation + source text + speech for understanding only.
        assert "E1S01" in prompt
        assert "Visual adaptation: 姜月茴立于庭院，目光沉静，晨光斜照。" in prompt
        assert "姜月茴缓步走进庭院" in prompt
        assert "你来了。" in prompt

    async def test_drama_prompt_authoring_build_prompt_omits_outline(self, tmp_path):
        """Episode outlines drive planning content generation; visual authoring never renders outline sections."""
        project_path = tmp_path / "projects" / "demo"
        _write_drama_ledger_project(
            project_path,
            [
                {
                    "episode": 1,
                    "title": "初入江湖",
                    "script_file": "scripts/episode_1.json",
                    "hook": "少年坠崖生死未卜",
                    "outline": {"story_beats": ["少年下山"], "next_episode_teaser": "崖底神秘人出手相救"},
                    "ledger_status": "planned",
                },
            ],
            characters={"姜月茴": {}},
        )
        _write_drama_script_plan_json(project_path, 1, _drama_script_plan_content())
        await _materialized_script(project_path)

        generator = ScriptGenerator(project_path, config_resolver=_resolver())
        prompt = await generator.build_prompt(1)

        assert "少年坠崖生死未卜" not in prompt

    async def test_drama_prompt_authoring_build_prompt_uses_project_source_language(self, tmp_path):
        """Visual authoring follows project source_language, never reverting non-Chinese projects to Chinese."""
        project_path = tmp_path / "projects" / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
            characters={"姜月茴": {}},
        )
        # Inject non-Chinese source_language, authoritative for generated content.
        project_json_path = project_path / "project.json"
        payload = json.loads(project_json_path.read_text(encoding="utf-8"))
        payload["source_language"] = "English"
        _write_json(project_json_path, payload)
        _write_drama_script_plan_json(project_path, 1, _drama_script_plan_content())
        await _materialized_script(project_path)

        generator = ScriptGenerator(project_path, config_resolver=_resolver())
        prompt = await generator.build_prompt(1)

        # Output language follows project source_language, never default Chinese.
        assert "all string values must use English" in prompt
        assert "all string values must use 中文" not in prompt

    async def test_parse_response_invalid_json_raises(self, tmp_path):
        project_path = tmp_path / "projects" / "demo"
        _write_json(project_path / "project.json", {"title": "项目"})

        generator = ScriptGenerator(project_path)
        with pytest.raises(ValueError, match=r"JSON 解析失败"):
            generator._parse_response("not-json", 1)

    async def test_parse_response_validation_error_returns_raw_data(self, tmp_path):
        project_path = tmp_path / "projects" / "demo"
        _write_json(project_path / "project.json", {"title": "项目"})

        generator = ScriptGenerator(project_path)
        parsed = generator._parse_response('{"foo": "bar"}', 1)
        # Validation fallback returns raw data; ledger-title defaults, including empty titles, are injected first and retained on fallback.
        assert parsed == {"foo": "bar", "title": ""}

    async def test_generate_writes_script_and_metadata(self, tmp_path):
        """Fill pending visuals and clear markers, preserving content verbatim; metadata refreshes generator but keeps created_at."""
        project_path = tmp_path / "projects" / "demo"
        _write_project_json(
            project_path,
            {
                "title": "项目",
                "content_mode": "narration",
                "overview": {},
                "characters": {"姜月茴": {}},
                "clues": {"玉佩": {}},
                "style": "古风",
                "style_description": "cinematic",
            },
        )
        _write_script_plan_json(
            project_path,
            1,
            [{**_script_plan_seg("E1S01", "原样保留的小说原文。", duration=4), "characters_in_segment": ["姜月茴"]}],
        )
        converted = await _materialized_script(project_path)

        fake = _FakeTextGenerator(json.dumps(_narration_visual_response(["E1S01"]), ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake, config_resolver=_resolver())
        output = await generator.generate(1)

        payload = json.loads(output.read_text(encoding="utf-8"))
        assert output == project_path / "scripts" / "episode_1.json"
        assert payload["episode"] == 1
        seg = payload["segments"][0]
        assert seg["novel_text"] == "原样保留的小说原文。"
        assert seg["characters_in_segment"] == ["姜月茴"]
        assert seg["image_prompt"]["scene"] == "画面"
        assert "pending_authoring" not in seg
        assert payload["metadata"]["generator"] == "fake-model"
        assert payload["metadata"]["created_at"] == converted["metadata"]["created_at"]

    @pytest.mark.parametrize("content_mode", ["narration", "drama"])
    async def test_generate_reads_formal_baseline_without_blocking_event_loop(
        self, tmp_path, monkeypatch, content_mode
    ):
        project_path = tmp_path / "projects" / "demo"
        if content_mode == "narration":
            _write_project_json(
                project_path,
                {
                    "title": "项目",
                    "content_mode": "narration",
                    "overview": {},
                    "characters": {},
                    "style": "古风",
                    "style_description": "cinematic",
                },
            )
            _write_script_plan_json(project_path, 1, [_script_plan_seg("E1S01", "原文", duration=4)])
            response = _narration_visual_response(["E1S01"])
        else:
            _write_drama_ledger_project(
                project_path,
                [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
                characters={"姜月茴": {"description": "", "character_sheet": ""}},
            )
            _write_drama_script_plan_json(project_path, 1, _drama_script_plan_content())
            response = _drama_visual_response()
        await _materialized_script(project_path)

        loop_tick = threading.Event()
        provider_saw_tick: list[bool] = []

        class _OrderedTextGenerator(_FakeTextGenerator):
            async def generate(self, request, project_name=None):
                provider_saw_tick.append(loop_tick.is_set())
                return await super().generate(request, project_name)

        generator = ScriptGenerator(
            project_path,
            generator=_OrderedTextGenerator(json.dumps(response, ensure_ascii=False)),
            config_resolver=cast(ConfigResolver, FakeConfigResolver()),
        )

        formal_path = project_path / "scripts" / "episode_1.json"
        baseline_started = threading.Event()
        release_baseline = threading.Event()
        loop_thread = threading.get_ident()
        baseline_thread: list[int] = []
        original_read_bytes = Path.read_bytes

        def blocking_read_bytes(path: Path) -> bytes:
            if path == formal_path and not baseline_started.is_set():
                baseline_thread.append(threading.get_ident())
                baseline_started.set()
                release_baseline.wait()
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", blocking_read_bytes)
        loop = asyncio.get_running_loop()

        def coordinate() -> None:
            baseline_started.wait()
            if baseline_thread == [loop_thread]:
                release_baseline.set()
                return

            def tick_and_release() -> None:
                loop_tick.set()
                release_baseline.set()

            loop.call_soon_threadsafe(tick_and_release)

        coordinator = threading.Thread(target=coordinate)
        coordinator.start()
        await generator.generate(1)
        coordinator.join()

        assert baseline_thread
        assert baseline_thread != [loop_thread]
        assert provider_saw_tick == [True]

    async def test_conversion_rejects_an_unregistered_formal_script_plan(self, tmp_path):
        """Unregistered planning cannot be read for materialization and creates no formal script."""
        project_path = tmp_path / "projects" / "demo"
        _write_project_json(
            project_path,
            {
                "schema_version": CURRENT_SCHEMA_VERSION,
                "title": "项目",
                "content_mode": "narration",
                "generation_mode": "storyboard",
                "source_kind": "novel",
                "source_language": "中文",
                "overview": {},
                "characters": {},
                "scenes": {},
                "props": {},
                "style": "古风",
                "style_description": "cinematic",
                "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
            },
        )
        _write_script_plan_json(
            project_path, 1, [_script_plan_seg("E1S01", "未登记的正式原文。", duration=4)], register=False
        )

        with pytest.raises(ValueError, match=r"script_plan artifact is not registered"):
            await _materialize(ScriptGenerator(project_path, config_resolver=_resolver()))

        assert not (project_path / "scripts" / "episode_1.json").exists()

    async def test_conversion_injects_hook_and_teaser_from_ledger(self, tmp_path):
        """Formal episode hook / next_episode_teaser comes from the ledger through strict persistence validation."""
        project_path = tmp_path / "projects" / "demo"
        _write_drama_ledger_project(
            project_path,
            [
                {
                    "episode": 1,
                    "title": "初入江湖",
                    "script_file": "scripts/episode_1.json",
                    "hook": "少年坠崖生死未卜",
                    "outline": {
                        "story_beats": ["少年下山"],
                        "next_episode_teaser": "崖底神秘人出手相救",
                    },
                    "ledger_status": "planned",
                },
            ],
            characters={"姜月茴": {}},
        )
        _write_drama_script_plan_json(project_path, 1, _drama_script_plan_content())

        payload = await _materialized_script(project_path)

        assert payload["hook"] == "少年坠崖生死未卜"
        assert payload["next_episode_teaser"] == "崖底神秘人出手相救"

    async def test_conversion_without_ledger_hook_leaves_fields_null(self, tmp_path):
        """Legacy ledgers without hooks/teasers use null fields and still pass persistence validation."""
        project_path = tmp_path / "projects" / "demo"
        _write_project_json(
            project_path,
            {
                "title": "项目",
                "content_mode": "narration",
                "overview": {},
                "characters": {"姜月茴": {}},
                "style": "古风",
                "style_description": "cinematic",
                "episodes": [
                    {"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"},
                ],
            },
        )
        _write_script_plan_json(project_path, 1, [_script_plan_seg("E1S01", "原文", duration=4)])

        payload = await _materialized_script(project_path)

        assert payload["hook"] is None
        assert payload["next_episode_teaser"] is None

    async def test_generate_keeps_formal_entry_ids_and_episode(self, tmp_path):
        """Formal segment_id anchors writeback; authoring episode10 preserves entry IDs and episode numbers."""
        project_path = tmp_path / "projects" / "demo"
        _write_project_json(
            project_path,
            {
                "title": "项目",
                "content_mode": "narration",
                "overview": {},
                "characters": {"姜月茴": {}},
                "clues": {"玉佩": {}},
                "style": "古风",
                "style_description": "cinematic",
                "episodes": [{"episode": 10, "title": "第十集", "script_file": "scripts/episode_10.json"}],
            },
        )
        _write_script_plan_json(project_path, 10, [_script_plan_seg("E10S01", "原文", duration=4)])
        await _materialized_script(project_path, 10)

        fake = _FakeTextGenerator(
            json.dumps(_narration_visual_response(["E10S01"], title="第十集"), ensure_ascii=False)
        )
        generator = ScriptGenerator(project_path, generator=fake, config_resolver=_resolver())

        output = await generator.generate(10)

        payload = json.loads(output.read_text(encoding="utf-8"))
        assert output == project_path / "scripts" / "episode_10.json"
        assert payload["episode"] == 10
        assert payload["segments"][0]["segment_id"] == "E10S01"
        assert payload["segments"][0]["image_prompt"]["scene"] == "画面"

    async def test_generate_drama_prompt_authoring_passes_visual_schema(self, tmp_path):
        """Drama authoring output schema is DramaVisualScript with scene_id + visuals only, excluding nonvisual fields."""
        from lib.script.script_models import DramaVisualScript

        project_path = tmp_path / "projects" / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
            characters={"姜月茴": {}},
        )
        _write_drama_script_plan_json(project_path, 1, _drama_script_plan_content())
        await _materialized_script(project_path)

        fake = _FakeTextGenerator(json.dumps(_drama_visual_response(), ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake, config_resolver=_resolver())
        output = await generator.generate(1)

        schema = fake.backend.last_request.response_schema
        assert schema is DramaVisualScript
        props = DramaVisualScript.model_json_schema()["$defs"]["DramaSceneVisual"]["properties"]
        # Nonvisual fields stay out of LLM schemas: engineering forwards them without drift.
        assert "utterances" not in props
        assert "source_text" not in props
        assert "duration_seconds" not in props
        scene = json.loads(output.read_text(encoding="utf-8"))["scenes"][0]
        assert scene["source_text"] == "姜月茴缓步走进庭院，抬眼望来。"
        assert scene["utterances"][0]["text"] == "你来了。"
        assert scene["image_prompt"]["scene"] == "场景"

    async def test_generate_drama_prompt_authoring_appends_user_instructions(self, tmp_path):
        """Generation also appends instructions at prompt end under neutral additional-instructions headings."""
        project_path = tmp_path / "projects" / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
            characters={"姜月茴": {}},
        )
        _write_drama_script_plan_json(project_path, 1, _drama_script_plan_content())
        await _materialized_script(project_path)

        fake = _FakeTextGenerator(json.dumps(_drama_visual_response(), ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake, config_resolver=_resolver())
        await generator.generate(1, instructions="打斗场面多给全景")

        assert fake.backend.last_request.prompt.endswith("# Additional instructions\n打斗场面多给全景")

    async def test_generate_drama_prompt_authoring_rejects_mixed_scene_before_backend_call(self, tmp_path):
        """Narration mixed into pending drama dialogue fails speech admission before text-model invocation, preserving formal scripts."""
        project_path = tmp_path / "projects" / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
            characters={"姜月茴": {}},
        )
        _write_drama_script_plan_json(project_path, 1, _drama_script_plan_content())
        await _materialized_script(project_path)
        script_path = project_path / "scripts" / "episode_1.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))
        script["scenes"][0]["utterances"].append({"kind": "voiceover", "speaker": None, "text": "庭院里只剩风声。"})
        script["scenes"][0]["needs_replan"] = True
        _write_json(script_path, script)
        before = script_path.read_bytes()

        fake = _FakeTextGenerator(json.dumps(_drama_visual_response(), ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake, config_resolver=_resolver())

        with pytest.raises(SpeechAdmissionError) as exc_info:
            await generator.generate(1)

        admission = exc_info.value.admission
        assert admission.unit_id == "E1S01"
        assert admission.problems
        assert fake.backend.last_request is None
        assert script_path.read_bytes() == before

    async def test_generate_sets_script_max_output_tokens(self, tmp_path):
        """Drama authoring sets shared DEFAULT_MAX_OUTPUT_TOKENS on TextGenerationRequest."""
        from lib.backends.text_backends.base import DEFAULT_MAX_OUTPUT_TOKENS

        project_path = tmp_path / "projects" / "demo"
        _write_drama_ledger_project(
            project_path,
            [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
            characters={"姜月茴": {}},
        )
        _write_drama_script_plan_json(project_path, 1, _drama_script_plan_content())
        await _materialized_script(project_path)

        # Empty visual output fails structural validation loudly, but model calls happened and request arguments remain assertable.
        fake = _FakeTextGenerator(json.dumps({"foo": "bar"}))
        generator = ScriptGenerator(project_path, generator=fake, config_resolver=_resolver())
        with pytest.raises(ValueError, match="视觉层结构校验失败"):
            await generator.generate(1)

        assert fake.backend.last_request.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS
        assert DEFAULT_MAX_OUTPUT_TOKENS >= 16000

    async def test_generate_without_backend_raises(self, tmp_path):
        """generate() without injected backends raises RuntimeError."""
        project_path = tmp_path / "projects" / "demo"
        _write_json(project_path / "project.json", {"title": "项目"})
        _write(project_path / "drafts" / "episode_1" / "script_plan_segments.md", "content")

        generator = ScriptGenerator(project_path)  # No backend.
        with pytest.raises(RuntimeError, match="TextGenerator 未初始化"):
            await generator.generate(1)

    @pytest.mark.parametrize(
        "bad_filename",
        [
            "subdir/episode_1.json",  # Subdirectory.
            "../etc/passwd",  # path traversal
            "/tmp/abs.json",  # Absolute path.
            "a\\b.json",  # Windows separators.
            "",  # Empty string: Path("").name == "" passes the first two checks and otherwise fails only during persistence.
        ],
    )
    async def test_generate_rejects_non_basename_output_filename(self, tmp_path, bad_filename):
        """Public generate(output_filename=...) accepts filenames only, never directories, and enforces this at entry. save_script _safe_subpath blocks absolute/traversal paths but allows contained subdirectories; explicit public rejection keeps its documented contract."""
        project_path = tmp_path / "projects" / "demo"
        _write_json(project_path / "project.json", {"title": "项目"})

        fake = _FakeTextGenerator(json.dumps(_valid_narration_response(), ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)
        with pytest.raises(ValueError, match="只接受纯文件名"):
            await generator.generate(1, output_filename=bad_filename)


class TestAddMetadataRewritesEpisodePrefix:
    """_add_metadata fallback rewrites E\\d+ prefixes on segment/scene/unit IDs."""

    @staticmethod
    def _make_generator(
        tmp_path: Path, content_mode: str = "narration", generation_mode: str = "storyboard"
    ) -> ScriptGenerator:
        project_path = tmp_path / "projects" / "demo"
        _write_project_json(
            project_path,
            {
                "title": "项目",
                "content_mode": content_mode,
                "generation_mode": generation_mode,
            },
        )
        return ScriptGenerator(project_path)

    def test_drama_rewrites_scene_ids(self, tmp_path: Path) -> None:
        sg = self._make_generator(tmp_path, content_mode="drama")
        data = {
            "scenes": [
                {"scene_id": "E1S01", "other": "keep"},
                {"scene_id": "E1S04_2"},
            ],
        }
        out = sg._add_metadata(data, episode=2)
        assert out["scenes"][0]["scene_id"] == "E2S01"
        assert out["scenes"][1]["scene_id"] == "E2S04_2"
        assert out["scenes"][0]["other"] == "keep"

    def test_narration_rewrites_segment_ids(self, tmp_path: Path) -> None:
        sg = self._make_generator(tmp_path, content_mode="narration")
        data = {
            "segments": [
                {"segment_id": "E1S01"},
                {"segment_id": "E1S02_1"},
            ],
        }
        out = sg._add_metadata(data, episode=3)
        assert out["segments"][0]["segment_id"] == "E3S01"
        assert out["segments"][1]["segment_id"] == "E3S02_1"

    @pytest.mark.parametrize("case", SPEECH_CONTRACT_CASES, ids=lambda case: case.route_id)
    def test_six_route_machine_candidates_preserve_mixed_speech_and_mark_replan(
        self,
        tmp_path: Path,
        case: SpeechContractCase,
    ) -> None:
        sg = self._make_generator(tmp_path, content_mode=case.content_mode, generation_mode=case.generation_mode)
        original = case.unit()
        data = {case.kind: [case.unit()]}

        out = sg._add_metadata(data, episode=1)

        assert out[case.kind][0]["needs_replan"] is True
        assert {key: value for key, value in out[case.kind][0].items() if key != "needs_replan"} == original

    def test_reference_video_rewrites_unit_ids(self, tmp_path: Path) -> None:
        project_path = tmp_path / "projects" / "demo"
        _write_project_json(
            project_path,
            {
                "title": "项目",
                "content_mode": "narration",
                "generation_mode": "reference_video",
            },
        )
        sg = ScriptGenerator(project_path)
        data = {
            "video_units": [
                {"unit_id": "E1U01"},
                {"unit_id": "E1U02_1"},
            ],
        }
        out = sg._add_metadata(data, episode=2)
        assert out["video_units"][0]["unit_id"] == "E2U01"
        assert out["video_units"][1]["unit_id"] == "E2U02_1"

    def test_idempotent_when_prefix_already_correct(self, tmp_path: Path) -> None:
        """Already-correct episode prefixes remain unchanged, preserving valid data."""
        sg = self._make_generator(tmp_path, content_mode="narration")
        data = {"segments": [{"segment_id": "E2S01"}, {"segment_id": "E2S02_3"}]}
        out = sg._add_metadata(data, episode=2)
        assert out["segments"][0]["segment_id"] == "E2S01"
        assert out["segments"][1]["segment_id"] == "E2S02_3"

    def test_unknown_id_format_unchanged(self, tmp_path: Path) -> None:
        """Never rewrite IDs lacking `E\\d+[SU]` prefixes, avoiding collateral changes."""
        sg = self._make_generator(tmp_path, content_mode="narration")
        data = {"segments": [{"segment_id": "G01"}, {"segment_id": "scene_1"}]}
        out = sg._add_metadata(data, episode=2)
        assert out["segments"][0]["segment_id"] == "G01"
        assert out["segments"][1]["segment_id"] == "scene_1"


class TestAddMetadataInjectsHiddenFields:
    """After LLM schemas hide content_mode / novel, _add_metadata still persists both in dicts: project summaries / files router / Jianying consume dicts rather than models, so fallback belongs at dict level."""

    @staticmethod
    def _make_generator(tmp_path: Path, content_mode: str = "drama") -> ScriptGenerator:
        project_path = tmp_path / "projects" / "demo"
        _write_project_json(
            project_path,
            {
                "title": "项目标题",
                "content_mode": content_mode,
            },
        )
        return ScriptGenerator(project_path)

    def test_drama_injects_content_mode_and_novel_when_llm_omits(self, tmp_path: Path) -> None:
        sg = self._make_generator(tmp_path, content_mode="drama")
        data = {"title": "第一集", "scenes": [{"scene_id": "E1S01"}]}
        out = sg._add_metadata(data, episode=1)
        assert out["content_mode"] == "drama"
        assert out["novel"] == {"title": "项目标题", "chapter": "第一集"}

    def test_narration_injects_content_mode_and_novel_when_llm_omits(self, tmp_path: Path) -> None:
        sg = self._make_generator(tmp_path, content_mode="narration")
        data = {"title": "第一集", "segments": [{"segment_id": "E1S01"}]}
        out = sg._add_metadata(data, episode=1)
        assert out["content_mode"] == "narration"
        assert out["novel"]["chapter"] == "第一集"

    def test_strips_legacy_generation_mode_stamp(self, tmp_path: Path) -> None:
        """project.json alone owns generation mode; strip script generation_mode markers before persistence from regenerated legacy scripts or raw validation-fallback dicts."""
        sg = self._make_generator(tmp_path, content_mode="drama")
        data = {"title": "第一集", "generation_mode": "reference_video", "scenes": [{"scene_id": "E1S01"}]}
        out = sg._add_metadata(data, episode=1)
        assert "generation_mode" not in out

    def test_setdefault_does_not_overwrite_existing_values(self, tmp_path: Path) -> None:
        """If LLM unexpectedly supplies content_mode / novel, setdefault must not overwrite them."""
        sg = self._make_generator(tmp_path, content_mode="drama")
        data = {
            "title": "第一集",
            "content_mode": "drama",
            "novel": {"title": "用户的小说", "chapter": "卷一·风起"},
            "scenes": [{"scene_id": "E1S01"}],
        }
        out = sg._add_metadata(data, episode=1)
        assert out["content_mode"] == "drama"
        assert out["novel"] == {"title": "用户的小说", "chapter": "卷一·风起"}

    def test_drama_overrides_empty_novel_after_model_dump(self, tmp_path: Path) -> None:
        """End-to-end model_validate → model_dump leaves novel present but empty; _add_metadata checks content emptiness rather than key presence or would retain placeholders."""
        from lib.script.script_models import DramaEpisodeScript

        sg = self._make_generator(tmp_path, content_mode="drama")
        llm_response = {
            "title": "第一集",
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "characters_in_scene": ["A"],
                    "image_prompt": {
                        "scene": "s",
                        "composition": {"shot_type": "Medium Shot", "lighting": "l", "ambiance": "a"},
                    },
                    "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "x"},
                }
            ],
        }
        # Fully simulate _parse_response: model_validate → model_dump.
        dumped = DramaEpisodeScript.model_validate(llm_response).model_dump()
        # Guard premise: model_dump already inserted empty NovelInfo.
        assert dumped["novel"] == {"title": "", "chapter": ""}

        out = sg._add_metadata(dumped, episode=1)
        assert out["novel"] == {"title": "项目标题", "chapter": "第一集"}

    def test_narration_overrides_empty_novel_after_model_dump(self, tmp_path: Path) -> None:
        from lib.script.script_models import NarrationEpisodeScript

        sg = self._make_generator(tmp_path, content_mode="narration")
        llm_response = {
            "title": "第一集",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "novel_text": "x",
                    "characters_in_segment": [],
                    "image_prompt": {
                        "scene": "s",
                        "composition": {"shot_type": "Medium Shot", "lighting": "l", "ambiance": "a"},
                    },
                    "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "x"},
                }
            ],
        }
        dumped = NarrationEpisodeScript.model_validate(llm_response).model_dump()
        assert dumped["novel"] == {"title": "", "chapter": ""}

        out = sg._add_metadata(dumped, episode=2)
        assert out["novel"] == {"title": "项目标题", "chapter": ""}

    def test_partial_novel_only_title_is_also_reinjected(self, tmp_path: Path) -> None:
        """Partially filled novel objects (title only/chapter only) also trigger reinjection, preventing incomplete metadata."""
        sg = self._make_generator(tmp_path, content_mode="drama")
        data = {
            "title": "第一集",
            "novel": {"title": "残缺标题", "chapter": ""},
            "scenes": [{"scene_id": "E1S01"}],
        }
        out = sg._add_metadata(data, episode=1)
        assert out["novel"]["chapter"] == "第一集"
        assert out["novel"]["title"] == "项目标题"


def _sg_with_project(tmp_path, project: dict, *, config_resolver: ConfigResolver | None = None) -> ScriptGenerator:
    """Construct ScriptGenerator without __init__ for request-fact reads only, without TextGenerator."""
    project_dir = tmp_path / "p"
    project_dir.mkdir(exist_ok=True)
    sg = ScriptGenerator.__new__(ScriptGenerator)
    sg.project_path = project_dir
    sg.project_json = project
    sg.config_resolver = config_resolver
    return sg


_VEO = "gemini-vertex/veo-3.1-generate-001"


class TestPlanningVideoFacts:
    """Planning tiers come directly from video-request facts; unresolved facts raise original codes without secondary sources."""

    def test_narrowed_tiers_are_the_planning_tiers(self):
        """Planning tiers are narrowed facts, never full model declarations."""
        facts = PlanningVideoFacts(
            by_bucket={"i2v": make_video_request_facts(supported_durations=(4, 6, 8), allowed_durations=(8,))},
        )
        assert facts.planning_durations("i2v") == [8]

    def test_an_endpoint_fixed_tier_borrows_the_planning_durations(self):
        """Endpoint-fixed duration must not block planning (``docs/adr/0082``).

        ComfyUI workflows determine length, so empty tiers are valid. Planning still needs approximate storyboard duration for volume estimates; borrow fixed planning tiers."""
        facts = PlanningVideoFacts(
            by_bucket={
                "i2v": make_video_request_facts(
                    provider_id="custom-3",
                    model_id="my-wan-workflow",
                    supported_durations=(),
                    allowed_durations=(),
                    duration_endpoint_fixed=True,
                )
            },
        )
        assert facts.planning_durations("i2v") == ENDPOINT_FIXED_PLANNING_DURATIONS

    @pytest.mark.parametrize(
        "code",
        ["video_supported_durations_missing", "video_supported_durations_invalid", "video_capability_unavailable"],
    )
    def test_a_failed_bucket_raises_the_same_code_as_execution(self, code):
        """At checkpoints requiring successful facts, unresolvable buckets raise original codes without full-set/default fallback."""
        facts = PlanningVideoFacts(by_bucket={"i2v": VideoRequestFactsFailure(code)})

        with pytest.raises(VideoRequestFactsError) as excinfo:
            facts.planning_durations("i2v")
        assert excinfo.value.code == code


class TestFetchVideoRequestFacts:
    """Read facts by route/bucket: storyboard has only its project-mode bucket; reference reads both r2v and i2v."""

    async def test_storyboard_route_reads_the_single_bucket(self, tmp_path, set_video_request_facts):
        set_video_request_facts({"i2v": make_video_request_facts(allowed_durations=(8,))})
        sg = _sg_with_project(tmp_path, {"video_backend": _VEO, "generation_mode": "storyboard"})

        facts = await sg._fetch_video_request_facts()

        assert set(facts.by_bucket) == {"i2v"}
        assert sg._storyboard_planning_durations(facts) == [8]

    async def test_reference_route_keeps_a_failed_bucket_without_raising(self, tmp_path, set_video_request_facts):
        """Reference routes read both buckets, retain failures and raise only at checkpoints needing them."""
        set_video_request_facts(
            {
                "r2v": VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "r2v"),)),
                "i2v": make_video_request_facts(route="reference_video", allowed_durations=(4, 6, 8)),
            }
        )
        sg = _sg_with_project(tmp_path, {"video_backend": _VEO, "generation_mode": "reference_video"})

        facts = await sg._fetch_video_request_facts()

        assert set(facts.by_bucket) == {"r2v", "i2v"}
        assert facts.planning_durations("i2v") == [4, 6, 8]
        assert sg._resolve_max_refs(facts) is None
        with pytest.raises(VideoRequestFactsError) as excinfo:
            facts.planning_durations("r2v")
        assert excinfo.value.code == "reference_capability_unavailable"


@pytest.mark.parametrize(
    ("project", "bucket", "expected"),
    [
        pytest.param(
            {"video_backend": _VEO, "generation_mode": "storyboard", "model_settings": {_VEO: {"resolution": "1080p"}}},
            "i2v",
            [8],
            id="storyboard-veo-1080p",
        ),
        pytest.param(
            {"video_backend": _VEO, "generation_mode": "storyboard"},
            "i2v",
            [4, 6, 8],
            id="storyboard-veo-unset-resolution",
        ),
        pytest.param(
            {"video_provider_r2v": _VEO, "video_provider_i2v": _VEO, "generation_mode": "reference_video"},
            "r2v",
            [8],
            id="reference-veo-with-reference-images",
        ),
        pytest.param(
            {"video_provider_r2v": _VEO, "video_provider_i2v": _VEO, "generation_mode": "reference_video"},
            "i2v",
            [4, 6, 8],
            id="reference-veo-without-reference-images",
        ),
    ],
)
async def test_planning_and_execution_read_the_same_tiers_under_one_configuration(
    tmp_path, db_factory, project, bucket, expected
):
    """For one configuration, planning tiers equal execution tiers from actual execution models. Both share fact evaluation: extra planned seconds fail execution; omitted valid seconds arbitrarily restrict pacing."""
    resolver = ConfigResolver(db_factory)
    sg = _sg_with_project(tmp_path, project, config_resolver=resolver)

    planning = await sg._fetch_video_request_facts()
    executed = await evaluate_video_request_facts(
        project,
        route=planning.require(bucket).route,
        generation_type=bucket,
        identity=ExecutionVideoIdentity("gemini-vertex", "veo-3.1-generate-001"),
        resolver=resolver,
    )

    assert isinstance(executed, VideoRequestFacts)
    assert planning.planning_durations(bucket) == expected == list(executed.allowed_durations)


def _bare_generator(tmp_path: Path, project_extra: dict | None = None) -> ScriptGenerator:
    """Construct narration ScriptGenerator without backend initialization for internal-method tests. Persist project.json: _load_reference_script_plan shifts confirmation fingerprints through unconditionally locked ProjectManager.update_project, not memory short-circuit; absent files raise FileNotFoundError."""
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True, exist_ok=True)
    sg = ScriptGenerator.__new__(ScriptGenerator)
    sg.generator = None
    sg.project_path = project_dir
    sg.project_json = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "episodes": [{"episode": n, "title": f"第{n}集", "script_file": f"scripts/episode_{n}.json"} for n in (1, 2)],
        **(project_extra or {}),
    }
    sg.content_mode = sg.project_json.get("content_mode", "narration")
    (project_dir / "project.json").write_text(json.dumps(sg.project_json, ensure_ascii=False), encoding="utf-8")
    return sg


# Skeleton kind → triggering (content_mode, generation_mode), inverse of resolve_declared_kind.
_KIND_TO_MODES: dict[str, tuple[str, str | None]] = {
    "segments": ("narration", None),
    "scenes": ("drama", None),
    "shots": ("ad", None),
    "video_units": ("narration", "reference_video"),
}


class TestScriptGeneratorSkeletonExhaustiveness:
    """Exhaustively cover every SKELETONS key in script-generator dispatch. Adding a fifth plus canonical mapping must fail each unregistered local mapping/unhandled dispatch."""

    def test_parse_schema_covers_every_skeleton_kind(self):
        from lib.script.script_generator import _KIND_PARSE_SCHEMA
        from lib.script.script_skeleton import SKELETONS

        assert set(_KIND_PARSE_SCHEMA) == set(SKELETONS)

    def test_item_fallback_duration_covers_every_skeleton_kind(self):
        # Centralize duration fallback in script_models for all four skeletons, including shots/video_units→zero;
        # adding a fifth without registration makes item_duration lookup raise KeyError.
        from lib.script.script_models import _ITEM_FALLBACK_DURATIONS
        from lib.script.script_skeleton import SKELETONS

        assert set(_ITEM_FALLBACK_DURATIONS) == set(SKELETONS)

    @pytest.mark.parametrize("kind", list(_KIND_TO_MODES))
    def test_add_metadata_handles_every_skeleton_kind(self, kind: str, tmp_path: Path):
        from lib.script.script_skeleton import SKELETONS

        # Parametrize every SKELETONS key; adding a fifth without _KIND_TO_MODES registration raises KeyError.
        assert set(_KIND_TO_MODES) == set(SKELETONS)

        content_mode, gen_mode = _KIND_TO_MODES[kind]
        extra: dict = {"content_mode": content_mode}
        if gen_mode:
            extra["generation_mode"] = gen_mode
        sg = _bare_generator(tmp_path, extra)

        id_field = SKELETONS[kind].id_field
        out = sg._add_metadata({kind: [{id_field: "E1S01"}]}, episode=2)

        # Look up array/ID fields and rewrite prefixes to current episode.
        assert out[kind][0][id_field] == "E2S01"
        # Never persist counts/total duration; project summaries derive them on read.
        assert "duration_seconds" not in out

    def test_add_metadata_survives_dirty_degraded_items(self, tmp_path: Path):
        # Raw fallback dicts may contain nondict segments or nonnumeric duration_seconds;
        # prefix rewriting must preserve malformed entries without crashing.
        sg = _bare_generator(tmp_path, {"content_mode": "narration"})

        out = sg._add_metadata(
            {
                "segments": [
                    {"segment_id": "E1S01", "duration_seconds": 5},
                    "junk_not_a_dict",
                    {"segment_id": "E1S02"},
                    {"segment_id": "E1S03", "duration_seconds": None},
                    {"segment_id": "E1S04", "duration_seconds": -5},
                ]
            },
            episode=2,
        )

        # Rewrite dict prefixes, leaving nondicts untouched and excluded.
        assert out["segments"][0]["segment_id"] == "E2S01"
        assert out["segments"][1] == "junk_not_a_dict"
        assert out["segments"][2]["segment_id"] == "E2S02"
        assert out["segments"][4]["segment_id"] == "E2S04"

    def test_add_metadata_survives_non_list_array(self, tmp_path: Path):
        # Truthy scalar arrays from LLM mistakes bypass `... or []`; isinstance guards prevent iteration failure.
        sg = _bare_generator(tmp_path, {"content_mode": "narration"})

        out = sg._add_metadata({"segments": 123}, episode=1)

        assert out["segments"] == 123
        assert out["episode"] == 1

    def test_quality_probe_survives_non_list_array(self, tmp_path: Path, caplog):
        # Truthy scalar arrays make _quality_probe empty via isinstance; although outer try/except would absorb errors,
        # never take “quality probe skipped”, which would mean failed guards and incorrectly skipped probes.
        sg = _bare_generator(tmp_path, {"content_mode": "narration"})

        with caplog.at_level("WARNING", logger="lib.script.script_generator"):
            sg._quality_probe({"segments": 123}, episode=1)

        assert not any("quality probe skipped" in r.message for r in caplog.records)


def _script_plan_seg(
    segment_id: str,
    novel_text: str,
    *,
    duration: int = 4,
    brk: bool = False,
    characters: list[str] | None = None,
    scenes: list[str] | None = None,
    props: list[str] | None = None,
) -> dict:
    return {
        "segment_id": segment_id,
        "novel_text": novel_text,
        "duration_seconds": duration,
        "segment_break": brk,
        "characters_in_segment": characters or [],
        "scenes": scenes or [],
        "props": props or [],
    }


def _visual_seg(segment_id: str, *, scene: str = "画面", action: str = "动作") -> dict:
    return {
        "segment_id": segment_id,
        "image_prompt": {
            "scene": scene,
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": action, "camera_motion": "Static", "ambiance_audio": "风声", "dialogue": []},
    }


def _write_script_plan_json(project_path: Path, episode: int, segments: list[dict], *, register: bool = True) -> None:
    """Write narration intermediate script_plan_segments.json and register using production rules."""
    path = project_path / "drafts" / f"episode_{episode}" / "script_plan_segments.json"
    _write(path, json.dumps({"episode": episode, "segments": segments}, ensure_ascii=False))
    if register:
        _activate_project_artifacts(project_path, episode)


def _write_drama_script_plan_json(project_path: Path, episode: int, content: dict, *, register: bool = True) -> None:
    """Write drama intermediate script_plan_normalized_script.json and register in manifests."""
    _write_json(project_path / "drafts" / f"episode_{episode}" / "script_plan_normalized_script.json", content)
    if register:
        _activate_project_artifacts(project_path, episode)


def _narration_visual_response(segment_ids: list[str], *, title: str = "第一集") -> dict:
    """Authoring visual-layer LLM response shaped as NarrationVisualEpisodeScript."""
    return {"title": title, "segments": [_visual_seg(sid) for sid in segment_ids]}


def _resolver() -> ConfigResolver:
    return cast(ConfigResolver, FakeConfigResolver())


async def _materialized_script(project_path: Path, episode: int = 1) -> dict:
    """Materialize entire registered plans through confirmation into pending formal entries and return persisted scripts."""
    await _materialize(ScriptGenerator(project_path, config_resolver=_resolver()), episode)
    return json.loads((project_path / "scripts" / f"episode_{episode}.json").read_text(encoding="utf-8"))


async def _materialize(generator: ScriptGenerator, episode: int = 1) -> None:
    plan_path = script_plan_path(generator.project_path, generator.project_json, episode)
    assert plan_path is not None
    plan_revision = content_fingerprint(plan_path)
    assert plan_revision is not None
    await generator.materialize_script_plan(
        episode,
        expected_plan_revision=plan_revision,
        expected_script_fingerprint=content_fingerprint(generator.project_path / "scripts" / f"episode_{episode}.json"),
        project_update=lambda _project: None,
    )


def _segment_targets(segments: list[dict], target_ids: list[str]) -> PromptAuthoringTargets:
    script = {"segments": segments}
    wanted = set(target_ids)
    return PromptAuthoringTargets(
        kind="segments",
        id_field="segment_id",
        script=script,
        entries=tuple(seg for seg in segments if seg["segment_id"] in wanted),
        selection=select_prompt_authoring(
            segments, kind="segments", id_field="segment_id", entry_ids=target_ids, rewrite=True
        ),
    )


class TestMergeVisualLayer:
    """Merge visuals by entry IDs into formal scripts, overwriting only image_prompt / video_prompt and preserving every other field verbatim."""

    def test_content_fields_pass_through_verbatim(self):
        segments = [
            {**_script_plan_seg("E1S01", "原文甲。", duration=6, brk=True), "note": "用户备注"},
            _script_plan_seg("E1S02", "原文乙！"),
        ]
        targets = _segment_targets(segments, ["E1S01", "E1S02"])

        merged = ScriptGenerator._merge_visual_layer(targets, [_visual_seg("E1S01"), _visual_seg("E1S02")], 1)

        assert [s["segment_id"] for s in merged] == ["E1S01", "E1S02"]
        assert merged[0]["novel_text"] == "原文甲。"
        assert merged[0]["duration_seconds"] == 6
        assert merged[0]["segment_break"] is True
        assert merged[0]["note"] == "用户备注"
        assert merged[1]["novel_text"] == "原文乙！"
        assert merged[0]["image_prompt"]["scene"] == "画面"
        assert merged[0]["video_prompt"]["action"] == "动作"

    def test_merge_aligns_by_id_not_order(self):
        """Out-of-order visual outputs align by segment_id, preserving formal order."""
        segments = [_script_plan_seg("E1S01", "甲"), _script_plan_seg("E1S02", "乙")]
        targets = _segment_targets(segments, ["E1S01", "E1S02"])
        visual = [_visual_seg("E1S02", scene="乙画面"), _visual_seg("E1S01", scene="甲画面")]

        merged = ScriptGenerator._merge_visual_layer(targets, visual, 1)

        assert [s["segment_id"] for s in merged] == ["E1S01", "E1S02"]
        assert merged[0]["image_prompt"]["scene"] == "甲画面"
        assert merged[1]["image_prompt"]["scene"] == "乙画面"

    def test_merge_returns_only_the_authored_entries(self):
        segments = [_script_plan_seg("E1S01", "甲"), _script_plan_seg("E1S02", "乙")]
        targets = _segment_targets(segments, ["E1S02"])

        merged = ScriptGenerator._merge_visual_layer(targets, [_visual_seg("E1S02")], 1)

        assert [s["segment_id"] for s in merged] == ["E1S02"]

    def test_missing_visual_segment_raises(self):
        segments = [_script_plan_seg("E1S01", "甲"), _script_plan_seg("E1S02", "乙")]
        targets = _segment_targets(segments, ["E1S01", "E1S02"])
        with pytest.raises(ValueError, match="E1S02"):
            ScriptGenerator._merge_visual_layer(targets, [_visual_seg("E1S01")], 1)

    def test_visual_segment_outside_the_targets_raises(self):
        segments = [_script_plan_seg("E1S01", "甲"), _script_plan_seg("E1S02", "乙")]
        targets = _segment_targets(segments, ["E1S01"])
        with pytest.raises(ValueError, match="E1S02"):
            ScriptGenerator._merge_visual_layer(targets, [_visual_seg("E1S01"), _visual_seg("E1S02")], 1)

    def test_duplicate_visual_segment_raises(self):
        segments = [_script_plan_seg("E1S01", "甲")]
        targets = _segment_targets(segments, ["E1S01"])
        with pytest.raises(ValueError, match="E1S01"):
            ScriptGenerator._merge_visual_layer(targets, [_visual_seg("E1S01"), _visual_seg("E1S01")], 1)


class TestLoadNarrationScriptPlan:
    """Read/validate structured intermediate script_plan_segments.json."""

    @staticmethod
    def _script_plan_path(sg: ScriptGenerator, episode: int) -> Path:
        return sg.project_path / "drafts" / f"episode_{episode}" / "script_plan_segments.json"

    def _write(self, sg: ScriptGenerator, episode: int, payload: dict) -> None:
        path = self._script_plan_path(sg, episode)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _activate_project_artifacts(sg.project_path, episode)

    def test_loads_structured_segments_verbatim(self, tmp_path):
        sg = _bare_generator(tmp_path)
        self._write(
            sg,
            1,
            {
                "episode": 1,
                "segments": [
                    _script_plan_seg("E1S01", "第一段原文，逐字保留。", duration=6, brk=True),
                    _script_plan_seg("E1S02", "第二段原文！"),
                ],
            },
        )
        segments = sg._load_narration_script_plan(1, [4, 6, 8])
        assert [s["segment_id"] for s in segments] == ["E1S01", "E1S02"]
        assert segments[0]["novel_text"] == "第一段原文，逐字保留。"
        assert segments[0]["duration_seconds"] == 6
        assert segments[0]["segment_break"] is True

    def test_quarantined_script_plan_blocks_prompt_authoring_even_with_a_valid_formal_file(self, tmp_path):
        """Direct calls also reject while drafts exist: formal content is still the previous version, so authoring would silently replace pending output with old content. Tools share this guard; protect script/test direct callers too."""
        from lib.script.draft_quarantine import QUARANTINE_KIND_NARRATION_SCRIPT_PLAN, write_quarantine

        sg = _bare_generator(tmp_path)
        self._write(sg, 1, {"episode": 1, "segments": [_script_plan_seg("E1S01", "第一段原文。")]})
        write_quarantine(
            sg.project_path,
            1,
            QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
            content={"segments": [_script_plan_seg("E1S01", "改到一半的原文。")]},
            violations=[],
        )

        with pytest.raises(ValueError, match="草稿待处置"):
            sg._load_narration_script_plan(1, [4, 6, 8])

    def test_missing_json_without_legacy_md_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        with pytest.raises(FileNotFoundError, match=re.escape("script_plan_segments.json")):
            sg._load_narration_script_plan(1, [4, 6, 8])

    def test_legacy_md_only_raises_rerun_hint(self, tmp_path):
        """Legacy pre-structured script_plan_segments.md explicitly requires rerunning splitting; never read old Markdown."""
        sg = _bare_generator(tmp_path)
        legacy = sg.project_path / "drafts" / "episode_1" / "script_plan_segments.md"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("| 片段 | 原文 |\n| G01 | 旧表 |", encoding="utf-8")
        with pytest.raises(FileNotFoundError, match="generate_script_plan"):
            sg._load_narration_script_plan(1, [4, 6, 8])

    def test_malformed_json_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        path = self._script_plan_path(sg, 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match=r"script_plan_segments\.json 解析失败"):
            sg._load_narration_script_plan(1, [4, 6, 8])

    def test_invalid_structure_missing_novel_text_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        self._write(sg, 1, {"segments": [{"segment_id": "E1S01", "duration_seconds": 4}]})
        with pytest.raises(ValueError, match=r"script_plan_segments\.json 结构校验失败"):
            sg._load_narration_script_plan(1, [4, 6, 8])

    def test_duplicate_segment_id_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        self._write(sg, 1, {"segments": [_script_plan_seg("E1S01", "甲"), _script_plan_seg("E1S01", "乙")]})
        with pytest.raises(ValueError, match=r"segment_id 重复"):
            sg._load_narration_script_plan(1, [4, 6, 8])

    def test_post_rewrite_collision_raises(self, tmp_path):
        """Distinct IDs colliding after episode-prefix rewriting (E1S02 / E2S02 both E2S02 in episode2) fail loudly."""
        sg = _bare_generator(tmp_path)
        self._write(sg, 2, {"segments": [_script_plan_seg("E1S02", "甲"), _script_plan_seg("E2S02", "乙")]})
        with pytest.raises(ValueError, match=r"segment_id 改写到 episode=2 后重复"):
            sg._load_narration_script_plan(2, [4, 6, 8])

    def test_duration_outside_supported_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        self._write(sg, 1, {"segments": [_script_plan_seg("E1S01", "甲", duration=5)]})  # 5 ∉ [4,6,8]
        with pytest.raises(ValueError, match="duration"):
            sg._load_narration_script_plan(1, [4, 6, 8])

    def test_empty_segments_raises(self, tmp_path):
        sg = _bare_generator(tmp_path)
        self._write(sg, 1, {"segments": []})
        with pytest.raises(ValueError, match=r"script_plan_segments\.json segments 为空"):
            sg._load_narration_script_plan(1, [4, 6, 8])

    def test_missing_asset_arrays_raises(self, tmp_path):
        """Plan asset fields characters_in_segment/scenes/props are required; omissions fail loudly without implicit []."""
        sg = _bare_generator(tmp_path)
        self._write(sg, 1, {"segments": [{"segment_id": "E1S01", "novel_text": "甲", "duration_seconds": 4}]})
        with pytest.raises(ValueError, match=r"script_plan_segments\.json 结构校验失败"):
            sg._load_narration_script_plan(1, [4, 6, 8])

    def test_explicit_empty_asset_arrays_pass(self, tmp_path):
        """Explicit empty asset lists [] are valid."""
        sg = _bare_generator(tmp_path)
        self._write(sg, 1, {"segments": [_script_plan_seg("E1S01", "甲", characters=[], scenes=[], props=[])]})
        segments = sg._load_narration_script_plan(1, [4, 6, 8])
        assert segments[0]["characters_in_segment"] == []


class TestLoadReferenceScriptPlan:
    """Read/validate structured intermediate script_plan_reference_units.json."""

    @staticmethod
    def _script_plan_path(sg: ScriptGenerator, episode: int) -> Path:
        return sg.project_path / "drafts" / f"episode_{episode}" / "script_plan_reference_units.json"

    @staticmethod
    def _generator(tmp_path: Path, project_extra: dict | None = None) -> ScriptGenerator:
        """Reference-video projects locate/register canonical script_plan by generation_mode."""
        return _bare_generator(tmp_path, {"generation_mode": "reference_video", **(project_extra or {})})

    def _write(self, sg: ScriptGenerator, episode: int, payload: dict) -> None:
        path = self._script_plan_path(sg, episode)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _activate_project_artifacts(sg.project_path, episode)

    @staticmethod
    def _unit(unit_id: str, *, duration: int = 6) -> dict:
        return {"unit_id": unit_id, "text": "甲走进屋子", "duration_seconds": duration}

    def test_loads_structured_units_verbatim(self, tmp_path):
        sg = self._generator(tmp_path)
        self._write(sg, 1, {"units": [self._unit("E1U01"), self._unit("E1U02", duration=8)]})
        units = sg._load_reference_script_plan(1, [4, 6, 8])
        assert [u["unit_id"] for u in units] == ["E1U01", "E1U02"]

    def test_duplicate_unit_id_raises(self, tmp_path):
        sg = self._generator(tmp_path)
        self._write(sg, 1, {"units": [self._unit("E1U01"), self._unit("E1U01")]})
        with pytest.raises(ValueError, match=r"unit_id 重复"):
            sg._load_reference_script_plan(1, [4, 6, 8])

    def test_post_rewrite_collision_raises(self, tmp_path):
        """Distinct unit IDs colliding after episode rewriting (E1U01 / E2U01 → E2U01 in episode2) fail loudly. _add_metadata unconditionally rewrites E\\d+ before persistence; catch mixed LLM episode prefixes at plan reads rather than silently persisting collisions, like narration/drama loaders."""
        sg = self._generator(tmp_path)
        self._write(sg, 2, {"units": [self._unit("E1U01"), self._unit("E2U01")]})
        with pytest.raises(ValueError, match=r"unit_id 改写到 episode=2 后重复"):
            sg._load_reference_script_plan(2, [4, 6, 8])

    def test_loading_preserves_duration_for_per_unit_admission(self, tmp_path):
        sg = self._generator(tmp_path)
        self._write(sg, 1, {"units": [self._unit("E1U01", duration=5)]})
        units = sg._load_reference_script_plan(1, [4, 6, 8])
        assert units[0]["duration_seconds"] == 5

    def test_strips_retired_duration_override_marker_and_persists(self, tmp_path):
        """Strip retired ``duration_override`` from legacy drafts and write back in place; second loads do not repeat."""
        sg = self._generator(tmp_path)
        self._write(
            sg,
            1,
            {
                "units": [
                    {
                        "unit_id": "E1U01",
                        "text": "甲起身\n甲出门",
                        "duration_seconds": 4,
                        "duration_override": True,
                    }
                ]
            },
        )
        units = sg._load_reference_script_plan(1, [4, 6, 8])
        assert units[0]["duration_seconds"] == 4
        assert units[0]["text"] == "甲起身\n甲出门"

        on_disk = json.loads(self._script_plan_path(sg, 1).read_text(encoding="utf-8"))
        assert on_disk["units"][0]["duration_seconds"] == 4
        assert "duration_override" not in on_disk["units"][0]

        # Idempotent: second loads do not rewrite persisted content.
        before_second_load = self._script_plan_path(sg, 1).read_bytes()
        sg._load_reference_script_plan(1, [4, 6, 8])
        assert self._script_plan_path(sg, 1).read_bytes() == before_second_load

    def test_migration_clamps_sum_to_supported_slot(self, tmp_path, caplog):
        """Out-of-tier existing durations select by capacity semantics (here7→8), persist and warn."""
        sg = self._generator(tmp_path)
        self._write(
            sg,
            1,
            {
                "units": [
                    {
                        "unit_id": "E1U01",
                        "text": "甲起身\n甲出门",
                        "duration_seconds": 7,
                        "duration_override": True,
                    }
                ]
            },
        )
        with caplog.at_level(logging.WARNING):
            units = sg._load_reference_script_plan(1, [4, 6, 8])
        assert units[0]["duration_seconds"] == 8
        assert any("时长收编迁移" in r.message for r in caplog.records)

        on_disk = json.loads(self._script_plan_path(sg, 1).read_text(encoding="utf-8"))
        assert on_disk["units"][0]["duration_seconds"] == 8
        assert "duration_override" not in on_disk["units"][0]

    def test_empty_units_raises(self, tmp_path):
        sg = self._generator(tmp_path)
        self._write(sg, 1, {"units": []})
        with pytest.raises(ValueError, match=r"script_plan_reference_units\.json units 为空"):
            sg._load_reference_script_plan(1, [4, 6, 8])


def _write_ad_project(project_path: Path, *, generation_mode: str = "storyboard", products: dict | None = None):
    payload = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "title": "速干杯",
        "content_mode": "ad",
        "generation_mode": generation_mode,
        "target_duration": 30,
        "brief": "突出速干卖点",
        "overview": {"synopsis": "带货短片"},
        "characters": {"小美": {"description": "白领"}},
        "scenes": {},
        "props": {},
        "products": products
        if products is not None
        else {"速干杯": {"description": "随行杯", "selling_points": ["30 秒速干"]}},
        "style": "实拍",
        "style_description": "真实质感",
        "aspect_ratio": "9:16",
        "episodes": [{"episode": 1, "title": "", "script_file": "scripts/episode_1.json"}],
    }
    _write_json(project_path / "project.json", payload)


def _ad_shot(shot_id: str, *, duration: int = 4, section: str = "hook", voiceover: str = "口播") -> dict:
    return {
        "shot_id": shot_id,
        "section": section,
        "duration_seconds": duration,
        "voiceover_text": voiceover,
        "characters_in_shot": [],
        "scenes": [],
        "props": [],
        "products_in_shot": ["速干杯"],
        "image_prompt": {
            "scene": "速干杯特写" * 10,
            "composition": {"shot_type": "Close-up", "lighting": "柔和顶光", "ambiance": "清爽"},
        },
        "video_prompt": {
            "action": "水珠从杯壁滑落，杯身迅速恢复干爽" * 2,
            "camera_motion": "Static",
            "ambiance_audio": "水声",
            "dialogue": [],
        },
    }


class TestAdScriptGeneration:
    async def test_build_prompt_without_script_plan_uses_brief_and_products(self, tmp_path, video_request_facts):
        """Whole ad generation bypasses planning intermediates; prompts use brief + products + ratio table directly."""
        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path)

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        assert "突出速干卖点" in prompt
        assert "速干杯" in prompt

    async def test_build_prompt_reference_path_uses_free_duration(self, tmp_path, video_request_facts):
        """ad + reference_video outputs unified reference-syntax video_units without persisting legacy shot fields."""
        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path, generation_mode="reference_video")

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        assert "突出速干卖点" in prompt
        assert "速干杯" in prompt
        assert "video unit" in prompt
        assert "Do not output shots, section, shot_id" in prompt
        assert "@[名称]" in prompt

    async def test_build_prompt_uses_project_source_language(self, tmp_path, video_request_facts):
        """Ad speech-rate conversion/output language follows project source_language like drama/narration, never reverting non-Chinese projects to Chinese/zh rates."""
        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path)
        project_json_path = project_path / "project.json"
        payload = json.loads(project_json_path.read_text(encoding="utf-8"))
        payload["source_language"] = "en"
        _write_json(project_json_path, payload)

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        # Output language follows project source_language, never default Chinese.
        assert "all string values must use en" in prompt
        assert "all string values must use 中文" not in prompt

    async def test_build_prompt_uses_project_speech_rate_override(self, tmp_path, video_request_facts):
        """Project speech_rate_units_per_second overrides language defaults authoritatively in ad speech conversion."""
        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path)
        project_json_path = project_path / "project.json"
        payload = json.loads(project_json_path.read_text(encoding="utf-8"))
        payload["speech_rate_units_per_second"] = 7.5
        _write_json(project_json_path, payload)

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        assert "length estimated at approximately 7.5 字/second" in prompt

    @pytest.mark.parametrize("generation_mode", ["storyboard", "reference_video"])
    async def test_build_prompt_ends_with_optional_instructions_section(
        self, tmp_path, generation_mode, video_request_facts
    ):
        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path, generation_mode=generation_mode)
        generator = ScriptGenerator(project_path)

        plain = await generator.build_prompt(1)
        prompt = await generator.build_prompt(1, instructions="结尾给商品特写")

        assert "# Additional instructions" not in plain
        assert prompt.endswith("\n\n# Additional instructions\n结尾给商品特写")
        assert "\n\n\n# Additional instructions" not in prompt

    async def test_build_prompt_tolerates_null_project_fields(self, tmp_path, video_request_facts):
        """Explicit null after manual project.json edits normalizes empty during prompt building without AttributeError."""
        project_path = tmp_path / "projects" / "demo"
        _write_project_json(
            project_path,
            {
                "title": "速干杯",
                "content_mode": "ad",
                "generation_mode": "storyboard",
                "target_duration": 30,
                "brief": None,
                "overview": None,
                "characters": None,
                "scenes": None,
                "props": None,
                "products": None,
                "style": None,
                "style_description": None,
                "aspect_ratio": "9:16",
                "episodes": [{"episode": 1, "title": "", "script_file": "scripts/episode_1.json"}],
            },
        )

        generator = ScriptGenerator(project_path)
        prompt = await generator.build_prompt(1)

        assert isinstance(prompt, str)
        assert prompt

    async def test_generate_writes_ad_script_with_metadata(self, tmp_path, video_request_facts):
        """Persist generated ad scripts with shots skeleton and content_mode=ad."""
        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path)

        response = {
            "title": "速干杯短片",
            "shots": [
                _ad_shot("E1S01", duration=4, section="hook", voiceover="还在等杯子干？"),
                _ad_shot("E1S02", duration=6, section="demo", voiceover="30 秒，倒扣即干。"),
            ],
        }
        fake = _FakeTextGenerator(json.dumps(response, ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)

        output_path = await generator.generate(1)

        saved = json.loads(output_path.read_text(encoding="utf-8"))
        assert saved["content_mode"] == "ad"
        assert saved["episode"] == 1
        assert [s["shot_id"] for s in saved["shots"]] == ["E1S01", "E1S02"]
        assert saved["shots"][0]["voiceover_text"] == "还在等杯子干？"

    async def test_generate_ad_storyboard_passes_enum_schema(self, tmp_path, video_request_facts):
        """ad + storyboard response_schema subclasses AdEpisodeScript with duration enums."""
        from lib.script.script_models import AdEpisodeScript

        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path)
        fake = _FakeTextGenerator(json.dumps({"foo": "bar"}))
        generator = ScriptGenerator(project_path, generator=fake)

        with pytest.raises(AdScriptRejected):
            await generator.generate(1)

        schema = fake.backend.last_request.response_schema
        assert isinstance(schema, type)
        assert issubclass(schema, AdEpisodeScript)
        duration_enums = [
            props["duration_seconds"].get("enum")
            for props in (d.get("properties", {}) for d in schema.model_json_schema().get("$defs", {}).values())
            if "duration_seconds" in props
        ]
        assert [4, 6, 8] in duration_enums

    async def test_generate_ad_reference_passes_free_range_schema(self, tmp_path, video_request_facts):
        """ad + reference_video response_schema contains only unit durations and unified-reference bodies."""
        from lib.script.script_models import AdReferenceFlatScript

        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path, generation_mode="reference_video")
        fake = _FakeTextGenerator(json.dumps({"foo": "bar"}))
        generator = ScriptGenerator(project_path, generator=fake)

        with pytest.raises(ValueError, match="广告参考剧本结构校验失败"):
            await generator.generate(1)

        schema = fake.backend.last_request.response_schema
        assert schema is AdReferenceFlatScript
        field_schemas = [
            props["duration_seconds"]
            for props in (d.get("properties", {}) for d in schema.model_json_schema().get("$defs", {}).values())
            if "duration_seconds" in props
        ]
        assert any(fs.get("minimum") == 1 and fs.get("maximum") == 300 and "enum" not in fs for fs in field_schemas)

    async def test_generate_rewrites_wrong_episode_prefix_on_shot_ids(self, tmp_path, video_request_facts):
        """Wrong LLM episode prefixes rewrite to E1, since ads always have one episode."""
        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path)
        response = {
            "title": "速干杯短片",
            "shots": [_ad_shot("E3S01", duration=4)],
        }
        fake = _FakeTextGenerator(json.dumps(response, ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)

        output_path = await generator.generate(1)
        saved = json.loads(output_path.read_text(encoding="utf-8"))
        assert saved["shots"][0]["shot_id"] == "E1S01"


class TestAdParseResponseDriftRecovery:
    """Tolerate recoverable output when proxy gateways do not enforce constrained decoding: normalize enum-style drift and supply missing titles.

    Reproduce diagnostic failures from 2026-07-13: Gemini proxies emitted uppercase/lowercase snake enums MEDIUM_SHOT / dolly_in, null dialogue and missing top-level titles; all three generations failed save_script validation. _parse_response normalizes recoverable drift and admits it."""

    @staticmethod
    def _drifted_shot(shot_id: str, shot_type: str, camera_motion: str) -> dict:
        return {
            "shot_id": shot_id,
            "section": "hook",
            "duration_seconds": 3,
            "voiceover_text": "开场口播",
            "image_prompt": {
                "scene": "老伴在广场中央起舞",
                "composition": {"shot_type": shot_type, "lighting": "夕阳侧光", "ambiance": "人群环绕"},
            },
            "video_prompt": {
                "action": "旋转舞动",
                "camera_motion": camera_motion,
                "ambiance_audio": "广场音乐",
                "dialogue": None,
            },
        }

    def test_parse_response_recovers_drifted_payload_without_title(self, tmp_path):
        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path)
        generator = ScriptGenerator(project_path)

        llm_response = json.dumps(
            {
                "shots": [
                    self._drifted_shot("E1S01", "MEDIUM_SHOT", "ZOOM_OUT"),
                    self._drifted_shot("E1S02", "wide_shot", "dolly_in"),
                ]
            },
            ensure_ascii=False,
        )
        parsed = generator._parse_response(llm_response, 1)

        # Empty ledger titles stay empty, never derived episode-N labels.
        assert parsed["title"] == ""
        first, second = parsed["shots"]
        assert first["image_prompt"]["composition"]["shot_type"] == "Medium Shot"
        assert first["video_prompt"]["camera_motion"] == "Zoom Out"
        assert first["video_prompt"]["dialogue"] == []
        # Out-of-vocabulary wide_shot / dolly_in use neutral defaults, never semantic remapping.
        assert second["image_prompt"]["composition"]["shot_type"] == "Medium Shot"
        assert second["video_prompt"]["camera_motion"] == "Static"

    def test_parse_response_keeps_model_title_when_present(self, tmp_path):
        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path)
        generator = ScriptGenerator(project_path)

        llm_response = json.dumps(
            {"title": "速干杯广场舞", "shots": [self._drifted_shot("E1S01", "Medium Shot", "Static")]},
            ensure_ascii=False,
        )
        parsed = generator._parse_response(llm_response, 1)
        assert parsed["title"] == "速干杯广场舞"


class TestAdQualityProbe:
    """Ad total-duration deviation probes log WARN only, without blocking or frontend notification."""

    def _sg(self, tmp_path, *, target_duration: int = 30) -> ScriptGenerator:
        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path)
        sg = ScriptGenerator.__new__(ScriptGenerator)
        sg.generator = None
        sg.project_path = project_path
        sg.project_json = {
            "content_mode": "ad",
            "target_duration": target_duration,
            "generation_mode": "storyboard",
        }
        sg.content_mode = "ad"
        return sg

    def _script(self, durations: list[int]) -> dict:
        return {"shots": [_ad_shot(f"E1S{i:02d}", duration=d) for i, d in enumerate(durations, start=1)]}

    def test_drift_above_threshold_warns(self, tmp_path, caplog):
        sg = self._sg(tmp_path, target_duration=30)
        with caplog.at_level("WARNING", logger="lib.script.script_generator"):
            sg._quality_probe(self._script([4, 4]), episode=1)  # Eight versus thirty seconds.
        assert any("target_duration drift" in r.message for r in caplog.records)

    def test_drift_within_threshold_silent(self, tmp_path, caplog):
        sg = self._sg(tmp_path, target_duration=30)
        with caplog.at_level("WARNING", logger="lib.script.script_generator"):
            sg._quality_probe(self._script([4, 6, 6, 6, 4, 6]), episode=1)  # Thirty-two versus thirty seconds.
        assert not any("target_duration drift" in r.message for r in caplog.records)

    def test_short_prompt_probe_covers_shots(self, tmp_path, caplog):
        sg = self._sg(tmp_path)
        script = self._script([4])
        script["shots"][0]["image_prompt"]["scene"] = "短"
        with caplog.at_level("WARNING", logger="lib.script.script_generator"):
            sg._quality_probe(script, episode=1)
        assert any("quality probe" in r.message and "E1S01" in r.message for r in caplog.records)

    async def test_save_not_blocked_by_drift(self, tmp_path, video_request_facts, caplog):
        """Duration deviation above thresholds still saves; probes WARN only, never raise/refuse."""
        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path)
        response = {"title": "短片", "shots": [_ad_shot("E1S01", duration=4)]}  # Four versus thirty seconds.
        fake = _FakeTextGenerator(json.dumps(response, ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)

        with caplog.at_level("WARNING", logger="lib.script.script_generator"):
            output_path = await generator.generate(1)

        assert output_path.exists()
        assert any("target_duration drift" in r.message for r in caplog.records)


class TestAdAspectRatioFallback:
    def test_ad_without_aspect_ratio_falls_back_to_portrait(self, tmp_path):
        """Ad projects lacking aspect_ratio default to vertical9:16, matching creation wizards."""
        sg = ScriptGenerator.__new__(ScriptGenerator)
        sg.generator = None
        sg.project_path = tmp_path
        sg.project_json = {"content_mode": "ad"}
        sg.content_mode = "ad"
        assert sg._resolve_aspect_ratio() == "9:16"


class TestAdReferenceSkeletonUnity:
    """ad + reference_video outputs self-contained video_units without generation-mode markers."""

    async def test_generate_ad_reference_script_carries_no_generation_mode(self, tmp_path):
        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path, generation_mode="reference_video")
        response = {
            "title": "速干杯短片",
            "units": [
                {"duration_seconds": 7, "text": "镜头1：@[速干杯] 表面的水珠迅速滑落"},
                {"duration_seconds": 5, "text": "镜头1：@[小美] 举起 @[速干杯]\n@[小美]：{现在就试试。}"},
            ],
        }
        fake = _FakeTextGenerator(json.dumps(response, ensure_ascii=False))
        generator = ScriptGenerator(project_path, generator=fake)

        output_path = await generator.generate(1)
        saved = json.loads(output_path.read_text(encoding="utf-8"))

        assert "generation_mode" not in saved
        assert saved["content_mode"] == "ad"
        assert "shots" not in saved
        assert "reference_units" not in saved
        assert [unit["unit_id"] for unit in saved["video_units"]] == ["E1U1", "E1U2"]
        # References are never persisted: bodies are authoritative; readers derive `@[名称]` references.
        assert "references" not in saved["video_units"][0]
        assert "@[速干杯]" in saved["video_units"][0]["text"]

    async def test_generate_ad_reference_preserves_mixed_speech_and_marks_replan(self, tmp_path):
        project_path = tmp_path / "projects" / "demo"
        _write_ad_project(project_path, generation_mode="reference_video")
        text = "镜头1：@[小美] 举起 @[速干杯]\n@[小美]：{试试这一杯。}\n{旁白补充卖点。}"
        fake = _FakeTextGenerator(
            json.dumps({"title": "混合发声", "units": [{"duration_seconds": 8, "text": text}]}, ensure_ascii=False)
        )
        generator = ScriptGenerator(project_path, generator=fake)

        output_path = await generator.generate(1)
        unit = json.loads(output_path.read_text(encoding="utf-8"))["video_units"][0]

        assert unit["needs_replan"] is True
        assert "@[小美]：{试试这一杯。}" in unit["text"]
        assert "{旁白补充卖点。}" in unit["text"]
