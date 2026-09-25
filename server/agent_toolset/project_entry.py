"""项目入口工具的声明：列出、创建项目与上传源文。"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration, UnscopedToolDeclaration
from server.tool_runtime import (
    CreateProjectToolRequest,
    NoArguments,
    UploadSourceRequest,
    create_project,
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
        "创建一个 ArcReel 项目并写入完整的项目元数据（标题、内容模式、源文类型、生成模式、画面比例等），"
        "返回规范化后的项目 name 与 project.json 内容；后续工具以该 name 寻址项目。"
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
        "把一段文本源文规范化为 UTF-8 后写入项目 source/ 目录，供源文读取与分集规划使用；返回写入后的项目相对"
        "路径、识别出的原始编码与章节数。只接受 .txt / .md 文件名；同名文件的处理由 on_conflict 决定。"
        "写入项目文件。"
    ),
    request_model=UploadSourceRequest,
    migration=BLOCKED,
    domain_key="source",
    handler=upload_source,
)

PROJECT_ENTRY_TOOLS = (LIST_PROJECTS, CREATE_PROJECT, UPLOAD_SOURCE)

__all__ = [
    "CREATE_PROJECT",
    "LIST_PROJECTS",
    "PROJECT_ENTRY_TOOLS",
    "UPLOAD_SOURCE",
]
