"""Full episode-planning reset behavior tests with real filesystems and no LLM calls.

Assert external behavior only: ledger state, file destinations, two-stage confirmation exits, and error paths.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.episode.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    discover_episode_file_aliases,
    discover_episode_files,
)
from lib.episode.episode_reset import (
    EpisodeResetConflictError,
    EpisodeResetError,
    EpisodeResetResult,
    ResetConfirmationRequired,
    reset_episode_planning,
)
from lib.episode.episode_sources import discover_sources, planning_start

# Every case coordinates EpisodeReset / ProjectManager / EpisodePlanner with real tmp_path filesystems,
# without mocking tested public entries; classify as integration under docs/standards/testing.md.

SOURCE = "第一章 山村少年。李恒在山村长大。第二章 下山。李恒辞别师父。第三章 风波。少女身份成谜。"


def _write_project(
    tmp_path: Path,
    *,
    episodes: list | None = None,
    extra: dict | None = None,
    source_text: str = SOURCE,
) -> Path:
    project_dir = tmp_path / "projects" / "demo-proj"
    (project_dir / "source").mkdir(parents=True)
    project = {
        "schema_version": 3,
        "title": "测试项目",
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "style": "国漫",
        "characters": {},
        "scenes": {},
        "props": {},
        "episodes": episodes or [],
        "whole_source_files": [{"source_file": "source/novel.txt"}],
    }
    if extra:
        project.update(extra)
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (project_dir / "source" / "novel.txt").write_text(source_text, encoding="utf-8")
    return project_dir


def _load_project(project_dir: Path) -> dict:
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))


def _planning_start(project_dir: Path) -> tuple[str, int] | None:
    """Ledger-derived continuation planning start."""
    project = _load_project(project_dir)
    return planning_start(project, discover_sources(project_dir, project))


def _entry(num: int, *, source_range: dict | None, status: str = "planned") -> dict:
    return {
        "episode": num,
        "title": f"第 {num} 集",
        "script_file": f"scripts/episode_{num}.json",
        "source_range": source_range,
        "ledger_status": status,
        "source_origin": "whole_source",
    }


def _write_script(project_dir: Path, num: int) -> Path:
    scripts = project_dir / "scripts"
    scripts.mkdir(exist_ok=True)
    path = scripts / f"episode_{num}.json"
    path.write_text("{}", encoding="utf-8")
    return path


def _write_claimed_artifact(
    project_dir: Path,
    adapter: ProjectArtifactManifestAdapter,
    key: ArtifactKey,
    relative_path: str,
    *,
    digest_byte: str,
) -> None:
    path = project_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(relative_path.encode("utf-8"))
    adapter.put_entry(
        key,
        ArtifactManifestEntry(
            artifact_path=relative_path,
            basis_digest=f"sha256-v1:{digest_byte * 64}",
        ),
    )


# ---------------------------------------------------------------------------
# Full reset must succeed despite any ledger corruption.
# ---------------------------------------------------------------------------


def test_reset_on_corrupted_ledger_clears_everything(tmp_path: Path) -> None:
    """Out-of-bounds cursor/entry coordinates plus ledger/disk disagreement still permit full reset and subsequent planning from the beginning."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 99999}),
            _entry(2, source_range={"source_file": "source/gone.txt", "start": 500, "end": 900}),
        ],
    )
    (project_dir / "source" / "episode_1.txt").write_text("旧内容", encoding="utf-8")

    result = reset_episode_planning(project_dir)

    assert isinstance(result, EpisodeResetResult)
    assert result.removed_episodes == [1, 2]
    project = _load_project(project_dir)
    assert project["episodes"] == []
    assert _planning_start(project_dir) == ("source/novel.txt", 0)
    # Reset planning start to the first source beginning, allowing plan to restart normally.
    assert _planning_start(project_dir) == ("source/novel.txt", 0)


def test_full_reset_succeeds_with_an_unreadable_artifact_manifest_and_leaves_it_alone(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10})],
        extra={"schema_version": 8},
    )
    derived = project_dir / "source" / "episode_1.txt"
    derived.write_text(SOURCE[:10], encoding="utf-8")
    manifest = project_dir / MANIFEST_FILENAME
    manifest.write_bytes(b"{not-json")

    result = reset_episode_planning(project_dir)

    assert isinstance(result, EpisodeResetResult)
    assert _load_project(project_dir)["episodes"] == []
    assert not derived.exists()
    assert manifest.read_bytes() == b"{not-json"


def test_reset_clears_source_fingerprints(tmp_path: Path) -> None:
    """Source fingerprints invalidate with the ledger: clear existing fields, tolerate absent ones."""
    project_dir = _write_project(
        tmp_path,
        extra={SOURCE_FINGERPRINTS_KEY: {"source/novel.txt": "deadbeef"}},
    )

    reset_episode_planning(project_dir)

    assert SOURCE_FINGERPRINTS_KEY not in _load_project(project_dir)


def test_reset_removes_remaining_file(tmp_path: Path) -> None:
    """Remove remainder files superseded by ledger-derived planning starts; otherwise source/ retains stale text unrelated to the ledger."""
    project_dir = _write_project(tmp_path)
    remaining = project_dir / "source" / "_remaining.txt"
    remaining.write_text("第三章 风波。少女身份成谜。", encoding="utf-8")

    reset_episode_planning(project_dir)

    assert not remaining.exists()
    assert _planning_start(project_dir) == ("source/novel.txt", 0)


# ---------------------------------------------------------------------------
# File handling: delete derived content, preserve nonderived originals.
# ---------------------------------------------------------------------------


