import unicodedata

import pytest

from lib.asset_types import ASSET_SPECS, DERIVATIVES_FIELD
from lib.reference_catalog import (
    REFERENCE_ATTRIBUTION_ORDER,
    build_reference_catalog,
    renamed_reference,
    split_derivative_reference,
)

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


class TestDerivativeReferences:
    """``本体名/衍生名``：引用命名空间在角色一类上多出的那一层（``docs/adr/0072``）。"""

    def test_registered_derivative_resolves_to_the_character_namespace(self):
        catalog = build_reference_catalog(
            _project(character={"张三": {DERIVATIVES_FIELD: {"劲装": {"character_sheet": "characters/x.png"}}}})
        )

        entry = catalog.resolve("张三/劲装")

        assert entry is not None
        assert (entry.asset_type, entry.name, entry.asset_name) == ("character", "张三/劲装", "张三")

    def test_derivative_entry_carries_its_own_sheet_not_the_base_one(self):
        """准入与投影按引用取资产图：衍生条目的图与本体的图不能混为一张。"""
        catalog = build_reference_catalog(
            _project(
                character={
                    "张三": {
                        "character_sheet": "characters/张三.png",
                        DERIVATIVES_FIELD: {"劲装": {"character_sheet": "characters/derivatives/张三/劲装.png"}},
                    }
                }
            )
        )

        derivative = catalog.lookup("character", "张三/劲装")
        base = catalog.lookup("character", "张三")

        assert derivative is not None
        assert base is not None
        assert derivative.asset == {"character_sheet": "characters/derivatives/张三/劲装.png"}
        assert base.asset["character_sheet"] == "characters/张三.png"  # type: ignore[index]

    def test_unregistered_derivative_of_a_registered_character_is_not_a_reference(self):
        catalog = build_reference_catalog(_project(character={"张三": {DERIVATIVES_FIELD: {"劲装": {}}}}))

        assert catalog.resolve("张三/夜行衣") is None
        assert catalog.lookup("character", "张三/夜行衣") is None

    def test_reference_names_include_derivatives_while_asset_names_do_not(self):
        """说话人位要绑参考音频，须落在本体这条资产上；引用位可以指到衍生这套外观。"""
        catalog = build_reference_catalog(_project(character={"张三": {DERIVATIVES_FIELD: {"劲装": {}, "兽化": {}}}}))

        assert catalog.reference_names("character") == frozenset({"张三", "张三/劲装", "张三/兽化"})
        assert catalog.asset_names("character") == frozenset({"张三"})

    @pytest.mark.parametrize("asset_type", sorted(t for t, s in ASSET_SPECS.items() if not s.supports_derivatives))
    def test_types_without_the_capability_do_not_expand_derivatives(self, asset_type: str):
        """未开启该能力的类型即使被写进 derivatives 字段也不产生 ``名/子名`` 引用。"""
        catalog = build_reference_catalog(_project(**{asset_type: {"甲": {DERIVATIVES_FIELD: {"乙": {}}}}}))

        assert catalog.reference_names(asset_type) == frozenset({"甲"})

    @pytest.mark.parametrize(
        "table", [None, [], "劲装", {"劲装": "not-a-dict"}], ids=["缺失", "列表", "字符串", "值非对象"]
    )
    def test_malformed_derivative_table_does_not_break_the_catalog(self, table: object):
        """畸形载荷的结构错误由 DataValidator 报告；目录构造不抛也不多报。"""
        catalog = build_reference_catalog(_project(character={"张三": {DERIVATIVES_FIELD: table}}))

        assert catalog.resolve("张三") is not None

    @pytest.mark.parametrize("registered", [_NAME_NFC, _NAME_NFD], ids=["登记NFC", "登记NFD"])
    @pytest.mark.parametrize("queried", [_NAME_NFC, _NAME_NFD], ids=["查询NFC", "查询NFD"])
    def test_derivative_name_matches_across_encoding_forms(self, registered: str, queried: str):
        catalog = build_reference_catalog(_project(character={"张三": {DERIVATIVES_FIELD: {registered: {}}}}))

        entry = catalog.resolve(f"张三/{queried}")

        assert entry is not None
        assert entry.name == f"张三/{_NAME_NFC}"


class TestRenamedReference:
    """一次改名怎么映射一个引用名——正文 mention 与引用数组共用的判定。"""

    @pytest.mark.parametrize(
        ("name", "old", "new", "expected"),
        [
            ("张三", "张三", "李四", "李四"),
            ("张三/劲装", "张三", "李四", "李四/劲装"),
            ("张三/劲装", "张三/劲装", "张三/夜行衣", "张三/夜行衣"),
            ("张三/兽化", "张三/劲装", "张三/夜行衣", None),
            ("张三", "张三/劲装", "张三/夜行衣", None),
            ("王五", "张三", "李四", None),
            ("王五/劲装", "张三", "李四", None),
        ],
        ids=[
            "本体改名",
            "本体改名连带衍生",
            "衍生改名",
            "衍生改名不动同角色的其它衍生",
            "衍生改名不动本体",
            "无关名字",
            "无关角色的同名衍生",
        ],
    )
    def test_rename_mapping(self, name: str, old: str, new: str, expected: str | None):
        assert renamed_reference(name, old, new) == expected

    def test_comparison_key_coordinates_apply_to_both_sides(self):
        assert renamed_reference(f" {_NAME_NFD}/劲装 ", _NAME_NFC, "Hero") == "Hero/劲装"


class TestSplitDerivativeReference:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [("张三", ("张三", "")), ("张三/劲装", ("张三", "劲装")), ("/劲装", ("", "劲装")), ("张三/", ("张三", ""))],
        ids=["本体", "衍生", "缺本体", "缺衍生"],
    )
    def test_split(self, name: str, expected: tuple[str, str]):
        assert split_derivative_reference(name) == expected
