"""Tests for episode_planning."""

from __future__ import annotations

from typing import Any

import pytest

from server.media_tools.context import ToolContext
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _call,
)

# ---------------------------------------------------------------------------
# episode_planning — plan_episodes 薄包装
# ---------------------------------------------------------------------------


def _fake_planner_cls(result: Any, captured: dict[str, Any] | None = None):
    """构造可注入的 EpisodePlanner 替身：create() 工厂 + plan() 返回预置结果。"""

    class _FakePlanner:
        def __init__(self) -> None:
            pass

        @classmethod
        async def create(cls, project_path):
            if captured is not None:
                captured["project_path"] = project_path
            return cls()

        async def plan(self, instructions=None):
            if captured is not None:
                captured["plan_instructions"] = instructions
            if isinstance(result, BaseException):
                raise result
            return result

    return _FakePlanner


async def test_plan_episodes_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_planner import EpisodePlanSummary, PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    captured: dict[str, Any] = {}
    result = PlanResult(
        episodes=[
            EpisodePlanSummary(
                episode=1, title="古玉藏诀", hook="剑诀来历成谜", reading_units=812, ledger_status="planned"
            ),
            EpisodePlanSummary(
                episode=2, title="城门遇袭", hook="少女是谁", reading_units=903, ledger_status="planned"
            ),
        ],
        cursor={"source_file": "source/novel.txt", "offset": 1715},
    )
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result, captured))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {})

    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "古玉藏诀" in text
    assert "剑诀来历成谜" in text
    assert "812" in text
    assert "城门遇袭" in text
    assert captured["project_path"] == fake_ctx.project_path
    assert captured["plan_instructions"] is None  # 不传时透传 None


async def test_plan_episodes_forwards_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """用户分集偏好经 instructions 透传给 EpisodePlanner.plan（strip 后非空）。"""
    from lib.episode_planner import EpisodePlanSummary, PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    captured: dict[str, Any] = {}
    result = PlanResult(
        episodes=[
            EpisodePlanSummary(episode=1, title="第一章", hook="悬念", reading_units=800, ledger_status="planned")
        ],
        cursor=None,
    )
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result, captured))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {"instructions": "  按章节对齐切分  "})

    assert out.get("is_error") is not True
    assert captured["plan_instructions"] == "按章节对齐切分"


async def test_plan_episodes_blank_instructions_treated_as_none(fake_ctx: ToolContext, monkeypatch) -> None:
    """纯空白 instructions 视同未传：透传 None，与不传逐字一致。"""
    from lib.episode_planner import PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        mod, "EpisodePlanner", _fake_planner_cls(PlanResult(episodes=[], cursor=None, source_exhausted=True), captured)
    )
    out = await _call(mod.plan_episodes_tool(fake_ctx), {"instructions": "   \n "})

    assert out.get("is_error") is not True
    assert captured["plan_instructions"] is None


async def test_plan_episodes_rejects_non_string_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """instructions 传非字符串（如数组）按参数错误上报，不静默吞掉。"""
    from lib.episode_planner import PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(PlanResult(episodes=[], cursor=None)))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {"instructions": ["按章切"]})

    assert out.get("is_error") is True
    assert "instructions" in out["content"][0]["text"]


async def test_plan_episodes_rejects_overlong_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """instructions 超长按参数错误提前拒绝，不注入 prompt。"""
    from lib.episode_planner import PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(PlanResult(episodes=[], cursor=None)))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {"instructions": "章" * (mod.MAX_INSTRUCTIONS_LEN + 1)})

    assert out.get("is_error") is True
    assert "过长" in out["content"][0]["text"]


async def test_plan_episodes_accepts_boundary_length_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """instructions 恰好等于上限长度应被接受（覆盖 > 比较的差一边界）。"""
    from lib.episode_planner import EpisodePlanSummary, PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    captured: dict[str, Any] = {}
    result = PlanResult(
        episodes=[
            EpisodePlanSummary(episode=1, title="第一章", hook="悬念", reading_units=800, ledger_status="planned")
        ],
        cursor=None,
    )
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result, captured))
    text = "章" * mod.MAX_INSTRUCTIONS_LEN
    out = await _call(mod.plan_episodes_tool(fake_ctx), {"instructions": text})

    assert out.get("is_error") is not True
    assert captured["plan_instructions"] == text