def test_derived_files_deleted_and_legacy_files_archived(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range=None),
        ],
    )
    derived = project_dir / "source" / "episode_1.txt"
    derived.write_text(SOURCE[:10], encoding="utf-8")
    legacy = project_dir / "source" / "episode_2.txt"
    legacy.write_text("老项目手工内容", encoding="utf-8")

    result = reset_episode_planning(project_dir)

    assert isinstance(result, EpisodeResetResult)
    assert not derived.exists()
    assert result.deleted_files == ["source/episode_1.txt"]
    assert not legacy.exists()
    archived = project_dir / "source" / "_episode_2.txt.bak"
    assert archived.read_text(encoding="utf-8") == "老项目手工内容"
    assert result.archived_files == [("source/episode_2.txt", "source/_episode_2.txt.bak")]


def test_archived_file_left_out_of_discovery(tmp_path: Path) -> None:
    """Preserved originals enter neither source candidates nor derived-episode reclamation."""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(2, source_range=None)],
    )
    (project_dir / "source" / "episode_2.txt").write_text("老项目手工内容", encoding="utf-8")

    reset_episode_planning(project_dir)

    assert discover_episode_files(project_dir) == {}
    assert [doc.rel_path for doc in discover_sources(project_dir, _load_project(project_dir))] == ["source/novel.txt"]


def test_archive_does_not_overwrite_existing_backup(tmp_path: Path) -> None:
    """Repeated resets do not overwrite earlier preserved originals; append numbers on collisions."""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(2, source_range=None)],
    )
    (project_dir / "source" / "_episode_2.txt.bak").write_text("上一次的留底", encoding="utf-8")
    (project_dir / "source" / "episode_2.txt").write_text("这一次的内容", encoding="utf-8")

    reset_episode_planning(project_dir)

    assert (project_dir / "source" / "_episode_2.txt.bak").read_text(encoding="utf-8") == "上一次的留底"
    assert (project_dir / "source" / "_episode_2.txt.1.bak").read_text(encoding="utf-8") == "这一次的内容"


def test_orphan_episode_file_archived(tmp_path: Path) -> None:
    """Orphan episode files without ledger entries have no coordinates: preserve instead of deleting, avoiding orphan registration rebuilding entries."""
    project_dir = _write_project(tmp_path)
    (project_dir / "source" / "episode_7.txt").write_text("孤儿内容", encoding="utf-8")

    result = reset_episode_planning(project_dir)

    assert isinstance(result, EpisodeResetResult)
    assert result.archived_files == [("source/episode_7.txt", "source/_episode_7.txt.bak")]
    assert discover_episode_files(project_dir) == {}


def test_file_failure_aborts_and_leaves_ledger_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """File-handling failure aborts and rolls ledger writes back, avoiding empty ledgers with leftover files that could be reclaimed."""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10})],
    )
    (project_dir / "source" / "episode_1.txt").write_text(SOURCE[:10], encoding="utf-8")
    before = _load_project(project_dir)

    def _boom(self: Path, missing_ok: bool = False) -> None:
        raise OSError("device busy")

    monkeypatch.setattr(Path, "unlink", _boom)

    with pytest.raises(EpisodeResetError, match="派生集文件删除失败"):
        reset_episode_planning(project_dir)

    assert _load_project(project_dir) == before


def test_conflict_when_new_consumed_episode_appears_during_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Locked rescans finding newly consumed episodes outside confirmation manifests reject commit without ledger changes. This protects agreement
    between the confirmed list and actual commit-time state; monkeypatch simulates that race window."""
    # Get module objects through sys.modules for monkeypatch, avoiding another import style;
    # lib.episode.episode_reset already has a top-level from-import.
    episode_reset_module = sys.modules["lib.episode.episode_reset"]
    project_dir = _write_project(tmp_path)
    before = _load_project(project_dir)
    original_scan = episode_reset_module._scan
    calls = {"n": 0}

    def _fake_scan(pd: Path, project: dict, *, retained: frozenset[int] = frozenset()):
        calls["n"] += 1
        result = original_scan(pd, project, retained=retained)
        if calls["n"] == 2:  # The second call occurs under lock in _commit rescan.
            result.consumed.append(1)
        return result

    monkeypatch.setattr(episode_reset_module, "_scan", _fake_scan)

    with pytest.raises(EpisodeResetConflictError):
        reset_episode_planning(project_dir)

    assert _load_project(project_dir) == before


# ---------------------------------------------------------------------------
# Two-stage confirmation for consumed episodes.
# ---------------------------------------------------------------------------


def test_consumed_requires_confirmation_and_writes_nothing(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}, status="consumed"),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    derived = project_dir / "source" / "episode_1.txt"
    derived.write_text("已消费集", encoding="utf-8")
    before = _load_project(project_dir)

    result = reset_episode_planning(project_dir)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [1]
    assert _load_project(project_dir) == before
    assert derived.exists()


def test_consumed_detected_from_disk_when_ledger_status_missing(tmp_path: Path) -> None:
    """When ledger status is untrusted, disk outputs govern: entries without ledger_status but with scripts still require confirmation."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            {
                "episode": 1,
                "title": "第 1 集",
                "script_file": "scripts/episode_1.json",
                "source_origin": "whole_source",
            }
        ],
    )
    _write_script(project_dir, 1)

    result = reset_episode_planning(project_dir)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [1]


