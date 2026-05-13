"""ScriptGenerator reference_video 分支测试。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from lib.script_generator import ScriptGenerator


@pytest.fixture
def reference_project(tmp_path: Path) -> Path:
    """造一个 reference_video 模式的最小项目。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        """{
          "title": "t",
          "content_mode": "narration",
          "generation_mode": "reference_video",
          "_supported_durations": [4, 8],
          "overview": {"synopsis": "s", "genre": "g", "theme": "th", "world_setting": "w"},
          "style": "国漫",
          "style_description": "水墨",
          "characters": {"主角": {"description": "d"}},
          "scenes": {"酒馆": {"description": "d"}},
          "props": {},
          "episodes": [{"episode": 1, "title": "t1", "generation_mode": "reference_video"}]
        }""",
        encoding="utf-8",
    )
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_reference_units.md").write_text(
        "| unit | shots | refs |\n| E1U1 | Shot1(4s) | 主角,酒馆 |\n",
        encoding="utf-8",
    )
    return project_dir


@pytest.mark.asyncio
async def test_script_generator_build_prompt_selects_reference_branch(reference_project: Path):
    """当 generation_mode == reference_video 时，build_prompt 必须走 reference 分支。"""
    gen = ScriptGenerator(reference_project)
    prompt = await gen.build_prompt(episode=1)
    # reference 分支特征标签
    assert "ReferenceVideoScript" in prompt
    assert "references" in prompt
    assert "@名称" in prompt
    # 不应出现 narration / drama 特征
    assert "characters_in_segment" not in prompt


@pytest.mark.asyncio
async def test_script_generator_reads_step1_reference_units(reference_project: Path):
    gen = ScriptGenerator(reference_project)
    prompt = await gen.build_prompt(episode=1)
    # step1_reference_units.md 的内容必须透传
    assert "E1U1" in prompt


@pytest.mark.asyncio
async def test_script_generator_uses_reference_schema_on_generate(reference_project: Path):
    """_parse_response 在 reference 模式下用 ReferenceVideoScript 校验。"""
    from lib.script_models import ReferenceVideoScript

    fake_generator = MagicMock()
    fake_generator.model = "mock"
    fake_generator.generate = AsyncMock(
        return_value=MagicMock(
            text=(
                '{"episode":1,"title":"t","content_mode":"reference_video",'
                '"summary":"s","novel":{"title":"t","chapter":"1"},'
                '"video_units":[{"unit_id":"E1U1",'
                '"shots":[{"duration":4,"text":"@主角 推门"}],'
                '"references":[{"type":"character","name":"主角"}],'
                '"duration_seconds":4,"duration_override":false,"transition_to_next":"cut"}]}'
            )
        )
    )

    gen = ScriptGenerator(reference_project, generator=fake_generator)

    out = await gen.generate(episode=1)
    assert out.exists()
    import json as _j

    data = _j.loads(out.read_text(encoding="utf-8"))
    assert data["content_mode"] == "reference_video"
    assert len(data["video_units"]) == 1

    # 确认生成时用了 ReferenceVideoScript schema
    call_kwargs = fake_generator.generate.await_args.args[0]
    assert call_kwargs.response_schema is ReferenceVideoScript


@pytest.mark.parametrize(
    "video_backend, expected",
    [
        ("gemini-aistudio/veo-3.1-generate-preview", 3),
        ("gemini-vertex/veo-3.1", 3),
        ("openai/sora-2", 1),
        ("grok/grok-imagine-video", 7),
        ("ark/seedance-2.0", 9),
        (None, 9),
        ("unknown/xyz", 9),
    ],
)
def test_resolve_max_refs_by_provider(tmp_path: Path, video_backend, expected):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    import json as _j

    project = {
        "title": "t",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "overview": {},
        "style": "",
        "style_description": "",
        "characters": {},
        "scenes": {},
        "props": {},
    }
    if video_backend is not None:
        project["video_backend"] = video_backend
    (project_dir / "project.json").write_text(_j.dumps(project), encoding="utf-8")

    gen = ScriptGenerator(project_dir)
    assert gen._resolve_max_refs() == expected


