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


@pytest.mark.parametrize(
    ("model_id", "expected_endpoint"),
    [
        # WAN_DOT_FORM_PATTERN 右边界：紧跟字母不算 2.x 点号形态，落通用视频端点（裸 "wan" 子串
        # 仍命中 _VIDEO_PATTERN）。
        ("wan2.7foo-r2v", "openai-video"),
        # HAPPYHORSE_PATTERN 右边界：紧跟字母不算 happyhorse 家族，不含视频/图像关键字，落默认
        # 文本端点。
        ("happyhorsefoo-t2v", "openai-chat"),
        # WAN_IMAGE_TO_VIDEO_PATTERN 左边界：紧邻字母不算续接语法，按含 image 语义的图像变体处理。
        ("wan-2.7-fooimage-to-video", "openai-images"),
        # WAN_IMAGE_TO_VIDEO_PATTERN 右边界：同上。
        ("wan-2.7-image-to-videofoo", "openai-images"),
    ],
)
def test_adjacent_letters_do_not_qualify_as_classification_tokens(model_id: str, expected_endpoint: str) -> None:
    """分类 token 两侧标识符边界须完整：紧邻字母/数字的相似子串不应被误判命中。"""
    assert infer_endpoint(model_id, "openai") == expected_endpoint
    caps = DashScopeVideoBackend.video_capabilities_for_model(model_id)
    assert caps is _DEFAULT_PROFILE


@pytest.mark.parametrize("model_id", ["swan2.7-image", "vendorwan2.7-image"])
def test_wan_substring_image_variant_routes_to_image_endpoint_despite_rejected_family(
    model_id: str,
) -> None:
    """不满足家族标识符边界的 id（如 "swan2.7-image"）仍含 "wan" 子串，会被通用 _VIDEO_PATTERN
    命中——排除到图像端点的判定不能拿严格家族边界做门槛，否则会被误推到 openai-video。"""
    assert infer_endpoint(model_id, "openai") == "openai-images"


@pytest.mark.parametrize("model_id", ["image-proxy/wan-2.7-i2v", "proxy-image/wan_2.7-r2v"])
def test_wan27_known_modality_not_downgraded_by_unrelated_image_decoration(model_id: str) -> None:
    """wan2.7 已解析出已知 t2v/i2v/r2v profile 时，id 别处（如代理命名空间前缀）另含无关 "image"
    子串不应被误判成图像变体——已知 profile 本身已确立视频语义，须优先于笼统 image 子串判定。"""
    assert infer_endpoint(model_id, "openai") == "dashscope-async-video"


@pytest.mark.parametrize("model_id", ["wan3.0-image-edit", "wan-3-turbo-image-preview"])
def test_wan3_image_variant_still_routes_to_image_endpoint(model_id: str) -> None:
    """wan3 只有单一 profile key，has_known_modality 恒真，不区分 t2v/i2v/r2v 与 image-edit 等真
    图像别名——上一条已知 modality 豁免不套用到 wan3，真图像别名仍须正确落图像端点。"""
    assert infer_endpoint(model_id, "openai") == "openai-images"


def test_wan27_videoedit_excluded_from_family_duration_preset() -> None:
    """wan2.7-videoedit 本后端未实现该模态的请求构造，时长不套用 t2v/i2v/r2v 家族档
    （落到通用预设，而非家族专属的 2-15s 全档）。"""
    assert infer_supported_durations("wan2.7-videoedit") != list(range(2, 16))


def test_wan27_videoedit_excluded_even_alongside_recognized_modality_token() -> None:
    """ "wan2.7-i2v-videoedit" 同时含已知 profile token（i2v）与 videoedit 标记：videoedit
    未实现请求构造这一事实优先于已知 token 命中，不能被后者掩盖而误放行原生路由。"""
    assert infer_endpoint("wan2.7-i2v-videoedit", "openai") == "openai-video"
    assert infer_supported_durations("wan2.7-i2v-videoedit") != list(range(2, 16))


