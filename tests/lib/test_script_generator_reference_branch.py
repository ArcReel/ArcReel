"""ScriptGenerator reference_video 分支测试。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

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


def test_script_generator_build_prompt_selects_reference_branch(reference_project: Path):
    """当 generation_mode == reference_video 时，build_prompt 必须走 reference 分支。"""
    gen = ScriptGenerator(reference_project)
    prompt = gen.build_prompt(episode=1)
    # reference 分支特征标签
    assert "video_units" in prompt
    assert "@名称" in prompt
    # 不应出现 narration / drama 特征
    assert "characters_in_segment" not in prompt


def test_script_generator_reads_step1_reference_units(reference_project: Path):
    gen = ScriptGenerator(reference_project)
    prompt = gen.build_prompt(episode=1)
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


def test_effective_generation_mode_honors_episode_override(tmp_path: Path):
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
    prompt = gen.build_prompt(episode=1)
    # 走 reference 分支
    assert "video_units" in prompt
    assert "@名称" in prompt
