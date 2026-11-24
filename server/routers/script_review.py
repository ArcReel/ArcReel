"""script_plan→prompt_authoring web 内容确认路由。

暴露脚本规划的审阅 / 编辑 / 确认：script_plan 产出后在 web 可见可改，用户显式确认时整份转为
正式脚本（条目带待编写标记），之后由 Agent 的 generate_episode_script 按正式脚本补写视觉层。
drama（utterances + source_text）与 narration（结构化 novel_text）共用本机制。
"""

import ast
import json
import logging
from collections.abc import Mapping
from string import Formatter

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict, Field

from lib.i18n import DEFAULT_LOCALE, MESSAGES, render_message
from lib.i18n.display_names import DisplayNames
from lib.infra.api_errors import NotFoundError
from lib.project.asset_types import localize_asset_type
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from lib.script.draft_violation import SCHEMA_ERROR_PARAM, draft_violation_message_key
from lib.script.script_review import overwrite_with_text
from server.dependencies import require_project_migration_ok
from server.i18n import DisplayNamesCatalog, Translator
from server.routers._script_review_errors import raise_review_error
from server.services.project.script_review import ScriptReviewError, ScriptReviewService

logger = logging.getLogger(__name__)

router = APIRouter()


async def _attach_duration_tiers(
    service: ScriptReviewService, project_name: str, episode: int, state: dict, _t: Translator, names: DisplayNames
) -> dict:
    """把逐 unit 时长档位与本地化降级提示挂到 state 上；三个端点（GET/PUT/POST）
    都要走这一步——否则保存 / 确认后 ``adopt()`` 用不带 ``duration_tiers`` 的响应覆盖 GET
    读到的收窄结果，面板无法区分生效档位与 i2v 失败。

    是否调用交给 ``get_reference_duration_tiers`` 自己按 script_plan_kind 判断，不靠
    ``state["supported_durations"] is not None`` 短路——那是另一个方法的返回值，型号解析
    不到时同样为 None，靠它短路会让这类项目连未收窄的档位都拿不到。
    """
    content = state.get("content")
    units = content.get("units") if isinstance(content, dict) else None
    state["duration_tiers"] = await service.get_reference_duration_tiers(
        project_name, episode, units if isinstance(units, list) else ()
    )
    localize_draft_view(
        {"violations": state.get("blocking_issues", []), "soft_violations": state.get("soft_violations", [])}, _t, names
    )
    return state


def _attach_overwrite_text(state: dict, _t: Translator) -> dict:
    """给覆盖清单附上服务端渲染的丢失清单文本；Web 确认框只呈现这份文本，与 Agent 回执同源。"""
    state["script_overwrite"] = overwrite_with_text(state.get("script_overwrite"), _t)
    return state


_SCHEMA_ERROR_MESSAGE_PREFIX = "draft_schema_"


def _template_fields(template: str) -> set[str]:
    return {field for _, field, _, _ in Formatter().parse(template) if field is not None}


def _schema_choices(expected: str, _t: Translator) -> str:
    """把 pydantic 拼好的取值列表（``'a', 'b' or 'c'``）按界面语言重排；解析不了时原样返回。"""
    head, separator, last = expected.rpartition(" or ")
    try:
        values = ast.literal_eval(f"[{head}, {last}]" if separator else f"[{expected}]")
    except (ValueError, SyntaxError):
        return expected
    return _t("draft_schema_choice_separator").join(json.dumps(value, ensure_ascii=False) for value in values)


def _schema_detail(schema_error: Mapping[str, object], fallback: object, _t: Translator) -> object:
    """按类型码与约束值写出结构校验原因；没有对应文案或约束值不全时退回 pydantic 原文。"""
    kind = schema_error.get("type")
    raw_ctx = schema_error.get("ctx")
    ctx = {str(key): value for key, value in raw_ctx.items()} if isinstance(raw_ctx, Mapping) else {}
    if kind == "literal_error" and isinstance(expected := ctx.get("expected"), str):
        ctx["expected"] = _schema_choices(expected, _t)
    key = f"{_SCHEMA_ERROR_MESSAGE_PREFIX}{kind}"
    template = MESSAGES[DEFAULT_LOCALE].get(key) if isinstance(kind, str) else None
    if template is not None and _template_fields(template) <= ctx.keys():
        reason = _t(key, **ctx)
    elif isinstance(msg := schema_error.get("msg"), str) and msg:
        reason = msg
    else:
        return fallback
    if "input" not in schema_error:
        return reason
    return _t("draft_schema_with_value", reason=reason, value=json.dumps(schema_error["input"], ensure_ascii=False))


