"""剪辑时间线工具的声明：按脚本新建、列出、读取与批量编辑。"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.edit_timelines import (
    CreateTimelineRequest,
    EditTimelineRequest,
    ListTimelinesRequest,
    ReadTimelineRequest,
    create_timeline,
    edit_timeline,
    list_timelines,
    read_timeline,
    timeline_list_summary,
    timeline_readout_summary,
    timeline_write_summary,
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
        "列出项目里的剪辑时间线：id、集 ID、显示名、最新修订号、剪辑片段数与最近修改时间，按集与创建顺序排列。"
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
        "trim（入出点与所依据的视频版本 basis_version）、source_duration（current 视频全长，没有可用视频时为 null）、"
        "hold（尾部定格延长）、transition_to_next（到下一片段的转场，null 为硬切）、narration（旁白起止，end 为 null 表示还没有旁白配音）和 status："
        "ready 可用；video_missing 时时长暂按编排时长占位；unit_deleted 时渲染跳过、时长计 0。"
        "issues 每条带 code、severity（blocking 阻断出片；warning、info 不阻断）、"
        "applies_to（all，或 with_narration 只影响带旁白版本）与相关的 clip_ids / unit_id："
        "video_missing 视频单元还没有可用视频；unit_deleted 片段引用的视频单元已从脚本删除；"
        "unit_unused 脚本里的视频单元没进这条时间线；trim_ignored 截取所依据的视频版本已不是 current，"
        "渲染时暂用完整视频；hold_too_long 单个片段的定格延长超过 2 秒。"
    ),
    request_model=ReadTimelineRequest,
    migration=BLOCKED,
    domain_key="edit_timeline",
    handler=read_timeline,
    summary=timeline_readout_summary,
)

EDIT_TIMELINE = ToolDeclaration(
    name="edit_timeline",
    description=(
        "用一批按剪辑片段 ID 定位的操作修改一条剪辑时间线，整批原子提交为一个带改动摘要的新修订。"
        "base_revision 取自 read_timeline 的 revision，整批按它解读；操作按顺序执行，后一条看到前一条的结果。"
        "任一条非法时整批不生效，返回 operation_invalid，params 带 operation_index（从 0 起）、clip_id、field "
        "与 allowed（合法取值范围）。base_revision 已不是最新修订时：本批涉及的片段在那之后都没被改过，"
        "就照常应用到最新修订上，结果的 concurrent_revisions 列出期间的修订；否则返回 revision_conflict，"
        "带 latest_revision 与 conflicting_clip_ids，重新读取后再改。"
        "操作：insert 新建片段，ID 由服务端分配并在结果里返回，同一视频单元可以插入多次，"
        "未给 source_volume 时画外音单位 0.3、台词与无人声单位 1.0；delete；move；"
        "set_trim 按 current 视频版本设置入出点，换版本后截取作废、暂用完整视频；set_volume；set_hold；"
        "set_reason；set_transition。时间一律以秒为单位，最多三位小数，结果里是服务端规整后的值。"
        "转场挂在前一片段上，表示到下一片段的转场，不改变总时长：窗口以切点为中心、前后各占一半，"
        "一个片段两侧转场的时长之和不能超过它时长的两倍。insert、delete、move 让相邻关系变了的切点一律恢复硬切，"
        "包括被移动片段自己的转场；需要时在同一批里随后重新 set_transition。"
        "一个视频单元的旁白只挂在它的一个片段上：插入的片段在该单元还没有承载旁白的片段时承载旁白；"
        "删除承载片段时，旁白改挂到该单元剩下的第一个片段上。"
        "结果只含新 revision、一行确认 message、受影响片段的新状态 clips（字段同 read_timeline）、"
        "deleted_clip_ids、总时长 duration 与更新后的 issues，不返回整份时间线。"
    ),
    request_model=EditTimelineRequest,
    migration=BLOCKED,
    domain_key="timeline_edit",
    handler=edit_timeline,
    summary=timeline_write_summary,
)

EDIT_TIMELINE_TOOLS = (CREATE_TIMELINE, LIST_TIMELINES, READ_TIMELINE, EDIT_TIMELINE)

__all__ = ["CREATE_TIMELINE", "EDIT_TIMELINE", "EDIT_TIMELINE_TOOLS", "LIST_TIMELINES", "READ_TIMELINE"]
