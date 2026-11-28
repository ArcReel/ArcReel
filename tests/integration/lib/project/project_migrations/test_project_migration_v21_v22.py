from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus
from lib.artifacts.sqlite_history import SQLiteHistory
from lib.episode.episode_deletion import delete_episode
from lib.episode.episode_layout import build_episode_layout
from lib.episode.episode_ledger import discover_episode_files
from lib.episode.episode_manual_split import merge_with_next_episode, move_episode_boundary
from lib.episode.episode_replan import (
    ReplanAdoptionResult,
    ReplanConfirmationRequired,
    adopt_replan_candidate,
    create_replan_candidate,
)
from lib.episode.episode_reset import EpisodeResetResult, reset_episode_planning
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_failure import ProjectMigrationError
from lib.project.project_migration_report import (
    MigrationAdjustment,
    MigrationReport,
    MigrationSkippedArtifact,
    load_migration_report,
    migration_report_path,
    write_migration_report,
)
from lib.project.project_migrations import v21_to_v22_episode_source_names as step
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.workflow.workflow_state import WorkflowStateService
from server.services.project.project_archive import (
    ARCHIVE_MANIFEST_NAME,
    ProjectArchiveService,
    ProjectArchiveValidationError,
)
from tests.legacy_project_shapes import (
    advance_project_schema,
    write_legacy_padded_episode_sources_project,
    write_legacy_storyboard_project,
)

SOURCE_TEXT = "第一章雨夜开端。\n第二章山中重逢。\n第三章拂晓离别。\n"
CUT_1 = SOURCE_TEXT.index("第二章")
CUT_2 = SOURCE_TEXT.index("第三章")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_v21_project(
    root: Path,
    name: str = "demo",
    *,
    episodes: list[dict[str, Any]],
    whole_source_text: str | None = SOURCE_TEXT,
    source_files: dict[str, bytes] | None = None,
    updated_at: str = "2026-08-01T00:00:00Z",
) -> Path:
    project_dir = root / name
    project_dir.mkdir(parents=True)
    source_dir = project_dir / "source"
    source_dir.mkdir()
    whole_files: list[dict[str, str]] = []
    fingerprints: dict[str, str] = {}
    if whole_source_text is not None:
        (source_dir / "novel.txt").write_text(whole_source_text, encoding="utf-8")
        whole_files.append({"source_file": "source/novel.txt"})
        fingerprints["source/novel.txt"] = hashlib.sha256(whole_source_text.encode("utf-8")).hexdigest()
    for filename, content in (source_files or {}).items():
        (source_dir / filename).write_bytes(content)
    project: dict[str, Any] = {
        "schema_version": 21,
        "title": "分集命名测试",
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "source_language": "zh",
        "style": "写实",
        "aspect_ratio": "9:16",
        "metadata": {"updated_at": updated_at},
        "characters": {},
        "scenes": {},
        "props": {},
        "products": {},
        "whole_source_files": whole_files,
        "source_fingerprints": fingerprints,
        "episodes": episodes,
    }
    _write_json(project_dir / "project.json", project)
    SQLiteHistory(project_dir).create({})
    return project_dir


def _cut_entry(episode: int, start: int, end: int) -> dict[str, Any]:
    return {
        "episode": episode,
        "title": f"第{episode}集",
        "script_file": f"scripts/episode_{episode}.json",
        "source_origin": "whole_source",
        "source_range": {"source_file": "source/novel.txt", "start": start, "end": end},
        "ledger_status": "planned",
    }


def _own_entry(episode: int) -> dict[str, Any]:
    return {
        "episode": episode,
        "title": f"自带第{episode}集",
        "script_file": f"scripts/episode_{episode}.json",
        "source_origin": "own",
        "ledger_status": "planned",
    }


def _none_entry(episode: int) -> dict[str, Any]:
    return {
        "episode": episode,
        "title": f"无原文第{episode}集",
        "script_file": f"scripts/episode_{episode}.json",
        "source_origin": "none",
        "ledger_status": "planned",
    }


