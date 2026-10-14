"""模型名对应条目（ADR 0105）：三步归一化、命中先后、两栖模型按媒体类型取、绑定优先。"""

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
_MIXED = _entry("acme", "Omni-V3", "text")  # 官方名带大小写
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
        # 精确命中官方模型名与模型别名
        ("gpt-4o", None, _GPT),
        ("gpt-4o-2024-08-06", None, _GPT),
        # 忽略大小写
        ("GPT-4O", None, _GPT),
        ("omni-v3", None, _MIXED),
        # 去掉最后一个 / 及之前的命名空间，多层命名空间也只留最后一段
        ("openai/gpt-4o", None, _GPT),
        ("vendor/openai/gpt-4o", None, _GPT),
        ("openai/GPT-4o-2024-08-06", None, _GPT),
        # 点号视同连字符，两个方向都成立
        ("claude-sonnet-4-5", None, _CLAUDE),
        ("anthropic/claude-sonnet-4.5", None, _CLAUDE),
        ("model.x.1", None, _DOTTED_ALIAS),
        ("MODEL-X-1", None, _DOTTED_ALIAS),
        # 不去日期后缀：未收录的日期快照对应不上
        ("gpt-4o-2024-11-20", None, None),
        # 带日期的收录名是独立条目，不会并到主名
        ("gpt-4o-2024-05-13", None, _GPT_OLD),
        # 不去 :free、-thinking、-preview 一类后缀
        ("deepseek-v3.1:free", None, None),
        ("deepseek/deepseek-v3.1-thinking", None, _THINKING),
        ("deepseek-v3.1-preview", None, None),
        ("gpt-4o-preview", None, None),
        ("Omni-V3", None, _MIXED),
        # 同等命中时官方模型名优先于模型别名：beta 既是 _ALIAS_OWNER 的别名，又是 _NAME_OWNER 的官方名
        ("beta", None, _NAME_OWNER),
        ("BETA", None, _NAME_OWNER),
        # 完全不相关
        ("", None, None),
        ("unknown-model", None, None),
        # 两栖模型：媒体类型一致的那一条；不给媒体类型则无法选择
        ("kling-v3-omni", "video", _OMNI_VIDEO),
        ("kling-v3-omni", "image", _OMNI_IMAGE),
        ("kling/Kling-V3-Omni", "image", _OMNI_IMAGE),
        ("kling-v3-omni", None, None),
        # 只命中其他媒体类型的条目时对应不上
        ("gpt-4o", "image", None),
    ],
)
def test_match_entry_by_name(model_name: str, media_type: str | None, expected: CatalogEntry | None) -> None:
    assert match_entry(_ALL, model_name, media_type=media_type) is expected


def test_exact_hit_beats_normalized_hit_even_when_listed_later() -> None:
    dotted = _entry("acme", "m-1", "text")  # 归一化后与 "M.1" 相同
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
    # 归一化命中一档里同样如此
    assert match_entry((_ALIAS_OWNER, _NAME_OWNER), "Beta") is _NAME_OWNER


def test_amphibious_with_better_rank_on_one_side_is_not_ambiguous() -> None:
    image = replace(_OMNI_IMAGE, name="Kling-V3-Omni")  # 只与视频条目精确命中的情形
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

    # 官方同步给条目带来新别名后，同一个模型名的现查结果随之变化
    after = replace(before, entries=(replace(_PLAIN, aliases=("deepseek-v3.1-0324",)),))
    install_catalog(monkeypatch, after)
    found = catalog.find_entry("DeepSeek/deepseek-v3.1-0324")
    assert found is not None
    assert found.key == _PLAIN.key


def test_find_entry_reads_factory_snapshot() -> None:
    # 方舟带日期的 ID 在快照里是点号官方名的模型别名
    entry = catalog.find_entry("doubao-seed-2-0-pro-260215")
    assert entry is not None
    assert (entry.maker, entry.name, entry.media_type) == ("bytedance", "doubao-seed-2.0-pro", "text")
    assert catalog.find_entry("ByteDance/Doubao-Seed-2.0-Pro") is entry
    assert catalog.find_entry("doubao-seed-2-0-pro-999999") is None
    # 两栖模型：给出媒体类型才能选
    assert catalog.find_entry("kling-v3-omni") is None
    video = catalog.find_entry("kling-v3-omni", media_type="video")
    image = catalog.find_entry("kling-v3-omni", media_type="image")
    assert video is not None
    assert image is not None
    assert (video.media_type, image.media_type) == ("video", "image")
