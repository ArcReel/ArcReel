"""项目写路由要么把写入标成 ``webui``，要么在这里登记不标的理由。

来源上下文缺省是 ``filesystem``：漏标的 Web 写入会被前端当成外部改动，弹通知、排队聚焦，新建草稿还会自动
跳转。路由从应用路由表枚举，新增的写路由自动纳入检查。端点函数或它在同模块内调用的函数里出现
``project_change_source(`` 即算已标记；来源标在跨模块服务里的，登记在 :data:`_EXEMPT`，写明出处。

标记只是机械下限：Web 写入的变更要靠 hint 或显式批次才能带着来源广播，行为由
``tests/integration/server/routers/test_web_write_change_source.py`` 经真实项目事件服务验证。
"""

from __future__ import annotations

import ast
import inspect
import sys
from collections.abc import Callable
from functools import cache
from pathlib import Path

from fastapi.routing import iter_route_contexts

import server.routers
from server.app import app

_MARK = "project_change_source("

_TASK = "只提交、停止或取消任务；写入发生在任务执行侧，按任务来源广播"
_NO_WRITE = "预览或只读计算，不写项目文件"
_NOT_PROJECT_CHANGE = "Agent 会话、Agent 记忆或项目整体操作，不产生创作内容的变更事件"
_SERVICE = "来源在服务层标记：{}"
# 本检查落地时（#3510）已存在、写项目文件却未标来源的路由，按 follow-up 逐个核查。新路由不得加入。
_UNREVIEWED = "存量待核查：写项目文件但未标来源"

_EXEMPT: dict[str, str] = {
    # 任务提交与控制
    "ad_script:generate_ad_script": _TASK,
    "asset_sheets:submit_asset_sheet_batch_route": _TASK,
    "edit_timelines:export_jianying_draft": _TASK,
    "edit_timelines:render_final_cut": _TASK,
    "episode_drafts:repair_episode_draft": _TASK,
    "episode_planning:continue_replan": _TASK,
    "episode_planning:plan_episodes_to_end": _TASK,
    "episode_planning:start_replan": _TASK,
    "episode_planning:stop_planning": _TASK,
    "generate:edit_image": _TASK,
    "generate:generate_character": _TASK,
    "generate:generate_character_derivative": _TASK,
    "generate:generate_character_voice_sample": _TASK,
    "generate:generate_product": _TASK,
    "generate:generate_prop": _TASK,
    "generate:generate_scene": _TASK,
    "generate:generate_storyboard": _TASK,
    "generate:generate_tts": _TASK,
    "generate:generate_tts_batch": _TASK,
    "generate:generate_video": _TASK,
    "grids:generate_grid": _TASK,
    "grids:regenerate_grid": _TASK,
    "prompt_authoring:author_prompts": _TASK,
    "reference_videos:generate_unit": _TASK,
    "reference_videos:generate_units_batch": _TASK,
    "script_plan:plan_script": _TASK,
    "storyboard_batches:submit_storyboard_batch": _TASK,
    "storyboard_batches:submit_storyboard_video_batch_route": _TASK,
    "tasks:cancel_all_queued": _TASK,
    # 预览与只读
    "_asset_prompt_preview:preview_asset": _NO_WRITE,
    "_asset_prompt_preview:preview_derivative": _NO_WRITE,
    "asset_sheets:preview_asset_sheet_batch": _NO_WRITE,
    "projects:create_export_token": _NO_WRITE,
    "projects:get_workflow_plan": _NO_WRITE,
    "reference_videos:preview_script": _NO_WRITE,
    "reference_videos:preview_unit_prompt": _NO_WRITE,
    "reference_videos:preview_units_batch": _NO_WRITE,
    "storyboard_batches:preview_storyboard_batch": _NO_WRITE,
    "storyboard_batches:preview_storyboard_video_batch": _NO_WRITE,
    # Agent 会话、记忆与项目整体操作
    "agent_memory:clear_project_memory": _NOT_PROJECT_CHANGE,
    "agent_memory:delete_project_memory_file": _NOT_PROJECT_CHANGE,
    "agent_memory:write_project_memory_file": _NOT_PROJECT_CHANGE,
    "assistant:answer_question": _NOT_PROJECT_CHANGE,
    "assistant:delete_queued_message": _NOT_PROJECT_CHANGE,
    "assistant:delete_session": _NOT_PROJECT_CHANGE,
    "assistant:edit_queued_message": _NOT_PROJECT_CHANGE,
    "assistant:interrupt_session": _NOT_PROJECT_CHANGE,
    "assistant:resend_queued_message": _NOT_PROJECT_CHANGE,
    "assistant:rewrite_message": _NOT_PROJECT_CHANGE,
    "assistant:send_message": _NOT_PROJECT_CHANGE,
    "assistant:send_queued_message_now": _NOT_PROJECT_CHANGE,
    "projects:delete_project": _NOT_PROJECT_CHANGE,
    "projects:reset_agent_profile": _NOT_PROJECT_CHANGE,
    # 来源标在服务层
    "end_frames:delete_end_frame": _SERVICE.format("server.services.project.end_frame"),
    "end_frames:select_end_frame": _SERVICE.format("server.services.project.end_frame"),
    "end_frames:upload_end_frame": _SERVICE.format("server.services.project.end_frame"),
    "prompt_authoring:save_prompt_authoring_instructions": _SERVICE.format(
        "server.services.project.episode_instructions"
    ),
    "script_plan:save_script_plan_instructions": _SERVICE.format("server.services.project.episode_instructions"),
    # 存量待核查
    "edit_timelines:copy_edit_timeline": _UNREVIEWED,
    "edit_timelines:create_edit_timeline": _UNREVIEWED,
    "edit_timelines:delete_edit_timeline": _UNREVIEWED,
    "edit_timelines:rename_edit_timeline": _UNREVIEWED,
    "edit_timelines:restore_edit_timeline_revision": _UNREVIEWED,
    "project_migration:acknowledge_report": _UNREVIEWED,
    "project_migration:retry_migration": _UNREVIEWED,
    "reference_videos:add_unit": _UNREVIEWED,
    "reference_videos:delete_unit": _UNREVIEWED,
    "reference_videos:move_unit": _UNREVIEWED,
    "reference_videos:patch_unit": _UNREVIEWED,
}


