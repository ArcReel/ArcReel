"""构建侧从严：目录数据有任何问题都不发布。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from catalog_factories import catalog, text_entry, video_entry

from arcreel_catalog_core import check_catalog


def _codes(raw: object) -> list[tuple[str, str]]:
    return [(issue.path, issue.code) for issue in check_catalog(raw)]


def test_valid_catalog_has_no_issues():
    assert check_catalog(catalog()) == []


def test_model_alias_conflicting_between_two_entries_is_rejected():
    data = catalog()
    other = text_entry()
    other["name"] = "gemini-3.1-pro-exp"
    other["aliases"] = ["Gemini-3.1-Pro-Latest"]  # 归一化后与第一个条目的模型别名相同
    data["entries"].append(other)

    assert _codes(data) == [("entries[4].aliases[0]", "name_conflict")]


def test_provider_listing_referencing_missing_entry_is_rejected():
    data = catalog()
    data["builtin_providers"]["gemini-aistudio"]["models"].append(
        {"api_model_name": "gemini-9-ultra", "entry": "google/gemini-9-ultra"}
    )

    assert _codes(data) == [("builtin_providers.gemini-aistudio.models[1].entry", "unknown_entry")]


def _mutate(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    """按 ``a.b[0].c`` 形式的路径写入一个值。"""

    def apply(data: dict[str, Any]) -> None:
        *parents, last = path.replace("[", ".").replace("]", "").split(".")
        node: Any = data
        for key in parents:
            node = node[int(key)] if key.isdigit() else node[key]
        if last.isdigit():
            node[int(last)] = value
        else:
            node[last] = value

    return apply


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        pytest.param(
            lambda data: data["entries"].append({**video_entry(), "maker": "bytedance", "name": "Kling-V3.Omni"}),
            ("entries[4].name", "name_conflict"),
            id="不同模型厂商的同媒体类型条目归一化后同名",
        ),
        pytest.param(
            lambda data: data["entries"].append(text_entry()),
            ("entries[4]", "duplicate_entry"),
            id="条目身份重复",
        ),
        pytest.param(
            _mutate("entries[0].price.meters[2].meter", "output_tokenz"),
            ("entries[0].price.meters[2].meter", "unknown_meter"),
            id="计量项不在已知集合内",
        ),
        pytest.param(
            _mutate("entries[0].price.meters[2].meter", "output_seconds"),
            ("entries[0].price.meters[2].meter", "meter_not_for_media_type"),
            id="计量项不适用于条目的媒体类型",
        ),
        pytest.param(
            _mutate("entries[1].price.meters[0].dims", ["resolution", "audio", "fps"]),
            ("entries[1].price.meters[0].dims[2]", "unknown_dimension"),
            id="选择维度不在已知集合内",
        ),
        pytest.param(
            _mutate("entries[1].price.meters[0].rows[2].when", {"resolution": "720p"}),
            ("entries[1].price.meters[0].rows[2]", "overlapping_rows"),
            id="同一请求能命中两行单价",
        ),
        pytest.param(
            _mutate("entries[1].price.meters[0].rows[0].when", {"resolution": "720p", "audio": "no"}),
            ("entries[1].price.meters[0].rows[0].when.audio", "invalid_dimension_value"),
            id="选择维度取值类型不符",
        ),
        pytest.param(
            _mutate(
                "entries[1].price.meters[0]",
                {
                    "meter": "output_seconds",
                    "dims": ["reference_to_video"],
                    "rows": [{"when": {"reference_to_video": "yes"}, "price": 0.5}],
                },
            ),
            ("entries[1].price.meters[0].rows[0].when.reference_to_video", "invalid_dimension_value"),
            id="参考生视频维度取值不是布尔值",
        ),
        pytest.param(
            _mutate("entries[0].price.meters[2].cjk_double", True),
            ("entries[0].price.meters[2].cjk_double", "cjk_double_not_characters"),
            id="汉字计 2 只用于字符数",
        ),
        pytest.param(
            _mutate("entries[0].price.currency", "EUR"),
            ("entries[0].price.currency", "unknown_currency"),
            id="币种不在已知集合内",
        ),
        pytest.param(
            _mutate("entries[0].display", "x"),
            ("entries[0].display", "unknown_field"),
            id="不认识的字段",
        ),
        pytest.param(
            _mutate("entries[1].facts.duration_constraints.by_resolution", {"4k": [5]}),
            ("entries[1].facts.duration_constraints.by_resolution.4k", "constraint_outside_facts"),
            id="联动约束引用条目没有的分辨率",
        ),
        pytest.param(
            _mutate("entries[2].price.meters[0].rows[0].when.resolution", "2k"),
            ("entries[2].price.meters[0].rows[0].when.resolution", "resolution_case_mismatch"),
            id="价目表行的分辨率与条目声明的分辨率只差大小写",
        ),
        pytest.param(
            _mutate("entries[1].price.meters[0].defaults.resolution", "1080P"),
            ("entries[1].price.meters[0].defaults.resolution", "resolution_case_mismatch"),
            id="价目表缺省分辨率与条目声明的分辨率只差大小写",
        ),
        pytest.param(
            _mutate("builtin_providers.kling.models[0].media_type", None),
            ("builtin_providers.kling.models[0].entry", "ambiguous_entry"),
            id="引用两栖模型未写媒体类型",
        ),
        pytest.param(
            lambda data: data["builtin_providers"]["kling"]["models"].append(
                {"api_model_name": "kling-v3-omni", "entry": "kuaishou/kling-v3-omni", "media_type": "video"}
            ),
            ("builtin_providers.kling.models[2]", "duplicate_provider_model"),
            id="同一供应商下 API 模型名与媒体类型重复",
        ),
        pytest.param(
            _mutate("builtin_providers.kling.defaults.audio", "kling-v3-omni"),
            ("builtin_providers.kling.defaults.audio", "unknown_default_model"),
            id="默认模型不在该供应商该媒体类型的清单中",
        ),
        pytest.param(
            _mutate("builtin_providers.kling.defaults.vidoe", "kling-v3-omni"),
            ("builtin_providers.kling.defaults.vidoe", "unknown_media_type"),
            id="默认模型的媒体类型键拼错",
        ),
        pytest.param(
            _mutate("entries[0].discontinued", True),
            ("builtin_providers.gemini-aistudio.defaults.text", "default_model_discontinued"),
            id="默认模型已下架",
        ),
        pytest.param(
            _mutate("builtin_providers.kling.models[1].min_app_version", "next"),
            ("builtin_providers.kling.models[1].min_app_version", "invalid_app_version"),
            id="最低应用版本格式不对",
        ),
    ],
)
def test_build_side_rejects(mutation: Callable[[dict[str, Any]], None], expected: tuple[str, str]):
    data = catalog()
    mutation(data)

    assert _codes(data) == [expected]


def test_price_rows_for_resolutions_beyond_entry_facts_are_allowed():
    """条目声明之外的档位照常定价：存量设置或 Agent 入参仍可能带着它们进入计费。"""
    data = catalog()
    data["entries"][2]["price"]["meters"][0]["rows"].append({"when": {"resolution": "4K"}, "price": 0.4})
    data["entries"][1]["price"]["meters"][0]["rows"].append({"when": {"resolution": "4k"}, "price": 2.0})

    assert check_catalog(data) == []


def test_price_resolution_case_is_unchecked_when_entry_declares_no_resolutions():
    data = catalog()
    del data["entries"][2]["facts"]["resolutions"]
    data["entries"][2]["price"]["meters"][0]["rows"][0]["when"]["resolution"] = "2k"

    assert check_catalog(data) == []
