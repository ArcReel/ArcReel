"""自定义供应商模块。"""

import re

CUSTOM_PROVIDER_PREFIX = "custom-"

#: 用户自定义调用端点的键前缀（``ce-<id>``）。内置端点键不得占用该前缀——两套键共享模型行的
#: ``endpoint`` 列与端点查表入口，前缀是唯一的分流依据。
CUSTOM_ENDPOINT_KEY_PREFIX = "ce-"


def make_provider_id(db_id: int) -> str:
    """构造自定义供应商的 provider_id 字符串，如 'custom-3'。"""
    return f"{CUSTOM_PROVIDER_PREFIX}{db_id}"


def parse_provider_id(provider_id: str) -> int:
    """从 'custom-3' 格式的 provider_id 提取数据库 ID。

    Raises:
        ValueError: 如果格式不正确
    """
    return int(provider_id.removeprefix(CUSTOM_PROVIDER_PREFIX))


def is_custom_provider(provider_id: str) -> bool:
    """判断是否为自定义供应商的 provider_id。"""
    return provider_id.startswith(CUSTOM_PROVIDER_PREFIX)


#: 自定义调用端点键的前缀。内置注册表的键不得以此开头（``lib.custom_provider.endpoints``
#: 在导入期校验），两个命名空间因此按前缀划分、永不重叠，查表顺序由前缀唯一决定。
CUSTOM_ENDPOINT_KEY_PREFIX = "ce-"


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
