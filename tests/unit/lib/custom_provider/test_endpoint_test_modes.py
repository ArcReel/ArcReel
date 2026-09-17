"""端点测试的模式支持矩阵：哪种定义 kind 能跑哪几种模式。"""

from __future__ import annotations

import pytest

from lib.custom_provider.endpoint_definition import DECLARATIVE_KIND
from lib.custom_provider.endpoint_test import EndpointTestMode, supports_test_mode


@pytest.mark.parametrize("mode", list(EndpointTestMode))
def test_declarative_supports_every_mode(mode: EndpointTestMode):
    assert supports_test_mode(DECLARATIVE_KIND, mode)


@pytest.mark.parametrize("mode", list(EndpointTestMode))
def test_a_kind_without_an_implementation_supports_nothing(mode: EndpointTestMode):
    """名录外的 kind 一种模式都不支持：调用方据此结构化拒绝，而不是执行到一半才发现没有实现。"""
    assert not supports_test_mode("comfyui", mode)


def test_modes_are_the_three_documented_ones():
    assert {mode.value for mode in EndpointTestMode} == {"preview-request", "check-response", "trial-run"}
