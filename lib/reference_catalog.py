"""引用目录：项目内「已登记引用名」的单一构造入口。

正文的引用语法 ``@[名称]``（见 :mod:`lib.reference_video.text_parser`）与分镜路线的
``characters_in_*`` / ``scenes`` / ``props`` / ``products_in_shot`` 字段写的都是「已登记的
资产名」。数据校验、草稿校验、正文引用归属与参考图投影都要回答同一组问题——这个名字登记
了吗、它归属哪一类资产、承载它的资产条目是哪一条。

约束：这些消费方一律消费本模块从项目数据构造的 :class:`ReferenceCatalog`，不自行从
project.json 拼装名称集合。引用命名空间的构成只在 :func:`build_reference_catalog` 一处
定义，判等坐标系也只在这一处落地；分散拼装会让同一份数据在各处得出不同结论。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lib.asset_types import ASSET_SPECS, AssetSpec, asset_name_comparison_key, normalize_asset_bucket

#: 归属优先级：同一个名字在多张资产表里都登记时，正文引用按此顺序决议出唯一归属。
#:
#: 新项目的四类资产共用一个名称空间（见 :func:`lib.asset_types.ensure_project_asset_namespace`），
#: 重名只可能来自该闸门之前的存量项目；顺序固定即让这些项目的归属结论可复现，而不是随
#: dict 迭代顺序漂移。与 ``AssetSpec.namespace_priority`` 不是同一维度：后者定的是重名冲突
#: 报告里谁算「已有条目」，此处定的是引用落在哪一类。
REFERENCE_ATTRIBUTION_ORDER: tuple[str, ...] = ("product", "character", "scene", "prop")


@dataclass(frozen=True)
class ReferenceCatalogEntry:
    """引用目录里的一条已登记引用。"""

    #: 归属的资产类型（``ASSET_SPECS`` 的键）。
    asset_type: str
    #: 可写在正文与引用字段里的引用名，已在比对坐标系（Unicode NFC）中。
    name: str
    #: 承载该引用的资产表条目名，已在比对坐标系中。
    asset_name: str
    #: 该资产表条目的落盘值。外部编辑写坏的 project.json 里可能不是 dict，读侧按需判型。
    asset: object

    @property
    def spec(self) -> AssetSpec:
        """归属类型的资产规格（sheet 字段、原图字段、子目录等）。"""
        return ASSET_SPECS[self.asset_type]


class ReferenceCatalog:
    """一份项目载荷当前可被引用的全部名字，按类型分组并按归属优先级决议。

    只经 :func:`build_reference_catalog` 构造。查询一律以未归一的名字入参，坐标系收敛在
    内部完成——调用点不该也无需知道资产表以 NFC 还是 NFD 落盘。
    """

    def __init__(self, assets_by_type: Mapping[str, Mapping[str, Any]]) -> None:
        self._by_type: dict[str, dict[str, ReferenceCatalogEntry]] = {
            asset_type: {
                name: ReferenceCatalogEntry(asset_type=asset_type, name=name, asset_name=name, asset=asset)
                for name, asset in bucket.items()
            }
            for asset_type, bucket in assets_by_type.items()
        }
        self._attributed: dict[str, ReferenceCatalogEntry] = {}
        for asset_type in REFERENCE_ATTRIBUTION_ORDER:
            for name, entry in self._by_type[asset_type].items():
                self._attributed.setdefault(name, entry)

    def resolve(self, name: str) -> ReferenceCatalogEntry | None:
        """把一个引用名归属到唯一资产类型；未登记返回 ``None``。

        重名按 :data:`REFERENCE_ATTRIBUTION_ORDER` 决议。
        """
        return self._attributed.get(asset_name_comparison_key(name))

    def lookup(self, asset_type: str, name: str) -> ReferenceCatalogEntry | None:
        """在指定类型内查一个引用名；该类型没登记返回 ``None``。

        与 :meth:`resolve` 的区别是不跨类型决议：调用方已知类型（如引用字段按字段名限定
        类型、投影已带着派生出的类型）时用本方法，跨类型重名不会把结论偷换成别的类型。
        """
        return self._by_type[asset_type].get(asset_name_comparison_key(name))

    def reference_names(self, asset_type: str) -> frozenset[str]:
        """该类型可被正文与引用字段引用的全部名字，已在比对坐标系中。

        跨类型重名不折叠：判「这个字段写的名字登记了吗」问的是该类型登记了没有，与它在
        别的表里是否同名无关。
        """
        return frozenset(self._by_type[asset_type])

    def asset_names(self, asset_type: str) -> frozenset[str]:
        """该类型资产表登记的条目名，已在比对坐标系中。

        与 :meth:`reference_names` 的区别是问的不是「能不能引用」而是「是不是一条资产」：
        说话人位要绑参考音频与音色，须落在一条真实资产条目上。
        """
        return frozenset(entry.asset_name for entry in self._by_type[asset_type].values())


def build_reference_catalog(project: object) -> ReferenceCatalog:
    """从 project.json 载荷构造引用目录——引用命名空间的唯一构造入口。

    资产表的 key 在此收敛到比对坐标系（见 :func:`lib.asset_types.normalize_asset_bucket`）：
    落盘形态 NFC / NFD 皆可且不迁移，判等两侧同形才判得准。畸形载荷（整体或某张表不是
    dict）按空表处理——其结构错误由 :class:`lib.data_validator.DataValidator` 另行报告，
    目录构造不重复报错也不抛异常。
    """
    payload: dict[str, Any] = project if isinstance(project, dict) else {}
    return ReferenceCatalog(
        {spec.asset_type: normalize_asset_bucket(payload.get(spec.bucket_key)) for spec in ASSET_SPECS.values()}
    )