def _write_acknowledged_report(project_dir: Path) -> MigrationReport:
    report = MigrationReport(
        from_schema_version=14,
        to_schema_version=21,
        migrated_at="2026-01-01T00:00:00Z",
        acknowledged_at="2026-01-02T00:00:00Z",
        adjusted=[MigrationAdjustment(code="placeholder_asset_added", file="project.json")],
        skipped=[
            MigrationSkippedArtifact(
                kind="character",
                resource_id="a",
                artifact_path="a.png",
                reason="unreadable image",
                category="unreadable",
            )
        ],
        registered={"character": 2},
    )
    write_migration_report(project_dir, report)
    return report


def test_filename_adjustment_extends_acknowledged_report(tmp_path: Path) -> None:
    project_dir = _make_v21_project(tmp_path, episodes=[_own_entry(1)], source_files={"episode_01.txt": b"body"})
    previous = _write_acknowledged_report(project_dir)

    assert migrate_project_dir(project_dir)

    report = load_migration_report(project_dir)
    assert report is not None
    assert report.complete
    assert report.from_schema_version == previous.from_schema_version
    assert report.to_schema_version == 22
    assert report.acknowledged_at is None
    assert report.skipped == previous.skipped
    assert report.registered == previous.registered
    assert report.adjusted[:-1] == previous.adjusted
    assert report.adjusted[-1].code == "episode_source_canonicalized"


def test_no_adjustment_leaves_acknowledged_report_untouched(tmp_path: Path) -> None:
    project_dir = _make_v21_project(tmp_path, episodes=[_own_entry(1)], source_files={"episode_1.txt": b"body"})
    _write_acknowledged_report(project_dir)
    path = migration_report_path(project_dir)
    before = path.read_bytes()

    assert migrate_project_dir(project_dir)

    assert path.read_bytes() == before


def test_only_padded_alias_promotes_to_canonical_and_readable_by_workflow(tmp_path: Path) -> None:
    project_dir = _make_v21_project(
        tmp_path / "projects",
        episodes=[_cut_entry(1, 0, CUT_1), _own_entry(2)],
        source_files={
            "episode_01.txt": SOURCE_TEXT[:CUT_1].encode(),
            "episode_02.txt": "自带原文内容。".encode(),
        },
    )

    assert migrate_project_dir(project_dir) is True

    project = _read_json(project_dir / "project.json")
    assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert project["metadata"]["updated_at"] == "2026-08-01T00:00:00Z"
    assert not (project_dir / "source/episode_01.txt").exists()
    assert not (project_dir / "source/episode_02.txt").exists()
    assert (project_dir / "source/episode_1.txt").read_text(encoding="utf-8") == SOURCE_TEXT[:CUT_1]
    assert (project_dir / "source/episode_2.txt").read_text(encoding="utf-8") == "自带原文内容。"
    assert discover_episode_files(project_dir) == {
        1: project_dir / "source/episode_1.txt",
        2: project_dir / "source/episode_2.txt",
    }

    report = load_migration_report(project_dir)
    assert report is not None
    assert [(item.code, item.episode, item.file, item.params) for item in report.adjusted] == [
        (
            "episode_source_canonicalized",
            1,
            "source/episode_1.txt",
            {"from": "source/episode_01.txt", "to": "source/episode_1.txt"},
        ),
        (
            "episode_source_canonicalized",
            2,
            "source/episode_2.txt",
            {"from": "source/episode_02.txt", "to": "source/episode_2.txt"},
        ),
    ]

    layout = build_episode_layout(project_dir, project)
    assert [ep.units for ep in layout.episodes] == [len("第一章雨夜开端。"), len("自带原文内容。")]
    assert layout.unregistered == []
    summary = WorkflowStateService(ProjectManager(tmp_path)).get_project_summary(project_dir.name)
    assert [ep.episode for ep in summary.episodes] == [1, 2]


