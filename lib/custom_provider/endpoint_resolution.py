"""按端点键分流的 spec 解析：内置查表，``ce-`` 前缀读库现构造。

``get_endpoint_spec`` 保持同步纯查表（内置注册表是 import 期就位的常量），需要同时看到两个
命名空间的调用方改走本模块的 :func:`resolve_endpoint_spec`。顺序完全由前缀决定，不存在「先自
定义后内置」的兜底——两个命名空间同域但永不重叠（内置键禁用 ``ce-`` 前缀，见
``lib.custom_provider.endpoints``）。

定义本体是唯一真相源，spec 是它的运行时投影：显示名取 ``meta.name``、能力取 ``capabilities``、
请求形态取 ``submit``。投影本身不在本模块——用户定义与随版定义是同一种东西，共用
``endpoints.declarative_endpoint_spec`` 那一份实现，本模块只管「读库取行 → 投影」。装载走 async
（读库）、构造是纯函数，两段分明（``docs/adr/0039``）；不做启动时全量加载进内存注册表——定义原地
更新须立即对新任务生效。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lib.custom_provider import is_custom_endpoint, make_endpoint_key, parse_endpoint_key
from lib.custom_provider.builtin_definitions import DECLARATIVE_MEDIA_TYPE
from lib.custom_provider.endpoints import EndpointSpec, declarative_endpoint_spec, get_endpoint_spec

if TYPE_CHECKING:
    from lib.db.models.custom_endpoint import CustomEndpoint


@dataclass(frozen=True)
class MirrorColumns:
    """写入时由定义派生的只读镜像列，供 DB 层不解析 JSON 就能过滤与展示。"""

    kind: str
    schema_version: str
    media_type: str
    display_name: str


def derive_mirror_columns(definition: Mapping[str, Any]) -> MirrorColumns:
    """从一份**已过校验**的定义派生镜像列。

    校验保证了 ``kind`` / ``schema_version`` / ``meta.name`` 存在且是字符串，故此处直接取值：
    在未过校验的定义上派生只会把残缺结构写进库。
    """
    meta: Mapping[str, Any] = definition["meta"]
    return MirrorColumns(
        kind=str(definition["kind"]),
        schema_version=str(definition["schema_version"]),
        media_type=DECLARATIVE_MEDIA_TYPE,
        display_name=str(meta["name"]),
    )


def endpoint_spec_from_row(row: CustomEndpoint) -> EndpointSpec:
    """按库里的行构造 spec；键由行的自增 id 派生，来源标为 ``custom``。"""
    return declarative_endpoint_spec(make_endpoint_key(row.id), row.definition, source="custom")


async def resolve_endpoint_spec(
    endpoint: str,
    get_custom_endpoint: Callable[[int], Awaitable[CustomEndpoint | None]],
) -> EndpointSpec:
    """按端点键取 spec：``ce-`` 前缀读 ``custom_endpoint`` 表，其余委托内置查表。

    Raises:
        ValueError: 键不存在（内置注册表查无此键，或自定义端点已被删除）
    """
    if not is_custom_endpoint(endpoint):
        return get_endpoint_spec(endpoint)

    try:
        endpoint_id = parse_endpoint_key(endpoint)
    except ValueError:
        raise ValueError(f"unknown endpoint: {endpoint!r}") from None
    row = await get_custom_endpoint(endpoint_id)
    if row is None:
        raise ValueError(f"unknown endpoint: {endpoint!r}")
    return endpoint_spec_from_row(row)
