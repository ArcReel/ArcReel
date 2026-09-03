import unicodedata

import pytest

from lib.asset_types import ASSET_SPECS
from lib.reference_catalog import REFERENCE_ATTRIBUTION_ORDER, build_reference_catalog

#: 带组合附加符的资产名（越南语），两种编码屏幕显示相同、字节不同——资产名比对坐标系的用例。
_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")


def _project(**buckets: dict[str, object]) -> dict[str, object]:
    """按资产类型名给出 project.json 载荷（``characters`` 等桶键从 ASSET_SPECS 取）。"""
    return {ASSET_SPECS[asset_type].bucket_key: bucket for asset_type, bucket in buckets.items()}


def test_attribution_order_covers_every_asset_type():
    """优先级表必须穷尽资产类型：漏一类会让该类的引用永远解析不出归属。"""
    assert set(REFERENCE_ATTRIBUTION_ORDER) == set(ASSET_SPECS)


@pytest.mark.parametrize("asset_type", sorted(ASSET_SPECS))
def test_resolve_attributes_name_to_its_only_bucket(asset_type: str):
    catalog = build_reference_catalog(_project(**{asset_type: {"甲": {}}}))

    entry = catalog.resolve("甲")

    assert entry is not None
    assert (entry.asset_type, entry.name, entry.asset_name) == (asset_type, "甲", "甲")
    assert entry.spec is ASSET_SPECS[asset_type]


@pytest.mark.parametrize(
    ("registered_types", "expected"),
    [
        (("product", "character", "scene", "prop"), "product"),
        (("character", "scene", "prop"), "character"),
        (("scene", "prop"), "scene"),
        (("prop",), "prop"),
        (("product", "prop"), "product"),
        (("character", "prop"), "character"),
    ],
    ids=["四类同名", "去商品", "去角色", "只道具", "商品胜道具", "角色胜道具"],
)
def test_resolve_decides_duplicate_names_by_attribution_order(registered_types: tuple[str, ...], expected: str):
    """商品→角色→场景→道具：共享名称空间闸门之前的存量重名按此稳定决议。"""
    catalog = build_reference_catalog(_project(**{t: {"Shared": {"tag": t}} for t in registered_types}))

    entry = catalog.resolve("Shared")

    assert entry is not None
    assert entry.asset_type == expected
    assert entry.asset == {"tag": expected}


def test_resolve_returns_none_for_unregistered_name():
    assert build_reference_catalog(_project(character={"张三": {}})).resolve("未登记") is None


def test_resolve_ignores_leading_and_trailing_whitespace_in_query():
    assert build_reference_catalog(_project(character={"Hero": {}})).resolve(" Hero ") is not None


@pytest.mark.parametrize("registered", [_NAME_NFC, _NAME_NFD], ids=["登记NFC", "登记NFD"])
@pytest.mark.parametrize("queried", [_NAME_NFC, _NAME_NFD], ids=["查询NFC", "查询NFD"])
def test_resolve_matches_across_encoding_forms(registered: str, queried: str):
    """四种 NFC/NFD 配对都判为已登记，且目录给出的名字一律是归一形式。

    资产表以哪种形式落盘不可控（登记闸口落 NFC，存量不迁移），少归一一侧的后果是用户对着
    两个肉眼一致的名字无从排查。
    """
    catalog = build_reference_catalog(_project(character={registered: {}}))

    entry = catalog.resolve(queried)

    assert entry is not None
    assert (entry.name, entry.asset_name) == (_NAME_NFC, _NAME_NFC)


@pytest.mark.parametrize("registered", [_NAME_NFC, _NAME_NFD], ids=["登记NFC", "登记NFD"])
def test_reference_names_are_normalized(registered: str):
    catalog = build_reference_catalog(_project(character={registered: {}}))

    assert catalog.reference_names("character") == frozenset({_NAME_NFC})
    assert catalog.asset_names("character") == frozenset({_NAME_NFC})


def test_duplicate_encoding_forms_in_one_bucket_collapse_to_one_name():
    """同一张表里的 NFC / NFD 同名 key 本就指同一个资产，归一后合并，后写入的胜出。"""
    catalog = build_reference_catalog(_project(character={_NAME_NFD: {"tag": "nfd"}, _NAME_NFC: {"tag": "nfc"}}))

    assert catalog.reference_names("character") == frozenset({_NAME_NFC})
    entry = catalog.resolve(_NAME_NFC)
    assert entry is not None
    assert entry.asset == {"tag": "nfc"}


def test_lookup_does_not_cross_asset_types():
    """按类型查不跨类型决议：调用方已知类型时，重名不该把结论偷换成优先级更高的那一类。"""
    catalog = build_reference_catalog(_project(product={"Shared": {}}, prop={"Shared": {}}))

    prop_entry = catalog.lookup("prop", "Shared")

    assert prop_entry is not None
    assert prop_entry.asset_type == "prop"
    assert catalog.lookup("scene", "Shared") is None


def test_reference_names_keep_duplicates_visible_in_every_type():
    """重名在各自类型里都算已登记：引用字段问的是「该类型登记了没有」。"""
    catalog = build_reference_catalog(_project(product={"Shared": {}}, character={"Shared": {}}))

    assert catalog.reference_names("product") == frozenset({"Shared"})
    assert catalog.reference_names("character") == frozenset({"Shared"})


def test_unknown_asset_type_is_rejected_loudly():
    """类型名写错时空集会静默把「未登记」判成结论，故按 KeyError 响亮失败。"""
    catalog = build_reference_catalog(_project(character={"甲": {}}))

    with pytest.raises(KeyError):
        catalog.reference_names("unknown")
    with pytest.raises(KeyError):
        catalog.asset_names("unknown")
    with pytest.raises(KeyError):
        catalog.lookup("unknown", "甲")


@pytest.mark.parametrize(
    "project",
    [None, [], "characters", 0, {"characters": []}, {"characters": None}],
    ids=["None", "列表", "字符串", "整数", "桶是列表", "桶是None"],
)
def test_malformed_payload_yields_empty_catalog(project: object):
    """畸形载荷按空表处理：结构错误由 DataValidator 报告，目录构造不重复报错也不抛异常。"""
    catalog = build_reference_catalog(project)

    assert catalog.resolve("甲") is None
    assert all(catalog.reference_names(asset_type) == frozenset() for asset_type in ASSET_SPECS)


def test_non_string_bucket_keys_are_read_as_text():
    """外部编辑写出的非字符串 key 不让目录构造崩：读成文本，其结构错误另行报告。"""
    catalog = build_reference_catalog({ASSET_SPECS["prop"].bucket_key: {7: {}}})

    assert catalog.reference_names("prop") == frozenset({"7"})


def test_entry_carries_the_asset_payload_for_downstream_path_resolution():
    """参考图投影要从条目读 sheet / 原图字段，目录必须原样带上落盘值（含畸形值）。"""
    catalog = build_reference_catalog(
        _project(character={"张三": {"character_sheet": "characters/张三.png"}, "李四": 3})
    )

    zhang = catalog.resolve("张三")
    li = catalog.resolve("李四")

    assert zhang is not None
    assert zhang.asset == {"character_sheet": "characters/张三.png"}
    assert li is not None
    assert li.asset == 3
