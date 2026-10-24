"""项目回收站工具的声明：删除未引用文件或历史版本（只进回收站）、列出删除记录、恢复删除记录。

不声明清空与彻底删除：两者不可恢复，只开放给创作者在文件视图里操作（``docs/adr/0107``）。
"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, Exempt, ToolDeclaration
from server.media_tools.project_trash import (
    DeleteProjectFileRequest,
    RestoreTrashRecordRequest,
    delete_project_file,
    delete_project_file_summary,
    list_trash_records,
    restore_trash_record,
    restore_trash_record_summary,
)
from server.tool_runtime import NoArguments

DELETE_PROJECT_FILE = ToolDeclaration(
    name="delete_project_file",
    description=(
        "把项目里的一个未引用文件或一个非当前的历史版本（versions/ 下的快照）移入项目回收站，成为一条删除记录，"
        "30 天内可用 restore_trash_record 恢复，历史版本恢复后回到版本历史。"
        "这是 Agent 删除项目文件的唯一途径：媒体目录与 versions/ 不能直接写入或删除。"
        "删除前在项目锁内按引用关系重新判定，被项目数据、剧本、产物清单或版本历史引用的文件"
        "返回 file_view_delete_not_allowed，当前版本返回 version_delete_current，未纳入整本源文的源文件返回 file_view_delete_source_in_episodes，"
        "文件不存在返回 file_not_found。只删创作者明确要删的文件；回收站的清空与彻底删除只能由创作者在文件视图里操作。"
        "成功返回 record（含 id、files、deleted_at、size）。"
    ),
    request_model=DeleteProjectFileRequest,
    migration=BLOCKED,
    domain_key="delete_project_file",
    handler=delete_project_file,
    summary=delete_project_file_summary,
)

LIST_TRASH_RECORDS = ToolDeclaration(
    name="list_trash_records",
    description=(
        "列出项目回收站里的删除记录，新的在前：每条含 id、object（被删对象的类型与身份）、files（删除前的项目内路径）、"
        "deleted_at、origin（user 为创作者发起，agent 为 Agent 发起）与 size；另返回 total_size 与 retention_days。"
        "只读，无副作用。"
    ),
    request_model=NoArguments,
    migration=Exempt("只读：删除记录在项目私有区，不读写产物清单、不签发写入凭据；迁移失败时仍须可查。"),
    domain_key="trash_records",
    handler=list_trash_records,
)

RESTORE_TRASH_RECORD = ToolDeclaration(
    name="restore_trash_record",
    description=(
        "恢复一条删除记录。恢复只新增、不覆盖：原路径空闲则回原路径；原路径已有该资源的当前文件时，"
        "被删文件作为它的历史版本加回，当前版本不变；其余被占用的路径换名为「name (n).ext」。"
        "记录不存在（可能已被恢复或清除）返回 trash_record_not_found。"
        "成功返回 restored：每项含 original（删除前路径）、path（恢复后路径）与 as_history。"
    ),
    request_model=RestoreTrashRecordRequest,
    migration=BLOCKED,
    domain_key="restore_trash_record",
    handler=restore_trash_record,
    summary=restore_trash_record_summary,
)

PROJECT_TRASH_TOOLS = (DELETE_PROJECT_FILE, LIST_TRASH_RECORDS, RESTORE_TRASH_RECORD)

__all__ = ["DELETE_PROJECT_FILE", "LIST_TRASH_RECORDS", "PROJECT_TRASH_TOOLS", "RESTORE_TRASH_RECORD"]
