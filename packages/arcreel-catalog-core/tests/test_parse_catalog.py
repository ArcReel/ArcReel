"""实例侧从宽：不认识的内容不阻断读入，只有结构无法解析时整份拒收。"""

from __future__ import annotations

import pytest
from catalog_factories import catalog

from arcreel_catalog_core import CatalogFormatError, DurationConstraints, VideoFacts, parse_catalog, parse_entry


def test_unknown_fields_are_ignored():
    data = catalog()
    data["published_by"] = "bot"
    data["entries"][0]["benchmark"] = 91.2
    data["entries"][0]["price"]["meters"][0]["note"] = "long context"
    data["builtin_providers"]["kling"]["models"][0]["region"] = "cn"

    parsed = parse_catalog(data)

    assert [entry.key.ref for entry in parsed.entries] == [
        "google/gemini-3.1-pro",
        "kuaishou/kling-v3-omni",
        "kuaishou/kling-v3-omni",
        "alibaba/qwen3-tts-flash",
    ]
    assert parsed.entries[1].facts == VideoFacts(
        durations=(5, 10),
        resolutions=("720p", "1080p"),
        duration_constraints=DurationConstraints(by_resolution={"1080p": (5,)}, with_reference_images=(5,)),
    )


@pytest.mark.parametrize(
    ("entry_index", "path", "value", "unrecognized"),
    [
        pytest.param(0, ("meters", 2, "meter"), "reasoning_tokens", ("reasoning_tokens",), id="计量项"),
        pytest.param(1, ("meters", 0, "dims"), ["resolution", "audio", "fps"], ("fps",), id="选择维度"),
        pytest.param(0, ("currency",), "EUR", ("EUR",), id="币种"),
    ],
)
def test_price_table_depending_on_unknown_vocabulary_is_unpriceable(
    entry_index: int, path: tuple[str | int, ...], value: object, unrecognized: tuple[str, ...]
):
    data = catalog()
    node = data["entries"][entry_index]["price"]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value

    price = parse_catalog(data).entries[entry_index].price

    assert price is not None
    assert price.unrecognized_keys == unrecognized
    assert not price.is_priceable


def test_known_price_table_is_priceable():
    price = parse_catalog(catalog()).entries[0].price

    assert price is not None
    assert price.is_priceable


def test_entry_of_unknown_media_type_is_skipped():
    data = catalog()
    data["entries"][3]["media_type"] = "music"

    parsed = parse_catalog(data)

    assert [entry.media_type for entry in parsed.entries] == ["text", "video", "image"]


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(lambda data: data.update(entries={"x": 1}), id="条目列表不是数组"),
        pytest.param(lambda data: data.update(dataset_version="yesterday"), id="数据集版本格式不对"),
        pytest.param(lambda data: data["entries"][0].pop("name"), id="条目缺少官方模型名"),
        pytest.param(
            lambda data: data["entries"][0]["price"]["meters"][0]["rows"][0].update(price="2"), id="单价不是数字"
        ),
    ],
)
def test_unparseable_catalog_is_rejected(corrupt):
    data = catalog()
    corrupt(data)

    with pytest.raises(CatalogFormatError):
        parse_catalog(data)


def test_amphibious_listing_resolves_to_entry_of_its_media_type():
    parsed = parse_catalog(catalog())
    image_model = parsed.builtin_providers["kling"].models[1]

    entry = parsed.entry_of(image_model)

    assert entry is not None
    assert (entry.key.ref, entry.media_type, entry.display_name) == (
        "kuaishou/kling-v3-omni",
        "image",
        "可灵 V3 Omni（图片）",
    )


def test_single_entry_is_read_like_a_catalog_entry():
    entry = parse_entry(catalog()["entries"][3])

    assert entry.price is not None
    assert (entry.price.meters[0].meter, entry.price.meters[0].cjk_double) == ("characters", True)
