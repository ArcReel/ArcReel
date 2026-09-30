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
        "只在创作者要求出片时调用。目前渲染的是不带旁白、不烧入字幕的版本。"
        "提交前检查所选修订，下列情况直接拒绝、不入队：有 blocking 级 issue（如 video_missing）返回 "
        "final_cut_blocked，params.issues 列出这些 issue；含转场或 BGM 返回 final_cut_content_unsupported，"
        "params 列出 clip_ids 与 bgm_ids；随包 ffmpeg 不可用返回 final_cut_ffmpeg_unavailable。"
        "渲染按任务开始时的修订取快照：渲染期间剪辑时间线又被改动，或显式渲染旧修订，成片一出来就是 stale，"
        "仍可下载。每条剪辑时间线只保留最新一份成片，再次渲染会覆盖它并把 version 加一。"
        "终态结果的 final_cut 带 revision、version、artifact_path、acceptance（expected_duration 为剪辑时间线时长，"
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
