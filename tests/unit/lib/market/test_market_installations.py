"""安装记录摘要是持久化契约；市场轴状态与本地修改轴各自独立判定。"""

import hashlib
from typing import Any

import pytest

from lib.db.models.market_source import MarketSource
from lib.market.entry import project_meta
from lib.market.index import MarketIndexEntry
from lib.market.installations import InstallationState, available_entries, definition_digest, installation_status
from tests.factories import custom_endpoint_definition


def test_digest_ignores_key_order_and_hashes_compact_unescaped_utf8():
    reordered = {"meta": {"name": "示例", "author": "A"}, "kind": "declarative"}
    original = {"kind": "declarative", "meta": {"author": "A", "name": "示例"}}

    expected = hashlib.sha256('{"kind":"declarative","meta":{"author":"A","name":"示例"}}'.encode()).hexdigest()
    assert definition_digest(reordered) == definition_digest(original) == expected


def _entry(version: str) -> MarketIndexEntry:
    return MarketIndexEntry(
        type="endpoint",
        slug="example",
        path="endpoints/example/definition.json",
        name="示例端点",
        author="ArcReel",
        version=version,
        media_type="video",
    )


def _source(*, is_enabled: bool = True, entries: list[dict[str, Any]] | None = None) -> MarketSource:
    definition = custom_endpoint_definition()
    if entries is None:
        entries = [{"type": "endpoint", "slug": "example", "path": "endpoints/example/definition.json"}]
        entries[0].update(project_meta(definition))
    return MarketSource(
        id=1,
        is_enabled=is_enabled,
        cached_index={"schema_version": "1.0.0", "name": "Market", "entries": entries},
    )


def _status(definition: dict[str, Any], entry: MarketIndexEntry | None, *, installed: dict[str, Any] | None = None):
    installed = installed or custom_endpoint_definition()
    return installation_status(
        installed_version=installed["meta"]["version"],
        installed_digest=definition_digest(installed),
        definition=definition,
        entry=entry,
    )


def test_local_version_bump_is_modified_but_not_update_available():
    edited = custom_endpoint_definition()
    edited["meta"]["version"] = "9.9.9"

    status = _status(edited, _entry("0.1.0"))

    assert status.state is InstallationState.CURRENT
    assert status.modified is True


def test_same_version_with_changed_market_content_is_not_update_available():
    assert _status(custom_endpoint_definition(), _entry("0.1.0")).state is InstallationState.CURRENT


@pytest.mark.parametrize("index_version", ["0.2.0", "0.0.9", "0.1.0-rc.1"])
def test_any_differing_index_version_is_update_available(index_version: str):
    status = _status(custom_endpoint_definition(), _entry(index_version))

    assert status.state is InstallationState.UPDATE_AVAILABLE
    assert status.modified is False


def test_market_axis_and_modified_axis_are_orthogonal():
    edited = custom_endpoint_definition()
    edited["submit"]["url"] = "{{ base_url }}/v2/video/create"

    assert _status(edited, _entry("0.2.0")) == (InstallationState.UPDATE_AVAILABLE, True)
    assert _status(edited, None) == (InstallationState.UNAVAILABLE, True)
    assert _status(custom_endpoint_definition(), None) == (InstallationState.UNAVAILABLE, False)


def test_available_entries_require_enabled_existing_source_that_still_lists_the_slug():
    assert available_entries(_source())["example"].version == "0.1.0"
    assert available_entries(_source(is_enabled=False)) == {}
    assert available_entries(None) == {}
    assert available_entries(_source(entries=[])) == {}
    assert available_entries(MarketSource(id=1, is_enabled=True, cached_index=None)) == {}
