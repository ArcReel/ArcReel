"""正式剧本编辑工具的声明。"""

from __future__ import annotations

from lib.i18n import _ as translate
from lib.script.storyboard_mentions import render_storyboard_mention_warnings
from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.tool_runtime import PatchEpisodeScriptRequest, ScriptPatchResult, patch_episode_script


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
        "按 canonical revision 原子执行有序剧本 operations（update / insert / remove / split）。"
        "先在内存形成完整 candidate，再统一校验 schema、项目引用、Artifact Manifest 与 SpeechComposition；"
        "任一问题整批零写入，结果 success=false，problems 给出稳定 code、operation_index、unit/field location 与 "
        "next_action，发声组合问题另附 speech_admission。不会删除或清空已有付费媒体；改了 image_prompt / "
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

SCRIPT_EDITING_TOOLS = (PATCH_EPISODE_SCRIPT,)

__all__ = ["PATCH_EPISODE_SCRIPT", "SCRIPT_EDITING_TOOLS"]
