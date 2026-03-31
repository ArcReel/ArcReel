"""自定义供应商模块。"""

CUSTOM_PROVIDER_PREFIX = "custom-"


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
