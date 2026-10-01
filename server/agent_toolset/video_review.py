"""看素材工具的声明：为视频单元出联系表，以图片内容块交给 Agent 看。"""

from __future__ import annotations

from lib.video_review.contact_sheet import MAX_FRAMES_PER_SHEET
from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.video_review import (
    MAX_FRAMES_PER_CALL,
    InspectVideoUnitsRequest,
    inspect_video_units,
    inspect_video_units_images,
    inspect_video_units_projection,
    inspect_video_units_summary,
)

INSPECT_VIDEO_UNITS = ToolDeclaration(
    name="inspect_video_units",
    description=(
        "看视频单元的画面：为每个视频单元的一个视频版本均匀抽帧、拼成联系表，联系表作为图片内容块附在结果后面。"
        "只读，不收费。用来判断素材能否使用、哪里崩坏、候选版本孰优，以及入出点大致落在哪里。"
        f"每张联系表最多 {MAX_FRAMES_PER_SHEET} 帧，顶部标视频单元 ID 与版本号；每帧下方标视频单元 ID 与该帧的起始时刻，"
        "单位为秒，以视频首帧为 0，与 read_timeline 的入出点同一时间轴。一个单元的帧数超过一张的容量时分到多张。"
        "version 对列表中的每个单元都生效，省略时取各单元的 current 版本；比较同一单元的候选版本时，每个版本各调用一次。"
        f"每次调用最多 {MAX_FRAMES_PER_CALL} 帧：单元数 × frames 超出时按单元数平分，frames_per_unit 为实际每单元帧数；"
        f"单元数超过 {MAX_FRAMES_PER_CALL} 返回 frame_budget_exceeded，请分批调用。"
        "结果 units 按请求顺序排列，每项带 version、status 与 sheets：status 为 ok；video_missing 表示该单元没有可用视频或该版本缺文件；"
        "video_unreadable 表示视频无法解码，detail 说明原因。sheets 每项的 image 是附图序号（从 1 起），times 是各帧时刻。"
        "model_review 为预留字段，目前恒为 null。"
        "视频单元不存在返回 video_unit_not_found，params.unit_ids 列出不存在的 ID；指定版本不存在返回 version_not_found，"
        "params.available_versions 列出该单元现有版本。"
    ),
    request_model=InspectVideoUnitsRequest,
    migration=BLOCKED,
    domain_key="inspect_video_units",
    handler=inspect_video_units,
    summary=inspect_video_units_summary,
    projection=inspect_video_units_projection,
    images=inspect_video_units_images,
)

VIDEO_REVIEW_TOOLS = (INSPECT_VIDEO_UNITS,)

__all__ = ["INSPECT_VIDEO_UNITS", "VIDEO_REVIEW_TOOLS"]
