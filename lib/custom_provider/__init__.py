"""自定义供应商模块。"""

import re

from lib.backends.providers import CUSTOM_PROVIDER_PREFIX as CUSTOM_PROVIDER_PREFIX
from lib.backends.providers import is_custom_provider as is_custom_provider
from lib.backends.providers import parse_provider_id as parse_provider_id

CUSTOM_ENDPOINT_KEY_PREFIX = "ce-"


def make_provider_id(db_id: int) -> str:
    """构造自定义供应商的 provider_id 字符串，如 'custom-3'。"""
    return f"{CUSTOM_PROVIDER_PREFIX}{db_id}"


def make_endpoint_key(db_id: int) -> str:
    """构造自定义调用端点的 endpoint 键，如 'ce-3'。键由系统分配，分享文件不带键。"""
    return f"{CUSTOM_ENDPOINT_KEY_PREFIX}{db_id}"


_CUSTOM_ENDPOINT_KEY = re.compile(rf"^{re.escape(CUSTOM_ENDPOINT_KEY_PREFIX)}([1-9][0-9]*)\Z")


def parse_endpoint_key(endpoint: str) -> int:
    """从 'ce-3' 格式的 endpoint 键提取数据库 ID。

    只认规范形：前缀 + 无前导零的正整数。键的字面量即唯一规范形，删除路径按字面量比对
    模型行的 endpoint 列，解析侧放宽会让两条路径对「同一端点」的判定分裂。

    Raises:
        ValueError: 非规范形（前导零、正负号、空白、下划线分隔、非 ASCII 数字等）
    """
    match = _CUSTOM_ENDPOINT_KEY.match(endpoint)
    if match is None:
        raise ValueError(f"malformed custom endpoint key: {endpoint!r}")
    return int(match.group(1))


def is_custom_endpoint(endpoint: str) -> bool:
    """判断是否为自定义调用端点的键。"""
    return endpoint.startswith(CUSTOM_ENDPOINT_KEY_PREFIX)
