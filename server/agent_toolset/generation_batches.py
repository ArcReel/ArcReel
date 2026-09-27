"""生成批次查询与取消工具的声明。"""

from __future__ import annotations

from server.agent_toolset.declaration import Exempt, ToolDeclaration
from server.tool_runtime import GenerationBatchToolRequest, cancel_generation_batch, get_generation_batch

GET_GENERATION_BATCH = ToolDeclaration(
    name="get_generation_batch",
    description=(
        "查询一个生成批次：成员逐个的任务状态、计数、建议轮询间隔 poll_after_seconds、是否已终结 done，"
        "终结后另附终态 generation_result（逐 ID 的成功 / 失败 / 阻断结局）。未终结时按 poll_after_seconds "
        "间隔再次调用，直到 done=true。批次不属于本项目或不存在时返回 generation_batch_not_found。只读，无副作用。"
    ),
    request_model=GenerationBatchToolRequest,
    migration=Exempt("只读：批次状态存于任务队列而非项目产物；迁移失败前已提交的批次仍须可查询。"),
    domain_key="generation_batch",
    handler=get_generation_batch,
)

CANCEL_GENERATION_BATCH = ToolDeclaration(
    name="cancel_generation_batch",
    description=(
        "取消生成批次中仍在排队的成员，返回已取消（cancelled）、因已开始执行而跳过（skipped_running）与已终结"
        "而跳过（skipped_terminal）的任务 id。已开始执行的成员不可取消，照常跑完；重复调用安全。"
        "批次不属于本项目或不存在时返回 generation_batch_not_found。"
    ),
    request_model=GenerationBatchToolRequest,
    migration=Exempt("只撤销排队中的任务，不写项目产物；迁移失败时须能止损取消此前提交的批次。"),
    domain_key="generation_batch_cancellation",
    handler=cancel_generation_batch,
)

GENERATION_BATCH_TOOLS = (GET_GENERATION_BATCH, CANCEL_GENERATION_BATCH)

__all__ = [
    "CANCEL_GENERATION_BATCH",
    "GENERATION_BATCH_TOOLS",
    "GET_GENERATION_BATCH",
]
