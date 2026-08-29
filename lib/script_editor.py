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

from lib.script_plan_entries import SCRIPT_PLAN_ENTRY_REVISION_FIELD
from lib.script_skeleton import resolve_kind_items

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


def resolve_items(script: dict[str, Any], *, kind: str | None = None) -> tuple[list[dict[str, Any]], str, str]:
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


def _find_index(items: list[dict[str, Any]], id_field: str, item_id: str) -> int:
    for idx, item in enumerate(items):
        if isinstance(item, dict) and str(item.get(id_field)) == str(item_id):
            return idx
    raise ScriptEditError(f"未找到 id={item_id!r} 的分镜（{id_field}）")


def _existing_ids(items: list[dict[str, Any]], id_field: str) -> set[str]:
    return {str(item.get(id_field)) for item in items if isinstance(item, dict)}


def _next_suffixed_id(base: str, taken: set[str]) -> str:
    """在 ``base`` 后追加 ``_{k}`` 生成不与 ``taken`` 冲突的稳定新 id（k 从 1 起）。

    id 稳定不重排：新 id 由锚点 id 派生 ``_{子序号}`` 后缀，不触动其余分镜的 id，
    序列顺序由数组位决定。

    先把 ``base`` 收敛到 stem（首个 ``_`` 之前的部分）再追加子序号——否则锚点本身已含
    后缀（如 ``E1S01_1``）时会产生 ``E1S01_1_1`` 这种多层嵌套，违反 ``data_validator.ID_PATTERN``
    （``^E\\d+S\\d+(?:_\\d+)?$``，archive 层）。base 一律 segment_id 形式
    ``E\\d+S\\d+`` / ``E\\d+U\\d+`` 不含 ``_``，``split('_')[0]`` 取 stem 是安全的。
    """
    stem = base.split("_")[0]
    k = 1
    while f"{stem}_{k}" in taken:
        k += 1
    return f"{stem}_{k}"


def _set_nested(obj: dict[str, Any], field_path: str, value: Any) -> None:
    parts = field_path.split(".")
    if not parts or any(not p for p in parts):
        raise ScriptEditError(f"非法字段路径: {field_path!r}")
    if parts[0] == "generated_assets":
        # patch 是纯字段 setter，资产生命周期与剧本编辑解耦（见 ADR-0003）。
        raise ScriptEditError("patch_episode_script 不可改 generated_assets；资产的生成/重生是独立的显式动作")
    if parts[0] == "needs_replan":
        raise ScriptEditError("patch_episode_script 不可直接改重规划标记；修改 unit 规划内容后由系统重算")
    if parts[0] == SCRIPT_PLAN_ENTRY_REVISION_FIELD:
        # 条目内容指纹陈述「这一条的视觉层是照着哪份脚本规划内容写的」，由提示词编写落盘时写入。
        # 放行 patch 等于让 Agent 手改这条陈述，失效条目便能被伪装成未变、逃过重写。
        raise ScriptEditError(
            f"patch_episode_script 不可改 {SCRIPT_PLAN_ENTRY_REVISION_FIELD}；"
            "它由提示词编写落盘时写入，陈述该条目消费的脚本规划内容"
        )
    if parts[0] == "end_frame_image":
        # 尾帧字段的值是本服务写出的快照相对路径，只由尾帧设置/清除端点写入。放行 patch
        # 会让原样写入的任意字符串绕过快照复制，重新引入悬空引用与越界路径。
        raise ScriptEditError("patch_episode_script 不可改 end_frame_image；尾帧的设置/清除是独立的显式动作")
    if parts[0] in {"segment_id", "scene_id", "unit_id", "shot_id"}:
        # patch 不可改分镜 id：id 由 insert/split 从锚点派生，结构校验不查 id 唯一性，
        # Agent 改 id 后会让其他依赖 id 定位的 helper（update_scene_asset 等）回写到错误分镜
        # 或产生重复 id 歧义。增减分镜走 patch_episode_script 的 insert / split / remove operation。
        raise ScriptEditError(
            f"patch_episode_script 不可改分镜 id 字段 ({parts[0]})；id 由 insert/split 派生，不允许直接修改"
        )
    cur: Any = obj
    # 三类异常分别报告，让 Agent 错误信息更精确（拼写错误 vs 类型错误 vs 中间节点不存在）。
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
    # 叶子(最后一段)允许不存在:LLM 漏写的 optional 字段(video_prompt.dialogue / note 等
    # 在 Pydantic 模型里有 default 或 default_factory,JSON 序列化时可能被省略)Agent 应能补,
    # 而不是被迫走 remove+insert 重生整个分镜。父节点(中间路径段)不存在仍 fail-loud——那是
    # 真的拼写错误(如 image_prompt.scen 应为 image_prompt.scene),不该在 dict 上凭空新建
    # 中间节点。结构上的错误最终由写盘统一入口的「不更坏」结构校验兜住。
    cur[parts[-1]] = value


