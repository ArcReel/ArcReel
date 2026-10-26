"""Reference upgrades keep provenance without replaying every previous edition."""

from pathlib import Path

import pytest

from lib.project.reference_library import (
    current_reference_context,
    publish_reference_context,
    register_pdf_reference,
)


def pdf(path: Path, body: str) -> Path:
    path.write_bytes(b"%PDF-1.7\n" + body.encode())
    return path


def test_new_edition_requires_new_map_and_only_latest_map_is_loaded(tmp_path: Path) -> None:
    project = tmp_path / "project"
    old = register_pdf_reference(project, "world", pdf(tmp_path / "old.pdf", "old facts"))
    publish_reference_context(project, "world", expected_revision=old.latest, context="Old facts p1")
    new = register_pdf_reference(project, "world", pdf(tmp_path / "new.pdf", "new facts"))
    with pytest.raises(ValueError, match="needs a consolidated map"):
        current_reference_context(project)
    with pytest.raises(ValueError, match="Reference changed"):
        publish_reference_context(project, "world", expected_revision=old.latest, context="Stale map")
    publish_reference_context(project, "world", expected_revision=new.latest, context="New facts p2")
    loaded = current_reference_context(project)
    assert len(loaded) == 1
    assert loaded[0]["context"] == "New facts p2"
    assert loaded[0]["source_sha256"] == new.latest
    assert len(new.revisions) == 2
    archive = project / "source/reference-library/world" / old.latest
    assert (archive / "original.pdf").read_bytes().endswith(b"old facts")
    assert len(list(archive.glob("context-*.md"))) == 1


def test_duplicate_import_and_older_reimport_do_not_add_context_or_roll_back(tmp_path: Path) -> None:
    project = tmp_path / "project"
    old_pdf = pdf(tmp_path / "old.pdf", "old")
    register_pdf_reference(project, "world", old_pdf)
    newest = pdf(tmp_path / "new.pdf", "new")
    new = register_pdf_reference(project, "world", newest)
    active = publish_reference_context(project, "world", expected_revision=new.latest, context="Current p2")
    alias = pdf(tmp_path / "renamed.pdf", "new")
    assert register_pdf_reference(project, "world", alias) == active
    assert register_pdf_reference(project, "world", old_pdf) == active
    assert current_reference_context(project)[0]["context"] == "Current p2"


def test_context_revisions_are_retained_but_only_one_is_loaded(tmp_path: Path) -> None:
    project = tmp_path / "project"
    doc = register_pdf_reference(project, "world", pdf(tmp_path / "world.pdf", "facts"))
    publish_reference_context(project, "world", expected_revision=doc.latest, context="Initial map p1")
    publish_reference_context(project, "world", expected_revision=doc.latest, context="Corrected map p1")
    assert current_reference_context(project)[0]["context"] == "Corrected map p1"
    directory = project / "source/reference-library/world" / doc.latest
    assert len(list(directory.glob("context-*.md"))) == 2


def test_references_are_project_scoped_and_optional(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    doc = register_pdf_reference(first, "world", pdf(tmp_path / "world.pdf", "facts"))
    publish_reference_context(first, "world", expected_revision=doc.latest, context="Only first project p1")
    assert current_reference_context(second) == []
    assert not second.exists()


def test_context_integrity_and_reference_path_bounds(tmp_path: Path) -> None:
    source = pdf(tmp_path / "world.pdf", "facts")
    with pytest.raises(ValueError, match="String should match pattern"):
        register_pdf_reference(tmp_path / "project", "../outside", source)
    doc = register_pdf_reference(tmp_path / "project", "world", source)
    active = publish_reference_context(
        tmp_path / "project", "world", expected_revision=doc.latest, context="Source facts p1"
    )
    directory = tmp_path / "project/source/reference-library/world" / doc.latest
    (directory / f"context-{active.context_digest}.md").write_text("Changed without registration")
    with pytest.raises(ValueError, match="outside its version history"):
        current_reference_context(tmp_path / "project")


def test_total_context_is_bounded_and_absent_context_preserves_prompts(tmp_path: Path) -> None:
    from lib.project.reference_library import MAX_CONTEXT_BYTES, append_reference_context

    assert append_reference_context("Existing prompt", []) == "Existing prompt"
    for name in ("first", "second"):
        doc = register_pdf_reference(tmp_path, name, pdf(tmp_path / f"{name}.pdf", name))
        publish_reference_context(
            tmp_path, name, expected_revision=doc.latest, context="a" * (MAX_CONTEXT_BYTES // 2 + 1)
        )
    with pytest.raises(ValueError, match="context budget"):
        current_reference_context(tmp_path)
