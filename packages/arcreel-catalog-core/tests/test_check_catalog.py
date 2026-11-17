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


def test_context_tiers_must_start_at_zero_in_every_tiered_meter():
    """首档不是 0 时，比它短的请求不属于任何一档；分界相同也要拦下。"""
    data = catalog()
    for meter in data["entries"][0]["price"]["meters"][:2]:
        meter["rows"][0]["when"]["context_tier"] = 100000

    assert _codes(data) == [
        ("entries[0].price.meters[0]", "context_tier_not_from_zero"),
        ("entries[0].price.meters[1]", "context_tier_not_from_zero"),
    ]


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
            _mutate("entries[0].price.meters[1].rows[1].when", {"context_tier": 128000}),
            ("entries[0].price.meters[1]", "context_tier_mismatch"),
            id="各计量项的上下文档分界不同",
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
                {
                    "api_model_name": "kling-v3-omni",
                    "entry": "kuaishou/kling-v3-omni",
                    "media_type": "video",
                    "endpoint": "kling-video-audio",
                }
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
            id="默认模型已下线",
        ),
        pytest.param(
            _mutate("entries[0].shutdown_date", "2027-06-30"),
            ("builtin_providers.gemini-aistudio.defaults.text", "default_model_retiring"),
            id="默认模型带关停日期",
        ),
        pytest.param(
            _mutate("entries[0].successor", "google/gemini-9-ultra"),
            ("entries[0].successor", "unknown_successor"),
            id="后继模型指向不存在的条目",
        ),
        pytest.param(
            _mutate("entries[3].successor", "google/gemini-3.1-pro"),
            ("entries[3].successor", "successor_media_type_mismatch"),
            id="后继模型的媒体类型与原条目不同",
        ),
        pytest.param(
            _mutate("builtin_providers.kling.models[1].min_app_version", "next"),
            ("builtin_providers.kling.models[1].min_app_version", "invalid_app_version"),
            id="最低应用版本格式不对",
        ),
        pytest.param(
            _mutate("builtin_providers.kling.models[0].endpoint", "kling-video-v9"),
            ("builtin_providers.kling.models[0].endpoint", "unknown_endpoint"),
            id="调用端点不在端点清单里",
        ),
        pytest.param(
            _mutate("builtin_providers.kling.models[0].endpoint", "kling-image"),
            ("builtin_providers.kling.models[0].endpoint", "endpoint_media_type_mismatch"),
            id="调用端点的媒体类型与条目不同",
        ),
        pytest.param(
            _mutate("builtin_providers.kling.models[0].endpoint", "ark-seedance-2"),
            ("builtin_providers.kling.models[0].endpoint", "endpoint_not_selectable"),
            id="调用端点不是该供应商的可选端点",
        ),
        pytest.param(
            lambda data: data["entries"][1]["facts"].update(reference_audio=True, max_reference_audio_count=0),
            ("entries[1].facts.max_reference_audio_count", "reference_audio_without_count"),
            id="接受参考音频但数量上限为 0",
        ),
        pytest.param(
            _mutate("entries[0].price.meters[0].rows[0].price", 10**400),
            ("entries[0].price.meters[0].rows[0].price", "negative_number"),
            id="单价超出浮点范围",
        ),
    ],
)
def test_build_side_rejects(mutation: Callable[[dict[str, Any]], None], expected: tuple[str, str]):
    data = catalog()
    mutation(data)

    assert _codes(data) == [expected]


def test_valid_output_token_scaled_bases_have_no_issues():
    data = catalog()
    data["entries"][2]["facts"].update(
        output_token_bases={"high": 48},
        output_token_scaled_bases={"low": {"base": 16, "multiplier": 4.2}, "medium": {"base": 24, "multiplier": 3.3}},
    )

    assert _codes(data) == []


def test_quality_with_both_plain_and_scaled_base_is_rejected():
    """同一质量档同时写进两张表时，不认识新字段的旧实例会按普通基数算出不带系数的低估值。"""
    data = catalog()
    data["entries"][2]["facts"].update(
        output_token_bases={"low": 16, "high": 48},
        output_token_scaled_bases={"low": {"base": 16, "multiplier": 4.2}},
    )

    assert _codes(data) == [("entries[2].facts.output_token_scaled_bases.low", "output_token_quality_overlap")]


def _priced_by_output_tokens(data: dict[str, Any]) -> dict[str, Any]:
    """把图片条目换成按输出图片 token 计价。"""
    entry = data["entries"][2]
    entry["price"]["meters"] = [{"meter": "image_output_tokens", "dims": [], "rows": [{"when": {}, "price": 30.0}]}]
    return entry


