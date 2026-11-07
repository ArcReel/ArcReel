"""Episode-planning service behavior tests with mocked text backends, no real LLM calls.

Assert external behavior only: ledger content, derived files, ledger-derived planning start, errors, and return summaries. Pin prompts only under explicit
verbatim-compatibility contracts; assert internal call counts only for retry semantics.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest

import lib.script.script_review as script_review
from lib.backends.providers import CallPurpose
from lib.backends.text_backends.base import StructuredOutputExhaustedError, TextGenerationResult
from lib.episode.episode_ledger import SOURCE_FINGERPRINTS_KEY
from lib.episode.episode_planner import (
    EpisodePlanner,
    EpisodePlanningError,
    NarrationPlanDraft,
    NoCutPointError,
    PlanningConflictError,
    PlanningOutputTruncatedError,
    _DraftRejected,
    _find_all_overlapping,
)
from lib.episode.episode_reset import EpisodeResetResult, reset_episode_planning
from lib.episode.episode_sources import discover_sources as _real_discover_sources
from lib.episode.episode_sources import planning_start, source_snapshot_path
from lib.infra.text_metrics import count_reading_units

# Source: three plot paragraphs with distinct sentences and uniquely locatable anchors.
SOURCE = (
    "第一章 山村少年。李恒在山村长大，自幼习得吐纳之法。"
    "一日他在后山偶得一枚古玉，玉中藏着剑诀。"
    "第二章 下山。李恒辞别师父，踏上去往青云城的路。"
    "城门口他撞见了被追杀的少女。"
    "第三章 风波。少女身份成谜，李恒被卷入漩涡之中。"
)

ANCHOR_EP1 = "玉中藏着剑诀。"
ANCHOR_EP2 = "被追杀的少女。"
ANCHOR_EP3 = "卷入漩涡之中。"

#: Three-episode draft consuming all source in one pass, shared by several source-exhaustion cases.
_THREE_EPISODE_DRAFT = [
    {"title": "甲", "hook": "甲", "end_anchor": ANCHOR_EP1},
    {"title": "乙", "hook": "乙", "end_anchor": ANCHOR_EP2},
    {"title": "丙", "hook": "丙", "end_anchor": ANCHOR_EP3},
]

#: Prompt line when neither target-volume source is configured, pinning current self-directed wording exactly.
_TARGET_VOLUME_UNSET_LINE = (
    "- Target volume per episode: not set; use your judgment for short-video pacing, prioritizing complete plot arcs"
)


def _expected_planning_prompt(
    target_volume_line: str, *, content_mode: str = "narration", source_kind: str = "novel"
) -> str:
    """Lock the complete planning prompt outside the three target-volume sources."""
    screenplay = source_kind == "screenplay"
    if screenplay:
        lines = [
            "You are a short-video episode planner. Below is an excerpt from the author’s finished screenplay. "
            "Respect the author’s episode divisions: extract them rather than splitting again."
        ]
    else:
        lines = [
            "You are a short-video episode planner. Split the original novel excerpt below into episodes, "
            "each with a complete plot arc,",
            "and leave a hook at each episode’s end that makes viewers want to watch the next one.",
        ]
    lines += [
        "",
        "# Project information",
        f"- Content mode: {'Drama (drama)' if content_mode == 'drama' else 'Narration / commentary (narration)'}",
        target_volume_line,
        # 64000-token limit: 64 drama episodes or 160 narration / commentary episodes per batch.
        f"- Plan at most {64 if content_mode == 'drama' else 160} episodes in this batch",
        "",
        "# Splitting rules",
        "- For each episode provide title (an appealing short title), hook "
        "(an explanation of the episode-end hook: why the cut belongs here and what suspense it leaves),",
        "  and end_anchor (a 10~30-character passage at the episode’s end, copied verbatim from the source below "
        "including punctuation, occurring exactly once in the entire source excerpt;",
        "  episode content is all source text from after the previous episode’s ending through the end of this passage).",
    ]
    if screenplay:
        lines.append(
            "- If the screenplay already has episode divisions, retain the author’s divisions: an episode contains "
            "all text from its opening up to the next episode’s opening. Everything the author places between those "
            "openings belongs to the earlier episode. Set end_anchor to a verbatim passage at the end of that text; "
            "take title / hook from the screenplay too (the author’s explicit episode title and end hook). "
            "If the screenplay has no episode divisions, split by complete plot arcs, with each episode forming "
            "a complete story segment."
        )
    if content_mode == "drama":
        lines.append(
            "- Also provide story_beats (the ordered list of this episode’s story beats) and next_episode_teaser "
            "(a teaser for the next episode; null is allowed for the last episode if what follows is unknown)."
            + (
                " If the screenplay explicitly provides beats / a next-episode teaser, copy their original wording; "
                "otherwise derive them yourself."
                if screenplay
                else ""
            )
        )
    lines += [
        "- List episodes in order. end_anchor positions must strictly increase "
        "(continuous ranges, without overlap or gaps).",
        "- This excerpt contains the end of the full source: plan through that ending, using a passage at the "
        "full source’s end as the last episode’s end_anchor; leave no trailing text.",
        "- If this excerpt contains no splitting point with a complete plot arc, return an empty episodes list; "
        "do not force a cut.",
        "- Output only JSON conforming to the schema, with no other content.",
        "",
        "# Original screenplay excerpt" if screenplay else "# Original novel excerpt",
        "---",
        SOURCE,
        "---",
    ]
    return "\n".join(lines)


def _end_of(anchor: str, text: str = SOURCE) -> int:
    return text.index(anchor) + len(anchor)


class _FakeTextGenerator:
    """TextGenerator substitute replays preset responses in order and records every request."""

    def __init__(self, responses: list[str], *, max_output_tokens: int = 64000):
        self._responses = list(responses)
        self.requests = []
        self.model = "fake-model"
        self.max_output_tokens = max_output_tokens

    async def generate(self, request, project_name=None):
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("FakeTextGenerator 预置响应已耗尽")
        text = self._responses.pop(0)
        return TextGenerationResult(text=text, provider="fake", model="fake-model")


def _write_project(
    tmp_path: Path,
    *,
    content_mode: str = "narration",
    episodes: list | None = None,
    extra: dict | None = None,
    source_text: str = SOURCE,
    whole_source: tuple[str, ...] = ("novel.txt",),
    source_kinds: tuple[str, ...] | None = None,
) -> Path:
    """``source_kinds`` corresponds one-to-one with ``whole_source`` and is stored on whole-source manifest entries; omit when absent."""
    project_dir = tmp_path / "projects" / "demo-proj"
    (project_dir / "source").mkdir(parents=True)
    project = {
        "schema_version": 3,
        "title": "测试项目",
        "content_mode": content_mode,
        "generation_mode": "storyboard",
        "characters": {},
        "scenes": {},
        "props": {},
        "episodes": episodes or [],
        "whole_source_files": [
            {"source_file": f"source/{name}", **({} if source_kinds is None else {"source_kind": source_kinds[i]})}
            for i, name in enumerate(whole_source)
        ],
    }
    if extra:
        project.update(extra)
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (project_dir / "source" / "novel.txt").write_text(source_text, encoding="utf-8")
    return project_dir


def _load_project(project_dir: Path) -> dict:
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))


async def _rewrite_project_elsewhere(project_dir: Path, mutate: Callable[[dict], None]) -> str:
    """Simulate another writer changing project.json during a model call; return the entire rewritten text.

    The other writer is outside the tested call's event loop, so perform rewriting in a worker thread.
    """

    def _rewrite() -> str:
        project = _load_project(project_dir)
        mutate(project)
        text = json.dumps(project, ensure_ascii=False)
        (project_dir / "project.json").write_text(text, encoding="utf-8")
        return text

    return await asyncio.to_thread(_rewrite)


def _plan_response(episodes: list[dict]) -> str:
    return json.dumps({"episodes": episodes}, ensure_ascii=False)


class TestFindAllOverlapping:
    def test_collects_overlapping_starts(self):
        assert _find_all_overlapping("aaaa", "aaa") == [0, 1]

    def test_empty_needle_returns_empty_without_hanging(self):
        # Empty-needle defense returns [] without position sliding; caller anchors require min_length=2.
        assert _find_all_overlapping("abcabc", "") == []
        assert _find_all_overlapping("", "") == []


class TestParseDraft:
    """Parsing rejects both failure types as model-returnable _DraftRejected and logs original output."""

    def test_rejects_malformed_json_with_unescaped_quote(self, caplog):
        """Unescaped quotes make invalid JSON; reject and log raw output."""
        raw = '{"episodes": [{"title": "他说"你好"", "hook": "h", "end_anchor": "锚点"}]}'

        with (
            caplog.at_level("WARNING", logger="lib.episode.episode_planner"),
            pytest.raises(_DraftRejected) as exc_info,
        ):
            EpisodePlanner._parse_draft(raw, NarrationPlanDraft)

        assert "不是合法 JSON" in exc_info.value.reasons[0]
        assert any(raw[:20] in record.getMessage() for record in caplog.records)

    def test_rejects_bare_episode_dict_at_top_level(self, caplog):
        """A bare episode object without episodes wrapper is structurally invalid; reject instead of mechanically adding wrappers."""
        raw = json.dumps({"title": "山村少年", "hook": "h", "end_anchor": ANCHOR_EP1}, ensure_ascii=False)

        with (
            caplog.at_level("WARNING", logger="lib.episode.episode_planner"),
            pytest.raises(_DraftRejected) as exc_info,
        ):
            EpisodePlanner._parse_draft(raw, NarrationPlanDraft)

        assert "不符合 schema" in exc_info.value.reasons[0]
        assert "episodes" in exc_info.value.reasons[0]
        assert any(ANCHOR_EP1 in record.getMessage() for record in caplog.records)

    def test_accepts_wrapped_episodes(self):
        raw = _plan_response([{"title": "山村少年", "hook": "h", "end_anchor": ANCHOR_EP1}])
        draft = EpisodePlanner._parse_draft(raw, NarrationPlanDraft)
        assert [ep.end_anchor for ep in draft.episodes] == [ANCHOR_EP1]


class TestPlan:
    async def test_create_declares_episode_planning_purpose(self, tmp_path: Path, monkeypatch) -> None:
        captured: dict[str, object] = {}
        generator = _FakeTextGenerator([])

        async def fake_create(task_type, project_name=None, *, purpose):
            captured.update(task_type=task_type, project_name=project_name, purpose=purpose)
            return generator

        monkeypatch.setattr("lib.episode.episode_planner.TextGenerator.create", fake_create)

        planner = await EpisodePlanner.create(tmp_path / "demo")

        assert planner.project_name == "demo"
        assert captured["purpose"] is CallPurpose.EPISODE_PLANNING

    async def test_plan_writes_ledger_derives_files_and_advances_cursor(self, tmp_path: Path):
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "古玉藏诀", "hook": "玉中剑诀来历成谜", "end_anchor": ANCHOR_EP1},
                        {"title": "城门遇袭", "hook": "少女为何被追杀", "end_anchor": ANCHOR_EP2},
                    ]
                )
            ]
        )
        planner = EpisodePlanner(project_dir, generator=fake)

        result = await planner.plan()

        project = _load_project(project_dir)
        eps = project["episodes"]
        assert [e["episode"] for e in eps] == [1, 2]
        assert eps[0]["title"] == "古玉藏诀"
        assert eps[0]["hook"] == "玉中剑诀来历成谜"
        assert eps[0]["ledger_status"] == "planned"
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": _end_of(ANCHOR_EP1)}
        assert eps[1]["source_range"] == {
            "source_file": "source/novel.txt",
            "start": _end_of(ANCHOR_EP1),
            "end": _end_of(ANCHOR_EP2),
        }
        assert eps[0]["source_origin"] == "whole_source"
        assert "planning_cursor" not in project
        assert result.cursor == {"source_file": "source/novel.txt", "offset": _end_of(ANCHOR_EP2)}
        assert planning_start(project, _real_discover_sources(project_dir, project)) == (
            "source/novel.txt",
            _end_of(ANCHOR_EP2),
        )
        snapshot = source_snapshot_path(project_dir, "source/novel.txt")
        assert snapshot.read_text(encoding="utf-8") == SOURCE

        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        ep2 = (project_dir / "source" / "episode_2.txt").read_text(encoding="utf-8")
        assert ep1 == SOURCE[: _end_of(ANCHOR_EP1)]
        assert ep2 == SOURCE[_end_of(ANCHOR_EP1) : _end_of(ANCHOR_EP2)]

        assert [s.episode for s in result.episodes] == [1, 2]
        assert result.episodes[0].title == "古玉藏诀"
        assert result.episodes[0].hook == "玉中剑诀来历成谜"
        assert result.episodes[0].reading_units > 0
        assert result.source_exhausted is False

    async def test_plan_summary_carries_first_and_last_sentence_of_each_episode(self, tmp_path: Path):
        """Multiepisode summaries include first/last sentences from each episode's ledger-range source slice."""
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator([_plan_response(_THREE_EPISODE_DRAFT)])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert [(s.first_sentence, s.last_sentence) for s in result.episodes] == [
            ("第一章 山村少年。", "一日他在后山偶得一枚古玉，玉中藏着剑诀。"),
            ("第二章 下山。", "城门口他撞见了被追杀的少女。"),
            ("第三章 风波。", "少女身份成谜，李恒被卷入漩涡之中。"),
        ]

    async def test_plan_summary_splits_screenplay_lines_and_truncates_long_sentences(self, tmp_path: Path):
        """Split by lines and sentence punctuation; retain beginnings of long first sentences and endings of long last sentences, marking truncation with ellipses."""
        long_head = "序幕" + "风" * 100 + "。"
        long_tail = "终" + "雨" * 100 + "落幕了。"
        source = f"\n  {long_head}\n角色甲：「你来了？」\n角色乙：好。\n{long_tail}\n\n第二集\n角色甲：再见！\n"
        project_dir = _write_project(tmp_path, source_text=source)
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "甲", "hook": "甲", "end_anchor": "雨雨雨雨雨雨雨雨雨落幕了。"},
                        {"title": "乙", "hook": "乙", "end_anchor": "角色甲：再见！"},
                    ]
                )
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        first, second = result.episodes
        assert first.first_sentence == "序幕" + "风" * 57 + "…"
        assert first.last_sentence == "…" + "雨" * 55 + "落幕了。"
        assert (second.first_sentence, second.last_sentence) == ("第二集", "角色甲：再见！")

    async def test_plan_summary_splits_english_sentence_before_closing_quote(self, tmp_path: Path):
        source = '"Hello." She left. The end.'
        project_dir = _write_project(tmp_path, source_text=source, extra={"source_language": "en"})
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "One", "hook": "End", "end_anchor": "She left. The end."}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert result.episodes[0].first_sentence == '"Hello."'
        assert result.episodes[0].last_sentence == "The end."

    async def test_plan_summary_keeps_english_scene_heading_as_one_sentence(self, tmp_path: Path):
        """Periods after uppercase INT. / EXT. abbreviations do not split; use the whole scene-heading line as the first sentence."""
        source = "INT. KITCHEN - NIGHT\nJohn enters. He sits down.\nEXT. PARK - DAY\n"
        project_dir = _write_project(tmp_path, source_text=source, extra={"source_language": "en"})
        fake = _FakeTextGenerator([_plan_response([{"title": "One", "hook": "Park", "end_anchor": "EXT. PARK - DAY"}])])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert result.episodes[0].first_sentence == "INT. KITCHEN - NIGHT"
        assert result.episodes[0].last_sentence == "EXT. PARK - DAY"

    async def test_plan_rejects_legacy_cut_episodes_without_source_range(self, tmp_path: Path):
        """Legacy extracted episodes without source ranges block planning: identify episode IDs, direct full reset, and never call models."""
        legacy = {
            "episode": 1,
            "title": "旧集",
            "script_file": "scripts/episode_1.json",
            "source_origin": "whole_source",
        }
        project_dir = _write_project(tmp_path, episodes=[dict(legacy)])
        (project_dir / "source" / "episode_1.txt").write_text(SOURCE[: _end_of(ANCHOR_EP1)], encoding="utf-8")
        fake = _FakeTextGenerator([])

        with pytest.raises(EpisodePlanningError, match="没有原文范围记录") as excinfo:
            await EpisodePlanner(project_dir, generator=fake).plan()

        assert "照常可以做脚本规划" in str(excinfo.value)
        assert "reset_episode_planning" in str(excinfo.value)
        assert fake.requests == []
        assert _load_project(project_dir)["episodes"] == [legacy]

    async def test_plan_rejects_legacy_status_entry_without_source_range(self, tmp_path: Path):
        """Retired legacy status values do not affect detection; check extracted episodes for source_range."""
        project_dir = _write_project(
            tmp_path,
            episodes=[
                {
                    "episode": 1,
                    "title": "老条目",
                    "script_file": "scripts/episode_1.json",
                    "source_range": None,
                    "source_origin": "whole_source",
                    "ledger_status": "已废弃的状态",
                }
            ],
        )
        (project_dir / "source" / "episode_1.txt").write_text("人工改过的内容。", encoding="utf-8")
        fake = _FakeTextGenerator([])

        with pytest.raises(EpisodePlanningError, match="reset_episode_planning"):
            await EpisodePlanner(project_dir, generator=fake).plan()

        assert fake.requests == []
        # Preserve episode files; without position records, physical files are the final record.
        assert (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8") == "人工改过的内容。"

    async def test_own_and_no_source_episodes_do_not_block_planning_and_keep_their_place(self, tmp_path: Path):
        """Self-sourced/sourceless episodes need no ranges and do not block planning; append new extracted episodes after the last extracted one, preserving other positions."""
        own = {"episode": 2, "title": "番外", "script_file": "scripts/episode_2.json", "source_origin": "own"}
        blank = {"episode": 3, "title": "空白", "script_file": "scripts/episode_3.json", "source_origin": "none"}
        cut = {
            "episode": 1,
            "title": "古玉藏诀",
            "script_file": "scripts/episode_1.json",
            "ledger_status": "planned",
            "source_origin": "whole_source",
            "source_range": {"source_file": "source/novel.txt", "start": 0, "end": _end_of(ANCHOR_EP1)},
        }
        project_dir = _write_project(
            tmp_path,
            episodes=[dict(own), dict(cut), dict(blank)],
            extra={"episode_id_high_water": 3},
        )
        (project_dir / "source" / "episode_1.txt").write_text(SOURCE[: _end_of(ANCHOR_EP1)], encoding="utf-8")
        (project_dir / "source" / "episode_2.txt").write_text("番外原文。", encoding="utf-8")
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "城门遇袭", "hook": "少女为何被追杀", "end_anchor": ANCHOR_EP2}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        eps = _load_project(project_dir)["episodes"]
        assert [e["episode"] for e in eps] == [2, 1, 4, 3]
        assert eps[0] == own
        assert eps[3] == blank
        assert eps[2]["source_range"] == {
            "source_file": "source/novel.txt",
            "start": _end_of(ANCHOR_EP1),
            "end": _end_of(ANCHOR_EP2),
        }
        assert [s.episode for s in result.episodes] == [4]
        assert (project_dir / "source" / "episode_2.txt").read_text(encoding="utf-8") == "番外原文。"
        assert not (project_dir / "source" / "episode_3.txt").exists()

    async def test_unregistered_files_in_source_do_not_affect_planning(self, tmp_path: Path):
        """Unregistered source/ files, even episode_N.txt lookalikes, are not whole sources and stay outside planning windows."""
        project_dir = _write_project(tmp_path, episodes=[])
        (project_dir / "source" / "0_stray.txt").write_text("没有登记的文件。", encoding="utf-8")
        (project_dir / "source" / "episode_9.txt").write_text("人工放进来的集内容。", encoding="utf-8")
        fake = _FakeTextGenerator([_plan_response(_THREE_EPISODE_DRAFT)])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert "没有登记的文件" not in fake.requests[0].prompt
        assert "人工放进来的集内容" not in fake.requests[0].prompt
        assert [s.episode for s in result.episodes] == [1, 2, 3]
        assert result.source_exhausted is True
        assert (project_dir / "source" / "0_stray.txt").read_text(encoding="utf-8") == "没有登记的文件。"
        assert not source_snapshot_path(project_dir, "source/0_stray.txt").exists()
        assert (project_dir / "source" / "episode_9.txt").read_text(encoding="utf-8") == "人工放进来的集内容。"

    async def test_unregistered_episode_file_taking_a_new_id_is_archived_not_overwritten(self, tmp_path: Path):
        """If new episode IDs collide with unregistered source/ episode files, rename originals before writing derived files to preserve content."""
        project_dir = _write_project(tmp_path, episodes=[])
        (project_dir / "source" / "episode_2.txt").write_text("人工放进来的集内容。", encoding="utf-8")
        fake = _FakeTextGenerator([_plan_response(_THREE_EPISODE_DRAFT)])

        await EpisodePlanner(project_dir, generator=fake).plan()

        archived = project_dir / "source" / "_episode_2.txt.bak"
        assert archived.read_text(encoding="utf-8") == "人工放进来的集内容。"
        assert (project_dir / "source" / "episode_2.txt").read_text(encoding="utf-8") != "人工放进来的集内容。"

    async def test_full_reset_unblocks_planning_for_legacy_ledger(self, tmp_path: Path):
        """Legacy recovery: plan first rejects extracted episodes without source ranges; full reset then enables normal planning from the beginning."""
        project_dir = _write_project(
            tmp_path,
            episodes=[
                {
                    "episode": 1,
                    "title": "旧集",
                    "script_file": "scripts/episode_1.json",
                    "source_origin": "whole_source",
                }
            ],
        )
        (project_dir / "source" / "episode_1.txt").write_text("人工预拆分的旧集内容。", encoding="utf-8")
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "玉中剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )
        planner = EpisodePlanner(project_dir, generator=fake)

        with pytest.raises(EpisodePlanningError, match="没有原文范围记录"):
            await planner.plan()

        reset = reset_episode_planning(project_dir, confirm_consumed=True)
        assert isinstance(reset, EpisodeResetResult)

        result = await planner.plan()

        assert [s.title for s in result.episodes] == ["古玉藏诀"]
        eps = _load_project(project_dir)["episodes"]
        assert [e["episode"] for e in eps] == [2]
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": _end_of(ANCHOR_EP1)}

    async def test_plan_retries_with_failure_reason_when_anchor_invalid(self, tmp_path: Path):
        """Mechanical-validation failures retry automatically: missing anchors enter retry prompts as reasons; round two succeeds."""
        project_dir = _write_project(tmp_path)
        bad_anchor = "这句话不在原文里。"
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "坏", "hook": "坏", "end_anchor": bad_anchor}]),
                _plan_response([{"title": "古玉藏诀", "hook": "玉中剑诀来历成谜", "end_anchor": ANCHOR_EP1}]),
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 2
        retry_prompt = fake.requests[1].prompt
        assert bad_anchor in retry_prompt  # The failure reason identifies the specific anchor.
        assert "不存在" in retry_prompt
        assert [s.title for s in result.episodes] == ["古玉藏诀"]
        project = _load_project(project_dir)
        assert len(project["episodes"]) == 1

    async def test_plan_retries_on_ambiguous_and_non_monotonic_anchors(self, tmp_path: Path):
        """Nonunique anchors / discontinuous ranges also retry with distinguishable reasons."""
        repeated = "李恒抬头看了看天。"
        source = "山中清晨。" + repeated + "他继续赶路。" + repeated + "夜幕降临，他找到了破庙。"
        project_dir = _write_project(tmp_path, source_text=source)
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "甲", "hook": "甲", "end_anchor": repeated}]),  # Occurs twice.
                _plan_response(
                    [
                        {"title": "甲", "hook": "甲", "end_anchor": "他继续赶路。"},
                        {"title": "乙", "hook": "乙", "end_anchor": "山中清晨。"},  # Before the previous episode ends.
                    ]
                ),
                _plan_response([{"title": "丙", "hook": "丙", "end_anchor": "他继续赶路。"}]),
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 3
        assert "2 次" in fake.requests[1].prompt  # Nonunique: report occurrence count.
        assert "连续" in fake.requests[2].prompt  # Discontinuous: report range-advancement issue.
        assert [s.title for s in result.episodes] == ["丙"]

    async def test_plan_rejects_overlapping_anchor_occurrences_as_ambiguous(self, tmp_path: Path):
        """Overlapping anchors are nonunique too; nonoverlapping counts would falsely locate 哪哪 uniquely in 哪哪哪."""
        source = "天哪哪哪，山里炸开了锅。李恒收剑而立。"
        project_dir = _write_project(tmp_path, source_text=source)
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "甲", "hook": "甲", "end_anchor": "哪哪"}]),  # Two overlapping occurrences.
                _plan_response([{"title": "乙", "hook": "乙", "end_anchor": "李恒收剑而立。"}]),
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 2
        assert "2 次" in fake.requests[1].prompt  # Count with overlaps allowed.
        assert [s.title for s in result.episodes] == ["乙"]

    async def test_plan_resolves_anchor_with_halfwidth_punctuation(self, tmp_path: Path):
        """Fullwidth/halfwidth punctuation differences (，→ , and 。→ .) fold tolerantly to exact NFC offsets, succeeding without retries."""
        project_dir = _write_project(tmp_path)
        half_width_anchor = "古玉,玉中藏着剑诀."  # Fullwidth source ， and 。 become model-returned halfwidth , and .
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "玉中剑诀来历成谜", "end_anchor": half_width_anchor}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1  # Tolerant match without retry.
        eps = _load_project(project_dir)["episodes"]
        # Recovered offsets exactly match exact-match coordinates byte for byte.
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": _end_of(ANCHOR_EP1)}
        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        assert ep1 == SOURCE[: _end_of(ANCHOR_EP1)]
        assert ep1.endswith("玉中藏着剑诀。")  # Source punctuation stays fullwidth 。.
        assert [s.title for s in result.episodes] == ["古玉藏诀"]

    async def test_plan_drama_resolves_anchor_with_punctuation_width_mismatch(self, tmp_path: Path):
        """Drama drafts also tolerate punctuation widths; 。→ . mismatches resolve correct exact offsets."""
        project_dir = _write_project(tmp_path, content_mode="drama")
        half_width_anchor = "被追杀的少女."  # Fullwidth source 。.
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {
                            "title": "城门遇袭",
                            "hook": "少女为何被追杀",
                            "end_anchor": half_width_anchor,
                            "story_beats": ["辞别师父", "城门遇袭"],
                            "next_episode_teaser": "风波将起",
                        }
                    ]
                )
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1  # Tolerant match without retry.
        eps = _load_project(project_dir)["episodes"]
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": _end_of(ANCHOR_EP2)}
        assert eps[0]["outline"]["story_beats"] == ["辞别师父", "城门遇袭"]
        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        assert ep1 == SOURCE[: _end_of(ANCHOR_EP2)]
        assert [s.title for s in result.episodes] == ["城门遇袭"]

    async def test_plan_resolves_anchor_with_whitespace_width_mismatch(self, tmp_path: Path):
        """Fullwidth/halfwidth anchor-space differences fold tolerantly to exact offsets."""
        source = "The hero　rose at dawn. Then darkness fell over the land."  # Includes fullwidth U+3000 space.
        project_dir = _write_project(tmp_path, source_text=source)
        half_width_anchor = "The hero rose at dawn."  # Model returns halfwidth spaces.
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "Dawn", "hook": "hook", "end_anchor": half_width_anchor}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1
        eps = _load_project(project_dir)["episodes"]
        end = source.index("dawn.") + len("dawn.")
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": end}
        assert (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8") == source[:end]
        assert [s.title for s in result.episodes] == ["Dawn"]

    async def test_plan_resolves_anchor_with_unicode_nfc_mismatch(self, tmp_path: Path):
        """NFD anchors versus NFC sources use folded fallback: apply the same normalize_source_text as the window
        to an anchor copy before matching, restoring exact offsets without changing Tier1 or source coordinates."""
        source = unicodedata.normalize("NFC", "Tôi yêu tiếng Việt. Hôm nay trời đẹp lắm.")
        project_dir = _write_project(tmp_path, source_text=source)
        nfc_anchor = "Tôi yêu tiếng Việt."
        nfd_anchor = unicodedata.normalize("NFD", nfc_anchor)  # Model returns decomposed form.
        assert nfd_anchor != nfc_anchor
        assert len(nfd_anchor) != len(nfc_anchor)
        fake = _FakeTextGenerator([_plan_response([{"title": "Mở đầu", "hook": "hook", "end_anchor": nfd_anchor}])])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1  # Tolerant match without retry.
        eps = _load_project(project_dir)["episodes"]
        end = source.index(nfc_anchor) + len(nfc_anchor)  # Exact NFC offsets.
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": end}
        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        assert ep1 == source[:end]  # Slice NFC source without rewriting it.
        assert [s.title for s in result.episodes] == ["Mở đầu"]

    async def test_plan_resolves_anchor_with_crlf_and_combining_length_change(self, tmp_path: Path):
        """Length guard: anchors contain both \\r\\n and length-changing NFD combining characters, each lengthening them relative to source substrings.
        normalize_source_text applies NFC/newline normalization to copies; reconstruct end spans using normalized anchor length and assert source offsets/
        slices exactly follow normalized coordinates without \\r or combining-character drift; Tier1 stays byte-identical."""
        # Source already uses stored NFC + \n coordinates; both windows and slices use them.
        source = "Tôi yêu tiếng Việt.\nHôm nay trời đẹp lắm. Mọi thứ thật tuyệt vời."
        project_dir = _write_project(tmp_path, source_text=source)
        nfc_sub = "Tôi yêu tiếng Việt.\nHôm nay"  # Exact source substring (NFC + \n).
        # Model returns expanded NFD combining characters plus \r\n newlines, adding another character.
        crlf_nfd_anchor = unicodedata.normalize("NFD", nfc_sub).replace("\n", "\r\n")
        assert "\r\n" in crlf_nfd_anchor
        assert len(crlf_nfd_anchor) > len(nfc_sub)

        fake = _FakeTextGenerator(
            [_plan_response([{"title": "Mở đầu", "hook": "hook", "end_anchor": crlf_nfd_anchor}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1  # Tolerant match without retry.
        eps = _load_project(project_dir)["episodes"]
        end = source.index(nfc_sub) + len(nfc_sub)  # End uses normalized length, not raw anchor length.
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": end}
        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        assert ep1 == source[:end]  # Exact source slice; no \r/combining-mark drift.
        assert [s.title for s in result.episodes] == ["Mở đầu"]

    async def test_plan_resolves_anchor_with_curly_double_quote_mismatch(self, tmp_path: Path):
        """Curly source double quotes (U+201C/U+201D) versus straight model quotes align through added folding-table mappings."""
        source = "他说：“古玉里藏着剑诀。”少女沉默不语。"
        curly_anchor = "“古玉里藏着剑诀。”"
        straight_anchor = '"古玉里藏着剑诀。"'  # Model returns straight quotes.
        project_dir = _write_project(tmp_path, source_text=source)
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "hook", "end_anchor": straight_anchor}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1  # Tolerant match without retry.
        end = source.index(curly_anchor) + len(curly_anchor)
        eps = _load_project(project_dir)["episodes"]
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": end}
        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        assert ep1 == source[:end]
        assert ep1.endswith("”")  # Source curly quotes remain unchanged.
        assert [s.title for s in result.episodes] == ["古玉藏诀"]

    async def test_plan_resolves_anchor_with_curly_double_quote_mismatch_reverse(self, tmp_path: Path):
        """Inverse: straight source double quotes versus curly model quotes align through the same bidirectional folding table."""
        source = '他说："古玉里藏着剑诀。"少女沉默不语。'
        straight_anchor = '"古玉里藏着剑诀。"'
        curly_anchor = "“古玉里藏着剑诀。”"  # Model returns curly quotes.
        project_dir = _write_project(tmp_path, source_text=source)
        fake = _FakeTextGenerator([_plan_response([{"title": "古玉藏诀", "hook": "hook", "end_anchor": curly_anchor}])])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1  # Tolerant match without retry.
        end = source.index(straight_anchor) + len(straight_anchor)
        eps = _load_project(project_dir)["episodes"]
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": end}
        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        assert ep1 == source[:end]
        assert [s.title for s in result.episodes] == ["古玉藏诀"]

    async def test_plan_resolves_anchor_with_curly_single_quote_mismatch(self, tmp_path: Path):
        """Curly source single quotes (U+2018/U+2019) versus straight model quotes align through added folding-table mappings."""
        source = "少女低声说：‘我不是坏人。’李恒不再追问。"
        curly_anchor = "‘我不是坏人。’"
        straight_anchor = "'我不是坏人。'"  # Model returns straight quotes.
        project_dir = _write_project(tmp_path, source_text=source)
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "少女辩白", "hook": "hook", "end_anchor": straight_anchor}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1  # Tolerant match without retry.
        end = source.index(curly_anchor) + len(curly_anchor)
        eps = _load_project(project_dir)["episodes"]
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": end}
        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        assert ep1 == source[:end]
        assert ep1.endswith("’")  # Source curly quotes remain unchanged.
        assert [s.title for s in result.episodes] == ["少女辩白"]

    async def test_plan_resolves_anchor_with_cjk_wave_dash_fullwidth_anchor(self, tmp_path: Path):
        """Source CJK wave dash U+301C versus model fullwidth tilde U+FF5E both fold to halfwidth ~."""
        source = "古玉估价约〜百金，来历不明。少女转身离去。"
        cjk_anchor = "约〜百金，来历不明。"
        fullwidth_anchor = "约～百金，来历不明。"  # Model returns U+FF5E.
        project_dir = _write_project(tmp_path, source_text=source)
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "估价成谜", "hook": "hook", "end_anchor": fullwidth_anchor}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1  # Tolerant match without retry.
        end = source.index(cjk_anchor) + len(cjk_anchor)
        eps = _load_project(project_dir)["episodes"]
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": end}
        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        assert ep1 == source[:end]
        assert ep1.endswith("〜百金，来历不明。")  # Source CJK wave dash remains unchanged.
        assert [s.title for s in result.episodes] == ["估价成谜"]

    async def test_plan_resolves_anchor_with_cjk_wave_dash_halfwidth_anchor(self, tmp_path: Path):
        """Source CJK wave dash U+301C versus model halfwidth ~ align through added folding-table mappings."""
        source = "古玉估价约〜百金，来历不明。少女转身离去。"
        cjk_anchor = "约〜百金，来历不明。"
        halfwidth_anchor = "约~百金，来历不明。"  # Model returns halfwidth ~.
        project_dir = _write_project(tmp_path, source_text=source)
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "估价成谜", "hook": "hook", "end_anchor": halfwidth_anchor}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1  # Tolerant match without retry.
        end = source.index(cjk_anchor) + len(cjk_anchor)
        eps = _load_project(project_dir)["episodes"]
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": end}
        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        assert ep1 == source[:end]
        assert [s.title for s in result.episodes] == ["估价成谜"]

    async def test_plan_resolves_anchor_with_fullwidth_straight_quote_mismatch(self, tmp_path: Path):
        """Fullwidth straight source quotes (U+FF02/U+FF07) versus model halfwidth straight quotes align through added folding-table mappings."""
        source = "少女喊道：＂别跑，＇等等我＇！＂李恒停下脚步。"
        fullwidth_anchor = "＂别跑，＇等等我＇！＂"
        halfwidth_anchor = "\"别跑，'等等我'！\""  # Model returns halfwidth straight quotes.
        project_dir = _write_project(tmp_path, source_text=source)
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "城门呼喊", "hook": "hook", "end_anchor": halfwidth_anchor}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1  # Tolerant match without retry.
        end = source.index(fullwidth_anchor) + len(fullwidth_anchor)
        eps = _load_project(project_dir)["episodes"]
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": end}
        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        assert ep1 == source[:end]
        assert ep1.endswith("＂")  # Source fullwidth straight quotes remain unchanged.
        assert [s.title for s in result.episodes] == ["城门呼喊"]

    async def test_plan_rejects_anchor_ambiguous_after_punctuation_folding(self, tmp_path: Path):
        """Multiple folded matches still reject as nonunique and retry, never silently selecting the first."""
        source = "他说好的。她说好的。后来他离开了。"  # Two fullwidth-period occurrences of 说好的。.
        project_dir = _write_project(tmp_path, source_text=source)
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "甲", "hook": "甲", "end_anchor": "说好的."}]),  # . folds to 2 matches.
                _plan_response([{"title": "乙", "hook": "乙", "end_anchor": "后来他离开了。"}]),
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 2
        assert "无法唯一定位" in fake.requests[1].prompt
        assert "2 次" in fake.requests[1].prompt
        assert [s.title for s in result.episodes] == ["乙"]

    async def test_plan_continues_after_last_cut_episode_in_next_source_file(self, tmp_path: Path):
        """When the last extracted episode reaches a later source file, continue from its end without replanning that file's prefix."""
        c = _end_of(ANCHOR2_MID, SOURCE2)
        project_dir = _write_project(
            tmp_path,
            episodes=[
                _entry(1, 0, len(SOURCE)),
                _entry(2, 0, c, source_file="source/novel2.txt"),
            ],
            whole_source=("novel.txt", "novel2.txt"),
        )
        (project_dir / "source" / "novel2.txt").write_text(SOURCE2, encoding="utf-8")
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "结伴赶路", "hook": "兽潮来袭", "end_anchor": "途中遭遇兽潮。"}])]
        )

        await EpisodePlanner(project_dir, generator=fake).plan()

        eps = {e["episode"]: e for e in _load_project(project_dir)["episodes"]}
        assert eps[3]["source_range"] == {"source_file": "source/novel2.txt", "start": c, "end": len(SOURCE2)}

    async def test_plan_raises_and_leaves_project_untouched_after_retry_exhaustion(self, tmp_path: Path):
        """Exhausted retries raise EpisodePlanningError without ledger/file changes, preserving atomicity."""
        project_dir = _write_project(tmp_path)
        before = (project_dir / "project.json").read_text(encoding="utf-8")
        bad = _plan_response([{"title": "坏", "hook": "坏", "end_anchor": "不在原文里"}])
        fake = _FakeTextGenerator([bad, bad, bad])

        with pytest.raises(EpisodePlanningError):
            await EpisodePlanner(project_dir, generator=fake).plan()

        assert (project_dir / "project.json").read_text(encoding="utf-8") == before
        assert not list((project_dir / "source").glob("episode_*.txt"))

    async def test_plan_converts_structured_output_exhausted_to_planning_error(self, tmp_path: Path):
        """Exhausted backend structured-output fallback becomes EpisodePlanningError pointing to inadequate provider capability, without retrying at this layer."""
        project_dir = _write_project(tmp_path)

        class _ExhaustedGenerator(_FakeTextGenerator):
            def __init__(self):
                super().__init__([])
                self.calls = 0

            async def generate(self, request, project_name=None):
                self.calls += 1
                raise StructuredOutputExhaustedError(
                    provider="custom-relay",
                    model="claude-sonnet-4-6",
                    reason="降级链各档重试耗尽后模型输出仍不合规",
                )

        fake = _ExhaustedGenerator()
        with pytest.raises(EpisodePlanningError, match="结构化输出能力不足") as exc_info:
            await EpisodePlanner(project_dir, generator=fake).plan()

        assert fake.calls == 1
        assert isinstance(exc_info.value.__cause__, StructuredOutputExhaustedError)
        assert "InstructorRetryException" not in str(exc_info.value)

    async def test_plan_raises_planning_conflict_when_ledger_advances_during_call(self, tmp_path: Path):
        """Another call commits first while requests are in flight, advancing the effective start; locked rechecks discard the whole batch without ledger/disk writes."""
        project_dir = _write_project(tmp_path)
        after_concurrent_commit: list[str] = []

        class _ConcurrentCommitGenerator(_FakeTextGenerator):
            async def generate(self, request, project_name=None):
                # Simulate another plan() committing episode 1 between the unlocked snapshot and locked recheck.
                def _commit_concurrently(project: dict) -> None:
                    project["episodes"] = [_entry(1, 0, _end_of(ANCHOR_EP1))]

                after_concurrent_commit.append(await _rewrite_project_elsewhere(project_dir, _commit_concurrently))
                return await super().generate(request, project_name)

        fake = _ConcurrentCommitGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "玉中剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )

        with pytest.raises(PlanningConflictError, match="并发"):
            await EpisodePlanner(project_dir, generator=fake).plan()

        # Ledger remains exactly at the concurrent committed state; this call writes no episode entries or source fingerprints.
        assert (project_dir / "project.json").read_text(encoding="utf-8") == after_concurrent_commit[0]
        assert not list((project_dir / "source").glob("episode_*.txt"))

    async def test_plan_raises_planning_conflict_when_episode_ids_are_allocated_during_call(self, tmp_path: Path):
        """In-flight episode ID allocation by another writer advances only the historical high-water mark, not ledger/progress; discard the whole batch without unprotected derived-file writes."""
        project_dir = _write_project(tmp_path)
        after_concurrent_write: list[str] = []

        class _ConcurrentAllocationGenerator(_FakeTextGenerator):
            async def generate(self, request, project_name=None):
                def _allocate_concurrently(project: dict) -> None:
                    project["episode_id_high_water"] = 5

                after_concurrent_write.append(await _rewrite_project_elsewhere(project_dir, _allocate_concurrently))
                return await super().generate(request, project_name)

        fake = _ConcurrentAllocationGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "玉中剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )

        with pytest.raises(PlanningConflictError, match="并发"):
            await EpisodePlanner(project_dir, generator=fake).plan()

        assert (project_dir / "project.json").read_text(encoding="utf-8") == after_concurrent_write[0]
        assert not list((project_dir / "source").glob("episode_*.txt"))

    async def test_plan_truncation_short_circuits_retry_and_reports_the_model(self, tmp_path: Path):
        """Truncated structured output does not retry; report provider/model and custom-model status (docs/adr/0044)."""
        from lib.backends.text_backends.base import TextOutputTruncatedError

        class _TruncatingGenerator:
            model = "my-llm"
            max_output_tokens = 8192

            def __init__(self):
                self.call_count = 0

            async def generate(self, request, project_name=None):
                self.call_count += 1
                raise TextOutputTruncatedError(
                    provider="openai", model="my-llm", output_tokens=8192, provider_id="custom-3", custom_model=True
                )

        project_dir = _write_project(tmp_path)
        before = (project_dir / "project.json").read_text(encoding="utf-8")
        fake = _TruncatingGenerator()

        with pytest.raises(PlanningOutputTruncatedError) as exc_info:
            await EpisodePlanner(project_dir, generator=fake).plan()

        # Truncation calls once, unlike schema/mechanical failures exhausting max_attempts.
        assert fake.call_count == 1
        assert (exc_info.value.provider_id, exc_info.value.model, exc_info.value.custom_model) == (
            "custom-3",
            "my-llm",
            True,
        )
        assert (project_dir / "project.json").read_text(encoding="utf-8") == before

    async def test_plan_without_a_complete_arc_reports_the_window_start(self, tmp_path: Path):
        """No complete plot-arc boundary in the window means a batch error with the unsplit source start, without retries or ledger changes."""
        project_dir = _write_project(tmp_path, episodes=[_entry(1, 0, _end_of(ANCHOR_EP1))])
        before = (project_dir / "project.json").read_text(encoding="utf-8")
        fake = _FakeTextGenerator([json.dumps({"episodes": []})])

        with pytest.raises(NoCutPointError) as exc_info:
            await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1
        assert (exc_info.value.source_file, exc_info.value.offset) == ("source/novel.txt", _end_of(ANCHOR_EP1))
        assert (project_dir / "project.json").read_text(encoding="utf-8") == before

    async def test_plan_accepts_uppercase_json_fence(self, tmp_path: Path):
        """Parse uppercase ```JSON fences too; fence markers are case-insensitive."""
        project_dir = _write_project(tmp_path)
        fenced = (
            "```JSON\n" + _plan_response([{"title": "古玉藏诀", "hook": "钩子", "end_anchor": ANCHOR_EP1}]) + "\n```"
        )
        fake = _FakeTextGenerator([fenced])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert [s.title for s in result.episodes] == ["古玉藏诀"]
        assert len(fake.requests) == 1  # Succeed without retry.

    async def test_plan_drama_writes_outline_to_ledger(self, tmp_path: Path):
        """Expand drama entries into episode outlines, persisting story_beats + next_episode_teaser in ledger outline."""
        project_dir = _write_project(tmp_path, content_mode="drama")
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {
                            "title": "古玉藏诀",
                            "hook": "玉中剑诀来历成谜",
                            "end_anchor": ANCHOR_EP1,
                            "story_beats": ["山村习武", "后山得玉"],
                            "next_episode_teaser": "下集李恒下山",
                        }
                    ]
                )
            ]
        )

        await EpisodePlanner(project_dir, generator=fake).plan()

        eps = _load_project(project_dir)["episodes"]
        assert eps[0]["outline"] == {
            "story_beats": ["山村习武", "后山得玉"],
            "next_episode_teaser": "下集李恒下山",
        }

    async def test_plan_drama_rejects_response_missing_story_beats(self, tmp_path: Path):
        """Drama output missing story_beats fails schema validation and retries."""
        project_dir = _write_project(tmp_path, content_mode="drama")
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "甲", "hook": "甲", "end_anchor": ANCHOR_EP1}]),
                _plan_response(
                    [
                        {
                            "title": "甲",
                            "hook": "甲",
                            "end_anchor": ANCHOR_EP1,
                            "story_beats": ["节点"],
                            "next_episode_teaser": None,
                        }
                    ]
                ),
            ]
        )

        await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 2
        assert "schema" in fake.requests[1].prompt

    async def test_plan_screenplay_respects_author_divisions(self, tmp_path: Path):
        """Screenplay mocks honor author episode boundaries; persist their boundaries/titles/hooks/outlines in the ledger."""
        project_dir = _write_project(tmp_path, content_mode="drama", source_kinds=("screenplay",))
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {
                            "title": "山村少年",  # Copy author-supplied episode titles unchanged.
                            "hook": "古玉剑诀来历成谜",
                            "end_anchor": ANCHOR_EP1,
                            "story_beats": ["山村习武", "后山得玉"],
                            "next_episode_teaser": "下集李恒下山",
                        },
                        {
                            "title": "下山",
                            "hook": "少女为何被追杀",
                            "end_anchor": ANCHOR_EP2,
                            "story_beats": ["辞别师父", "城门遇袭"],
                            "next_episode_teaser": "下集风波将起",
                        },
                    ]
                )
            ]
        )

        await EpisodePlanner(project_dir, generator=fake).plan()

        eps = _load_project(project_dir)["episodes"]
        assert [e["title"] for e in eps] == ["山村少年", "下山"]
        assert eps[0]["hook"] == "古玉剑诀来历成谜"
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": _end_of(ANCHOR_EP1)}
        assert eps[1]["source_range"] == {
            "source_file": "source/novel.txt",
            "start": _end_of(ANCHOR_EP1),
            "end": _end_of(ANCHOR_EP2),
        }
        assert eps[0]["outline"] == {"story_beats": ["山村习武", "后山得玉"], "next_episode_teaser": "下集李恒下山"}

    async def test_plan_prompt_branches_on_source_kind(self, tmp_path: Path):
        """Screenplay planning defines author episodes by the next episode's beginning exactly once, without negative marker-recognition wording; novel semantics stay unchanged."""

        def _one_episode_generator() -> _FakeTextGenerator:
            return _FakeTextGenerator(
                [
                    _plan_response(
                        [
                            {
                                "title": "甲",
                                "hook": "甲",
                                "end_anchor": ANCHOR_EP1,
                                "story_beats": ["节点"],
                                "next_episode_teaser": None,
                            }
                        ]
                    )
                ]
            )

        screenplay_dir = _write_project(tmp_path / "scr", content_mode="drama", source_kinds=("screenplay",))
        scr_fake = _one_episode_generator()
        await EpisodePlanner(screenplay_dir, generator=scr_fake).plan()
        scr_prompt = scr_fake.requests[0].prompt

        novel_dir = _write_project(tmp_path / "nov", content_mode="drama")
        nov_fake = _one_episode_generator()
        await EpisodePlanner(novel_dir, generator=nov_fake).plan()
        nov_prompt = nov_fake.requests[0].prompt

        # Screenplay respects author episodes, ending before the next begins (defined once per prompt), or uses plot arcs if absent.
        assert "Respect the author" in scr_prompt
        assert scr_prompt.count("next episode’s opening") == 1
        assert "fixed marker" not in scr_prompt
        assert "regex" not in scr_prompt
        assert "plot arc" in scr_prompt
        assert "Original screenplay excerpt" in scr_prompt
        # Novel retains creative splitting into episodes, without screenplay-specific instructions (regression guard).
        assert "Respect the author" not in nov_prompt
        assert "next episode’s opening" not in nov_prompt
        assert "Original novel excerpt" in nov_prompt

    async def test_plan_window_limits_prompt_window(self, tmp_path: Path):
        """Content outside the window never enters prompts."""
        window_chars = _end_of(ANCHOR_EP1) + 4
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator([_plan_response([{"title": "古玉藏诀", "hook": "钩子", "end_anchor": ANCHOR_EP1}])])

        result = await EpisodePlanner(project_dir, generator=fake, window_chars=window_chars).plan()

        assert ANCHOR_EP2 not in fake.requests[0].prompt  # Window truncated.
        assert result.source_exhausted is False
        assert result.cursor == {"source_file": "source/novel.txt", "offset": _end_of(ANCHOR_EP1)}

    async def test_plan_window_is_fifty_thousand_chars_regardless_of_project_settings(self, tmp_path: Path):
        """Planning windows use 50,000 characters; project.json planning_window_chars does not participate."""
        source = "甲" * 49_990 + "窗口内最后一句。" + "乙" * 20_000 + "窗口外的一句。"
        project_dir = _write_project(
            tmp_path, source_text=source, extra={"planning_window_chars": 100, "planning_max_episodes": 1}
        )
        fake = _FakeTextGenerator([_plan_response([{"title": "甲", "hook": "甲", "end_anchor": "窗口内最后一句。"}])])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        prompt = fake.requests[0].prompt
        assert "窗口内最后一句" in prompt
        assert "窗口外的一句" not in prompt
        assert result.cursor == {"source_file": "source/novel.txt", "offset": 49_998}

    async def test_plan_window_elasticity_extends_to_full_text_when_remainder_small(self, tmp_path: Path):
        """When remaining text is below 1.2 windows, extend directly to source end rather than force a separate remainder episode."""
        window_chars = len(SOURCE) - 8  # Smaller than full text, but remaining amount stays within 1.2 windows.
        project_dir = _write_project(tmp_path)
        last_anchor = "卷入漩涡之中。"
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "甲", "hook": "甲", "end_anchor": ANCHOR_EP1},
                        {"title": "乙", "hook": "乙", "end_anchor": ANCHOR_EP2},
                        {"title": "丙", "hook": "丙", "end_anchor": last_anchor},
                    ]
                )
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake, window_chars=window_chars).plan()

        assert last_anchor in fake.requests[0].prompt  # Window extends to source end without window_chars truncation.
        assert result.source_exhausted is True
        assert result.cursor == {"source_file": "source/novel.txt", "offset": len(SOURCE)}

    async def test_plan_batch_size_follows_the_model_output_limit(self, tmp_path: Path):
        """Batch episode counts derive from model output limits; if only one fits, trim extra episodes into the next batch."""
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "甲", "hook": "甲", "end_anchor": ANCHOR_EP1},
                        {"title": "乙", "hook": "乙", "end_anchor": ANCHOR_EP2},
                    ]
                )
            ],
            max_output_tokens=400,
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert [s.title for s in result.episodes] == ["甲"]
        project = _load_project(project_dir)
        assert len(project["episodes"]) == 1
        assert result.cursor == {"source_file": "source/novel.txt", "offset": _end_of(ANCHOR_EP1)}

    async def test_plan_final_window_with_whitespace_tail_marks_source_exhausted(self, tmp_path: Path):
        """At source end with whitespace only remaining, align the last episode/cursor to the end and report source exhausted."""
        source = SOURCE + "\n\n"
        project_dir = _write_project(tmp_path, source_text=source)
        last_anchor = "卷入漩涡之中。"
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "甲", "hook": "甲", "end_anchor": ANCHOR_EP2},
                        {"title": "乙", "hook": "乙", "end_anchor": last_anchor},
                    ]
                )
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert result.source_exhausted is True
        project = _load_project(project_dir)
        assert result.cursor == {"source_file": "source/novel.txt", "offset": len(source)}
        assert project["episodes"][-1]["source_range"]["end"] == len(source)
        ep2_file = (project_dir / "source" / "episode_2.txt").read_text(encoding="utf-8")
        assert ep2_file.endswith("漩涡之中。\n\n")

    async def test_plan_on_exhausted_source_returns_without_llm_call(self, tmp_path: Path):
        """If the last extracted episode already ends the source, return source_exhausted without model calls or ledger edits."""
        project_dir = _write_project(tmp_path, episodes=[_entry(1, 0, len(SOURCE))])
        fake = _FakeTextGenerator([])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert result.source_exhausted is True
        assert result.episodes == []
        assert fake.requests == []
        assert _load_project(project_dir)["episodes"] == [_entry(1, 0, len(SOURCE))]

    async def test_plan_advances_to_next_source_when_current_exhausted(self, tmp_path: Path):
        """With multiple sources, automatically continue at the next source beginning when the current one is fully planned."""
        source2 = "第二部 新的征程。李恒踏入上界，结识了新的同伴。"
        anchor2 = "结识了新的同伴。"
        project_dir = _write_project(
            tmp_path,
            episodes=[_entry(1, 0, len(SOURCE))],
            whole_source=("novel.txt", "novel2.txt"),
        )
        (project_dir / "source" / "novel2.txt").write_text(source2, encoding="utf-8")
        (project_dir / "source" / "episode_1.txt").write_text(SOURCE, encoding="utf-8")
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "新的征程", "hook": "上界有什么", "end_anchor": anchor2}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert "第二部" in fake.requests[0].prompt  # Take the window from the next source.
        project = _load_project(project_dir)
        eps = {e["episode"]: e for e in project["episodes"]}
        assert eps[2]["source_range"] == {"source_file": "source/novel2.txt", "start": 0, "end": len(source2)}
        assert result.cursor == {"source_file": "source/novel2.txt", "offset": len(source2)}
        assert (project_dir / "source" / "episode_2.txt").read_text(encoding="utf-8") == source2
        assert result.source_exhausted is True  # The second file also ends, with no more sources.

    async def test_each_whole_source_file_is_planned_by_its_own_source_kind(self, tmp_path: Path):
        """Drama plans novel/script files by their own source types; each window contains one file's source text only."""
        source2 = "第一场 夜 山门\n李恒：师父，我走了。\n"
        project_dir = _write_project(
            tmp_path,
            content_mode="drama",
            whole_source=("novel.txt", "novel2.txt"),
            source_kinds=("novel", "screenplay"),
        )
        (project_dir / "source" / "novel2.txt").write_text(source2, encoding="utf-8")
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "甲", "hook": "甲", "end_anchor": "卷入漩涡之中。", "story_beats": ["甲"]}]),
                _plan_response([{"title": "乙", "hook": "乙", "end_anchor": "我走了。", "story_beats": ["乙"]}]),
            ]
        )
        planner = EpisodePlanner(project_dir, generator=fake)

        first = await planner.plan()
        second = await planner.plan()

        novel_prompt, screenplay_prompt = (request.prompt for request in fake.requests)
        assert "# Original novel excerpt" in novel_prompt
        assert "李恒：师父" not in novel_prompt
        assert "# Original screenplay excerpt" in screenplay_prompt
        assert "卷入漩涡之中。" not in screenplay_prompt
        assert first.cursor == {"source_file": "source/novel.txt", "offset": len(SOURCE)}
        assert second.source_exhausted is True
        eps = _load_project(project_dir)["episodes"]
        assert [e["source_range"]["source_file"] for e in eps] == ["source/novel.txt", "source/novel2.txt"]

    async def test_plan_not_exhausted_when_more_source_files_remain(self, tmp_path: Path):
        """Reaching the current source end with later files remaining does not report source exhausted."""
        project_dir = _write_project(tmp_path, whole_source=("novel.txt", "novel2.txt"))
        (project_dir / "source" / "novel2.txt").write_text("第二部 新的征程。", encoding="utf-8")
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "甲", "hook": "甲", "end_anchor": ANCHOR_EP2},
                        {"title": "乙", "hook": "乙", "end_anchor": "卷入漩涡之中。"},
                    ]
                )
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert result.source_exhausted is False
        assert result.cursor == {"source_file": "source/novel.txt", "offset": len(SOURCE)}

    async def test_plan_rejects_when_entry_loses_source_range_during_model_call(self, tmp_path: Path):
        """Locked rechecks block commit if another writer adds unranged ledger entries during model calls, without writing the ledger."""
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator([_plan_response([{"title": "甲", "hook": "甲", "end_anchor": ANCHOR_EP1}])])
        planner = EpisodePlanner(project_dir, generator=fake)

        original_generate = fake.generate

        async def _generate_then_pollute(*args, **kwargs):
            result = await original_generate(*args, **kwargs)

            def _pollute(project: dict) -> None:
                project["episodes"] = [
                    {
                        "episode": 7,
                        "title": "旧流程切出集",
                        "script_file": "scripts/episode_7.json",
                        "source_origin": "whole_source",
                    }
                ]

            await _rewrite_project_elsewhere(project_dir, _pollute)
            return result

        fake.generate = _generate_then_pollute

        with pytest.raises(EpisodePlanningError, match="没有原文范围记录"):
            await planner.plan()

        episodes = _load_project(project_dir)["episodes"]
        assert [e["episode"] for e in episodes] == [7]
        assert not (project_dir / "source" / "episode_1.txt").exists()

    async def test_plan_forwards_instructions_into_prompt(self, tmp_path: Path):
        """Initial planning instructions enter a neutral additional-instructions section unchanged, without inherent strength qualifiers."""
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )

        await EpisodePlanner(project_dir, generator=fake).plan(instructions="严格按章节切分，一章一集")

        prompt = fake.requests[0].prompt
        assert "# Additional instructions" in prompt
        assert "严格按章节切分，一章一集" in prompt
        # Instruction bodies express their own adherence strength; injection templates add no strength qualifiers.
        assert "必须全部落实" not in prompt
        assert "must all be implemented" not in prompt

    async def test_plan_without_instructions_omits_section(self, tmp_path: Path):
        """Without instructions, omit the section and preserve today's pure plot-arc prompts exactly."""
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )

        await EpisodePlanner(project_dir, generator=fake).plan()

        prompt = fake.requests[0].prompt
        assert "# Additional instructions" not in prompt
        assert "必须全部落实" not in prompt
        assert "must all be implemented" not in prompt

    async def test_plan_blank_instructions_treated_as_absent(self, tmp_path: Path):
        """Whitespace-only instructions strip to absent; prompts exactly match omission, without instruction sections."""
        blank = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )
        none = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )

        await EpisodePlanner(_write_project(tmp_path / "b"), generator=blank).plan(instructions="   \n  ")
        await EpisodePlanner(_write_project(tmp_path / "n"), generator=none).plan()

        assert blank.requests[0].prompt == none.requests[0].prompt

    async def test_plan_with_instructions_injects_global_progress_section(self, tmp_path: Path):
        """Only instructions add global-progress sections; calculate planned counts, remainder, and window volume in reading units."""
        ep1_end = _end_of(ANCHOR_EP1)
        window_chars = ep1_end + 4
        project_dir = _write_project(
            tmp_path,
            episodes=[_entry(1, 0, ep1_end)],
        )
        fake = _FakeTextGenerator([_plan_response([{"title": "乙", "hook": "乙", "end_anchor": ANCHOR_EP2}])])

        await EpisodePlanner(project_dir, generator=fake, window_chars=window_chars).plan(
            instructions="严格按章节切分，一章一集"
        )

        prompt = fake.requests[0].prompt
        remaining_units = count_reading_units(SOURCE[ep1_end:], None)
        window_units = count_reading_units(SOURCE[ep1_end : ep1_end + window_chars], None)
        assert "# Overall progress" in prompt
        assert "1 episodes already planned" in prompt
        assert f"Approximately {remaining_units} 字 remain unplanned" in prompt
        assert f"This window contains the first {window_units} 字" in prompt

    async def test_plan_prompt_target_volume_line_reads_explicit_units(self, tmp_path: Path):
        """When both targets exist, explicit volume wins; prompts exactly match episode_target_units alone, without conversion."""
        both = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )
        units_only = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )
        both_dir = _write_project(tmp_path / "b", extra={"episode_target_units": 800, "episode_target_duration": 90})
        units_dir = _write_project(tmp_path / "u", extra={"episode_target_units": 800})

        await EpisodePlanner(both_dir, generator=both).plan()
        await EpisodePlanner(units_dir, generator=units_only).plan()

        expected = _expected_planning_prompt(
            "- Target volume per episode: approximately 800 字 (variation is allowed to preserve plot completeness)"
        )
        assert both.requests[0].prompt == expected
        assert units_only.requests[0].prompt == expected

    async def test_plan_prompt_target_volume_line_derived_from_target_duration(self, tmp_path: Path):
        """Duration-only targets produce converted volume with duration/speech rate, making its estimated nature clear to models."""
        project_dir = _write_project(tmp_path, content_mode="drama", extra={"episode_target_duration": 90})
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {
                            "title": "古玉藏诀",
                            "hook": "剑诀来历成谜",
                            "end_anchor": ANCHOR_EP1,
                            "story_beats": ["李恒获得古玉"],
                            "next_episode_teaser": "剑诀来历成谜",
                        }
                    ]
                )
            ]
        )

        await EpisodePlanner(project_dir, generator=fake).plan()

        assert fake.requests[0].prompt == _expected_planning_prompt(
            "- Target volume per episode: approximately 450 字 (roughly converted from a target episode duration of 90 seconds "
            "at a speaking rate of approximately 5 字/second; variation is allowed to preserve plot completeness)",
            content_mode="drama",
        )

    async def test_plan_prompt_target_volume_line_absent_when_neither_setting_is_present(self, tmp_path: Path):
        """Without either target, retain existing wording; models still judge by content on unconfigured paths."""
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )

        await EpisodePlanner(project_dir, generator=fake).plan()

        assert fake.requests[0].prompt == _expected_planning_prompt(_TARGET_VOLUME_UNSET_LINE)

    @pytest.mark.parametrize(
        ("content_mode", "source_kind"), [("narration", "novel"), ("drama", "novel"), ("drama", "screenplay")]
    )
    async def test_plan_prompt_locks_each_content_mode_and_source_kind(
        self, tmp_path: Path, content_mode: str, source_kind: str
    ):
        """Pin opening sentences, split rules, and source-excerpt headings per content/source-type combination; only drama has source types."""
        project_dir = _write_project(tmp_path, content_mode=content_mode, source_kinds=(source_kind,))
        draft = {"title": "古玉藏诀", "hook": "剑诀来历成谜", "end_anchor": ANCHOR_EP1}
        if content_mode == "drama":
            draft |= {"story_beats": ["李恒获得古玉"], "next_episode_teaser": None}
        fake = _FakeTextGenerator([_plan_response([draft])])

        await EpisodePlanner(project_dir, generator=fake).plan()

        assert fake.requests[0].prompt == _expected_planning_prompt(
            _TARGET_VOLUME_UNSET_LINE, content_mode=content_mode, source_kind=source_kind
        )

    async def test_plan_normal_batch_omits_ledger_stats(self, tmp_path: Path):
        """Normal nonexhausting batches omit global review material, reporting cumulative episode counts only."""
        window_chars = _end_of(ANCHOR_EP1) + 4
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator([_plan_response([{"title": "古玉藏诀", "hook": "钩子", "end_anchor": ANCHOR_EP1}])])

        result = await EpisodePlanner(project_dir, generator=fake, window_chars=window_chars).plan()

        assert result.source_exhausted is False
        assert result.ledger_stats is None
        assert result.total_planned == 1

    async def test_plan_exhausted_batch_includes_ledger_stats(self, tmp_path: Path):
        """Exhausting final batches include global review material: cumulative counts, smallest-volume episodes in ascending order, and median."""
        last_anchor = "卷入漩涡之中。"
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "甲", "hook": "甲", "end_anchor": ANCHOR_EP1},
                        {"title": "乙", "hook": "乙", "end_anchor": ANCHOR_EP2},
                        {"title": "丙", "hook": "丙", "end_anchor": last_anchor},
                    ]
                )
            ]
        )

        result = await EpisodePlanner(_write_project(tmp_path), generator=fake).plan()

        assert result.source_exhausted is True
        stats = result.ledger_stats
        assert stats is not None
        assert stats.total_episodes == 3
        assert [num for num, _units in stats.smallest] == [3, 2, 1]  # Ascending volume: 丙 < 乙 < 甲.
        assert stats.median_units == 37
        assert stats.target_volume is None  # Omit when neither target-volume source is configured.

    async def test_plan_exhausted_batch_reports_target_units_when_configured(self, tmp_path: Path):
        """With episode_target_units configured, review material includes that target volume."""
        project_dir = _write_project(tmp_path, extra={"episode_target_units": 800})
        fake = _FakeTextGenerator([_plan_response(_THREE_EPISODE_DRAFT)])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert result.ledger_stats is not None
        volume = result.ledger_stats.target_volume
        assert volume is not None
        assert (volume.units, volume.source) == (800, "units")

    async def test_plan_exhausted_batch_reports_target_volume_derived_from_duration(self, tmp_path: Path):
        """Duration-only targets echo converted volume and identify its source, telling the main Agent it is not user-supplied."""
        project_dir = _write_project(tmp_path, extra={"episode_target_duration": 90})
        fake = _FakeTextGenerator([_plan_response(_THREE_EPISODE_DRAFT)])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert result.ledger_stats is not None
        volume = result.ledger_stats.target_volume
        assert volume is not None
        assert (volume.units, volume.source, volume.seconds) == (450, "duration", 90)

    async def test_plan_no_new_content_early_exit_includes_ledger_stats(self, tmp_path: Path):
        """Repeated calls without new content return early without model calls, still including global review material."""
        project_dir = _planned_three(tmp_path)
        fake = _FakeTextGenerator([])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert result.source_exhausted is True
        assert fake.requests == []
        stats = result.ledger_stats
        assert stats is not None
        assert stats.total_episodes == 3
        assert [num for num, _units in stats.smallest] == [3, 2, 1]
        assert stats.median_units == 37

    async def test_plan_marks_stale_when_new_episode_num_has_downstream_products(self, tmp_path: Path):
        """Newly committed episode IDs with existing script outputs (such as replanning after reset to earlier overlapping IDs) become stale without deleting artifacts."""
        project_dir = _write_project(tmp_path)
        (project_dir / "scripts").mkdir()
        (project_dir / "scripts" / "episode_1.json").write_text("{}", encoding="utf-8")
        script_plan_path = project_dir / "drafts" / "episode_1" / "script_plan_segments.json"
        script_plan_path.parent.mkdir(parents=True)
        script_plan_path.write_text('{"segments": [{"segment_id": "E1S01"}]}', encoding="utf-8")
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert result.stale_episodes == [1]
        assert result.episodes[0].ledger_status == "stale"
        eps = {e["episode"]: e for e in _load_project(project_dir)["episodes"]}
        assert eps[1]["ledger_status"] == "stale"
        assert eps[1][script_review.STALE_SCRIPT_PLAN_REVISION_FIELD] == script_review.content_fingerprint(
            script_plan_path
        )
        assert (project_dir / "scripts" / "episode_1.json").exists()  # Preserve artifacts.

    async def test_plan_keeps_planned_when_no_downstream_products(self, tmp_path: Path):
        """New episode IDs without disk artifacts become planned normally, leaving stale_episodes empty."""
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert result.stale_episodes == []
        assert result.episodes[0].ledger_status == "planned"


