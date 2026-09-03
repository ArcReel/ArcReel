"""资产候选块的引用名与外观展开：角色的本体与衍生。"""

from __future__ import annotations

from lib.prompt_rules.asset_appearance import asset_reference_names, iter_asset_appearances


def _characters() -> dict[str, object]:
    return {
        "张三": {
            "description": "青年剑客，玄色长衫",
            "derivatives": {
                "劲装": {"description": "换上黑色劲装"},
                "兽化": {"description": "半兽化，双臂覆鳞"},
            },
        },
        "李四": {"description": "酒馆掌柜"},
    }


def test_character_names_list_each_form_after_its_ontology():
    assert asset_reference_names("character", _characters()) == ["张三", "张三/劲装", "张三/兽化", "李四"]


def test_a_derivative_appearance_is_the_ontology_description_plus_its_change():
    appearances = dict(iter_asset_appearances("character", _characters()))

    assert appearances["张三/劲装"] == "青年剑客，玄色长衫\n当前形态：换上黑色劲装"
    assert appearances["张三"] == "青年剑客，玄色长衫"


def test_types_without_the_derivative_capability_expand_to_names_only():
    scenes = {"酒馆": {"description": "木质吧台", "derivatives": {"夜": {"description": "打烊后"}}}}

    assert asset_reference_names("scene", scenes) == ["酒馆"]


def test_a_derivative_without_a_change_description_keeps_only_the_ontology_line():
    characters = {"张三": {"description": "青年剑客", "derivatives": {"劲装": {}}}}

    assert dict(iter_asset_appearances("character", characters))["张三/劲装"] == "青年剑客"


def test_a_derivative_of_a_character_without_a_description_keeps_only_its_change():
    characters = {"张三": {"derivatives": {"劲装": {"description": "换上黑色劲装"}}}}

    assert dict(iter_asset_appearances("character", characters))["张三/劲装"] == "当前形态：换上黑色劲装"


def test_malformed_entries_degrade_to_names_without_raising():
    characters = {"张三": "坏数据", "李四": {"description": "掌柜", "derivatives": "坏数据"}}

    assert asset_reference_names("character", characters) == ["张三", "李四"]


def test_an_absent_bucket_expands_to_nothing():
    assert asset_reference_names("character", None) == []
