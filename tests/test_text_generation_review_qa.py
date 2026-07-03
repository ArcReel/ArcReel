from pathlib import Path

import lib.script_review as script_review
from lib.json_io import atomic_write_json
from lib.project_manager import ProjectManager


def _make_project(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "drama")
    pm.add_character("demo", "阿离", "少女")
    pm.add_project_scene("demo", "屋檐", "雨夜屋檐")
    pm.add_prop("demo", "信纸", "关键证据")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    return pm


def _step1_with_missing_prop() -> dict:
    return {
        "title": "第一集",
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "segment_break": False,
                "characters_in_scene": ["阿离"],
                "scenes": ["屋檐"],
                "props": ["信纸", "玉佩"],
                "scene_description": "突然，阿离在屋檐下拆开信纸，脸色骤变！",
                "utterances": [{"kind": "dialogue", "speaker": "阿离", "text": "真相？"}],
                "source_text": "突然，阿离拆开信纸，发现真相？",
            }
        ],
    }


def _write_step1(pm: ProjectManager, content: dict) -> None:
    drafts = pm.get_project_path("demo") / "drafts" / "episode_1"
    drafts.mkdir(parents=True, exist_ok=True)
    atomic_write_json(drafts / "step1_normalized_script.json", content)


def _write_step2(pm: ProjectManager) -> None:
    scripts = pm.get_project_path("demo") / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    atomic_write_json(scripts / "episode_1.json", {"title": "第一集", "scenes": []})


async def test_generate_episode_script_block_message_includes_qa_summary(tmp_path: Path) -> None:
    from server.agent_runtime.sdk_tools._context import ToolContext
    from server.agent_runtime.sdk_tools.text_generation import generate_episode_script_tool

    pm = _make_project(tmp_path)
    _write_step1(pm, _step1_with_missing_prop())

    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    result = await generate_episode_script_tool(ctx).handler({"episode": 1})

    assert result.get("is_error") is True
    text = result["content"][0]["text"]
    assert "QA 摘要" in text
    assert "block=1" in text
    assert "missing_prop_reference" in text
    assert "玉佩" in text


async def test_generate_episode_script_blocks_grandfathered_qa_blocks(tmp_path: Path) -> None:
    from server.agent_runtime.sdk_tools._context import ToolContext
    from server.agent_runtime.sdk_tools.text_generation import generate_episode_script_tool

    pm = _make_project(tmp_path)
    _write_step1(pm, _step1_with_missing_prop())
    _write_step2(pm)
    project_path = pm.get_project_path("demo")

    assert script_review.review_status(project_path, pm.load_project("demo"), 1) == "confirmed"
    assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is True
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    result = await generate_episode_script_tool(ctx).handler({"episode": 1})

    assert result.get("is_error") is True
    text = result["content"][0]["text"]
    assert "step1 QA" in text
    assert "missing_prop_reference" in text
    assert "玉佩" in text


async def test_confirm_script_review_tool_cannot_bypass_qa_block(tmp_path: Path) -> None:
    from server.agent_runtime.sdk_tools._context import ToolContext
    from server.agent_runtime.sdk_tools.text_generation import confirm_script_review_tool

    pm = _make_project(tmp_path)
    _write_step1(pm, _step1_with_missing_prop())
    project_path = pm.get_project_path("demo")

    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    result = await confirm_script_review_tool(ctx).handler({"episode": 1})

    assert result.get("is_error") is True
    text = result["content"][0]["text"]
    assert "qa_gate_blocked" in text
    assert "QA 摘要" in text
    assert "missing_prop_reference" in text
    assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is True
    assert "step1_review" not in pm.load_project("demo")["episodes"][0]