def _entry(
    num: int,
    start: int,
    end: int,
    *,
    status: str = "planned",
    title: str | None = None,
    source_file: str = "source/novel.txt",
) -> dict:
    return {
        "episode": num,
        "title": title or f"第{num}集",
        "script_file": f"scripts/episode_{num}.json",
        "source_range": {"source_file": source_file, "start": start, "end": end},
        "source_origin": "whole_source",
        "hook": f"钩子{num}",
        "ledger_status": status,
    }


def _planned_three(tmp_path: Path, *, statuses: tuple[str, str, str] = ("planned", "planned", "planned")) -> Path:
    """A fully covered three-episode project has ranges [0,a) [a,b) [b,source end), deriving planning start at the end."""
    a, b = _end_of(ANCHOR_EP1), _end_of(ANCHOR_EP2)
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, 0, a, status=statuses[0]),
            _entry(2, a, b, status=statuses[1]),
            _entry(3, b, len(SOURCE), status=statuses[2]),
        ],
    )
    for num, (s, e) in enumerate([(0, a), (a, b), (b, len(SOURCE))], start=1):
        (project_dir / "source" / f"episode_{num}.txt").write_text(SOURCE[s:e], encoding="utf-8")
    return project_dir


# Second source: sequel content with uniquely locatable anchors.
SOURCE2 = "第二部 上界风云。李恒踏入上界，灵气扑面而来。他在坊市结识了云岚宗弟子苏沐。两人结伴前往宗门，途中遭遇兽潮。"

