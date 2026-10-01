"""剪映草稿工具的声明：把剪辑时间线导出成剪映草稿。"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.jianying_drafts import (
    ExportJianyingDraftRequest,
    export_jianying_draft,
    jianying_draft_projection,
    jianying_draft_summary,
)

EXPORT_JIANYING_DRAFT = ToolDeclaration(
    name="export_jianying_draft",
    description=(
        "把一条剪辑时间线导出成剪映草稿：在本机生成，不付费，登记为产物，创作者在 Web 端填写本机剪映草稿目录后下载。"
        "只在创作者要求导出剪映草稿时调用。草稿有一条视频轨和字幕轨，带旁白版本另有旁白轨；旁白或字幕互相重叠时"
        "多开一条轨，每条轨内不重叠，新增的字幕轨整体上移。截取、原声音量、定格延长与转场都按剪辑时间线写入。"
        "提交前检查所选修订与旁白版本，下列情况直接拒绝、不入队：有适用于该版本的 blocking 级 issue（如 video_missing）"
        "返回 jianying_draft_blocked，params.issues 列出这些 issue；含 BGM 返回 jianying_draft_content_unsupported，"
        "params.bgm_ids 列出 BGM 片段；没有可导出的剪辑片段返回 jianying_draft_empty；非 TTS 配音项目请求 with_narration 返回 "
        "jianying_draft_narration_unavailable。"
        "省略 revision 时导出提交时的最新修订：导出期间剪辑时间线又被改动，或显式导出旧修订，草稿一出来就是 stale，仍可下载。"
        "每条剪辑时间线的每个旁白版本只保留最新一份草稿，再次导出会覆盖它并把 version 加一。"
        "终态结果的 jianying_draft 带 revision、narration、version、artifact_path、duration（剪辑时间线时长，单位秒）"
        "与 warnings（warning 级 issues）。"
    ),
    request_model=ExportJianyingDraftRequest,
    migration=BLOCKED,
    domain_key="jianying_draft",
    handler=export_jianying_draft,
    long_task=True,
    summary=jianying_draft_summary,
    projection=jianying_draft_projection,
)

JIANYING_DRAFT_TOOLS = (EXPORT_JIANYING_DRAFT,)

__all__ = ["EXPORT_JIANYING_DRAFT", "JIANYING_DRAFT_TOOLS"]
