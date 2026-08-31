"""Structured task-failure encoding for the generation worker.

The worker (lib layer) stores a machine-stable reason in ``Task.error_message``
instead of locale-locked text: a ``[code]`` token optionally followed by a JSON
object of parameters. The tasks API serialization path renders that reason via
the request Translator on read, so the same failed task shows zh/en/vi text per
``Accept-Language``.

Anything that is not a recognised ``[code]`` form — raw provider exception text
(``str(exc)``), or legacy rows written before this format — passes through
verbatim, so no stored reason is ever lost or mis-parsed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, TypeGuard

# Backend capability rejections (``ImageCapabilityError`` / ``VideoCapabilityError`` /
# ``ReferencePayloadFloorError``). Their ``.code`` is already an ``errors`` catalog key,
# so the mapping below is identity — no prefix indirection. Enumerated rather than
# derived so an unregistered code fails fast; ``tests/integration/lib/test_task_failure_capability.py``
# AST-scans the raise sites and fails CI when a new code is added without registering it.
CAPABILITY_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "image_capability_missing_i2i",
        "image_capability_missing_t2i",
        "image_dashscope_4k_t2i_only",
        "image_endpoint_mismatch_no_i2i",
        "image_endpoint_mismatch_no_t2i",
        "image_reference_images_unreadable",
        "ref_payload_floor_exceeded",
        "video_capability_missing_t2v",
        "video_duration_invalid",
        "video_duration_not_supported",
        "video_end_image_requires_start_image",
        "video_end_image_unreadable",
        "video_last_frame_requires_pro",
        "video_last_frame_unsupported",
        "video_prompt_too_long",
        "video_reference_images_duration_unsupported",
        "video_reference_images_exceeded",
        "video_reference_images_required",
        "video_reference_images_unreadable",
        "video_reference_images_unsupported",
        "video_reference_images_with_frames_unsupported",
        "video_reference_audio_duration_exceeded",
        "video_reference_audio_exceeded",
        "video_reference_audio_format_unsupported",
        "video_reference_audio_slots_insufficient",
        "video_reference_audio_unreadable",
        "video_reference_audio_unsupported",
        "video_resolution_duration_unsupported",
        "video_start_image_unreadable",
    }
)

# Blocking codes emitted by ``ReferenceUnitRequestProjector``. The executor raises the
# canonical ``ProjectionProblem`` through ``ReferenceProjectionBlockedError``; this
# registry only authorizes those same codes for the persisted task-failure envelope.
REFERENCE_PROJECTION_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "reference_asset_missing",
        "reference_capability_changed",
        "reference_capability_unavailable",
        "reference_duration_confirmation_required",
        "reference_supported_durations_incompatible",
        "reference_supported_durations_invalid",
        "reference_supported_durations_missing",
        "video_audio_switch_not_supported",
        "video_capability_missing_i2v",
        "video_capability_missing_r2v",
        "video_capability_reference_unavailable",
    }
)

NARRATION_DELIVERY_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "needs_replan",
        "reference_duration_confirmation_required",
        "tts_duration_unavailable",
        "tts_generating",
        "tts_conflicts_with_active_narrated_video",
        "tts_missing",
        "tts_not_applicable",
        "tts_not_configured",
        "tts_stale",
        "tts_state_unavailable",
        "video_duration_unavailable",
        "video_shorter_than_tts",
        "video_supported_durations_missing",
    }
)

# Stable failure code -> i18n errors key. The code is agent-facing and persisted
# in the DB; the key resolves to zh/en/vi templates rendered at read time.
FAILURE_CODE_KEYS: dict[str, str] = {
    **{code: code for code in CAPABILITY_FAILURE_CODES},
    **{code: code for code in REFERENCE_PROJECTION_FAILURE_CODES},
    **{code: code for code in NARRATION_DELIVERY_FAILURE_CODES},
    "provider_unsupported_media": "task_fail_provider_unsupported_media",
    "dispatch_provider_requeue_failed": "task_fail_dispatch_provider_requeue_failed",
    "restart_lost_image": "task_fail_restart_lost_image",
    "restart_lost_audio": "task_fail_restart_lost_audio",
    "restart_lost_text": "task_fail_restart_lost_text",
    "restart_lost_no_job_id": "task_fail_restart_lost_no_job_id",
    "restart_lost_resume_no_job_id": "task_fail_restart_lost_resume_no_job_id",
    "resume_unsupported_provider": "task_fail_resume_unsupported_provider",
    "resume_unsupported_capacity_zero": "task_fail_resume_unsupported_capacity_zero",
    "resume_unsupported_detail": "task_fail_resume_unsupported_detail",
    "resume_expired_detail": "task_fail_resume_expired_detail",
    "resume_endpoint_changed_detail": "task_fail_resume_endpoint_changed_detail",
    "declarative_template_render_failed": "task_fail_declarative_template_render_failed",
    "declarative_response_extract_failed": "task_fail_declarative_response_extract_failed",
    "artifact_download_failed": "task_fail_artifact_download_failed",
    "restart_lost_checkpoint_no_job_id": "task_fail_restart_lost_checkpoint_no_job_id",
    "execution_identity_unrecoverable": "task_fail_execution_identity_unrecoverable",
    # ScriptEditError.key 本身就是 errors.py 的 key（见 lib/script_editor.py），无需前缀间接层。
    "script_edit_error": "script_edit_error",
    "script_edit_items_not_list": "script_edit_items_not_list",
    "script_edit_unit_lists_invalid": "script_edit_unit_lists_invalid",
    "script_edit_generated_assets_invalid": "script_edit_generated_assets_invalid",
    # 级联失败（TaskRepository._cascade_failed_queued）：reason 嵌套存储被级联依赖任务自身
    # 的失败原因（可能又是一个结构化编码串），渲染时递归展开，见 render_failure。
    "cascade_blocked_dependency": "task_fail_cascade_blocked_dependency",
}

# A structured reason is ``[code]`` optionally followed by a single space and a
# JSON object of params. Anchored at both ends so legacy ``[restart_lost] 中文``
# (non-JSON tail) and arbitrary exception text never match. DOTALL keeps the JSON
# group matching even if a param value contains an escaped newline.
_STRUCTURED_RE = re.compile(r"^\[(\w+)\](?:[ ](\{.*\}))?$", re.DOTALL)

_CASCADE_CODE = "cascade_blocked_dependency"

# collapse_cascade_reason 的解包上限，纯粹的空转防线。
_MAX_CASCADE_UNWRAP = 100


def encode_failure(code: str, /, **params: Any) -> str:
    """Encode a known failure code (+ params) into the stored machine string.

    ``[code]`` when there are no params, otherwise ``[code] {sorted-json}``.
    Raises ``KeyError`` for codes not declared in :data:`FAILURE_CODE_KEYS`, so a
    typo fails fast at the call site instead of silently storing an unrenderable
    reason.

    Param values are stringified when not JSON-native (``default=str``): capability
    codes carry whatever the caller rejected — e.g. ``video_duration_invalid`` echoes
    the raw payload value — and a failed task must still record a reason rather than
    have encoding raise inside the failure path.
    """
    if code not in FAILURE_CODE_KEYS:
        raise KeyError(f"unknown failure code: {code!r}")
    if params:
        return f"[{code}] {json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)}"
    return f"[{code}]"


def _as_shrinkable(value: Any) -> Any:
    """把容器参数值换成自身的 JSON 文本，好让裁剪逻辑能收窄它。

    截断一个容器的字符串形态比截断 JSON 字面量安全——后者会留下不闭合的括号。嵌套过深到
    ``json.dumps`` 撞递归上限的值降级为省略号：裁剪发生在落库路径上，抛出去会让任务卡在
    running。

    只动容器：``int`` / ``float`` / ``bool`` / ``None`` 本就是 JSON 原生标量，撑不爆预算也
    无从收窄，转成文本反而会在重新编码时被加上引号存回去（``42`` 变 ``"42"``、``True`` 变
    ``"true"``、``None`` 变 ``"null"``），把落库信封里的参数类型改掉。而裁剪是整个 params
    一起过一遍的，所以只要有任一参数触发裁剪，同条失败里全部标量都会跟着变形。
    """
    if not isinstance(value, dict | list):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except RecursionError:
        return "…"


def _is_validation_message(value: Any) -> TypeGuard[dict[str, Any]]:
    """Return whether ``value`` is the locale-neutral nested diagnostic envelope."""
    return (
        isinstance(value, dict)
        and set(value) == {"key", "params"}
        and isinstance(value.get("key"), str)
        and isinstance(value.get("params"), dict)
    )


def collapse_cascade_reason(reason: str) -> str:
    """把嵌套的级联原因折叠成最内层的根本原因，供级联编码前调用。

    逐层包裹会让串近指数增长：每一层都把上一层的整个信封重新编码进 JSON，转义使长度翻倍，
    七层左右就会撑破落库预算，裁剪只能从尾部切字符，把内层信封切在 JSON 中途——外层仍可解析，
    读侧却把残缺的内层当普通文本原样嵌进本地化文案里。

    中间层携带的只是「另一个同样被阻塞的任务 id」，用户能据以行动的是直接依赖与根本原因两项，
    前者由外层自己的 ``dependency_task_id`` 保留。折叠后串长与依赖链深度无关。
    """
    seen = 0
    while True:
        parsed = parse_failure(reason)
        if parsed is None or parsed[0] != _CASCADE_CODE:
            return reason
        nested = parsed[1].get("reason")
        if not isinstance(nested, str):
            return reason
        reason = nested
        seen += 1
        if seen > _MAX_CASCADE_UNWRAP:
            # 正常链路远达不到这个深度（撑破预算前就折叠掉了）。真撞上说明遇到了畸形或人造
            # 的深层串，就地收手：返回当前层，不在落库路径上空转。
            return reason


def bound_reason(reason: str, limit: int) -> str:
    """把 ``reason`` 裁剪到 ``limit`` 字符内，供级联失败编码前调用。

    直接按字符截断合法的 ``[code] {params}`` 结构化串会切断 JSON 尾部，使
    :func:`render_failure` 无法解析、原文未翻译地泄露给界面（如 ``resume_expired_detail``
    的 ``detail`` 参数源自远端错误响应，可能长达上千字符）。这里优先裁剪结构化串里最长的
    字符串参数，保持重新编码后仍是合法的 ``[code] {params}``；非结构化文本或裁剪后仍超限
    时退回按原始字符裁剪。

    超长的非字符串参数先降级成自身的 JSON 文本再参与裁剪：容器值同样可能撑爆预算——
    ``video_duration_invalid`` 回显的是调用方拒绝的原始配置值，导入或手工编辑的
    ``project.json`` 能把它写成一个大数组——而 JSON 原生类型不经 ``default=str``，
    没有字符串参数可收窄时就会退到裸切片，把信封切在 JSON 中途。

    裁剪按重新编码后的实际长度迭代收敛，不用一次算准的差值：``"`` 与 ``\\`` 之类的字符经
    JSON 转义后占两列，超额长度与原始字符数不等长，一次估算会把本可收窄的参数误判成砍不动。
    """
    if len(reason) <= limit:
        return reason
    match = _STRUCTURED_RE.match(reason)
    if match is None:
        return reason[:limit]
    code = match.group(1)
    if code not in FAILURE_CODE_KEYS:
        return reason[:limit]
    raw_params = match.group(2)
    if not raw_params:
        return reason[:limit]
    try:
        parsed = json.loads(raw_params)
    except (ValueError, RecursionError):
        # 畸形 JSON，以及嵌套过深到 ``json.loads`` 自身撞递归上限的 params。裁剪发生在落库
        # 路径上，异常逸出会让任务卡在 running，因此一并按不可解析处理，退回按字符裁剪。
        return reason[:limit]
    if not isinstance(parsed, dict):
        return reason[:limit]
    params: dict[str, Any] = {key: _as_shrinkable(value) for key, value in parsed.items()}
    detail = parsed.get("detail")
    if _is_validation_message(detail):
        # ``detail`` 必须保持机器结构，读侧才能按请求 locale 渲染。只把它的 params 中可能
        # 过大的容器降级成可裁剪文本，并把字符串叶子加入与顶层参数相同的预算竞争。
        params["detail"] = {
            "key": detail["key"],
            "params": {key: _as_shrinkable(value) for key, value in detail["params"].items()},
        }

    shrinkable: list[tuple[dict[str, Any], str]] = [
        (params, key) for key, value in params.items() if isinstance(value, str)
    ]
    nested_detail = params.get("detail")
    if _is_validation_message(nested_detail):
        nested_params = nested_detail["params"]
        shrinkable.extend((nested_params, key) for key, value in nested_params.items() if isinstance(value, str))
    if not shrinkable:
        return reason[:limit]

    encoded = encode_failure(code, **params)
    while len(encoded) > limit:
        owner, longest_key = max(shrinkable, key=lambda item: len(item[0][item[1]]))
        current: str = owner[longest_key]
        if not current:
            # 字符串参数全被削空仍超限，说明预算连信封骨架都装不下，只能退回按字符裁剪。
            return reason[:limit]
        # 超额长度以编码后的字符计，而原始字符经 JSON 转义可能占多列，两者不等长：拿它当
        # 要砍的原始字符数只是个下界估算，砍完重编码再看，直到真正装下为止。估算超过参数
        # 自身长度时改砍一半而非削空——转义参数的超额长度常大于原始长度，一次削空会把本
        # 可保留的诊断内容全丢掉。至少砍一个字符保证收敛。
        deficit = len(encoded) - limit
        drop = deficit if deficit < len(current) else max(1, len(current) // 2)
        owner[longest_key] = current[: len(current) - drop]
        encoded = encode_failure(code, **params)
    return encoded


def render_failure(error_message: str | None, translate: Callable[..., str]) -> str | None:
    """Render a stored failure reason for display via the request Translator.

    Recognised ``[code]`` / ``[code] {params}`` strings render to localized text; cascade
    reasons nest the upstream reason in their ``reason`` param and are expanded
    recursively, so it is localized too. Everything else (raw exception text, legacy rows,
    malformed payloads) passes through unchanged.

    Cascade nesting is self-limiting: each layer re-encodes the previous envelope into JSON,
    so escaping makes the string grow super-linearly and the write side caps it well before
    the depth could threaten the recursion limit.
    """
    if not error_message:
        return error_message
    parsed = parse_failure(error_message)
    if parsed is None:
        return error_message
    code, params = parsed
    if code == _CASCADE_CODE:
        nested_reason = params.get("reason")
        if isinstance(nested_reason, str):
            params = {**params, "reason": render_failure(nested_reason, translate)}
    if code in {"declarative_template_render_failed", "declarative_response_extract_failed"}:
        detail = params.get("detail")
        if _is_validation_message(detail):
            params = {**params, "detail": translate(detail["key"], **detail["params"])}
    return translate(FAILURE_CODE_KEYS[code], **params)


def parse_failure(error_message: str | None) -> tuple[str, dict[str, Any]] | None:
    """把已登记的 ``[code] {params}`` 拆成机器码与参数；原始/畸形原因返回 ``None``。"""
    if not error_message:
        return None
    match = _STRUCTURED_RE.match(error_message)
    if match is None:
        return None
    code = match.group(1)
    if code not in FAILURE_CODE_KEYS:
        return None
    raw_params = match.group(2)
    if not raw_params:
        return code, {}
    try:
        parsed = json.loads(raw_params)
    except (ValueError, RecursionError):
        # 畸形 JSON，以及嵌套过深到 ``json.loads`` 自身撞递归上限的 payload。渲染发生在
        # HTTP 请求内，异常逸出就是 500，因此一并按无法识别处理：调用方原样透传，原因不丢。
        return None
    if not isinstance(parsed, dict):
        return None
    return code, parsed
