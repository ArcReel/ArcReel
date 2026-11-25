"""正式剧本编辑工具的声明。"""

from __future__ import annotations

from pydantic import ValidationError

from lib.i18n import _ as translate
from lib.script.script_plan_patch import EDITABLE_PLAN_FIELDS, PatchScriptPlanRequest, ScriptPlanPatchResult
from lib.script.storyboard_mentions import render_storyboard_mention_warnings
from server.agent_toolset.declaration import BLOCKED, ToolDeclaration, invalid_request_problem
from server.tool_runtime import (
    PatchEpisodeScriptRequest,
    ScriptPatchResult,
    ToolProblem,
    patch_episode_script,
    patch_script_plan,
)


def _script_patch_summary(result: ScriptPatchResult) -> str:
    if result.success:
        lines = [
            f"✅ patch_episode_script 已原子提交；revision={result.revision}；"
            f"affected_ids={', '.join(result.affected_ids) or '无'}",
            *(f"⚠️ {line}" for line in render_storyboard_mention_warnings(result.warnings, translate)),
        ]
        if result.regeneration_required_ids:
            lines.append(
                f"⚠️ 改了 prompt 的分镜（{', '.join(result.regeneration_required_ids)}）须紧接着重新生成对应图/视频。"
            )
        return "\n".join(lines)
    problem = result.problems[0]
    locations = ", ".join(
        ".".join(str(part) for part in location.path) for location in problem.locations if location.path
    )
    return (
        f"patch_episode_script 失败: code={problem.code}, operation_index={problem.operation_index}, "
        f"unit_id={problem.unit_id}, location={locations or None}, next_action={problem.next_action}"
    )


PATCH_EPISODE_SCRIPT = ToolDeclaration(
    name="patch_episode_script",
    description=(
        "按 canonical revision 原子执行有序剧本 operations（update / insert / move / remove / split）。"
        "update 的嵌套字段使用点号路径，如 image_prompt.scene。先在内存形成完整 candidate，再统一校验 schema、项目引用、Artifact Manifest 与 SpeechComposition；"
        "任一问题整批零写入，结果 success=false，problems 给出稳定 code、operation_index、unit/field location 与 "
        "next_action，发声组合问题另附 speech_admission。insert 与 split 的新条目 id 由服务端分配：取本集用过的最大序号之后的号，"
        "编号不连续是正常的，已删除条目的编号不会再发出；新 id 见结果的 affected_ids 或重新读取剧本。"
        "after_id 省略时插到最前，空脚本的第一条即如此。"
        "remove 移除的条目连同它的媒体与版本历史移入项目回收站，可以恢复。"
        "move 调整顺序：把 id 移到 after_id 之后，after_id 省略时移到最前；条目连同已生成的媒体一起移动。"
        "移动后，前一条分镜变了的分镜图（章节切分点上的除外）转为过期，不列入 regeneration_required_ids，是否重新生成由用户决定。"
        "脚本里现有条目的付费媒体不会被删除或清空；改了 image_prompt / "
        "video_prompt 的条目列在 regeneration_required_ids，须紧接着显式重新生成；画面描述里未绑定的 @[名称] "
        "列在 warnings。写入对应原文 source_text 时，项目有源文则须是本集源文的逐字片段，否则以 "
        "source_text_not_verbatim 拒绝。剧本不存在时返回 script_not_found problem。"
    ),
    request_model=PatchEpisodeScriptRequest,
    migration=BLOCKED,
    domain_key="script_patch",
    handler=patch_episode_script,
    summary=_script_patch_summary,
    is_error=lambda result: not result.success,
)


def _plan_patch_summary(result: ScriptPlanPatchResult) -> str:
    if not result.success:
        problem = result.problems[0]
        location = ", ".join(".".join(map(str, item.path)) for item in problem.locations)
        return (
            f"patch_script_plan 失败。code={problem.code}, revision={result.revision}, "
            f"operation_index={problem.operation_index}, location={location or None}, "
            f"next_action={problem.next_action}。{problem.reason}"
        )
    return (
        f"patch_script_plan 已原子提交。revision={result.revision}，"
        f"affected_ids={', '.join(result.affected_ids) or '无'}，"
        f"确认阻断项 {len(result.blocking_issues)} 条，软违约 {len(result.soft_violations)} 条。"
    )


def _plan_patch_request_error(exc: ValidationError) -> ToolProblem:
    problem = invalid_request_problem(exc)
    for error in exc.errors():
        location = error["loc"]
        if len(location) > 1 and location[0] == "operations" and isinstance(location[1], int):
            return ToolProblem(
                problem.code,
                problem.detail,
                action="fix_operation",
                params={
                    **(problem.params or {}),
                    "operation_index": location[1],
                    "location": list(location),
                    "next_action": "fix_operation",
                },
            )
    return problem


PATCH_SCRIPT_PLAN = ToolDeclaration(
    name="patch_script_plan",
    description=(
        "用 get_script_plan_content 返回的 revision 原子修改指定集的未确认脚本规划。"
        "按顺序执行 update / insert / remove / move / split / set_title / set_new_assets。"
        "update 的 fields 仅接受本路线的顶层字段："
        + "；".join(f"{kind}：{' / '.join(sorted(fields))}" for kind, fields in EDITABLE_PLAN_FIELDS.items())
        + "。insert 的 item 与 split 的 parts 填写条目内容，服务端管理 ID 和派生字段。"
        "insert / move 的 after_id 省略时放到最前；split 的首条保留原 ID，新增 ID 见 affected_ids。"
        "set_title 适用于剧情演绎。set_new_assets 整表替换，空列表清空。"
        "失败时整批改动不落盘，按 problems 的位置、operation_index 和 next_action 修正。"
        "修订冲突时重新读取。已确认后改用 patch_episode_script 编辑正式脚本。"
        "确认阻断项与软违约随回执刷新，可继续分步修正。"
    ),
    request_model=PatchScriptPlanRequest,
    migration=BLOCKED,
    domain_key="script_plan_patch",
    handler=patch_script_plan,
    request_error=_plan_patch_request_error,
    summary=_plan_patch_summary,
    is_error=lambda result: not result.success,
)


SCRIPT_EDITING_TOOLS = (PATCH_EPISODE_SCRIPT, PATCH_SCRIPT_PLAN)

__all__ = ["PATCH_EPISODE_SCRIPT", "PATCH_SCRIPT_PLAN", "SCRIPT_EDITING_TOOLS"]
