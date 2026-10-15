"""目录合成：官方数据取较新的一份，用户副本整条替换官方条目（docs/adr/0099）。"""

from dataclasses import replace

from arcreel_catalog_core import CatalogData, CatalogEntry, TextFacts
from lib.catalog.composition import compose_catalog, newest_official
from tests.catalog_support import catalog_of


def _text(name: str, display_name: str | None = None, **facts) -> CatalogEntry:
    return CatalogEntry(
        maker="acme",
        name=name,
        media_type="text",
        display_name=display_name or name.title(),
        facts=TextFacts(**facts),
    )


def _official(version: str, *entries: CatalogEntry) -> CatalogData:
    data = catalog_of(entries, {"acme": tuple(entry.key.ref for entry in entries)})
    return replace(data, dataset_version=version)


class TestNewestOfficial:
    def test_takes_the_source_with_the_newer_dataset_version(self):
        factory = _official("2026-10-01T00:00:00Z", _text("chat", "Chat v1"))
        synced = _official("2026-10-20T00:00:00Z", _text("chat", "Chat v2"))

        assert newest_official([factory, synced]) is synced
        assert newest_official([synced, factory]) is synced

    def test_older_sync_does_not_shadow_a_newer_factory_snapshot(self):
        # 升级应用后出厂快照可能比库里存的官方同步更新
        factory = _official("2026-11-01T00:00:00Z", _text("chat", "Chat v3"))
        synced = _official("2026-10-20T00:00:00Z", _text("chat", "Chat v2"))

        assert newest_official([factory, synced]) is factory

    def test_same_version_keeps_the_first_source(self):
        factory = _official("2026-10-01T00:00:00Z", _text("chat"))
        synced = _official("2026-10-01T00:00:00Z", _text("chat"))

        assert newest_official([factory, synced]) is factory


class TestComposeCatalog:
    def test_following_official_uses_official_entries(self):
        official = _official("2026-10-01T00:00:00Z", _text("chat"), _text("draft"))

        composed = compose_catalog(official, ())

        assert composed.data.entries == official.entries
        assert composed.source_of(_text("chat").key) == "official"

    def test_user_copy_replaces_the_official_entry_in_place(self):
        chat, draft = _text("chat", max_output_tokens=8192), _text("draft")
        official = _official("2026-10-01T00:00:00Z", chat, draft)
        mine = replace(chat, display_name="My Chat", facts=TextFacts(max_output_tokens=4096))

        composed = compose_catalog(official, (mine,))

        assert composed.data.entries == (mine, draft)
        assert composed.data.builtin_providers == official.builtin_providers
        assert composed.data.dataset_version == official.dataset_version
        assert composed.source_of(chat.key) == "modified"
        assert composed.official_entry(chat.key) == chat
        assert composed.source_of(draft.key) == "official"

    def test_user_entry_without_official_counterpart_is_user_created(self):
        official = _official("2026-10-01T00:00:00Z", _text("chat"))
        mine = _text("homebrew")

        composed = compose_catalog(official, (mine,))

        assert composed.data.entries[-1] == mine
        assert composed.source_of(mine.key) == "user_created"
        assert composed.official_entry(mine.key) is None

    def test_newer_official_data_does_not_override_user_copy(self):
        old = _official("2026-10-01T00:00:00Z", _text("chat", "Chat v1"))
        new = _official("2026-10-20T00:00:00Z", _text("chat", "Chat v2"))
        mine = _text("chat", "Mine")

        composed = compose_catalog(newest_official([old, new]), (mine,))

        assert composed.data.entries == (mine,)
        assert composed.official_entry(mine.key) == _text("chat", "Chat v2")

    def test_user_created_entry_stays_user_created_once_official_data_adds_it(self):
        # 用户新建的条目日后被官方数据收录：仍是用户新建、不跟随官方，同时能看到官方的值（可改用官方数据）
        official = _official("2026-10-20T00:00:00Z", _text("homebrew", "Official Homebrew"))
        mine = _text("homebrew", "My Homebrew")

        created = compose_catalog(official, (mine,), created=(mine.key,))
        modified = compose_catalog(official, (mine,))

        assert created.data.entries == (mine,)
        assert created.source_of(mine.key) == "user_created"
        assert created.official_entry(mine.key) == _text("homebrew", "Official Homebrew")
        # 同一份副本没有新建的标记时，是用户改过官方条目
        assert modified.source_of(mine.key) == "modified"
