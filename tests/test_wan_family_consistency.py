"""万相 / happyhorse 家族判定的组合矩阵回归测试。

`lib.video_backends.dashscope.classify_wan_model` 是家族归属、分隔符归一化、标识符边界、
image-to-video 续接语法、videoedit 模态排除的唯一判定入口；端点路由
（`lib.custom_provider.endpoints.infer_endpoint`）、能力档
（`DashScopeVideoBackend.video_capabilities_for_model`）、时长档
（`lib.custom_provider.duration_presets.infer_supported_durations`）三处判定点都只消费其结论。
本文件覆盖分隔符 × 版本 × 模态 × 大小写 × 装饰前缀等轴的组合，逐条比对三处判定点的期望值，
并对可解析出具体模态（t2v/i2v/r2v）的家族成员做一条通用一致性断言：命中原生路由必有非默认
能力档，反之亦然——三处判定点各自维护一份正则时容易出现宽度不一致，产生这类互斥组合；
逐点单测无法发现，只有跨判定点的组合校验能发现。
"""

from __future__ import annotations

import itertools

import pytest

from lib.custom_provider.duration_presets import infer_supported_durations
from lib.custom_provider.endpoints import infer_endpoint
from lib.video_backends.dashscope import _DEFAULT_PROFILE, DashScopeVideoBackend

pytestmark = pytest.mark.unit

_PREFIX_SEPS = ["-", "_", ""]  # wan[-_]?<version>
_MODALITY_SEPS = ["-", "_"]  # <version>[-_]<modality>
_CASE_FNS = [str.lower, str.upper, str.title]


def _build(prefix_sep: str, version: str, modality_sep: str, modality: str) -> str:
    return f"wan{prefix_sep}{version}{modality_sep}{modality}"


def _wan27_ids() -> list[str]:
    return [id_ for id_, _modality in _wan27_ids_with_modality()]


def _wan27_ids_with_modality() -> list[tuple[str, str]]:
    return [
        (case_fn(_build(prefix_sep, "2.7", modality_sep, modality)), modality)
        for prefix_sep, modality_sep, modality, case_fn in itertools.product(
            _PREFIX_SEPS, _MODALITY_SEPS, ["t2v", "i2v", "r2v"], _CASE_FNS
        )
    ]


@pytest.mark.parametrize(("model_id", "modality"), _wan27_ids_with_modality())
def test_wan27_alias_routes_and_profiles_consistently(model_id: str, modality: str) -> None:
    """wan2.7 t2v/i2v/r2v：连字符/下划线前缀 × 连字符/下划线模态分隔符 × 大小写全组合。"""
    assert infer_endpoint(model_id, "openai") == "dashscope-async-video"
    caps = DashScopeVideoBackend.video_capabilities_for_model(model_id)
    assert caps.first_frame is (modality in ("i2v", "r2v"))
    assert caps.max_prompt_chars == 5000
    assert infer_supported_durations(model_id) == list(range(2, 16))


@pytest.mark.parametrize(
    "modality_word",
    ["image-to-video", "image2video", "image_to_video"],
)
@pytest.mark.parametrize("prefix_sep", _PREFIX_SEPS)
@pytest.mark.parametrize("case_fn", _CASE_FNS)
def test_wan27_image_to_video_alias_normalizes_to_i2v(prefix_sep: str, modality_word: str, case_fn: type[str]) -> None:
    model_id = case_fn(f"wan{prefix_sep}2.7-{modality_word}")
    assert infer_endpoint(model_id, "openai") == "dashscope-async-video"
    caps = DashScopeVideoBackend.video_capabilities_for_model(model_id)
    assert caps.first_frame is True
    assert caps.max_reference_images == 0  # 与 r2v 档区分


@pytest.mark.parametrize("prefix_sep", _PREFIX_SEPS)
@pytest.mark.parametrize("case_fn", _CASE_FNS)
def test_wan27_pure_image_variant_is_not_native_video_route(prefix_sep: str, case_fn: type[str]) -> None:
    """wan2.7-image（无 image-to-video 续接语法）是图像变体，不落原生视频端点。"""
    model_id = case_fn(f"wan{prefix_sep}2.7-image")
    assert infer_endpoint(model_id, "openai") == "openai-images"


def _wan3_ids() -> list[str]:
    return [
        case_fn(_build(prefix_sep, "3", modality_sep, modality))
        for prefix_sep, modality_sep, modality, case_fn in itertools.product(
            _PREFIX_SEPS, _MODALITY_SEPS, ["turbo", "video"], _CASE_FNS
        )
    ]


@pytest.mark.parametrize("model_id", _wan3_ids())
def test_wan3_alias_routes_and_durations_consistently(model_id: str) -> None:
    assert infer_endpoint(model_id, "openai") == "dashscope-async-video"
    assert infer_supported_durations(model_id) == list(range(2, 31))