ANCHOR2_MID = "云岚宗弟子苏沐。"


class TestSourceFingerprintGate:
    """plan commits source fingerprints; entry comparisons reject changed sources."""

    async def test_plan_records_fingerprints_for_legacy_project_without_error(self, tmp_path: Path):
        """Legacy projects without fingerprint fields plan normally the first time and backfill them."""
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator([_plan_response([{"title": "t1", "hook": "h1", "end_anchor": ANCHOR_EP2}])])
        planner = EpisodePlanner(project_dir, generator=fake)

        await planner.plan()

        project = _load_project(project_dir)
        expected = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
        assert project[SOURCE_FINGERPRINTS_KEY] == {"source/novel.txt": expected}

    async def test_plan_rejects_when_recorded_fingerprint_mismatches(self, tmp_path: Path):
        stale_fp = hashlib.sha256("旧版本原文内容".encode()).hexdigest()
        project_dir = _write_project(tmp_path, extra={SOURCE_FINGERPRINTS_KEY: {"source/novel.txt": stale_fp}})
        planner = EpisodePlanner(project_dir, generator=_FakeTextGenerator([]))

        with pytest.raises(EpisodePlanningError, match=re.escape("source/novel.txt")):
            await planner.plan()

    async def test_plan_error_names_changed_file_and_points_to_reset(self, tmp_path: Path):
        stale_fp = hashlib.sha256(b"stale").hexdigest()
        project_dir = _write_project(tmp_path, extra={SOURCE_FINGERPRINTS_KEY: {"source/novel.txt": stale_fp}})
        planner = EpisodePlanner(project_dir, generator=_FakeTextGenerator([]))

        with pytest.raises(EpisodePlanningError, match="reset_episode_planning"):
            await planner.plan()

    async def test_full_reset_clears_gate_and_plan_recovers(self, tmp_path: Path):
        """Replacing source after long-form planning rejects plan; full reset then permits replanning, reproducing the reported scenario."""
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator([_plan_response([{"title": "t1", "hook": "h1", "end_anchor": ANCHOR_EP1}])])
        await EpisodePlanner(project_dir, generator=fake).plan()

        (project_dir / "source" / "novel.txt").write_text("全新的故事内容，这是重置后的新素材。", encoding="utf-8")
        blocked = EpisodePlanner(project_dir, generator=_FakeTextGenerator([]))
        with pytest.raises(EpisodePlanningError, match=re.escape("source/novel.txt")):
            await blocked.plan()

        result = reset_episode_planning(project_dir)
        assert isinstance(result, EpisodeResetResult)
        assert SOURCE_FINGERPRINTS_KEY not in _load_project(project_dir)

        fake2 = _FakeTextGenerator([_plan_response([{"title": "t2", "hook": "h2", "end_anchor": "重置后的新素材。"}])])
        result2 = await EpisodePlanner(project_dir, generator=fake2).plan()
        assert result2.episodes[0].title == "t2"

    async def test_partial_reset_retains_prefix_and_plan_allocates_fresh_ids(self, tmp_path: Path):
        """After planning two episodes, partial reset from episode 2 preserves episode 1 and returns planning start to its end; new planning does not reuse ID 2."""
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "t1", "hook": "h1", "end_anchor": ANCHOR_EP1},
                        {"title": "t2", "hook": "h2", "end_anchor": ANCHOR_EP2},
                    ]
                )
            ]
        )
        await EpisodePlanner(project_dir, generator=fake).plan()
        assert [e["episode"] for e in _load_project(project_dir)["episodes"]] == [1, 2]

        result = reset_episode_planning(project_dir, episode_id=2)
        assert isinstance(result, EpisodeResetResult)

        project = _load_project(project_dir)
        assert [e["episode"] for e in project["episodes"]] == [1]
        # Planning starts at episode 1 range end; retain fingerprints, verified against current source by partial reset.
        assert planning_start(project, _real_discover_sources(project_dir, project)) == (
            "source/novel.txt",
            _end_of(ANCHOR_EP1),
        )
        assert SOURCE_FINGERPRINTS_KEY in project
        # Preserve retained-range derived files and delete reset-range derived files.
        assert (project_dir / "source" / "episode_1.txt").is_file()
        assert not (project_dir / "source" / "episode_2.txt").exists()

        fake2 = _FakeTextGenerator([_plan_response([{"title": "t2b", "hook": "h2b", "end_anchor": ANCHOR_EP2}])])
        result2 = await EpisodePlanner(project_dir, generator=fake2).plan()

        assert [ep.episode for ep in result2.episodes] == [3]
        assert [e["episode"] for e in _load_project(project_dir)["episodes"]] == [1, 3]

    async def test_plan_rejects_when_source_changes_during_model_call_without_record(self, tmp_path: Path):
        """Legacy backfill rejects source changes during model calls by text rechecks at commit, without any writes."""
        project_dir = _write_project(tmp_path)
        source_path = project_dir / "source" / "novel.txt"

        class _MutatingGenerator(_FakeTextGenerator):
            async def generate(self, request, project_name=None):
                await asyncio.to_thread(source_path.write_text, "规划期间被换掉的全新原文内容。", encoding="utf-8")
                return await super().generate(request, project_name)

        fake = _MutatingGenerator([_plan_response([{"title": "t1", "hook": "h1", "end_anchor": ANCHOR_EP2}])])
        planner = EpisodePlanner(project_dir, generator=fake)

        with pytest.raises(EpisodePlanningError, match=re.escape("source/novel.txt")):
            await planner.plan()

        project = _load_project(project_dir)
        assert not project.get("episodes")
        assert SOURCE_FINGERPRINTS_KEY not in project

    async def test_plan_rejects_when_already_anchored_source_changes_during_model_call(self, tmp_path: Path):
        """Legacy multisource planning of novel2.txt must also reject novel.txt changes during calls if episode 1 already anchors it;
        checking only this batch source_rel would miss cross-file drift inside committed bounds."""
        project_dir = _write_project(
            tmp_path,
            episodes=[_entry(1, 0, len(SOURCE))],
            whole_source=("novel.txt", "novel2.txt"),
        )
        (project_dir / "source" / "novel2.txt").write_text(SOURCE2, encoding="utf-8")
        novel_path = project_dir / "source" / "novel.txt"

        class _MutatingGenerator(_FakeTextGenerator):
            async def generate(self, request, project_name=None):
                await asyncio.to_thread(novel_path.write_text, "已锚定的第1集原文被换掉。", encoding="utf-8")
                return await super().generate(request, project_name)

        fake = _MutatingGenerator([_plan_response([{"title": "t3", "hook": "h3", "end_anchor": ANCHOR2_MID}])])
        planner = EpisodePlanner(project_dir, generator=fake)

        with pytest.raises(EpisodePlanningError, match=re.escape("source/novel.txt")):
            await planner.plan()

        project = _load_project(project_dir)
        assert len(project.get("episodes") or []) == 1  # Episode 1 is unaffected and no new episodes are committed.
        assert SOURCE_FINGERPRINTS_KEY not in project

    async def test_plan_backfills_fingerprints_on_source_exhausted_early_return(self, tmp_path: Path):
        """Legacy fully exhausted projects return source_exhausted before commit closures, but must still backfill
        fingerprints or later equal-length source edits would pass without comparison baselines."""
        project_dir = _write_project(tmp_path, episodes=[_entry(1, 0, len(SOURCE))])
        planner = EpisodePlanner(project_dir, generator=_FakeTextGenerator([]))

        result = await planner.plan()

        assert result.source_exhausted is True
        project = _load_project(project_dir)
        expected = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
        assert project[SOURCE_FINGERPRINTS_KEY] == {"source/novel.txt": expected}

    async def test_plan_rejects_when_source_changes_between_snapshot_and_exhausted_backfill(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Legacy fully exhausted source changes between entry snapshot and exhaustion-backfill reads must
        reject against the entry snapshot, never registering changed content as a trusted baseline."""
        project_dir = _write_project(tmp_path, episodes=[_entry(1, 0, len(SOURCE))])
        source_path = project_dir / "source" / "novel.txt"
        call_count = 0

        def _mutating_discover_sources(project_path: Path, project: dict):
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # Change source between entry read 1 and backfill read 2.
                source_path.write_text("耗尽补记窗口期间被换掉的原文。", encoding="utf-8")
            return _real_discover_sources(project_path, project)

        monkeypatch.setattr("lib.episode.episode_planner.discover_sources", _mutating_discover_sources)
        planner = EpisodePlanner(project_dir, generator=_FakeTextGenerator([]))

        with pytest.raises(EpisodePlanningError, match=re.escape("source/novel.txt")):
            await planner.plan()

        project = _load_project(project_dir)
        assert SOURCE_FINGERPRINTS_KEY not in project

    async def test_plan_rejects_when_later_source_file_changes_during_model_call(self, tmp_path: Path):
        """With earlier files complete and the current window from a later manifest file, changes during model calls fail commit rechecks."""
        project_dir = _write_project(
            tmp_path,
            episodes=[_entry(1, 0, len(SOURCE))],
            whole_source=("novel.txt", "novel2.txt"),
        )
        novel2_path = project_dir / "source" / "novel2.txt"
        novel2_path.write_text(SOURCE2, encoding="utf-8")

        class _MutatingGenerator(_FakeTextGenerator):
            async def generate(self, request, project_name=None):
                await asyncio.to_thread(novel2_path.write_text, "新源文件在模型调用期间被换掉。", encoding="utf-8")
                return await super().generate(request, project_name)

        fake = _MutatingGenerator([_plan_response([{"title": "t3", "hook": "h3", "end_anchor": ANCHOR2_MID}])])
        planner = EpisodePlanner(project_dir, generator=fake)

        with pytest.raises(EpisodePlanningError, match=re.escape("source/novel2.txt")):
            await planner.plan()

        project = _load_project(project_dir)
        assert len(project.get("episodes") or []) == 1  # Episode 1 is unaffected and no new episodes are committed.
        assert SOURCE_FINGERPRINTS_KEY not in project


class TestEpisodeFileWrites:
    """Commit writes only this batch's new episode files, leaving all others untouched."""

    async def test_continuing_writes_only_the_new_episode_file(self, tmp_path: Path):
        a = _end_of(ANCHOR_EP1)
        project_dir = _write_project(tmp_path, episodes=[_entry(1, 0, a)])
        sentinel = "创作者改过的集文件"
        (project_dir / "source" / "episode_1.txt").write_text(sentinel, encoding="utf-8")
        fake = _FakeTextGenerator([_plan_response([{"title": "乙", "hook": "乙", "end_anchor": ANCHOR_EP3}])])

        await EpisodePlanner(project_dir, generator=fake).plan()

        assert (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8") == sentinel
        assert (project_dir / "source" / "episode_2.txt").read_text(encoding="utf-8") == SOURCE[a:]

    async def test_a_symlinked_file_on_the_new_episode_id_is_archived_not_followed(self, tmp_path: Path):
        project_dir = _write_project(tmp_path, episodes=[])
        outside = tmp_path / "outside.txt"
        outside.write_text("外部文件", encoding="utf-8")
        (project_dir / "source" / "episode_1.txt").symlink_to(outside)
        fake = _FakeTextGenerator([_plan_response(_THREE_EPISODE_DRAFT)])

        await EpisodePlanner(project_dir, generator=fake).plan()

        assert outside.read_text(encoding="utf-8") == "外部文件"
        assert (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8") == SOURCE[: _end_of(ANCHOR_EP1)]


class TestPlanGap:
    """Plan an unsplit source gap left by deleting a middle extracted episode, ending at the gap boundary without replacing any episodes."""

    @staticmethod
    def _gap_project(tmp_path: Path) -> Path:
        """Episodes 1/3 remain with episode 2 deleted and gap [a,b); self-sourced episode 9 lies between them."""
        a, b = _end_of(ANCHOR_EP1), _end_of(ANCHOR_EP2)
        own = {"episode": 9, "title": "番外", "script_file": "scripts/episode_9.json", "source_origin": "own"}
        project_dir = _write_project(
            tmp_path,
            episodes=[_entry(1, 0, a), own, _entry(3, b, len(SOURCE))],
            extra={"episode_id_high_water": 9},
        )
        (project_dir / "source" / "episode_1.txt").write_text(SOURCE[:a], encoding="utf-8")
        (project_dir / "source" / "episode_3.txt").write_text(SOURCE[b:], encoding="utf-8")
        (project_dir / "source" / "episode_9.txt").write_text("番外原文。", encoding="utf-8")
        return project_dir

    async def test_gap_is_planned_up_to_its_end_and_inserted_by_source_position(self, tmp_path: Path):
        project_dir = self._gap_project(tmp_path)
        a, b = _end_of(ANCHOR_EP1), _end_of(ANCHOR_EP2)
        # Anchor in the gap middle; align nonwhitespace remainder to the gap end.
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "辞别", "hook": "钩", "end_anchor": "踏上去往青云城的路。"},
                        {"title": "遇袭", "hook": "钩", "end_anchor": ANCHOR_EP2},
                    ]
                )
            ]
        )
        more = []

        async def _more() -> None:
            more.append(True)

        result = await EpisodePlanner(project_dir, generator=fake).plan(
            gap=("source/novel.txt", b), on_more_to_plan=_more
        )

        eps = _load_project(project_dir)["episodes"]
        assert [e["episode"] for e in eps] == [1, 10, 11, 9, 3]
        mid = _end_of("踏上去往青云城的路。")
        assert eps[1]["source_range"] == {"source_file": "source/novel.txt", "start": a, "end": mid}
        assert eps[2]["source_range"] == {"source_file": "source/novel.txt", "start": mid, "end": b}
        assert (project_dir / "source" / "episode_11.txt").read_text(encoding="utf-8") == SOURCE[mid:b]
        # Episode 3 unchanged.
        assert eps[4] == _entry(3, b, len(SOURCE))
        assert result.source_exhausted is True
        assert more == []
        prompt = fake.requests[0].prompt
        assert SOURCE[a:b] in prompt
        assert SOURCE[b:] not in prompt
        assert "An already planned next episode immediately follows" in prompt

    async def test_gap_before_the_first_cut_episode_goes_before_it(self, tmp_path: Path):
        a = _end_of(ANCHOR_EP1)
        project_dir = _write_project(tmp_path, episodes=[_entry(5, a, len(SOURCE))], extra={"episode_id_high_water": 5})
        (project_dir / "source" / "episode_5.txt").write_text(SOURCE[a:], encoding="utf-8")
        fake = _FakeTextGenerator([_plan_response([{"title": "甲", "hook": "甲", "end_anchor": ANCHOR_EP1}])])

        await EpisodePlanner(project_dir, generator=fake).plan(gap=("source/novel.txt", a))

        eps = _load_project(project_dir)["episodes"]
        assert [e["episode"] for e in eps] == [6, 5]
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": a}

    async def test_gap_that_is_no_longer_unsplit_is_rejected(self, tmp_path: Path):
        project_dir = _planned_three(tmp_path)
        before = (project_dir / "project.json").read_text(encoding="utf-8")
        fake = _FakeTextGenerator([])

        with pytest.raises(EpisodePlanningError):
            await EpisodePlanner(project_dir, generator=fake).plan(gap=("source/novel.txt", _end_of(ANCHOR_EP2) - 2))

        assert fake.requests == []
        assert (project_dir / "project.json").read_text(encoding="utf-8") == before

    async def test_a_large_gap_queues_the_next_window(self, tmp_path: Path):
        project_dir = self._gap_project(tmp_path)
        b = _end_of(ANCHOR_EP2)
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "辞别", "hook": "钩", "end_anchor": "踏上去往青云城的路。"}])]
        )
        more = []

        async def _more() -> None:
            more.append(True)

        planner = EpisodePlanner(project_dir, generator=fake, window_chars=30)
        result = await planner.plan(gap=("source/novel.txt", b), on_more_to_plan=_more)

        assert more == [True]
        assert result.source_exhausted is False
        assert [e["episode"] for e in _load_project(project_dir)["episodes"]] == [1, 10, 9, 3]
