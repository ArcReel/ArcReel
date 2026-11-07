"""Project-summary projection: episode progress/output counts for broad views (lists, cards, global headers).

Assert projected external output: per-episode progress, usable/stale counts, episode aggregation, and shared
artifact manifests with workbench production status, not internal call order. Two artifact policies have separate groups:
``verified`` matches workbench per-item counts; ``registered`` checks registration/presence without reading artifact content.
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
    """Create one episode with generated script, storyboards, and videos; ``edit_timeline`` additionally creates a timeline from its script."""

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
    """At filesystem boundaries, count artifact path opens for hashing, maximum bytes read from one artifact descriptor,
    and manifest/version-history opens."""

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
    """Storyboard-image-to-video reports storyboard counts, sharing semantics for ad/short-film shots and narration segments."""

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
    """Reference-to-video reports video-unit counts and has no storyboard-image artifact tier."""

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
    """Episode completion requires all videos plus at least one editing timeline; complete videos without editing remain in production."""

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
    """Lists/workbench share manifests; deleting one asset image removes it from usable counts in both."""

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
    """Replanning invalidates episode source ranges; existing disk scripts no longer count as generated, returning to pending planning."""

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
    """Lobby/header stale-episode semantics agree: incomplete before rebuilding, then ordinary progress determines completion afterward."""

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
    """Listing N projects must not read N novels; neither whole sources nor source revisions enter this projection.

    Episode sources (``source/episode_N.txt``) only feed script_plan baselines; counted scripts/media do not depend
    on script_plan, so do not read them either. Adding whole/episode source reads for progress, or full sha256
    for revisions, makes this fail.
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
    # Never read whole/episode sources (all direct source/ files) or compute revisions.
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
    # Unupgraded manifests are unreadable and yield no usable artifacts; still list episodes/projects.
    assert summary.episodes_summary.total == len(summary.episodes)
    assert all(episode.videos.available == 0 for episode in summary.episodes)


def test_deleting_a_storyboard_keeps_the_episode_completed(tmp_path: Path) -> None:
    """Completion checks only videos/timelines; deleting a storyboard leaves episodes complete, recommending storyboard completion within the episode."""

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
    """Episode cards and workbench manifests yield identical episode counts.

    Cards call ``get_project_summary``, workbench calls ``get_status``; usable/stale counts for one project must
    agree or users see contradictory progress between sidebar and canvas.
    """

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)
    service = WorkflowStateService(pm)

    # Change shot visual prompts; disk storyboards remain usable but older than current content.
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

    # Fixtures must discriminate; equal all-zero counts are no evidence.
    assert episode.storyboards.stale == 1
    assert episode.storyboards.available == 1


def test_registered_currency_counts_stale_artifacts_as_current(tmp_path: Path) -> None:
    """List policy does not compare canonical state: registered/present artifacts are usable, without comparison-derived stale counts."""

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
    # Episode aggregation is policy-independent: equal usable counts, without distinguishing age.
    assert registered.episodes_summary == verified.episodes_summary


def test_registered_currency_still_requires_registration_and_presence(tmp_path: Path) -> None:
    """List policy still follows manifests: unregistered disk files and deleted registered files are not usable."""

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)
    service = WorkflowStateService(pm)
    assert service.get_project_summary("demo", currency="registered").episodes_summary.completed == 1

    # Asset images written after backfill are absent from manifests.
    _add_character_with_sheet(pm, project_path, name="小红")
    (project_path / resource_relative_path("videos", "E1S01")).unlink()

    summary = service.get_project_summary("demo", currency="registered")

    assert summary.assets["character"].model_dump() == {"total": 1, "available": 0, "stale": 0}
    episode = summary.episodes[0]
    assert (episode.videos.total, episode.videos.available) == (1, 0)
    assert episode.status == "in_production"
    assert summary.episodes_summary.completed == 0