def test_only_canonical_files_upgrade_without_adjustments(tmp_path: Path) -> None:
    project_dir = _make_v21_project(
        tmp_path / "projects",
        episodes=[_cut_entry(1, 0, CUT_1)],
        source_files={"episode_1.txt": SOURCE_TEXT[:CUT_1].encode("utf-8")},
    )

    assert migrate_project_dir(project_dir) is True

    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert load_migration_report(project_dir) is None
    assert (project_dir / "source/episode_1.txt").read_text(encoding="utf-8") == SOURCE_TEXT[:CUT_1]
    assert not migrate_project_dir(project_dir)


def test_coexisting_canonical_and_aliases_clean_provable_and_preserve_unproven(tmp_path: Path) -> None:
    project_dir = write_legacy_padded_episode_sources_project(tmp_path / "projects")
    canonical_before = (project_dir / "source/episode_1.txt").read_bytes()

    assert migrate_project_dir(project_dir) is True

    project = _read_json(project_dir / "project.json")
    assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert (project_dir / "source/episode_1.txt").read_bytes() == canonical_before
    assert not (project_dir / "source/episode_01.txt").exists()
    assert not (project_dir / "source/episode_001.txt").exists()
    assert (project_dir / "source/episode_2.txt").read_text(encoding="utf-8") == "自带原文第二集。"
    assert not (project_dir / "source/episode_02.txt").exists()

    report = load_migration_report(project_dir)
    assert report is not None
    preserved = [item for item in report.adjusted if item.code == "episode_source_alias_preserved"]
    assert [(item.episode, item.file) for item in preserved] == [(1, "source/episode_01.txt")]
    assert preserved[0].backup is not None
    assert (project_dir / preserved[0].backup).read_bytes() == b"unproven-old-draft"

    layout = build_episode_layout(project_dir, project)
    assert layout.unregistered == []
    resolver = ArtifactCurrencyResolver(project_dir)
    assert (
        resolver.compare(ArtifactKey.episode_script(1), artifact_path="scripts/episode_1.json").status
        is ArtifactStatus.CURRENT
    )


def test_multiple_padded_aliases_without_canonical_handle_identical_provable_and_ambiguous(tmp_path: Path) -> None:
    project_dir = _make_v21_project(
        tmp_path / "projects",
        episodes=[_own_entry(1), _cut_entry(2, CUT_1, CUT_2), _own_entry(3)],
        source_files={
            "episode_01.txt": b"identical-own",
            "episode_001.txt": b"identical-own",
            "episode_02.txt": SOURCE_TEXT[CUT_1:CUT_2].encode("utf-8"),
            "episode_002.txt": b"diverged-cut-draft",
            "episode_03.txt": b"conflict-a",
            "episode_003.txt": b"conflict-b",
        },
    )

    assert migrate_project_dir(project_dir) is True

    assert (project_dir / "source/episode_1.txt").read_bytes() == b"identical-own"
    assert not (project_dir / "source/episode_01.txt").exists()
    assert not (project_dir / "source/episode_001.txt").exists()

    assert (project_dir / "source/episode_2.txt").read_text(encoding="utf-8") == SOURCE_TEXT[CUT_1:CUT_2]
    assert not (project_dir / "source/episode_02.txt").exists()
    assert not (project_dir / "source/episode_002.txt").exists()

    assert not (project_dir / "source/episode_3.txt").exists()
    assert not (project_dir / "source/episode_03.txt").exists()
    assert not (project_dir / "source/episode_003.txt").exists()

    report = load_migration_report(project_dir)
    assert report is not None
    preserved = {
        (item.episode, item.file): (project_dir / item.backup).read_bytes()
        for item in report.adjusted
        if item.code == "episode_source_alias_preserved" and item.backup is not None
    }
    assert preserved == {
        (2, "source/episode_002.txt"): b"diverged-cut-draft",
        (3, "source/episode_003.txt"): b"conflict-b",
        (3, "source/episode_03.txt"): b"conflict-a",
    }


