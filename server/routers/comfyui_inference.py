"""ComfyUI 节点绑定的推断接口。

一份 workflow 里哪个节点承接提示词、哪个产出成片，ComfyUI 自己不声明，只能由外部推断。推断
全部在服务端跑、只产出**候选**：每条候选带分数与命中信号说明，前端照着渲染，用户确认后才落盘
（``docs/adr/0082``）。推断与重导入重匹配是同一个接口——重匹配的输入就是「这份 workflow 加它
已有的节点绑定」，与首次导入只差一个「既有绑定是否为空」。

请求体与导入入口同形：带 ``kind`` 的按端点定义收，其余按 ComfyUI 的原始 API workflow 收并按
``media_type`` 包成定义。放行的门槛只到结构层——重导入一份改过的 workflow 时，既有条目指向的
节点大半已不存在，那正是重匹配要处理的输入。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body
from pydantic import BaseModel

from lib.custom_provider.comfyui.import_shapes import ImportShape, route_import_payload, ui_workflow_refusal
from lib.custom_provider.comfyui.inference import infer_bindings
from lib.custom_provider.comfyui.validator import structural_diagnostics
from lib.infra.api_errors import UnprocessableError
from server.i18n import Translator

router = APIRouter(prefix="/comfyui", tags=["Custom Endpoints"])

#: 请求体即载荷原样，与导入入口同形；不声明成 ``dict`` 以便非对象输入也走结构化诊断。
InferBody = Annotated[Any, Body()]

#: 粘进来的是原始 workflow 时，由导入方指定它产图还是产视频。
ImportMediaType = Literal["image", "video"]


class SignalPayload(BaseModel):
    """一条命中的信号：稳定名、它贡献的分量、给用户看的说明。"""

    signal: str
    weight: int
    message: str


class NotePayload(BaseModel):
    """一条提示。不拦保存，但有它用户才知道某个形态会怎么表现。"""

    code: str
    message: str


class CandidatePayload(BaseModel):
    """一条候选：可直接写进 ``bindings`` 的条目，加上它凭什么被选中。"""

    target: dict[str, Any]
    score: int
    signals: list[SignalPayload]
    selected: bool
    origin: str
    # 产物候选的上游链长度，用于解释「为什么是这一个」；其余语义键为 null。
    depth: int | None = None


class KeyInferencePayload(BaseModel):
    """一个语义键的推断结果。"""

    state: str
    candidates: list[CandidatePayload]
    notes: list[NotePayload]


class InferResponse(BaseModel):
    media_type: str
    # 照这份结果直接落盘能不能过校验：任何键歧义或待确认、提示词与产物没着落都不行。
    savable: bool
    bindings: dict[str, KeyInferencePayload]
    notes: list[NotePayload]
    # 这份载荷被当成什么收的。
    import_shape: ImportShape
    # 原始 API workflow 的包装结果；载荷本就是定义时为 null，客户端继续用手上那份。
    wrapped_definition: dict[str, Any] | None = None


@router.post("/infer")
async def infer_endpoint_bindings(
    body: InferBody,
    _t: Translator,
    media_type: ImportMediaType = "video",
) -> InferResponse:
    """推断一份 workflow 的节点绑定候选；带既有节点绑定时同时做重导入重匹配。

    服务端不留状态：结果不入库，客户端拿它渲染绑定编辑器，用户确认后经端点的创建或整份替换接口
    保存。``media_type`` 只在载荷是原始 workflow 时生效——端点定义自己带着这一项。
    """
    shape, definition = route_import_payload(body, media_type=media_type)
    if shape is ImportShape.COMFYUI_UI_WORKFLOW:
        raise UnprocessableError("custom_endpoint_definition_invalid").with_diagnostic(
            ui_workflow_refusal().to_payload(_t)
        )
    diagnostics = structural_diagnostics(definition)
    if diagnostics.errors or not isinstance(definition, dict):
        raise UnprocessableError("custom_endpoint_definition_invalid").with_diagnostic(diagnostics.to_payload(_t))
    payload = infer_bindings(definition).to_payload(_t)
    return InferResponse(
        **payload,
        import_shape=shape,
        wrapped_definition=definition if shape is ImportShape.COMFYUI_API_WORKFLOW else None,
    )