def test_confirmed_reset_keeps_downstream_products(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}, status="consumed"),
        ],
    )
    script = _write_script(project_dir, 1)
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    script_plan = drafts / "script_plan_segments.json"
    script_plan.write_text("{}", encoding="utf-8")

    result = reset_episode_planning(project_dir, confirm_consumed=True)

    assert isinstance(result, EpisodeResetResult)
    assert result.consumed_episodes == [1]
    assert result.retired_episodes == [1]
    assert result.removed_episodes == []
    assert script.is_file()
    assert script_plan.is_file()
    [entry] = _load_project(project_dir)["episodes"]
    assert (entry["episode"], entry["source_origin"], entry["ledger_status"]) == (1, "none", "stale")
    assert "source_range" not in entry
    assert _planning_start(project_dir) == ("source/novel.txt", 0)


# ---------------------------------------------------------------------------
# Partial reset not yet supported.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Corruption boundaries: nonlist episodes / symlinks / orphan disk outputs / same-ID filename aliases.
# ---------------------------------------------------------------------------


def test_reset_tolerates_non_list_episodes(tmp_path: Path) -> None:
    """Truthy nonlist episodes, such as manually written integers, count as empty ledgers without crashing."""
    project_dir = _write_project(tmp_path, extra={"episodes": 1})

    result = reset_episode_planning(project_dir)

    assert isinstance(result, EpisodeResetResult)
    assert result.removed_episodes == []
    assert _load_project(project_dir)["episodes"] == []


def test_reset_rejects_symlinked_source_dir(tmp_path: Path) -> None:
    """Reject source/ symlinks outside projects to avoid deleting/renaming external files."""
    project_dir = _write_project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "episode_1.txt").write_text("外部文件", encoding="utf-8")
    source_dir = project_dir / "source"
    shutil.rmtree(source_dir)
    source_dir.symlink_to(outside, target_is_directory=True)
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="符号链接"):
        reset_episode_planning(project_dir)

    assert (outside / "episode_1.txt").exists()
    assert _load_project(project_dir) == before


def test_reset_deletes_symlinked_episode_file_without_touching_target(tmp_path: Path) -> None:
    """Individual derived-episode symlinks are handled normally: unlink/rename affects link entries only, never
    follows the final target, and preserves outside files, unlike symlinked source/ directories."""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10})],
    )
    outside_target = tmp_path / "outside_episode_1.txt"
    outside_target.write_text("外部文件", encoding="utf-8")
    link = project_dir / "source" / "episode_1.txt"
    link.symlink_to(outside_target)

    result = reset_episode_planning(project_dir)

    assert isinstance(result, EpisodeResetResult)
    assert not link.exists()
    assert outside_target.exists()
    assert outside_target.read_text(encoding="utf-8") == "外部文件"


def test_reset_clears_dangling_symlinked_episode_file(tmp_path: Path) -> None:
    """Find/clean dangling symlinks too; otherwise EpisodePlanner symlink checks would block the next plan_episodes
    derived-file write, breaking reset's promise of continued planning."""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(2, source_range=None)],
    )
    link = project_dir / "source" / "episode_2.txt"
    link.symlink_to(project_dir / "source" / "does_not_exist.txt")

    result = reset_episode_planning(project_dir)

    assert isinstance(result, EpisodeResetResult)
    assert not link.exists()
    assert result.archived_files
    archived_target = project_dir / result.archived_files[0][1]
    assert archived_target.is_symlink()


def test_reset_aggregates_consumed_status_across_duplicate_episode_entries(tmp_path: Path) -> None:
    """Duplicate episode IDs with first planned and later consumed entries aggregate consumption across all
    entries; examining only the deduplicated first entry would miss it."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}, status="planned"),
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}, status="consumed"),
        ],
    )

    result = reset_episode_planning(project_dir)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [1]


def test_reset_aggregates_downstream_products_across_duplicate_entries(tmp_path: Path) -> None:
    """Duplicate episode IDs may first reference missing canonical scripts, then existing custom script_file
    such as scripts/custom_name.json. Consumption must still match, rather than passing only the first
    setdefault-retained entry to has_downstream_products."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            {"episode": 1, "title": "首条", "script_file": "scripts/episode_1.json", "source_origin": "whole_source"},
            {"episode": 1, "title": "后条", "script_file": "scripts/custom_name.json", "source_origin": "whole_source"},
        ],
    )
    scripts = project_dir / "scripts"
    scripts.mkdir()
    (scripts / "custom_name.json").write_text("{}", encoding="utf-8")

    result = reset_episode_planning(project_dir)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [1]


def test_reset_archives_when_source_range_is_structurally_incomplete(tmp_path: Path) -> None:
    """Empty/incomplete source_range mappings satisfy isinstance(Mapping) but lack usable coordinates;
    treat as unreconstructable from the ledger, preserving rather than deleting files."""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(1, source_range={})],
    )
    derived = project_dir / "source" / "episode_1.txt"
    derived.write_text(SOURCE[:10], encoding="utf-8")

    result = reset_episode_planning(project_dir)

    assert isinstance(result, EpisodeResetResult)
    assert result.deleted_files == []
    assert result.archived_files
    assert not derived.exists()