def test_none_origin_orphan_zero_and_symlink_aliases_are_preserved_not_promoted(tmp_path: Path) -> None:
    project_dir = _make_v21_project(
        tmp_path / "projects",
        episodes=[_none_entry(1), _own_entry(2)],
        source_files={
            "episode_01.txt": b"stray-for-none",
            "episode_07.txt": b"orphan-unregistered",
            "episode_0.txt": b"zero-episode",
        },
    )
    symlink_alias = project_dir / "source/episode_02.txt"
    symlink_alias.symlink_to(project_dir / "source/does-not-exist.txt")

    assert migrate_project_dir(project_dir) is True

    assert discover_episode_files(project_dir, include_symlinks=True) == {}
    for name in ("episode_01.txt", "episode_02.txt", "episode_07.txt", "episode_0.txt"):
        assert not (project_dir / "source" / name).exists()
        assert not (project_dir / "source" / name).is_symlink()

    report = load_migration_report(project_dir)
    assert report is not None
    by_file = {item.file: item for item in report.adjusted if item.code == "episode_source_alias_preserved"}
    assert by_file["source/episode_01.txt"].episode == 1
    assert by_file["source/episode_02.txt"].episode == 2
    assert by_file["source/episode_07.txt"].episode is None
    assert by_file["source/episode_0.txt"].episode is None
    assert (project_dir / by_file["source/episode_01.txt"].backup).read_bytes() == b"stray-for-none"
    assert (project_dir / by_file["source/episode_02.txt"].backup).is_symlink()
    assert (project_dir / by_file["source/episode_07.txt"].backup).read_bytes() == b"orphan-unregistered"
    assert (project_dir / by_file["source/episode_0.txt"].backup).read_bytes() == b"zero-episode"


def test_source_symlink_directory_is_rejected_without_advancing_schema(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects/demo"
    project_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "episode_01.txt").write_bytes(b"external")
    (project_dir / "source").symlink_to(outside, target_is_directory=True)
    _write_json(project_dir / "project.json", {"schema_version": 21, "episodes": [_own_entry(1)]})
    SQLiteHistory(project_dir).create({})

    with pytest.raises(ProjectMigrationError):
        migrate_project_dir(project_dir)

    assert _read_json(project_dir / "project.json")["schema_version"] == 21
    assert (outside / "episode_01.txt").read_bytes() == b"external"


def test_pre_v16_project_with_only_padded_files_registers_and_normalizes(tmp_path: Path) -> None:
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=15)
    (project_dir / "source/1-7-0227.txt").unlink()
    (project_dir / "source/episode_01.txt").write_text("第一集补零正文。", encoding="utf-8")
    (project_dir / "source/episode_02.txt").write_text("第二集补零正文。", encoding="utf-8")

    assert migrate_project_dir(project_dir) is True

    project = _read_json(project_dir / "project.json")
    assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert [(e["episode"], e["source_origin"]) for e in project["episodes"]] == [(1, "own"), (2, "own")]
    assert (project_dir / "source/episode_1.txt").read_text(encoding="utf-8") == "第一集补零正文。"
    assert (project_dir / "source/episode_2.txt").read_text(encoding="utf-8") == "第二集补零正文。"
    assert not (project_dir / "source/episode_01.txt").exists()
    assert not (project_dir / "source/episode_02.txt").exists()