def test_registered_currency_never_reads_artifact_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Listing N projects must not hash N projects' images: list policy never opens artifact paths, reads at most
    one byte per artifact descriptor for presence probes, and reads manifests once per project.

    ``verified`` reads full content in the same fixture, proving discrimination; per-item comparisons
    still parse the whole manifest at most once.
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
    """One episode with ``count`` storyboards, complete images/videos, and manifest backfill."""

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
    """Full policy compares every artifact, but whole-manifest parsing is independent of artifact count; doubling storyboards keeps parses unchanged.

    Reads take no manifest locks, both for projects with writer-created locks and those never locked.
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
    """Full-policy video currency opens formal files once; admission/comparison/content-fact proof share that observation.

    With thousands of detail-view videos, every extra open adds directory-by-directory system calls.
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
    """Project summaries return normally while writers hold manifest locks; reads never acquire/wait/fail on those locks."""

    pm, project_path = _make_project(tmp_path, "narration")
    _episode_with_storyboards(pm, project_path, 2)
    service = WorkflowStateService(pm)
    expected = service.get_project_summary("demo")
    assert expected.episodes[0].videos.available == 2

    with (project_path / LOCK_FILENAME).open("ab") as lock_handle:
        portalocker.lock(lock_handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        try:
            # Old reads acquired this lock and timed out; identical locked/unlocked results prove reads do not acquire it.
            summary = service.get_project_summary("demo")
        finally:
            portalocker.unlock(lock_handle)

    assert summary == expected


def test_one_resolver_answers_every_comparison_from_one_manifest_snapshot(tmp_path: Path) -> None:
    """One request reads one manifest snapshot; midrequest replacement does not change its conclusions."""

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


@pytest.mark.parametrize(
    ("swap", "expected"),
    [("removed", ArtifactStatus.MISSING), ("symlinked", ArtifactStatus.BLOCKED)],
)
def test_video_swapped_after_admission_is_judged_by_the_newer_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swap: str, expected: ArtifactStatus
) -> None:
    """After admission sees formal video but before full comparison, deletion or outside-project symlink replacement uses the new
    observation to classify MISSING/BLOCKED, not old admission observations as STALE.

    Outside files copy formal bytes; following the link would match fingerprints and classify CURRENT.
    """

    pm, project_path = _make_project(tmp_path, "narration")
    _episode_with_storyboards(pm, project_path, 1)
    key = ArtifactKey.episode_video(1, "E1S01")
    video_path = resource_relative_path("videos", "E1S01")
    # Manifest mtime predates registered video mtime; same-timestamp rewriting is possible, so proof falls back to full comparison.
    os.utime(project_path / MANIFEST_FILENAME, ns=(0, 0))
    outside = tmp_path / "outside.mp4"
    shutil.copyfile(project_path / video_path, outside)
    original_inspect = ProjectArtifactManifestAdapter.inspect_artifact
    swapped: list[str] = []

    def _inspect_then_swap(self: ProjectArtifactManifestAdapter, relative_path: str) -> Any:
        observation = original_inspect(self, relative_path)
        if relative_path == video_path and not swapped:
            swapped.append(relative_path)
            formal = project_path / video_path
            formal.unlink()
            if swap == "symlinked":
                formal.symlink_to(outside)
        return observation

    resolver = ArtifactCurrencyResolver(project_path)
    monkeypatch.setattr(ProjectArtifactManifestAdapter, "inspect_artifact", _inspect_then_swap)

    assert resolver.compare(key, artifact_path=video_path).status is expected


def test_verified_versions_parses_do_not_grow_with_the_episode_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full-policy per-episode comparisons parse whole version history independently of episode count; counts 1 to 3 leave parses unchanged."""

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
    """Uploaded videos register bytes; external replacement makes full policy stale but usable, while list policy never compares content."""

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
    """Ledger commands record list-side unplanned-source conclusions, matching workbench continuation-planning judgments.

    Lists do not read sources; ledger-writing commands persist conclusions in project.json, matching live-source production status at every step.
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
    """One-video-per-episode project returning formal video and its selected-version snapshot.

    Backdate both mtimes before reregistering: recorded content facts precede manifest modification time, outside coarse-timestamp
    racy windows.
    """

    source_text = "完整原文"
    _write_source(pm, project_path, source_text)
    _episode_with_media(pm, project_path, source_text)
    # Import validation requires episode titles.
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
    """Matching registered byte lengths/mtimes establish the registered version; even after restart with empty summary caches, never read formal videos/snapshots."""

    pm, project_path = _make_project(tmp_path, "narration")
    video, snapshot = _registered_video_files(pm, project_path)
    reset_file_digest_cache_for_tests()

    reads = _count_content_reads(monkeypatch, video, snapshot)
    counts = _video_counts(pm)

    assert counts == {"total": 1, "available": 1, "stale": 0}
    assert reads["content_reads"] == 0


def test_a_copied_video_with_a_new_mtime_stays_current(tmp_path: Path) -> None:
    """Copies/imports change inode/mtime; rehashing matching content still yields current."""

    pm, project_path = _make_project(tmp_path, "narration")
    video, _snapshot = _registered_video_files(pm, project_path)

    duplicate = video.with_name("copy.mp4")
    shutil.copyfile(video, duplicate)
    _age(duplicate, seconds=1800)
    duplicate.replace(video)

    assert _video_counts(pm) == {"total": 1, "available": 1, "stale": 0}


def test_a_same_size_rewrite_within_the_manifest_timestamp_is_still_caught(tmp_path: Path) -> None:
    """mtime at/after manifest mtime can hide rewriting under equal stats within one timestamp; fall back to full comparison and still classify stale."""

    pm, project_path = _make_project(tmp_path, "narration")
    video, _snapshot = _registered_video_files(pm, project_path)
    recorded = video.stat()
    # Do not cache freshly written files; backdate mtime to simulate the same timestamp and clear fixture caches first.
    reset_file_digest_cache_for_tests()
    os.utime(project_path / MANIFEST_FILENAME, ns=(recorded.st_atime_ns, recorded.st_mtime_ns))

    video.write_bytes(b"X" * recorded.st_size)
    os.utime(video, ns=(recorded.st_atime_ns, recorded.st_mtime_ns))

    assert _video_counts(pm) == {"total": 1, "available": 1, "stale": 1}


def _without_artifact_envelope(archive_path: Path, rewritten: Path) -> Path:
    """Rewrite official exports into legacy archives without manifest envelopes."""

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
    """Remove content fingerprints from selected video-version records, simulating selection of versions predating fingerprints."""

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
    """Imports change inode/mtime but unchanged videos stay current, with/without archive manifest envelopes or selected-version fingerprints."""

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
