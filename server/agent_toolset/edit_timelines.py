"""剪辑时间线工具的声明：按脚本新建、列出与读取。"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.edit_timelines import (
    CreateTimelineRequest,
    ListTimelinesRequest,
    ReadTimelineRequest,
    create_timeline,
    list_timelines,
    read_timeline,
    timeline_list_summary,
    timeline_readout_summary,
)

CREATE_TIMELINE = ToolDeclaration(
    name="create_timeline",
    description=(
        "为一集新建一条具名剪辑时间线，作为剪辑的起点。from=script 按当前脚本顺序排列每个视频单元："
        "整段使用、全部硬切，画外音单位原声 0.3、台词与无人声单位 1.0，旁白挂在该单元的第一个片段上。"
        "剪辑片段的画面一律取视频单元的 current 视频版本。剪辑时间线不随脚本自动变化，"
        "脚本改动后在 issues 里看到差异。同一集可以有多条剪辑时间线，显示名不能重名，"
        "重名返回 timeline_name_conflict；集不存在返回 episode_not_found。结果与 read_timeline 同形。"
    ),
    request_model=CreateTimelineRequest,
    migration=BLOCKED,
    domain_key="edit_timeline",
    handler=create_timeline,
    summary=timeline_readout_summary,
)

LIST_TIMELINES = ToolDeclaration(
    name="list_timelines",
    description=(
        "列出项目里的剪辑时间线：id、集号、显示名、最新修订号、剪辑片段数与最近修改时间，按集与创建顺序排列。"
        "只读，无副作用。"
    ),
    request_model=ListTimelinesRequest,
    migration=BLOCKED,
    domain_key="edit_timelines",
    handler=list_timelines,
    summary=timeline_list_summary,
)

READ_TIMELINE = ToolDeclaration(
    name="read_timeline",
    description=(
        "读取一条剪辑时间线最新修订的完整内容，revision 为当前修订号。只读，无副作用；"
        "ID 不存在返回 timeline_not_found。时间一律以秒为单位，最多三位小数。"
        "clips 按播放顺序排列，每个剪辑片段带 id（如 c3，在这条时间线内稳定，与创作者沟通时用它指代片段）、"
        "unit_id、start 与 duration（服务端算好的绝对起点与时长）、source_volume（原声音量 0–1）、"
        "narration（旁白起止，end 为 null 表示还没有旁白配音）和 status："
        "ready 可用；video_missing 时时长暂按编排时长占位；unit_deleted 时渲染跳过、时长计 0。"
        "issues 每条带 code、severity（blocking 阻断出片；warning、info 不阻断）、"
        "applies_to（all，或 with_narration 只影响带旁白版本）与相关的 clip_ids / unit_id："
        "video_missing 视频单元还没有可用视频；unit_deleted 片段引用的视频单元已从脚本删除；"
        "unit_unused 脚本里的视频单元没进这条时间线。"
    ),
    request_model=ReadTimelineRequest,
    migration=BLOCKED,
    domain_key="edit_timeline",
    handler=read_timeline,
    summary=timeline_readout_summary,
)

EDIT_TIMELINE_TOOLS = (CREATE_TIMELINE, LIST_TIMELINES, READ_TIMELINE)

__all__ = ["CREATE_TIMELINE", "EDIT_TIMELINE_TOOLS", "LIST_TIMELINES", "READ_TIMELINE"]