def test_reset_archives_when_any_duplicate_entry_lacks_source_range(tmp_path: Path) -> None:
    """Duplicate episode IDs with source_range only on the first entry cannot prove reconstructability;
    preserve rather than irreversibly delete when evidence conflicts."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(1, source_range=None),
        ],
    )
    derived = project_dir / "source" / "episode_1.txt"
    derived.write_text(SOURCE[:10], encoding="utf-8")

    result = reset_episode_planning(project_dir)

    assert isinstance(result, EpisodeResetResult)
    assert result.deleted_files == []
    assert result.archived_files
    assert not derived.exists()


def test_archive_path_increments_past_dangling_symlink_collision(tmp_path: Path) -> None:
    """Dangling symlinks occupying preserved-original destinations still count as occupied, requiring numbered names;
    exists() alone returns False, allowing rename() to silently overwrite previous preserved originals."""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(2, source_range=None)],
    )
    stale_backup = project_dir / "source" / "_episode_2.txt.bak"
    stale_backup.symlink_to(project_dir / "source" / "does_not_exist_either.txt")
    legacy = project_dir / "source" / "episode_2.txt"
    legacy.write_text("这一次的内容", encoding="utf-8")

    result = reset_episode_planning(project_dir)

    assert isinstance(result, EpisodeResetResult)
    assert stale_backup.is_symlink()  # Previous preserved dangling link remains untouched.
    new_backup = project_dir / "source" / "_episode_2.txt.1.bak"
    assert new_backup.read_text(encoding="utf-8") == "这一次的内容"
    assert result.archived_files == [("source/episode_2.txt", "source/_episode_2.txt.1.bak")]


def test_discover_episode_files_prefers_readable_over_dangling_alias(tmp_path: Path) -> None:
    """Prefer readable representative files even when dangling aliases sort first, preventing content readers
    from mistaking unreadable links for episodes without content."""
    project_dir = _write_project(tmp_path)
    valid = project_dir / "source" / "episode_1.txt"
    valid.write_text(SOURCE[:10], encoding="utf-8")
    dangling = project_dir / "source" / "episode_01.txt"  # Sorts before episode_1.txt.
    dangling.symlink_to(project_dir / "source" / "does_not_exist.txt")

    assert discover_episode_files(project_dir)[1] == valid


def test_discover_episode_files_skips_episode_with_only_dangling_alias(tmp_path: Path) -> None:
    """If every episode-ID alias is dangling without real content, omit that ID from representative mappings
    instead of returning unreadable paths; otherwise orphan registration would invent ghost ledger entries
    for contentless dangling IDs."""
    project_dir = _write_project(tmp_path)
    dangling = project_dir / "source" / "episode_9.txt"
    dangling.symlink_to(project_dir / "source" / "does_not_exist.txt")

    assert 9 not in discover_episode_files(project_dir)
    assert discover_episode_file_aliases(project_dir)[9] == [dangling]


def test_empty_drafts_dir_does_not_require_confirmation(tmp_path: Path) -> None:
    """Empty drafts/episode_N/ directories, such as rejected draft-save leftovers, are not consumed outputs
    and must not trigger confirmation merely by existing."""
    project_dir = _write_project(tmp_path)
    (project_dir / "drafts" / "episode_1").mkdir(parents=True)

    result = reset_episode_planning(project_dir)

    assert isinstance(result, EpisodeResetResult)


def test_reset_tolerates_unconvertible_digit_episode_num(tmp_path: Path) -> None:
    """Episode characters accepted by str.isdigit() but not int(), such as superscript ², must not crash;
    skip unrecognized IDs unchanged without blocking the reset recovery exit."""
    project_dir = _write_project(
        tmp_path,
        episodes=[{"episode": "²", "title": "损坏集号"}],
    )

    result = reset_episode_planning(project_dir)

    assert isinstance(result, EpisodeResetResult)
    assert result.removed_episodes == []


def test_orphan_disk_product_with_padded_filename_requires_confirmation(tmp_path: Path) -> None:
    """Missing ledger entries with padded noncanonical output names (episode_01.json instead of
    episode_1.json) still require confirmation; canonical-only has_downstream_products must not
    miss consumption."""
    project_dir = _write_project(tmp_path)
    scripts = project_dir / "scripts"
    scripts.mkdir()
    (scripts / "episode_01.json").write_text("{}", encoding="utf-8")

    result = reset_episode_planning(project_dir)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [1]


def test_orphan_disk_product_requires_confirmation(tmp_path: Path) -> None:
    """Missing ledger entries/source/episode_N.txt still require confirmation when scripts/ outputs remain."""
    project_dir = _write_project(tmp_path)
    _write_script(project_dir, 1)

    result = reset_episode_planning(project_dir)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [1]


def test_orphan_draft_dir_requires_confirmation(tmp_path: Path) -> None:
    """Missing ledger entries/source/episode_N.txt still require confirmation when drafts/ script_plan outputs remain."""
    project_dir = _write_project(tmp_path)
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "script_plan_segments.json").write_text("{}", encoding="utf-8")

    result = reset_episode_planning(project_dir)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [1]


def test_reset_processes_all_padding_aliases_of_same_episode(tmp_path: Path) -> None:
    """Handle every padding alias for one ID (episode_1.txt / episode_01.txt),
    or unhandled aliases would let orphan registration rebuild ledger entries."""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10})],
    )
    alias_a = project_dir / "source" / "episode_1.txt"
    alias_a.write_text(SOURCE[:10], encoding="utf-8")
    alias_b = project_dir / "source" / "episode_01.txt"
    alias_b.write_text(SOURCE[:10], encoding="utf-8")

    result = reset_episode_planning(project_dir)

    assert isinstance(result, EpisodeResetResult)
    assert not alias_a.exists()
    assert not alias_b.exists()
    assert discover_episode_files(project_dir) == {}


def test_reset_rejects_non_positive_episode_id(tmp_path: Path) -> None:
    project_dir = _write_project(tmp_path)
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="正整数"):
        reset_episode_planning(project_dir, episode_id=0)

    assert _load_project(project_dir) == before


# ---------------------------------------------------------------------------
# Partial-reset success paths.
# ---------------------------------------------------------------------------


def test_partial_reset_keeps_retained_episodes_and_rewinds_cursor(tmp_path: Path) -> None:
    """After three planned episodes, partial reset from ID 2 retains only ID 1 and moves cursor to its source-range end."""
    end1 = 10
    end2 = 20
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": end1}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": end1, "end": end2}),
            _entry(3, source_range={"source_file": "source/novel.txt", "start": end2, "end": end2 + 10}),
        ],
    )
    (project_dir / "source" / "episode_2.txt").write_text(SOURCE[end1:end2], encoding="utf-8")
    (project_dir / "source" / "episode_3.txt").write_text(SOURCE[end2 : end2 + 10], encoding="utf-8")

    result = reset_episode_planning(project_dir, episode_id=2)

    assert isinstance(result, EpisodeResetResult)
    assert result.removed_episodes == [2, 3]
    project = _load_project(project_dir)
    assert [e["episode"] for e in project["episodes"]] == [1]
    assert _planning_start(project_dir) == ("source/novel.txt", end1)
    assert _planning_start(project_dir) == ("source/novel.txt", end1)


def test_partial_reset_deletes_derived_files_in_range_keeps_retained(tmp_path: Path) -> None:
    """Delete reset-range derived files with source_range while preserving retained-range derived files."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    retained_file = project_dir / "source" / "episode_1.txt"
    retained_file.write_text(SOURCE[:10], encoding="utf-8")
    reset_file = project_dir / "source" / "episode_2.txt"
    reset_file.write_text(SOURCE[10:20], encoding="utf-8")

    result = reset_episode_planning(project_dir, episode_id=2)

    assert isinstance(result, EpisodeResetResult)
    assert result.deleted_files == ["source/episode_2.txt"]
    assert not reset_file.exists()
    assert retained_file.exists()  # Retained-range files unchanged.