def test_failure_before_schema_commit_recovers_on_retry_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = _make_v21_project(
        tmp_path / "projects",
        episodes=[_own_entry(1), _own_entry(2)],
        source_files={
            "episode_01.txt": b"ep1-body",
            "episode_02.txt": b"ep2-conflict-a",
            "episode_002.txt": b"ep2-conflict-b",
        },
    )
    previous = _write_acknowledged_report(project_dir)
    real_write = step.atomic_write_json

    def fail_on_project(path: Path, payload: object) -> None:
        if path == project_dir / "project.json":
            raise OSError("disk full before commit")
        real_write(path, payload)

    with monkeypatch.context() as fault:
        fault.setattr(step, "atomic_write_json", fail_on_project)
        with pytest.raises(OSError, match="disk full before commit"):
            migrate_project_dir(project_dir)

    assert _read_json(project_dir / "project.json")["schema_version"] == 21
    partial_report = load_migration_report(project_dir)
    assert partial_report is not None
    assert partial_report.complete is False
    assert partial_report.skipped == previous.skipped
    assert partial_report.registered == previous.registered
    assert partial_report.adjusted[:1] == previous.adjusted

    assert migrate_project_dir(project_dir) is True
    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert (project_dir / "source/episode_1.txt").read_bytes() == b"ep1-body"
    assert not (project_dir / "source/episode_2.txt").exists()

    final_report = load_migration_report(project_dir)
    assert final_report is not None
    assert final_report.complete is True
    assert final_report.skipped == previous.skipped
    assert final_report.registered == previous.registered
    assert final_report.adjusted == partial_report.adjusted
    assert len(final_report.adjusted) == len(previous.adjusted) + 3
    assert final_report.from_schema_version == previous.from_schema_version
    assert final_report.to_schema_version == 22
    assert final_report.acknowledged_at is None

    before = {p.relative_to(project_dir): p.read_bytes() for p in (project_dir / "source").iterdir() if p.is_file()}
    assert migrate_project_dir(project_dir) is False
    after = {p.relative_to(project_dir): p.read_bytes() for p in (project_dir / "source").iterdir() if p.is_file()}
    assert after == before


def test_interrupted_ambiguous_group_retry_does_not_promote_remaining_conflict(tmp_path: Path) -> None:
    project_dir = _make_v21_project(
        tmp_path / "projects",
        episodes=[_own_entry(1)],
        source_files={
            "episode_001.txt.kept.v21-100": b"already-set-aside",
            "episode_01.txt": b"remaining-conflict",
        },
    )

    assert migrate_project_dir(project_dir) is True

    assert not (project_dir / "source/episode_1.txt").exists()
    assert not (project_dir / "source/episode_01.txt").exists()
    kept_files = sorted(p.name for p in (project_dir / "source").glob("episode_*.txt.kept.v21-*"))
    assert len(kept_files) == 2

    report = load_migration_report(project_dir)
    assert report is not None
    assert report.complete is True
    assert sorted(item.backup for item in report.adjusted if item.backup is not None) == [
        f"source/{name}" for name in kept_files
    ]
    assert {(item.file, item.episode) for item in report.adjusted} == {
        ("source/episode_001.txt", 1),
        ("source/episode_01.txt", 1),
    }


def test_interrupted_after_all_aliases_preserved_still_records_kept_copies_in_report(tmp_path: Path) -> None:
    project_dir = _make_v21_project(
        tmp_path / "projects",
        episodes=[_own_entry(1)],
        source_files={"episode_01.txt.kept.v21-100": b"preserved-before-crash"},
    )

    assert migrate_project_dir(project_dir) is True
    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    report = load_migration_report(project_dir)
    assert report is not None
    assert report.complete is True
    assert [(item.code, item.episode, item.file, item.backup) for item in report.adjusted] == [
        ("episode_source_alias_preserved", 1, "source/episode_01.txt", "source/episode_01.txt.kept.v21-100")
    ]


