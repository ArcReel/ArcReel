"""可灵 Kling 共享工具：JWT HS256 鉴权管理器 + base URL + 异步任务响应解析。

供 image_backends / video_backends / 连接测试复用。可灵官方走 JWT HS256 鉴权（access_key 作
``iss``、secret_key 签名、约 30 分钟过期），图像/视频均为异步任务（提交→轮询 task_id→取 url）。

- ``KLING_BASE_URL`` — 官方 base（含 ``/v1``）。
- ``KlingJWTManager`` — HS256 token 管理器：缓存 token，**每次取用前检查过期、距过期 <60s 按需重签**
  （异步渲染可能超单 token 寿命，不能实例级签一次）；时钟可注入（测试不依赖真实墙钟）。
- ``kling_bearer_headers`` — bearer 模式旁路 JWT、用静态 api_key 的鉴权头。
- 异步任务响应解析：``code`` 信封错误、``data.task_id``、``data.task_status``
  （submitted/processing/succeed/failed）、``data.task_result.{videos,images}[].url``。
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

import jwt

# 官方 base（含 /v1），对齐 ark_shared.ARK_BASE_URL 约定。国内调用域名官方已由 api.klingai.com
# 迁移至 api-beijing.klingai.com（旧域名仍可用，未强制下线）；此处只影响未显式配置 base_url 的
# 新用户，registry.default_base_url 同步该值（两处保持一致，单一真相源）。
KLING_BASE_URL = "https://api-beijing.klingai.com/v1"

# JWT token 寿命与刷新策略。
_TOKEN_TTL_SECONDS = 1800  # 约 30 分钟过期（官方）。
_TOKEN_NBF_SKEW_SECONDS = 5  # nbf 略提前，容忍 client/server 轻微时钟偏移。
_TOKEN_REFRESH_MARGIN_SECONDS = 60  # 距过期 <60s 即重签，避免长任务途中 token 失效。

# 异步视频任务终态：succeed / failed；中间态 submitted / processing 继续轮询。
KLING_STATUS_SUCCEED = "succeed"
KLING_STATUS_FAILED = "failed"
_TERMINAL_STATES = frozenset({KLING_STATUS_SUCCEED, KLING_STATUS_FAILED})


class KlingJWTManager:
    """可灵 JWT HS256 token 管理器（按需重签，时钟可注入）。

    payload = ``{"iss": access_key, "exp": now+1800, "nbf": now-5}``，secret_key 签名。
    缓存 token，``token()`` 每次取用前检查：距过期 >60s 复用缓存，否则重签。HS256 编码近乎
    零成本，缓存只省重复编码——重点是长任务（>30 分钟异步渲染）跨越单 token 寿命时自动续签。
    """

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._access_key = access_key
        self._secret_key = secret_key
        self._clock = clock
        self._cached_token: str | None = None
        self._expires_at: float = 0.0

    def token(self) -> str:
        """返回有效 token：距过期 >60s 复用缓存，否则按需重签。"""
        now = self._clock()
        if self._cached_token is not None and self._expires_at - now > _TOKEN_REFRESH_MARGIN_SECONDS:
            return self._cached_token
        return self._mint(now)

    def _mint(self, now: float) -> str:
        exp = int(now) + _TOKEN_TTL_SECONDS
        payload = {
            "iss": self._access_key,
            "exp": exp,
            "nbf": int(now) - _TOKEN_NBF_SKEW_SECONDS,
        }
        token = jwt.encode(
            payload,
            self._secret_key,
            algorithm="HS256",
            headers={"alg": "HS256", "typ": "JWT"},
        )
        self._cached_token = token
        self._expires_at = exp
        return token

    def auth_headers(self) -> dict[str, str]:
        """鉴权头（每次取用触发过期检查 + 按需重签）。"""
        return {
            "Authorization": f"Bearer {self.token()}",
            "Content-Type": "application/json",
        }


def kling_bearer_headers(api_key: str) -> dict[str, str]:
    """bearer 模式（自定义 endpoint）：旁路 JWT 管理器，用静态 api_key 的鉴权头。"""
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def resolve_kling_jwt_credentials(access_key: str | None, secret_key: str | None) -> tuple[str, str]:
    """校验并归一化可灵 JWT 双密钥；任一缺失即 raise（不走 env fallback）。"""
    ak = (access_key or "").strip()
    sk = (secret_key or "").strip()
    if not ak or not sk:
        raise ValueError("请到系统配置页填写可灵 Kling 的 Access Key 与 Secret Key")
    return ak, sk


def resolve_kling_api_key(api_key: str | None) -> str:
    """校验并归一化 bearer 模式静态 api_key；缺失即 raise。

    bearer 模式现有两条调用路径共用本校验：自定义 endpoint（中转站 Bearer key）与内置
    provider 的 API Key 单键模式，故报错文案不专指其中一条。
    """
    key = (api_key or "").strip()
    if not key:
        raise ValueError("请填写可灵 Kling 的 API Key")
    return key


def kling_auth_mode(credentials: Mapping[str, str | None]) -> Literal["bearer", "jwt"]:
    """按凭证形态决定 auth_mode：``api_key`` 非空优先 bearer，否则 jwt（access_key + secret_key）。

    内置 provider 构造（backend_assembly._build_kling）与连接测试（providers._test_kling）共用
    本判定，避免"api_key 优先"这条业务规则在两处各写一份、调整时改一处漏另一处。

    ``.strip()`` 后判空：纯空格 api_key（如误粘贴产生的空白字符串）不应误判为已填写 bearer
    模式、静默吞掉同时存在的有效 access_key/secret_key（resolve_kling_api_key 对空白输入的
    校验在其后才触发，本判定须先与之口径一致）。
    """
    api_key = credentials.get("api_key")
    return "bearer" if api_key and api_key.strip() else "jwt"


def image_to_base64(image_path: Path) -> str:
    """本地图片 → 纯 base64 字符串（可灵 image / image_tail 接受 URL 或 base64，无 data URI 前缀）。"""
    return base64.b64encode(image_path.read_bytes()).decode("ascii")


# ── 异步任务响应解析（提交 / 轮询 / 取结果） ──────────────────────────────────────


def _as_dict(value: object) -> dict:
    """把任意值归一化为 dict：非 dict（None / list / str 等异常上游结构）一律回空 dict。"""
    return value if isinstance(value, dict) else {}


def _as_str(value: object) -> str:
    """把任意值归一化为 str：非 str（含显式 None）一律回空串，避免 null 被格式化成字面量 'None'。"""
    return value if isinstance(value, str) else ""


def _code_error(scope: dict, message_key: str) -> str | None:
    """从 ``scope`` 取 ``code`` / ``scope[message_key]`` 构造错误描述；``code`` 为 0 或缺失 → None。

    ``code`` 归一化为 int 再比较：bearer / 中转 endpoint 可能把 code 序列化成字符串（``"0"``）
    或浮点，直接 ``code != 0`` 会把字符串 ``"0"`` 误判为错误；无法解析的 code 一律视为错误暴露原值。
    """
    code = scope.get("code")
    if code is None:
        return None
    try:
        is_error = int(float(code)) != 0
    except (TypeError, ValueError, OverflowError):
        is_error = True
    if is_error:
        return f"Kling API code={code}: {_as_str(scope.get(message_key))}".strip()
    return None


def kling_response_error(payload: object) -> str | None:
    """``code != 0`` → 错误描述；0 或缺失 → None。

    可灵所有响应带顶层 ``code`` / ``message``，0 表成功。提交/查询接口本身失败（鉴权、参数
    非法等）即在此暴露（鉴权失败等也可能另走 4xx，由 submit_post / raise_for_status 兜住）。

    部分接口（如 ``account/costs``）额外在 ``data`` 内嵌一层业务级 ``code`` / ``msg``——与顶层
    信封状态分离，专指该次业务查询本身的成功/失败（如参数非法、资源包不存在）；顶层通过后
    接着查这层，任一层非 0 即视为错误。提交/轮询类接口的 ``data``（``task_id`` /
    ``task_status`` 等）不含 ``code`` 键，该检查对其为 no-op。

    ``payload`` 归一化为 dict：中转 endpoint / 异常网关可能返回非对象 JSON（数组、字符串等），
    此时按空 dict 处理（无 ``code`` 键 → 视为无错误），避免 ``.get()`` 触发 AttributeError。
    """
    payload = payload if isinstance(payload, dict) else {}
    return _code_error(payload, "message") or _code_error(_as_dict(payload.get("data")), "msg")


def extract_kling_task_id(submit_payload: dict) -> str:
    """从提交响应提取 ``data.task_id``；缺失则按 code 错误或原样抛出。"""
    task_id = _as_dict(submit_payload.get("data")).get("task_id")
    if not task_id:
        reason = kling_response_error(submit_payload)
        raise RuntimeError(reason or f"Kling 提交响应缺少 task_id: {submit_payload}")
    return str(task_id)


def kling_task_status(payload: dict) -> str | None:
    """查询响应的 ``data.task_status``（submitted/processing/succeed/failed）。"""
    return _as_dict(payload.get("data")).get("task_status")


def is_kling_task_terminal(payload: dict) -> bool:
    """succeed / failed 为终态，停止轮询；其余（含未知/空）继续轮询。"""
    return kling_task_status(payload) in _TERMINAL_STATES


def kling_task_failure_reason(payload: dict) -> str | None:
    """``task_status=failed`` 或顶层 ``code`` 硬错误 → 错误描述；否则 None。

    succeed / 中间态返回 None（中间态由 is_terminal 判定为未完成继续轮询）。
    """
    # 顶层 code 硬错误（如 task_id 不存在 / 鉴权失败）优先暴露。
    err = kling_response_error(payload)
    if err is not None:
        return err
    if kling_task_status(payload) == KLING_STATUS_FAILED:
        data = _as_dict(payload.get("data"))
        return (f"Kling 任务失败 task_id={data.get('task_id')}: {_as_str(data.get('task_status_msg'))}").strip()
    return None


def _extract_task_result_urls(payload: dict, collection_key: str) -> list[str]:
    """从 succeed 查询响应提取 ``data.task_result.{collection_key}[].url`` 列表（非法结构回空）。"""
    result = _as_dict(_as_dict(payload.get("data")).get("task_result"))
    items = result.get(collection_key)
    urls: list[str] = []
    if isinstance(items, list):
        for item in items:
            url = _as_dict(item).get("url")
            if isinstance(url, str) and url:
                urls.append(url)
    return urls


def extract_kling_video_url(payload: dict) -> str:
    """从 succeed 的查询响应提取 ``data.task_result.videos[0].url``。"""
    for url in _extract_task_result_urls(payload, "videos"):
        return url
    reason = kling_response_error(payload)
    raise RuntimeError(reason or f"Kling 视频任务完成但缺少视频 URL: {payload}")


def extract_kling_image_urls(payload: dict) -> list[str]:
    """从 succeed 的查询响应提取 ``data.task_result.images[].url`` 列表（按张顺序）。

    可灵图像异步任务可一次产出多张（``n`` / 组图）；返回全部有效 URL，由后端按需取首张转存。
    缺少任何有效 URL 即按 code 错误或原样抛出（与视频取 url 对称的 fail-loud）。
    """
    urls = _extract_task_result_urls(payload, "images")
    if urls:
        return urls
    reason = kling_response_error(payload)
    raise RuntimeError(reason or f"Kling 图像任务完成但缺少图片 URL: {payload}")