def patch_field(script: dict[str, Any], item_id: str, field_path: str, value: Any) -> dict[str, Any]:
    """按 id 定位一个分镜，设置其（可嵌套的）字段。纯 setter，不触碰 generated_assets。"""
    items, id_field, _ = resolve_items(script)
    idx = _find_index(items, id_field, item_id)
    _set_nested(items[idx], field_path, value)
    return script


def insert_segment(script: dict[str, Any], after_id: str, new_item: dict[str, Any]) -> dict[str, Any]:
    """在 ``after_id`` 之后插入一个新分镜，分配派生自锚点 id 的稳定新 id。

    新分镜的 id 字段被强制改写为 ``{after_id}_{k}``（唯一），``generated_assets`` 与
    ``end_frame_image`` 清空。其余字段由 Agent 提供，结构是否合法由写盘统一入口校验。
    """
    if not isinstance(new_item, dict):
        raise ScriptEditError("new_item 必须是对象")
    items, id_field, _ = resolve_items(script)
    idx = _find_index(items, id_field, after_id)
    item = deepcopy(new_item)
    item[id_field] = _next_suffixed_id(str(after_id), _existing_ids(items, id_field))
    item["generated_assets"] = {}
    # 尾帧快照按分镜 id 命名，新 id 名下还没有快照；Agent 自带的值只会指向别人的快照或空路径。
    item.pop("end_frame_image", None)
    items.insert(idx + 1, item)
    return script


def remove_segment(script: dict[str, Any], item_id: str) -> dict[str, Any]:
    """按 id 删除一个分镜。被删分镜的资产随之消失；不改动其余分镜的 id。"""
    items, id_field, _ = resolve_items(script)
    idx = _find_index(items, id_field, item_id)
    items.pop(idx)
    return script


def split_segment(script: dict[str, Any], item_id: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    """把 ``item_id`` 分镜按 Agent 提供的各部分内容拆成多个。

    首个部分保留原 id 且**保留** ``generated_assets`` 与 ``end_frame_image`` 不清空——视为
    "锚点延续",与 ``insert_segment`` 的锚点资产不动语义对齐(同族结构操作,资产作废粒度统一)。
    其余 parts 取 ``{item_id}_{k}`` 后缀的新 id 且清空 ``generated_assets`` 与
    ``end_frame_image``(身份变化,旧资产无归属,退回 pending 待重生)。Agent 想微调原分镜内容请用 ``patch_episode_script`` 改字段,
    用 split 时锚点资产被保留是为了避免误用一次 split 把已生成的图/视频全部失效。

    参考生视频拆的是顶层 ``video_units``（``item_id`` 即 unit_id），每个 part 是一个完整
    新 unit（各带自己的 ``shots``），不是把原 unit 内的 ``shots`` 拆细。``duration_seconds`` 是
    unit 独立字段、不由 ``shots`` 派生，故各新 part 须自行给出，本函数不代算。
    """
    if not isinstance(parts, list) or len(parts) < 2:
        raise ScriptEditError("split 至少需要 2 个部分")
    if any(not isinstance(p, dict) for p in parts):
        raise ScriptEditError("split 的每个部分必须是对象")
    items, id_field, _ = resolve_items(script)
    idx = _find_index(items, id_field, item_id)
    anchor_assets = items[idx].get("generated_assets")
    anchor_end_frame = items[idx].get("end_frame_image")

    taken = _existing_ids(items, id_field)
    new_parts: list[dict[str, Any]] = []
    for offset, raw in enumerate(parts):
        part = deepcopy(raw)
        if offset == 0:
            part[id_field] = str(item_id)
            # 锚点延续:保留原分镜的 generated_assets(若 Agent 在 parts[0] 自带了
            # generated_assets,以原分镜实际值为准,不让 Agent 凭空写资产路径)。
            if isinstance(anchor_assets, dict):
                part["generated_assets"] = deepcopy(anchor_assets)
            else:
                # 锚点 generated_assets 形态异常(非 dict,如 list/str 等脏数据)→ 退化为空 dict。
                # Agent 在 parts[0] 自带的 generated_assets(deepcopy(raw) 已拷入 part)也会被这里
                # 覆盖丢弃。warning 让运维知道,符合 ADR-0003 增补「禁止零信号成功」原则。
                # anchor_assets is None 视为"原本就没有"正常态,不 warn。
                if anchor_assets is not None:
                    logger.warning(
                        "split_segment: 锚点 %r generated_assets 形态异常(%s),退化为空 dict",
                        item_id,
                        type(anchor_assets).__name__,
                    )
                part["generated_assets"] = {}
            # 尾帧同锚点延续：以原分镜实际值为准，不让 Agent 在 parts[0] 凭空改写快照路径。
            if anchor_end_frame is None:
                part.pop("end_frame_image", None)
            else:
                part["end_frame_image"] = anchor_end_frame
        else:
            new_id = _next_suffixed_id(str(item_id), taken)
            taken.add(new_id)
            part[id_field] = new_id
            part["generated_assets"] = {}
            # 新 id 名下没有快照，Agent 自带的值只会指向锚点的快照。
            part.pop("end_frame_image", None)
        new_parts.append(part)

    items[idx : idx + 1] = new_parts
    return script
