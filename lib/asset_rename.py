"""资产级联重命名的纯函数层：剧本/草稿引用改写、关联文件迁移规划与结果报告。

资产以 name 为身份（见 docs/adr/0057），重命名是一次级联事务：资产桶 key、全部剧集剧本
与 script_plan 草稿里的名称引用（引用数组 / speaker / ``@[名称]`` mention）、按名命名的关联文件
（不变式「文件 stem = 资产名」）须一次改齐。本模块承载其中**无副作用**的部分——引用改写
（就地改 dict、返回改写数）与文件迁移规划（返回 (src, dst) 列表）——供 ProjectManager 的
编排入口在锁内先扫描（dry-run 预览与执行共用同一套扫描）再落盘。

名字引用判等一律走比对坐标系（strip + NFC，见
:func:`lib.asset_types.asset_name_comparison_key`）：正文与引用数组可能带两端空白或以 NFD
形式存量落盘，按字节比对会漏改同一资产的引用。文件 stem 仍只做 NFC，避免改变路径语义。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lib.asset_types import (
    ASSET_SPECS,
    AssetSpec,
    asset_name_comparison_key,
    normalize_asset_name,
    rekey_equivalent_entries,
)
from lib.reference_video.text_parser import rewrite_mentions


class AssetRenameNotFoundError(KeyError):
    """旧名在资产桶中不存在。message 含恢复导向提示（可能上次重命名已成功）。"""


class AssetRenameConflictError(ValueError):
    """目标名与既有同类型资产归一化判定冲突，整体拒绝、不落盘。"""

    def __init__(self, conflict_name: str):
        super().__init__(f"目标名与既有资产 {conflict_name!r} 冲突（按 NFC 归一判定），请换一个名字或先处理既有资产")
        self.conflict_name = conflict_name


class AssetRenameFileCollisionError(ValueError):
    """迁移目标文件已被占用，整体拒绝、不落盘。

    占用者是无对应资产的孤儿文件（有资产时先被 :class:`AssetRenameConflictError` 拦下），
    落盘处的 ``os.replace`` 会静默销毁它。
    """

    def __init__(self, destination: Path):
        super().__init__(f"迁移目标文件已存在: {destination}，请先移除该文件或换一个名字")
        self.destination = destination


class AssetRenameHistoryCollisionError(ValueError):
    """新名下已有保留的版本历史，整体拒绝、不落盘。

    资产删除只删资产桶 key，版本记录与快照会留下（见 delete_entry）：迁移过去会覆盖这份
    历史且不可恢复。与孤儿文件同口径——宁可 fail loud 让用户先处理，也不静默销毁。
    """

    def __init__(self, resource_id: str):
        super().__init__(f"新名 {resource_id!r} 下已有保留的版本历史，请先清理该历史或换一个名字")
        self.resource_id = resource_id


@dataclass(frozen=True)
class AssetRenameReport:
    """级联重命名的影响报告。dry-run 预览与实际执行共用同一次扫描，数字必然一致。"""

    table: str
    old_name: str
    new_name: str
    episodes: int
    references: int
    files: int
    dry_run: bool


#: 各资产类型在剧本/草稿骨架里的「名称列表」引用字段。列表内只有 str 元素才是名称引用——
#: drama 顶层 ``scenes`` 是分镜 dict 列表，与 narration 分镜里的场景名列表同 key 不同形，
#: 按元素类型即可区分，无需骨架特例。
_LIST_FIELDS_BY_TYPE: dict[str, frozenset[str]] = {
    asset_type: frozenset(spec.reference_list_fields) for asset_type, spec in ASSET_SPECS.items()
}


def rewrite_payload_references(payload: dict, asset_type: str, old_name: str, new_name: str) -> int:
    """就地把剧本/草稿 payload 中指向 *old_name* 的名称引用改写为 *new_name*，返回改写数。

    覆盖面（与 :mod:`lib.data_validator` 的引用扫描 + 引用语法派生口径对齐）：

    - 各骨架的引用数组（``_LIST_FIELDS_BY_TYPE``，仅 str 元素）；
    - drama ``utterances[].speaker`` 与 ad ``video_prompt.dialogue[].speaker``（仅 character）；
    - 单元正文与 ad 分镜文本内的 ``@[旧名]`` mention（经 :func:`rewrite_mentions`）；
    - 旧式剧本内嵌的顶层 ``characters`` 镜像 dict（仅 character：re-key + 路径字段同步）。

    只识别骨架结构、不校验语义：结构校验由写盘统一入口的「不更坏」守卫兜底。
    """
    target = asset_name_comparison_key(old_name)
    list_fields = _LIST_FIELDS_BY_TYPE[asset_type]
    count = 0

    def _matches(value: object) -> bool:
        return isinstance(value, str) and asset_name_comparison_key(value) == target and value != new_name

    def _walk(node: object) -> None:
        nonlocal count
        if isinstance(node, dict):
            for key, value in node.items():
                if key in list_fields and isinstance(value, list):
                    for i, item in enumerate(value):
                        if _matches(item):
                            value[i] = new_name
                            count += 1
                        else:
                            _walk(item)
                    continue
                if key == "speaker" and asset_type == "character" and _matches(value):
                    node[key] = new_name
                    count += 1
                    continue
                if key in ("shots", "units", "video_units") and isinstance(value, list):
                    # 参考生视频的 mention 落在 unit 正文（``video_units[].text``，草稿里是
                    # ``units[].text``）；ad 分镜的 shot 还带引用数组与 video_prompt.dialogue，
                    # 继续下钻由通用规则处理。
                    for item in value:
                        if isinstance(item, dict) and isinstance(item.get("text"), str):
                            new_text, n = rewrite_mentions(item["text"], old_name, new_name)
                            if n:
                                item["text"] = new_text
                                count += n
                        _walk(item)
                    continue
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    if asset_type == "character":
        # 旧式剧本把角色表镜像内嵌在顶层 characters dict（update_character_sheet 写入路径），
        # re-key 并同步其中的 sheet 路径字段，避免旧名以镜像形式残留。
        embedded = payload.get("characters")
        if isinstance(embedded, dict):
            # 无条件收编：胜出 key 恰好已是新名（纯改编码形式的改名）时，另一条等价 key
            # 仍带着旧路径，不能因为胜出 key 看起来没变就跳过。计数看 key 是否真的变了。
            keys_before = list(embedded)
            entry = rekey_equivalent_entries(embedded, old_name, new_name)
            if entry is not None:
                if list(embedded) != keys_before:
                    count += 1
                if isinstance(entry, dict):
                    count += rewrite_entry_paths(entry, ASSET_SPECS[asset_type], old_name, new_name)

    _walk(payload)
    return count


def renamed_file_stem(stem: str, old_name: str, new_name: str, *, allow_sequence: bool = False) -> str | None:
    """按「文件 stem = 资产名」不变式推导改名后的 stem；与旧名无关时返回 None。

    默认只认 stem 归一后**精确等于**旧名（sheet / 参考图 / 参考音频）。``allow_sequence``
    额外放行 ``旧名_{序号}`` 形态（product 多图序列，见 server/routers/files.py 的
    sequenced 命名），仅可用于确实按序号累积命名的位置：资产名本身允许下划线加数字
    （``validate_asset_name`` 不禁），在普通目录放行该形态会把兄弟资产「旧名_2」的文件
    一并卷走、把它的路径字段改成悬空。

    比对与拼接都在归一坐标系上进行——macOS 落盘的文件名可能是 NFD。
    """
    normalized = normalize_asset_name(stem)
    target = normalize_asset_name(old_name)
    if normalized == target:
        return new_name
    if allow_sequence:
        prefix = f"{target}_"
        if normalized.startswith(prefix) and normalized[len(prefix) :].isdigit():
            return f"{new_name}_{normalized[len(prefix) :]}"
    return None


def renamed_relpath(rel: str, old_name: str, new_name: str, *, allow_sequence: bool = False) -> str | None:
    """把路径字段值中的文件 stem 从旧名改为新名；stem 与旧名无关时返回 None。"""
    path = PurePosixPath(rel.replace("\\", "/"))
    new_stem = renamed_file_stem(path.stem, old_name, new_name, allow_sequence=allow_sequence)
    if new_stem is None:
        return None
    return str(path.with_name(new_stem + path.suffix))


def rewrite_entry_paths(entry: dict, spec: AssetSpec, old_name: str, new_name: str) -> int:
    """就地同步资产 entry 内按名命名的路径字段（sheet / 参考图 / 参考音频 / 多图序列），返回改写数。

    改写范围与 :func:`plan_asset_file_renames` 的迁移范围逐维对齐，两侧用同一个谓词：只动
    落在该资产类型目录本级及其 ``refs`` / ``refs_audio`` 子目录下、且 stem 命中旧名的值；
    ``旧名_{序号}`` 形态只在多图序列资产的 ``refs`` 子目录放行，其余位置一律精确同名。
    用户手动指到别处的路径（如 ``thumbnails/旧名.png``）不动——那里的文件不在迁移范围内，
    改了字段就会把一条原本有效的引用指成空。
    """
    base = PurePosixPath(spec.subdir)
    migrated_dirs = {base, base / "refs", base / "refs_audio"}
    sequenced_refs = "reference_images" in spec.extra_list_fields

    def rewrite(value: str, *, sequenced_field: bool = False) -> str | None:
        parent = PurePosixPath(value.replace("\\", "/")).parent
        if parent not in migrated_dirs:
            return None
        allow_sequence = sequenced_field and sequenced_refs and parent == base / "refs"
        renamed = renamed_relpath(value, old_name, new_name, allow_sequence=allow_sequence)
        return renamed if renamed != value else None

    count = 0
    for field in (spec.sheet_field, "reference_image", "reference_audio"):
        value = entry.get(field)
        if isinstance(value, str) and value:
            renamed = rewrite(value)
            if renamed is not None:
                entry[field] = renamed
                count += 1
    images = entry.get("reference_images")
    if isinstance(images, list):
        for i, value in enumerate(images):
            if isinstance(value, str) and value:
                renamed = rewrite(value, sequenced_field=True)
                if renamed is not None:
                    images[i] = renamed
                    count += 1
    return count


def plan_asset_file_renames(
    project_dir: Path, spec: AssetSpec, old_name: str, new_name: str
) -> list[tuple[Path, Path]]:
    """扫描该资产类型的落盘目录，规划 stem 命中旧名的文件迁移，返回 ``(src, dst)`` 列表。

    覆盖资产图目录本级与其上传子目录（``refs`` / ``refs_audio``），版本快照另由
    VersionManager 迁移。按目录扫描而非只信 entry 路径字段：生成中间产物可能已按旧名
    落盘而字段未写，旧名文件不应残留。

    不按文件名前缀排除隐藏文件：命中与否只由 stem 是否等于资产名决定，这已足以排除杂物
    （``.DS_Store`` 的 stem 就是 ``.DS_Store``，原子写的临时文件形如 ``tmpXXXX.tmp``），
    而前导点是合法资产名，一刀切跳过会让名为 ``.甲`` 的资产改名后留下失效的路径字段。

    ``旧名_{序号}`` 形态只在多图序列资产（entry 带 ``reference_images``）的 ``refs``
    子目录放行——那里的文件名由上传侧按序号机械生成，不会是别的资产的名字；其余目录
    一律精确同名，否则兄弟资产「旧名_2」的资产图会被一并卷走。

    目标已被占用时抛 :class:`AssetRenameFileCollisionError`：规划早于任何写入，因此整体
    拒绝、不落盘。占用有两种来源，都要拦：磁盘上已有的孤儿文件，以及同批两个源文件（如
    NFC / NFD 两种编码的同名文件）撞到同一个目标——后者若放行，第二次 ``os.replace``
    会吃掉第一次的成果。

    大小写不敏感或归一化不敏感的文件系统（APFS / NTFS）上，仅改大小写或仅改编码形式的
    改名会让目标解析回源文件自身，那不是占用，按 ``samefile`` 豁免。

    这不影响「中途失败重跑收敛」——已完成的迁移其 ``src`` 已不存在，扫描不会再把它规划进来。

    Raises:
        AssetRenameFileCollisionError: 某个迁移目标路径已被他人占用或被同批另一次迁移占用。
    """
    base = project_dir / spec.subdir
    sequenced_refs = "reference_images" in spec.extra_list_fields
    moves: list[tuple[Path, Path]] = []
    planned: set[Path] = set()
    for directory, allow_sequence in ((base, False), (base / "refs", sequenced_refs), (base / "refs_audio", False)):
        if not directory.is_dir():
            continue
        for file in sorted(directory.iterdir()):
            if not file.is_file():
                continue
            new_stem = renamed_file_stem(file.stem, old_name, new_name, allow_sequence=allow_sequence)
            if new_stem is not None and file.stem != new_stem:
                destination = file.with_name(new_stem + file.suffix)
                if destination in planned:
                    raise AssetRenameFileCollisionError(destination)
                if destination.exists() and not destination.samefile(file):
                    raise AssetRenameFileCollisionError(destination)
                planned.add(destination)
                moves.append((file, destination))
    return moves


__all__ = [
    "AssetRenameConflictError",
    "AssetRenameFileCollisionError",
    "AssetRenameHistoryCollisionError",
    "AssetRenameNotFoundError",
    "AssetRenameReport",
    "plan_asset_file_renames",
    "renamed_file_stem",
    "renamed_relpath",
    "rewrite_entry_paths",
    "rewrite_payload_references",
]
