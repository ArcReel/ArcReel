"""step1→step2 web 审核 gate 路由。

暴露结构化中间态的审阅 / 编辑 / 确认：step1 产出后中间态在 web 可见可改，用户显式确认后才放行
step2 视觉生成（step2 由 agent 的 generate_episode_script 执行，读时经 gate 校验阻塞到确认）。
drama（utterances + source_text）与 narration（结构化 novel_text）共用本机制。
"""

import logging

from fastapi import APIRouter, Body

from lib.api_errors import NotFoundError
from lib.i18n import Translator
from lib.project_manager import get_project_manager
from server.routers._script_review_errors import raise_review_error
from server.services.script_review import ScriptReviewError, ScriptReviewService

logger = logging.getLogger(__name__)

router = APIRouter()


async def _attach_duration_tiers(service: ScriptReviewService, project_name: str, episode: int, state: dict) -> dict:
    """把收窄后的逐 unit 时长档位挂到 state 上；三个改动 step1 内容的端点（GET/PUT/POST）
    都要走这一步——否则保存 / 确认后 ``adopt()`` 用不带 ``duration_tiers`` 的响应覆盖 GET
    读到的收窄结果，面板退回未收窄的 ``supported_durations``，与 GET 首次加载时的呈现不一致。

    是否调用交给 ``get_reference_duration_tiers`` 自己按 step1_kind 判断，不靠
    ``state["supported_durations"] is not None`` 短路——那是另一个方法的返回值，型号解析
    不到时同样为 None，靠它短路会让这类项目连未收窄的档位都拿不到。
    """
    state["duration_tiers"] = await service.get_reference_duration_tiers(project_name, episode)
    return state


def _localize_quarantine_violations(quarantine: dict | None, _t: Translator) -> dict | None:
    """把 ``quarantine_unreadable`` 违约的固定中文文案换成按 ``_t`` 渲染的本地化文本。

    该 code 只由两处产出（隔离草稿信封本身损坏 / 重算所需的 meta 缺失损坏），两处都是不带
    插值的固定字符串，不涉及 ``lib.reference_video.draft_validation`` 里其余违约类型那种
    产出时已渲染好插值的模板——本地化改造范围限定在这两条，不牵动其余违约消息的展示形态。
    """
    if quarantine is None:
        return None
    for violation in quarantine["violations"]:
        if violation["code"] == "quarantine_unreadable":
            violation["message"] = _t("script_review_quarantine_unreadable")
    return quarantine


@router.get("/projects/{project_name}/episodes/{episode}/script-review")
async def get_script_review(project_name: str, episode: int, _t: Translator):
    """读取该集 step1 结构化中间态 + 审核状态（供 web 渲染与编辑）。

    ``quarantine`` 字段单独合并（reference_video 变体、隔离草稿在场时才非 None）：它按产出时
    那套校验器做读时重算，与 ``get_state`` 的落盘读写彼此独立。
    先取 ``quarantine`` 再取 ``state``：agent 的晋升工具在两次读之间把隔离草稿清掉、正式
    step1 写成新内容时，这个顺序让响应落在「content 已是新的、quarantine 却还带着晋升前的
    违约报告」这一侧——面板会误判成仍在隔离态、阻塞确认，下一轮轮询自然纠正；反过来的顺序会
    让响应落在「content 仍是旧的、quarantine 已经是 None」这一侧，面板会误判成干净态放行确认，
    用户点下确认时实际晋升的是他从未看过的那份新内容。
    """
    try:
        service = ScriptReviewService(get_project_manager())
        quarantine = await service.get_quarantine_info(project_name, episode)
        state = await service.get_state(project_name, episode)
        await _attach_duration_tiers(service, project_name, episode, state)
        state["quarantine"] = _localize_quarantine_violations(quarantine, _t)
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
    content: dict = Body(...),
    base_fingerprint: str | None = None,
):
    """保存手动 / agent 编辑后的结构化中间态，并使该集重新进入待审。

    ``base_fingerprint``（query）是编辑方 GET 时拿到的内容指纹：给定时服务端在锁内比对，
    编辑期间 step1 被另一写入方改过则 409 冲突、不落盘；缺省不比对（无基线的直连调用）。

    ``quarantine`` 同 GET 一并合并：保存作用于正式草稿，与隔离草稿是两份独立文件，保存在途时
    agent 可能已经另外产出一份新的隔离草稿——响应缺这个字段的话 ``adopt()`` 会把它当作
    「无隔离草稿」，面板显示干净态、放行确认，而 confirm() 仍会按隔离文件存在性 409。

    保存完成后立即取 ``quarantine``，早于 ``_attach_duration_tiers`` 那次 await（视频能力
    解析）：晋升工具若恰好在这条 await 期间把隔离草稿清掉，越晚读 quarantine 越可能读到
    「已清除」而不是晋升前那份，响应就会落在「本次保存的内容 + quarantine: null」这一侧，
    使用户没看过的、晋升后的内容被当作可放行确认——同 GET 端点的顺序取舍，先取的一侧读到
    的是相对更旧但更保守的快照，读时序错位只会让确认被多余地拦一轮，不会误放行。
    """
    try:
        service = ScriptReviewService(get_project_manager())
        state = await service.save_content(project_name, episode, content, base_fingerprint)
        quarantine = await service.get_quarantine_info(project_name, episode)
        await _attach_duration_tiers(service, project_name, episode, state)
        state["quarantine"] = _localize_quarantine_violations(quarantine, _t)
        return state
    except ScriptReviewError as exc:
        raise_review_error(exc, episode, _t)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.post("/projects/{project_name}/episodes/{episode}/script-review/confirm")
async def confirm_script_review(project_name: str, episode: int, _t: Translator):
    """用户显式确认 step1 内容，放行 step2 视觉生成。

    ``quarantine`` 同 GET / PUT 一并合并，保持三个端点响应形状一致——``confirm()`` 内部虽已
    按隔离文件存在性拒绝确认，但响应仍应如实反映确认完成那一刻的隔离态，而不是让这个字段在
    三个端点里时有时无。
    """
    try:
        service = ScriptReviewService(get_project_manager())
        state = await service.confirm(project_name, episode)
        await _attach_duration_tiers(service, project_name, episode, state)
        state["quarantine"] = _localize_quarantine_violations(
            await service.get_quarantine_info(project_name, episode), _t
        )
        return state
    except ScriptReviewError as exc:
        raise_review_error(exc, episode, _t)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
