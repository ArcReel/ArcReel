"""脚本规划带出的新增资产：登记名与项目里已有资产或本次另一项新增只差大小写时视为重名。"""

from __future__ import annotations

from typing import Any

import pytest

from lib.script.plan_new_assets import NewAssetsError, resolve_new_assets


def _new(name: str, asset_type: str = "character", **fields: Any) -> dict[str, Any]:
    return {"type": asset_type, "name": name, "decision": "register", "reason": "原文第一次出现", **fields}


def _problems(project: dict[str, Any], items: list[dict[str, Any]]) -> list[tuple[str, int, str]]:
    with pytest.raises(NewAssetsError) as exc_info:
        resolve_new_assets(project, items)
    return [(problem.code, problem.index, problem.message) for problem in exc_info.value.problems]


def _problem_codes(project: dict[str, Any], items: list[dict[str, Any]]) -> list[tuple[str, int]]:
    return [(code, index) for code, index, _ in _problems(project, items)]


@pytest.mark.parametrize(("asset_type", "name"), [("character", "alice"), ("scene", "ALICE")])
def test_a_registration_differing_only_in_case_from_a_registered_asset_is_taken(asset_type: str, name: str) -> None:
    project = {"characters": {"Alice": {"description": "主角"}}}

    assert _problem_codes(project, [_new(name, asset_type, description="另一个人")]) == [("new_asset_name_taken", 0)]


def test_a_registration_name_differing_only_in_case_from_a_registered_asset_is_taken() -> None:
    project = {"characters": {"Alice": {"description": "主角"}}}

    assert _problem_codes(project, [_new("小爱", asset_name="ALICE", description="另一个人")]) == [
        ("new_asset_name_taken", 0)
    ]


def test_two_new_assets_differing_only_in_case_cannot_both_register() -> None:
    items = [_new("Bob", description="一个人"), _new("BOB", "prop", description="一样东西")]

    assert _problem_codes({}, items) == [("new_asset_name_taken", 1)]


def test_a_registration_exactly_matching_a_registered_asset_still_merges_into_it() -> None:
    project = {"characters": {"Alice": {"description": "主角"}}}

    resolution = resolve_new_assets(project, [_new(" Alice ", description="重复登记")])

    assert resolution.registrations == {}
    assert resolution.auto_merged == [("character", " Alice ", "Alice")]


def _derivative(name: str, target: str = "Alice", **fields: Any) -> dict[str, Any]:
    return _new(name, decision="derivative", target=target, description="换装", **fields)


def test_a_derivative_differing_only_in_case_from_an_existing_derivative_is_taken() -> None:
    project = {"characters": {"Alice": {"description": "主角", "derivatives": {"Coat": {"description": "大衣"}}}}}

    assert _problems(project, [_derivative("coat")]) == [
        ("new_asset_name_taken", 0, "new_assets[0]：衍生名「coat」与「Alice」已有的衍生「Coat」重名")
    ]
    assert _problem_codes(project, [_derivative("外套", asset_name="COAT")]) == [("new_asset_name_taken", 0)]


def test_two_new_derivatives_differing_only_in_case_under_one_base_cannot_both_register() -> None:
    project = {"characters": {"Alice": {"description": "主角"}}}

    assert _problems(project, [_derivative("Coat"), _derivative("coat")]) == [
        ("new_asset_name_taken", 1, "new_assets[1]：衍生名「coat」与「Alice」本集新增的衍生「Coat」重名")
    ]


def test_a_derivative_exactly_matching_an_existing_derivative_still_resolves_to_it() -> None:
    project = {
        "characters": {
            "Alice": {
                "description": "主角",
                "derivatives": {"Coat": {"description": "大衣"}, "coat": {"description": "旧版登记的另一件"}},
            }
        }
    }

    resolution = resolve_new_assets(project, [_derivative(" coat ")])

    assert resolution.derivatives == []
    assert resolution.outcomes[("character", "coat")].visual == "Alice/coat"


def test_writing_a_derivative_rejects_a_case_variant_registered_after_resolution() -> None:
    resolution = resolve_new_assets({"characters": {"Alice": {"description": "主角"}}}, [_derivative("Coat")])
    project = {"characters": {"Alice": {"description": "主角", "derivatives": {"coat": {"description": "大衣"}}}}}

    with pytest.raises(ValueError, match="重名"):
        resolution.apply_to_project(project)

    assert list(project["characters"]["Alice"]["derivatives"]) == ["coat"]
