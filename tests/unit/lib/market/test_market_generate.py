"""索引生成器：从 ``endpoints/<slug>/`` 目录投影出索引，产物必过整体校验。"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from lib.market import INDEX_FILENAME, GenerateError, build_index, check_source, write_index
from tests.factories import custom_endpoint_definition


def _definition(**meta: Any) -> dict[str, Any]:
    definition = custom_endpoint_definition()
    definition["meta"] = {"name": "演示视频", "author": "ArcReel", "version": "1.0.0", **meta}
    return definition


def _add(root: Path, slug: str, definition: object | None = None, **files: bytes) -> Path:
    directory = root / "endpoints" / slug
    directory.mkdir(parents=True)
    payload = _definition() if definition is None else definition
    (directory / "definition.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    for name, data in files.items():
        (directory / name.replace("_", ".")).write_bytes(data)
    return directory


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 32)).save(buffer, format="PNG")
    return buffer.getvalue()


class TestRoundTrip:
    def test_generated_index_passes_check(self, tmp_path: Path):
        _add(tmp_path, "zeta", _definition(name="Zeta", description="说明", min_app_version="0.30.0"), icon_png=_png())
        _add(tmp_path, "alpha", _definition(name="Alpha", homepage="https://example.com"))
        _add(tmp_path, "svg-one", icon_svg=b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"/>')

        write_index(tmp_path, build_index(tmp_path))

        assert check_source(tmp_path) == []

    def test_entries_project_meta_and_discover_icons(self, tmp_path: Path):
        _add(tmp_path, "zeta", _definition(name="Zeta", description="说明", min_app_version="0.30.0"), icon_png=_png())
        _add(tmp_path, "alpha", _definition(name="Alpha"))

        index = build_index(tmp_path)

        assert index["entries"] == [
            {
                "type": "endpoint",
                "slug": "alpha",
                "path": "endpoints/alpha/definition.json",
                "name": "Alpha",
                "author": "ArcReel",
                "version": "1.0.0",
                "media_type": "video",
            },
            {
                "type": "endpoint",
                "slug": "zeta",
                "path": "endpoints/zeta/definition.json",
                "name": "Zeta",
                "author": "ArcReel",
                "version": "1.0.0",
                "description": "说明",
                "min_app_version": "0.30.0",
                "media_type": "video",
                "icon": "endpoints/zeta/icon.png",
            },
        ]

    def test_stray_files_beside_entry_directories_are_ignored(self, tmp_path: Path):
        (tmp_path / "endpoints").mkdir()
        (tmp_path / "endpoints" / ".gitkeep").write_text("", encoding="utf-8")

        assert build_index(tmp_path)["entries"] == []

    def test_symlinked_index_is_replaced_by_a_regular_file(self, tmp_path: Path):
        stale = tmp_path / "docs" / "index.json"
        stale.parent.mkdir()
        stale.write_text("{}", encoding="utf-8")
        (tmp_path / INDEX_FILENAME).symlink_to(stale)

        write_index(tmp_path, build_index(tmp_path, name="空市场"))

        assert not (tmp_path / INDEX_FILENAME).is_symlink()
        assert stale.read_text(encoding="utf-8") == "{}"
        assert check_source(tmp_path) == []

    def test_source_without_endpoints_directory_yields_an_empty_index(self, tmp_path: Path):
        write_index(tmp_path, build_index(tmp_path, name="空市场"))

        assert json.loads((tmp_path / INDEX_FILENAME).read_text(encoding="utf-8")) == {
            "schema_version": "1.0.0",
            "name": "空市场",
            "entries": [],
        }
        assert check_source(tmp_path) == []


class TestTopLevelFields:
    def test_existing_index_header_is_kept(self, tmp_path: Path):
        header = {"name": "官方市场", "description": "说明", "homepage": "https://github.com/ArcReel/arcreel-market"}
        (tmp_path / INDEX_FILENAME).write_text(json.dumps({**header, "entries": "stale"}), encoding="utf-8")

        index = build_index(tmp_path)

        assert {field: index[field] for field in header} == header

    def test_arguments_override_the_existing_header(self, tmp_path: Path):
        (tmp_path / INDEX_FILENAME).write_text(
            json.dumps({"name": "旧名", "homepage": "https://a.example"}), encoding="utf-8"
        )

        index = build_index(tmp_path, name="新名", homepage="https://b.example")

        assert (index["name"], index["homepage"]) == ("新名", "https://b.example")

    def test_name_defaults_to_the_directory_name(self, tmp_path: Path):
        source = tmp_path / "my-market"
        source.mkdir()

        assert build_index(source)["name"] == "my-market"

    @pytest.mark.parametrize("existing", [None, "{", json.dumps({"name": 42, "entries": []}), json.dumps([])])
    def test_default_name_fills_in_when_no_existing_name_is_readable(self, tmp_path: Path, existing: str | None):
        if existing is not None:
            (tmp_path / INDEX_FILENAME).write_text(existing, encoding="utf-8")

        assert build_index(tmp_path, default_name="arcreel-market")["name"] == "arcreel-market"

    def test_symlinked_index_header_is_not_read(self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory):
        outside = tmp_path_factory.mktemp("outside") / INDEX_FILENAME
        outside.write_text(json.dumps({"name": "外部文件里的名字", "entries": []}), encoding="utf-8")
        (tmp_path / INDEX_FILENAME).symlink_to(outside)

        assert build_index(tmp_path, default_name="arcreel-market")["name"] == "arcreel-market"

    def test_default_name_does_not_replace_the_existing_name(self, tmp_path: Path):
        (tmp_path / INDEX_FILENAME).write_text(json.dumps({"name": "官方市场", "entries": []}), encoding="utf-8")

        assert build_index(tmp_path, default_name="arcreel-market")["name"] == "官方市场"


class TestGenerateErrors:
    def _codes(self, root: Path) -> list[tuple[str, str]]:
        with pytest.raises(GenerateError) as excinfo:
            build_index(root)
        return [(issue.file, issue.code.value) for issue in excinfo.value.issues]

    def test_directory_without_definition_is_reported(self, tmp_path: Path):
        (tmp_path / "endpoints" / "demo").mkdir(parents=True)

        assert self._codes(tmp_path) == [("endpoints/demo/definition.json", "file_missing")]

    @pytest.mark.parametrize("content", ["{", "[]", '{"meta": "x"}'])
    def test_definition_without_readable_meta_is_reported(self, tmp_path: Path, content: str):
        directory = _add(tmp_path, "demo")
        (directory / "definition.json").write_text(content, encoding="utf-8")

        assert self._codes(tmp_path) == [("endpoints/demo/definition.json", "definition_unreadable")]

    def test_more_than_one_icon_is_ambiguous(self, tmp_path: Path):
        _add(tmp_path, "demo", icon_png=_png(), icon_svg=b"<svg/>")

        assert self._codes(tmp_path) == [("endpoints/demo", "icon_ambiguous")]

    def test_symlinked_definition_is_rejected(self, tmp_path: Path):
        directory = _add(tmp_path, "demo")
        (directory / "definition.json").rename(directory / "real.json")
        (directory / "definition.json").symlink_to("real.json")

        assert self._codes(tmp_path) == [("endpoints/demo/definition.json", "symlink_not_allowed")]

    @pytest.mark.parametrize("dangling", [False, True])
    def test_symlinked_icon_is_rejected(self, tmp_path: Path, dangling: bool):
        directory = _add(tmp_path, "demo", real_png=_png())
        (directory / "icon.png").symlink_to("missing.png" if dangling else "real.png")

        assert self._codes(tmp_path) == [("endpoints/demo/icon.png", "symlink_not_allowed")]

    @pytest.mark.parametrize("target", ["real", "missing", "demo"])
    def test_symlinked_entry_directory_is_rejected(self, tmp_path: Path, target: str):
        _add(tmp_path, "real")
        (tmp_path / "endpoints" / "demo").symlink_to(target, target_is_directory=True)

        assert self._codes(tmp_path) == [("endpoints/demo", "symlink_not_allowed")]

    def test_symlinked_endpoints_directory_is_rejected(self, tmp_path: Path):
        _add(tmp_path / "elsewhere", "demo")
        (tmp_path / "endpoints").symlink_to("elsewhere/endpoints", target_is_directory=True)

        assert self._codes(tmp_path) == [("endpoints", "symlink_not_allowed")]
