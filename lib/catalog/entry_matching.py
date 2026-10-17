"""模型名对应条目（见 docs/adr/0105）：由模型名在一组条目里找出对应的那一条。

规则只有名字这一层，不存储对应关系，也不修改条目：调用方每次拿当前目录的条目现查。名字归一化沿用
``arcreel_catalog_core.normalize_model_name``，构建侧的名字冲突检查与这里是同一份实现。
"""

from __future__ import annotations

from collections.abc import Iterable

from arcreel_catalog_core import CatalogEntry, EntryKey, normalize_model_name

# 命中的先后：精确命中优先于归一化命中；同等命中时，官方模型名优先于模型别名。
_EXACT_NAME, _EXACT_ALIAS, _NORMALIZED_NAME, _NORMALIZED_ALIAS = range(4)


def _hit_rank(model_name: str, normalized: str, entry: CatalogEntry) -> int | None:
    """条目对这个模型名的最好一档命中；对不上返回 ``None``。"""
    if model_name == entry.name:
        return _EXACT_NAME
    if model_name in entry.aliases:
        return _EXACT_ALIAS
    if normalized == normalize_model_name(entry.name):
        return _NORMALIZED_NAME
    if any(normalized == normalize_model_name(alias) for alias in entry.aliases):
        return _NORMALIZED_ALIAS
    return None


def match_entry(
    entries: Iterable[CatalogEntry],
    model_name: str,
    *,
    media_type: str | None = None,
    binding: EntryKey | None = None,
) -> CatalogEntry | None:
    """模型名对应的条目；对应不上，或不能唯一确定时返回 ``None``。

    ``binding`` 是用户为这个模型指定的条目：它在 ``entries`` 里（且与 ``media_type`` 一致）就直接取它；
    条目已不在目录里，或与媒体类型不符时，绑定不生效，继续按名字查。

    按名字查时，只看与 ``media_type`` 一致的条目（不给则不限），取命中最靠前的一档：精确的官方模型名、
    精确的模型别名、归一化的官方模型名、归一化的模型别名。一个名字同时命中同一媒体类型下的多个条目时按
    目录顺序取第一个；命中的是不同媒体类型的条目（两栖模型）而没有给 ``media_type`` 时无法选择，返回
    ``None``，由调用方先确定媒体类型。已下架的条目照常对应。
    """
    candidates = [entry for entry in entries if media_type in (None, entry.media_type)]
    if binding is not None:
        bound = next((entry for entry in candidates if entry.key == binding), None)
        if bound is not None:
            return bound
    normalized = normalize_model_name(model_name)
    hits = [(rank, entry) for entry in candidates if (rank := _hit_rank(model_name, normalized, entry)) is not None]
    if not hits:
        return None
    best = min(rank for rank, _ in hits)
    top = [entry for rank, entry in hits if rank == best]
    if len({entry.media_type for entry in top}) > 1:
        return None
    return top[0]
