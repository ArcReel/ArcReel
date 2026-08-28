"""``schema_version`` 档位与重复血统的新旧判定。"""

from __future__ import annotations

import pytest

from lib.custom_provider.endpoint_definition import (
    CURRENT_SCHEMA_VERSION,
    SchemaVersionLevel,
    VersionRelation,
    parse_semver,
    schema_version_level,
    version_relation,
)


class TestParseSemver:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1.0.0", (1, 0, 0)),
            ("0.1.12", (0, 1, 12)),
            ("10.20.30", (10, 20, 30)),
        ],
    )
    def test_parses_semver(self, value: str, expected: tuple[int, int, int]):
        assert parse_semver(value) == expected

    @pytest.mark.parametrize("value", ["1.0", "1.0.0-beta", "01.0.0", "v1.0.0", "", None, 1])
    def test_rejects_non_semver(self, value: object):
        assert parse_semver(value) is None


class TestSchemaVersionLevel:
    def test_same_version_imports_directly(self):
        assert schema_version_level(CURRENT_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION) is SchemaVersionLevel.DIRECT

    @pytest.mark.parametrize("file_version", ["1.0.1", "1.1.0", "2.0.0"])
    def test_newer_file_needs_confirmation(self, file_version: str):
        assert schema_version_level(file_version, "1.0.0") is SchemaVersionLevel.CONFIRM

    def test_lower_major_needs_confirmation(self):
        assert schema_version_level("1.9.9", "2.0.0") is SchemaVersionLevel.CONFIRM

    @pytest.mark.parametrize("file_version", ["1.0.0", "1.1.0"])
    def test_older_within_same_major_only_warns(self, file_version: str):
        assert schema_version_level(file_version, "1.2.0") is SchemaVersionLevel.WARNING

    @pytest.mark.parametrize("file_version", [None, "", "1.0", 3])
    def test_unreadable_version_needs_confirmation(self, file_version: object):
        assert schema_version_level(file_version, CURRENT_SCHEMA_VERSION) is SchemaVersionLevel.CONFIRM


class TestVersionRelation:
    def test_existing_newer_than_file(self):
        assert version_relation("0.2.0", "0.1.0") is VersionRelation.NEWER

    def test_existing_older_than_file(self):
        assert version_relation("0.1.0", "0.2.0") is VersionRelation.OLDER

    def test_equal_versions_are_same(self):
        assert version_relation("0.1.0", "0.1.0") is VersionRelation.SAME

    @pytest.mark.parametrize(("existing", "file"), [("bogus", "0.1.0"), ("0.1.0", None)])
    def test_unreadable_version_reports_same(self, existing: object, file: object):
        """判不出新旧时只说重复、不谈方向——凭空猜一个方向会把用户导向错误的处置。"""
        assert version_relation(existing, file) is VersionRelation.SAME