async def test_plan_episodes_planner_value_error_not_mislabeled_as_param_error(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """规划器内部抛出的 ValueError（如供应商未配置）走通用工具错误，不被误标为参数错误。"""
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(ValueError("未找到可用的 text 供应商")))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {})

    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "未找到可用的 text 供应商" in text
    assert "参数错误" not in text  # 供应商未配置不是入参问题


async def test_plan_episodes_source_exhausted(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_planner import PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    result = PlanResult(episodes=[], cursor=None, source_exhausted=True)
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {})

    assert out.get("is_error") is not True
    assert "全部规划" in out["content"][0]["text"]


async def test_plan_episodes_source_exhausted_includes_ledger_stats(fake_ctx: ToolContext, monkeypatch) -> None:
    """再次调用无新内容（早退路径）：附全局核对材料供主 Agent 核对结构性偏好。"""
    from lib.episode_planner import LedgerStats, PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    stats = LedgerStats(total_episodes=30, smallest=[(30, 57), (12, 640)], median_units=812, target_units=800)
    result = PlanResult(episodes=[], cursor=None, source_exhausted=True, ledger_stats=stats)
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {})

    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "累计总集数：30" in text
    assert "第 30 集（约 57）" in text
    assert "第 12 集（约 640）" in text
    assert "中位数：约 812" in text
    assert "目标体量设置：约 800" in text
    assert "有偏差须向用户明确说明" in text


async def test_plan_episodes_normal_batch_reports_total_planned_line_only(fake_ctx: ToolContext, monkeypatch) -> None:
    """常规（非耗尽）批次没有 ledger_stats：只附「累计已规划 N 集」一行，不带全局核对材料。"""
    from lib.episode_planner import EpisodePlanSummary, PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    result = PlanResult(
        episodes=[
            EpisodePlanSummary(episode=5, title="第五集", hook="悬念", reading_units=800, ledger_status="planned")
        ],
        cursor={"source_file": "source/novel.txt", "offset": 4000},
        source_exhausted=False,
        total_planned=5,
        ledger_stats=None,
    )
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {})

    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "累计已规划 5 集。" in text
    assert "累计总集数" not in text  # 不附全局核对材料
    assert "体量最小的几集" not in text


async def test_plan_episodes_error_envelope(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_planner import EpisodePlanningError
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(EpisodePlanningError("校验耗尽")))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {})

    assert out.get("is_error") is True
    assert "校验耗尽" in out["content"][0]["text"]


# ---------------------------------------------------------------------------
# episode_planning — reset_episode_planning 薄包装
# ---------------------------------------------------------------------------


def _fake_reset(result: Any, captured: dict[str, Any] | None = None):
    def _reset(project_path, *, from_episode, confirm_consumed):
        if captured is not None:
            captured["args"] = (project_path, from_episode, confirm_consumed)
        if isinstance(result, BaseException):
            raise result
        return result

    return _reset


async def test_reset_episode_planning_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_reset import EpisodeResetResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    captured: dict[str, Any] = {}
    result = EpisodeResetResult(
        removed_episodes=[1, 2],
        deleted_files=["source/episode_1.txt"],
        archived_files=[("source/episode_2.txt", "source/_episode_2.txt.bak")],
        consumed_episodes=[],
    )
    monkeypatch.setattr(mod, "reset_episode_planning", _fake_reset(result, captured))

    out = await _call(mod.reset_episode_planning_tool(fake_ctx), {"from_episode": 1})

    assert out.get("is_error") is not True
    assert captured["args"][1:] == (1, False)
    text = out["content"][0]["text"]
    assert "清空 2 集" in text
    assert "source/_episode_2.txt.bak" in text
    assert "plan_episodes" in text  # 指路后续动作


