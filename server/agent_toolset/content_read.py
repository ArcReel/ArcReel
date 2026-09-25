"""项目内容读取工具的声明。"""

from __future__ import annotations

from server.agent_toolset.declaration import READ_CHECK, Exempt, ToolDeclaration
from server.tool_runtime import (
    EpisodeScriptRequest,
    NoArguments,
    ProjectFileRequest,
    ScriptPlanContentRequest,
    SourceTextRequest,
    get_episode_script,
    get_project_content,
    get_script_plan_content,
    get_source_text,
    list_project_files,
    list_source_files,
    read_project_file,
)

_READ_ONLY_FILE = Exempt("只读：按路径直取项目文件，不经产物清单，也不签发写入凭据；迁移失败时仍须可读，供诊断与修复。")

GET_PROJECT_CONTENT = ToolDeclaration(
    name="get_project_content",
    description=(
        "读取项目 project.json 的完整内容（标题、内容模式、生成模式、风格、分集与资产设定等）"
        "及其 canonical revision。只读，无副作用。"
    ),
    request_model=NoArguments,
    migration=_READ_ONLY_FILE,
    domain_key="project_content",
    handler=get_project_content,
)

LIST_SOURCE_FILES = ToolDeclaration(
    name="list_source_files",
    description=(
        "列出项目 source/ 下的源文文件（UTF-8 的 .txt / .md），返回每个文件的项目相对路径、字节数与 etag，"
        "以及整份列表的 revision。只读，无副作用。读取正文用 get_source_text。"
    ),
    request_model=NoArguments,
    migration=_READ_ONLY_FILE,
    domain_key="source_files",
    handler=list_source_files,
)

GET_SOURCE_TEXT = ToolDeclaration(
    name="get_source_text",
    description=(
        "读取 source/ 下一个 UTF-8 源文文件的全文及其 revision 与 etag。只读，无副作用。"
        "source/ 之外的路径、非文本文件、symlink 与超过大小上限的文件会被拒绝。"
    ),
    request_model=SourceTextRequest,
    migration=_READ_ONLY_FILE,
    domain_key="source_text",
    handler=get_source_text,
)

GET_EPISODE_SCRIPT = ToolDeclaration(
    name="get_episode_script",
    description=(
        "读取一集剧本的 JSON 正文及其 canonical revision。把返回的 revision 原样作为 "
        "patch_episode_script 的 base_revision；剧本被改动后 revision 随之变化，须重新读取。只读，无副作用。"
        "项目数据升级失败时不签发 revision，返回 project_migration_failed problem；按其明细修复后调用 "
        "retry_project_migration。"
    ),
    request_model=EpisodeScriptRequest,
    migration=READ_CHECK,
    domain_key="episode_script",
    handler=get_episode_script,
)

GET_SCRIPT_PLAN_CONTENT = ToolDeclaration(
    name="get_script_plan_content",
    description=(
        "读取指定集当前正式的 script_plan（剧本规划中间态，位于 drafts/episode_N/）正文及其 canonical "
        "revision。只读，无副作用。项目不使用 script_plan 时返回 script_plan_not_applicable；"
        "该集尚无 script_plan 时返回 file_not_found。"
    ),
    request_model=ScriptPlanContentRequest,
    migration=_READ_ONLY_FILE,
    domain_key="script_plan_content",
    handler=get_script_plan_content,
)

LIST_PROJECT_FILES = ToolDeclaration(
    name="list_project_files",
    description=(
        "列出可供诊断读取的项目业务文本文件：project.json、source/ 源文、scripts/ 剧本与 drafts/episode_N/ "
        "下的 script_plan，返回路径、字节数与 etag，以及整份列表的 revision。敏感文件、symlink 与其他路径"
        "不列出。只读，无副作用。"
    ),
    request_model=NoArguments,
    migration=_READ_ONLY_FILE,
    domain_key="project_files",
    handler=list_project_files,
)

READ_PROJECT_FILE = ToolDeclaration(
    name="read_project_file",
    description=(
        "读取一个项目业务文本文件的内容及其 revision 与 etag：JSON 文件返回解析后的对象，文本文件返回字符串。"
        "可读范围与 list_project_files 一致，敏感文件、symlink、越界路径与超过大小上限的文件会被拒绝。"
        "只读，无副作用。有专用读取工具的文件（get_project_content、get_source_text、get_episode_script、"
        "get_script_plan_content）优先使用专用工具。"
    ),
    request_model=ProjectFileRequest,
    migration=_READ_ONLY_FILE,
    domain_key="project_file",
    handler=read_project_file,
)

CONTENT_READ_TOOLS = (
    GET_PROJECT_CONTENT,
    LIST_SOURCE_FILES,
    GET_SOURCE_TEXT,
    GET_EPISODE_SCRIPT,
    GET_SCRIPT_PLAN_CONTENT,
    LIST_PROJECT_FILES,
    READ_PROJECT_FILE,
)

__all__ = ["CONTENT_READ_TOOLS"]
