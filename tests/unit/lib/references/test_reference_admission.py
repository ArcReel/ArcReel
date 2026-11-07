import unicodedata

import pytest

from lib.project.asset_types import ASSET_SPECS
from lib.references.reference_admission import (
    SHEET_REQUIRED_ASSET_TYPES,
    ReferenceAdmission,
    admit_references,
    admit_storyboard_item,
    admit_storyboard_items,
)
from lib.references.reference_catalog import build_reference_catalog

#: Vietnamese combining-mark names: visually identical encodings with different bytes test asset-name comparison coordinates.
_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")


def _project(**buckets: dict[str, object]) -> dict[str, object]:
    """Build project.json payloads by asset type, obtaining buckets such as ``characters`` from ASSET_SPECS."""
    return {ASSET_SPECS[asset_type].bucket_key: bucket for asset_type, bucket in buckets.items()}


def _sheet(asset_type: str, value: str = "assets/sheet.png") -> dict[str, object]:
    return {ASSET_SPECS[asset_type].sheet_field: value}


def _catalog(**buckets: dict[str, object]):
    return build_reference_catalog(_project(**buckets))


def test_sheet_required_types_exclude_product():
    """Products without sheets pass originals downstream (ADR 0034), outside the blocking axis."""
    assert set(ASSET_SPECS) - {"product"} == SHEET_REQUIRED_ASSET_TYPES


def test_empty_admission_is_allowed():
    assert ReferenceAdmission().admitted is True


@pytest.mark.parametrize("asset_type", sorted(SHEET_REQUIRED_ASSET_TYPES))
def test_registered_reference_with_sheet_is_admitted(asset_type: str):
    catalog = _catalog(**{asset_type: {"甲": _sheet(asset_type)}})

    admission = admit_references(catalog, references=[(asset_type, "甲")])

    assert admission == ReferenceAdmission()
    assert admission.admitted is True


@pytest.mark.parametrize("asset_type", sorted(SHEET_REQUIRED_ASSET_TYPES))
def test_registered_reference_without_sheet_blocks_and_names_it(asset_type: str):
    """Missing sheets block and list names; originals no longer substitute (ADR 0073)."""
    catalog = _catalog(**{asset_type: {"甲": {"reference_image": "uploads/raw.png"}}})

    admission = admit_references(catalog, references=[(asset_type, "甲")])

    assert admission.without_sheet == ((asset_type, "甲"),)
    assert admission.admitted is False
    assert admission.without_sheet_text() == f"{asset_type}: 甲"


def test_product_without_sheet_is_admitted():
    """Product originals anchor fidelity acceptance; missing sheets do not block."""
    catalog = _catalog(product={"可乐": {"reference_images": ["uploads/a.png"]}})

    assert admit_references(catalog, references=[("product", "可乐")]).admitted is True


def test_empty_sheet_field_counts_as_no_sheet():
    catalog = _catalog(character={"甲": {"character_sheet": ""}})

    assert admit_references(catalog, references=[("character", "甲")]).without_sheet == (("character", "甲"),)


def test_non_dict_asset_entry_counts_as_no_sheet():
    """Externally malformed entries act as missing sheets without exceptions."""
    catalog = _catalog(character={"甲": "坏数据"})

    assert admit_references(catalog, references=[("character", "甲")]).without_sheet == (("character", "甲"),)


def test_unregistered_reference_blocks_and_names_it():
    catalog = _catalog(character={"张三": _sheet("character")})

    admission = admit_references(catalog, references=[("character", "李四")])

    assert admission.unregistered == ("李四",)
    assert admission.admitted is False
    assert admission.unregistered_text() == "李四"


def test_pre_derived_unregistered_names_join_the_same_axis():
    """Names already classified unregistered from the body, including deleted-asset residuals, block directly."""
    catalog = _catalog(character={"张三": _sheet("character")})

    admission = admit_references(catalog, references=[("character", "张三")], unregistered=["已删除的道具"])

    assert admission.unregistered == ("已删除的道具",)
    assert admission.without_sheet == ()


def test_unregistered_names_are_deduplicated_in_first_seen_order():
    catalog = _catalog(character={})

    admission = admit_references(catalog, references=[("character", "乙"), ("character", "甲"), ("character", "乙")])

    assert admission.unregistered == ("乙", "甲")


def test_repeated_sheetless_reference_is_reported_once():
    catalog = _catalog(scene={"酒馆": {}})

    admission = admit_references(catalog, references=[("scene", "酒馆"), ("scene", "酒馆")])

    assert admission.without_sheet == (("scene", "酒馆"),)


