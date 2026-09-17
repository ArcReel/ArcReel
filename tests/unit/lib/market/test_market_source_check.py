"""市场源目录的整体校验：六条规则各自给出定位到文件与字段的诊断。"""

from __future__ import annotations

import json
import random
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from lib.market import INDEX_FILENAME, check_source
from tests.factories import custom_endpoint_definition


def _png(width: int = 64, height: int = 64, fmt: str = "PNG") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (200, 80, 40)).save(buffer, format=fmt)
    return buffer.getvalue()


def _png_with_bad_crc() -> bytes:
    data = bytearray(_png())
    chunk = data.find(b"IDAT")
    length = int.from_bytes(data[chunk - 4 : chunk], "big")
    data[chunk + 4 + length] ^= 0xFF
    return bytes(data)


def _png_with_empty_ihdr() -> bytes:
    data = bytearray(_png())
    data[8:12] = (0).to_bytes(4, "big")
    return bytes(data)


def _svg(attributes: str) -> bytes:
    return f'<svg xmlns="http://www.w3.org/2000/svg" {attributes}><rect width="1" height="1"/></svg>'.encode()


def _svg_with_dtd(encoding: str) -> bytes:
    document = (
        f'<?xml version="1.0" encoding="{encoding}"?><!DOCTYPE svg [<!ENTITY a "b">]>'
        '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><title>&a;</title></svg>'
    )
    return document.encode(encoding)


def _definition(**meta: Any) -> dict[str, Any]:
    definition = custom_endpoint_definition()
    definition["meta"] = {"name": "演示视频", "author": "ArcReel", "version": "1.0.0", **meta}
    return definition