def test_partial_reset_archives_file_without_source_range_in_range(tmp_path: Path) -> None:
    """Reset-range episode files without source_range are unreconstructable; preserve instead of deleting."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range=None),
        ],
    )
    (project_dir / "source" / "episode_1.txt").write_text(SOURCE[:10], encoding="utf-8")
    legacy = project_dir / "source" / "episode_2.txt"
    legacy.write_text("老项目手工内容", encoding="utf-8")

    result = reset_episode_planning(project_dir, episode_id=2)

    assert isinstance(result, EpisodeResetResult)
    assert result.archived_files == [("source/episode_2.txt", "source/_episode_2.txt.bak")]
    assert not legacy.exists()


# ---------------------------------------------------------------------------
# Partial-reset precheck rejection preserves both ledger and files.
# ---------------------------------------------------------------------------


def test_partial_reset_rejects_when_episode_id_not_in_ledger(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10})],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="不在账本中"):
        reset_episode_planning(project_dir, episode_id=5)

    assert _load_project(project_dir) == before


def test_partial_reset_boundary_follows_ledger_order_not_episode_ids(tmp_path: Path) -> None:
    """Ledger order is broadcast order; noncontiguous/unordered IDs retain entries preceding the target in ledger order."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(7, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
            _entry(5, source_range={"source_file": "source/novel.txt", "start": 20, "end": 30}),
        ],
    )

    result = reset_episode_planning(project_dir, episode_id=2)

    assert isinstance(result, EpisodeResetResult)
    assert sorted(result.removed_episodes) == [2, 5]
    project = _load_project(project_dir)
    assert [e["episode"] for e in project["episodes"]] == [7]
    assert _planning_start(project_dir) == ("source/novel.txt", 10)


def test_reset_from_first_ledger_episode_is_full_reset(tmp_path: Path) -> None:
    """Targets first in broadcast order have no retained range and use full-reset semantics."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(4, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )

    result = reset_episode_planning(project_dir, episode_id=4)

    assert isinstance(result, EpisodeResetResult)
    project = _load_project(project_dir)
    assert project["episodes"] == []
    assert _planning_start(project_dir) == ("source/novel.txt", 0)


def test_partial_reset_rejects_when_retain_boundary_has_no_source_range(tmp_path: Path) -> None:
    """Without any positioned episodes in the retained range, postreset planning start cannot be determined."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range=None),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="没有带可信原文范围记录"):
        reset_episode_planning(project_dir, episode_id=2)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_structurally_incomplete_boundary_same_as_missing(tmp_path: Path) -> None:
    """Incomplete source_range with invalid fields, though non-None, matches missing-range semantics: no trusted coordinates."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": "0", "end": 10}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="没有带可信原文范围记录"):
        reset_episode_planning(project_dir, episode_id=2)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_on_fingerprint_mismatch(tmp_path: Path) -> None:
    """Recorded source fingerprints differing from current text reject without changing ledger/files."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
        extra={SOURCE_FINGERPRINTS_KEY: {"source/novel.txt": "0" * 64}},  # Incorrect fingerprint.
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="已被修改或移除"):
        reset_episode_planning(project_dir, episode_id=2)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_when_retained_range_out_of_bounds(tmp_path: Path) -> None:
    """Reject retained coordinates beyond current shorter replacement source length, even when absent fingerprints bypass their gate."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 99999}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 99999, "end": 100010}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="原文范围无效"):
        reset_episode_planning(project_dir, episode_id=2)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_overlapping_retained_ranges(tmp_path: Path) -> None:
    """Reject overlapping/backward source ranges in broadcast-adjacent retained extracted episodes; corruption would disconnect
    the restored cursor from the truly consumed range."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 20}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 30}),
            _entry(3, source_range={"source_file": "source/novel.txt", "start": 30, "end": 40}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="重叠或倒退"):
        reset_episode_planning(project_dir, episode_id=3)

    assert _load_project(project_dir) == before