@pytest.mark.parametrize("model_id", ["proxy-videoeditor/wan2.7-i2v", "proxy-videoedit-service/wan_2.7-r2v"])
def test_videoedit_token_requires_identifier_boundary(model_id: str) -> None:
    """ "videoedit" 判定须按标识符边界匹配："videoeditor" 一类无关词形不应误判命中；真正的
    "videoedit" 装饰前缀（如 "-service" 后缀分隔）仍要正确命中并排除出原生路由。"""
    is_editor_typo = "videoeditor" in model_id
    assert infer_endpoint(model_id, "openai") == ("dashscope-async-video" if is_editor_typo else "openai-video")


@pytest.mark.parametrize("model_id", ["proxy-videoedit/wan3-turbo", "wan-3-turbo-videoedit"])
def test_videoedit_exclusion_scoped_to_wan27_only(model_id: str) -> None:
    """videoedit 排除只对 wan2.7 家族生效——wan3 的 id 即便含 "videoedit" 子串（装饰前缀或
    其他来源），也不应被误排除出原生路由，该模态的能力欠缺只记录在 wan2.7 家族下。"""
    assert infer_endpoint(model_id, "openai") == "dashscope-async-video"
    assert infer_supported_durations(model_id) == list(range(2, 31))


@pytest.mark.parametrize(
    ("model_id", "expected_endpoint"),
    [
        ("wan-2.7-v2v", "openai-video"),
        ("wan_2.7-foo", "openai-video"),
        ("wan-2.7-s2v-0715", "openai-video"),
    ],
)
def test_wan27_unrecognized_modality_excluded_from_native_route_and_family_duration(
    model_id: str, expected_endpoint: str
) -> None:
    """wan2.7 家族命中但模态未实现请求构造（非 t2v/i2v/r2v，videoedit 之外的其余未知后缀）时，
    不落原生端点、时长也不套用家族档——与 videoedit 走同一条排除路径。"""
    assert infer_endpoint(model_id, "openai") == expected_endpoint
    assert infer_supported_durations(model_id) != list(range(2, 16))


@pytest.mark.parametrize("model_id", ["wan2.6-image-to-video", "wan2.1-image-to-video"])
def test_wan2x_dot_image_to_video_has_no_registered_capability_falls_back_to_generic(model_id: str) -> None:
    """wan2x_dot（2.7 以外的点号形态）没有登记任何 VideoCapabilities；image-to-video 续接语法
    命中时若仍放行原生路由，会静默拿到 _DEFAULT_PROFILE（恰好 first_frame=True，掩盖问题）。
    没有已验证能力/请求 schema 的 id 排除出原生路由，落通用视频端点。"""
    assert infer_endpoint(model_id, "openai") == "openai-video"


@pytest.mark.parametrize("model_id", ["wan-2.2-image-to-video", "wan_2.6-image2video"])
def test_image_to_video_syntax_recognized_even_when_family_boundary_unmet(model_id: str) -> None:
    """image-to-video 续接语法的识别不依赖家族严格边界：家族分隔符（连字符隔开 wan 与版本号）
    未满足点号形态边界、classify_wan_model 判定 family=None 时，续接语法信息仍须原样带出，
    不能被笼统 image 判定误吞成图像端点。"""
    assert infer_endpoint(model_id, "openai") == "openai-video"


@pytest.mark.parametrize("model_id", ["wan-2.2-s2v", "wan_2.6-s2v", "vendorwan2.7-s2v"])
def test_wan_substring_s2v_excluded_from_minimax_routing_even_without_family_match(model_id: str) -> None:
    """裸 "s2v" 排除 MiniMax 路由的判定同样不能拿家族严格边界做门槛：这些 id 含 "wan" 子串但
    家族边界未满足（family=None），仍应落通用视频端点而非被误吞成 MiniMax S2V 协议。"""
    assert infer_endpoint(model_id, "openai") == "openai-video"


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