async def test_reset_episode_planning_confirmation_required(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_reset import ResetConfirmationRequired
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(
        mod,
        "reset_episode_planning",
        _fake_reset(ResetConfirmationRequired(consumed_episodes=[1, 3], archived_files=[])),
    )
    out = await _call(mod.reset_episode_planning_tool(fake_ctx), {"from_episode": 1})

    assert out.get("is_error") is not True  # 预期内的流程出口，不是错误
    text = out["content"][0]["text"]
    assert "已消费" in text
    assert "confirm_consumed" in text


async def test_reset_episode_planning_forwards_confirm(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_reset import EpisodeResetResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    captured: dict[str, Any] = {}
    result = EpisodeResetResult(removed_episodes=[1], deleted_files=[], archived_files=[], consumed_episodes=[1])
    monkeypatch.setattr(mod, "reset_episode_planning", _fake_reset(result, captured))

    out = await _call(mod.reset_episode_planning_tool(fake_ctx), {"from_episode": 1, "confirm_consumed": True})

    assert captured["args"][1:] == (1, True)
    assert "未删除" in out["content"][0]["text"]  # 产物保留须对主 Agent 说明


async def test_reset_episode_planning_partial_reset_error(fake_ctx: ToolContext, monkeypatch) -> None:
    """部分重置前置校验未通过（如源文指纹不一致）按可读错误返回，不走通用异常兜底。"""
    from lib.episode_reset import EpisodeResetError
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(
        mod, "reset_episode_planning", _fake_reset(EpisodeResetError("源文件已被修改或移除：source/novel.txt"))
    )
    out = await _call(mod.reset_episode_planning_tool(fake_ctx), {"from_episode": 3})

    assert out.get("is_error") is True
    assert "源文件已被修改或移除" in out["content"][0]["text"]


async def test_reset_episode_planning_partial_reset_success_message(fake_ctx: ToolContext, monkeypatch) -> None:
    """部分重置成功时的摘要区分于全量重置：报清空范围与新起点，而非「账本已空」。"""
    from lib.episode_reset import EpisodeResetResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    result = EpisodeResetResult(
        removed_episodes=[2, 3], deleted_files=["source/episode_2.txt"], archived_files=[], consumed_episodes=[]
    )
    monkeypatch.setattr(mod, "reset_episode_planning", _fake_reset(result))

    out = await _call(mod.reset_episode_planning_tool(fake_ctx), {"from_episode": 2})

    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "部分重置" in text
    assert "第 2 集起共 2 集" in text
    assert "第 1 集原文范围末尾" in text
    assert "新集号从第 2 集起" in text
    assert "账本已空" not in text


async def test_reset_episode_planning_rejects_string_confirm_consumed(fake_ctx: ToolContext) -> None:
    """confirm_consumed 是确认安全边界：非布尔值必须拒绝而非真值化。"""
    from server.agent_runtime.sdk_tools import episode_planning as mod

    out = await _call(
        mod.reset_episode_planning_tool(fake_ctx),
        {"from_episode": 1, "confirm_consumed": "true"},
    )
    assert out.get("is_error") is True
    assert "confirm_consumed" in out["content"][0]["text"]


@pytest.mark.parametrize("bad", [0, -1, "1", True, None])
async def test_reset_episode_planning_rejects_bad_from_episode(fake_ctx: ToolContext, bad: Any) -> None:
    from server.agent_runtime.sdk_tools import episode_planning as mod

    out = await _call(mod.reset_episode_planning_tool(fake_ctx), {"from_episode": bad})
    assert out.get("is_error") is True
    assert "from_episode" in out["content"][0]["text"]


async def test_reset_episode_planning_requires_from_episode(fake_ctx: ToolContext) -> None:
    from server.agent_runtime.sdk_tools import episode_planning as mod

    out = await _call(mod.reset_episode_planning_tool(fake_ctx), {})
    assert out.get("is_error") is True
