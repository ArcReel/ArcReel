"""剪辑时间线工具的声明：新建（按脚本或复制）、列出、读取、批量编辑、改名、修订历史与回滚。"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.edit_timelines import (
    CreateTimelineRequest,
    EditTimelineRequest,
    ListRevisionsRequest,
    ListTimelinesRequest,
    ReadTimelineRequest,
    RenameTimelineRequest,
    RestoreRevisionRequest,
    create_timeline,
    edit_timeline,
    list_revisions,
    list_timelines,
    read_timeline,
    rename_timeline,
    restore_revision,
    revision_history_summary,
    timeline_list_summary,
    timeline_readout_summary,
    timeline_renamed_summary,
    timeline_write_summary,
)

CREATE_TIMELINE = ToolDeclaration(
    name="create_timeline",
    description=(
        "新建一条具名剪辑时间线，同一集可以有多条，显示名不能重名（timeline_name_conflict）。"
        "from=script：为 episode 集按当前脚本顺序排列每个视频单元，作为剪辑的起点："
        "整段使用、全部硬切，画外音单位原声 0.3、台词与无人声单位 1.0，旁白挂在该单元的第一个片段上；"
        "集不存在返回 episode_not_found。"
        "from=timeline：复制 timeline 的 revision 修订（省略时为最新修订）到同一集的新时间线，"
        "内容原样带过去（顺序、截取、原声音量、定格、转场、旁白落点、BGM），片段 ID 不变，新时间线从修订 1 起；"
        "适合大规模重构前留一份退路，或并排做出另一种剪法；源时间线不受影响，"
        "revision 不存在返回 revision_not_found。"
        "剪辑片段的画面一律取视频单元的 current 视频版本；剪辑时间线不随脚本自动变化，脚本改动后在 issues 里看到差异。"
        "结果与 read_timeline 同形。"
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
        "旁白相关的三种只在 TTS 配音项目里报告：narration_missing 画外音单位还没有旁白配音（blocking，只阻断带旁白版本）；"
        "narration_overrun 旁白压到下一段旁白上（params.cause=next_narration，clip_ids 为两个承载片段）"
        "或超出时间线末尾（cause=timeline_end）；narration_source_collision 旁白延伸到台词片段（cause=dialogue）"
        "或原声音量高于 0.3 的片段（cause=source_volume）上，clip_ids 为承载片段与被覆盖的片段。"
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
        "set_reason；set_transition；place_narration。时间一律以秒为单位，最多三位小数，结果里是服务端规整后的值。"
        "转场挂在前一片段上，表示到下一片段的转场，不改变总时长：窗口以切点为中心、前后各占一半，"
        "一个片段两侧转场的时长之和不能超过它时长的两倍。insert、delete、move 让相邻关系变了的切点一律恢复硬切，"
        "包括被移动片段自己的转场；需要时在同一批里随后重新 set_transition。"
        "一个视频单元的旁白只挂在它的一个片段上，从该片段起点开始，按配音实测时长播放，可以延伸到后续片段上："
        "插入的片段在该单元还没有承载旁白的片段时承载旁白；删除承载片段时，旁白改挂到该单元剩下的第一个片段上；"
        "place_narration 把旁白改挂到画外音单位的指定片段上，同一单元原先的承载片段随之卸下。"
        "结果只含新 revision、一行确认 message、受影响片段的新状态 clips（字段同 read_timeline）、"
        "deleted_clip_ids、总时长 duration 与更新后的 issues，不返回整份时间线。"
    ),
    request_model=EditTimelineRequest,
    migration=BLOCKED,
    domain_key="timeline_edit",
    handler=edit_timeline,
    summary=timeline_write_summary,
)

RENAME_TIMELINE = ToolDeclaration(
    name="rename_timeline",
    description=(
        "修改一条剪辑时间线的显示名，同一集内不能重名（timeline_name_conflict）。"
        "只改名字：剪辑内容、修订历史与时间线 ID 都不变，不产生新修订，已渲染的成片与剪映草稿也不因此过期。"
    ),
    request_model=RenameTimelineRequest,
    migration=BLOCKED,
    domain_key="edit_timeline_summary",
    handler=rename_timeline,
    summary=timeline_renamed_summary,
)

LIST_REVISIONS = ToolDeclaration(
    name="list_revisions",
    description=(
        "列出一条剪辑时间线的修订历史，按修订号从旧到新：number、parent、author（creator 创作者、arcreel_agent、"
        "external_agent）、summary（改动摘要）、created_at、clip_count、changed_clip_ids（该修订改动过的剪辑片段，"
        "没有记录时为 null）与 restored_from（回滚产生的修订，值为它还原到的修订号）。只读，无副作用；"
        "ID 不存在返回 timeline_not_found。要复制或回滚到某个修订时，先在这里确认修订号。"
    ),
    request_model=ListRevisionsRequest,
    migration=BLOCKED,
    domain_key="edit_timeline_revisions",
    handler=list_revisions,
    summary=revision_history_summary,
)

RESTORE_REVISION = ToolDeclaration(
    name="restore_revision",
    description=(
        "把一条剪辑时间线回滚到某个旧修订：以那个修订的内容追加一个新修订（restored_from 记录来源），"
        "历史不改写，回滚前的最新修订仍可再回滚回去。回滚总是作用在最新修订上，会整段替换当前内容，"
        "所以回滚前先用 list_revisions 与 read_timeline 确认目标修订和当前内容。"
        "目标修订的内容与最新修订相同时返回 revision_unchanged，修订不存在返回 revision_not_found。"
        "之后基于回滚前修订的 edit_timeline 会按被回滚改动的片段判定冲突。"
        "结果与 edit_timeline 同形：新 revision、message、受影响片段 clips（回滚后的状态）、"
        "deleted_clip_ids（回滚后不再存在的片段）、duration 与 issues。"
    ),
    request_model=RestoreRevisionRequest,
    migration=BLOCKED,
    domain_key="timeline_edit",
    handler=restore_revision,
    summary=timeline_write_summary,
)

EDIT_TIMELINE_TOOLS = (
    CREATE_TIMELINE,
    LIST_TIMELINES,
    READ_TIMELINE,
    EDIT_TIMELINE,
    RENAME_TIMELINE,
    LIST_REVISIONS,
    RESTORE_REVISION,
)

__all__ = [
    "CREATE_TIMELINE",
    "EDIT_TIMELINE",
    "EDIT_TIMELINE_TOOLS",
    "LIST_REVISIONS",
    "LIST_TIMELINES",
    "READ_TIMELINE",
    "RENAME_TIMELINE",
    "RESTORE_REVISION",
]
