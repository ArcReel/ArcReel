"""MiniMax（海螺）共享工具模块。

供 text_backends / video_backends / config / 连接测试复用。MiniMax 本身 OpenAI 兼容，
单 `/v1` base（无 DashScope 那种文本/原生双 base 派生），文本与原生视频端点共用同一 base：
- MINIMAX_BASE_URL — 国内站默认 base（含 `/v1`）
- MINIMAX_INTL_BASE_URL — 国际站 base，供配置覆盖参考
- resolve_minimax_api_key — Bearer API Key 解析（缺失即 raise，不走 env fallback）
- minimax_text_base_url / minimax_video_base_url — 归一化为 {host}/v1，容忍用户填 host 或带 `/v1` 后缀
- minimax_headers — Bearer 鉴权头
- 视频两步取 URL 状态机：submit→轮询 query 至 status=Success 取 file_id→files/retrieve 取 download_url
"""

from __future__ import annotations

import base64
from pathlib import Path

# 国内站默认 base（含 /v1）；国际站经配置覆盖 base_url 指向 MINIMAX_INTL_BASE_URL。
MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_INTL_BASE_URL = "https://api.minimax.io/v1"

# 单一已知路径后缀，归一化 host 时剥除以容忍用户填入完整 base。
_V1_SUFFIX = "/v1"

# 视频异步任务终态：Success/Fail；中间态 Preparing/Queueing/Processing 继续轮询。
MINIMAX_STATUS_SUCCESS = "Success"
MINIMAX_STATUS_FAIL = "Fail"
_TERMINAL_VIDEO_STATES = frozenset({MINIMAX_STATUS_SUCCESS, MINIMAX_STATUS_FAIL})

# 轮询间隔（秒）：海螺视频生成通常数十秒至数分钟，10s 一次平衡时延与请求量。
MINIMAX_VIDEO_POLL_INTERVAL_SECONDS = 10.0

# 图片后缀 → MIME（first_frame_image 接受 data URI）。
_IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def resolve_minimax_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 MiniMax API Key")
    return api_key.strip()


def _minimax_host(configured: str | None) -> str:
    """从配置的 base_url 提取 host 段（剥除 `/v1` 后缀），缺省回落国内站 host。"""
    # 先 strip 再判空：纯空白串（"   "）是真值会绕过 or，回落必须在 strip 之后，
    # 否则 base 变空串、派生出 "/v1" 这类非法相对 URL。
    base = ((configured or "").strip() or MINIMAX_BASE_URL).rstrip("/")
    if base.endswith(_V1_SUFFIX):
        return base[: -len(_V1_SUFFIX)]
    return base


def minimax_text_base_url(configured: str | None = None) -> str:
    """文本（OpenAI 兼容）base：{host}/v1。"""
    return f"{_minimax_host(configured)}{_V1_SUFFIX}"


def minimax_video_base_url(configured: str | None = None) -> str:
    """原生视频端点 base：{host}/v1。与文本共用单一 /v1 base，仅命名区分用途。"""
    return f"{_minimax_host(configured)}{_V1_SUFFIX}"


def minimax_headers(api_key: str) -> dict[str, str]:
    """Bearer 鉴权头。"""
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def image_to_data_uri(image_path: Path) -> str:
    """本地图片 → base64 data URI（first_frame_image 接受 URL 或 data URI）。"""
    mime = _IMAGE_MIME_TYPES.get(image_path.suffix.lower(), "image/png")
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ── 视频两步取 URL 状态机工具 ──────────────────────────────────────────────────


def _as_dict(value: object) -> dict:
    """把任意值归一化为 dict：非 dict（含 None / list / str 等异常上游结构）一律回空 dict。"""
    return value if isinstance(value, dict) else {}


def _base_resp_error(payload: dict) -> str | None:
    """``base_resp.status_code != 0`` → 错误描述；0 或缺失 → None。

    MiniMax 所有响应带 ``base_resp``（``{status_code, status_msg}``），0 表成功。
    提交/查询/取回接口本身失败（鉴权、task_id 非法等）即在此暴露。
    """
    base = _as_dict(payload.get("base_resp"))
    code = base.get("status_code")
    if code is not None and code != 0:
        return f"MiniMax base_resp status_code={code}: {base.get('status_msg', '')}".strip()
    return None


def extract_minimax_video_task_id(submit_payload: dict) -> str:
    """从提交响应提取顶层 task_id；缺失则按 base_resp 错误或原样抛出。"""
    task_id = submit_payload.get("task_id")
    if not task_id:
        reason = _base_resp_error(submit_payload)
        raise RuntimeError(reason or f"MiniMax 视频提交响应缺少 task_id: {submit_payload}")
    return str(task_id)


def minimax_video_status(payload: dict) -> str | None:
    """查询响应的 status 字段（Preparing/Queueing/Processing/Success/Fail）。"""
    return payload.get("status")


def is_minimax_video_terminal(payload: dict) -> bool:
    """Success / Fail 为终态，停止轮询；其余（含未知/空）继续轮询。"""
    return minimax_video_status(payload) in _TERMINAL_VIDEO_STATES


def minimax_video_failure_reason(payload: dict) -> str | None:
    """status=Fail 或查询接口顶层 base_resp 硬错误 → 错误描述；否则 None。

    status=Success / 中间态返回 None（中间态由 is_terminal 判定为未完成继续轮询）。
    """
    if minimax_video_status(payload) == MINIMAX_STATUS_FAIL:
        base = _as_dict(payload.get("base_resp"))
        return (
            f"MiniMax 视频任务失败 task_id={payload.get('task_id')} "
            f"status_code={base.get('status_code')}: {base.get('status_msg', '')}"
        ).strip()
    # 顶层 base_resp 硬错误（如 task_id 不存在 / 鉴权失败），非 0 即失败。
    return _base_resp_error(payload)


def extract_minimax_file_id(query_payload: dict) -> str:
    """从 status=Success 的查询响应提取 file_id。"""
    file_id = query_payload.get("file_id")
    if not file_id:
        raise RuntimeError(f"MiniMax 视频任务完成但缺少 file_id: {query_payload}")
    return str(file_id)


def extract_minimax_download_url(retrieve_payload: dict) -> str:
    """从 files/retrieve 响应提取 file.download_url。"""
    url = _as_dict(retrieve_payload.get("file")).get("download_url")
    if not url:
        reason = _base_resp_error(retrieve_payload)
        raise RuntimeError(reason or f"MiniMax files/retrieve 响应缺少 download_url: {retrieve_payload}")
    return url
