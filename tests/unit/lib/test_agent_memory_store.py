"""Tests for lib.agent_memory_store."""

import pytest

from lib.agent_memory_store import (
    INDEX_FILENAME,
    MAX_FILE_BYTES,
    AgentMemoryStore,
    is_valid_memory_filename,
    parse_memory_frontmatter,
)
from lib.api_errors import BadRequestError, NotFoundError


@pytest.fixture
def store(tmp_path):
    return AgentMemoryStore(tmp_path / "memory")


class TestFilenameRule:
    @pytest.mark.parametrize(
        "filename",
        ["a.md", "MEMORY.md", "note-1.md", "note_1.md", "0.md", "a.b.md", "n" * 97 + ".md"],
    )
    def test_accepts_top_level_markdown_names(self, filename):
        assert is_valid_memory_filename(filename)

    @pytest.mark.parametrize(
        "filename",
        [
            "",
            "notes.txt",
            "notes",
            ".hidden.md",
            "-lead.md",
            "sub/notes.md",
            "..\\notes.md",
            "../notes.md",
            "note s.md",
            "n" * 98 + ".md",
        ],
    )
    def test_rejects_everything_else(self, filename):
        assert not is_valid_memory_filename(filename)


class TestFrontmatter:
    def test_parses_name_description_and_type(self):
        raw = b"---\nname: Tone\ndescription: Prefers short lines\ntype: user\n---\nbody\n"
        assert parse_memory_frontmatter(raw) == {
            "name": "Tone",
            "description": "Prefers short lines",
            "type": "user",
        }

    def test_missing_optional_fields_become_none(self):
        assert parse_memory_frontmatter(b"---\ntype: reference\n---\n") == {
            "name": None,
            "description": None,
            "type": "reference",
        }

    @pytest.mark.parametrize(
        "raw",
        [
            b"plain body without frontmatter\n",
            b"---\nname: [unclosed\n---\n",
            b"---\n- just\n- a list\n---\n",
            b"---\nname: Tone\n",
            b"---\ntype: unknown\n---\n",
            b"---\ntype: 3\n---\n",
            b"---\ntype: user\n---\n\xff\xfe",
        ],
    )
    def test_unusable_metadata_yields_no_tag(self, raw):
        assert parse_memory_frontmatter(raw) is None


class TestOverview:
    def test_missing_directory_reads_as_empty(self, store):
        assert store.overview() == {
            "path": str(store.directory),
            "index": {"exists": False, "line_count": 0, "byte_size": 0, "over_limit": False},
            "files": [],
        }

    def test_lists_only_top_level_markdown_files(self, store):
        store.directory.mkdir(parents=True)
        (store.directory / "notes.md").write_text("body", encoding="utf-8")
        (store.directory / "notes.txt").write_text("body", encoding="utf-8")
        (store.directory / ".hidden.md").write_text("body", encoding="utf-8")
        (store.directory / "nested").mkdir()
        (store.directory / "nested" / "deep.md").write_text("body", encoding="utf-8")

        assert [entry["name"] for entry in store.overview()["files"]] == ["notes.md"]

    def test_index_is_reported_separately_from_files(self, store):
        store.write(INDEX_FILENAME, b"- notes.md\n")
        store.write("notes.md", b"body")

        overview = store.overview()
        assert [entry["name"] for entry in overview["files"]] == ["notes.md"]
        assert overview["index"] == {
            "exists": True,
            "line_count": 1,
            "byte_size": len(b"- notes.md\n"),
            "over_limit": False,
        }

    @pytest.mark.parametrize(
        ("content", "over_limit"),
        [(b"line\n" * 200, False), (b"line\n" * 201, True), (b"x" * 25_001, True)],
    )
    def test_index_over_limit_flag(self, store, content, over_limit):
        store.write(INDEX_FILENAME, content)
        assert store.overview()["index"]["over_limit"] is over_limit

    def test_entry_carries_size_mtime_and_frontmatter(self, store):
        store.write("tone.md", b"---\nname: Tone\ndescription: Short lines\ntype: feedback\n---\nbody\n")

        entry = store.overview()["files"][0]
        assert entry["size"] == len(b"---\nname: Tone\ndescription: Short lines\ntype: feedback\n---\nbody\n")
        assert entry["modified_at"].endswith("+00:00")
        assert entry["frontmatter"] == {"name": "Tone", "description": "Short lines", "type": "feedback"}

    def test_unparsable_frontmatter_leaves_entry_untagged(self, store):
        store.write("tone.md", b"no frontmatter here\n")
        assert store.overview()["files"][0]["frontmatter"] is None

    def test_symlink_escaping_the_directory_is_not_listed(self, store, tmp_path):
        outside = tmp_path / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        store.directory.mkdir(parents=True)
        (store.directory / "leak.md").symlink_to(outside)

        assert store.overview()["files"] == []


