"""剧本编辑核心（纯函数）。

把「如何按 id 安全地编辑一份剧本 dict」收敛到唯一一处：喂入剧本 dict + 一个编辑操作，
就地改 dict 并返回它，或对非法操作（id 未命中、数组越界、拆分份数不足、字段路径不存在）
抛 `ScriptEditError`。**不读盘、不依赖项目状态、不做结构良构校验**——结构是否合法交给写盘
统一入口的 `_write_script_unlocked`（「不更坏」+ Pydantic 模型）兜底，本模块只负责数组手术、
id 分配与资产作废。MCP 工具与测试都复用它。

四种剧本骨架（segments/scenes/shots/video_units）的条目数组与 id 字段判别委托给
`script_skeleton.resolve_kind_items`（骨架条目访问的唯一入口），`resolve_items` 在其上叠加
编辑核心特有的 fail-loud 校验策略；与 `script_structure_validator._select_model`、写盘统一
入口的 metadata 重算共用同一取证解析，避免多处漂移。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from lib.script.item_ids import ITEM_ID_RE, ItemIdIssuer, max_item_number
from lib.script.script_models import PENDING_AUTHORING_FIELD
from lib.script.script_skeleton import resolve_kind_items

logger = logging.getLogger(__name__)


class ScriptEditError(ValueError):
    """剧本编辑操作非法（id 未命中、数组越界、拆分份数不足、字段路径不存在等）。

    ``key``/``params`` 是给用户可见路径准备的翻译坐标：会经 HTTP 路由回传终端用户的 raise 点
    必须显式传具体 ``key``，服务端按请求 ``Accept-Language`` 渲染，而不是把固定中文的
    ``str(self)`` 嵌进已翻译的响应模板。只被 MCP 工具与日志消费的 raise 点是面向 Agent、
    按 CLAUDE.md 豁免 i18n，沿用默认 key 即可（渲染为通用兜底文案）。
    """

    def __init__(self, message: str, *, key: str = "script_edit_error", **params: Any) -> None:
        super().__init__(message)
        self.key = key
        self.params = params


def resolve_items(script: dict[str, Any], *, kind: str | None = None) -> tuple[list[Any], str, str]:
    """按剧本骨架选出当前剧本的条目数组、其 id 字段名与种类。

    返回 ``(items, id_field, kind)``：``kind`` ∈ {"segments", "scenes", "shots", "video_units"}。
    键与 id 字段的查法委托给 `script_skeleton.resolve_kind_items`（骨架条目访问的唯一入口，
    取证解析同源）；``kind`` 由调用方显式给定（如任务开工时已定死的生成模式）时跳过取证解析，
    直接按该种类取值。本函数在原样返回值上加编辑核心特有的校验策略：**键缺失**视为空数组；
    **键存在但类型非 list（含值为 null）**时 fail-loud 抛 `ScriptEditError`（不静默降级为 []，
    避免把数据损坏掩盖成「未找到 id」——`"segments": null` 这类损坏会暴露而非被当成空草稿）。
    返回的 list 在键存在时即 script 内的实际引用（就地编辑生效）。
    """
    raw_items, id_field, kind = resolve_kind_items(script, kind=kind)
    if kind not in script:
        return [], id_field, kind
    if not isinstance(raw_items, list):
        type_name = type(raw_items).__name__
        raise ScriptEditError(
            f"{kind} 必须是列表，当前为 {type_name}",
            key="script_edit_items_not_list",
            kind=kind,
            type_name=type_name,
        )
    return raw_items, id_field, kind


def _find_index(items: list[Any], id_field: str, item_id: str) -> int:
    for idx, item in enumerate(items):
        if isinstance(item, dict) and str(item.get(id_field)) == str(item_id):
            return idx
    raise ScriptEditError(f"未找到 id={item_id!r} 的分镜（{id_field}）")


def _set_nested(obj: dict[str, Any], field_path: str, value: Any) -> None:
    parts = field_path.split(".")
    if not parts or any(not p for p in parts):
        raise ScriptEditError(f"非法字段路径: {field_path!r}")
    if parts[0] == "generated_assets":
        raise ScriptEditError("patch_episode_script 不可改 generated_assets；资产的生成/重生是独立的显式动作")
    if parts[0] == "needs_replan":
        raise ScriptEditError("patch_episode_script 不可直接改重规划标记；修改 unit 规划内容后由系统重算")
    if parts[0] == PENDING_AUTHORING_FIELD:
        raise ScriptEditError(
            f"patch_episode_script 不可改 {PENDING_AUTHORING_FIELD}；它由新增条目置位，写齐视觉层或提示词编写写回时清除"
        )
    if parts[0] == "end_frame_image":
        raise ScriptEditError("patch_episode_script 不可改 end_frame_image；尾帧的设置/清除是独立的显式动作")
    if value is None and parts in (["image_prompt"], ["video_prompt"]):
        raise ScriptEditError(
            f"patch_episode_script 不可把 {parts[0]} 清成 null；待生成态只由脚本规划机械转换写入，"
            "要重写请给出新的提示词"
        )
    if parts[0] in {"segment_id", "scene_id", "unit_id", "shot_id"}:
        raise ScriptEditError(
            f"patch_episode_script 不可改分镜 id 字段 ({parts[0]})；id 由 insert/split 分配，不允许直接修改"
        )
    cur: Any = obj
    for p in parts[:-1]:
        if not isinstance(cur, dict):
            raise ScriptEditError(f"父节点非对象 (类型 {type(cur).__name__}): {field_path!r}")
        if p not in cur:
            raise ScriptEditError(f"字段路径不存在: {field_path!r}")
        if not isinstance(cur[p], dict):
            raise ScriptEditError(f"父节点非对象 (键 {p!r} 类型为 {type(cur[p]).__name__}): {field_path!r}")
        cur = cur[p]
    if not isinstance(cur, dict):
        raise ScriptEditError(f"父节点非对象: {field_path!r}")
    cur[parts[-1]] = value


def patch_field(script: dict[str, Any], item_id: str, field_path: str, value: Any) -> dict[str, Any]:
    """按 id 定位一个分镜，设置其（可嵌套的）字段。纯 setter，不触碰 generated_assets。"""
    items, id_field, _ = resolve_items(script)
    idx = _find_index(items, id_field, item_id)
    _set_nested(items[idx], field_path, value)
    return script


def _script_episode(script: dict[str, Any], ids: list[str]) -> int:
    episode = script.get("episode")
    if isinstance(episode, int) and not isinstance(episode, bool) and episode >= 1:
        return episode
    for item_id in ids:
        if (match := ITEM_ID_RE.match(item_id)) is not None:
            return int(match.group(1))
    return 1


def new_item_ids(script: dict[str, Any], count: int, *, floor: int = 0) -> list[str]:
    """新增条目的 id：从本集用过的最大主序号之后依次取 ``count`` 个，Web 与 Agent 的新增都经这里取号。

    ``floor`` 是本集用过的最大主序号（``ProjectManager.item_id_floor``：账本记录的最大号与剧本现存编号
    取大）；编号只分配、不复用，删掉的条目的序号不会再发给新条目。序号不回填中间空缺，也不从锚点派生，
    条目顺序只由数组位置决定。分镜族为 ``E{集}S{序号:02d}``，参考生视频单元为 ``E{集}U{序号}``。
    """
    items, id_field, kind = resolve_items(script)
    ids = [str(item.get(id_field)) for item in items if isinstance(item, dict)]
    episode = _script_episode(script, ids)
    issuer = ItemIdIssuer(episode, kind, max(floor, max_item_number(ids, episode)))
    taken = set(ids)
    issued: list[str] = []
    while len(issued) < count:
        if (item_id := issuer.issue()) not in taken:
            issued.append(item_id)
    return issued


def new_item_id(script: dict[str, Any], *, floor: int = 0) -> str:
    """新增一个条目的 id，见 :func:`new_item_ids`。"""
    return new_item_ids(script, 1, floor=floor)[0]


def insert_segment(
    script: dict[str, Any], after_id: str | None, new_item: dict[str, Any], *, floor: int = 0
) -> dict[str, Any]:
    """在 ``after_id`` 之后插入一个新条目；``after_id`` 为 ``None`` 时插到最前。

    新条目的 id 字段一律由 ``new_item_id`` 分配，``generated_assets`` 与 ``end_frame_image`` 清空。
    其余字段由调用方提供，结构是否合法由写盘统一入口校验。
    """
    items, id_field, _ = resolve_items(script)
    idx = -1 if after_id is None else _find_index(items, id_field, after_id)
    item = deepcopy(new_item)
    item[id_field] = new_item_id(script, floor=floor)
    item["generated_assets"] = {}
    item.pop("end_frame_image", None)
    items.insert(idx + 1, item)
    return script


def remove_segment(script: dict[str, Any], item_id: str) -> dict[str, Any]:
    """按 id 删除一个分镜。被删分镜的资产随之消失；不改动其余分镜的 id。"""
    items, id_field, _ = resolve_items(script)
    idx = _find_index(items, id_field, item_id)
    items.pop(idx)
    return script


def move_segment(script: dict[str, Any], item_id: str, after_id: str | None) -> dict[str, Any]:
    """把 ``item_id`` 移到 ``after_id`` 之后；``after_id`` 为 ``None`` 时移到最前。条目内容与产物不变。"""
    if after_id == item_id:
        raise ScriptEditError(f"id={item_id!r} 不能移到自己之后")
    items, id_field, _ = resolve_items(script)
    item = items.pop(_find_index(items, id_field, item_id))
    idx = -1 if after_id is None else _find_index(items, id_field, after_id)
    items.insert(idx + 1, item)
    return script


def split_segment(
    script: dict[str, Any], item_id: str, parts: list[dict[str, Any]], *, floor: int = 0
) -> dict[str, Any]:
    """把 ``item_id`` 分镜按 Agent 提供的各部分内容拆成多个。

    首个部分保留原 id 且**保留** ``generated_assets`` 与 ``end_frame_image`` 不清空——视为
    "锚点延续",与 ``insert_segment`` 的锚点资产不动语义对齐(同族结构操作,资产作废粒度统一)。
    其余 parts 按新增条目的取号规则（:func:`new_item_ids`，``floor`` 同义）各取一个新主序号且清空 ``generated_assets`` 与
    ``end_frame_image``(身份变化,旧资产无归属,退回 pending 待重生)。Agent 想微调原分镜内容请用 ``patch_episode_script`` 改字段,
    用 split 时锚点资产被保留是为了避免误用一次 split 把已生成的图/视频全部失效。

    参考生视频拆的是顶层 ``video_units``（``item_id`` 即 unit_id），每个 part 是一个完整
    新 unit（各带自己的 ``shots``），不是把原 unit 内的 ``shots`` 拆细。``duration_seconds`` 是
    unit 独立字段、不由 ``shots`` 派生，故各新 part 须自行给出，本函数不代算。
    """
    if len(parts) < 2:
        raise ScriptEditError("split 至少需要 2 个部分")
    items, id_field, _ = resolve_items(script)
    idx = _find_index(items, id_field, item_id)
    anchor_assets = items[idx].get("generated_assets")
    anchor_end_frame = items[idx].get("end_frame_image")

    fresh_ids = iter(new_item_ids(script, len(parts) - 1, floor=floor))
    new_parts: list[dict[str, Any]] = []
    for offset, raw in enumerate(parts):
        part = deepcopy(raw)
        if offset == 0:
            part[id_field] = str(item_id)
            if isinstance(anchor_assets, dict):
                part["generated_assets"] = deepcopy(anchor_assets)
            else:
                if anchor_assets is not None:
                    logger.warning(
                        "split_segment: 锚点 %r generated_assets 形态异常(%s),退化为空 dict",
                        item_id,
                        type(anchor_assets).__name__,
                    )
                part["generated_assets"] = {}
            if anchor_end_frame is None:
                part.pop("end_frame_image", None)
            else:
                part["end_frame_image"] = anchor_end_frame
        else:
            part[id_field] = next(fresh_ids)
            part["generated_assets"] = {}
            part.pop("end_frame_image", None)
        new_parts.append(part)

    items[idx : idx + 1] = new_parts
    return script
