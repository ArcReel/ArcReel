"""端点测试的三种模式，以及每种定义 ``kind`` 支持哪几种。

三模式对同一份定义回答三个层层递进的问题，但不是每种 kind 都答得上：能不能「验证响应」取决于
这种 kind 是否有用户可配的取值路径可验——没有可配路径时，验的只是一段固定代码，给不出任何信息
（``docs/adr/0081``）。不支持不是错误定义，故不走定义诊断，由调用方转成一条结构化的拒绝。

本模块只有名录与一个纯判定，不引运行时——三个 HTTP 入口与服务层共用同一份支持矩阵，才不会出现
某个入口放行、执行到一半才发现这种 kind 根本没有实现。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from lib.custom_provider.endpoint_definition import DECLARATIVE_KIND


class EndpointTestMode(StrEnum):
    """端点测试的三种模式：预览请求、验证响应、测试连接。取值只作模式标识，不外发。"""

    PREVIEW_REQUEST = "preview-request"
    CHECK_RESPONSE = "check-response"
    TRIAL_RUN = "trial-run"


#: ``kind`` → 该 kind 支持的模式。名录外的 kind 一种模式都不支持。
_MODES_BY_KIND: Mapping[str, frozenset[EndpointTestMode]] = {
    DECLARATIVE_KIND: frozenset(EndpointTestMode),
}


def supports_test_mode(kind: str, mode: EndpointTestMode) -> bool:
    """这种 ``kind`` 的定义能不能跑这种模式。"""
    return mode in _MODES_BY_KIND.get(kind, frozenset())