@cache
def _module_functions(module_name: str) -> dict[str, ast.AST]:
    tree = ast.parse(inspect.getsource(sys.modules[module_name]))
    functions: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions.setdefault(node.name, node)
    return functions


def _called_names(node: ast.AST) -> set[str]:
    names = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            if isinstance(call.func, ast.Name):
                names.add(call.func.id)
            elif isinstance(call.func, ast.Attribute):
                names.add(call.func.attr)
    return names


def _marks_webui(endpoint: Callable) -> bool:
    """端点函数或它在同模块内（传递地）调用的函数里出现来源标记。"""
    functions = _module_functions(endpoint.__module__)
    pending, seen = [endpoint.__name__], set()
    while pending:
        name = pending.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        node = functions[name]
        if _MARK in ast.unparse(node):
            return True
        pending.extend(_called_names(node))
    return False


def _route_key(endpoint: Callable) -> str:
    return f"{endpoint.__module__.removeprefix('server.routers.')}:{endpoint.__name__}"


def _project_write_endpoints() -> dict[str, Callable]:
    endpoints = {}
    for route in iter_route_contexts(app.routes):
        methods = (getattr(route, "methods", None) or set()) - {"GET", "HEAD", "OPTIONS"}
        endpoint = getattr(route, "endpoint", None)
        if methods and endpoint is not None and "/projects/{" in route.path:
            endpoints[_route_key(endpoint)] = endpoint
    return endpoints


def test_every_project_write_route_marks_webui_or_says_why_not() -> None:
    endpoints = _project_write_endpoints()
    assert endpoints
    unmarked = sorted(key for key, endpoint in endpoints.items() if not _marks_webui(endpoint))
    missing = [key for key in unmarked if key not in _EXEMPT]
    assert not missing, (
        "这些项目写路由没有把写入标成 webui：用 project_change_source('webui') 包住写入，"
        f"确实不需要时在 _EXEMPT 写明理由：{missing}"
    )


def test_exemptions_only_list_unmarked_write_routes() -> None:
    """路由改名、删除或补上标记后，登记随之移除，清单不留过期条目。"""
    endpoints = _project_write_endpoints()
    stale = sorted(key for key in _EXEMPT if key not in endpoints or _marks_webui(endpoints[key]))
    assert not stale, f"_EXEMPT 里的这些条目已不是未标来源的写路由，请删除：{stale}"


def test_routers_do_not_broadcast_under_another_source() -> None:
    """路由里发 hint 或批次时不写死别的来源：写死 ``worker`` 会让用户自己的保存触发外部改动的通知与跳转。"""
    emitters = {"emit_project_change_batch", "emit_project_change_hint"}
    offenders = []
    for path in sorted(Path(server.routers.__file__).parent.glob("*.py")):
        for call in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                not isinstance(call, ast.Call)
                or getattr(call.func, "id", getattr(call.func, "attr", None)) not in emitters
            ):
                continue
            source = next((kw.value for kw in call.keywords if kw.arg == "source"), None)
            if source is not None and not (isinstance(source, ast.Constant) and source.value == "webui"):
                offenders.append(f"{path.name}:{call.lineno}")
    assert not offenders, f"路由按上下文来源广播，不要传别的 source：{offenders}"
