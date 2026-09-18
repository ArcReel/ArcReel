"""每种定义 ``kind`` 在端点测试里的实现：预览怎么渲、测试连接怎么装配、要哪几样凭证。

与 :mod:`.modes` 的分工：那里是「支不支持」的纯名录（不引运行时，三个 HTTP 入口共用同一份支持
矩阵），这里是「支持的那些怎么做」。两张表按 kind 对齐，
``test_the_support_matrix_and_the_implementations_cover_the_same_kinds`` 守住它们不分叉。

router 因此不认识任何一种 kind：它按定义里的 ``kind`` 取出这一份实现，剩下的流程两种 kind 完全
一致——凭证归一、素材落盘、记账、写盘与取消都不该因为端点是哪一种而各写一遍。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from lib.custom_provider.endpoint_definition import COMFYUI_KIND, DECLARATIVE_KIND
from lib.custom_provider.endpoints import declarative_requires_api_key, declarative_requires_base_url

from .comfyui import comfyui_credential_needs, comfyui_target, preview_comfyui_request
from .inputs import EndpointTestAssets, EndpointTestCredentials, EndpointTestParameters
from .preview import RequestPreview, preview_request
from .trial_run import TrialRunTarget, declarative_target

#: 测试连接只构造视频请求，图像端点跑不了；预览请求与媒体类型无关，两种端点都给得出。
TRIAL_RUN_IMAGE_UNSUPPORTED = "endpoint_test_trial_run_image_unsupported"


class PreviewFn(Protocol):
    """渲染一次预览请求。"""

    def __call__(
        self,
        definition: Mapping[str, Any],
        parameters: EndpointTestParameters,
        *,
        credentials: EndpointTestCredentials | None = None,
        assets: EndpointTestAssets | None = None,
        placeholder_missing_assets: bool = True,
    ) -> RequestPreview: ...


class TrialTargetFn(Protocol):
    """按一份内联定义与凭证装配测试连接的目标。"""

    def __call__(
        self,
        definition: Mapping[str, Any],
        credentials: EndpointTestCredentials,
        parameters: EndpointTestParameters,
        *,
        provider: str | None = None,
    ) -> TrialRunTarget: ...


@dataclass(frozen=True)
class KindTestSupport:
    """一种 kind 的端点测试实现。"""

    preview: PreviewFn
    #: 这份定义要 ``base_url`` / ``api_key`` 吗。两者皆否的定义不必带凭证即可调用。
    credential_needs: Callable[[Mapping[str, Any]], tuple[bool, bool]]
    trial_target: TrialTargetFn
    #: 测试连接的结果体里放不放一段渲染后的提交请求（它同时充当提交前的渲染闸）。
    #: ComfyUI 不放：实发 workflow 的种子与素材引用名要到提交那一刻才定下来。
    trial_run_request_preview: bool


def _declarative_credential_needs(definition: Mapping[str, Any]) -> tuple[bool, bool]:
    return declarative_requires_base_url(definition), declarative_requires_api_key(definition)


_SUPPORT_BY_KIND: Mapping[str, KindTestSupport] = {
    DECLARATIVE_KIND: KindTestSupport(
        preview=preview_request,
        credential_needs=_declarative_credential_needs,
        trial_target=declarative_target,
        trial_run_request_preview=True,
    ),
    COMFYUI_KIND: KindTestSupport(
        preview=preview_comfyui_request,
        credential_needs=comfyui_credential_needs,
        trial_target=comfyui_target,
        trial_run_request_preview=False,
    ),
}


#: 有端点测试实现的全部 kind。与 :data:`.modes.TESTABLE_KINDS` 一致。
SUPPORTED_KINDS = frozenset(_SUPPORT_BY_KIND)


def support_for_kind(kind: str) -> KindTestSupport:
    """这种 ``kind`` 的端点测试实现。

    Raises:
        KeyError: 名录外的 kind。调用方先过 :func:`~.modes.supports_test_mode` 的结构化拒绝，
            走到这里的 kind 必然有实现。
    """
    return _SUPPORT_BY_KIND[kind]


def trial_run_refusal(definition: Mapping[str, Any]) -> str | None:
    """这份定义能不能跑测试连接；不能时返回一条 i18n key。

    与「这种 kind 支不支持测试连接」分开：这一条判的是定义自身（ComfyUI 端点的媒体类型写在定义
    里，同一种 kind 的视频端点跑得了、图像端点跑不了），模式矩阵按 kind 答不了它。
    """
    if str(definition.get("kind")) == COMFYUI_KIND and str(definition.get("media_type")) != "video":
        return TRIAL_RUN_IMAGE_UNSUPPORTED
    return None
