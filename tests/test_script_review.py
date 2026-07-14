"""step1→step2 审核 gate 的服务层与纯逻辑测试。

只测外部可观察行为：审核状态流转（step1 产出 → pending → 阻塞 → 确认 → confirmed → 放行）、
适用范围、内容编辑后重新待审、结构校验。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import script_review
from lib.json_io import atomic_write_json
from lib.project_manager import ProjectManager
from server.services.script_review import ScriptReviewError, ScriptReviewService


def _drama_step1() -> dict:
    return {
        "title": "第一集",
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "segment_break": False,
                "characters_in_scene": ["阿离"],
                "scenes": [],
                "props": [],
                "scene_description": "雨夜，阿离立于屋檐下",
                "utterances": [
                    {"kind": "voiceover", "speaker": None, "text": "三年后。"},
                    {"kind": "dialogue", "speaker": "阿离", "text": "你终于回来了。"},
                ],
                "source_text": "三年后，阿离立于屋檐下，轻声道：你终于回来了。",
            }
        ],
    }


def _narration_step1() -> dict:
    return {
        "episode": 1,
        "segments": [
            {
                "segment_id": "E1S01",
                "novel_text": "裴与出征后的第二年，送回一个襁褓中的婴儿。",
                "duration_seconds": 6,
                "segment_break": False,
                "characters_in_segment": ["裴与"],
                "scenes": [],
                "props": [],
            }
        ],
    }


def _make_project(tmp_path: Path, content_mode: str, *, generation_mode: str | None = None) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", content_mode)
    pm.add_character("demo", "阿离", "少女")
    pm.add_character("demo", "裴与", "将军")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    if generation_mode is not None:

        def _set_mode(p: dict) -> None:
            p["generation_mode"] = generation_mode

        pm.update_project("demo", _set_mode)
    return pm


def _write_step1(pm: ProjectManager, content_mode: str, content: dict) -> Path:
    filename = "step1_normalized_script.json" if content_mode == "drama" else "step1_segments.json"
    drafts = pm.get_project_path("demo") / "drafts" / "episode_1"
    drafts.mkdir(parents=True, exist_ok=True)
    path = drafts / filename
    atomic_write_json(path, content)
    return path


def _write_step2(pm: ProjectManager) -> Path:
    """写出 step2 产物（生成的剧本 JSON），模拟「已产 step2」。"""
    scripts = pm.get_project_path("demo") / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    path = scripts / "episode_1.json"
    atomic_write_json(path, {"title": "第一集", "scenes": []})
    return path


def _make_manual_split_project(tmp_path: Path, content_mode: str) -> ProjectManager:
    """手动预拆分场景：绕过分集规划器，``episodes[]`` 账本为空，仅有派生 source/episode_N.txt。"""
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", content_mode)
    return pm


def _write_source_text(pm: ProjectManager, filename: str, text: str) -> Path:
    source_dir = pm.get_project_path("demo") / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / filename
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 状态流转（drama）
# ---------------------------------------------------------------------------


class TestDramaGateFlow:
    def test_no_step1_then_pending_then_confirmed(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)

        # step1 未产出
        assert svc.get_state("demo", 1)["status"] == "no_step1"

        # step1 产出 → 可审中间态、阻塞
        _write_step1(pm, "drama", _drama_step1())
        state = svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        assert state["content"]["scenes"][0]["scene_id"] == "E1S01"
        assert state["content"]["scenes"][0]["utterances"][1]["speaker"] == "阿离"
        project_path = pm.get_project_path("demo")
        project = pm.load_project("demo")
        assert script_review.gate_blocks_step2(project_path, project, 1) is True

        # 确认 → 放行
        confirmed = svc.confirm("demo", 1)
        assert confirmed["status"] == "confirmed"
        assert confirmed["confirmed_at"]
        project = pm.load_project("demo")
        assert script_review.gate_blocks_step2(project_path, project, 1) is False

    def test_editing_step1_after_confirm_repends(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)
        _write_step1(pm, "drama", _drama_step1())
        svc.confirm("demo", 1)
        assert svc.get_state("demo", 1)["status"] == "confirmed"

        # 内容变更（指纹漂移）→ 自动重新待审
        edited = _drama_step1()
        edited["scenes"][0]["utterances"][1]["text"] = "你怎么才回来。"
        svc.save_content("demo", 1, edited)

        state = svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        assert state["content"]["scenes"][0]["utterances"][1]["text"] == "你怎么才回来。"

    def test_whitespace_reformat_keeps_confirmed(self, tmp_path):
        """纯键序 / 空白重排不改语义 → 指纹不变、保持 confirmed。"""
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)
        path = _write_step1(pm, "drama", _drama_step1())
        svc.confirm("demo", 1)

        # 同内容、不同缩进 / 键序重写
        path.write_text(json.dumps(_drama_step1(), ensure_ascii=False, indent=4), encoding="utf-8")
        assert svc.get_state("demo", 1)["status"] == "confirmed"


# ---------------------------------------------------------------------------
# 状态流转（narration，共用同一 gate）
# ---------------------------------------------------------------------------


class TestNarrationGateFlow:
    def test_pending_then_confirm(self, tmp_path):
        pm = _make_project(tmp_path, "narration")
        svc = ScriptReviewService(pm)
        _write_step1(pm, "narration", _narration_step1())

        state = svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        assert state["content"]["segments"][0]["novel_text"].startswith("裴与")

        assert svc.confirm("demo", 1)["status"] == "confirmed"

    def test_edit_novel_text_repends(self, tmp_path):
        pm = _make_project(tmp_path, "narration")
        svc = ScriptReviewService(pm)
        _write_step1(pm, "narration", _narration_step1())
        svc.confirm("demo", 1)

        edited = _narration_step1()
        edited["segments"][0]["novel_text"] = "裴与出征后的第三年。"
        svc.save_content("demo", 1, edited)
        assert svc.get_state("demo", 1)["status"] == "pending_review"


# ---------------------------------------------------------------------------
# 适用范围：ad / reference_video 不纳入 gate
# ---------------------------------------------------------------------------


class TestApplicability:
    def test_reference_video_not_applicable(self, tmp_path):
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        assert svc.get_state("demo", 1)["status"] == "not_applicable"
        project_path = pm.get_project_path("demo")
        assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is False

    def test_ad_not_applicable(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("addemo")
        pm.create_project_metadata("addemo", "Ad", "Anime", "ad")
        svc = ScriptReviewService(pm)
        assert svc.get_state("addemo", 1)["status"] == "not_applicable"


# ---------------------------------------------------------------------------
# 编辑校验 + 确认前置错误
# ---------------------------------------------------------------------------


class TestErrors:
    def test_save_invalid_content_rejected(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)
        _write_step1(pm, "drama", _drama_step1())

        bad = _drama_step1()
        # dialogue 缺 speaker → kind ⇄ speaker 约束失败
        bad["scenes"][0]["utterances"][1] = {"kind": "dialogue", "speaker": None, "text": "无人"}
        with pytest.raises(ScriptReviewError) as exc:
            svc.save_content("demo", 1, bad)
        assert exc.value.code == "invalid_content"

    def test_confirm_without_step1_rejected(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)
        with pytest.raises(ScriptReviewError) as exc:
            svc.confirm("demo", 1)
        assert exc.value.code == "no_step1"

    def test_save_not_applicable_rejected(self, tmp_path):
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        with pytest.raises(ScriptReviewError) as exc:
            svc.save_content("demo", 1, _drama_step1())
        assert exc.value.code == "not_applicable"

    def test_get_state_unregistered_episode_rejected(self, tmp_path):
        """适用 gate 但分集未登记 project.json → episode_not_found（而非误报 no_step1）。"""
        pm = _make_project(tmp_path, "drama")  # 仅登记第 1 集
        svc = ScriptReviewService(pm)
        with pytest.raises(ScriptReviewError) as exc:
            svc.get_state("demo", 99)
        assert exc.value.code == "episode_not_found"

    def test_save_unregistered_episode_writes_no_orphan(self, tmp_path):
        """给未登记分集保存 → episode_not_found，且不落 drafts/episode_99 孤儿 step1 文件。"""
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)
        with pytest.raises(ScriptReviewError) as exc:
            svc.save_content("demo", 99, _drama_step1())
        assert exc.value.code == "episode_not_found"
        orphan = pm.get_project_path("demo") / "drafts" / "episode_99" / "step1_normalized_script.json"
        assert not orphan.exists()

    def test_confirm_corrupt_step1_rejected(self, tmp_path):
        """step1 文件损坏（非法 JSON，但 content_fingerprint 仍产哈希）→ 确认被结构校验拒绝。"""
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)
        path = _write_step1(pm, "drama", _drama_step1())
        path.write_bytes(b"\x00\x01 not json at all {")
        with pytest.raises(ScriptReviewError) as exc:
            svc.confirm("demo", 1)
        assert exc.value.code == "invalid_content"


# ---------------------------------------------------------------------------
# step2 工具阻塞 enforcement：pending 时 generate_episode_script 拒绝
# ---------------------------------------------------------------------------


class TestStep2Enforcement:
    async def test_generate_blocked_when_pending(self, tmp_path):
        from server.agent_runtime.sdk_tools._context import ToolContext
        from server.agent_runtime.sdk_tools.text_generation import generate_episode_script_tool

        pm = _make_project(tmp_path, "drama")
        _write_step1(pm, "drama", _drama_step1())

        ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
        tool = generate_episode_script_tool(ctx)
        result = await tool.handler({"episode": 1})

        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "step1" in text and "阻塞" in text

    async def test_confirm_tool_unblocks_step2(self, tmp_path):
        """agent 路径：confirm_script_review 工具确认后，gate 放行（既有 step1→step2 不被破坏）。"""
        from server.agent_runtime.sdk_tools._context import ToolContext
        from server.agent_runtime.sdk_tools.text_generation import confirm_script_review_tool

        pm = _make_project(tmp_path, "drama")
        _write_step1(pm, "drama", _drama_step1())
        project_path = pm.get_project_path("demo")
        assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is True

        ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
        result = await confirm_script_review_tool(ctx).handler({"episode": 1})

        assert result.get("is_error") is not True
        assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is False


# ---------------------------------------------------------------------------
# 存量穷举：{step1 有无 × step2 有无 × step1_review 有无} 的 gate 派生态
# ---------------------------------------------------------------------------


class TestLegacyEnumeration:
    def test_no_step1(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        assert ScriptReviewService(pm).get_state("demo", 1)["status"] == "no_step1"

    def test_step1_no_step2_no_review_pending(self, tmp_path):
        """feature 后首次产 step1（未产 step2、无确认）→ 待审、阻塞。"""
        pm = _make_project(tmp_path, "drama")
        _write_step1(pm, "drama", _drama_step1())
        assert ScriptReviewService(pm).get_state("demo", 1)["status"] == "pending_review"

    def test_step1_step2_no_review_grandfathered_confirmed(self, tmp_path):
        """存量项目（已产 step1 + step2、无 step1_review 字段）→ grandfather 放行，不阻塞重跑。"""
        pm = _make_project(tmp_path, "drama")
        _write_step1(pm, "drama", _drama_step1())
        _write_step2(pm)
        project_path = pm.get_project_path("demo")
        assert ScriptReviewService(pm).get_state("demo", 1)["status"] == "confirmed"
        assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is False

    def test_step1_step2_review_matching_confirmed(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        _write_step1(pm, "drama", _drama_step1())
        _write_step2(pm)
        ScriptReviewService(pm).confirm("demo", 1)
        assert ScriptReviewService(pm).get_state("demo", 1)["status"] == "confirmed"

    def test_step1_step2_review_mismatch_pending(self, tmp_path):
        """已确认后 step1 又被改（即便 step2 在）→ 重新待审，指纹优先于 grandfather。"""
        pm = _make_project(tmp_path, "drama")
        _write_step1(pm, "drama", _drama_step1())
        _write_step2(pm)
        ScriptReviewService(pm).confirm("demo", 1)
        edited = _drama_step1()
        edited["scenes"][0]["source_text"] = "改写后的原文锚"
        ScriptReviewService(pm).save_content("demo", 1, edited)
        assert ScriptReviewService(pm).get_state("demo", 1)["status"] == "pending_review"


# ---------------------------------------------------------------------------
# 手动预拆分自愈：episodes[] 账本为空但 source/episode_N.txt 派生文件已存在时，
# _require_episode 自愈补建条目而非直接判死锁。
# ---------------------------------------------------------------------------


class TestManualSplitSelfHeal:
    def test_get_state_self_heals_unanchored_orphan(self, tmp_path):
        """无可匹配原文 → 自愈为 unanchored 条目（source_range 为 null），get_state 不再 episode_not_found。"""
        pm = _make_manual_split_project(tmp_path, "narration")
        _write_source_text(pm, "episode_1.txt", "裴与出征后的第二年。")
        _write_step1(pm, "narration", _narration_step1())

        state = ScriptReviewService(pm).get_state("demo", 1)
        assert state["status"] == "pending_review"

        ep = script_review.find_episode(pm.load_project("demo"), 1)
        assert ep is not None
        assert ep["ledger_status"] == "unanchored"
        assert ep["source_range"] is None

    def test_confirm_self_heals_and_unblocks_step2(self, tmp_path):
        """confirm（web 与 agent 工具共用同一 service）在空账本下不再 episode_not_found，且放行 step2。"""
        pm = _make_manual_split_project(tmp_path, "drama")
        _write_source_text(pm, "episode_1.txt", "任意派生内容")
        _write_step1(pm, "drama", _drama_step1())

        confirmed = ScriptReviewService(pm).confirm("demo", 1)
        assert confirmed["status"] == "confirmed"

        project_path = pm.get_project_path("demo")
        assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is False

    def test_self_heal_anchors_when_source_text_matches(self, tmp_path):
        """派生文件内容能在原文中精确子串匹配 → 回填 source_range（而非 unanchored）。"""
        pm = _make_manual_split_project(tmp_path, "narration")
        original = "裴与出征后的第二年，送回一个襁褓中的婴儿。后续内容在此。"
        _write_source_text(pm, "novel.txt", original)
        _write_source_text(pm, "episode_1.txt", "裴与出征后的第二年，送回一个襁褓中的婴儿。")

        ScriptReviewService(pm).get_state("demo", 1)

        ep = script_review.find_episode(pm.load_project("demo"), 1)
        assert ep is not None
        assert ep["ledger_status"] in ("planned", "consumed")
        assert ep["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": 21}

    def test_self_heal_backfills_all_orphans_not_just_requested(self, tmp_path):
        """自愈一次回填账本中所有孤儿集号的派生文件，不只是当前请求的那一集。"""
        pm = _make_manual_split_project(tmp_path, "narration")
        _write_source_text(pm, "episode_1.txt", "第一集内容")
        _write_source_text(pm, "episode_2.txt", "第二集内容")

        ScriptReviewService(pm).get_state("demo", 1)

        project = pm.load_project("demo")
        assert script_review.find_episode(project, 1) is not None
        assert script_review.find_episode(project, 2) is not None

    def test_self_heal_preserves_existing_ledger_status_entries(self, tmp_path):
        """已带 ledger_status 的条目（规划工具写入）不因其他集号的自愈触发被改写。"""
        pm = _make_manual_split_project(tmp_path, "narration")
        pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")

        def _mark_planned(p: dict) -> None:
            ep = next(e for e in p["episodes"] if e["episode"] == 1)
            ep["ledger_status"] = "planned"
            ep["source_range"] = {"source_file": "source/novel.txt", "start": 0, "end": 5}

        pm.update_project("demo", _mark_planned)
        _write_source_text(pm, "episode_2.txt", "第二集派生内容")

        # 触发对孤儿集（episode 2）的自愈请求，不涉及 episode 1。
        ScriptReviewService(pm).get_state("demo", 2)

        project = pm.load_project("demo")
        ep1 = script_review.find_episode(project, 1)
        assert ep1 is not None
        assert ep1["ledger_status"] == "planned"
        assert ep1["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": 5}
        assert script_review.find_episode(project, 2) is not None

    def test_self_heal_does_not_apply_when_derivative_file_missing(self, tmp_path):
        """账本为空且该集派生文件也不存在（真正缺失的集号）→ 仍抛 episode_not_found，不自愈。"""
        pm = _make_manual_split_project(tmp_path, "narration")
        with pytest.raises(ScriptReviewError) as exc:
            ScriptReviewService(pm).get_state("demo", 1)
        assert exc.value.code == "episode_not_found"
        assert pm.load_project("demo")["episodes"] == []

    def test_self_heal_idempotent_no_duplicate_entries(self, tmp_path):
        """重复触发自愈（同集反复读状态）不产生重复集号条目，也不重复改写已回填条目。"""
        pm = _make_manual_split_project(tmp_path, "narration")
        _write_source_text(pm, "episode_1.txt", "第一集派生内容")

        svc = ScriptReviewService(pm)
        svc.get_state("demo", 1)
        first = script_review.find_episode(pm.load_project("demo"), 1)

        svc.get_state("demo", 1)
        svc.get_state("demo", 1)

        project = pm.load_project("demo")
        matches = [e for e in project["episodes"] if e.get("episode") == 1]
        assert len(matches) == 1
        assert matches[0] == first