def _entry_for(slug: str, definition: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    meta = definition["meta"]
    entry: dict[str, Any] = {
        "type": "endpoint",
        "slug": slug,
        "path": f"endpoints/{slug}/definition.json",
        **{
            field: meta[field]
            for field in ("name", "author", "version", "description", "homepage", "min_app_version")
            if field in meta
        },
        "media_type": "video",
    }
    entry.update(overrides)
    return entry


class _Source:
    """在临时目录里搭一个市场源：逐条写入定义与旁置文件，最后写索引。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.entries: list[dict[str, Any]] = []

    def add(
        self, slug: str, definition: object | None = None, *, icon: tuple[str, bytes] | None = None, **overrides: Any
    ):
        definition = _definition() if definition is None else definition
        directory = self.root / "endpoints" / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "definition.json").write_text(json.dumps(definition, ensure_ascii=False), encoding="utf-8")
        extra: dict[str, Any] = {}
        if icon is not None:
            name, data = icon
            (directory / name).write_bytes(data)
            extra["icon"] = f"endpoints/{slug}/{name}"
        base = (
            definition if isinstance(definition, dict) and isinstance(definition.get("meta"), dict) else _definition()
        )
        self.entries.append(_entry_for(slug, base, **extra, **overrides))
        return self

    def write(self, **overrides: Any) -> Path:
        index = {"schema_version": "1.0.0", "name": "测试市场", "entries": self.entries, **overrides}
        (self.root / INDEX_FILENAME).write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        return self.root


@pytest.fixture
def source(tmp_path: Path) -> _Source:
    return _Source(tmp_path)


def _codes(root: Path) -> list[tuple[str, str, str]]:
    return [(issue.file, issue.path, issue.code.value) for issue in check_source(root)]


class TestCompliantSource:
    def test_source_with_every_icon_format_passes(self, source: _Source):
        source.add("png-icon", icon=("icon.png", _png()))
        source.add("webp-icon", icon=("icon.webp", _png(fmt="WEBP")))
        source.add("svg-icon", icon=("icon.svg", _svg('viewBox="0 0 24 24"')))
        source.add(
            "full-meta", _definition(description="说明", homepage="https://example.com", min_app_version="0.30.0")
        )

        assert check_source(source.write()) == []

    def test_empty_source_passes(self, source: _Source):
        assert check_source(source.write()) == []

    def test_unknown_entry_types_are_not_checked(self, source: _Source):
        source.entries.append({"type": "prompt_template", "slug": "Not A Slug"})

        assert check_source(source.write()) == []


class TestIndexRule:
    def test_missing_index_is_reported(self, tmp_path: Path):
        assert _codes(tmp_path) == [(INDEX_FILENAME, "$", "index_unreadable")]

    def test_malformed_json_is_reported(self, tmp_path: Path):
        (tmp_path / INDEX_FILENAME).write_text("{not json", encoding="utf-8")

        assert _codes(tmp_path) == [(INDEX_FILENAME, "$", "index_unreadable")]

    def test_deeply_nested_index_is_reported(self, tmp_path: Path):
        nested = "[" * 100_000 + "]" * 100_000
        (tmp_path / INDEX_FILENAME).write_text(
            f'{{"schema_version": "1.0.0", "name": "x", "entries": [], "extra": {nested}}}', encoding="utf-8"
        )

        assert _codes(tmp_path) == [(INDEX_FILENAME, "$", "index_unreadable")]

    def test_symlinked_index_is_not_followed(self, source: _Source, tmp_path_factory: pytest.TempPathFactory):
        outside = tmp_path_factory.mktemp("outside") / INDEX_FILENAME
        outside.write_text(json.dumps({"schema_version": "1.0.0", "name": "外部", "entries": []}), encoding="utf-8")
        (source.root / INDEX_FILENAME).symlink_to(outside)

        assert _codes(source.root) == [(INDEX_FILENAME, "$", "index_unreadable")]

    def test_higher_major_schema_is_reported(self, source: _Source):
        assert _codes(source.write(schema_version="2.0.0")) == [
            (INDEX_FILENAME, "schema_version", "unsupported_schema_version")
        ]

    def test_schema_violations_stop_further_checks(self, source: _Source):
        """结构不成立时逐文件的规则没有可靠前提，只报结构诊断。"""
        source.add("demo", {"broken": True}, min_app_version="0.31")

        assert _codes(source.write()) == [(INDEX_FILENAME, "entries[0].min_app_version", "min_app_version_invalid")]


class TestSlugRule:
    def test_duplicate_slug_is_reported_on_the_later_entry(self, source: _Source):
        source.add("demo").add("demo")

        assert _codes(source.write()) == [(INDEX_FILENAME, "entries[1].slug", "slug_duplicate")]

    def test_slug_must_equal_its_directory(self, source: _Source):
        source.add("demo")
        source.entries[0]["slug"] = "other"

        assert _codes(source.write()) == [(INDEX_FILENAME, "entries[0].slug", "slug_directory_mismatch")]


class TestReferencedFilesRule:
    def test_missing_definition_file_is_reported(self, source: _Source):
        source.add("demo", path="endpoints/demo/missing.json")

        assert _codes(source.write()) == [(INDEX_FILENAME, "entries[0].path", "file_missing")]

    def test_symlink_escaping_the_source_is_rejected(self, source: _Source, tmp_path_factory: pytest.TempPathFactory):
        outside = tmp_path_factory.mktemp("outside") / "definition.json"
        outside.write_text(json.dumps(_definition()), encoding="utf-8")
        source.add("demo")
        target = source.root / "endpoints" / "demo" / "definition.json"
        target.unlink()
        target.symlink_to(outside)

        assert _codes(source.write()) == [(INDEX_FILENAME, "entries[0].path", "symlink_not_allowed")]

    def test_symlink_loop_is_rejected(self, source: _Source):
        source.add("demo")
        target = source.root / "endpoints" / "demo" / "definition.json"
        target.unlink()
        target.symlink_to(target)

        assert _codes(source.write()) == [(INDEX_FILENAME, "entries[0].path", "symlink_not_allowed")]

    def test_symlinked_definition_inside_the_source_is_rejected(self, source: _Source):
        """GitHub raw 对符号链接返回链接目标路径文本，客户端抓不到定义内容。"""
        source.add("demo")
        target = source.root / "endpoints" / "demo" / "definition.json"
        real = target.with_name("real.json")
        target.rename(real)
        target.symlink_to("real.json")

        issues = check_source(source.write())

        assert [(issue.path, issue.code.value, dict(issue.params)) for issue in issues] == [
            ("entries[0].path", "symlink_not_allowed", {"value": "endpoints/demo/definition.json"})
        ]

    def test_symlinked_icon_inside_the_source_is_rejected(self, source: _Source):
        source.add("demo", icon=("real.png", _png()))
        (source.root / "endpoints" / "demo" / "icon.png").symlink_to("real.png")
        source.entries[0]["icon"] = "endpoints/demo/icon.png"

        assert _codes(source.write()) == [(INDEX_FILENAME, "entries[0].icon", "symlink_not_allowed")]

    def test_symlinked_entry_directory_is_rejected(self, source: _Source):
        source.add("real")
        (source.root / "endpoints" / "demo").symlink_to("real", target_is_directory=True)
        source.entries[0] = _entry_for("demo", _definition())

        issues = check_source(source.write())

        assert [(issue.path, issue.code.value, dict(issue.params)) for issue in issues] == [
            ("entries[0].path", "symlink_not_allowed", {"value": "endpoints/demo"})
        ]

    def test_missing_icon_is_reported(self, source: _Source):
        source.add("demo")
        source.entries[0]["icon"] = "endpoints/demo/icon.png"

        assert _codes(source.write()) == [(INDEX_FILENAME, "entries[0].icon", "file_missing")]

    def test_oversized_icon_is_rejected(self, source: _Source):
        noisy = Image.frombytes("RGB", (256, 256), random.Random(0).randbytes(256 * 256 * 3))
        buffer = BytesIO()
        noisy.save(buffer, format="PNG")
        assert len(buffer.getvalue()) > 64 * 1024
        source.add("demo", icon=("icon.png", buffer.getvalue()))

        assert _codes(source.write()) == [("endpoints/demo/icon.png", "$", "icon_too_large")]

    def test_decompression_bomb_icon_is_rejected(self, source: _Source, monkeypatch: pytest.MonkeyPatch):
        data = _png()
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)
        source.add("demo", icon=("icon.png", data))

        assert _codes(source.write()) == [("endpoints/demo/icon.png", "$", "icon_format_invalid")]

    @pytest.mark.parametrize(
        ("name", "data"),
        [
            ("icon.png", _png(64, 32)),
            ("icon.svg", _svg('width="48" height="24"')),
            ("icon.svg", _svg('viewBox="0 0 48 24"')),
        ],
    )
    def test_non_square_icon_is_rejected(self, source: _Source, name: str, data: bytes):
        source.add("demo", icon=(name, data))

        assert _codes(source.write()) == [(f"endpoints/demo/{name}", "$", "icon_not_square")]

    @pytest.mark.parametrize(
        ("name", "data"),
        [
            ("icon.gif", _png(fmt="GIF")),
            ("icon.png", _png(fmt="WEBP")),
            ("icon.png", b"not an image"),
            ("icon.png", _png()[:50]),
            ("icon.png", _png_with_bad_crc()),
            ("icon.png", _png_with_empty_ihdr()),
            ("icon.png", b"P6 " + b"1" * 100),
            ("icon.svg", _svg('viewBox="0 0 inf inf"')),
            ("icon.svg", _svg('width="0" height="0"')),
            ("icon.svg", _svg(f'width="{"9" * 400}" height="{"9" * 400}"')),
            ("icon.svg", _svg_with_dtd("utf-16")),
            ("icon.svg", _svg_with_dtd("utf-8")),
            ("icon.svg", b"<html></html>"),
            ("icon.svg", _svg("")),
        ],
    )
    def test_icon_must_be_a_readable_png_webp_or_svg(self, source: _Source, name: str, data: bytes):
        source.add("demo", icon=(name, data))

        assert _codes(source.write()) == [(f"endpoints/demo/{name}", "$", "icon_format_invalid")]


class TestDefinitionRule:
    def test_unparsable_definition_is_reported(self, source: _Source):
        source.add("demo")
        (source.root / "endpoints" / "demo" / "definition.json").write_text("{", encoding="utf-8")

        assert _codes(source.write()) == [("endpoints/demo/definition.json", "$", "definition_unreadable")]

    def test_definition_errors_carry_their_own_code_and_location(self, source: _Source):
        definition = _definition()
        definition["status_map"]["pending"] = "expired"
        source.add("demo", definition)

        [issue] = check_source(source.write())

        assert (issue.file, issue.path, issue.code.value, issue.params["code"]) == (
            "endpoints/demo/definition.json",
            "status_map.pending",
            "definition_invalid",
            "status_map_target_invalid",
        )


class TestProjectionRule:
    def test_index_field_differing_from_meta_is_reported(self, source: _Source):
        source.add("demo", version="1.0.1")

        assert _codes(source.write()) == [(INDEX_FILENAME, "entries[0].version", "projection_mismatch")]

    def test_meta_field_absent_from_index_is_reported(self, source: _Source):
        source.add("demo", _definition(min_app_version="0.30.0"))
        del source.entries[0]["min_app_version"]

        assert _codes(source.write()) == [(INDEX_FILENAME, "entries[0].min_app_version", "projection_mismatch")]

    def test_media_type_is_fixed_to_video(self, source: _Source):
        source.add("demo", media_type="image")

        assert _codes(source.write()) == [(INDEX_FILENAME, "entries[0].media_type", "projection_mismatch")]