@pytest.mark.parametrize(
    "video_backend, expected_max_duration_sec",
    [
        ("grok/grok-imagine-video", "15"),
        ("gemini-aistudio/veo-3.1-generate-preview", "8"),
        ("ark/doubao-seedance-2-0-260128", "15"),
    ],
)
@pytest.mark.asyncio
async def test_build_prompt_injects_max_duration_from_registry(
    tmp_path: Path, video_backend: str, expected_max_duration_sec: str
):
    """build_prompt 的 reference 分支应基于 project.json.video_backend 的 model 能力派生 max_duration。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    import json as _j

    (project_dir / "project.json").write_text(
        _j.dumps(
            {
                "title": "t",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "video_backend": video_backend,
                "overview": {"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
                "style": "s",
                "style_description": "d",
                "characters": {},
                "scenes": {},
                "props": {},
                "episodes": [{"episode": 1, "generation_mode": "reference_video"}],
            }
        ),
        encoding="utf-8",
    )
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_reference_units.md").write_text("E1U1 stub", encoding="utf-8")

    gen = ScriptGenerator(project_dir)
    prompt = await gen.build_prompt(episode=1)
    assert f"{expected_max_duration_sec} 秒" in prompt
    assert "当前模型上限" in prompt


@pytest.mark.asyncio
async def test_build_prompt_no_video_backend_raises_value_error(tmp_path: Path):
    """project.json 缺 video_backend 且无 _supported_durations 且 caps 不可解析时，build_prompt 应抛 ValueError。

    设计意图：supported_durations 是单一真相源，必须由 caps（DB 全局默认）或 project.json 显式声明提供；
    都拿不到才 fail loud，避免向 LLM 注入兜底 [4, 8] 误导生成。
    用 mock 把 _fetch_video_capabilities 强制返 None，模拟无任何 model 配置的环境。
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    import json as _j

    (project_dir / "project.json").write_text(
        _j.dumps(
            {
                "title": "t",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "overview": {"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
                "style": "s",
                "style_description": "d",
                "characters": {},
                "scenes": {},
                "props": {},
                "episodes": [{"episode": 1, "generation_mode": "reference_video"}],
            }
        ),
        encoding="utf-8",
    )
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_reference_units.md").write_text("E1U1 stub", encoding="utf-8")

    gen = ScriptGenerator(project_dir)
    with patch(
        "lib.script_generator.ScriptGenerator._fetch_video_capabilities",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(ValueError, match="supported_durations"):
            await gen.build_prompt(episode=1)


@pytest.mark.asyncio
async def test_fetch_video_capabilities_swallows_db_errors(reference_project: Path):
    """CI 回归：裸测试容器缺 migration 时 ConfigResolver 会抛 OperationalError；
    _fetch_video_capabilities 必须 fallback 返 None，不让 generate() 崩溃。
    """
    gen = ScriptGenerator(reference_project)
    with patch(
        "lib.script_generator.ConfigResolver.video_capabilities_for_project",
        new=AsyncMock(side_effect=OperationalError("SELECT ...", {}, Exception("no such table: system_setting"))),
    ):
        caps = await gen._fetch_video_capabilities()
    assert caps is None


@pytest.mark.asyncio
async def test_effective_generation_mode_honors_episode_override(tmp_path: Path):
    """当 project=storyboard 但 episode=reference_video 时，build_prompt 必须走 reference 分支。

    Spec §4.6：`effective_mode(project, episode) = episode.generation_mode or project.generation_mode or "storyboard"`
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    import json as _j

    (project_dir / "project.json").write_text(
        _j.dumps(
            {
                "title": "t",
                "content_mode": "narration",
                "generation_mode": "storyboard",  # 项目级是 storyboard
                "_supported_durations": [4, 8],
                "overview": {"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
                "style": "s",
                "style_description": "d",
                "characters": {"A": {"description": "d"}},
                "scenes": {},
                "props": {},
                "episodes": [
                    {"episode": 1, "generation_mode": "reference_video"},  # 集级覆盖为 reference
                ],
            }
        ),
        encoding="utf-8",
    )
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_reference_units.md").write_text("E1U1 stub", encoding="utf-8")

    gen = ScriptGenerator(project_dir)
    prompt = await gen.build_prompt(episode=1)
    # 走 reference 分支：模板包含 ReferenceVideoScript 与 references 字段说明
    assert "ReferenceVideoScript" in prompt
    assert "references" in prompt
    assert "@名称" in prompt
