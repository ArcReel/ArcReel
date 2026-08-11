from __future__ import annotations

import os
import unicodedata
from pathlib import Path

import pytest

from lib.source_revision import SourceScope, compute_source_revision


def _project() -> dict[str, object]:
    return {"source_kind": "novel", "source_language": "zh"}


@pytest.mark.integration
def test_all_source_revision_is_stable_and_excludes_planned_episode_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "novel.txt").write_bytes("原文\r\n第二行".encode())

    first = compute_source_revision(tmp_path, _project(), SourceScope(kind="all"))
    (source / "episode_1.txt").write_bytes(b"derived planning output")
    second = compute_source_revision(tmp_path, _project(), SourceScope(kind="all"))

    assert first.blockers == []
    assert first.files == ["source/novel.txt"]
    assert first.revision is not None
    assert first.revision.startswith("sha256-v1:")
    assert second == first


@pytest.mark.integration
def test_scoped_revision_rejects_planned_episode_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "episode_1.txt").write_text("derived planning output", encoding="utf-8")

    result = compute_source_revision(
        tmp_path,
        _project(),
        SourceScope(kind="files", files=["source/episode_1.txt"]),
    )

    assert result.revision is None
    assert result.blockers[0].code == "invalid_source_scope"


@pytest.mark.integration
def test_scoped_revision_resolves_canonical_unicode_path_to_filesystem_spelling(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    disk_name = unicodedata.normalize("NFD", "truyện.txt")
    (source / disk_name).write_text("nội dung", encoding="utf-8")
    all_result = compute_source_revision(tmp_path, _project(), SourceScope(kind="all"))
    assert all_result.files == ["source/truyện.txt"]

    scoped_result = compute_source_revision(
        tmp_path,
        _project(),
        SourceScope(kind="files", files=all_result.files),
    )

    assert scoped_result.blockers == []
    assert scoped_result.revision == all_result.revision
    assert scoped_result.files == ["source/truyện.txt"]


@pytest.mark.integration
def test_revision_rejects_source_that_is_not_valid_utf8(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "broken.txt").write_bytes(b"\xff\xfe")

    result = compute_source_revision(tmp_path, _project(), SourceScope(kind="all"))

    assert result.revision is None
    assert result.blockers[0].code == "source_unreadable"


@pytest.mark.integration
def test_revision_changes_with_raw_bytes_path_and_source_semantics(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    original = source / "a.txt"
    original.write_bytes(b"same text\r\n")
    baseline = compute_source_revision(tmp_path, _project(), SourceScope(kind="all"))

    original.write_bytes(b"same text\n")
    changed_bytes = compute_source_revision(tmp_path, _project(), SourceScope(kind="all"))
    original.rename(source / "b.txt")
    changed_path = compute_source_revision(tmp_path, _project(), SourceScope(kind="all"))
    changed_semantics = compute_source_revision(
        tmp_path,
        {"source_kind": "screenplay", "source_language": "zh"},
        SourceScope(kind="all"),
    )

    assert len({baseline.revision, changed_bytes.revision, changed_path.revision, changed_semantics.revision}) == 4


@pytest.mark.integration
def test_scoped_revision_rejects_escape_symlink_and_invalid_scope(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (source / "linked.txt").symlink_to(outside)
    try:
        escape = compute_source_revision(
            tmp_path,
            _project(),
            {"kind": "files", "files": ["../outside.txt"]},
        )
        linked = compute_source_revision(
            tmp_path,
            _project(),
            SourceScope(kind="files", files=["source/linked.txt"]),
        )
        malformed = compute_source_revision(tmp_path, _project(), {"kind": "all", "files": ["source/a.txt"]})
    finally:
        outside.unlink()

    assert escape.revision is None
    assert escape.blockers[0].code == "source_path_escape"
    assert linked.blockers[0].code == "source_symlink"
    assert malformed.blockers[0].code == "invalid_source_scope"


@pytest.mark.integration
def test_scoped_revision_rejects_unreadable_file_on_posix(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permission bits are required to make the fixture unreadable")
    source = tmp_path / "source"
    source.mkdir()
    unreadable = source / "unreadable.txt"
    unreadable.write_text("secret", encoding="utf-8")
    os.chmod(unreadable, 0)

    try:
        denied = compute_source_revision(
            tmp_path,
            _project(),
            SourceScope(kind="files", files=["source/unreadable.txt"]),
        )
    finally:
        os.chmod(unreadable, 0o600)

    assert denied.blockers[0].code == "source_unreadable"


@pytest.mark.integration
def test_all_scope_reports_candidate_symlink_instead_of_skipping_it(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target.txt"
    target.write_text("outside source", encoding="utf-8")
    (source / "novel.txt").symlink_to(target)

    result = compute_source_revision(tmp_path, _project(), SourceScope(kind="all"))

    assert result.revision is None
    assert [(b.code, b.path) for b in result.blockers] == [("source_symlink", "source/novel.txt")]
