"""宫格分镜工具的声明：生成宫格联合图，经用户同意后切分落格。

``generate_grid`` 是长任务，结果钩子与生成类工具同形；``list_only`` 预览不是生成结果，立即返回，
放在工具名下。``split_grids`` 是普通写入工具。
"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.grid import (
    GenerateGridRequest,
    SplitGridsRequest,
    generate_grid,
    grid_is_error,
    grid_structured,
    grid_summary,
    split_grids,
)

GENERATE_GRID = ToolDeclaration(
    name="generate_grid",
    description=(
        "为已开启宫格装配的 storyboard 项目（generation_mode=storyboard 且 grid_storyboard=true）"
        "生成宫格联合图（付费生成；按 segment_break 分组，超出单张格数上限的分组切为多张）。"
        "本工具只产出联合图，不写任何分镜图：成功的分镜报告的是它所在宫格的联合图"
        "（artifact_path 为 grids/<grid_id>.png），状态为「联合图已就绪、未切分」；"
        "终态结果里未切分宫格的 grid_id 同时列在 grid_ids_awaiting_split。"
        "批次句柄与批次终态查询不带该字段：已就绪未切分的宫格见 skipped 各项的 artifact_path，"
        "本次新出的见成功分镜的 artifact_path。"
        "切分落格须先请用户在宫格面板审阅联合图，用户明确同意后再调用 split_grids。"
        "list_only=true 只预览规划、不入队，结果放在 generate_grid 键下。"
        "不传 scene_ids 时已失效但可用的旧图照常复用，联合图已就绪而未切分的宫格不重生成；"
        "同一组分镜的宫格正在生成时沿用在途任务，不重复计费。"
        "准入是整批的：任一分镜受阻（引用缺口、提示词待生成、与在途宫格部分重叠等）即整批不建任务，"
        "本身健康的分镜带 generation_batch_admission_withheld；已在生成中的宫格照常跑完，其分镜带 "
        "generation_active_task_conflict（action=wait_for_task）。"
        "终态结果的 generation_result 按 requested / succeeded / failed / blocked 逐分镜 ID 给出结局。"
        "项目数据升级失败时拒绝执行，返回 project_migration_failed problem。"
    ),
    request_model=GenerateGridRequest,
    migration=BLOCKED,
    domain_key="generation_result",
    handler=generate_grid,
    long_task=True,
    summary=grid_summary,
    projection=grid_structured,
    is_error=grid_is_error,
)

SPLIT_GRIDS = ToolDeclaration(
    name="split_grids",
    description=(
        "把一张或多张宫格的联合图切分落格：写成它覆盖的全部分镜的分镜图，旧分镜图留在版本历史里可回滚。"
        "只在用户明确同意时调用：generate_grid 完成后，先请用户在宫格面板审阅联合图（可重新生成、上传替换或回滚），"
        "用户确认要切分后，再传入这些 grid_id；不要在生成完成后自行调用。"
        "grid_id 取自 generate_grid 结果的 grid_ids_awaiting_split、成功分镜的 artifact_path（grids/<grid_id>.png），"
        "或 generate_grid 的 list_only 预览。"
        "逐宫格返回结果：已切分的列出写入的分镜，仍在生成、没有联合图或不存在的宫格跳过并说明原因；"
        "一张都没切分时返回 problem，params.results 带逐宫格原因。"
        "项目数据升级失败时拒绝执行，返回 project_migration_failed problem。"
    ),
    request_model=SplitGridsRequest,
    migration=BLOCKED,
    domain_key="split_grids",
    handler=split_grids,
)

GRID_STORYBOARD_TOOLS = (GENERATE_GRID, SPLIT_GRIDS)

__all__ = ["GENERATE_GRID", "GRID_STORYBOARD_TOOLS", "SPLIT_GRIDS"]
