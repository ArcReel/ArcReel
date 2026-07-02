"""step1 / episode 路径单一真相源的行为测试。

只测外部可观察契约：结构化 step1 文件名解析、旧版 .md 兼认边界、episode 剧本路径，
以及"新增 content_mode 登记一处即被 gate / web / 状态计算共同覆盖"这一收敛不变量。
"""

from __future__ import annotations

from pathlib import Path

from lib import episode_paths, script_review, status_calculator
from server.agent_runtime.sdk_tools import text_generation
from server.routers import files


def test_step1_filename_by_content_mode():
    assert episode_paths.step1_filename("drama") == "step1_normalized_script.json"
    assert episode_paths.step1_filename("narration") == "step1_segments.json"
    # ad 不走结构化 step1
    assert episode_paths.step1_filename("ad") is None
    assert episode_paths.step1_filename("unknown") is None


def test_step1_read_candidates_includes_legacy_md():
    assert episode_paths.step1_read_candidates("narration") == (
        "step1_segments.json",
        "step1_segments.md",
    )
    assert episode_paths.step1_read_candidates("drama") == (
        "step1_normalized_script.json",
        "step1_normalized_script.md",
    )
    assert episode_paths.step1_read_candidates("ad") == ()


def test_episode_script_paths():
    assert episode_paths.episode_script_filename(3) == "episode_3.json"
    assert episode_paths.episode_script_relpath(3) == "scripts/episode_3.json"


def test_episode_drafts_dir():
    assert episode_paths.episode_drafts_dir(Path("/p"), 2) == Path("/p/drafts/episode_2")


def test_new_content_mode_registered_once_covers_gate_web_and_status(monkeypatch, tmp_path):
    """在 STEP1_FILENAMES 登记一处新模式，gate 路径、web 步骤文件、状态草稿探测应自动一致。"""
    monkeypatch.setitem(episode_paths.STEP1_FILENAMES, "docudrama", "step1_docu.json")

    # 审核 gate：step1_path 指向登记的结构化文件名
    project = {"content_mode": "docudrama", "episodes": [{"episode": 1}]}
    gate_path = script_review.step1_path(tmp_path, project, 1)
    assert gate_path is not None
    assert gate_path == tmp_path / "drafts" / "episode_1" / "step1_docu.json"

    # web 草稿读写：_get_step_files 返回同一文件名
    assert files._get_step_files("docudrama") == {1: "step1_docu.json"}

    # agent 写盘：_resolve_step1_path 指向同一结构化文件名，不因 == "drama" 硬编码误落 narration
    resolved = text_generation._resolve_step1_path(tmp_path, 1, project)
    assert resolved is not None
    assert resolved[0] == tmp_path / "drafts" / "episode_1" / "step1_docu.json"

    # 状态计算：新模式的草稿探测覆盖登记的结构化文件名
    assert "step1_docu.json" in status_calculator._draft_candidates("docudrama")


def test_ad_has_no_structured_step1_across_web_and_agent(tmp_path):
    """ad 不走结构化 step1：web 步骤映射为空、agent 写盘解析为 None（与状态计算显式排除同口径）。"""
    # web 草稿读写：ad 不误落 drama 文件名，返回空映射；ad 优先于 generation_mode，
    # 带 reference_video 戳同样无 step1（与 _resolve_step1_path 先判 ad 同序）
    assert files._get_step_files("ad") == {}
    assert files._get_step_files("ad", generation_mode="reference_video") == {}
    # agent 写盘：ad 不依赖 step1
    assert (
        text_generation._resolve_step1_path(tmp_path, 1, {"content_mode": "ad", "episodes": [{"episode": 1}]}) is None
    )
    # 状态计算：ad 无草稿可探测
    assert status_calculator._draft_candidates("ad") == ()


def test_gate_only_json_status_and_web_also_md():
    """gate 只认结构化 .json；状态计算与 web 读取兼认旧版 .md（既有语义差异）。"""
    # gate 的登记表不含任何 .md
    assert all(name.endswith(".json") for name in episode_paths.STEP1_FILENAMES.values())
    # web 读取候选含旧 .md
    assert "step1_segments.md" in episode_paths.step1_read_candidates("narration")
    # 状态计算：narration 旧 .md 认作已分段，drama 旧 .md 不认（见 ADR 0041）
    assert "step1_segments.md" in status_calculator._draft_candidates("narration")
    assert "step1_normalized_script.md" not in status_calculator._draft_candidates("drama")
