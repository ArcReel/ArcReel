"""生成入口的引用准入：未登记的引用名与没有资产图的角色/场景/道具一律阻断。

参考视频、分镜图、宫格图、图生视频四条路线的单条生成与整批准入共用本模块的判定
（见 ``docs/adr/0073``）。视频生成是最贵的生成行为，准入结论必须只有一份：分成各入口
自行判定时，批量预览放行的目标会在单条提交时被拒，或者反过来——用户为同一份数据在两个
面板拿到相反的答复。

两条阻断轴：

- **未登记引用**：正文的 ``@[名称]`` 或分镜条目的 ``characters_in_*`` / ``scenes`` /
  ``props`` / ``products_in_shot`` 写了引用目录里没有的名字。删除资产后残留的引用与从未
  登记过的名字在这里同一出路——都不再被静默丢弃。
- **无资产图**：角色、场景、道具已登记但没有资产图。原图只是生成资产图的输入，不进分镜
  与视频请求；商品例外，它的原图是验收锚点（见 ``docs/adr/0034``）。

编辑与保存路径不消费本模块：作者写作时不必先建资产，那里仍只发警告（见 ``docs/adr/0064``）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from lib.asset_types import ASSET_SPECS
from lib.reference_catalog import ReferenceCatalog

#: 引用了目录里没有的名字。``reference_asset_missing`` 说的是「这条资产没有可用的图」，
#: 与「根本没有这条资产」是两种修法（前者去生成资产图，后者去登记资产或改正文），故另立一码。
UNREGISTERED_REFERENCE_CODE = "reference_asset_unregistered"

#: 已登记但没有资产图，沿用参考素材缺失家族的失败码。
SHEET_MISSING_CODE = "reference_asset_missing"

#: 必须有资产图才放行的资产类型。商品不在其中：没有资产图时它的原图直接进下游参考，
#: 是保真验收的锚点（``docs/adr/0034``）。
SHEET_REQUIRED_ASSET_TYPES: frozenset[str] = frozenset({"character", "scene", "prop"})


def _missing_text(entries: Iterable[tuple[str, str]]) -> str:
    return ", ".join(f"{asset_type}: {name}" for asset_type, name in entries)


@dataclass(frozen=True)
class ReferenceAdmission:
    """一次生成请求的引用准入结论。

    两条轴分开落地而不是折成一个布尔：失败码与修复动作按轴不同，调用方要能分别报出
    「这些名字没登记」与「这些资产还没出图」，而不是给用户一句合并后的含混提示。
    """

    #: 目录里没有的引用名，按首次出现顺序。
    unregistered: tuple[str, ...] = ()
    #: 已登记但没有资产图的引用，``(资产类型, 名称)``，按首次出现顺序。
    without_sheet: tuple[tuple[str, str], ...] = ()

    @property
    def admitted(self) -> bool:
        """两条轴都没有缺口时放行。"""
        return not self.unregistered and not self.without_sheet

    def unregistered_text(self) -> str:
        """未登记名字的展示串，逐个列名——用户要改的就是这些字。"""
        return ", ".join(self.unregistered)

    def without_sheet_text(self) -> str:
        """无资产图引用的展示串，带类型前缀，与 ``reference_asset_missing`` 同形。"""
        return _missing_text(self.without_sheet)


def _entry_has_sheet(asset: object, sheet_field: str) -> bool:
    """该资产条目登记了资产图路径。

    只问「登记了没有」，不碰文件系统：文件是否读得出来由各路线自己的可用性判定回答
    （参考生视频经 ``ReferenceAssetAvailability``、分镜路线经装配时的存在性检查），
    准入在此重复一遍 IO 只会让同一份数据出两个结论。条目不是 dict 的坏数据按没有
    资产图处理——它的结构错误由 ``lib.data_validator.DataValidator`` 另行报告。
    """
    if not isinstance(asset, dict):
        return False
    sheet = asset.get(sheet_field)
    return isinstance(sheet, str) and bool(sheet)


def admit_references(
    catalog: ReferenceCatalog,
    *,
    references: Iterable[tuple[str, str]],
    unregistered: Iterable[str] = (),
) -> ReferenceAdmission:
    """按引用目录判定一组已归属引用的准入。

    ``references`` 是 ``(资产类型, 引用名)``，由调用方从各自的引用载体派生——正文经
    ``derive_references_from_text``，分镜条目经 :func:`admit_storyboard_item`。
    ``unregistered`` 收正文派生已经认定未登记的名字：正文的归属决议在派生时就做完了，
    这里不重做一遍。

    两条轴都保序去重：用户按提示逐个修名字，顺序抖动会让两次提交的报错读起来像换了内容。
    """

    unregistered_names: list[str] = []
    seen_unregistered: set[str] = set()
    for name in unregistered:
        if name in seen_unregistered:
            continue
        seen_unregistered.add(name)
        unregistered_names.append(name)

    without_sheet: list[tuple[str, str]] = []
    seen_refs: set[tuple[str, str]] = set()
    for asset_type, name in references:
        entry = catalog.lookup(asset_type, name)
        if entry is None:
            if name not in seen_unregistered:
                seen_unregistered.add(name)
                unregistered_names.append(name)
            continue
        if asset_type not in SHEET_REQUIRED_ASSET_TYPES:
            continue
        key = (entry.asset_type, entry.name)
        if key in seen_refs:
            continue
        seen_refs.add(key)
        if not _entry_has_sheet(entry.asset, entry.spec.sheet_field):
            without_sheet.append(key)

    return ReferenceAdmission(unregistered=tuple(unregistered_names), without_sheet=tuple(without_sheet))


def _item_reference_names(item: Mapping[str, object]) -> list[tuple[str, str]]:
    """分镜条目的引用字段 → ``(资产类型, 名称)``，按 ``ASSET_SPECS`` 的字段声明顺序。

    条目来自磁盘剧本 JSON：字段值不是列表、元素不是字符串的坏数据在此跳过而不抛——
    结构校验是 ``lib.data_validator`` 的职责，准入不该把脏剧本打成 500。
    """
    names: list[tuple[str, str]] = []
    for spec in ASSET_SPECS.values():
        for field in spec.reference_list_fields:
            raw = item.get(field)
            if not isinstance(raw, (list, tuple)):
                continue
            names.extend((spec.asset_type, name) for name in raw if isinstance(name, str) and name)
    return names


def admit_storyboard_item(catalog: ReferenceCatalog, item: object) -> ReferenceAdmission:
    """判定一条分镜条目的引用准入——分镜图、宫格图与图生视频共用。

    引用字段按字段名限定类型（``scenes`` 写的就是场景），故走
    :meth:`ReferenceCatalog.lookup` 而不是跨类型决议：跨类型重名不该把「场景没登记」
    偷换成「有个同名角色，那就算登记了」。
    """
    if not isinstance(item, Mapping):
        return ReferenceAdmission()
    return admit_references(catalog, references=_item_reference_names(item))


def admit_storyboard_items(catalog: ReferenceCatalog, items: Iterable[object]) -> ReferenceAdmission:
    """整批分镜条目的合并准入结论，缺口按首次出现顺序合并去重。

    宫格图按分段成图、一次请求覆盖多条分镜：逐条报出后用户要提交多次才看全，合并成一份
    清单才是一次就能改完的修复指引。
    """
    unregistered: list[str] = []
    without_sheet: list[tuple[str, str]] = []
    for item in items:
        admission = admit_storyboard_item(catalog, item)
        unregistered.extend(name for name in admission.unregistered if name not in unregistered)
        without_sheet.extend(entry for entry in admission.without_sheet if entry not in without_sheet)
    return ReferenceAdmission(unregistered=tuple(unregistered), without_sheet=tuple(without_sheet))