def test_canonical_symlink_to_alias_preserves_readable_canonical_source(tmp_path: Path) -> None:
    project_dir = _make_v21_project(
        tmp_path / "projects",
        episodes=[_cut_entry(1, 0, CUT_1), _own_entry(2)],
        source_files={
            "episode_01.txt": SOURCE_TEXT[:CUT_1].encode("utf-8"),
            "episode_001.txt": b"diverged-extra-alias",
            "episode_02.txt": b"episode-two-body",
        },
    )
    (project_dir / "source/episode_1.txt").symlink_to("episode_01.txt")

    assert migrate_project_dir(project_dir) is True

    ep1 = project_dir / "source/episode_1.txt"
    assert not ep1.is_symlink()
    assert ep1.read_text(encoding="utf-8") == SOURCE_TEXT[:CUT_1]
    assert not (project_dir / "source/episode_01.txt").exists()
    assert not (project_dir / "source/episode_001.txt").exists()
    assert discover_episode_files(project_dir) == {
        1: ep1,
        2: project_dir / "source/episode_2.txt",
    }
    report = load_migration_report(project_dir)
    assert report is not None
    assert [item.code for item in report.adjusted] == [
        "episode_source_canonicalized",
        "episode_source_alias_preserved",
        "episode_source_canonicalized",
    ]
    preserved = next(item for item in report.adjusted if item.code == "episode_source_alias_preserved")
    assert preserved.backup is not None
    assert (project_dir / preserved.backup).read_bytes() == b"diverged-extra-alias"


def test_canonical_symlink_to_other_episode_alias_is_materialized_before_move(tmp_path: Path) -> None:
    project_dir = _make_v21_project(
        tmp_path / "projects",
        episodes=[_own_entry(1), _own_entry(2)],
        source_files={"episode_02.txt": b"shared-source-body"},
    )
    (project_dir / "source/episode_1.txt").symlink_to("episode_02.txt")

    assert migrate_project_dir(project_dir) is True

    ep1 = project_dir / "source/episode_1.txt"
    ep2 = project_dir / "source/episode_2.txt"
    assert not ep1.is_symlink()
    assert ep1.read_bytes() == b"shared-source-body"
    assert ep2.read_bytes() == b"shared-source-body"
    assert not (project_dir / "source/episode_02.txt").exists()


def test_post_migration_normal_operations_use_canonical_filenames_only(tmp_path: Path) -> None:
    project_dir = _make_v21_project(
        tmp_path / "projects",
        episodes=[_cut_entry(1, 0, CUT_1), _cut_entry(2, CUT_1, CUT_2), _cut_entry(3, CUT_2, len(SOURCE_TEXT))],
        source_files={
            "episode_1.txt": SOURCE_TEXT[:CUT_1].encode("utf-8"),
            "episode_01.txt": b"old-draft-1",
            "episode_02.txt": SOURCE_TEXT[CUT_1:CUT_2].encode("utf-8"),
            "episode_3.txt": SOURCE_TEXT[CUT_2:].encode("utf-8"),
            "episode_03.txt": SOURCE_TEXT[CUT_2:].encode("utf-8"),
        },
    )

    assert migrate_project_dir(project_dir) is True
    report = load_migration_report(project_dir)
    assert report is not None
    [kept_record] = [item for item in report.adjusted if item.code == "episode_source_alias_preserved"]
    assert kept_record.backup is not None
    kept_path = project_dir / kept_record.backup
    assert kept_path.read_bytes() == b"old-draft-1"

    new_boundary = CUT_1 + 3
    move_episode_boundary(project_dir, 1, at=new_boundary)
    assert (project_dir / "source/episode_1.txt").read_text(encoding="utf-8") == SOURCE_TEXT[:new_boundary]
    assert (project_dir / "source/episode_2.txt").read_text(encoding="utf-8") == SOURCE_TEXT[new_boundary:CUT_2]
    assert discover_episode_files(project_dir) == {
        1: project_dir / "source/episode_1.txt",
        2: project_dir / "source/episode_2.txt",
        3: project_dir / "source/episode_3.txt",
    }

    merge_with_next_episode(project_dir, 1)
    assert not (project_dir / "source/episode_2.txt").exists()
    assert (project_dir / "source/episode_1.txt").read_text(encoding="utf-8") == SOURCE_TEXT[:CUT_2]

    candidate_id = create_replan_candidate(project_dir, episode=1, instructions=None)
    pm = ProjectManager(tmp_path)
    pm.update_project(
        project_dir.name,
        lambda p: p["episode_replan"].update(
            episodes=[
                {
                    "title": "新全集",
                    "hook": "",
                    "source_range": {"source_file": "source/novel.txt", "start": 0, "end": len(SOURCE_TEXT)},
                }
            ],
            complete=True,
        ),
    )
    preview = adopt_replan_candidate(project_dir, candidate_id)
    assert isinstance(preview, ReplanConfirmationRequired)
    adopted = adopt_replan_candidate(project_dir, candidate_id, revision=preview.impact.revision)
    assert isinstance(adopted, ReplanAdoptionResult)
    assert adopted.episodes == [4]
    assert not (project_dir / "source/episode_1.txt").exists()
    assert not (project_dir / "source/episode_3.txt").exists()
    assert (project_dir / "source/episode_4.txt").read_text(encoding="utf-8") == SOURCE_TEXT

    reset = reset_episode_planning(project_dir)
    assert isinstance(reset, EpisodeResetResult)
    assert discover_episode_files(project_dir) == {}
    assert kept_path.read_bytes() == b"old-draft-1"


