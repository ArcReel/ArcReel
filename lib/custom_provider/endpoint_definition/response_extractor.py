"""声明式调用端点的响应 JSONPath 提取与状态映射。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from jsonpath_rfc9535 import JSONPathError, find

from lib.video_backends.base import ProviderJobStatus, normalize_provider_status

from .jsonpath_subset import parse_json_path

_DECLARATIVE_STATUSES = frozenset(
    {
        ProviderJobStatus.QUEUED,
        ProviderJobStatus.RUNNING,
        ProviderJobStatus.SUCCEEDED,
        ProviderJobStatus.FAILED,
    }
)


class JsonPathEvaluationError(ValueError):
    """运行时 JSONPath 实现无法对已通过子集闸门的路径求值。"""

    def __init__(self, path: str, cause: Exception) -> None:
        super().__init__(f"JSONPath 求值失败：{path}: {cause}")


def extract_value(spec: object, response_body: object) -> object | None:
    """按优先级返回第一项可接受的首个命中；全部无命中时返回 ``None``。"""
    paths, accept = normalize_extract_spec(spec)
    for item in paths:
        if isinstance(item, str):
            hit = _first_acceptable(item, response_body, accept)
        elif isinstance(item, Mapping):
            hit = _decode_then(item, response_body, accept)
        else:
            raise TypeError("提取路径项必须是字符串或对象")
        if hit is not None:
            return hit
    return None


def map_status(raw: object, status_map: Mapping[str, str] | None = None) -> ProviderJobStatus:
    """供应商状态经显式字典映射到声明式支持的四档；未知值继续轮询。

    ``status_map`` 未命中时回落到内置同义词表 ``normalize_provider_status``，
    它把各家常见写法（``IN_PROGRESS`` / ``WAITING_GPU`` 等）归一到同一批状态，
    完全不认识的值归为 ``RUNNING`` 以继续轮询而非误判失败；``EXPIRED`` 不在声明式四档内，折进 ``FAILED``。
    """
    key = str(raw).strip().lower() if raw is not None else ""
    normalized_map = {str(source).strip().lower(): target for source, target in (status_map or {}).items()}
    if key in normalized_map:
        mapped = ProviderJobStatus(normalized_map[key])
        if mapped not in _DECLARATIVE_STATUSES:
            raise ValueError(f"声明式状态不支持：{mapped.value}")
        return mapped
    fallback = normalize_provider_status(raw)
    return ProviderJobStatus.FAILED if fallback is ProviderJobStatus.EXPIRED else fallback


def normalize_extract_spec(spec: object) -> tuple[Sequence[object], str]:
    """把提取规则拆成「路径项序列 + 可接受类型」。

    公开而非私有：验证响应要逐条报告命中与否，那份逐条求值必须与 :func:`extract_value` 读同一
    份归一化结果——两处各自解析简写与对象两种形态，简写默认的 ``accept`` 一旦漂移，报告里的
    「命中」就会与运行时实际取到的值不同。
    """
    if isinstance(spec, list):
        return spec, "string"
    if not isinstance(spec, Mapping):
        raise TypeError("提取规则必须是路径数组或对象")
    paths = spec.get("paths")
    if not isinstance(paths, list):
        raise TypeError("提取规则缺少 paths 数组")
    accept = spec.get("accept", "string")
    if accept not in {"string", "scalar"}:
        raise ValueError(f"未知 accept：{accept}")
    return paths, accept


def _first_acceptable(path: str, target: Any, accept: str) -> object | None:
    parse_json_path(path)
    try:
        for node in find(path, target):
            if _acceptable(node.value, accept):
                return node.value
    except (ArithmeticError, JSONPathError) as exc:
        raise JsonPathEvaluationError(path, exc) from exc
    return None


def _decode_then(item: Mapping[str, object], response_body: object, accept: str) -> object | None:
    encoded = _first_acceptable(str(item["path"]), response_body, "string")
    if not isinstance(encoded, str):
        return None
    try:
        decoded = json.loads(encoded)
    except (TypeError, ValueError):
        return None
    then = item.get("then", ["$"])
    if not isinstance(then, list):
        return None
    for path in then:
        hit = _first_acceptable(str(path), decoded, accept)
        if hit is not None:
            return hit
    return None


def _acceptable(value: object, accept: str) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return accept == "scalar" and isinstance(value, (int, float, bool))
