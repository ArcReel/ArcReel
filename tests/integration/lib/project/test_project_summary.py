"""项目摘要投影：广度视图（项目列表、卡片、全局头）读到的集进度与产物计数。

断言的是投影的外部输出——各集进度、可用 / stale 计数、分集汇总，以及它与工作台
制作状态同用一份产物清单这件事，不断言内部调用顺序。两种产物口径各有一组：
``verified`` 与工作台逐件同数，``registered`` 只看登记与在场、不读产物内容。
"""

from __future__ import annotations

import builtins
import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import IO, Any

import portalocker
import pytest

from lib.artifacts.artifact_activation import register_current_artifact
from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import (
    LOCK_FILENAME,
    MANIFEST_FILENAME,
    ArtifactKey,
    ArtifactManifest,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
    reset_manifest_read_cache_for_tests,
)
from lib.artifacts.version_manager import VersionManager
from lib.episode.episode_ledger import SOURCE_FINGERPRINTS_KEY, compute_source_fingerprints
from lib.episode.episode_sources import discover_sources
from lib.infra.content_digest import reset_file_digest_cache_for_tests
from lib.infra.json_io import atomic_write_json
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations.runner import migrate_project_with_verdict
from lib.project.resource_paths import resource_relative_path
from lib.project.source_revision import SourceRevisionResult, compute_source_revision
from lib.script import script_review
from lib.workflow.workflow_state import WorkflowStateService
from server.services.project.project_archive import ARCHIVE_MANIFEST_NAME, ProjectArchiveService
from tests.factories import register_project_sources
from tests.integration.lib.artifacts.test_content_digest_currency import _age, _count_content_reads
from tests.integration.lib.episode_media_support import episodes_with_media
from tests.integration.lib.manifest_parse_support import count_manifest_parses, count_versions_parses
from tests.integration.lib.workflow.test_workflow_state import (
    _complete_episode_media,
    _count_source_reads,
    _create_edit_timeline,
    _make_project,
    _one_complete_episode,
    _register_produced_artifacts,
    _valid_ad_shot,
    _valid_narration_segment,
    _valid_video_unit,
    _write_artifact,
    _write_episode_source,
    _write_registered_script,
    _write_source,
)


def _plan_one_episode(pm: ProjectManager, project_path: Path, source_text: str) -> None:
    def _plan(project: dict) -> None:
        project["episodes"] = [
            {"episode": 1, "script_file": "scripts/episode_1.json", "ledger_status": "planned"},
        ]
        project[SOURCE_FINGERPRINTS_KEY] = compute_source_fingerprints(discover_sources(project_path, project))

    pm.update_project("demo", _plan)


def _write_script_plan(project_path: Path, episode: int = 1) -> None:
    draft_dir = project_path / "drafts" / f"episode_{episode}"
    draft_dir.mkdir(parents=True, exist_ok=True)
    _write_episode_source(project_path, episode)
    atomic_write_json(draft_dir / "script_plan_segments.json", {"episode": episode, "segments": []})


def _add_character_with_sheet(pm: ProjectManager, project_path: Path, name: str = "小明") -> str:
    sheet = f"characters/{name}.png"
    _write_artifact(project_path, sheet)

    def _write(project: dict) -> None:
        project.setdefault("characters", {})[name] = {"description": "主角", "character_sheet": sheet}

    pm.update_project("demo", _write)
    return sheet


def _episode_with_media(
    pm: ProjectManager, project_path: Path, source_text: str = "完整原文", *, edit_timeline: bool = True
) -> None:
    """把项目推到「一集脚本已生成、分镜与视频齐备」的状态；``edit_timeline`` 时再按脚本新建一条剪辑时间线。"""

    _plan_one_episode(pm, project_path, source_text)
    _write_script_plan(project_path)
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
    if edit_timeline:
        _create_edit_timeline(pm)