def test_partial_reset_tolerates_unsplit_source_gaps(tmp_path: Path) -> None:
    """Unsplit gaps before/between retained extracted episodes, left by deleting extracted episodes,
    are legal; restore cursor to the last retained extracted episode's source-range end."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 5, "end": 10}),
            _entry(3, source_range={"source_file": "source/novel.txt", "start": 20, "end": 30}),
            _entry(4, source_range={"source_file": "source/novel.txt", "start": 30, "end": 40}),
        ],
    )

    result = reset_episode_planning(project_dir, episode_id=4)

    assert isinstance(result, EpisodeResetResult)
    project = _load_project(project_dir)
    assert [e["episode"] for e in project["episodes"]] == [1, 3]
    assert _planning_start(project_dir) == ("source/novel.txt", 30)


def test_partial_reset_rejects_backward_source_file_switch(tmp_path: Path) -> None:
    """Reject retained ranges returning to an earlier-ordered source file in broadcast order."""
    project_dir = _write_project(tmp_path)
    (project_dir / "source" / "a.txt").write_text("A" * 10, encoding="utf-8")
    (project_dir / "source" / "c.txt").write_text("C" * 10, encoding="utf-8")
    project = _load_project(project_dir)
    project["whole_source_files"] = [
        {"source_file": "source/a.txt"},
        {"source_file": "source/c.txt"},
        {"source_file": "source/novel.txt"},
    ]
    project["episodes"] = [
        _entry(1, source_range={"source_file": "source/c.txt", "start": 0, "end": 10}),
        _entry(2, source_range={"source_file": "source/a.txt", "start": 0, "end": 10}),
        _entry(3, source_range={"source_file": "source/a.txt", "start": 10, "end": 10}),
    ]
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="重叠或倒退"):
        reset_episode_planning(project_dir, episode_id=3)

    assert _load_project(project_dir) == before


def test_partial_reset_allows_first_episode_after_blank_leading_source(tmp_path: Path) -> None:
    """Whitespace-only whole-source files before episode 1's source are automatically skipped by EpisodePlanner;
    a first episode beginning in a later file is valid, not ledger corruption."""
    project_dir = _write_project(tmp_path)
    (project_dir / "source" / "a.txt").write_text("   \n  ", encoding="utf-8")
    (project_dir / "source" / "b.txt").write_text("B" * 10, encoding="utf-8")
    project = _load_project(project_dir)
    project["whole_source_files"] = [
        {"source_file": "source/a.txt"},
        {"source_file": "source/b.txt"},
        {"source_file": "source/novel.txt"},
    ]
    project["episodes"] = [
        _entry(1, source_range={"source_file": "source/b.txt", "start": 0, "end": 10}),
        _entry(2, source_range={"source_file": "source/b.txt", "start": 10, "end": 10}),
    ]
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    result = reset_episode_planning(project_dir, episode_id=2)

    assert isinstance(result, EpisodeResetResult)
    assert _planning_start(project_dir) == ("source/b.txt", 10)


def test_partial_reset_allows_skip_over_blank_middle_source(tmp_path: Path) -> None:
    """Adjacent retained episodes may also skip whitespace-only intermediate source files; they need not use immediately
    consecutive files."""
    project_dir = _write_project(tmp_path)
    (project_dir / "source" / "a.txt").write_text("A" * 10, encoding="utf-8")
    (project_dir / "source" / "b.txt").write_text("   \n  ", encoding="utf-8")
    (project_dir / "source" / "c.txt").write_text("C" * 10, encoding="utf-8")
    project = _load_project(project_dir)
    project["whole_source_files"] = [
        {"source_file": "source/a.txt"},
        {"source_file": "source/b.txt"},
        {"source_file": "source/c.txt"},
        {"source_file": "source/novel.txt"},
    ]
    project["episodes"] = [
        _entry(1, source_range={"source_file": "source/a.txt", "start": 0, "end": 10}),
        _entry(2, source_range={"source_file": "source/c.txt", "start": 0, "end": 10}),
        _entry(3, source_range={"source_file": "source/c.txt", "start": 10, "end": 10}),
    ]
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    result = reset_episode_planning(project_dir, episode_id=3)

    assert isinstance(result, EpisodeResetResult)
    assert _planning_start(project_dir) == ("source/c.txt", 10)


def test_full_reset_keeps_own_source_and_no_source_episodes(tmp_path: Path) -> None:
    """Full reset removes only extracted episodes; self-sourced/sourceless episodes and files stay in place with relative order unchanged."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            {"episode": 5, "title": "自带", "script_file": "scripts/episode_5.json", "source_origin": "own"},
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            {"episode": 6, "title": "待填", "script_file": "scripts/episode_6.json", "source_origin": "none"},
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    (project_dir / "source" / "episode_5.txt").write_text("自带的原文", encoding="utf-8")
    (project_dir / "source" / "episode_1.txt").write_text(SOURCE[:10], encoding="utf-8")

    result = reset_episode_planning(project_dir)

    assert isinstance(result, EpisodeResetResult)
    assert result.removed_episodes == [1, 2]
    project = _load_project(project_dir)
    assert [(e["episode"], e["source_origin"]) for e in project["episodes"]] == [(5, "own"), (6, "none")]
    assert (project_dir / "source" / "episode_5.txt").read_text(encoding="utf-8") == "自带的原文"
    assert not (project_dir / "source" / "episode_1.txt").exists()
    assert _planning_start(project_dir) == ("source/novel.txt", 0)


