"""市场源索引的读取口径：过索引 schema 才可用，旧客户端忽略未知可选字段与未知条目类型。"""

from __future__ import annotations

from typing import Any

import pytest

from lib.market import (
    INDEX_SCHEMA_VERSION,
    InvalidIndexError,
    MarketIssueCode,
    UnsupportedIndexSchemaError,
    parse_index,
)


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": "endpoint",
        "slug": "demo-video",
        "path": "endpoints/demo-video/definition.json",
        "name": "演示视频",
        "author": "ArcReel",
        "version": "1.0.0",
        "media_type": "video",
    }
    entry.update(overrides)
    return entry


def _index(*entries: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    index: dict[str, Any] = {"schema_version": "1.0.0", "name": "官方市场", "entries": list(entries)}
    index.update(overrides)
    return index


def _issues(document: object) -> list[tuple[str, str]]:
    with pytest.raises(InvalidIndexError) as excinfo:
        parse_index(document)
    return [(issue.path, issue.code.value) for issue in excinfo.value.issues]


class TestAcceptedIndexes:
    def test_full_entry_is_projected(self):
        index = parse_index(
            _index(
                _entry(
                    description="一句话",
                    homepage="https://example.com/demo",
                    icon="endpoints/demo-video/icon.png",
                    min_app_version="0.31.0",
                ),
                description="源描述",
                homepage="https://github.com/ArcReel/arcreel-market",
            )
        )

        assert (index.name, index.description, index.homepage) == (
            "官方市场",
            "源描述",
            "https://github.com/ArcReel/arcreel-market",
        )
        [entry] = index.entries
        assert (entry.slug, entry.icon, entry.min_app_version, entry.media_type) == (
            "demo-video",
            "endpoints/demo-video/icon.png",
            "0.31.0",
            "video",
        )

    def test_empty_index_is_valid(self):
        assert parse_index(_index()).entries == ()

    def test_unknown_optional_fields_are_ignored(self):
        index = parse_index(_index(_entry(tags=["video"], downloads=3), maintainer="someone"))

        assert [entry.slug for entry in index.entries] == ["demo-video"]

    def test_unknown_entry_types_are_skipped_silently(self):
        index = parse_index(_index({"type": "prompt_template", "anything": 1}, _entry()))

        assert [entry.type for entry in index.entries] == ["endpoint"]

    def test_newer_minor_schema_version_is_read(self):
        assert parse_index(_index(_entry(), schema_version="1.7.0")).entries


class TestUnsupportedSchema:
    def test_higher_major_is_rejected_as_a_whole(self):
        with pytest.raises(UnsupportedIndexSchemaError) as excinfo:
            parse_index(_index(_entry(), schema_version="2.0.0"))

        assert excinfo.value.version == "2.0.0"
        assert excinfo.value.supported == INDEX_SCHEMA_VERSION

    def test_higher_major_wins_over_otherwise_broken_content(self):
        """新主版本的形状旧客户端本就判不了，一律报不支持而非索引无效。"""
        with pytest.raises(UnsupportedIndexSchemaError):
            parse_index({"schema_version": "2.0.0", "entries": "reshaped"})


class TestInvalidIndexes:
    def test_non_object_is_invalid(self):
        assert [code for _, code in _issues([])] == [MarketIssueCode.INVALID_TYPE.value]

    def test_missing_top_level_fields_are_named(self):
        with pytest.raises(InvalidIndexError) as excinfo:
            parse_index({"entries": []})

        assert sorted(issue.params["field"] for issue in excinfo.value.issues) == ["name", "schema_version"]

    def test_missing_entry_field_points_at_the_entry(self):
        entry = _entry()
        del entry["author"]

        assert _issues(_index(entry)) == [("entries[0]", "missing_field")]

    @pytest.mark.parametrize("slug", ["Demo", "-demo", "demo_video", "demo\n", "a" * 65, ""])
    def test_malformed_slug_is_invalid(self, slug: str):
        assert _issues(_index(_entry(slug=slug))) == [("entries[0].slug", "slug_invalid")]

    @pytest.mark.parametrize(
        "path",
        [
            "/endpoints/demo/definition.json",
            "https://example.com/definition.json",
            "file:definition.json",
            "endpoints/../secrets/definition.json",
            "..\\definition.json",
            "endpoints/demo/definition.json\n",
        ],
    )
    @pytest.mark.parametrize("field", ["path", "icon"])
    def test_asset_references_must_stay_inside_the_source(self, field: str, path: str):
        assert _issues(_index(_entry(**{field: path}))) == [(f"entries[0].{field}", "path_not_relative")]

    @pytest.mark.parametrize("value", ["0.31", "v0.31.0", "0.31.0-rc1", "0.31.0\n"])
    def test_min_app_version_must_be_semver(self, value: str):
        assert _issues(_index(_entry(min_app_version=value))) == [
            ("entries[0].min_app_version", "min_app_version_invalid")
        ]

    def test_all_entries_are_judged_together(self):
        """任一条目不合规即整份无效，诊断逐条给出。"""
        issues = _issues(_index(_entry(), _entry(slug="Bad"), _entry(version="x")))

        assert issues == [("entries[1].slug", "slug_invalid"), ("entries[2].version", "invalid_value")]

    def test_too_many_entries_is_invalid(self):
        entries = [_entry(slug=f"e{i}") for i in range(1001)]

        assert _issues(_index(*entries)) == [("entries", "invalid_value")]