def test_image_priced_by_output_tokens_without_estimation_rules_is_rejected():
    """按输出图片 token 计价、却没有收录估算规则的图片条目，调用前估不出费用。"""
    data = catalog()
    _priced_by_output_tokens(data)

    assert _codes(data) == [("entries[2].facts", "missing_output_token_rules")]


@pytest.mark.parametrize(
    "rules",
    [
        pytest.param({"output_token_bases": {"medium": 48}}, id="普通基数"),
        pytest.param({"output_token_scaled_bases": {"low": {"base": 16, "multiplier": 4.2}}}, id="带系数基数"),
    ],
)
def test_image_priced_by_output_tokens_with_either_rule_table_has_no_issues(rules: dict[str, Any]):
    data = catalog()
    _priced_by_output_tokens(data)["facts"].update(rules)

    assert _codes(data) == []


def test_image_qualities_covered_by_either_rule_table_have_no_issues():
    data = catalog()
    data["entries"][2]["facts"].update(
        qualities=["low", "medium", "high"],
        output_token_bases={"high": 48},
        output_token_scaled_bases={"low": {"base": 16, "multiplier": 4.2}, "medium": {"base": 24, "multiplier": 3.3}},
    )

    assert _codes(data) == []


def test_image_qualities_are_unchecked_when_entry_has_no_output_token_rules():
    """按张计价的条目没有估算规则，也可以收录画质取值。"""
    data = catalog()
    data["entries"][2]["facts"]["qualities"] = ["standard", "hd"]

    assert _codes(data) == []


def test_image_quality_without_output_token_rule_is_rejected():
    """收录了估算规则时，每个可选画质都要能预估费用。"""
    data = catalog()
    data["entries"][2]["facts"].update(
        qualities=["low", "medium", "high", "max"],
        output_token_bases={"medium": 48, "high": 96},
        output_token_scaled_bases={"low": {"base": 16, "multiplier": 4.2}},
    )

    assert _codes(data) == [("entries[2].facts.qualities[3]", "quality_without_output_token_rule")]


def test_duplicate_image_quality_is_rejected():
    data = catalog()
    data["entries"][2]["facts"]["qualities"] = ["low", "high", "low"]

    assert _codes(data) == [("entries[2].facts.qualities[2]", "duplicate_value")]


@pytest.mark.parametrize("entry_index", [0, 1, 3], ids=["文本", "视频", "音频"])
def test_qualities_on_non_image_entry_is_an_unknown_field(entry_index: int):
    data = catalog()
    data["entries"][entry_index].setdefault("facts", {})["qualities"] = ["low", "high"]

    assert _codes(data) == [(f"entries[{entry_index}].facts.qualities", "unknown_field")]


def test_unknown_field_inside_scaled_base_is_reported():
    data = catalog()
    data["entries"][2]["facts"].update(
        output_token_scaled_bases={"low": {"base": 16, "multiplier": 4.2, "rounding": "ceil"}}
    )

    assert _codes(data) == [("entries[2].facts.output_token_scaled_bases.low.rounding", "unknown_field")]


@pytest.mark.parametrize(
    ("multiplier", "code"),
    [
        pytest.param(0, "invalid_positive_number", id="零"),
        pytest.param(-1, "invalid_positive_number", id="负数"),
        pytest.param(10**400, "invalid_positive_number", id="超出浮点范围"),
        pytest.param(True, "invalid_positive_number", id="布尔值"),
    ],
)
def test_invalid_multiplier_is_reported(multiplier: object, code: str):
    data = catalog()
    data["entries"][2]["facts"].update(output_token_scaled_bases={"low": {"base": 16, "multiplier": multiplier}})

    assert _codes(data) == [("entries[2].facts.output_token_scaled_bases.low.multiplier", code)]


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


def test_entry_with_lifecycle_and_valid_successor_passes():
    """后继按原条目的媒体类型对应条目，指向两栖模型时不必另写媒体类型；默认模型只带后继、没有关停日期时放行。"""
    data = catalog()
    successor = {**text_entry(), "name": "gemini-3.5-pro", "aliases": [], "release_date": "2026-09-01"}
    retiring = {
        **video_entry(),
        "name": "kling-v2-master",
        "shutdown_date": "2026-11-24",
        "successor": "kuaishou/kling-v3-omni",
    }
    data["entries"] += [successor, retiring]
    data["entries"][0].update(successor="google/gemini-3.5-pro")

    assert check_catalog(data) == []


def test_published_provider_model_requires_an_endpoint():
    data = catalog()
    data["builtin_providers"]["kling"]["models"][1].pop("endpoint")

    assert ("builtin_providers.kling.models[1].endpoint", "missing_endpoint") in _codes(data)
