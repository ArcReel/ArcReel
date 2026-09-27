"""修复通道工具的声明：项目数据升级失败时仍可用的受控写入，以及重跑升级链。

这里的写入豁免只属于 Agent 工具的修复通道，不延伸到 REST：写剧本正文的路由仍受迁移阻断。
"""

from __future__ import annotations

from server.agent_toolset.declaration import Exempt, ToolDeclaration
from server.tool_runtime import (
    EPISODE_META_FIELDS,
    PROJECT_OVERVIEW_FIELDS,
    PROJECT_SETTINGS,
    NoArguments,
    PatchEpisodeMetaRequest,
    PatchProjectRequest,
    RenameAssetRequest,
    patch_episode_meta,
    patch_project,
    rename_asset,
    retry_project_migration,
)

_REPAIR_WRITE = Exempt(
    "修复通道：项目数据升级失败时，只能经受控的元数据写入修好被点名的集 / 文件再重试升级；"
    "写入经结构校验，不触及剧本正文。"
)

PATCH_PROJECT = ToolDeclaration(
    name="patch_project",
    description=(
        "新增或修改 project.json，三种形态三选一，同时给出多个或都不给会被拒：(1) 资产 upsert（传 table + "
        "entries），按资产表 + 名称 upsert，名称不存在则新增、存在则合并改字段；(2) 顶层 settings 写入（传 "
        f"settings），白名单字段 {list(PROJECT_SETTINGS)}，值为 null 时清除；(3) 项目概述编辑（传 overview），"
        f"白名单字段 {list(PROJECT_OVERVIEW_FIELDS)}，只改传入字段、概述不存在时创建。写入后结构非法时不落盘"
        "并返回 problem。资产改名不要用本工具（会变成「新名新建 + 旧名残留」），改用 rename_asset。"
        "项目数据升级失败时仍可调用，用于修复。"
    ),
    request_model=PatchProjectRequest,
    migration=_REPAIR_WRITE,
    domain_key="project_patch",
    handler=patch_project,
)

PATCH_EPISODE_META = ToolDeclaration(
    name="patch_episode_meta",
    description=(
        f"编辑一集剧本的顶层元数据字段（非分镜级），白名单字段 {list(EPISODE_META_FIELDS)}。分集标题以剧本顶层 "
        "title 为唯一真相源，改后自动镜像到 project.json 的分集列表。改分镜内部字段请用 patch_episode_script。"
        "项目数据升级失败时仍可调用，用于修复。"
    ),
    request_model=PatchEpisodeMetaRequest,
    migration=_REPAIR_WRITE,
    domain_key="episode_meta_patch",
    handler=patch_episode_meta,
)

RENAME_ASSET = ToolDeclaration(
    name="rename_asset",
    description=(
        "级联重命名资产：一次改齐资产表 key、全部剧集剧本与 script_plan 草稿中的名称引用（引用数组 / speaker / "
        "@[名称] mention），以及按名命名的关联文件与版本历史。新名与同表既有资产冲突时整体拒绝、不落盘。"
        "改名不影响全局资产库。资产改名一律用本工具，不要用 patch_project。项目数据升级失败时仍可调用，用于修复。"
    ),
    request_model=RenameAssetRequest,
    migration=_REPAIR_WRITE,
    domain_key="asset_rename",
    handler=rename_asset,
)

RETRY_PROJECT_MIGRATION = ToolDeclaration(
    name="retry_project_migration",
    description=(
        "重跑本项目的数据升级链（含产物补录）。升级失败时项目被阻断，阻断期仍可用的写入工具只有 patch_project / "
        "patch_episode_meta / rename_asset；patch_episode_script 一律被拒。按失败明细用这三个工具修好被点名的集 / "
        "文件，再调用本工具；它们改不到的位置（如剧本正文类违约）如实报告卡点给用户，不要反复重试。"
        "幂等：已是最新版本时直接返回成功。成功返回新的制作计划 workflow_plan；失败返回 project_migration_failed "
        "problem，params.details 中含结构化明细（episode / file / violation）。"
    ),
    request_model=NoArguments,
    migration=Exempt("修复入口本身：重跑升级链正是解除迁移阻断的途径。"),
    domain_key="migration_retry",
    handler=retry_project_migration,
)

REPAIR_CHANNEL_TOOLS = (PATCH_PROJECT, PATCH_EPISODE_META, RENAME_ASSET, RETRY_PROJECT_MIGRATION)

__all__ = [
    "PATCH_EPISODE_META",
    "PATCH_PROJECT",
    "RENAME_ASSET",
    "REPAIR_CHANNEL_TOOLS",
    "RETRY_PROJECT_MIGRATION",
]
