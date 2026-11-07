"""Entry matching (ADR 0105): three-step normalization, precedence, multimodal media selection, binding priority."""

from dataclasses import replace

import pytest

from arcreel_catalog_core import CatalogEntry, EntryKey, ImageFacts, MediaType, TextFacts, VideoFacts
from lib.catalog import query as catalog
from lib.catalog.entry_matching import match_entry
from tests.catalog_support import catalog_of, install_catalog


def _entry(maker: str, name: str, media_type: MediaType, *aliases: str, discontinued: bool = False) -> CatalogEntry:
    facts = {"text": TextFacts(), "image": ImageFacts(), "video": VideoFacts(durations=(5,))}[media_type]
    return CatalogEntry(
        maker=maker,
        name=name,
        media_type=media_type,
        display_name=name,
        facts=facts,
        aliases=aliases,
        discontinued=discontinued,
    )


_GPT = _entry("openai", "gpt-4o", "text", "gpt-4o-2024-08-06")
_GPT_OLD = _entry("openai", "gpt-4o-2024-05-13", "text")
_CLAUDE = _entry("anthropic", "claude-sonnet-4.5", "text")
_THINKING = _entry("deepseek", "deepseek-v3.1-thinking", "text")
_PLAIN = _entry("deepseek", "deepseek-v3.1", "text")
_MIXED = _entry("acme", "Omni-V3", "text")  # Official name has mixed case.
_OMNI_VIDEO = _entry("kling", "kling-v3-omni", "video")
_OMNI_IMAGE = _entry("kling", "kling-v3-omni", "image")
_ALIAS_OWNER = _entry("acme", "alpha", "text", "beta")
_NAME_OWNER = _entry("acme", "beta", "text")
_DOTTED_ALIAS = _entry("acme", "model-x", "text", "Model.X-1")
_ALL = (
    _GPT,
    _GPT_OLD,
    _CLAUDE,
    _THINKING,
    _PLAIN,
    _MIXED,
    _OMNI_VIDEO,
    _OMNI_IMAGE,
    _ALIAS_OWNER,
    _NAME_OWNER,
    _DOTTED_ALIAS,
)


@pytest.mark.parametrize(
    ("model_name", "media_type", "expected"),
    [
        # Exact official-name and alias matches.
        ("gpt-4o", None, _GPT),
        ("gpt-4o-2024-08-06", None, _GPT),
        # Ignore case.
        ("GPT-4O", None, _GPT),
        ("omni-v3", None, _MIXED),
        # Strip through the last /; nested namespaces also retain only the final segment.
        ("openai/gpt-4o", None, _GPT),
        ("vendor/openai/gpt-4o", None, _GPT),
        ("openai/GPT-4o-2024-08-06", None, _GPT),
        # Dots equal hyphens, in both directions.
        ("claude-sonnet-4-5", None, _CLAUDE),
        ("anthropic/claude-sonnet-4.5", None, _CLAUDE),
        ("model.x.1", None, _DOTTED_ALIAS),
        ("MODEL-X-1", None, _DOTTED_ALIAS),
        # Do not strip date suffixes; unlisted dated snapshots cannot match.
        ("gpt-4o-2024-11-20", None, None),
        # Listed dated names are independent entries, not merged into their base name.
        ("gpt-4o-2024-05-13", None, _GPT_OLD),
        # Do not strip suffixes such as :free, -thinking, -preview.
        ("deepseek-v3.1:free", None, None),
        ("deepseek/deepseek-v3.1-thinking", None, _THINKING),
        ("deepseek-v3.1-preview", None, None),
        ("gpt-4o-preview", None, None),
        ("Omni-V3", None, _MIXED),
        # Equal matches prefer official names over aliases: beta aliases _ALIAS_OWNER and names _NAME_OWNER officially.
        ("beta", None, _NAME_OWNER),
        ("BETA", None, _NAME_OWNER),
        # Completely unrelated.
        ("", None, None),
        ("unknown-model", None, None),
        # Multimodal entries require matching media type; without one selection is impossible.
        ("kling-v3-omni", "video", _OMNI_VIDEO),
        ("kling-v3-omni", "image", _OMNI_IMAGE),
        ("kling/Kling-V3-Omni", "image", _OMNI_IMAGE),
        ("kling-v3-omni", None, None),
        # Matching only another media type yields no entry.
        ("gpt-4o", "image", None),
    ],
)
def test_match_entry_by_name(model_name: str, media_type: str | None, expected: CatalogEntry | None) -> None:
    assert match_entry(_ALL, model_name, media_type=media_type) is expected