def _count_artifact_opens(monkeypatch: pytest.MonkeyPatch, project_path: Path) -> dict[str, int]:
    """在文件系统边界上记录：产物图被按路径打开（哈希）的次数、单个产物描述符上读到的最大字节数、
    产物清单与版本历史被打开的次数。"""

    counts = {"artifact_bytes": 0, "artifact_max_read": 0, "manifest_opens": 0, "versions_opens": 0}
    root = project_path.resolve()
    artifact_dirs = {root / "characters", root / "scenes", root / "props", root / "storyboards", root / "videos"}
    artifact_identities = {
        (stat.st_dev, stat.st_ino)
        for directory in (*artifact_dirs, root / "versions")
        if directory.is_dir()
        for file in directory.rglob("*")
        if file.is_file() and file.suffix != ".json"
        for stat in (file.stat(),)
    }
    artifact_fd_reads: dict[int, int] = {}

    def _under_artifact_dir(path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return any(parent in artifact_dirs for parent in resolved.parents)

    original_open = Path.open
    original_read_bytes = Path.read_bytes
    original_os_open = os.open
    original_os_read = os.read
    original_os_close = os.close

    def _counted_path_open(self: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        if _under_artifact_dir(self):
            counts["artifact_bytes"] += 1
        return original_open(self, *args, **kwargs)

    def _counted_read_bytes(self: Path) -> bytes:
        if _under_artifact_dir(self):
            counts["artifact_bytes"] += 1
        return original_read_bytes(self)

    def _counted_os_open(path: object, *args: object, **kwargs: object) -> int:
        if isinstance(path, (str, Path)) and Path(path).name == MANIFEST_FILENAME:
            counts["manifest_opens"] += 1
        fd = original_os_open(path, *args, **kwargs)
        stat = os.fstat(fd)
        if (stat.st_dev, stat.st_ino) in artifact_identities:
            artifact_fd_reads[fd] = 0
        return fd

    def _counted_os_read(fd: int, length: int, /) -> bytes:
        data = original_os_read(fd, length)
        if fd in artifact_fd_reads:
            artifact_fd_reads[fd] += len(data)
            counts["artifact_max_read"] = max(counts["artifact_max_read"], artifact_fd_reads[fd])
        return data

    def _counted_os_close(fd: int, /) -> None:
        artifact_fd_reads.pop(fd, None)
        original_os_close(fd)

    original_builtin_open = builtins.open

    def _counted_builtin_open(file: Any, *args: Any, **kwargs: Any) -> IO[Any]:
        if isinstance(file, (str, Path)) and Path(file).name == "versions.json":
            counts["versions_opens"] += 1
        return original_builtin_open(file, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _counted_path_open)
    monkeypatch.setattr(Path, "read_bytes", _counted_read_bytes)
    monkeypatch.setattr(os, "open", _counted_os_open)
    monkeypatch.setattr(os, "read", _counted_os_read)
    monkeypatch.setattr(os, "close", _counted_os_close)
    monkeypatch.setattr(builtins, "open", _counted_builtin_open)
    return counts


def test_project_without_episodes_has_an_empty_episode_summary(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    (project_path / "source" / "novel.txt").write_text("原文", encoding="utf-8")

    summary = WorkflowStateService(pm).get_project_summary("demo")

    assert summary.episodes == []
    assert summary.episodes_summary.total == 0


def test_planned_episode_without_script_is_segmented(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _plan_one_episode(pm, project_path, source_text)
    _write_script_plan(project_path)
    register_current_artifact(project_path, ArtifactKey.episode_script_plan(1))

    summary = WorkflowStateService(pm).get_project_summary("demo")

    assert [episode.script_status for episode in summary.episodes] == ["segmented"]
    assert [episode.status for episode in summary.episodes] == ["draft"]


def test_script_without_media_reports_a_scripted_episode(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _plan_one_episode(pm, project_path, source_text)
    _write_script_plan(project_path)
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
    _write_source(pm, project_path, source_text)
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
    _write_source(pm, project_path, source_text)
    _plan_one_episode(pm, project_path, source_text)
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True, exist_ok=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "script_plan_reference_units.json", {"units": []})
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
    _write_source(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)

    summary = WorkflowStateService(pm).get_project_summary("demo")

    assert summary.episodes_summary.model_dump() == {
        "total": 1,
        "scripted": 1,
        "in_production": 0,
        "completed": 1,
    }


@pytest.mark.parametrize("currency", ["verified", "registered"])
def test_episode_with_all_videos_completes_only_once_it_has_an_edit_timeline(tmp_path: Path, currency: str) -> None:
    """一集完成 = 视频齐全且至少有一条剪辑时间线；视频齐全、还没剪辑的集仍在制作中。"""

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text, edit_timeline=False)
    service = WorkflowStateService(pm)

    before = service.get_project_summary("demo", currency=currency)
    assert before.episodes[0].status == "in_production"
    assert before.episodes_summary.completed == 0

    _create_edit_timeline(pm)

    after = service.get_project_summary("demo", currency=currency)
    assert after.episodes[0].status == "completed"
    assert after.episodes_summary.completed == 1


def test_deleting_an_asset_sheet_drops_the_available_count_like_the_workbench(tmp_path: Path) -> None:
    """列表页与工作台同用产物清单：删掉一张资产图，两处一起从「可用」里掉出来。"""

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
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


def test_deleting_a_video_drops_the_episode_out_of_completed(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)
    service = WorkflowStateService(pm)
    assert service.get_project_summary("demo").episodes[0].status == "completed"

    (project_path / resource_relative_path("videos", "E1S01")).unlink()

    summary = service.get_project_summary("demo")
    episode = summary.episodes[0]
    assert (episode.videos.total, episode.videos.available) == (1, 0)
    assert episode.status == "in_production"
    assert summary.episodes_summary.completed == 0


def test_stale_ledger_episode_falls_back_to_pending_preprocess(tmp_path: Path) -> None:
    """重新规划使该集原文范围失效：脚本仍在盘上，但该集回到待脚本规划，不计入已生成。"""

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)

    def _mark_stale(project: dict) -> None:
        project["episodes"][0]["ledger_status"] = "stale"

    pm.update_project("demo", _mark_stale)

    summary = WorkflowStateService(pm).get_project_summary("demo")

    assert [episode.script_status for episode in summary.episodes] == ["none"]
    assert summary.episodes_summary.scripted == 0
    assert summary.episodes[0].videos.total == 0


def test_stale_episode_counts_as_complete_only_after_its_script_plan_is_rebuilt(tmp_path: Path) -> None:
    """大厅与顶栏对 stale 集同一口径：未重建时两处都不算完成，重建后两处都按常规进度算完成。"""

    pm, project_path = _make_project(tmp_path, "narration")
    _one_complete_episode(pm, project_path)
    _create_edit_timeline(pm)

    def _mark_stale(project: dict) -> None:
        script_review.mark_ledger_stale(project_path, project, project["episodes"][0], 1)

    pm.update_project("demo", _mark_stale)
    service = WorkflowStateService(pm)

    awaiting = service.get_project_summary("demo", currency="registered")
    assert awaiting.episodes[0].script_status == "none"
    assert awaiting.episodes_summary.completed == 0
    awaiting_status = service.get_status("demo")
    assert awaiting_status.content is not None
    assert awaiting_status.content.project_complete is False

    atomic_write_json(
        project_path / "drafts" / "episode_1" / "script_plan_segments.json",
        {"episode": 1, "segments": [{"segment_id": "E1S01"}]},
    )

    rebuilt = service.get_project_summary("demo", currency="registered")
    assert rebuilt.episodes[0].status == "completed"
    assert rebuilt.episodes_summary.completed == 1
    rebuilt_status = service.get_status("demo")
    assert rebuilt_status.content is not None
    assert rebuilt_status.content.project_complete is True


def test_stale_artifacts_stay_available_and_are_counted_separately(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
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

    分集原文（``source/episode_N.txt``）只是 script_plan 基线的输入；本投影计数的剧本与媒体产物
    都不以 script_plan 为依据，因此同样不读。一旦有人为了算进度又去读整本源文、分集原文，
    或为了修订号做全量 sha256，它就会红。
    """

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)

    revision_calls: list[Path] = []

    def _spy(project_path: Path, *args: object, **kwargs: object) -> SourceRevisionResult:
        revision_calls.append(project_path)
        return compute_source_revision(project_path, *args, **kwargs)

    monkeypatch.setattr("lib.workflow.workflow_state.compute_source_revision", _spy)
    source_reads = _count_source_reads(monkeypatch, project_path)
    summary = WorkflowStateService(pm).get_project_summary("demo")

    assert summary.episodes_summary.completed == 1
    # 整本源文、分集原文（source/ 直下全部文件）一次不读，修订号也不算。
    assert revision_calls == []
    assert source_reads == {}


def test_migration_blocked_project_is_listed_as_needing_repair(tmp_path: Path) -> None:
    from tests.integration.lib.project.project_migrations.test_project_migration_v7_v8 import _project

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(tmp_path)
    script_path = project_dir / "scripts" / "episode_1.json"
    script_path.write_text("{not json", encoding="utf-8")
    failure = migrate_project_with_verdict(project_dir)
    assert failure is not None

    summary = WorkflowStateService(ProjectManager(str(tmp_path))).get_project_summary("demo")

    assert summary.needs_repair is True
    assert summary.repair_reason == failure.reason
    # 产物清单对未升级的数据不可读，一件产物都不报可用；集数照常列出，项目不从列表里消失。
    assert summary.episodes_summary.total == len(summary.episodes)
    assert all(episode.videos.available == 0 for episode in summary.episodes)


def test_deleting_a_storyboard_keeps_the_episode_completed(tmp_path: Path) -> None:
    """一集完成只看视频与剪辑时间线：删掉一张分镜图，该集仍完成，补分镜图作为集内建议的下一步。"""

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)
    service = WorkflowStateService(pm)
    assert service.get_project_summary("demo").episodes_summary.completed == 1

    (project_path / resource_relative_path("storyboards", "E1S01")).unlink()

    summary = service.get_project_summary("demo")
    episode = summary.episodes[0]
    assert (episode.storyboards.total, episode.storyboards.available) == (1, 0)
    assert episode.status == "completed"
    assert summary.episodes_summary.completed == 1
    status = service.get_status("demo", episode=1)
    assert status.content is not None
    assert status.content.episode_complete
    assert status.next_action.type == "generate_storyboards"


def test_episode_counts_match_the_workbench_on_the_same_project(tmp_path: Path) -> None:
    """剧集卡读的每集计数与工作台读的产物清单是同一份数字。

    剧集卡拿 ``get_project_summary``、工作台拿 ``get_status``：同一个项目上两处必须给出
    相同的可用数与 stale 数，否则用户在侧栏与画布之间会看到互相矛盾的进度。
    """

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
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


def test_registered_currency_counts_stale_artifacts_as_current(tmp_path: Path) -> None:
    """列表口径不比对规范状态：登记在案且文件在场的产物一律可用，产物比对不产生 stale。"""

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _add_character_with_sheet(pm, project_path)
    _episode_with_media(pm, project_path, source_text)
    service = WorkflowStateService(pm)

    def _redescribe(project: dict) -> None:
        project["characters"]["小明"]["description"] = "改过的设定"

    pm.update_project("demo", _redescribe)

    verified = service.get_project_summary("demo", currency="verified")
    registered = service.get_project_summary("demo", currency="registered")

    assert verified.assets["character"].model_dump() == {"total": 1, "available": 1, "stale": 1}
    assert registered.assets["character"].model_dump() == {"total": 1, "available": 1, "stale": 0}
    # 分集汇总不因口径而异：可用数相同，只是不再区分新旧。
    assert registered.episodes_summary == verified.episodes_summary


def test_registered_currency_still_requires_registration_and_presence(tmp_path: Path) -> None:
    """列表口径仍以清单为准：落盘未登记不算可用，登记后删掉文件也不算。"""

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)
    service = WorkflowStateService(pm)
    assert service.get_project_summary("demo", currency="registered").episodes_summary.completed == 1

    # 补录之后才写入的资产图：清单里没有它。
    _add_character_with_sheet(pm, project_path, name="小红")
    (project_path / resource_relative_path("videos", "E1S01")).unlink()

    summary = service.get_project_summary("demo", currency="registered")

    assert summary.assets["character"].model_dump() == {"total": 1, "available": 0, "stale": 0}
    episode = summary.episodes[0]
    assert (episode.videos.total, episode.videos.available) == (1, 0)
    assert episode.status == "in_production"
    assert summary.episodes_summary.completed == 0


def test_registered_currency_never_reads_artifact_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """列出 N 个项目不该哈希 N 个项目的图：列表口径不按路径打开任何产物、单个产物描述符上
    最多读一个字节（在场探针），清单每项目只读一次。

    ``verified`` 口径在同一夹具上会整读产物内容，用来证明夹具有区分力；它逐件比对时清单
    仍至多整份解析一次。
    """

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _add_character_with_sheet(pm, project_path)
    _episode_with_media(pm, project_path, source_text)
    service = WorkflowStateService(pm)

    reset_manifest_read_cache_for_tests()
    verified_counts = _count_artifact_opens(monkeypatch, project_path)
    verified_parses = count_manifest_parses(monkeypatch)
    verified = service.get_project_summary("demo", currency="verified")
    monkeypatch.undo()

    registered_counts = _count_artifact_opens(monkeypatch, project_path)
    registered = service.get_project_summary("demo", currency="registered")

    assert verified.episodes_summary.completed == registered.episodes_summary.completed == 1
    assert verified_counts["artifact_bytes"] > 0
    assert verified_parses["parses"] == 1
    assert registered_counts["artifact_bytes"] == 0
    assert registered_counts["artifact_max_read"] <= 1
    assert registered_counts["manifest_opens"] == 1


def _episode_with_storyboards(pm: ProjectManager, project_path: Path, count: int) -> None:
    """一集 ``count`` 个分镜，分镜图与视频齐备并补录进清单。"""

    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _plan_one_episode(pm, project_path, source_text)
    _write_script_plan(project_path)
    segments = [
        _valid_narration_segment(
            segment_id=resource_id, generated_assets=_complete_episode_media(project_path, resource_id)
        )
        for resource_id in (f"E1S{index:02d}" for index in range(1, count + 1))
    ]
    _write_registered_script(
        project_path,
        {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": segments},
    )
    _register_produced_artifacts(project_path)


@pytest.mark.parametrize("lock_file", [True, False], ids=("locked-read", "lockless-read"))
def test_verified_manifest_parses_do_not_grow_with_the_artifact_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lock_file: bool
) -> None:
    """完整口径逐件比对产物，但清单整份解析的次数与产物件数无关：分镜数翻倍，解析次数不变。

    读取不拿清单锁；写入方建过锁文件的项目与从未建锁的项目都要成立。
    """

    parses: dict[int, int] = {}
    for count in (4, 8):
        pm, project_path = _make_project(tmp_path / f"storyboards-{count}", "narration")
        _episode_with_storyboards(pm, project_path, count)
        lock_path = project_path / LOCK_FILENAME
        if not lock_file:
            lock_path.unlink()

        reset_manifest_read_cache_for_tests()
        counts = _count_artifact_opens(monkeypatch, project_path)
        manifest_parses = count_manifest_parses(monkeypatch)
        summary = WorkflowStateService(pm).get_project_summary("demo", currency="verified")
        monkeypatch.undo()

        episode = summary.episodes[0]
        assert (episode.videos.total, episode.videos.available) == (count, count)
        assert counts["artifact_bytes"] > 0
        assert lock_path.exists() is lock_file
        parses[count] = manifest_parses["parses"]

    assert parses[8] == parses[4] == 1


def test_verified_summary_opens_each_formal_video_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """完整口径判一条视频的时效只打开正式文件一次：准入、比对与内容事实证明共用同一次观察。

    项目详情逐件比对上千条视频，每多开一次文件就多一轮逐级打开目录的系统调用。
    """

    pm, project_path = _make_project(tmp_path, "narration")
    _episode_with_storyboards(pm, project_path, 3)
    names = {path.name for path in (project_path / "videos").glob("*.mp4")}
    assert len(names) == 3
    opens = dict.fromkeys(names, 0)
    original_os_open = os.open

    def _counted_os_open(path: object, *args: object, **kwargs: object) -> int:
        if isinstance(path, (str, Path)) and Path(path).name in opens:
            opens[Path(path).name] += 1
        return original_os_open(path, *args, **kwargs)

    reset_manifest_read_cache_for_tests()
    monkeypatch.setattr(os, "open", _counted_os_open)
    summary = WorkflowStateService(pm).get_project_summary("demo", currency="verified")
    monkeypatch.undo()

    assert summary.episodes[0].videos.available == 3
    assert opens == dict.fromkeys(names, 1)


def test_verified_summary_does_not_wait_for_a_writer_holding_the_manifest_lock(tmp_path: Path) -> None:
    """写入方持有清单锁期间，项目摘要照常返回：读取不拿清单锁，不等待也不失败。"""

    pm, project_path = _make_project(tmp_path, "narration")
    _episode_with_storyboards(pm, project_path, 2)
    service = WorkflowStateService(pm)
    expected = service.get_project_summary("demo")
    assert expected.episodes[0].videos.available == 2

    with (project_path / LOCK_FILENAME).open("ab") as lock_handle:
        portalocker.lock(lock_handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        try:
            # 旧协议下读取要拿这把锁，会在锁超时后报错；能拿到与无锁时相同的结果即说明读取不拿锁。
            summary = service.get_project_summary("demo")
        finally:
            portalocker.unlock(lock_handle)

    assert summary == expected


def test_one_resolver_answers_every_comparison_from_one_manifest_snapshot(tmp_path: Path) -> None:
    """一次请求只读一份清单快照：请求中途清单被替换，不影响这次请求的判定结果。"""

    pm, project_path = _make_project(tmp_path, "narration")
    _episode_with_storyboards(pm, project_path, 1)
    key = ArtifactKey.episode_video(1, "E1S01")
    video_path = resource_relative_path("videos", "E1S01")
    resolver = ArtifactCurrencyResolver(project_path)

    assert ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).forget_entry_transactionally(key)

    assert resolver.compare(key, artifact_path=video_path).status is ArtifactStatus.CURRENT
    assert ArtifactCurrencyResolver(project_path).compare(key, artifact_path=video_path).status is (
        ArtifactStatus.MISSING
    )


def test_verified_versions_parses_do_not_grow_with_the_episode_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """完整口径按集比对产物，但版本历史整份解析的次数与集数无关：集数从 1 到 3，解析次数不变。"""

    parses: dict[int, int] = {}
    for count in (1, 3):
        pm, project_path = _make_project(tmp_path / f"episodes-{count}", "narration")
        episodes_with_media(pm, project_path, count)

        versions_parses = count_versions_parses(monkeypatch)
        summary = WorkflowStateService(pm).get_project_summary("demo", currency="verified")
        monkeypatch.undo()

        assert [(episode.videos.total, episode.videos.available) for episode in summary.episodes] == [(1, 1)] * count
        parses[count] = versions_parses["parses"]

    assert parses[3] == parses[1] == 1


def test_externally_replaced_upload_is_stale_but_still_available(tmp_path: Path) -> None:
    """上传视频按上传字节登记：被外部替换后完整口径判 stale 仍可用，列表口径不比对内容。"""

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)
    service = WorkflowStateService(pm)

    (project_path / resource_relative_path("videos", "E1S01")).write_bytes(b"replaced outside the version flow")

    verified = service.get_project_summary("demo", currency="verified").episodes[0]
    registered = service.get_project_summary("demo", currency="registered").episodes[0]

    assert verified.videos.model_dump() == {"total": 1, "available": 1, "stale": 1}
    assert registered.videos.model_dump() == {"total": 1, "available": 1, "stale": 0}


def test_source_remaining_follows_ledger_commands_like_the_workbench(tmp_path: Path) -> None:
    """列表口径「源文还有未规划的原文」由账本命令记下，与工作台「继续分集规划」同一判定。

    项目列表不读源文，靠写账本的命令把结论写进 project.json；每一步都与按源文现算的制作状态一致。
    """

    from lib.episode.episode_manual_split import cut_unsplit_source

    pm, project_path = _make_project(tmp_path, "narration")
    text = "第一章。少年下山。第二章。城里起火。"
    _write_source(pm, project_path, text)
    service = WorkflowStateService(pm)

    def _assert_remaining(expected: bool) -> None:
        summary = service.get_project_summary("demo", currency="registered")
        status = service.get_status("demo")
        assert summary.source_remaining is expected
        assert status.content is not None
        assert status.content.source_remaining is expected

    _assert_remaining(True)

    cut_unsplit_source(project_path, source_file="source/novel.txt", end=text.index("第二章"))
    _assert_remaining(True)

    cut_unsplit_source(project_path, source_file="source/novel.txt", end=len(text))
    _assert_remaining(False)

    register_project_sources(pm, "demo", whole_source={"sequel.txt": "第三章。重逢。"})
    _assert_remaining(True)


def test_ad_project_never_reports_remaining_source(tmp_path: Path) -> None:
    pm, _project_path = _make_project(tmp_path, "ad")

    assert WorkflowStateService(pm).get_project_summary("demo", currency="registered").source_remaining is False


def _registered_video_files(pm: ProjectManager, project_path: Path) -> tuple[Path, Path]:
    """一集一个视频的项目，返回正式视频与它选中版本的快照。

    两者的 mtime 都拨到很久以前再重新登记：登记的内容事实早于清单文件自身的修改时间，不在粗粒度
    时间戳的 racy 窗口里。
    """

    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)
    # 导入校验要求集有标题。
    pm.update_project("demo", lambda project: project["episodes"][0].update(title="第一集"))
    video = project_path / resource_relative_path("videos", "E1S01")
    versions = VersionManager(project_path).get_versions("videos", "E1S01")
    record = next(item for item in versions["versions"] if item["version"] == versions["current_version"])
    snapshot = project_path / record["file"]
    _age(video)
    _age(snapshot)
    _register_produced_artifacts(project_path)
    return video, snapshot


def _video_counts(pm: ProjectManager, name: str = "demo") -> dict[str, int]:
    return WorkflowStateService(pm).get_project_summary(name).episodes[0].videos.model_dump()


def test_unchanged_video_is_current_without_reading_any_video_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """字节数与修改时间都和登记时一致，就认定是登记的那个版本：重启后（摘要缓存为空）也不读正式视频与版本快照。"""

    pm, project_path = _make_project(tmp_path, "narration")
    video, snapshot = _registered_video_files(pm, project_path)
    reset_file_digest_cache_for_tests()

    reads = _count_content_reads(monkeypatch, video, snapshot)
    counts = _video_counts(pm)

    assert counts == {"total": 1, "available": 1, "stale": 0}
    assert reads["content_reads"] == 0


def test_a_copied_video_with_a_new_mtime_stays_current(tmp_path: Path) -> None:
    """复制、导入会换 inode 和 mtime：重新取指纹，与登记的一致就仍是 current。"""

    pm, project_path = _make_project(tmp_path, "narration")
    video, _snapshot = _registered_video_files(pm, project_path)

    duplicate = video.with_name("copy.mp4")
    shutil.copyfile(video, duplicate)
    _age(duplicate, seconds=1800)
    duplicate.replace(video)

    assert _video_counts(pm) == {"total": 1, "available": 1, "stale": 0}


def test_a_same_size_rewrite_within_the_manifest_timestamp_is_still_caught(tmp_path: Path) -> None:
    """mtime 不早于清单自身的 mtime 时，同一时间戳下的改写可能 stat 全同：退化为完整比对，照样判 stale。"""

    pm, project_path = _make_project(tmp_path, "narration")
    video, _snapshot = _registered_video_files(pm, project_path)
    recorded = video.stat()
    # 摘要缓存不收刚写过的文件；这里把 mtime 拨回旧值来模拟同一时间戳，先清掉夹具留下的缓存。
    reset_file_digest_cache_for_tests()
    os.utime(project_path / MANIFEST_FILENAME, ns=(recorded.st_atime_ns, recorded.st_mtime_ns))

    video.write_bytes(b"X" * recorded.st_size)
    os.utime(video, ns=(recorded.st_atime_ns, recorded.st_mtime_ns))

    assert _video_counts(pm) == {"total": 1, "available": 1, "stale": 1}


def _without_artifact_envelope(archive_path: Path, rewritten: Path) -> Path:
    """把官方导出改写成没有清单信封的旧归档。"""

    with zipfile.ZipFile(archive_path) as source, zipfile.ZipFile(rewritten, "w") as target:
        for info in source.infolist():
            data = source.read(info)
            if info.filename.endswith(f"/{ARCHIVE_MANIFEST_NAME}"):
                manifest = json.loads(data)
                manifest.pop("artifact_manifest")
                data = json.dumps(manifest).encode()
            target.writestr(info, data)
    return rewritten


def _drop_selected_video_record_digest(project_path: Path) -> None:
    """把选中的视频版本记录改成没有内容指纹的旧记录（早于版本记录指纹的版本被切回选中）。"""

    path = project_path / "versions" / "versions.json"
    versions = json.loads(path.read_text(encoding="utf-8"))
    history = versions["videos"]["E1S01"]
    for record in history["versions"]:
        if record["version"] == history["current_version"]:
            del record["content_digest"]
    atomic_write_json(path, versions)


@pytest.mark.parametrize(
    ("envelope", "legacy_version_record"),
    [(True, False), (False, False), (True, True)],
    ids=["official-export", "legacy-archive", "legacy-version-record"],
)
def test_an_unchanged_video_stays_current_after_export_and_import(
    tmp_path: Path, envelope: bool, legacy_version_record: bool
) -> None:
    """导入换了 inode 与 mtime：内容没变的视频导入后仍是 current，不论归档带不带清单信封、选中版本记录有没有指纹。"""

    pm, project_path = _make_project(tmp_path, "narration")
    _registered_video_files(pm, project_path)
    if legacy_version_record:
        _drop_selected_video_record_digest(project_path)
    assert _video_counts(pm) == {"total": 1, "available": 1, "stale": 0}

    service = ProjectArchiveService(pm)
    archive_path, _ = service.export_project("demo")
    if not envelope:
        archive_path = _without_artifact_envelope(archive_path, tmp_path / "legacy.zip")
    shutil.rmtree(project_path)
    imported = service.import_project_archive(archive_path, uploaded_filename="demo.zip", confirm_report=True)

    assert _video_counts(pm, imported.project_name) == {"total": 1, "available": 1, "stale": 0}
