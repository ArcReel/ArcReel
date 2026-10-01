"""重新规划：在真实的临时项目目录上只经公开命令验证候选的生成、采纳与放弃。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.backends.text_backends.base import TextGenerationResult
from lib.episode.episode_ledger import SOURCE_FINGERPRINTS_KEY
from lib.episode.episode_planner import EpisodePlanner
from lib.episode.episode_replan import (
    REPLAN_CANDIDATE_KEY,
    ReplanAdoptionResult,
    ReplanConfirmationRequired,
    ReplanError,
    adopt_replan_candidate,
    create_replan_candidate,
    discard_replan_candidate,
    record_replan_interruption,
    render_replan_adoption_text,
    replan_candidate_summary,
    replan_scope,
    resume_replan_candidate,
)
from lib.episode.episode_reset import reset_episode_planning
from lib.i18n import _ as i18n_message
from lib.script.script_review import STALE_SCRIPT_PLAN_REVISION_FIELD

CH = ("第一章。少年下山。", "第二章。城里起火。", "第三章。夜雨相逢。", "第四章。重逢离别。")
SOURCE = "".join(CH)
#: 各章在源文里的起点；最后一项是源文结尾。
CUTS = [sum(len(c) for c in CH[:i]) for i in range(len(CH) + 1)]


def _cut(episode: int, chapter: int, **fields) -> dict:
    return {
        "episode": episode,
        "title": f"旧{episode}",
        "script_file": f"scripts/episode_{episode}.json",
        "source_origin": "whole_source",
        "source_range": {"source_file": "source/novel.txt", "start": CUTS[chapter], "end": CUTS[chapter + 1]},
        "hook": "旧钩子",
        "ledger_status": "planned",
        **fields,
    }


def _own(episode: int) -> dict:
    return {
        "episode": episode,
        "title": f"番外{episode}",
        "script_file": f"scripts/episode_{episode}.json",
        "source_origin": "own",
    }


def _project_dir(tmp_path: Path, episodes: list[dict], **fields) -> Path:
    project_dir = tmp_path / "projects" / "demo"
    (project_dir / "source").mkdir(parents=True)
    (project_dir / "source" / "novel.txt").write_text(SOURCE, encoding="utf-8")
    project = {
        "schema_version": 3,
        "title": "测试项目",
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "style": "国漫",
        "characters": {},
        "scenes": {},
        "props": {},
        "whole_source_files": [{"source_file": "source/novel.txt"}],
        "episodes": episodes,
        "episode_id_high_water": max([e["episode"] for e in episodes], default=0),
        **fields,
    }
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    for entry in episodes:
        source_range = entry.get("source_range")
        if source_range:
            text = SOURCE[source_range["start"] : source_range["end"]]
            (project_dir / "source" / f"episode_{entry['episode']}.txt").write_text(text, encoding="utf-8")
        elif entry.get("source_origin") == "own":
            (project_dir / "source" / f"episode_{entry['episode']}.txt").write_text("番外原文。", encoding="utf-8")
    return project_dir


def _load(project_dir: Path) -> dict:
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))


def _order(project_dir: Path) -> list[int]:
    return [entry["episode"] for entry in _load(project_dir)["episodes"]]


def _entry(project_dir: Path, episode: int) -> dict:
    return next(entry for entry in _load(project_dir)["episodes"] if entry["episode"] == episode)


def _give_products(project_dir: Path, *episodes: int) -> None:
    (project_dir / "scripts").mkdir(exist_ok=True)
    for episode in episodes:
        (project_dir / "scripts" / f"episode_{episode}.json").write_text("{}", encoding="utf-8")


class _Generator:
    """窗口里出现的每个锚点各回一集。"""

    model = "fake-model"
    max_output_tokens = 64000

    def __init__(self, anchors: list[str]) -> None:
        self.anchors = anchors
        self.prompts: list[str] = []

    async def generate(self, request, project_name=None) -> TextGenerationResult:
        del project_name
        self.prompts.append(request.prompt)
        window = request.prompt.rsplit("---", 2)[-2]
        episodes = [
            {"title": f"新{index + 1}", "hook": "新钩子", "end_anchor": anchor}
            for index, anchor in enumerate(self.anchors)
            if anchor in window
        ]
        return TextGenerationResult(
            text=json.dumps({"episodes": episodes}, ensure_ascii=False), provider="fake", model="fake-model"
        )


async def _generate(project_dir: Path, episode: int, anchors: list[str], *, instructions: str | None = None) -> str:
    candidate_id = create_replan_candidate(project_dir, episode=episode, instructions=instructions)
    planner = EpisodePlanner(project_dir, generator=_Generator(anchors))
    result = await planner.plan_candidate(candidate_id, instructions)
    assert result.source_exhausted is True
    return candidate_id


def _adopt(project_dir: Path, candidate_id: str, *, delete_retired: bool = False) -> ReplanAdoptionResult:
    preview = adopt_replan_candidate(project_dir, candidate_id)
    assert isinstance(preview, ReplanConfirmationRequired)
    result = adopt_replan_candidate(
        project_dir, candidate_id, revision=preview.impact.revision, delete_retired=delete_retired
    )
    assert isinstance(result, ReplanAdoptionResult)
    return result


class TestCandidate:
    async def test_the_ledger_stays_untouched_until_the_candidate_is_adopted(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1), _cut(3, 2), _cut(4, 3)])
        ledger_before = _load(project_dir)["episodes"]

        # 第 3、4 章合成一集
        candidate_id = await _generate(project_dir, 3, ["重逢离别。"], instructions="合并后两章")

        project = _load(project_dir)
        assert project["episodes"] == ledger_before
        candidate = project[REPLAN_CANDIDATE_KEY]
        assert candidate["complete"] is True
        assert candidate["instructions"] == "合并后两章"
        assert [e["source_range"] for e in candidate["episodes"]] == [
            {"source_file": "source/novel.txt", "start": CUTS[2], "end": CUTS[4]}
        ]

        result = _adopt(project_dir, candidate_id)

        assert result.episodes == [5]
        assert _order(project_dir) == [1, 2, 5]
        new = _entry(project_dir, 5)
        assert (new["title"], new["source_origin"], new["ledger_status"]) == ("新1", "whole_source", "planned")
        assert (project_dir / "source" / "episode_5.txt").read_text(encoding="utf-8") == SOURCE[CUTS[2] :]
        assert not (project_dir / "source" / "episode_3.txt").exists()
        assert not (project_dir / "source" / "episode_4.txt").exists()
        assert REPLAN_CANDIDATE_KEY not in _load(project_dir)

    async def test_generation_starts_from_the_episode_with_earlier_episodes_as_context(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1), _cut(3, 2), _cut(4, 3)])
        candidate_id = create_replan_candidate(project_dir, episode=3, instructions=None)
        generator = _Generator(["夜雨相逢。", "重逢离别。"])

        await EpisodePlanner(project_dir, generator=generator).plan_candidate(candidate_id)

        prompt = generator.prompts[0]
        assert SOURCE[CUTS[2] :] in prompt
        assert CH[1] not in prompt
        assert "旧2" in prompt

    def test_only_one_candidate_at_a_time(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1)])
        create_replan_candidate(project_dir, episode=2, instructions=None)

        with pytest.raises(ReplanError) as exc:
            create_replan_candidate(project_dir, episode=1, instructions=None)

        assert exc.value.code == "candidate_pending"

    def test_discarding_changes_nothing_in_the_ledger(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1)])
        ledger_before = _load(project_dir)["episodes"]
        candidate_id = create_replan_candidate(project_dir, episode=1, instructions=None)

        discard_replan_candidate(project_dir, candidate_id)

        project = _load(project_dir)
        assert project["episodes"] == ledger_before
        assert REPLAN_CANDIDATE_KEY not in project

    def test_the_scope_names_the_replaced_episodes_already_in_production(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1), _own(9), _cut(3, 2)])
        _give_products(project_dir, 3)

        scope = replan_scope(project_dir, _load(project_dir), 2)

        assert (scope.source_file, scope.offset, scope.from_beginning) == ("source/novel.txt", CUTS[1], False)
        assert scope.replaced == [2, 3]
        assert scope.started == [3]

    def test_legacy_episodes_allow_replanning_only_from_the_first_cut_episode(self, tmp_path: Path):
        legacy = {**_cut(2, 1)}
        legacy.pop("source_range")
        project_dir = _project_dir(tmp_path, [_cut(1, 0), legacy])

        with pytest.raises(ReplanError) as exc:
            replan_scope(project_dir, _load(project_dir), 2)
        scope = replan_scope(project_dir, _load(project_dir), 1)

        assert exc.value.code == "from_first_only"
        assert (scope.offset, scope.from_beginning, scope.replaced) == (0, True, [1, 2])


async def _generate_partial(
    project_dir: Path, episode: int, anchors: list[str], *, instructions: str | None = None
) -> str:
    """只生成一批就停下：候选没有覆盖到整本源文结尾。"""
    candidate_id = create_replan_candidate(project_dir, episode=episode, instructions=instructions)
    planner = EpisodePlanner(project_dir, generator=_Generator(anchors))
    result = await planner.plan_candidate(candidate_id, instructions)
    assert result.source_exhausted is False
    return candidate_id


class TestInterruptedCandidate:
    async def test_adopting_a_partial_candidate_replaces_the_episodes_beyond_it_too(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1), _cut(3, 2), _cut(4, 3), _own(9)])
        _give_products(project_dir, 4)
        candidate_id = await _generate_partial(project_dir, 2, ["城里起火。"])

        summary = replan_candidate_summary(project_dir, _load(project_dir))
        result = _adopt(project_dir, candidate_id)

        assert summary is not None
        assert (summary["complete"], summary["uncovered"]) == (False, [3, 4])
        assert result.impact.uncovered == [3, 4]
        assert (result.impact.retired, result.impact.removed) == ([4], [2, 3])
        assert result.episodes == [10]
        assert _order(project_dir) == [1, 10, 9, 4]
        assert _entry(project_dir, 4)["source_origin"] == "none"
        assert not (project_dir / "source" / "episode_3.txt").exists()

    async def test_the_confirmation_names_the_episodes_beyond_the_candidate(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1), _cut(3, 2)])
        candidate_id = await _generate_partial(project_dir, 1, ["少年下山。"])
        preview = adopt_replan_candidate(project_dir, candidate_id)
        assert isinstance(preview, ReplanConfirmationRequired)

        texts = render_replan_adoption_text(
            preview.impact, _load(project_dir), lambda key, **params: i18n_message(key, locale="zh", **params)
        )

        assert "超出新方案的范围" in texts["text"]
        assert "旧2、旧3" in texts["text"]

    async def test_a_complete_candidate_has_nothing_beyond_it(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1)])
        await _generate(project_dir, 1, ["重逢离别。"])

        summary = replan_candidate_summary(project_dir, _load(project_dir))

        assert summary is not None
        assert summary["uncovered"] == []

    async def test_resuming_clears_the_interruption_and_keeps_the_instructions(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1), _cut(3, 2)])
        candidate_id = await _generate_partial(project_dir, 1, ["少年下山。"], instructions="节奏放慢")
        record_replan_interruption(project_dir, candidate_id, "no_cut_point")
        assert replan_candidate_summary(project_dir, _load(project_dir))["interrupted"] == "no_cut_point"

        instructions = resume_replan_candidate(project_dir, candidate_id)
        await EpisodePlanner(project_dir, generator=_Generator(["重逢离别。"])).plan_candidate(
            candidate_id, instructions
        )

        summary = replan_candidate_summary(project_dir, _load(project_dir))
        assert instructions == "节奏放慢"
        assert summary["interrupted"] is None
        assert summary["complete"] is True
        assert summary["new_count"] == 2

    async def test_a_complete_or_stale_candidate_cannot_be_resumed(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1)])
        complete = await _generate(project_dir, 1, ["重逢离别。"])
        with pytest.raises(ReplanError) as done:
            resume_replan_candidate(project_dir, complete)
        discard_replan_candidate(project_dir, complete)
        partial = await _generate_partial(project_dir, 1, ["少年下山。"])
        (project_dir / "source" / "novel.txt").write_text(SOURCE + "尾声。", encoding="utf-8")

        with pytest.raises(ReplanError) as stale:
            resume_replan_candidate(project_dir, partial)

        assert done.value.code == "candidate_complete"
        assert stale.value.code == "source_changed"

    async def test_a_cross_file_episode_ending_beyond_the_candidate_is_named(self, tmp_path: Path):
        # a.txt 是第一、二章，b.txt 是第三、四章；第 2 集从第二章跨到第三章结尾。方案只生成了 a.txt 里的第二章
        project_dir = _project_dir(tmp_path, [])
        source_dir = project_dir / "source"
        (source_dir / "novel.txt").unlink()
        (source_dir / "a.txt").write_text(CH[0] + CH[1], encoding="utf-8")
        (source_dir / "b.txt").write_text(CH[2] + CH[3], encoding="utf-8")
        ch = len(CH[0])
        project = _load(project_dir)
        project["whole_source_files"] = [{"source_file": "source/a.txt"}, {"source_file": "source/b.txt"}]
        project["episodes"] = [
            {**_cut(1, 0), "source_range": {"source_file": "source/a.txt", "start": 0, "end": ch}},
            {
                **_cut(2, 1),
                "source_range": {"source_file": "source/a.txt", "start": ch, "end_file": "source/b.txt", "end": ch},
            },
            {**_cut(3, 3), "source_range": {"source_file": "source/b.txt", "start": ch, "end": 2 * ch}},
        ]
        project["episode_id_high_water"] = 3
        (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        candidate_id = await _generate_partial(project_dir, 2, ["城里起火。"])

        summary = replan_candidate_summary(project_dir, _load(project_dir))
        result = _adopt(project_dir, candidate_id)

        assert summary is not None
        assert summary["uncovered"] == [2, 3]
        assert result.impact.uncovered == [2, 3]
        assert _order(project_dir) == [1, 4]

    def test_an_interruption_for_a_candidate_that_is_gone_changes_nothing(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0)])
        before = _load(project_dir)

        record_replan_interruption(project_dir, "gone", "failed")

        assert _load(project_dir) == before


class TestAdoption:
    async def test_replaced_episodes_with_products_stay_without_source_and_move_to_the_end(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1), _cut(3, 2), _cut(4, 3), _own(9)])
        _give_products(project_dir, 3)
        candidate_id = await _generate(project_dir, 2, ["夜雨相逢。", "重逢离别。"])

        result = _adopt(project_dir, candidate_id)

        assert result.impact.retired == [3]
        assert result.impact.removed == [2, 4]
        assert result.impact.needs_review == [3]
        assert result.episodes == [10, 11]
        assert _order(project_dir) == [1, 10, 11, 9, 3]
        retired = _entry(project_dir, 3)
        assert (retired["source_origin"], retired["ledger_status"]) == ("none", "stale")
        assert "source_range" not in retired
        assert STALE_SCRIPT_PLAN_REVISION_FIELD in retired
        assert (project_dir / "scripts" / "episode_3.json").is_file()

    async def test_delete_retired_removes_them_with_their_products(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1)])
        _give_products(project_dir, 2)
        candidate_id = await _generate(project_dir, 2, ["重逢离别。"])

        result = _adopt(project_dir, candidate_id, delete_retired=True)

        assert result.deleted == [2]
        assert _order(project_dir) == [1, 3]
        assert not (project_dir / "scripts" / "episode_2.json").exists()

    async def test_episodes_of_other_origins_follow_the_story_by_their_anchor(self, tmp_path: Path):
        # 番外 7 排在第 1 集之后（锚点 = 第 1 集结尾 = 起点）；番外 8 排在第 2 集之后（锚点在第二章结尾）；
        # 番外 9 排在末尾（锚点在第四章结尾）
        project_dir = _project_dir(
            tmp_path, [_cut(1, 0), _own(7), _cut(2, 1), _own(8), _cut(3, 2), _cut(4, 3), _own(9)]
        )
        # 新方案把第二章和第三章的前半合成一集，第三章后半与第四章合成一集
        candidate_id = await _generate(project_dir, 2, ["夜雨", "重逢离别。"])

        result = _adopt(project_dir, candidate_id)

        assert result.episodes == [10, 11]
        assert _order(project_dir) == [1, 7, 10, 8, 11, 9]
        assert result.impact.moved == [(9, 7, 6)]

    async def test_the_episode_right_at_the_start_goes_before_all_candidate_episodes(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_own(7), _cut(1, 0), _cut(2, 1), _cut(3, 2), _cut(4, 3)])
        candidate_id = await _generate(project_dir, 1, ["城里起火。", "重逢离别。"])

        _adopt(project_dir, candidate_id)

        assert _order(project_dir) == [7, 8, 9]

    async def test_an_episode_after_a_cross_file_episode_follows_where_that_episode_ends(self, tmp_path: Path):
        # 源文分成两个文件：a.txt 是第一、二章，b.txt 是第三、四章；第 2 集从第二章跨到第三章结尾
        project_dir = _project_dir(tmp_path, [])
        source_dir = project_dir / "source"
        (source_dir / "novel.txt").unlink()
        (source_dir / "a.txt").write_text(CH[0] + CH[1], encoding="utf-8")
        (source_dir / "b.txt").write_text(CH[2] + CH[3], encoding="utf-8")
        ch = len(CH[0])
        crossing = {
            **_cut(2, 1),
            "source_range": {"source_file": "source/a.txt", "start": ch, "end_file": "source/b.txt", "end": ch},
        }
        tail = {**_cut(3, 3), "source_range": {"source_file": "source/b.txt", "start": ch, "end": 2 * ch}}
        project = _load(project_dir)
        project["whole_source_files"] = [{"source_file": "source/a.txt"}, {"source_file": "source/b.txt"}]
        project["episodes"] = [
            {**_cut(1, 0), "source_range": {"source_file": "source/a.txt", "start": 0, "end": ch}},
            crossing,
            _own(7),
            tail,
        ]
        project["episode_id_high_water"] = 7
        (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        (source_dir / "episode_7.txt").write_text("番外原文。", encoding="utf-8")
        # 新方案：第二章、第三章、第四章各一集；第 2 集原来的结尾在第三章结尾。窗口逐个文件取，生成到源文结尾
        candidate_id = create_replan_candidate(project_dir, episode=2, instructions=None)
        planner = EpisodePlanner(project_dir, generator=_Generator(["城里起火。", "夜雨相逢。", "重逢离别。"]))
        while not (await planner.plan_candidate(candidate_id, None)).source_exhausted:
            pass

        result = _adopt(project_dir, candidate_id)

        assert result.episodes == [8, 9, 10]
        assert _order(project_dir) == [1, 8, 9, 7, 10]

    async def test_a_ledger_change_after_generation_rejects_the_adoption(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1), _cut(3, 2), _cut(4, 3)])
        candidate_id = await _generate(project_dir, 3, ["重逢离别。"])
        reset_episode_planning(project_dir, episode_id=4)

        with pytest.raises(ReplanError) as exc:
            adopt_replan_candidate(project_dir, candidate_id)

        assert exc.value.code == "ledger_changed"
        assert replan_candidate_summary(project_dir, _load(project_dir))["stale"] == "ledger_changed"

    async def test_a_source_change_after_generation_rejects_the_adoption(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1)])
        candidate_id = await _generate(project_dir, 2, ["重逢离别。"])
        (project_dir / "source" / "novel.txt").write_text(SOURCE + "尾声。", encoding="utf-8")

        with pytest.raises(ReplanError) as exc:
            adopt_replan_candidate(project_dir, candidate_id)

        assert exc.value.code == "source_changed"

    async def test_a_stale_confirmation_writes_nothing_and_returns_the_new_list(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1)])
        candidate_id = await _generate(project_dir, 2, ["重逢离别。"])
        preview = adopt_replan_candidate(project_dir, candidate_id)
        assert isinstance(preview, ReplanConfirmationRequired)
        _give_products(project_dir, 2)
        ledger_before = _load(project_dir)["episodes"]

        outcome = adopt_replan_candidate(project_dir, candidate_id, revision=preview.impact.revision)

        assert isinstance(outcome, ReplanConfirmationRequired)
        assert outcome.impact.retired == [2]
        assert _load(project_dir)["episodes"] == ledger_before

    async def test_adoption_replaces_ledger_fingerprints_and_snapshots(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1)])
        candidate_id = await _generate(project_dir, 1, ["少年下山。", "重逢离别。"])

        _adopt(project_dir, candidate_id)

        project = _load(project_dir)
        assert set(project[SOURCE_FINGERPRINTS_KEY]) == {"source/novel.txt"}
        assert (project_dir / "source" / "snapshots" / "novel.txt").read_text(encoding="utf-8") == SOURCE


class TestSummaryAndText:
    async def test_summary_lists_counts_coverage_and_per_episode_changes(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1), _cut(3, 2), _cut(4, 3), _own(9)])
        _give_products(project_dir, 3)
        await _generate(project_dir, 2, ["城里起火。", "重逢离别。"], instructions="后两章合并")

        summary = replan_candidate_summary(project_dir, _load(project_dir))

        assert summary is not None
        assert (summary["old_count"], summary["new_count"], summary["complete"], summary["stale"]) == (3, 2, True, None)
        assert summary["instructions"] == "后两章合并"
        assert summary["start"] == {"source_file": "source/novel.txt", "offset": CUTS[1]}
        assert summary["end"] == {"source_file": "source/novel.txt", "offset": CUTS[4]}
        assert (summary["retired"], summary["removed"], summary["needs_review"]) == ([3], [2, 4], [3])
        assert [(e["same_as"], e["overlaps"]) for e in summary["episodes"]] == [(2, [2]), (None, [3, 4])]

    @pytest.mark.parametrize("locale", ["zh", "en", "vi"])
    async def test_the_confirmation_text_renders_in_every_locale(self, tmp_path: Path, locale: str):
        project_dir = _project_dir(tmp_path, [_cut(1, 0), _cut(2, 1), _cut(3, 2), _own(9)])
        _give_products(project_dir, 2)
        candidate_id = await _generate(project_dir, 2, ["夜雨相逢。", "重逢离别。"])
        preview = adopt_replan_candidate(project_dir, candidate_id)
        assert isinstance(preview, ReplanConfirmationRequired)

        texts = render_replan_adoption_text(
            preview.impact.to_dict(),
            _load(project_dir),
            lambda key, **params: i18n_message(key, locale=locale, **params),
        )

        assert "旧2" in texts["text"]
        assert "番外9" in texts["text"]
        assert "旧2" in texts["delete_text"]
        assert "{" not in texts["text"] + texts["delete_text"]
