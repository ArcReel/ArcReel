"""视频版本选择工具的声明：切换视频单元的 current 视频版本。"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.video_versions import (
    SelectVideoVersionRequest,
    select_video_version,
    select_video_version_summary,
)

SELECT_VIDEO_VERSION = ToolDeclaration(
    name="select_video_version",
    description=(
        "把一个视频单元的 current 视频版本切换到指定版本号（即 Web 上的版本还原，同一服务）。"
        "current 是该单元在全项目的唯一答案，单元预览、剪辑与导出都取它，切换后立即生效。"
        "不收费、不重新生成，也不需要创作者确认。"
        "两种用途：① 首轮审阅后，某单元的另一候选版本更好，改用它；"
        "② 重新生成后验收，新版本不如旧版，改回旧版本。"
        "unit_id 在分镜图生视频为分镜 ID，在参考生视频为 unit_id。"
        "视频单元不存在返回 video_unit_not_found；版本号不存在返回 version_not_found，"
        "params.available_versions 列出该单元现有版本；缺完整取证描述的版本（如手动上传）返回 request_invalid。"
        "成功返回 current_version 与 file_path。"
    ),
    request_model=SelectVideoVersionRequest,
    migration=BLOCKED,
    domain_key="select_video_version",
    handler=select_video_version,
    summary=select_video_version_summary,
)

VIDEO_VERSION_TOOLS = (SELECT_VIDEO_VERSION,)

__all__ = ["SELECT_VIDEO_VERSION", "VIDEO_VERSION_TOOLS"]