@pytest.mark.parametrize("version", ["2.1", "2.2"])
@pytest.mark.parametrize("modality", ["t2v", "i2v", "r2v"])
@pytest.mark.parametrize("prefix_sep", _PREFIX_SEPS)
@pytest.mark.parametrize("case_fn", _CASE_FNS)
def test_wan2x_non_27_dash_form_stays_generic_dot_form_stays_native(
    prefix_sep: str, modality: str, version: str, case_fn: type[str]
) -> None:
    """万相 2.1/2.2：连字符/下划线前缀落通用视频端点，点号形态走原生（本后端固定请求
    video-generation/video-synthesis 端点，是否收窄该判定需要供应商 API 事实与产品判断，
    见 classify_wan_model 的说明）。"""
    model_id = case_fn(_build(prefix_sep, version, "-", modality))
    expected_endpoint = "dashscope-async-video" if prefix_sep == "" else "openai-video"
    assert infer_endpoint(model_id, "openai") == expected_endpoint
    assert infer_supported_durations(model_id) == [4, 5]


@pytest.mark.parametrize(
    ("model_id", "expected_endpoint", "expected_first_frame"),
    [
        ("proxy/wan-2.7-r2v", "dashscope-async-video", True),
        ("proxy/wan_2.7_t2v", "dashscope-async-video", False),
        ("vendor-wan2.7-i2v-0715", "dashscope-async-video", True),
    ],
)
def test_decorated_prefix_suffix_still_resolves(
    model_id: str, expected_endpoint: str, expected_first_frame: bool
) -> None:
    """代理中转常见的前后缀装饰不影响判定。"""
    assert infer_endpoint(model_id, "openai") == expected_endpoint
    caps = DashScopeVideoBackend.video_capabilities_for_model(model_id)
    assert caps.first_frame is expected_first_frame


@pytest.mark.parametrize(
    "model_id",
    ["swan2.7-r2v", "vendorwan2.7-t2v", "wan20-i2v", "wan27-r2v", "swan2.1-kf2v"],
)
def test_boundary_false_positives_are_rejected(model_id: str) -> None:
    """含 wan2/wan3 子串但并非该家族的型号名不得被误判——两侧标识符边界。"""
    assert infer_endpoint(model_id, "openai") == "openai-video"
    caps = DashScopeVideoBackend.video_capabilities_for_model(model_id)
    assert caps.max_reference_images == 0
    assert caps.max_prompt_chars is None


@pytest.mark.parametrize("model_id", ["swan2.7-image", "vendorwan2.7-image"])
def test_wan_substring_image_variant_routes_to_image_endpoint_despite_rejected_family(
    model_id: str,
) -> None:
    """不满足家族标识符边界的 id（如 "swan2.7-image"）仍含 "wan" 子串，会被通用 _VIDEO_PATTERN
    命中——排除到图像端点的判定不能拿严格家族边界做门槛，否则会被误推到 openai-video。"""
    assert infer_endpoint(model_id, "openai") == "openai-images"


def test_wan27_videoedit_excluded_from_family_duration_preset() -> None:
    """wan2.7-videoedit 本后端未实现该模态的请求构造，时长不套用 t2v/i2v/r2v 家族档
    （落到通用预设，而非家族专属的 2-15s 全档）。"""
    assert infer_supported_durations("wan2.7-videoedit") != list(range(2, 16))
    assert infer_supported_durations("wan-2.7-videoedit") != list(range(2, 16))


@pytest.mark.parametrize(
    ("model_id", "expected_max_reference_images"),
    [
        ("proxy/happyhorse-1.0-r2v", 9),
        ("happyhorse-1.0-r2v-0715", 9),
        ("myhappyhorse-1.0-r2v", 0),  # 标识符边界拒绝：非 happyhorse 家族
    ],
)
def test_happyhorse_boundary_and_decoration(model_id: str, expected_max_reference_images: int) -> None:
    caps = DashScopeVideoBackend.video_capabilities_for_model(model_id)
    assert caps.max_reference_images == expected_max_reference_images


@pytest.mark.parametrize(
    "model_id",
    [
        *_wan27_ids(),
        *_wan3_ids(),
        "happyhorse-1.0-t2v",
        "happyhorse-1.1-i2v",
        "happyhorse-1.0-r2v",
    ],
)
def test_native_route_and_non_default_profile_agree(model_id: str) -> None:
    """一致性断言：家族成员且模态可解析（t2v/i2v/r2v）时，命中原生路由必有非默认能力档，反之亦然。

    该断言只覆盖模态可解析的成员——裸家族名（如 "happyhorse"）、wan2.7-videoedit、非 2.7 的点号形态
    2.x（"wan2.1-*"）均因模态/协议未确权而按设计落默认档，不受本断言约束（见 classify_wan_model
    与 _profile_for_model 的说明）。
    """
    routed_native = infer_endpoint(model_id, "openai") == "dashscope-async-video"
    caps = DashScopeVideoBackend.video_capabilities_for_model(model_id)
    # 用对象身份而非字段相等判定"是否解析出已知档"——happyhorse-i2v 的声明字段值恰好与
    # VideoCapabilities() 的默认值逐项相同（first_frame 默认即 True），字段级比较会漏判。
    # `_profile_for_model` 命中已知 key 时返回 `_MODEL_PROFILES` 里的原对象，未命中时返回
    # `_DEFAULT_PROFILE` 单例，两者身份不同，可靠区分"解析成功"与"落回默认"。
    has_known_profile = caps is not _DEFAULT_PROFILE
    assert routed_native is True
    assert has_known_profile is True