def test_v21_archive_import_normalizes_padded_aliases_and_requires_confirmation(tmp_path: Path) -> None:
    archive_path = tmp_path / "v21-padded.zip"
    project_payload = {
        "schema_version": 21,
        "title": "归档旧项目",
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "source_language": "zh",
        "style": "写实",
        "aspect_ratio": "9:16",
        "characters": {},
        "scenes": {},
        "props": {},
        "products": {},
        "whole_source_files": [{"source_file": "source/novel.txt"}],
        "episodes": [_cut_entry(1, 0, CUT_1), _own_entry(2)],
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(f"demo/{ARCHIVE_MANIFEST_NAME}", json.dumps({"project_name": "demo"}))
        archive.writestr("demo/project.json", json.dumps(project_payload, ensure_ascii=False))
        archive.writestr("demo/versions/versions.json", "{}")
        archive.writestr("demo/source/novel.txt", SOURCE_TEXT)
        archive.writestr("demo/source/episode_1.txt", SOURCE_TEXT[:CUT_1])
        archive.writestr("demo/source/episode_01.txt", "归档旧稿")
        archive.writestr("demo/source/episode_02.txt", "归档自带正文")

    manager = ProjectManager(tmp_path / "imported")
    service = ProjectArchiveService(manager)

    with pytest.raises(ProjectArchiveValidationError) as needs_confirm:
        service.import_project_archive(archive_path, confirm_report=False)
    assert needs_confirm.value.status_code == 409
    assert needs_confirm.value.extra["confirmation_required"] is True
    codes = [item["code"] for item in needs_confirm.value.extra["migration_report"]["adjusted"]]
    assert codes == ["episode_source_alias_preserved", "episode_source_canonicalized"]

    imported = service.import_project_archive(archive_path, confirm_report=True)
    root = manager.get_project_path(imported.project_name)
    assert _read_json(root / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert SQLiteHistory(root).is_enabled()
    assert not (root / "versions/versions.json").exists()
    assert (root / "source/episode_1.txt").read_text(encoding="utf-8") == SOURCE_TEXT[:CUT_1]
    assert (root / "source/episode_2.txt").read_text(encoding="utf-8") == "归档自带正文"
    assert not (root / "source/episode_01.txt").exists()
    assert not (root / "source/episode_02.txt").exists()
    [kept] = list((root / "source").glob("episode_01.txt.kept.v21-*"))
    assert kept.read_text(encoding="utf-8") == "归档旧稿"

    deletion = delete_episode(manager, imported.project_name, 2)
    assert hasattr(deletion, "impact")
