"""本集引用的资产：一集正式脚本里写到的资产与衍生，按资产表的落盘真名给出。

「本集要用到哪些资产图」只在这里算一次：集层批量生成资产图、Agent 按集列出与生成待生成资产、
制作状态里「本集引用的资产缺资产图」都取这一份，三处不各自拼集合。

引用落点与级联改名、引用状态共用 :func:`lib.script.script_references.payload_reference_names`
（引用数组、``speaker``、正文 ``@[名称]``），名字归属由 :class:`ReferenceCatalog` 决议，未登记的
名字不算。衍生引用 ``本体/衍生`` 同时算引用了本体：衍生资产图是对本体资产图的一次编辑，
本体没有可用资产图时衍生无从生成。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lib.project.asset_derivatives import derivative_artifact_id, derivative_table
from lib.project.asset_types import resolve_asset_key
from lib.references.reference_catalog import build_reference_catalog, split_derivative_reference
from lib.script.script_references import payload_reference_names


@dataclass(frozen=True, slots=True)
class ReferencedAsset:
    """一条被本集引用的资产或衍生。"""

    asset_type: str
    #: 资产表里的落盘真名；衍生写作 ``本体/衍生``（两段都是落盘真名）。
    name: str
    #: 衍生所属本体的落盘真名；本体条目为 ``None``。
    owner: str | None = None


def episode_referenced_assets(
    project: Mapping[str, Any], script: Mapping[str, Any] | None
) -> frozenset[ReferencedAsset]:
    """一集正式脚本引用的全部已登记资产与衍生；没有正式脚本时为空。"""

    if script is None:
        return frozenset()
    catalog = build_reference_catalog(project)
    referenced: set[ReferencedAsset] = set()
    for name in payload_reference_names(script):
        entry = catalog.resolve(name)
        if entry is None:
            continue
        bucket = project.get(entry.spec.bucket_key)
        owner_key = resolve_asset_key(bucket, entry.asset_name)
        if owner_key is None:
            continue
        referenced.add(ReferencedAsset(asset_type=entry.asset_type, name=owner_key))
        _base, derivative_name = split_derivative_reference(entry.name)
        if not derivative_name:
            continue
        table = derivative_table(bucket.get(owner_key) if isinstance(bucket, Mapping) else None)
        derivative_key = resolve_asset_key(table, derivative_name)
        if derivative_key is None:
            continue
        referenced.add(
            ReferencedAsset(
                asset_type=entry.asset_type,
                name=derivative_artifact_id(owner_key, derivative_key),
                owner=owner_key,
            )
        )
    return frozenset(referenced)


__all__ = ["ReferencedAsset", "episode_referenced_assets"]
