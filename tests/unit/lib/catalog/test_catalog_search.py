"""目录搜索：按名称与媒体类型查找模型条目的读契约。"""

import pytest

from arcreel_catalog_core import parse_catalog
from lib.catalog import query as catalog
from tests.catalog_support import install_catalog


def _entry(maker: str, name: str, media_type: str, display_name: str, **extra) -> dict:
    return {"maker": maker, "name": name, "media_type": media_type, "display_name": display_name, "facts": {}, **extra}


@pytest.fixture(autouse=True)
def _catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    data = parse_catalog(
        {
            "dataset_version": "2026-01-01T00:00:00Z",
            "entries": [
                _entry("openai", "gpt-5.5-pro", "text", "GPT-5.5 Pro"),
                _entry("openai", "gpt-5.5", "text", "GPT-5.5"),
                _entry("openai", "gpt-4o", "text", "GPT-4o", discontinued=True),
                _entry(
                    "bytedance",
                    "doubao-seed-2.0-pro",
                    "text",
                    "豆包 Seed 2.0 Pro",
                    aliases=["doubao-seed-2-0-pro-260215"],
                ),
                _entry("kuaishou", "kling-v3-omni", "video", "可灵 v3 Omni"),
                _entry("kuaishou", "kling-v3-omni", "image", "可灵 v3 Omni 图片"),
                _entry("openai", "gpt-image-2", "image", "GPT Image 2"),
            ],
            "builtin_providers": {},
        }
    )
    install_catalog(monkeypatch, data)


def _names(hits: list[catalog.CatalogSearchHit]) -> list[tuple[str, str]]:
    return [(hit.name, hit.media_type) for hit in hits]


def test_empty_query_lists_everything_with_discontinued_last() -> None:
    hits = catalog.search_entries()

    assert _names(hits)[-1] == ("gpt-4o", "text")
    assert len(hits) == 7


def test_media_type_filter_keeps_only_that_type() -> None:
    assert _names(catalog.search_entries(media_type="video")) == [("kling-v3-omni", "video")]


def test_amphibious_model_is_two_hits_distinguished_by_media_type() -> None:
    hits = catalog.search_entries("kling")

    assert sorted(_names(hits)) == [("kling-v3-omni", "image"), ("kling-v3-omni", "video")]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("GPT-5.5", ["gpt-5.5", "gpt-5.5-pro"]),
        ("gpt-5-5", ["gpt-5.5", "gpt-5.5-pro"]),
        ("豆包", ["doubao-seed-2.0-pro"]),
        ("260215", ["doubao-seed-2.0-pro"]),
        ("openai/gpt-5.5-pro", ["gpt-5.5-pro"]),
        ("gpt pro", ["gpt-5.5-pro"]),
        ("no-such-model", []),
    ],
)
def test_query_matches_name_display_name_and_alias_ignoring_case_and_dots(query: str, expected: list[str]) -> None:
    assert [hit.name for hit in catalog.search_entries(query, "text")] == expected


def test_exact_match_ranks_before_prefix_and_substring_matches() -> None:
    assert [hit.name for hit in catalog.search_entries("pro", "text")] == ["gpt-5.5-pro", "doubao-seed-2.0-pro"]
    first, *_ = catalog.search_entries("gpt-5.5", "text")
    assert first.name == "gpt-5.5"


def test_hit_carries_what_the_caller_needs_to_bind_the_entry() -> None:
    (hit,) = catalog.search_entries("gpt-image", "image")

    assert hit == catalog.CatalogSearchHit(
        maker="openai", name="gpt-image-2", media_type="image", display_name="GPT Image 2", discontinued=False
    )
