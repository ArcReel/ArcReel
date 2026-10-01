from __future__ import annotations

import os
import unicodedata
from pathlib import Path

import pytest

from lib.project.source_revision import SourceScope, compute_source_revision


def _project(*whole_source: str, own: tuple[int, ...] = (), cut: tuple[int, ...] = ()) -> dict[str, object]:
    """``whole_source`` 按顺序登记为整本源文；``own`` / ``cut`` 是自带原文与切出集的集 ID。"""
    return {
        "content_mode": "drama",
        "source_language": "zh",
        "whole_source_files": [{"source_file": f"source/{name}"} for name in whole_source],
        "episodes": [{"episode": n, "source_origin": "own"} for n in own]
        + [{"episode": n, "source_origin": "whole_source"} for n in cut],
    }


def test_all_source_revision_is_stable_and_excludes_derived_and_unregistered_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "novel.txt").write_bytes("原文\r\n第二行".encode())
    project = _project("novel.txt", cut=(1,))

    first = compute_source_revision(tmp_path, project, SourceScope(kind="all"))
    (source / "episode_1.txt").write_bytes(b"derived planning output")
    (source / "stray.txt").write_text("没有登记的文件", encoding="utf-8")
    second = compute_source_revision(tmp_path, project, SourceScope(kind="all"))

    assert first.blockers == []
    assert first.files == ["source/novel.txt"]
    assert first.revision is not None
    assert first.revision.startswith("sha256-v1:")
    assert second == first


def test_all_scope_includes_own_source_episode_files(tmp_path: Path) -> None:
    """自带原文的集的集文件就是源文，与整本源文一并计入。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / "episode_2.txt").write_text("第二集", encoding="utf-8")
    (source / "episode_1.txt").write_text("第一集", encoding="utf-8")

    result = compute_source_revision(tmp_path, _project(own=(2, 1)), SourceScope(kind="all"))

    assert result.blockers == []
    assert result.files == ["source/episode_1.txt", "source/episode_2.txt"]
    assert [doc.text for doc in result.documents] == ["第一集", "第二集"]
    assert result.revision is not None


def test_scoped_revision_accepts_own_source_episode_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "episode_1.txt").write_text("第一集", encoding="utf-8")

    result = compute_source_revision(
        tmp_path,
        _project(own=(1,)),
        SourceScope(kind="files", files=["source/episode_1.txt"]),
    )

    assert result.blockers == []
    assert result.files == ["source/episode_1.txt"]


def test_scoped_revision_rejects_cut_episode_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "novel.txt").write_text("原文", encoding="utf-8")
    (source / "episode_1.txt").write_text("derived planning output", encoding="utf-8")

    result = compute_source_revision(
        tmp_path,
        _project("novel.txt", cut=(1,)),
        SourceScope(kind="files", files=["source/episode_1.txt"]),
    )

    assert result.revision is None
    assert result.blockers[0].code == "invalid_source_scope"


def test_scoped_revision_resolves_canonical_unicode_path_to_filesystem_spelling(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    disk_name = unicodedata.normalize("NFD", "truyện.txt")
    (source / disk_name).write_text("nội dung", encoding="utf-8")
    project = _project(disk_name)
    all_result = compute_source_revision(tmp_path, project, SourceScope(kind="all"))
    assert all_result.files == ["source/truyện.txt"]

    scoped_result = compute_source_revision(
        tmp_path,
        project,
        SourceScope(kind="files", files=all_result.files),
    )

    assert scoped_result.blockers == []
    assert scoped_result.revision == all_result.revision
    assert scoped_result.files == ["source/truyện.txt"]


def test_revision_rejects_source_that_is_not_valid_utf8(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "broken.txt").write_bytes(b"\xff\xfe")

    result = compute_source_revision(tmp_path, _project("broken.txt"), SourceScope(kind="all"))

    assert result.revision is None
    assert result.blockers[0].code == "source_unreadable"


def test_revision_changes_with_raw_bytes_path_and_source_semantics(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    original = source / "a.txt"
    original.write_bytes(b"same text\r\n")
    project = _project("a.txt", "b.txt")
    baseline = compute_source_revision(tmp_path, project, SourceScope(kind="all"))

    original.write_bytes(b"same text\n")
    changed_bytes = compute_source_revision(tmp_path, project, SourceScope(kind="all"))
    original.rename(source / "b.txt")
    changed_path = compute_source_revision(tmp_path, project, SourceScope(kind="all"))
    changed_semantics = compute_source_revision(
        tmp_path,
        {
            **project,
            "whole_source_files": [
                {"source_file": f"source/{name}", "source_kind": "screenplay"} for name in ("a.txt", "b.txt")
            ],
        },
        SourceScope(kind="all"),
    )

    assert len({baseline.revision, changed_bytes.revision, changed_path.revision, changed_semantics.revision}) == 4


def test_revision_payload_order_is_stable_across_unicode_filename_spelling(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    nfc_name = "á.txt"
    nfd_name = unicodedata.normalize("NFD", nfc_name)
    accented = source / nfd_name
    accented.write_text("accented", encoding="utf-8")
    (source / "b.txt").write_text("plain", encoding="utf-8")
    before = compute_source_revision(tmp_path, _project(nfd_name, "b.txt"), SourceScope(kind="all"))

    intermediate = source / "rename.tmp"
    accented.rename(intermediate)
    intermediate.rename(source / nfc_name)
    after = compute_source_revision(tmp_path, _project(nfc_name, "b.txt"), SourceScope(kind="all"))

    assert after.revision == before.revision


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


def test_all_scope_reports_candidate_symlink_instead_of_skipping_it(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target.txt"
    target.write_text("outside source", encoding="utf-8")
    (source / "novel.txt").symlink_to(target)

    result = compute_source_revision(tmp_path, _project("novel.txt"), SourceScope(kind="all"))

    assert result.revision is None
    assert [(b.code, b.path) for b in result.blockers] == [("source_symlink", "source/novel.txt")]


def test_all_scope_reports_symlinked_source_dir(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "novel.txt").write_text("outside source", encoding="utf-8")
    project = tmp_path / "demo"
    project.mkdir()
    (project / "source").symlink_to(outside, target_is_directory=True)

    result = compute_source_revision(project, _project(), SourceScope(kind="all"))

    assert result.revision is None
    assert [(b.code, b.path) for b in result.blockers] == [("source_symlink", "source")]


def test_all_scope_reports_registered_symlink_beside_own_source_episode_files(tmp_path: Path) -> None:
    """登记为整本源文的符号链接照常以阻塞项报出，自带原文的集文件不受影响。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / "episode_1.txt").write_text("第一集", encoding="utf-8")
    target = tmp_path / "target.md"
    target.write_text("outside source", encoding="utf-8")
    (source / "linked.md").symlink_to(target)

    result = compute_source_revision(tmp_path, _project("linked.md", own=(1,)), SourceScope(kind="all"))

    assert result.revision is None
    assert [(b.code, b.path) for b in result.blockers] == [("source_symlink", "source/linked.md")]


def test_all_scope_ignores_unregistered_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "episode_1.txt").write_text("第一集", encoding="utf-8")
    target = tmp_path / "target.md"
    target.write_text("outside source", encoding="utf-8")
    (source / "linked.md").symlink_to(target)

    result = compute_source_revision(tmp_path, _project(own=(1,)), SourceScope(kind="all"))

    assert result.blockers == []
    assert result.files == ["source/episode_1.txt"]
