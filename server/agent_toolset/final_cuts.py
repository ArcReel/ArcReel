"""成片工具的声明：把剪辑时间线渲染成成片。"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.final_cuts import (
    RenderFinalCutRequest,
    final_cut_projection,
    final_cut_summary,
    render_final_cut,
)

RENDER_FINAL_CUT = ToolDeclaration(
    name="render_final_cut",
    description=(
        "把一条剪辑时间线渲染成成片：在本机用随包 ffmpeg 渲染，不付费，登记为产物并给出下载链接。"
        "只在创作者要求出片时调用。旁白版本（narration）与是否烧入字幕（burn_subtitles）决定成片的版本，"
        "各版本并存；带旁白版本把旁白配音混进整集音频，旁白越界照实渲染：重叠处同时响起，超出末尾的部分随成片截止。"
        "提交前按所选修订与版本检查，下列情况直接拒绝、不入队：有适用于该版本的 blocking 级 issue（如 video_missing，"
        "带旁白版本还有 narration_missing）返回 final_cut_blocked，params.issues 列出这些 issue；含 BGM 返回 "
        "final_cut_content_unsupported，params.bgm_ids 列出 BGM 片段；非 TTS 配音项目请求 with_narration 返回 "
        "final_cut_narration_unavailable；随包 ffmpeg 不可用返回 final_cut_ffmpeg_unavailable。"
        "省略 revision 时渲染提交时的最新修订：渲染期间剪辑时间线又被改动，或显式渲染旧修订，成片一出来就是 stale，"
        "仍可下载。每条剪辑时间线的每个版本只保留最新一份成片，再次渲染会覆盖它并把 version 加一。"
        "终态结果的 final_cut 带 revision、narration、subtitles、version、artifact_path、acceptance（expected_duration 为剪辑时间线时长，"
        "video_duration、audio_duration 为实测音视频流时长，单位秒）与 warnings（warning 级 issues），"
        "download_url 是下载链接。"
    ),
    request_model=RenderFinalCutRequest,
    migration=BLOCKED,
    domain_key="final_cut",
    handler=render_final_cut,
    long_task=True,
    summary=final_cut_summary,
    projection=final_cut_projection,
)

FINAL_CUT_TOOLS = (RENDER_FINAL_CUT,)

__all__ = ["FINAL_CUT_TOOLS", "RENDER_FINAL_CUT"]
