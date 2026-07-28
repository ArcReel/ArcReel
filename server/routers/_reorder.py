"""全排列重排校验：ad shots 与参考视频 video_units 的 reorder 端点共用。

返回错误判别值而非直接抛 HTTP 异常——两个调用方各自映射到自己域的 i18n 文案，
校验语义（长度一致、无重复、集合相等）单点维护。
"""

from __future__ import annotations

from typing import Literal

PermutationError = Literal["length", "duplicate", "mismatch"]


def full_permutation_error(existing_ids: list, proposed_ids: list[str]) -> PermutationError | None:
    """``proposed_ids`` 必须是 ``existing_ids`` 的全排列；合法返回 None，否则返回错误类别。"""
    if len(proposed_ids) != len(existing_ids):
        return "length"
    if len(set(proposed_ids)) != len(proposed_ids):
        return "duplicate"
    if set(proposed_ids) != set(existing_ids):
        return "mismatch"
    return None