def test_partial_reset_skips_other_origin_episodes_in_the_retained_segment(tmp_path: Path) -> None:
    """Retained self-sourced episodes occupy no whole-source positions, skip coordinate checks, and remain unchanged after reset."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            {"episode": 2, "title": "自带", "script_file": "scripts/episode_2.json", "source_origin": "own"},
            _entry(3, source_range={"source_file": "source/novel.txt", "start": 20, "end": 30}),
            _entry(4, source_range={"source_file": "source/novel.txt", "start": 30, "end": 40}),
        ],
    )

    result = reset_episode_planning(project_dir, episode_id=4)

    assert isinstance(result, EpisodeResetResult)
    project = _load_project(project_dir)
    assert [e["episode"] for e in project["episodes"]] == [1, 2, 3]
    assert _planning_start(project_dir) == ("source/novel.txt", 30)


@pytest.mark.parametrize("origin", ["own", "none"])
def test_reset_from_an_episode_that_was_not_cut_is_refused(tmp_path: Path, origin: str) -> None:
    """Reset boundaries must be extracted episodes; self-sourced/sourceless targets reject without ledger changes or clearing later extracted episodes."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            {"episode": 2, "title": "番外", "script_file": "scripts/episode_2.json", "source_origin": origin},
            _entry(3, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="不是切自整本源文的集"):
        reset_episode_planning(project_dir, episode_id=2)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_zero_length_retained_range(tmp_path: Path) -> None:
    """Reject zero-length retained coordinates start == end, which are not trusted ranges;
    otherwise an empty retained episode would move cursor to its beginning and misalign numbering."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 0}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="原文范围无效"):
        reset_episode_planning(project_dir, episode_id=2)

    assert _load_project(project_dir) == before


def test_partial_reset_accepts_retain_boundary_without_ledger_status(tmp_path: Path) -> None:
    """Cursor restoration without ledger_status still accepts structurally complete, in-bounds continuous source_range;
    positions are authoritative, while status is advisory."""
    project_dir = _write_project(tmp_path)
    project = _load_project(project_dir)
    entry = _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10})
    del entry["ledger_status"]
    project["episodes"] = [
        entry,
        _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
    ]
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    result = reset_episode_planning(project_dir, episode_id=2)

    assert isinstance(result, EpisodeResetResult)
    after = _load_project(project_dir)
    assert [e["episode"] for e in after["episodes"]] == [1]
    assert _planning_start(project_dir) == ("source/novel.txt", 10)


def test_partial_reset_trusts_source_range_under_legacy_status(tmp_path: Path) -> None:
    """Retired legacy statuses no longer invalidate coordinates; trust complete, in-bounds continuous source_range normally."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(
                1,
                source_range={"source_file": "source/novel.txt", "start": 0, "end": 10},
                status="已废弃的状态",
            ),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )

    result = reset_episode_planning(project_dir, episode_id=2)

    assert isinstance(result, EpisodeResetResult)
    assert _planning_start(project_dir) == ("source/novel.txt", 10)


