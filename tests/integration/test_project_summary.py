"""项目摘要投影：广度视图（项目列表、卡片、全局头）读到的阶段与产物计数。

断言的是投影的外部输出——阶段归并、可用 / stale 计数、分集汇总，以及它与工作台
制作状态同用一份产物清单这件事，不断言内部调用顺序。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.artifact_activation import register_current_artifact
from lib.artifact_manifest import ArtifactKey
from lib.episode_ledger import SOURCE_FINGERPRINTS_KEY, compute_source_fingerprints, discover_sources
from lib.json_io import atomic_write_json
from lib.project_manager import ProjectManager
from lib.project_migrations.runner import migrate_project_with_verdict
from lib.resource_paths import resource_relative_path
from lib.source_revision import SourceRevisionResult, compute_source_revision
from lib.workflow_state import WorkflowStateService
from tests.integration.test_workflow_state import (
    _complete_episode_media,
    _count_source_reads,
    _make_project,
    _register_produced_artifacts,
    _valid_ad_shot,
    _valid_narration_segment,
    _valid_video_unit,
    _write_artifact,
    _write_episode_source,
    _write_registered_script,
    _write_source_and_complete,
)

pytestmark = pytest.mark.integration


def _plan_one_episode(pm: ProjectManager, project_path: Path, source_text: str) -> None:
    def _plan(project: dict) -> None:
        project["episodes"] = [
            {"episode": 1, "script_file": "scripts/episode_1.json", "ledger_status": "planned"},
        ]
        project["planning_cursor"] = {"source_file": "source/novel.txt", "offset": len(source_text)}
        project[SOURCE_FINGERPRINTS_KEY] = compute_source_fingerprints(discover_sources(project_path))

    pm.update_project("demo", _plan)


def _write_step1(project_path: Path, episode: int = 1) -> None:
    draft_dir = project_path / "drafts" / f"episode_{episode}"
    draft_dir.mkdir(parents=True, exist_ok=True)
    _write_episode_source(project_path, episode)
    atomic_write_json(draft_dir / "step1_segments.json", {"episode": episode, "segments": []})


def _add_character_with_sheet(pm: ProjectManager, project_path: Path, name: str = "小明") -> str:
    sheet = f"characters/{name}.png"
    _write_artifact(project_path, sheet)

    def _write(project: dict) -> None:
        project.setdefault("characters", {})[name] = {"description": "主角", "character_sheet": sheet}

    pm.update_project("demo", _write)
    return sheet


def _episode_with_media(pm: ProjectManager, project_path: Path, source_text: str = "完整原文") -> None:
    """把项目推到「一集脚本已生成、分镜与视频齐备」的状态。"""

    _plan_one_episode(pm, project_path, source_text)
    _write_step1(project_path)
    generated_assets = _complete_episode_media(project_path)
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [_valid_narration_segment(generated_assets=generated_assets)],
        },
    )
    _register_produced_artifacts(project_path)


def test_project_without_asset_inventory_is_in_preparation(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    (project_path / "source" / "novel.txt").write_text("原文", encoding="utf-8")

    summary = WorkflowStateService(pm).get_project_summary("demo")

    assert summary.phase == "preparation"
    assert summary.phase_progress == 0.0
    assert summary.episodes == []
    assert summary.episodes_summary.total == 0


def test_planned_episode_without_script_is_in_script_phase(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)
    _plan_one_episode(pm, project_path, source_text)
    _write_step1(project_path)
    register_current_artifact(project_path, ArtifactKey.episode_step1(1))

    summary = WorkflowStateService(pm).get_project_summary("demo")

    assert summary.phase == "script"
    assert summary.phase_progress == 0.0
    assert [episode.script_status for episode in summary.episodes] == ["segmented"]
    assert [episode.status for episode in summary.episodes] == ["draft"]


def test_script_without_media_is_in_production(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)
    _plan_one_episode(pm, project_path, source_text)
    _write_step1(project_path)
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [_valid_narration_segment()],
        },
    )
    _register_produced_artifacts(project_path)

    summary = WorkflowStateService(pm).get_project_summary("demo")

    assert summary.phase == "production"
    assert summary.phase_progress == 0.0
    episode = summary.episodes[0]
    assert episode.script_status == "generated"
    assert episode.status == "scripted"
    assert episode.item_count == 1
    assert episode.duration_seconds == 4
    assert (episode.storyboards.total, episode.storyboards.available) == (1, 0)
    assert (episode.videos.total, episode.videos.available) == (1, 0)


def test_item_count_reports_the_storyboard_count_on_the_storyboard_route(tmp_path: Path) -> None:
    """分镜图生视频报分镜数——广告/短片的 shots 与旁白/解说的 segments 同一口径。"""

    pm, project_path = _make_project(tmp_path, "ad")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)
    _plan_one_episode(pm, project_path, source_text)
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "ad",
            "shots": [_valid_ad_shot(), _valid_ad_shot(shot_id="E1S02")],
        },
    )
    _register_produced_artifacts(project_path)

    episode = WorkflowStateService(pm).get_project_summary("demo").episodes[0]

    assert episode.item_count == 2
    assert episode.storyboards.total == 2


def test_item_count_reports_the_video_unit_count_on_the_reference_route(tmp_path: Path) -> None:
    """参考生视频报视频单元数，且该生成模式没有分镜图这一档产物。"""

    pm, project_path = _make_project(tmp_path, "drama", generation_mode="reference_video")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)
    _plan_one_episode(pm, project_path, source_text)
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True, exist_ok=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "step1_reference_units.json", {"units": []})
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "drama",
            "video_units": [_valid_video_unit(), _valid_video_unit(unit_id="E1U02")],
        },
    )
    _register_produced_artifacts(project_path)

    episode = WorkflowStateService(pm).get_project_summary("demo").episodes[0]

    assert episode.item_count == 2
    assert episode.storyboards.total == 0
    assert episode.videos.total == 2


def test_all_artifacts_usable_reports_completed(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)

    summary = WorkflowStateService(pm).get_project_summary("demo")

    assert summary.phase == "completed"
    assert summary.phase_progress == 1.0
    assert summary.episodes_summary.model_dump() == {
        "total": 1,
        "scripted": 1,
        "in_production": 0,
        "completed": 1,
    }


def test_deleting_an_asset_sheet_drops_the_available_count_like_the_workbench(tmp_path: Path) -> None:
    """列表页与工作台同用产物清单：删掉一张资产图，两处一起从「可用」里掉出来。"""

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)
    sheet = _add_character_with_sheet(pm, project_path)
    _episode_with_media(pm, project_path, source_text)
    service = WorkflowStateService(pm)

    before = service.get_project_summary("demo")
    assert before.assets["character"].model_dump() == {"total": 1, "available": 1, "stale": 0}
    assert service.get_status("demo").artifacts["asset_sheets"]["character"]["current_ids"] == ["小明"]

    (project_path / sheet).unlink()

    after = service.get_project_summary("demo")
    assert after.assets["character"].model_dump() == {"total": 1, "available": 0, "stale": 0}
    assert service.get_status("demo").artifacts["asset_sheets"]["character"]["missing_ids"] == ["小明"]
    assert after.phase == "production"


def test_deleting_a_video_drops_the_episode_out_of_completed(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)
    service = WorkflowStateService(pm)
    assert service.get_project_summary("demo").episodes[0].status == "completed"

    (project_path / resource_relative_path("videos", "E1S01")).unlink()

    summary = service.get_project_summary("demo")
    episode = summary.episodes[0]
    assert (episode.videos.total, episode.videos.available) == (1, 0)
    assert episode.status == "in_production"
    assert summary.phase == "production"
    assert summary.episodes_summary.completed == 0


def test_stale_ledger_episode_falls_back_to_pending_preprocess(tmp_path: Path) -> None:
    """重新规划使该集原文范围失效：脚本仍在盘上，但该集回到待内容整理，不计入已生成。"""

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)

    def _mark_stale(project: dict) -> None:
        project["episodes"][0]["ledger_status"] = "stale"

    pm.update_project("demo", _mark_stale)

    summary = WorkflowStateService(pm).get_project_summary("demo")

    assert summary.phase == "script"
    assert [episode.script_status for episode in summary.episodes] == ["none"]
    assert summary.episodes_summary.scripted == 0
    assert summary.episodes[0].videos.total == 0


def test_stale_artifacts_stay_available_and_are_counted_separately(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)
    sheet = _add_character_with_sheet(pm, project_path)
    _episode_with_media(pm, project_path, source_text)

    def _redescribe(project: dict) -> None:
        project["characters"]["小明"]["description"] = "改过的设定"

    pm.update_project("demo", _redescribe)
    assert (project_path / sheet).exists()

    summary = WorkflowStateService(pm).get_project_summary("demo")

    assert summary.assets["character"].model_dump() == {"total": 1, "available": 1, "stale": 1}


def test_summary_never_reads_the_source_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """列出 N 个项目不该读 N 份小说：整本源文与源文修订号都不进本投影。

    分集原文（``source/episode_N.txt``）是另一回事——它是产物清单重建 step1 基线的输入，
    每集至多读一次，与工作台比对同一件产物付的代价相同。这条断言把两者分开钉住：
    一旦有人为了算阶段又去读整本源文、或为了修订号做全量 sha256，它就会红。
    """

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)

    revision_calls: list[Path] = []

    def _spy(project_path: Path, *args: object, **kwargs: object) -> SourceRevisionResult:
        revision_calls.append(project_path)
        return compute_source_revision(project_path, *args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr("lib.workflow_state.compute_source_revision", _spy)
    source_reads = _count_source_reads(monkeypatch, project_path)
    summary = WorkflowStateService(pm).get_project_summary("demo")

    assert summary.phase == "completed"
    # 整本源文（含 source/ 直下其余文件）一次不读，修订号也不算。
    assert revision_calls == []
    assert "novel.txt" not in source_reads
    # 分集原文每集至多一次：产物清单比对 step1 基线的必需读。
    assert source_reads == {"episode_1.txt": 1}


def test_migration_blocked_project_is_listed_as_needing_repair(tmp_path: Path) -> None:
    from tests.test_project_migration_v7_v8 import _project

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(projects_root)
    script_path = project_dir / "scripts" / "episode_1.json"
    script = script_path.read_text(encoding="utf-8").replace('"segment_id"', '"dropped_id"', 1)
    script_path.write_text(script, encoding="utf-8")
    failure = migrate_project_with_verdict(project_dir)
    assert failure is not None

    summary = WorkflowStateService(ProjectManager(str(projects_root))).get_project_summary("demo")

    assert summary.needs_repair is True
    assert summary.repair_reason == failure.reason
    assert summary.phase == "preparation"
    # 产物清单对未升级的数据不可读，一件产物都不报可用；集数照常列出，项目不从列表里消失。
    assert summary.episodes_summary.total == len(summary.episodes)
    assert all(episode.videos.available == 0 for episode in summary.episodes)


def test_deleting_a_storyboard_drops_the_episode_out_of_completed(tmp_path: Path) -> None:
    """分镜图也是制作阶段的产物：删掉一张，大厅与工作台一起退回「制作」。"""

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)
    service = WorkflowStateService(pm)
    assert service.get_project_summary("demo").phase == "completed"

    (project_path / resource_relative_path("storyboards", "E1S01")).unlink()

    summary = service.get_project_summary("demo")
    episode = summary.episodes[0]
    assert (episode.storyboards.total, episode.storyboards.available) == (1, 0)
    assert episode.status == "in_production"
    assert summary.phase == "production"
    assert summary.phase_progress < 1.0
    assert service.get_status("demo").state == "STORYBOARD"


def test_episode_counts_match_the_workbench_on_the_same_project(tmp_path: Path) -> None:
    """剧集卡读的每集计数与工作台读的产物清单是同一份数字。

    剧集卡拿 ``get_project_summary``、工作台拿 ``get_status``：同一个项目上两处必须给出
    相同的可用数与 stale 数，否则用户在侧栏与画布之间会看到互相矛盾的进度。
    """

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)
    service = WorkflowStateService(pm)

    # 改写该镜的画面 prompt：分镜图还在盘上、仍可用，但已比当前内容旧。
    script_path = project_path / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["segments"][0]["image_prompt"] = "改过的画面描述"
    atomic_write_json(script_path, script)

    episode = service.get_project_summary("demo").episodes[0]
    status = service.get_status("demo", episode=1)

    for label, count in (("storyboards", episode.storyboards), ("videos", episode.videos)):
        workbench = status.artifacts[label]
        assert count.stale == len(workbench["stale_ids"]), label
        assert count.available == len(workbench["current_ids"]) + len(workbench["stale_ids"]), label
        assert count.total == (
            len(workbench["current_ids"]) + len(workbench["stale_ids"]) + len(workbench["missing_ids"])
        ), label

    # 夹具本身要有区分力：两侧全 0 相等不构成证据。
    assert episode.storyboards.stale == 1
    assert episode.storyboards.available == 1