class TestReadWriteDelete:
    def test_write_creates_directory_and_upserts(self, store):
        store.write("notes.md", b"first")
        store.write("notes.md", b"second")

        assert store.read("notes.md") == b"second"
        assert [entry["name"] for entry in store.overview()["files"]] == ["notes.md"]

    def test_write_keeps_no_temp_files_behind(self, store):
        store.write("notes.md", b"body")
        assert [path.name for path in store.directory.iterdir()] == ["notes.md"]

    def test_write_accepts_exactly_the_size_limit(self, store):
        store.write("notes.md", b"x" * MAX_FILE_BYTES)
        assert len(store.read("notes.md")) == MAX_FILE_BYTES

    def test_write_rejects_oversized_body(self, store):
        with pytest.raises(BadRequestError) as excinfo:
            store.write("notes.md", b"x" * (MAX_FILE_BYTES + 1))

        assert excinfo.value.key == "memory_file_too_large"
        assert excinfo.value.params == {"filename": "notes.md", "limit_kib": 256}
        assert not (store.directory / "notes.md").exists()

    def test_read_missing_file_reports_not_found(self, store):
        store.write("notes.md", b"body")
        with pytest.raises(NotFoundError) as excinfo:
            store.read("absent.md")

        assert excinfo.value.key == "memory_file_not_found"
        assert excinfo.value.params == {"filename": "absent.md"}

    def test_read_rejects_directory_shaped_entry(self, store):
        (store.directory / "notes.md").mkdir(parents=True)
        with pytest.raises(NotFoundError):
            store.read("notes.md")

    def test_write_rejects_directory_shaped_entry(self, store):
        (store.directory / "notes.md").mkdir(parents=True)
        with pytest.raises(BadRequestError) as excinfo:
            store.write("notes.md", b"body")

        assert excinfo.value.key == "memory_invalid_filename"

    def test_delete_removes_the_file(self, store):
        store.write("notes.md", b"body")
        store.delete("notes.md")

        assert store.overview()["files"] == []

    def test_delete_allows_the_reserved_index(self, store):
        store.write(INDEX_FILENAME, b"- notes.md\n")
        store.delete(INDEX_FILENAME)

        assert store.overview()["index"]["exists"] is False

    def test_delete_missing_file_reports_not_found(self, store):
        store.directory.mkdir(parents=True)
        with pytest.raises(NotFoundError):
            store.delete("absent.md")

    @pytest.mark.parametrize("filename", ["../escape.md", "notes.txt", ".hidden.md", "sub/notes.md"])
    def test_every_entry_point_rejects_illegal_names(self, store, filename):
        for call in (
            lambda: store.read(filename),
            lambda: store.write(filename, b"body"),
            lambda: store.delete(filename),
        ):
            with pytest.raises(BadRequestError) as excinfo:
                call()
            assert excinfo.value.key == "memory_invalid_filename"

    def test_illegal_name_write_does_not_create_the_directory(self, store):
        with pytest.raises(BadRequestError):
            store.write("../escape.md", b"body")

        assert not store.directory.exists()


class TestClear:
    def test_clear_empties_the_directory_without_an_index(self, store):
        store.write("notes.md", b"body")
        store.write(INDEX_FILENAME, b"- notes.md\n")
        (store.directory / "nested").mkdir()
        (store.directory / "nested" / "deep.md").write_text("body", encoding="utf-8")

        store.clear()

        assert store.directory.is_dir()
        assert list(store.directory.iterdir()) == []
        assert store.overview()["index"]["exists"] is False

    def test_clear_creates_a_missing_directory(self, store):
        store.clear()
        assert store.directory.is_dir()
