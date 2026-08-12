"""SDK MCP tools for editing an episode script by id.

把 agent 对 ``scripts/*.json`` 的一切编辑收归这组工具：批量字段编辑（``patch_episode_script``，
一次改多分镜 × 多字段的 ``{分镜id: {字段路径: 值}}`` 映射）+ 结构性增删拆（``insert_segment`` /
``remove_segment`` / ``split_segment``）。每个工具在
``ProjectManager`` 的锁定式读-改-写上下文里调 ``lib.script_editor`` 的纯函数核心改 dict；
字段 patch 还联合持有项目锁，以便按同一份资产表重派生 reference unit 引用。退出时经写盘统一
入口 ``_write_script_unlocked`` 写回——继承「不更坏」结构校验、metadata 重算、加锁与
filename↔episode 一致性。结构错误当场以「不更坏」语义挡下并返回明确错误。

工具返回文本是 agent-facing（免 i18n）；显示名在 ``ARCREEL_MCP_TOOL_IDS`` 注册、补三语。
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from lib.reference_video import rederive_unit_references
from lib.script_editor import (
    ScriptEditError,
    insert_segment,
    patch_field,
    remove_segment,
    resolve_items,
    split_segment,
)
from lib.speech_composition import (
    SpeechAdmissionError,
    admit_script_unit,
    refresh_video_unit_replan_state,
    require_script_unit_admitted,
)
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error, validate_script_filename

# 改了这些顶层字段路径的分镜须紧接着重新生成对应图/视频（工具不自动作废旧资产）。
_REGEN_TRIGGER_FIELDS = ("image_prompt", "video_prompt")


def _speech_admission_error(name: str, exc: SpeechAdmissionError) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": f"{name} 失败: unit {exc.admission.unit_id} 发声准入未通过；请按 problems 的 action 修复",
            }
        ],
        "is_error": True,
        "speech_admission": exc.admission.to_dict(),
    }


def _item_ids(script: dict[str, Any]) -> list[str]:
    items, id_field, _kind = resolve_items(script)
    return [str(it.get(id_field)) for it in items if isinstance(it, dict)]


def patch_episode_script_tool(ctx: ToolContext):
    @tool(
        "patch_episode_script",
        "批量编辑一个剧本文件里多个分镜的多个字段：传 script（单文件名）+ edits 映射，"
        "形如 {分镜id: {字段路径: 新值}}。一次调用可改多分镜 × 多字段；单条编辑写成长度 1 的 map。"
        "分镜 id 由数据形状判别（segment_id/scene_id/unit_id/shot_id），各内容/生成模式通用。"
        "字段支持点分嵌套路径（如 image_prompt.scene、duration_seconds、video_prompt.action）。"
        "all-or-nothing 原子：任一编辑非法（id 未命中 / 字段路径不存在 / 改 generated_assets 或分镜 id / "
        "最终结构校验失败）→ 整批零落盘。前三类错误指出触发的分镜 id 与字段；最终结构校验失败按写盘统一入口的 "
        "Pydantic 字段路径（含数组下标，如 segments.4.video_prompt.x）报告。修正后整批重提即可。"
        "纯字段 setter，不触碰已生成资产——批量改了任意分镜的 image_prompt / video_prompt 后，"
        "须紧接着重新生成对应分镜的图/视频，否则会留下「新 prompt + 旧画面」的陈旧。"
        "reference video unit 的 shots 正文变化时，references 会从其中的已登记 @ mention 机械重派生。"
        "叶子字段不存在会被创建（允许补 LLM 漏写的 optional 字段如 video_prompt.dialogue）；"
        "拼写错误（如 image_prompt.scen 应为 image_prompt.scene）会经写盘统一入口的 Pydantic "
        "extra='forbid' 结构校验拒，提交前请确认字段名拼写正确。",
        {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（纯文件名，如 episode_1.json）；单集单文件，多集编辑每集一次调用",
                },
                "edits": {
                    "type": "object",
                    "description": "{ 分镜id: { 字段路径: 新值 } } 映射；至少一个分镜、每个分镜至少一个字段。"
                    "同一分镜的所有字段写在它唯一的子映射里——勿重复该分镜 id（JSON 重复键只保留最后一个，"
                    "会静默丢失前面的编辑）。字段路径支持点分嵌套；不可改 generated_assets 与分镜 id 字段；"
                    "叶子不存在会创建，但需是合法 schema 字段否则写盘被拒。",
                },
            },
            "required": ["script", "edits"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            edits = args.get("edits")
            if not isinstance(edits, dict) or not edits:
                raise ScriptEditError("edits 必须是非空 { 分镜id: { 字段路径: 值 } } 映射")

            applied: list[tuple[str, list[str]]] = []
            regen_ids: list[str] = []
            project_out: dict[str, dict[str, Any]] = {}

            def _resolve_script(project: dict[str, Any]) -> str:
                # locked_episode_script 会在项目锁内再次调用 resolver；最后一次捕获因此与下方
                # script 写入处于同一临界区，资产改名/删除不能夹在引用重派生与落盘之间。
                project_out["project"] = project
                return script_filename

            # 全批在同一个 locked_episode_script 上下文内逐条 patch_field 就地改 dict：任一编辑抛异常即
            # 冒出 with 体 → 写盘被跳过 → 整批零落盘；全部 apply 后写盘统一入口跑一次「不更坏」
            # 结构校验，非法则整体拒。原子性由 locked_episode_script 承重，无需额外事务管线。
            with ctx.pm.locked_episode_script(ctx.project_name, _resolve_script) as script:
                project = project_out["project"]
                items, id_field, kind = resolve_items(script)
                for raw_id, field_map in edits.items():
                    scene_id = str(raw_id)
                    if not isinstance(field_map, dict) or not field_map:
                        raise ScriptEditError(f"分镜 {scene_id} 的编辑必须是非空 {{ 字段路径: 值 }} 映射")
                    unit = next(
                        (item for item in items if isinstance(item, dict) and str(item.get(id_field)) == scene_id),
                        None,
                    )
                    previous_shots = unit.get("shots") if unit is not None else None
                    previous_references = unit.get("references") if unit is not None else None
                    previous_speech = (
                        admit_script_unit(kind, unit, ignore_marker=True).preparation if unit is not None else None
                    )
                    fields: list[str] = []
                    for raw_field, value in field_map.items():
                        field = str(raw_field)
                        try:
                            patch_field(script, scene_id, field, value)
                        except ScriptEditError as exc:
                            raise ScriptEditError(f"分镜 {scene_id} 的字段 {field}：{exc}") from exc
                        fields.append(field)
                        if field.split(".", 1)[0] in _REGEN_TRIGGER_FIELDS and scene_id not in regen_ids:
                            regen_ids.append(scene_id)
                    edited_roots = {field.split(".", 1)[0] for field in fields}
                    body_changed = False
                    if unit is not None:
                        body_changed = (
                            kind == "video_units" and "shots" in edited_roots and unit.get("shots") != previous_shots
                        )
                        if body_changed:
                            rederive_unit_references([unit], project)
                        admission = admit_script_unit(kind, unit, ignore_marker=True)
                        if admission.preparation != previous_speech:
                            if not admission.allowed:
                                raise SpeechAdmissionError(admission)
                            if kind != "video_units":
                                unit.pop("needs_replan", None)
                    if unit is not None and kind == "video_units":
                        references_changed = (
                            "references" in edited_roots and unit.get("references") != previous_references
                        )
                        refresh_video_unit_replan_state(
                            unit,
                            allow_clear=body_changed or references_changed or "duration_seconds" in edited_roots,
                            content_changed=body_changed,
                        )
                    applied.append((scene_id, fields))

            lines = [f"✅ 已更新 {len(applied)} 个分镜的字段："]
            lines += [f"  {sid}: {', '.join(flds)}" for sid, flds in applied]
            if regen_ids:
                lines.append(
                    f"⚠️  改了 image_prompt / video_prompt 的分镜（{', '.join(regen_ids)}）"
                    "须紧接着重新生成对应图/视频，否则会留下「新 prompt + 旧画面」的陈旧。"
                )
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}
        except SpeechAdmissionError as exc:
            return _speech_admission_error("patch_episode_script", exc)
        except Exception as exc:  # noqa: BLE001
            return tool_error("patch_episode_script", exc)

    return _handler


def insert_segment_tool(ctx: ToolContext):
    @tool(
        "insert_segment",
        "在指定分镜 id 之后插入一个新分镜（segment/scene/unit）。新分镜由你提供完整内容，"
        "其 id 由系统分配（派生自锚点 id 的稳定后缀，不重排其余分镜），资产为空待生成。"
        "reference 模式插入的是 video_unit（含 shots）。",
        {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "剧本文件名（纯文件名）"},
                "after_id": {"type": "string", "description": "在此分镜 id 之后插入"},
                "item": {
                    "type": "object",
                    "description": "新分镜的完整内容对象（除 id/generated_assets 外的所有必填字段；id 由系统分配）",
                },
            },
            "required": ["script", "after_id", "item"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            after_id = str(args["after_id"])
            item = args["item"]
            project_out: dict[str, dict[str, Any]] = {}

            def _resolve_script(project: dict[str, Any]) -> str:
                project_out["project"] = project
                return script_filename

            with ctx.pm.locked_episode_script(ctx.project_name, _resolve_script) as script:
                insert_segment(script, after_id, item)
                items, _id_field, kind = resolve_items(script)
                anchor_index = next(i for i, entry in enumerate(items) if str(entry.get(_id_field)) == after_id)
                inserted = items[anchor_index + 1]
                if kind == "video_units":
                    rederive_unit_references([inserted], project_out["project"])
                require_script_unit_admitted(kind, inserted, ignore_marker=True)
                new_ids = _item_ids(script)
            return {
                "content": [{"type": "text", "text": f"✅ 已在 {after_id} 之后插入新分镜\n当前分镜顺序: {new_ids}"}]
            }
        except SpeechAdmissionError as exc:
            return _speech_admission_error("insert_segment", exc)
        except Exception as exc:  # noqa: BLE001
            return tool_error("insert_segment", exc)

    return _handler


def remove_segment_tool(ctx: ToolContext):
    @tool(
        "remove_segment",
        "按 id 删除一个分镜（segment/scene/unit）。其余分镜的 id 不变、不重排，被删分镜的"
        "已生成资产随之失效。reference 模式删除的是 video_unit。",
        {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "剧本文件名（纯文件名）"},
                "id": {"type": "string", "description": "要删除的分镜 id"},
            },
            "required": ["script", "id"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            item_id = str(args["id"])
            with ctx.pm.locked_script(ctx.project_name, script_filename) as script:
                remove_segment(script, item_id)
                new_ids = _item_ids(script)
            return {"content": [{"type": "text", "text": f"✅ 已删除分镜 {item_id}\n当前分镜顺序: {new_ids}"}]}
        except Exception as exc:  # noqa: BLE001
            return tool_error("remove_segment", exc)

    return _handler


def split_segment_tool(ctx: ToolContext):
    @tool(
        "split_segment",
        "把一个分镜按你提供的各部分内容拆成多个（≥2 份）。**首份保留原 id 且 generated_assets 不动**"
        "（锚点延续,与 insert_segment 资产保留语义对齐）;其余分配稳定的派生 id 且 generated_assets "
        "清空,需重新生成。只想微调原分镜内容请用 patch_episode_script——split 适合"
        "「这一镜信息量太大,拆成 N 镜分别表达」这类身份变化的场景。reference 模式下 unit 的 "
        "duration_seconds 是独立字段（不由 shots 派生），拆分后每份都要给出符合模型档位的值。",
        {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "剧本文件名（纯文件名）"},
                "id": {"type": "string", "description": "要拆分的分镜 id"},
                "parts": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "拆分后各部分的完整内容对象（≥2 个;id 由系统分配。首份保留原 id 的 "
                    "generated_assets,其余清空）",
                },
            },
            "required": ["script", "id", "parts"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            item_id = str(args["id"])
            parts = args["parts"]
            project_out: dict[str, dict[str, Any]] = {}

            def _resolve_script(project: dict[str, Any]) -> str:
                project_out["project"] = project
                return script_filename

            with ctx.pm.locked_episode_script(ctx.project_name, _resolve_script) as script:
                split_segment(script, item_id, parts)
                items, id_field, kind = resolve_items(script)
                anchor_index = next(i for i, item in enumerate(items) if str(item.get(id_field)) == item_id)
                generated = items[anchor_index : anchor_index + len(parts)]
                if kind == "video_units":
                    rederive_unit_references(generated, project_out["project"])
                for item in generated:
                    require_script_unit_admitted(kind, item, ignore_marker=True)
                new_ids = _item_ids(script)
            return {
                "content": [
                    {"type": "text", "text": f"✅ 已把分镜 {item_id} 拆为 {len(parts)} 份\n当前分镜顺序: {new_ids}"}
                ]
            }
        except SpeechAdmissionError as exc:
            return _speech_admission_error("split_segment", exc)
        except Exception as exc:  # noqa: BLE001
            return tool_error("split_segment", exc)

    return _handler


__all__ = [
    "patch_episode_script_tool",
    "insert_segment_tool",
    "remove_segment_tool",
    "split_segment_tool",
]