def test_partial_reset_rejects_non_positive_episode_numbers_in_ledger(tmp_path: Path) -> None:
    """Reject nonpositive ledger episode IDs such as corrupt 0; their retained-versus-reset membership cannot be proven."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(0, source_range={"source_file": "source/novel.txt", "start": 0, "end": 0}),
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="非法集 ID"):
        reset_episode_planning(project_dir, episode_id=2)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_duplicate_episode_numbers(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="重复集 ID"):
        reset_episode_planning(project_dir, episode_id=2)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_non_list_episodes(tmp_path: Path) -> None:
    project_dir = _write_project(tmp_path, extra={"episodes": 1})
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="形状异常"):
        reset_episode_planning(project_dir, episode_id=2)

    assert _load_project(project_dir) == before


# ---------------------------------------------------------------------------
# Two-stage confirmation for consumed episodes during partial reset.
# ---------------------------------------------------------------------------


def test_partial_reset_consumed_within_range_requires_confirmation(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(
                2,
                source_range={"source_file": "source/novel.txt", "start": 10, "end": 20},
                status="consumed",
            ),
        ],
    )
    script = _write_script(project_dir, 2)
    before = _load_project(project_dir)

    result = reset_episode_planning(project_dir, episode_id=2)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [2]
    assert _load_project(project_dir) == before
    assert script.is_file()


def test_partial_reset_consumed_before_range_not_flagged(tmp_path: Path) -> None:
    """Consumption in retained ranges does not affect confirmation; confirm only episodes within this reset range."""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}, status="consumed"),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    _write_script(project_dir, 1)

    result = reset_episode_planning(project_dir, episode_id=2)

    assert isinstance(result, EpisodeResetResult)


def test_partial_reset_confirmed_keeps_downstream_products(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(
                2,
                source_range={"source_file": "source/novel.txt", "start": 10, "end": 20},
                status="consumed",
            ),
        ],
    )
    script = _write_script(project_dir, 2)

    result = reset_episode_planning(project_dir, episode_id=2, confirm_consumed=True)

    assert isinstance(result, EpisodeResetResult)
    assert result.consumed_episodes == [2]
    assert script.is_file()
    project = _load_project(project_dir)
    assert [(e["episode"], e["source_origin"], e["ledger_status"]) for e in project["episodes"]] == [
        (1, "whole_source", "planned"),
        (2, "none", "stale"),
    ]
    assert _planning_start(project_dir) == ("source/novel.txt", 10)


def test_partial_reset_keeps_every_artifact_claim(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(
                2,
                source_range={"source_file": "source/novel.txt", "start": 10, "end": 20},
                status="consumed",
            ),
        ],
        extra={"schema_version": 8},
    )
    (project_dir / "source" / "episode_1.txt").write_text(SOURCE[:10], encoding="utf-8")
    (project_dir / "source" / "episode_2.txt").write_text(SOURCE[10:20], encoding="utf-8")
    _write_script(project_dir, 1)
    _write_script(project_dir, 2)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    retained_key = ArtifactKey.episode_script_plan(1)
    _write_claimed_artifact(
        project_dir,
        adapter,
        retained_key,
        "drafts/episode_1/script_plan_segments.json",
        digest_byte="1",
    )
    removed: dict[ArtifactKey, str] = {
        ArtifactKey.episode_script_plan(2): "drafts/episode_2/script_plan_segments.json",
        ArtifactKey.episode_script(2): "scripts/episode_2.json",
        ArtifactKey.episode_grid(2, "grid_000000000002"): "grids/grid_000000000002.png",
        ArtifactKey.episode_script_plan(3): "drafts/episode_3/script_plan_segments.json",
    }
    for index, key in enumerate(ArtifactKey.episode_resource_artifacts(2, "E2S01"), start=3):
        removed[key] = f"retained-media/episode-2-{index}.bin"
    for index, (key, relative_path) in enumerate(removed.items(), start=2):
        if not (project_dir / relative_path).exists():
            _write_claimed_artifact(
                project_dir,
                adapter,
                key,
                relative_path,
                digest_byte=hex(index)[-1],
            )
        else:
            adapter.put_entry(
                key,
                ArtifactManifestEntry(
                    artifact_path=relative_path,
                    basis_digest=f"sha256-v1:{hex(index)[-1] * 64}",
                ),
            )

    before = adapter.snapshot_entries()

    result = reset_episode_planning(project_dir, episode_id=2, confirm_consumed=True)

    assert isinstance(result, EpisodeResetResult)
    assert adapter.snapshot_entries() == before
    assert retained_key in before
    assert set(removed) <= set(before)
    assert all((project_dir / relative_path).is_file() for relative_path in removed.values())


def test_a_failed_file_step_restores_reset_project_and_claims_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(
                1,
                source_range={"source_file": "source/novel.txt", "start": 0, "end": 10},
                status="consumed",
            )
        ],
        extra={"schema_version": 8},
    )
    derived = project_dir / "source" / "episode_1.txt"
    derived.write_text(SOURCE[:10], encoding="utf-8")
    remaining = project_dir / "source" / "_remaining.txt"
    remaining.write_text("未规划余文", encoding="utf-8")
    _write_script(project_dir, 1)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    _write_claimed_artifact(
        project_dir,
        adapter,
        ArtifactKey.episode_script_plan(1),
        "drafts/episode_1/script_plan_segments.json",
        digest_byte="a",
    )
    project_before = (project_dir / "project.json").read_bytes()
    manifest_before = (project_dir / MANIFEST_FILENAME).read_bytes()
    derived_before = derived.read_bytes()
    remaining_before = remaining.read_bytes()

    def _fail_snapshots(*_args, **_kwargs):
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr("lib.episode.episode_reset.sync_source_snapshots", _fail_snapshots)

    with pytest.raises(RuntimeError, match="snapshot unavailable"):
        reset_episode_planning(project_dir, confirm_consumed=True)

    assert (project_dir / "project.json").read_bytes() == project_before
    assert (project_dir / MANIFEST_FILENAME).read_bytes() == manifest_before
    assert derived.read_bytes() == derived_before
    assert remaining.read_bytes() == remaining_before


def test_a_failed_file_step_restores_symlinked_episode_entry_without_touching_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(
                1,
                source_range={"source_file": "source/novel.txt", "start": 0, "end": 10},
                status="consumed",
            )
        ],
        extra={"schema_version": 8},
    )
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    derived = project_dir / "source" / "episode_1.txt"
    derived.symlink_to(outside)
    _write_script(project_dir, 1)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    _write_claimed_artifact(
        project_dir,
        adapter,
        ArtifactKey.episode_script_plan(1),
        "drafts/episode_1/script_plan_segments.json",
        digest_byte="b",
    )

    def _fail_snapshots(*_args, **_kwargs):
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr("lib.episode.episode_reset.sync_source_snapshots", _fail_snapshots)

    with pytest.raises(RuntimeError, match="snapshot unavailable"):
        reset_episode_planning(project_dir, confirm_consumed=True)

    assert derived.is_symlink()
    assert derived.readlink() == outside
    assert outside.read_bytes() == b"outside"


def test_partial_reset_keeps_the_snapshot_of_a_file_changed_outside(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    snapshot = project_dir / "source" / "snapshots" / "novel.txt"
    snapshot.parent.mkdir()
    snapshot.write_text(SOURCE, encoding="utf-8")
    (project_dir / "source" / "novel.txt").write_text(SOURCE + "外部追加的一段。", encoding="utf-8")

    result = reset_episode_planning(project_dir, episode_id=2)

    assert isinstance(result, EpisodeResetResult)
    assert snapshot.read_text(encoding="utf-8") == SOURCE