def test_lookup_does_not_borrow_registration_from_another_type():
    """Cross-type names do not collapse: scene slots require registration in the scene table."""
    catalog = _catalog(character={"月台": _sheet("character")})

    assert admit_references(catalog, references=[("scene", "月台")]).unregistered == ("月台",)


def test_name_comparison_is_unicode_normalized():
    """NFC asset names and equivalent NFD script names count as registered."""
    catalog = _catalog(character={_NAME_NFC: _sheet("character")})

    assert admit_references(catalog, references=[("character", _NAME_NFD)]).admitted is True


def test_storyboard_item_reads_every_reference_field():
    catalog = _catalog(
        character={"张三": _sheet("character")},
        scene={"酒馆": {}},
        prop={"酒杯": _sheet("prop")},
        product={"可乐": {}},
    )
    item = {
        "characters_in_shot": ["张三", "李四"],
        "scenes": ["酒馆"],
        "props": ["酒杯"],
        "products_in_shot": ["可乐"],
    }

    admission = admit_storyboard_item(catalog, item)

    assert admission.unregistered == ("李四",)
    assert admission.without_sheet == (("scene", "酒馆"),)


@pytest.mark.parametrize("char_field", ["characters_in_segment", "characters_in_scene", "characters_in_shot"])
def test_storyboard_item_covers_every_skeleton_character_field(char_field: str):
    """Character-list fields in all three skeletons share admission; none may miss unregistered references."""
    catalog = _catalog(character={})

    assert admit_storyboard_item(catalog, {char_field: ["李四"]}).unregistered == ("李四",)


@pytest.mark.parametrize("raw", [None, "张三", 7, {"name": "张三"}], ids=["缺字段", "字符串", "数字", "对象"])
def test_storyboard_item_skips_non_list_reference_fields(raw: object):
    """Malformed reference fields are skipped without throwing or producing 500."""
    catalog = _catalog(character={})

    assert admit_storyboard_item(catalog, {"characters_in_shot": raw}).admitted is True


def test_storyboard_item_skips_non_string_entries():
    catalog = _catalog(character={})

    assert admit_storyboard_item(catalog, {"characters_in_shot": [None, "", 7, "李四"]}).unregistered == ("李四",)


def test_non_mapping_item_is_admitted():
    assert admit_storyboard_item(_catalog(character={}), ["不是对象"]).admitted is True


def test_storyboard_items_merge_gaps_across_the_batch():
    """Report all batch gaps together, avoiding repeated submissions to discover required fixes."""
    catalog = _catalog(character={"张三": {}})
    items = [
        {"characters_in_shot": ["张三", "李四"]},
        {"characters_in_shot": ["张三"], "scenes": ["酒馆"]},
    ]

    admission = admit_storyboard_items(catalog, items)

    assert admission.unregistered == ("李四", "酒馆")
    assert admission.without_sheet == (("character", "张三"),)


def _character_with_derivatives(sheet: str, **derivatives: str) -> dict[str, object]:
    return {
        "character_sheet": sheet,
        "derivatives": {name: {"description": "变化", "character_sheet": value} for name, value in derivatives.items()},
    }


def test_a_derivative_without_a_sheet_blocks_and_names_the_form():
    """Block the body’s `角色/衍生` reference, not its base character (ADR 0073)."""
    catalog = _catalog(character={"张三": _character_with_derivatives("characters/张三.png", 劲装="")})

    admission = admit_references(catalog, references=[("character", "张三/劲装")])

    assert admission.without_sheet == (("character", "张三/劲装"),)
    assert admission.without_sheet_text() == "character: 张三/劲装"
    assert admission.admitted is False


def test_a_derivative_with_its_own_sheet_is_admitted_alongside_the_ontology():
    catalog = _catalog(
        character={
            "张三": _character_with_derivatives("characters/张三.png", 劲装="characters/derivatives/张三/劲装.png")
        }
    )

    admission = admit_references(catalog, references=[("character", "张三"), ("character", "张三/劲装")])

    assert admission.admitted is True


def test_an_unregistered_derivative_of_a_registered_character_is_unregistered():
    catalog = _catalog(character={"张三": _character_with_derivatives("characters/张三.png")})

    admission = admit_references(catalog, references=[("character", "张三/劲装")])

    assert admission.unregistered == ("张三/劲装",)
    assert admission.without_sheet == ()


def test_storyboard_item_admits_a_derivative_written_in_characters_in_shot():
    catalog = _catalog(character={"张三": _character_with_derivatives("characters/张三.png", 劲装="")})

    admission = admit_storyboard_item(catalog, {"characters_in_shot": ["张三/劲装"]})

    assert admission.without_sheet == (("character", "张三/劲装"),)
