"""端点测试的模式支持矩阵：哪种定义 kind 能跑哪几种模式，以及支持的模式有没有实现。"""

from __future__ import annotations

import pytest

from lib.custom_provider.endpoint_definition import COMFYUI_KIND, DECLARATIVE_KIND
from lib.custom_provider.endpoint_test import SUPPORTED_KINDS, TESTABLE_KINDS, EndpointTestMode, supports_test_mode


@pytest.mark.parametrize("mode", list(EndpointTestMode))
def test_declarative_supports_every_mode(mode: EndpointTestMode):
    assert supports_test_mode(DECLARATIVE_KIND, mode)


@pytest.mark.parametrize("mode", [EndpointTestMode.PREVIEW_REQUEST, EndpointTestMode.TRIAL_RUN])
def test_comfyui_supports_previewing_and_trial_running(mode: EndpointTestMode):
    assert supports_test_mode(COMFYUI_KIND, mode)


def test_comfyui_does_not_support_checking_a_response():
    """产物提取读的是固定三个键，没有用户可配的取值路径可验（ADR 0081）。"""
    assert not supports_test_mode(COMFYUI_KIND, EndpointTestMode.CHECK_RESPONSE)


@pytest.mark.parametrize("mode", list(EndpointTestMode))
def test_a_kind_without_an_implementation_supports_nothing(mode: EndpointTestMode):
    """名录外的 kind 一种模式都不支持：调用方据此结构化拒绝，而不是执行到一半才发现没有实现。"""
    assert not supports_test_mode("not-a-kind", mode)


def test_modes_are_the_three_documented_ones():
    assert {mode.value for mode in EndpointTestMode} == {"preview-request", "check-response", "trial-run"}


def test_the_support_matrix_and_the_implementations_cover_the_same_kinds():
    """两张按 kind 的表不许分叉：矩阵放行的 kind 必须有实现，有实现的 kind 必须在矩阵里。"""
    assert TESTABLE_KINDS == SUPPORTED_KINDS
