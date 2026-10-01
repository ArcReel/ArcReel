"""项目入口工具的声明：列出、创建项目，上传与编辑源文。

``source/`` 是 Agent 的受保护写路径，源文只经这里的两个工具写入：改动整本源文的文件时按改动前后的对齐重映射分集账本。
"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration, UnscopedToolDeclaration
from server.tool_runtime import (
    CreateProjectToolRequest,
    EditSourceTextRequest,
    NoArguments,
    UploadSourceRequest,
    create_project,
    edit_source_text,
    list_projects,
    upload_source,
)

LIST_PROJECTS = UnscopedToolDeclaration(
    name="list_projects",
    description=(
        "列出当前项目根下可供后续工具寻址的 ArcReel 项目，返回每个项目的 name、标题、内容模式与生成模式。"
        "缺少 project.json 或无法读取的目录不列出。只读，无副作用。"
    ),
    request_model=NoArguments,
    domain_key="projects",
    handler=list_projects,
)

CREATE_PROJECT = UnscopedToolDeclaration(
    name="create_project",
    description=(
        "创建一个 ArcReel 项目并写入完整的项目元数据（标题、内容模式、生成模式、画面比例、旁白交付方式等），"
        "返回规范化后的项目 name 与 project.json 内容；后续工具以该 name 寻址项目。"
        "旁白交付方式缺省后期配音；选 use_tts 时省略的 TTS 模型、音色与语速以全局默认预填，写入后成为项目快照。"
        "同名项目已存在时返回 project_exists，不覆盖。广告/短片项目（content_mode=ad）不接受 default_duration "
        "与宫格分镜，target_duration 与 brief 仅广告/短片项目可用。元数据写入失败时回滚已创建的项目目录。"
    ),
    request_model=CreateProjectToolRequest,
    domain_key="project",
    handler=create_project,
)

UPLOAD_SOURCE = ToolDeclaration(
    name="upload_source",
    description=(
        "把一段文本源文规范化为 UTF-8 后写入项目 source/ 目录并登记，返回写入后的项目相对路径、识别出的原始编码"
        "与章节数。role=whole_source（默认）登记为整本源文的文件，接在文件清单末尾，供分集规划切分；只接受 .txt / .md "
        "文件名，episode_N.txt 形式的文件名留给集文件，会被拒绝；同名文件的处理由 on_conflict 决定。role=episode 登记为"
        "一集自带原文的集：分配新集 ID、写入 source/episode_{集 ID}.txt、接在播出顺序末尾，另返回集 ID，用户自行拆好的"
        "分集逐个这样上传。剧情演绎项目用 source_kind 标明这份原文是小说（缺省）还是用户写好的成品剧本，其他创作类型"
        "忽略这个参数。on_conflict=replace 覆盖已登记的整本源文文件时，保留它在清单里的位置，按新旧文本的对齐重映射"
        "原文范围触及它的切出集；波及切出集时先不写入，返回 confirmation_required=true、按类分组的受影响集清单 impact "
        "与 revision，把清单如实告知用户，确认后带上同一 revision 重新调用。要移除或退下的集还有排队或执行中的任务时"
        "拒绝。source/ 只经本工具与 edit_source_text 写入。写入项目文件。"
    ),
    request_model=UploadSourceRequest,
    migration=BLOCKED,
    domain_key="source",
    handler=upload_source,
)

EDIT_SOURCE_TEXT = ToolDeclaration(
    name="edit_source_text",
    description=(
        "修改已登记的源文：filename 指整本源文的一个文件，episode_id 指一集自带原文或无原文的集的原文（无原文的集写入后"
        "转为自带原文）。切出集的原文由整本源文派生，要改就改它所在的整本源文文件。replacements 按片段替换，每个 old_text "
        "须在当前原文里恰好出现一次；text 整段改写。改整本源文的文件时按改动前后的对齐重映射触及它的切出集：文字没变的"
        "集只平移，文字变了的集标「原文已重新规划」，整段被删的集退下或移除；波及切出集时先不写入，返回 "
        "confirmation_required=true、按类分组的受影响集清单 impact 与 revision，把清单如实告知用户，确认后带上同一 "
        "revision 重新调用。文件在 ArcReel 之外被改动过、尚未在 Web 端「分集」视图里更新分集账本时拒绝。删除、调序整本"
        "源文的文件由用户在「分集」视图里操作。写入项目文件。"
    ),
    request_model=EditSourceTextRequest,
    migration=BLOCKED,
    domain_key="source_change",
    handler=edit_source_text,
)

PROJECT_ENTRY_TOOLS = (LIST_PROJECTS, CREATE_PROJECT, UPLOAD_SOURCE, EDIT_SOURCE_TEXT)

__all__ = [
    "CREATE_PROJECT",
    "EDIT_SOURCE_TEXT",
    "LIST_PROJECTS",
    "PROJECT_ENTRY_TOOLS",
    "UPLOAD_SOURCE",
]
