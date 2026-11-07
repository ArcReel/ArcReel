"""Content-digest cache effects on artifact currency: digests remain content hashes; caching only avoids repeat reads.

Assert external outcomes (artifacts depending on asset sheets become stale / current), except
for one performance contract: unchanged stat hashes a file only once. At the filesystem boundary,
count full reads of artifact content by inode.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import IO, Any

import pytest

from lib.infra.content_digest import reset_file_digest_cache_for_tests
from lib.infra.json_io import atomic_write_json
from lib.project.project_manager import ProjectManager
from lib.workflow.workflow_state import WorkflowStateService
from tests.integration.lib.workflow.test_workflow_state import (
    _make_project,
    _register_produced_artifacts,
    _valid_ad_shot,
    _write_artifact,
)

SHEET = "characters/Alice.png"
STORYBOARD = "storyboards/scene_E1S01.png"


@pytest.fixture(autouse=True)
def fresh_digest_cache() -> None:
    reset_file_digest_cache_for_tests()


def _age(path: Path, *, seconds: int = 3600) -> None:
    """Move mtime far enough into the past: freshly written files are not cached, so age them before observing caching."""

    aged = time.time_ns() - seconds * 1_000_000_000
    os.utime(path, ns=(aged, aged))


def _storyboard_project(tmp_path: Path) -> tuple[ProjectManager, Path]:
    """One character sheet and one referencing storyboard image, both adopted into the artifact manifest with aged mtimes."""

    pm, project_path = _make_project(tmp_path, "ad")
    _write_artifact(project_path, SHEET)
    _write_artifact(project_path, STORYBOARD)
    pm.update_project(
        "demo",
        lambda project: project.update(characters={"Alice": {"description": "red coat", "character_sheet": SHEET}}),
    )
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "title": "广告",
            "content_mode": "ad",
            "shots": [_valid_ad_shot(characters_in_shot=["Alice"], generated_assets={"storyboard_image": STORYBOARD})],
        },
    )
    _register_produced_artifacts(project_path)
    _age(project_path / SHEET)
    _age(project_path / STORYBOARD)
    reset_file_digest_cache_for_tests()
    return pm, project_path


def _storyboard_state(pm: ProjectManager) -> dict[str, list[str]]:
    return WorkflowStateService(pm).get_status("demo").artifacts["storyboards"]


def _count_content_reads(monkeypatch: pytest.MonkeyPatch, *targets: Path) -> dict[str, int]:
    """Count full reads of ``targets`` content at the filesystem boundary: each path open for streaming hashing counts once;
    descriptor reads count only when more than one byte is read (presence probes read one byte, not a hash)."""

    identities = {(target.stat().st_dev, target.stat().st_ino) for target in targets}
    counts = {"content_reads": 0}
    fd_reads: dict[int, int] = {}
    original_path_open = Path.open
    original_os_open = os.open
    original_os_read = os.read
    original_os_close = os.close

    def _is_target(path: Path) -> bool:
        try:
            info = path.stat()
        except OSError:
            return False
        return (info.st_dev, info.st_ino) in identities

    def _counted_path_open(self: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        if _is_target(self):
            counts["content_reads"] += 1
        return original_path_open(self, *args, **kwargs)

    def _counted_os_open(path: object, *args: object, **kwargs: object) -> int:
        fd = original_os_open(path, *args, **kwargs)
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) in identities:
            fd_reads[fd] = 0
        return fd

    def _counted_os_read(fd: int, length: int, /) -> bytes:
        data = original_os_read(fd, length)
        if fd in fd_reads:
            previous = fd_reads[fd]
            fd_reads[fd] = previous + len(data)
            if previous <= 1 < fd_reads[fd]:
                counts["content_reads"] += 1
        return data

    def _counted_os_close(fd: int, /) -> None:
        fd_reads.pop(fd, None)
        original_os_close(fd)

    monkeypatch.setattr(Path, "open", _counted_path_open)
    monkeypatch.setattr(os, "open", _counted_os_open)
    monkeypatch.setattr(os, "read", _counted_os_read)
    monkeypatch.setattr(os, "close", _counted_os_close)
    return counts


@pytest.mark.parametrize("replacement", [b"ARTIFACT", b"a redrawn design sheet"], ids=["same-size", "different-size"])
def test_replaced_design_sheet_stales_the_storyboard_that_uses_it(tmp_path: Path, replacement: bytes) -> None:
    """Cache the old image digest, then replace its content: downstream becomes stale regardless of coincidentally equal sizes."""

    pm, project_path = _storyboard_project(tmp_path)
    assert _storyboard_state(pm)["current_ids"] == ["E1S01"]
    sheet = project_path / SHEET
    assert (len(replacement) == len(sheet.read_bytes())) is (replacement == b"ARTIFACT")

    sheet.write_bytes(replacement)

    state = _storyboard_state(pm)
    assert state["stale_ids"] == ["E1S01"]
    assert state["current_ids"] == []


def test_a_copy_that_changes_the_mtime_keeps_the_storyboard_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Copy/import/restore changes mtime (sometimes inode too): hash again; unchanged content stays current."""

    pm, project_path = _storyboard_project(tmp_path)
    sheet = project_path / SHEET
    assert _storyboard_state(pm)["current_ids"] == ["E1S01"]

    # Change mtime in place.
    _age(sheet, seconds=1800)
    assert _storyboard_state(pm)["current_ids"] == ["E1S01"]

    # Replace with a same-content copy on a new inode.
    duplicate = sheet.with_name("Alice.copy.png")
    shutil.copyfile(sheet, duplicate)
    _age(duplicate, seconds=900)
    duplicate.replace(sheet)
    state = _storyboard_state(pm)
    assert state["current_ids"] == ["E1S01"]
    assert state["stale_ids"] == []


def test_unchanged_stat_hashes_the_design_sheet_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Performance contract: unchanged stat means repeated artifact-status reads fully read the sheet only once; read again only after stat changes."""

    pm, project_path = _storyboard_project(tmp_path)
    sheet = project_path / SHEET
    counts = _count_content_reads(monkeypatch, sheet)

    for _ in range(3):
        assert _storyboard_state(pm)["current_ids"] == ["E1S01"]
    assert counts["content_reads"] == 1

    _age(sheet, seconds=1800)
    assert _storyboard_state(pm)["current_ids"] == ["E1S01"]
    assert counts["content_reads"] == 2