def localize_draft_view(view: dict | None, _t: Translator, names: DisplayNames) -> dict | None:
    """按 code 与结构化 params 渲染已登记的违约；旧条目与未知码保留 Agent 原文。

    违约文案登记在 ``draft_violation_<code>`` 下；视频请求事实失败与正式单元预判沿用问题码本身
    作文案 key。资产类型参数换成当前语言的显示名；带结构校验原因的条目按界面语言重写 ``detail``。
    """
    if view is None:
        return None
    for violation in view["violations"]:
        params = violation.get("params")
        if isinstance(params, dict):
            key = draft_violation_message_key(violation["code"])
            if key in MESSAGES[DEFAULT_LOCALE]:
                if isinstance(asset_type := params.get("asset_type"), str):
                    params = {**params, "asset_type": localize_asset_type(asset_type, _t)}
                if isinstance(schema_error := params.get(SCHEMA_ERROR_PARAM), Mapping):
                    params = {**params, "detail": _schema_detail(schema_error, params.get("detail"), _t)}
                violation["message"] = render_message(key, params, _t, names)
            elif violation["code"] in MESSAGES[DEFAULT_LOCALE]:
                violation["message"] = render_message(violation["code"], params, _t, names)
    for soft in view.get("soft_violations") or []:
        soft["message"] = render_message(soft["code"], soft["params"], _t, names)
    return view


@router.get("/projects/{project_name}/episodes/{episode}/script-review")
async def get_script_review(project_name: str, episode: int, _t: Translator, names: DisplayNamesCatalog):
    """读取该集 script_plan 结构化中间态 + 内容确认状态（供 web 渲染与编辑）。"""
    try:
        service = ScriptReviewService(get_project_manager())
        state = await service.get_state(project_name, episode)
        await _attach_duration_tiers(service, project_name, episode, state, _t, names)
        _attach_overwrite_text(state, _t)
        return state
    except ScriptReviewError as exc:
        raise_review_error(exc, episode, _t)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.put("/projects/{project_name}/episodes/{episode}/script-review/content")
async def update_script_review_content(
    project_name: str,
    episode: int,
    _t: Translator,
    names: DisplayNamesCatalog,
    content: dict = Body(...),
    base_fingerprint: str | None = None,
):
    """保存手动 / Agent 编辑后的结构化中间态，并使该集重新等待确认。

    ``base_fingerprint``（query）是编辑方 GET 时拿到的内容指纹：给定时服务端在锁内比对，
    编辑期间 script_plan 被另一写入方改过则 409 冲突、不落盘；缺省不比对（无基线的直连调用）。
    """
    try:
        service = ScriptReviewService(get_project_manager())
        with project_change_source("webui"):
            state = await service.save_content(project_name, episode, content, base_fingerprint)
        await _attach_duration_tiers(service, project_name, episode, state, _t, names)
        _attach_overwrite_text(state, _t)
        return state
    except ScriptReviewError as exc:
        raise_review_error(exc, episode, _t)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


class ConfirmScriptReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overwrite_revision: str | None = Field(
        default=None, description="认可覆盖的正式脚本版本，取自 script_overwrite.revision；该集尚无正式脚本时不必给"
    )


@router.post(
    "/projects/{project_name}/episodes/{episode}/script-review/confirm",
    dependencies=[Depends(require_project_migration_ok)],
)
async def confirm_script_review(
    project_name: str,
    episode: int,
    _t: Translator,
    names: DisplayNamesCatalog,
    req: ConfirmScriptReviewRequest | None = None,
):
    """用户显式确认 script_plan 内容：整份转为正式脚本（全部分镜待编写），放行 prompt_authoring 视觉生成。

    该集已有正式脚本时须带 ``overwrite_revision``（覆盖清单的 ``revision``），缺失或与当前正式脚本不符
    时 409，诊断里的 ``script_overwrite`` 列出当前将被移除的分镜与产物摘要，不写正式脚本与确认记录。
    """
    try:
        service = ScriptReviewService(get_project_manager())
        with project_change_source("webui"):
            state = await service.confirm(
                project_name, episode, overwrite_revision=req.overwrite_revision if req is not None else None
            )
        await _attach_duration_tiers(service, project_name, episode, state, _t, names)
        _attach_overwrite_text(state, _t)
        return state
    except ScriptReviewError as exc:
        raise_review_error(exc, episode, _t)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