def test_exact_hit_beats_normalized_hit_even_when_listed_later() -> None:
    dotted = _entry("acme", "m-1", "text")  # Normalizes identically to "M.1".
    exact = _entry("acme", "M.1", "text")
    assert match_entry((dotted, exact), "M.1") is exact
    assert match_entry((exact, dotted), "M.1") is exact


def test_exact_alias_beats_normalized_official_name() -> None:
    normalized_name = _entry("acme", "m-1", "text")
    exact_alias = _entry("other", "primary", "text", "M.1")
    assert match_entry((normalized_name, exact_alias), "M.1") is exact_alias


def test_official_name_beats_alias_at_the_same_rank_regardless_of_order() -> None:
    assert match_entry((_ALIAS_OWNER, _NAME_OWNER), "beta") is _NAME_OWNER
    assert match_entry((_NAME_OWNER, _ALIAS_OWNER), "beta") is _NAME_OWNER
    # Same precedence in normalized-match tier.
    assert match_entry((_ALIAS_OWNER, _NAME_OWNER), "Beta") is _NAME_OWNER


def test_amphibious_with_better_rank_on_one_side_is_not_ambiguous() -> None:
    image = replace(_OMNI_IMAGE, name="Kling-V3-Omni")  # Exact match only against a video entry.
    assert match_entry((_OMNI_VIDEO, image), "kling-v3-omni") is _OMNI_VIDEO


def test_discontinued_entry_still_matches() -> None:
    old = _entry("acme", "old", "text", discontinued=True)
    assert match_entry((old,), "acme/OLD") is old


def test_binding_wins_over_name() -> None:
    binding = EntryKey("anthropic", "claude-sonnet-4.5", "text")
    assert match_entry(_ALL, "gpt-4o", binding=binding) is _CLAUDE


def test_binding_without_name_hit_still_resolves() -> None:
    binding = EntryKey("openai", "gpt-4o", "text")
    assert match_entry(_ALL, "relay-renamed-model", binding=binding) is _GPT


def test_binding_to_amphibious_entry_needs_no_media_type() -> None:
    binding = EntryKey("kling", "kling-v3-omni", "image")
    assert match_entry(_ALL, "whatever", binding=binding) is _OMNI_IMAGE


def test_stale_binding_falls_back_to_name() -> None:
    stale = EntryKey("openai", "removed-entry", "text")
    assert match_entry(_ALL, "gpt-4o", binding=stale) is _GPT
    assert match_entry(_ALL, "unknown", binding=stale) is None


def test_binding_with_other_media_type_than_endpoint_falls_back_to_name() -> None:
    binding = EntryKey("kling", "kling-v3-omni", "image")
    assert match_entry(_ALL, "kling-v3-omni", media_type="video", binding=binding) is _OMNI_VIDEO


def test_lookup_follows_current_catalog_without_storing_result(monkeypatch: pytest.MonkeyPatch) -> None:
    before = catalog_of((_PLAIN,), {})
    install_catalog(monkeypatch, before)
    assert catalog.find_entry("deepseek-v3.1-0324") is None

    # Officially synced new aliases change immediate lookup for the same model name.
    after = replace(before, entries=(replace(_PLAIN, aliases=("deepseek-v3.1-0324",)),))
    install_catalog(monkeypatch, after)
    found = catalog.find_entry("DeepSeek/deepseek-v3.1-0324")
    assert found is not None
    assert found.key == _PLAIN.key


def test_find_entry_reads_factory_snapshot() -> None:
    # Dated Ark IDs are aliases of dotted official names in the snapshot.
    entry = catalog.find_entry("doubao-seed-2-0-pro-260215")
    assert entry is not None
    assert (entry.maker, entry.name, entry.media_type) == ("bytedance", "doubao-seed-2.0-pro", "text")
    assert catalog.find_entry("ByteDance/Doubao-Seed-2.0-Pro") is entry
    assert catalog.find_entry("doubao-seed-2-0-pro-999999") is None
    # Multimodal model: media type is required to select.
    assert catalog.find_entry("kling-v3-omni") is None
    video = catalog.find_entry("kling-v3-omni", media_type="video")
    image = catalog.find_entry("kling-v3-omni", media_type="image")
    assert video is not None
    assert image is not None
    assert (video.media_type, image.media_type) == ("video", "image")
