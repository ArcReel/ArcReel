"""分集规划工具（plan_episodes / reset_episode_planning）经声明入口的 ``ToolOutcome``。

规划器与重置器经 handler 的关键字参数注入替身；请求校验与迁移阻断走两宿主共用的声明入口。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp import types

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.episode.episode_planner import (
    EpisodePlanningError,
    EpisodePlanSummary,
    LedgerStats,
    PlanResult,
)
from lib.episode.episode_reset import EpisodeResetError, EpisodeResetResult, ResetConfirmationRequired
from lib.episode.episode_target_volume import EpisodeTargetVolume
from lib.generation.generation_queue import GenerationQueue
from lib.project.project_manager import ProjectManager
from server.agent_toolset.episode_planning import PLAN_EPISODES, RESET_EPISODE_PLANNING
from server.agent_toolset.remote import remote_tool
from server.services.project.workflow_planner import WorkflowPlanner
from server.text_generation import MAX_INSTRUCTIONS_LEN
from server.tool_runtime import (
    CallerContext,
    PlanEpisodesResult,
    ResetEpisodePlanningResult,
    Services,
    ToolOutcome,
)
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool


def _fake_planner_cls(result: PlanResult | BaseException, captured: dict[str, Any] | None = None) -> Any:
    """EpisodePlanner 替身：create() 工厂 + plan() 返回预置结果或抛出预置异常。"""

    class _FakePlanner:
        @classmethod
        async def create(cls, project_path: Path) -> _FakePlanner:
            if captured is not None:
                captured["project_path"] = project_path
            return cls()

        async def plan(self, instructions: str | None = None) -> PlanResult:
            if captured is not None:
                captured["plan_instructions"] = instructions
            if isinstance(result, BaseException):
                raise result
            return result

    return _FakePlanner


def _episode(episode: int, title: str = "第一章", **overrides: Any) -> EpisodePlanSummary:
    fields: dict[str, Any] = {
        "episode": episode,
        "title": title,
        "hook": "悬念",
        "reading_units": 800,
        "ledger_status": "planned",
        "first_sentence": "首句。",
        "last_sentence": "尾句。",
    }
    fields.update(overrides)
    return EpisodePlanSummary(**fields)


def _plan_value(outcome: ToolOutcome[Any]) -> PlanEpisodesResult:
    assert outcome.problem is None
    assert isinstance(outcome.value, PlanEpisodesResult)
    return outcome.value


# ---------------------------------------------------------------------------
# plan_episodes
# ---------------------------------------------------------------------------


async def test_plan_episodes_reports_each_planned_episode_for_boundary_review(fake_ctx: ToolHarness) -> None:
    captured: dict[str, Any] = {}
    result = PlanResult(
        episodes=[
            _episode(
                1,
                "古玉藏诀",
                hook="剑诀来历成谜",
                reading_units=812,
                first_sentence="第一章 山村少年。",
                last_sentence="玉中藏着剑诀。",
            ),
            _episode(
                2,
                "城门遇袭",
                hook="少女是谁",
                reading_units=903,
                first_sentence="第二章 下山。",
                last_sentence="城门口他撞见了被追杀的少女。",
            ),
        ],
        cursor={"source_file": "source/novel.txt", "offset": 1715},
    )

    value = _plan_value(
        await run_declared_tool(PLAN_EPISODES, fake_ctx, {}, planner_cls=_fake_planner_cls(result, captured))
    )

    for fragment in (
        "古玉藏诀",
        "剑诀来历成谜",
        "812",
        "城门遇袭",
        "首句：第一章 山村少年。",
        "尾句：城门口他撞见了被追杀的少女。",
    ):
        assert fragment in value.message
    assert [episode["title"] for episode in value.episodes] == ["古玉藏诀", "城门遇袭"]
    assert captured["project_path"] == fake_ctx.project_path
    assert captured["plan_instructions"] is None


@pytest.mark.parametrize(
    ("instructions", "forwarded"),
    [
        pytest.param("  按章节对齐切分  ", "按章节对齐切分", id="stripped"),
        pytest.param("   \n ", None, id="blank-is-absent"),
        pytest.param("章" * MAX_INSTRUCTIONS_LEN, "章" * MAX_INSTRUCTIONS_LEN, id="at-limit"),
    ],
)
async def test_plan_episodes_forwards_normalized_instructions_to_the_planner(
    fake_ctx: ToolHarness, instructions: str, forwarded: str | None
) -> None:
    captured: dict[str, Any] = {}
    planner = _fake_planner_cls(PlanResult(episodes=[_episode(1)], cursor=None), captured)

    _plan_value(await run_declared_tool(PLAN_EPISODES, fake_ctx, {"instructions": instructions}, planner_cls=planner))

    assert captured["plan_instructions"] == forwarded


@pytest.mark.parametrize(
    "instructions",
    [pytest.param(["按章切"], id="not-a-string"), pytest.param("章" * (MAX_INSTRUCTIONS_LEN + 1), id="too-long")],
)
async def test_plan_episodes_rejects_bad_instructions_before_planning(fake_ctx: ToolHarness, instructions: Any) -> None:
    captured: dict[str, Any] = {}
    planner = _fake_planner_cls(PlanResult(episodes=[], cursor=None), captured)

    outcome = await run_declared_tool(PLAN_EPISODES, fake_ctx, {"instructions": instructions}, planner_cls=planner)

    assert outcome.problem is not None
    assert outcome.problem.code == "invalid_request"
    assert outcome.problem.params is not None
    assert outcome.problem.params["errors"][0]["loc"][0] == "instructions"
    assert captured == {}


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        pytest.param(EpisodePlanningError("校验耗尽"), "episode_planning_failed", id="planning-error"),
        # 供应商未配置等规划器内部的 ValueError 不是入参问题。
        pytest.param(ValueError("未找到可用的 text 供应商"), "internal_error", id="provider-error"),
    ],
)
async def test_plan_episodes_reports_planner_failures_without_blaming_the_request(
    fake_ctx: ToolHarness, raised: BaseException, code: str
) -> None:
    outcome = await run_declared_tool(PLAN_EPISODES, fake_ctx, {}, planner_cls=_fake_planner_cls(raised))

    assert outcome.problem is not None
    assert outcome.problem.code == code
    assert str(raised) in outcome.problem.detail


async def test_plan_episodes_reports_an_exhausted_source(fake_ctx: ToolHarness) -> None:
    result = PlanResult(episodes=[], cursor=None, source_exhausted=True)

    value = _plan_value(await run_declared_tool(PLAN_EPISODES, fake_ctx, {}, planner_cls=_fake_planner_cls(result)))

    assert value.source_exhausted is True
    assert "全部规划" in value.message


async def test_plan_episodes_attaches_global_volume_review_when_the_source_is_exhausted(
    fake_ctx: ToolHarness,
) -> None:
    volume = EpisodeTargetVolume(units=800, unit_noun="字", source="units")
    stats = LedgerStats(total_episodes=30, smallest=[(30, 57), (12, 640)], median_units=812, target_volume=volume)
    result = PlanResult(episodes=[], cursor=None, source_exhausted=True, ledger_stats=stats)

    value = _plan_value(await run_declared_tool(PLAN_EPISODES, fake_ctx, {}, planner_cls=_fake_planner_cls(result)))

    for fragment in (
        "累计总集数：30",
        "第 30 集（约 57）",
        "第 12 集（约 640）",
        "中位数：约 812",
        "目标体量设置：约 800",
        "有偏差须向用户明确说明",
    ):
        assert fragment in value.message
    assert "折算" not in value.message
    assert value.ledger_stats is not None
    assert value.ledger_stats["target_units"] == 800
    assert value.ledger_stats["target_units_source"] == "units"
    assert value.ledger_stats["target_seconds"] is None


async def test_plan_episodes_marks_a_target_volume_derived_from_duration(fake_ctx: ToolHarness) -> None:
    """折算而来的目标体量在核对材料里标明来源：主 Agent 不能把估算值当成用户给的硬指标。"""
    volume = EpisodeTargetVolume(units=450, unit_noun="字", source="duration", seconds=90, units_per_second=5.0)
    stats = LedgerStats(total_episodes=30, smallest=[], median_units=812, target_volume=volume)
    result = PlanResult(episodes=[], cursor=None, source_exhausted=True, ledger_stats=stats)

    value = _plan_value(await run_declared_tool(PLAN_EPISODES, fake_ctx, {}, planner_cls=_fake_planner_cls(result)))

    assert "目标体量设置：约 450（按单集目标时长 90 秒折算）" in value.message
    assert value.ledger_stats is not None
    assert value.ledger_stats["target_units"] == 450
    assert value.ledger_stats["target_units_source"] == "duration"
    assert value.ledger_stats["target_seconds"] == 90


async def test_plan_episodes_normal_batch_reports_only_the_running_total(fake_ctx: ToolHarness) -> None:
    result = PlanResult(
        episodes=[_episode(5, "第五集")],
        cursor={"source_file": "source/novel.txt", "offset": 4000},
        source_exhausted=False,
        total_planned=5,
        ledger_stats=None,
    )

    value = _plan_value(await run_declared_tool(PLAN_EPISODES, fake_ctx, {}, planner_cls=_fake_planner_cls(result)))

    assert "累计已规划 5 集。" in value.message
    assert "累计总集数" not in value.message
    assert "体量最小的几集" not in value.message


async def test_remote_plan_episodes_returns_the_generation_batch_handle(tmp_path: Path, db_factory) -> None:
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("demo", content_mode="narration")
    projects.create_project_metadata("demo", "Demo", "", "narration")
    queue = GenerationQueue(session_factory=db_factory, project_manager=projects)
    assert await queue.acquire_or_renew_worker_lease(name="default", owner_id="test-worker", ttl_seconds=60)
    services = Services(
        projects=projects,
        workflow_planner=WorkflowPlanner(projects),
        capabilities=ConfigResolver(async_session_factory),
        queue=queue,
    )
    tool = remote_tool(
        PLAN_EPISODES, projects=projects, services=services, caller=lambda: CallerContext(user_id="u1", source="mcp")
    )

    result = await tool.run({"project": "demo"})

    assert isinstance(result, types.CallToolResult)
    assert result.isError is False
    assert result.structuredContent is not None
    assert set(result.structuredContent) == {"generation_batch"}
    handle = result.structuredContent["generation_batch"]
    assert handle["done"] is False
    assert handle["poll_after_seconds"] is not None


# ---------------------------------------------------------------------------
# reset_episode_planning
# ---------------------------------------------------------------------------


def _fake_reset(result: object, captured: dict[str, Any] | None = None) -> Any:
    def _reset(project_path: Path, *, from_episode: int, confirm_consumed: bool) -> object:
        if captured is not None:
            captured["args"] = (project_path, from_episode, confirm_consumed)
        if isinstance(result, BaseException):
            raise result
        return result

    return _reset


def _reset_value(outcome: ToolOutcome[Any]) -> ResetEpisodePlanningResult:
    assert outcome.problem is None
    assert isinstance(outcome.value, ResetEpisodePlanningResult)
    return outcome.value


async def test_reset_episode_planning_full_reset_points_back_to_planning(fake_ctx: ToolHarness) -> None:
    captured: dict[str, Any] = {}
    result = EpisodeResetResult(
        removed_episodes=[1, 2],
        deleted_files=["source/episode_1.txt"],
        archived_files=[("source/episode_2.txt", "source/_episode_2.txt.bak")],
        consumed_episodes=[],
    )

    value = _reset_value(
        await run_declared_tool(
            RESET_EPISODE_PLANNING, fake_ctx, {"from_episode": 1}, resetter=_fake_reset(result, captured)
        )
    )

    assert captured["args"][1:] == (1, False)
    assert value.confirmation_required is False
    assert value.removed_episodes == [1, 2]
    assert "清空 2 集" in value.message
    assert "source/_episode_2.txt.bak" in value.message
    assert "plan_episodes" in value.message


async def test_reset_episode_planning_asks_for_confirmation_before_touching_consumed_episodes(
    fake_ctx: ToolHarness,
) -> None:
    resetter = _fake_reset(ResetConfirmationRequired(consumed_episodes=[1, 3], archived_files=[]))

    value = _reset_value(
        await run_declared_tool(RESET_EPISODE_PLANNING, fake_ctx, {"from_episode": 1}, resetter=resetter)
    )

    assert value.confirmation_required is True
    assert value.consumed_episodes == [1, 3]
    assert "confirm_consumed" in value.message


async def test_reset_episode_planning_forwards_the_confirmation(fake_ctx: ToolHarness) -> None:
    captured: dict[str, Any] = {}
    result = EpisodeResetResult(removed_episodes=[1], deleted_files=[], archived_files=[], consumed_episodes=[1])

    value = _reset_value(
        await run_declared_tool(
            RESET_EPISODE_PLANNING,
            fake_ctx,
            {"from_episode": 1, "confirm_consumed": True},
            resetter=_fake_reset(result, captured),
        )
    )

    assert captured["args"][1:] == (1, True)
    assert "未删除" in value.message


async def test_reset_episode_planning_partial_reset_reports_the_new_starting_point(fake_ctx: ToolHarness) -> None:
    result = EpisodeResetResult(
        removed_episodes=[2, 3], deleted_files=["source/episode_2.txt"], archived_files=[], consumed_episodes=[]
    )

    value = _reset_value(
        await run_declared_tool(RESET_EPISODE_PLANNING, fake_ctx, {"from_episode": 2}, resetter=_fake_reset(result))
    )

    for fragment in ("部分重置", "第 2 集起共 2 集", "第 1 集原文范围末尾", "新集号从第 2 集起"):
        assert fragment in value.message
    assert "账本已空" not in value.message


async def test_reset_episode_planning_reports_a_failed_partial_precheck(fake_ctx: ToolHarness) -> None:
    resetter = _fake_reset(EpisodeResetError("源文件已被修改或移除：source/novel.txt"))

    outcome = await run_declared_tool(RESET_EPISODE_PLANNING, fake_ctx, {"from_episode": 3}, resetter=resetter)

    assert outcome.problem is not None
    assert outcome.problem.code == "episode_reset_failed"
    assert "源文件已被修改或移除" in outcome.problem.detail


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        # confirm_consumed 是确认安全边界：非布尔值必须拒绝而非真值化。
        pytest.param({"from_episode": 1, "confirm_consumed": "true"}, "confirm_consumed", id="string-confirm"),
        pytest.param({"from_episode": 0}, "from_episode", id="zero"),
        pytest.param({"from_episode": -1}, "from_episode", id="negative"),
        pytest.param({"from_episode": "1"}, "from_episode", id="string"),
        pytest.param({"from_episode": True}, "from_episode", id="bool"),
        pytest.param({"from_episode": None}, "from_episode", id="null"),
        pytest.param({}, "from_episode", id="missing"),
    ],
)
async def test_reset_episode_planning_rejects_bad_arguments_before_resetting(
    fake_ctx: ToolHarness, arguments: dict[str, Any], field: str
) -> None:
    captured: dict[str, Any] = {}
    resetter = _fake_reset(
        EpisodeResetResult(removed_episodes=[], deleted_files=[], archived_files=[], consumed_episodes=[]), captured
    )

    outcome = await run_declared_tool(RESET_EPISODE_PLANNING, fake_ctx, arguments, resetter=resetter)

    assert outcome.problem is not None
    assert outcome.problem.code == "invalid_request"
    assert outcome.problem.params is not None
    assert outcome.problem.params["errors"][0]["loc"][0] == field
    assert captured == {}
