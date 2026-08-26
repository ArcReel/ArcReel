"""PROTOTYPE — 驱动 8 家内置 video backend 走一遍 submit → poll → 下载，把实际发出的请求与解析结果落成
``builtin_actual.json``，供 ``compare_builtin.mjs`` 与 templates/builtin 下的声明式定义逐字段对照（#2142）。

用法：uv run python lib/custom_provider/prototype_declarative_endpoint/builtin_probe.py

- HTTP 式 backend 由 respx 在 transport 层拦截，记录真实序列化后的请求（URL / 鉴权头 / JSON body）。
- Ark 走 volcenginesdkarkruntime SDK，记录 ``tasks.create`` 的 kwargs 与 ``tasks.get`` 的调用，URL 按 SDK
  源码（resources/content_generation/tasks.py）的路径拼出。
- 轮询等待经 tests.fakes.bounded_poll_clock 压缩；成片下载统一打 https://cdn.test/ 并由 respx 回固定字节。
- 每个 case 同时记录喂给 backend 的响应夹具与 backend 的最终结果（video_uri / duration / seed / usage /
  异常文本），JS 侧对同一组夹具跑声明式 reducer 再比对。
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import jwt
import respx

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from lib.video_backends.base import VideoGenerationRequest  # noqa: E402
from tests.fakes import bounded_poll_clock  # noqa: E402
from tests.http_capture import capture_http  # noqa: E402

HERE = Path(__file__).parent
OUT = HERE / "builtin_actual.json"

# 素材字节：PNG 魔数让 data URI 的 MIME 识别落到 image/png；wav 走扩展名表（各家均为 audio/wav）。
ASSET_BYTES: dict[str, bytes] = {
    "start.png": b"\x89PNG\r\n\x1a\nSTART",
    "end.png": b"\x89PNG\r\n\x1a\nEND",
    "ref1.png": b"\x89PNG\r\n\x1a\nREF1",
    "ref2.png": b"\x89PNG\r\n\x1a\nREF2",
    "a.wav": b"RIFF\x00\x00\x00\x00WAVEA",
}
ASSET_MIME = {
    "start.png": "image/png",
    "end.png": "image/png",
    "ref1.png": "image/png",
    "ref2.png": "image/png",
    "a.wav": "audio/wav",
}

DOWNLOAD_RE = r"^https://cdn\.test/"
CAPTURE_HEADERS = ("authorization", "x-dashscope-async")


@dataclass
class Case:
    id: str
    template: str | None
    params: dict[str, Any]
    request: dict[str, Any]
    assets: dict[str, Any]
    routes: dict[str, tuple[str, str, list[Any]]]
    make_backend: Callable[[], Any]
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _capture(request: httpx.Request) -> dict[str, Any]:
    body: Any = None
    if request.content:
        try:
            body = json.loads(request.content)
        except ValueError:
            body = request.content.decode("utf-8", "replace")
    return {
        "method": request.method,
        "url": str(request.url),
        "headers": {k: v for k, v in request.headers.items() if k.lower() in CAPTURE_HEADERS},
        "body": body,
    }


def _result_view(result: Any) -> dict[str, Any]:
    return {
        "task_id": result.task_id,
        "video_uri": result.video_uri,
        "duration_seconds": result.duration_seconds,
        "seed": result.seed,
        "usage_tokens": result.usage_tokens,
        "generate_audio": result.generate_audio,
    }


def _ns(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_ns(v) for v in value]
    return value


async def run_http_case(case: Case, tmp: Path) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": case.id,
        "template": case.template,
        "params": case.params,
        "assets": case.assets,
        "note": case.note,
    }
    fixtures: dict[str, Any] = {}
    with capture_http() as router, bounded_poll_clock():
        routes: dict[str, respx.Route] = {}
        for name, (method, pattern, responses) in case.routes.items():
            resps = [r if isinstance(r, httpx.Response) else httpx.Response(200, json=r) for r in responses]
            fixtures[name] = [json.loads(r.content) if r.content else None for r in resps]
            route = router.route(method=method, url__regex=pattern)
            if len(resps) == 1:
                route.mock(return_value=resps[0])
            else:
                route.mock(side_effect=resps)
            routes[name] = route
        routes["download"] = router.get(url__regex=DOWNLOAD_RE).mock(return_value=httpx.Response(200, content=b"mp4"))
        backend = case.make_backend()
        request = VideoGenerationRequest(output_path=tmp / f"{case.id.replace('/', '_')}.mp4", **case.request)
        try:
            result = await backend.generate(request)
            out["result"] = _result_view(result)
        except Exception as exc:  # noqa: BLE001 — 记录 backend 的失败文本本身就是对照对象
            out["error"] = f"{type(exc).__name__}: {exc}"
        out["requests"] = {name: [_capture(c.request) for c in route.calls] for name, route in routes.items()}
    out["fixtures"] = fixtures
    return out


async def run_ark_case(case: Case, tmp: Path) -> dict[str, Any]:
    from lib.ark_shared import ark_base_url

    out: dict[str, Any] = {
        "id": case.id,
        "template": case.template,
        "params": case.params,
        "assets": case.assets,
        "note": case.note,
    }
    submit_resp = case.extra["submit"]
    polls = case.extra["polls"]
    client = MagicMock()
    client.content_generation.tasks.create = MagicMock(return_value=_ns(submit_resp))
    client.content_generation.tasks.get = MagicMock(side_effect=[_ns(p) for p in polls])
    base = ark_base_url(case.params["base_url"])
    with (
        patch("lib.video_backends.ark.create_ark_client", return_value=client),
        capture_http() as router,
        bounded_poll_clock(),
    ):
        download = router.get(url__regex=DOWNLOAD_RE).mock(return_value=httpx.Response(200, content=b"mp4"))
        backend = case.make_backend()
        request = VideoGenerationRequest(output_path=tmp / f"{case.id.replace('/', '_')}.mp4", **case.request)
        try:
            result = await backend.generate(request)
            out["result"] = _result_view(result)
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
    create_kwargs = (
        client.content_generation.tasks.create.call_args.kwargs
        if client.content_generation.tasks.create.call_args
        else None
    )
    out["requests"] = {
        "submit": [
            {
                "method": "POST",
                "url": f"{base}/contents/generations/tasks",
                "headers": {"authorization": "Bearer <SDK 注入>"},
                "body": create_kwargs,
            }
        ]
        if create_kwargs is not None
        else [],
        "poll": [
            {
                "method": "GET",
                "url": f"{base}/contents/generations/tasks/{c.kwargs['task_id']}",
                "headers": {},
                "body": None,
            }
            for c in client.content_generation.tasks.get.call_args_list
        ],
        "download": [_capture(c.request) for c in download.calls],
    }
    out["fixtures"] = {"submit": [submit_resp], "poll": polls}
    return out


def _asset_record(names: list[str]) -> list[dict[str, str]]:
    return [
        {"name": n, "mime": ASSET_MIME[n], "bytes_b64": base64.b64encode(ASSET_BYTES[n]).decode("ascii")} for n in names
    ]


def build_cases(tmp: Path) -> list[tuple[Case, Callable]]:
    start, end, ref1, ref2, wav = (tmp / n for n in ("start.png", "end.png", "ref1.png", "ref2.png", "a.wav"))
    assets_all = {
        "start_image": _asset_record(["start.png"])[0],
        "end_image": _asset_record(["end.png"])[0],
        "reference_images": _asset_record(["ref1.png", "ref2.png"]),
        "reference_audio_files": _asset_record(["a.wav"]),
    }

    def assets(*keys: str, refs: int = 2) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for k in keys:
            v = assets_all[k]
            d[k] = v[:refs] if isinstance(v, list) else v
        return d

    cases: list[tuple[Case, Callable]] = []

    # ── agnes ─────────────────────────────────────────────────────────
    from lib.video_backends.agnes import AgnesVideoBackend

    agnes_base = "https://x/v1"
    agnes_params = {
        "base_url": agnes_base,
        "api_key": "K",
        "model": "agnes-video-v2.0",
        "prompt": "A cat running",
        "duration": 5,
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "generate_audio": True,
        "seed": 7,
    }
    agnes_req = {
        "prompt": "A cat running",
        "duration_seconds": 5,
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "generate_audio": True,
        "seed": 7,
    }
    agnes_submit = {"task_id": "task-42", "status": "queued"}
    agnes_done = {
        "task_id": "task-42",
        "status": "completed",
        "size": "720x1280",
        "url": "https://cdn.test/agnes/out.mp4",
        "remixed_from_video_id": None,
        "seconds": "5.0",
    }
    agnes_running = {"task_id": "task-42", "status": "in_progress", "progress": 40}

    def agnes_routes(polls: list[dict], query: list[dict] | None = None):
        r = {
            "submit": ("POST", rf"^{agnes_base}/videos$", [agnes_submit]),
            "poll": ("GET", rf"^{agnes_base}/videos/[^/]+$", polls),
        }
        if query is not None:
            r["query"] = ("GET", r"^https://x/agnesapi", query)
        return r

    def mk_agnes():
        return AgnesVideoBackend(api_key="K", base_url=agnes_base)

    for cid, tpl, req, ast, polls, query, note in [
        ("agnes/t2v", "agnes.json", {}, {}, [agnes_running, agnes_done], None, ""),
        ("agnes/i2v", "agnes.json", {"start_image": start}, assets("start_image"), [agnes_done], None, ""),
        (
            "agnes/keyframes",
            "agnes-keyframes.json",
            {"start_image": start, "end_image": end},
            assets("start_image", "end_image"),
            [agnes_done],
            None,
            "首尾帧形状单独一份定义",
        ),
        (
            "agnes/r2v",
            "agnes.json",
            {"reference_images": [ref1, ref2]},
            assets("reference_images"),
            [agnes_done],
            None,
            "",
        ),
        (
            "agnes/video_id_only",
            "agnes.json",
            {},
            {},
            [
                {
                    "task_id": "task-42",
                    "video_id": "vid-123",
                    "status": "completed",
                    "remixed_from_video_id": None,
                    "seconds": "8.0",
                }
            ],
            [{"video_id": "vid-123", "url": "https://cdn.test/agnes/queried.mp4"}],
            "完成态只带 video_id，内置二次查询 /agnesapi",
        ),
        (
            "agnes/legacy_remixed_url",
            "agnes.json",
            {},
            {},
            [
                {
                    "task_id": "task-42",
                    "video_id": "vid-x",
                    "status": "completed",
                    "remixed_from_video_id": "https://cdn.test/agnes/legacy.mp4",
                }
            ],
            None,
            "旧网关把 URL 回填在 remixed_from_video_id",
        ),
        (
            "agnes/failed",
            "agnes.json",
            {},
            {},
            [{"task_id": "task-42", "status": "failed", "error": {"message": "upstream down"}}],
            None,
            "",
        ),
        (
            "agnes/cancelled",
            "agnes.json",
            {},
            {},
            [{"task_id": "task-42", "status": "cancelled", "error": {"message": "user cancelled"}}],
            None,
            "",
        ),
        ("agnes/completed_no_url", "agnes.json", {}, {}, [{"task_id": "task-42", "status": "completed"}], None, ""),
    ]:
        cases.append(
            (
                Case(cid, tpl, agnes_params, {**agnes_req, **req}, ast, agnes_routes(polls, query), mk_agnes, note),
                run_http_case,
            )
        )

    # ── ark ───────────────────────────────────────────────────────────
    from lib.video_backends.ark import ArkVideoBackend

    ark_base = "https://ark.cn-beijing.volces.com/api/v3"
    ark_done = {
        "id": "cgt-1",
        "status": "succeeded",
        "content": {"video_url": "https://cdn.test/ark/video.mp4"},
        "seed": 58944,
        "usage": {"completion_tokens": 246840},
    }
    ark_queued = {"id": "cgt-1", "status": "queued"}

    def mk_ark(model):
        return lambda: ArkVideoBackend(api_key="K", model=model)

    for cid, tpl, model, req, ast, polls, note in [
        (
            "ark/1x-t2v",
            "ark-seedance-1x.json",
            "doubao-seedance-1-5-pro-251215",
            {
                "prompt": "a flower field",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "generate_audio": True,
                "seed": 42,
            },
            {},
            [ark_queued, ark_done],
            "service_tier=default",
        ),
        (
            "ark/1x-i2v-last",
            "ark-seedance-1x.json",
            "doubao-seedance-1-5-pro-251215",
            {
                "prompt": "morph",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "generate_audio": False,
                "start_image": start,
                "end_image": end,
            },
            assets("start_image", "end_image"),
            [ark_done],
            "",
        ),
        (
            "ark/1x-r2v",
            "ark-seedance-1x.json",
            "doubao-seedance-1-5-pro-251215",
            {
                "prompt": "[图1] 与 [图2] 对话",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "generate_audio": True,
                "reference_images": [ref1, ref2],
            },
            assets("reference_images"),
            [ark_done],
            "",
        ),
        (
            "ark/1x-flex",
            "ark-seedance-1x.json",
            "doubao-seedance-1-5-pro-251215",
            {
                "prompt": "test",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "generate_audio": True,
                "service_tier": "flex",
            },
            {},
            [ark_done],
            "service_tier=flex，格式无该变量",
        ),
        (
            "ark/2-0-r2v-audio",
            "ark-seedance-2-0.json",
            "doubao-seedance-2-0-260128",
            {
                "prompt": "两人对话",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "generate_audio": True,
                "reference_images": [ref1, ref2],
                "reference_audio_files": [wav],
            },
            assets("reference_images", "reference_audio_files"),
            [ark_done],
            "",
        ),
        (
            "ark/2-0-i2v-last",
            "ark-seedance-2-0.json",
            "doubao-seedance-2-0-260128",
            {
                "prompt": "morph",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "generate_audio": True,
                "start_image": start,
                "end_image": end,
            },
            assets("start_image", "end_image"),
            [ark_done],
            "",
        ),
        (
            "ark/2-5-i2v-adaptive",
            "ark-seedance-2-5.json",
            "doubao-seedance-2-5-260628",
            {
                "prompt": "girl",
                "duration_seconds": 5,
                "aspect_ratio": "adaptive",
                "resolution": "720p",
                "generate_audio": True,
                "start_image": start,
            },
            assets("start_image"),
            [ark_done],
            "编排层已把 aspect_ratio 改写为 adaptive",
        ),
        (
            "ark/failed",
            "ark-seedance-1x.json",
            "doubao-seedance-1-5-pro-251215",
            {"prompt": "test", "duration_seconds": 5, "aspect_ratio": "16:9", "resolution": "720p"},
            {},
            [
                {
                    "id": "cgt-1",
                    "status": "failed",
                    "error": {"code": "OutputVideoSensitiveContentDetected", "message": "content violation"},
                }
            ],
            "",
        ),
        (
            "ark/expired",
            "ark-seedance-1x.json",
            "doubao-seedance-1-5-pro-251215",
            {"prompt": "test", "duration_seconds": 5, "aspect_ratio": "16:9", "resolution": "720p"},
            {},
            [{"id": "cgt-1", "status": "expired", "error": None}],
            "",
        ),
    ]:
        params = {
            "base_url": ark_base,
            "api_key": "K",
            "model": model,
            "prompt": req["prompt"],
            "duration": req["duration_seconds"],
            "aspect_ratio": req["aspect_ratio"],
            "resolution": req.get("resolution"),
            "generate_audio": req.get("generate_audio", True),
            "seed": req.get("seed"),
        }
        cases.append(
            (
                Case(
                    cid,
                    tpl,
                    params,
                    req,
                    ast,
                    {},
                    mk_ark(model),
                    note,
                    extra={"submit": {"id": "cgt-1"}, "polls": polls},
                ),
                run_ark_case,
            )
        )

    # ── dashscope ─────────────────────────────────────────────────────
    from lib.video_backends.dashscope import DashScopeVideoBackend

    ds_host = "https://dashscope.aliyuncs.com"
    ds_base = f"{ds_host}/api/v1"
    ds_submit = {"output": {"task_id": "t-1", "task_status": "PENDING"}, "request_id": "req-1"}
    ds_running = {"output": {"task_id": "t-1", "task_status": "RUNNING"}, "request_id": "req-2"}
    ds_done = {
        "output": {"task_id": "t-1", "task_status": "SUCCEEDED", "video_url": "https://cdn.test/ds/o.mp4"},
        "usage": {"duration": 8, "input_video_duration": 0, "output_video_duration": 8},
        "request_id": "req-3",
    }

    def ds_routes(submit: list[dict], polls: list[dict]):
        return {
            "submit": ("POST", rf"^{ds_base}/services/aigc/video-generation/video-synthesis$", submit),
            "poll": ("GET", rf"^{ds_base}/tasks/[^/]+$", polls),
        }

    def mk_ds(model):
        return lambda: DashScopeVideoBackend(api_key="K", model=model, base_url=ds_host)

    for cid, tpl, model, req, ast, submit, polls, note in [
        (
            "dashscope/happyhorse-t2v",
            "dashscope-t2v.json",
            "happyhorse-1.1-t2v",
            {"prompt": "p", "duration_seconds": 5, "aspect_ratio": "16:9", "resolution": "720p", "seed": 3},
            {},
            [ds_submit],
            [ds_running, ds_done],
            "",
        ),
        (
            "dashscope/happyhorse-i2v",
            "dashscope-i2v.json",
            "happyhorse-1.1-i2v",
            {"prompt": "p", "duration_seconds": 5, "aspect_ratio": "16:9", "resolution": "480p", "start_image": start},
            assets("start_image"),
            [ds_submit],
            [ds_done],
            "首帧在场不下发 ratio",
        ),
        (
            "dashscope/happyhorse-r2v",
            "dashscope-happyhorse-r2v.json",
            "happyhorse-1.1-r2v",
            {
                "prompt": "[Image 1] dances",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "reference_images": [ref1, ref2],
            },
            assets("reference_images"),
            [ds_submit],
            [ds_done],
            "",
        ),
        (
            "dashscope/wan27-r2v-voice",
            "dashscope-wan27-r2v.json",
            "wan2.7-r2v",
            {
                "prompt": "两人对话",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "1080p",
                "start_image": start,
                "reference_images": [ref1, ref2],
                "reference_audio_files": [wav],
                "reference_audio_targets": [1],
            },
            assets("start_image", "reference_images", "reference_audio_files"),
            [ds_submit],
            [ds_done],
            "参考音频按 targets 挂到 refs[1].reference_voice",
        ),
        (
            "dashscope/wan3-t2v",
            "dashscope-wan3.json",
            "wan3.0-video",
            {
                "prompt": "p",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "generate_audio": False,
            },
            {},
            [ds_submit],
            [ds_done],
            "无首帧：ratio 下发",
        ),
        (
            "dashscope/wan3-all",
            "dashscope-wan3.json",
            "wan3.0-video",
            {
                "prompt": "p",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "generate_audio": True,
                "start_image": start,
                "end_image": end,
                "reference_images": [ref1],
                "reference_audio_files": [wav],
            },
            assets("start_image", "end_image", "reference_images", "reference_audio_files", refs=1),
            [ds_submit],
            [ds_done],
            "有首帧：ratio 不下发",
        ),
        (
            "dashscope/failed",
            "dashscope-t2v.json",
            "wan2.7-t2v",
            {"prompt": "p", "duration_seconds": 5, "aspect_ratio": "16:9", "resolution": "720p"},
            {},
            [ds_submit],
            [
                {
                    "output": {
                        "task_id": "t-1",
                        "task_status": "FAILED",
                        "code": "DataInspectionFailed",
                        "message": "boom",
                    },
                    "request_id": "r",
                }
            ],
            "",
        ),
        (
            "dashscope/unknown",
            "dashscope-t2v.json",
            "wan2.7-t2v",
            {"prompt": "p", "duration_seconds": 5, "aspect_ratio": "16:9", "resolution": "720p"},
            {},
            [ds_submit],
            [{"output": {"task_id": "t-1", "task_status": "UNKNOWN"}, "request_id": "r"}],
            "task_id 24h 过期",
        ),
        (
            "dashscope/submit_biz_error",
            "dashscope-t2v.json",
            "wan2.7-t2v",
            {"prompt": "p", "duration_seconds": 5, "aspect_ratio": "16:9", "resolution": "720p"},
            {},
            [{"code": "InvalidApiKey", "message": "Invalid API-key provided.", "request_id": "r"}],
            [ds_done],
            "HTTP 200 + 业务码，无 output.task_id",
        ),
    ]:
        params = {
            "base_url": ds_base,
            "api_key": "K",
            "model": model,
            "prompt": req["prompt"],
            "duration": req["duration_seconds"],
            "aspect_ratio": req["aspect_ratio"],
            "resolution": req.get("resolution"),
            "generate_audio": req.get("generate_audio", True),
            "seed": req.get("seed"),
        }
        cases.append((Case(cid, tpl, params, req, ast, ds_routes(submit, polls), mk_ds(model), note), run_http_case))

    # ── kling（bearer）────────────────────────────────────────────────
    from lib.video_backends.kling import KlingVideoBackend

    kl_base = "https://api-beijing.klingai.com/v1"
    kl_submit = {
        "code": 0,
        "message": "SUCCEED",
        "request_id": "r-1",
        "data": {"task_id": "t-1", "task_status": "submitted", "created_at": 1, "updated_at": 1},
    }
    kl_processing = {
        "code": 0,
        "message": "SUCCEED",
        "request_id": "r-2",
        "data": {"task_id": "t-1", "task_status": "processing"},
    }
    kl_done = {
        "code": 0,
        "message": "SUCCEED",
        "request_id": "r-3",
        "data": {
            "task_id": "t-1",
            "task_status": "succeed",
            "task_result": {"videos": [{"id": "v1", "url": "https://cdn.test/kling/v.mp4", "duration": "5"}]},
        },
    }

    def kl_routes(polls: list[dict]):
        return {
            "submit": ("POST", rf"^{kl_base}/videos/[^/]+$", [kl_submit]),
            "poll": ("GET", rf"^{kl_base}/videos/[^/]+/[^/]+$", polls),
        }

    def mk_kl(model, mode="bearer"):
        if mode == "jwt":
            return lambda: KlingVideoBackend(auth_mode="jwt", access_key="ak-1", secret_key="s" * 40, model=model)
        return lambda: KlingVideoBackend(auth_mode="bearer", api_key="K", model=model)

    for cid, tpl, model, req, ast, polls, note, mode in [
        (
            "kling/t2v",
            "kling-text2video.json",
            "kling-v3",
            {"prompt": "a cat walking", "duration_seconds": 5, "aspect_ratio": "9:16", "generate_audio": True},
            {},
            [kl_processing, kl_done],
            "",
            "bearer",
        ),
        (
            "kling/i2v-tail-pro",
            "kling-image2video.json",
            "kling-v3",
            {
                "prompt": "a cat walking",
                "duration_seconds": 10,
                "aspect_ratio": "9:16",
                "generate_audio": False,
                "service_tier": "pro",
                "start_image": start,
                "end_image": end,
            },
            assets("start_image", "end_image"),
            [kl_done],
            "service_tier=pro → mode=pro，格式无该变量",
            "bearer",
        ),
        (
            "kling/r2v",
            "kling-multi-image2video.json",
            "kling-v3-omni",
            {
                "prompt": "两人",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "generate_audio": True,
                "reference_images": [ref1, ref2],
            },
            assets("reference_images"),
            [kl_done],
            "",
            "bearer",
        ),
        (
            "kling/failed",
            "kling-text2video.json",
            "kling-v3",
            {"prompt": "x", "duration_seconds": 5, "aspect_ratio": "9:16"},
            {},
            [
                {
                    "code": 0,
                    "message": "SUCCEED",
                    "data": {"task_id": "t-1", "task_status": "failed", "task_status_msg": "nsfw"},
                }
            ],
            "",
            "bearer",
        ),
        (
            "kling/envelope_error_poll",
            "kling-text2video.json",
            "kling-v3",
            {"prompt": "x", "duration_seconds": 5, "aspect_ratio": "9:16"},
            {},
            [{"code": 1200, "message": "bad task", "request_id": "r"}],
            "HTTP 200 + 顶层 code≠0，无 task_status",
            "bearer",
        ),
        (
            "kling/jwt-t2v",
            None,
            "kling-v3",
            {"prompt": "a cat walking", "duration_seconds": 5, "aspect_ratio": "9:16", "generate_audio": True},
            {},
            [kl_done],
            "JWT 模式：Authorization 为 HS256 签名 token，格式 auth 节不可表达",
            "jwt",
        ),
    ]:
        params = {
            "base_url": kl_base,
            "api_key": "K",
            "model": model,
            "prompt": req["prompt"],
            "duration": req["duration_seconds"],
            "aspect_ratio": req["aspect_ratio"],
            "resolution": None,
            "generate_audio": req.get("generate_audio", True),
            "seed": None,
        }
        cases.append((Case(cid, tpl, params, req, ast, kl_routes(polls), mk_kl(model, mode), note), run_http_case))

    # ── minimax ───────────────────────────────────────────────────────
    from lib.video_backends.minimax import MiniMaxVideoBackend

    mm_host = "https://api.minimaxi.com"
    mm_ok = {"status_code": 0, "status_msg": "success"}
    mm_submit = {"task_id": "t-1", "base_resp": mm_ok}
    mm_processing = {"task_id": "t-1", "status": "Processing", "base_resp": mm_ok}
    mm_success = {"task_id": "t-1", "status": "Success", "file_id": "file-9", "base_resp": mm_ok}
    mm_retrieve = {
        "file": {"file_id": "file-9", "download_url": "https://cdn.test/mm/final.mp4"},
        "base_resp": {"status_code": 0},
    }
    h3_running = {"task": {"id": "h3-task", "model": "MiniMax-H3", "status": "running"}, "base_resp": mm_ok}
    h3_done = {
        "task": {
            "id": "h3-task",
            "model": "MiniMax-H3",
            "status": "succeeded",
            "content": {"url": "https://cdn.test/mm/h3.mp4"},
        },
        "base_resp": mm_ok,
    }

    def mm_routes(v: str, polls: list[dict], retrieve: list[dict] | None):
        r = {
            "submit": ("POST", rf"^{mm_host}/{v}/video_generation$", [mm_submit]),
            "poll": ("GET", rf"^{mm_host}/{v}/query/video_generation", polls),
        }
        if retrieve is not None:
            r["retrieve"] = ("GET", rf"^{mm_host}/v1/files/retrieve", retrieve)
        return r

    def mk_mm(model):
        return lambda: MiniMaxVideoBackend(api_key="K", model=model)

    for cid, tpl, model, v, req, ast, polls, retrieve, note in [
        (
            "minimax/hailuo-t2v",
            "minimax-hailuo-v1.json",
            "MiniMax-Hailuo-2.3",
            "v1",
            {"prompt": "a cat", "duration_seconds": 6, "resolution": "768p", "aspect_ratio": "16:9"},
            {},
            [mm_processing, mm_success],
            [mm_retrieve],
            "两步取 URL：file_id → files/retrieve",
        ),
        (
            "minimax/hailuo-i2v",
            "minimax-hailuo-v1.json",
            "MiniMax-Hailuo-2.3",
            "v1",
            {
                "prompt": "a cat",
                "duration_seconds": 6,
                "resolution": "1080p",
                "aspect_ratio": "16:9",
                "start_image": start,
            },
            assets("start_image"),
            [mm_success],
            [mm_retrieve],
            "",
        ),
        (
            "minimax/s2v",
            "minimax-s2v-01.json",
            "S2V-01",
            "v1",
            {
                "prompt": "a cat",
                "duration_seconds": 6,
                "resolution": "768p",
                "aspect_ratio": "16:9",
                "reference_images": [ref1],
            },
            assets("reference_images", refs=1),
            [mm_success],
            [mm_retrieve],
            "",
        ),
        (
            "minimax/hailuo-fail",
            "minimax-hailuo-v1.json",
            "MiniMax-Hailuo-2.3",
            "v1",
            {"prompt": "a cat", "duration_seconds": 6, "resolution": "768p", "aspect_ratio": "16:9"},
            {},
            [{"task_id": "t-1", "status": "Fail", "base_resp": {"status_code": 2013, "status_msg": "invalid params"}}],
            None,
            "",
        ),
        (
            "minimax/hailuo-base-resp-error-poll",
            "minimax-hailuo-v1.json",
            "MiniMax-Hailuo-2.3",
            "v1",
            {"prompt": "a cat", "duration_seconds": 6, "resolution": "768p", "aspect_ratio": "16:9"},
            {},
            [{"task_id": "t-1", "base_resp": {"status_code": 1004, "status_msg": "auth failed"}}],
            None,
            "HTTP 200 + base_resp.status_code≠0，无 status",
        ),
        (
            "minimax/h3-t2v",
            "minimax-h3-v2.json",
            "MiniMax-H3",
            "v2",
            {"prompt": "a cat", "duration_seconds": 6, "resolution": "768p", "aspect_ratio": "16:9"},
            {},
            [h3_running, h3_done],
            None,
            "",
        ),
        (
            "minimax/h3-i2v-last",
            "minimax-h3-v2.json",
            "MiniMax-H3",
            "v2",
            {
                "prompt": "a cat",
                "duration_seconds": 6,
                "resolution": "768p",
                "aspect_ratio": "adaptive",
                "start_image": start,
                "end_image": end,
            },
            assets("start_image", "end_image"),
            [h3_done],
            None,
            "编排层已把 aspect_ratio 改写为 adaptive",
        ),
        (
            "minimax/h3-r2v-audio",
            "minimax-h3-v2.json",
            "MiniMax-H3",
            "v2",
            {
                "prompt": "a cat",
                "duration_seconds": 6,
                "resolution": "768p",
                "aspect_ratio": "16:9",
                "reference_images": [ref1, ref2],
                "reference_audio_files": [wav],
            },
            assets("reference_images", "reference_audio_files"),
            [h3_done],
            None,
            "",
        ),
        (
            "minimax/h3-failed",
            "minimax-h3-v2.json",
            "MiniMax-H3",
            "v2",
            {"prompt": "a cat", "duration_seconds": 6, "resolution": "768p", "aspect_ratio": "16:9"},
            {},
            [{"task": {"id": "h3-task", "status": "failed", "error": "quota exhausted"}, "base_resp": mm_ok}],
            None,
            "",
        ),
    ]:
        params = {
            "base_url": f"{mm_host}/{v}",
            "api_key": "K",
            "model": model,
            "prompt": req["prompt"],
            "duration": req["duration_seconds"],
            "aspect_ratio": req["aspect_ratio"],
            "resolution": req.get("resolution"),
            "generate_audio": req.get("generate_audio", True),
            "seed": req.get("seed"),
        }
        cases.append(
            (Case(cid, tpl, params, req, ast, mm_routes(v, polls, retrieve), mk_mm(model), note), run_http_case)
        )

    # ── newapi ────────────────────────────────────────────────────────
    from lib.video_backends.newapi import NewAPIVideoBackend

    na_base = "https://x/v1"
    na_submit = {"task_id": "task-42", "status": "queued"}
    na_running = {"task_id": "task-42", "status": "in_progress"}
    na_done = {
        "task_id": "task-42",
        "status": "completed",
        "url": "https://cdn.test/na/out.mp4",
        "format": "mp4",
        "metadata": {"duration": 5, "fps": 24, "width": 720, "height": 1280, "seed": 0},
    }

    def na_routes(polls: list[dict]):
        return {
            "submit": ("POST", rf"^{na_base}/video/generations$", [na_submit]),
            "poll": ("GET", rf"^{na_base}/video/generations/[^/]+$", polls),
        }

    def mk_na():
        return NewAPIVideoBackend(api_key="K", base_url=na_base, model="kling-v1")

    na_req = {"prompt": "A cat running", "duration_seconds": 5, "aspect_ratio": "9:16", "resolution": "720p", "seed": 7}
    for cid, req, ast, polls, note in [
        ("newapi/t2v", {}, {}, [na_running, na_done], ""),
        ("newapi/i2v", {"start_image": start}, assets("start_image"), [na_done], ""),
        (
            "newapi/wrapped",
            {},
            {},
            [
                {
                    "code": "success",
                    "data": {
                        "task_id": "task-42",
                        "status": "SUCCESS",
                        "result_url": "https://cdn.test/na/wrapped.mp4",
                        "metadata": {"duration": 8, "seed": 4242},
                    },
                }
            ],
            "{code,data} 包装体",
        ),
        (
            "newapi/failed",
            {},
            {},
            [{"task_id": "task-42", "status": "failed", "error": {"code": 500, "message": "upstream down"}}],
            "",
        ),
        ("newapi/expired", {}, {}, [{"task_id": "task-42", "status": "expired"}], ""),
    ]:
        params = {
            "base_url": na_base,
            "api_key": "K",
            "model": "kling-v1",
            "prompt": na_req["prompt"],
            "duration": 5,
            "aspect_ratio": "9:16",
            "resolution": "720p",
            "generate_audio": True,
            "seed": 7,
        }
        cases.append(
            (
                Case(cid, "newapi-builtin.json", params, {**na_req, **req}, ast, na_routes(polls), mk_na, note),
                run_http_case,
            )
        )

    # ── v2 ────────────────────────────────────────────────────────────
    from lib.video_backends.v2_video_generations import V2VideoGenerationsBackend

    v2_root = "https://api.aimlapi.com"
    v2_gen = f"{v2_root}/v2/video/generations"
    v2_done = {"id": "gen-1", "status": "completed", "video": {"url": "https://cdn.test/v2/v.mp4"}}

    def v2_routes(submit: dict, polls: list[dict]):
        return {"submit": ("POST", rf"^{v2_gen}$", [submit]), "poll": ("GET", rf"^{v2_gen}\?", polls)}

    def mk_v2():
        return V2VideoGenerationsBackend(api_key="K", base_url=v2_root, model="seedance-1.0")

    v2_req = {"prompt": "a cat", "duration_seconds": 5, "aspect_ratio": "16:9", "resolution": "720p", "seed": 42}
    for cid, req, ast, submit, polls, note in [
        ("v2/t2v", {}, {}, {"id": "gen-1", "status": "queued"}, [{"id": "gen-1", "status": "generating"}, v2_done], ""),
        (
            "v2/i2v-last",
            {"start_image": start, "end_image": end},
            assets("start_image", "end_image"),
            {"id": "gen-1", "status": "queued"},
            [v2_done],
            "",
        ),
        (
            "v2/r2v",
            {"reference_images": [ref1, ref2]},
            assets("reference_images"),
            {"id": "gen-1", "status": "queued"},
            [v2_done],
            "",
        ),
        (
            "v2/int-id",
            {},
            {},
            {"id": 123},
            [{"id": 123, "status": "completed", "url": "https://cdn.test/v2/n.mp4"}],
            "整数 id 与扁平 url",
        ),
        ("v2/failed", {}, {}, {"generation_id": "vg_1"}, [{"status": "error", "error": {"message": "boom"}}], ""),
    ]:
        params = {
            "base_url": v2_root,
            "api_key": "K",
            "model": "seedance-1.0",
            "prompt": "a cat",
            "duration": 5,
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "generate_audio": True,
            "seed": 42,
        }
        cases.append(
            (
                Case(
                    cid,
                    "v2-video-generations.json",
                    params,
                    {**v2_req, **req},
                    ast,
                    v2_routes(submit, polls),
                    mk_v2,
                    note,
                ),
                run_http_case,
            )
        )

    # ── vidu ──────────────────────────────────────────────────────────
    from lib.video_backends.vidu import ViduVideoBackend

    vd_base = "https://api.vidu.cn/ent/v2"
    vd_submit = {"task_id": "job-1", "state": "created", "model": "viduq3-turbo", "credits": 96}
    vd_processing = {"id": "job-1", "state": "processing", "credits": 96}
    vd_done = {
        "id": "job-1",
        "state": "success",
        "credits": 96,
        "creations": [
            {"id": "c1", "url": "https://cdn.test/vidu/out.mp4", "cover_url": "https://cdn.test/vidu/cover.png"}
        ],
    }

    def vd_routes(polls: list[dict]):
        return {
            "submit": ("POST", rf"^{vd_base}/(text2video|img2video|start-end2video|reference2video)$", [vd_submit]),
            "poll": ("GET", rf"^{vd_base}/tasks/[^/]+/creations$", polls),
        }

    def mk_vd():
        return ViduVideoBackend(api_key="K", model="viduq3-turbo", base_url=vd_base)

    vd_req = {
        "prompt": "hello",
        "duration_seconds": 8,
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "seed": 3,
        "generate_audio": True,
    }
    for cid, tpl, req, ast, polls, note in [
        ("vidu/t2v", "vidu-text2video.json", {}, {}, [vd_processing, vd_done], ""),
        (
            "vidu/i2v",
            "vidu-img2video.json",
            {"start_image": start},
            assets("start_image"),
            [vd_done],
            "/img2video 不带 aspect_ratio",
        ),
        (
            "vidu/start-end",
            "vidu-start-end2video.json",
            {"start_image": start, "end_image": end},
            assets("start_image", "end_image"),
            [vd_done],
            "",
        ),
        (
            "vidu/r2v",
            "vidu-reference2video.json",
            {"reference_images": [ref1, ref2]},
            assets("reference_images"),
            [vd_done],
            "",
        ),
        (
            "vidu/failed",
            "vidu-text2video.json",
            {},
            {},
            [{"id": "job-1", "state": "failed", "err_code": "ContentModeration"}],
            "",
        ),
    ]:
        params = {
            "base_url": vd_base,
            "api_key": "K",
            "model": "viduq3-turbo",
            "prompt": "hello",
            "duration": 8,
            "aspect_ratio": "9:16",
            "resolution": "720p",
            "generate_audio": True,
            "seed": 3,
        }
        cases.append((Case(cid, tpl, params, {**vd_req, **req}, ast, vd_routes(polls), mk_vd, note), run_http_case))

    return cases


async def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name, content in ASSET_BYTES.items():
            (tmp / name).write_bytes(content)
        results = []
        for case, runner in build_cases(tmp):
            rec = await runner(case, tmp)
            if case.id == "kling/jwt-t2v":
                auth = rec["requests"]["submit"][0]["headers"].get("authorization", "")
                token = auth.removeprefix("Bearer ")
                rec["jwt_payload"] = jwt.decode(token, options={"verify_signature": False})
                rec["jwt_header"] = jwt.get_unverified_header(token)
                rec["requests"]["submit"][0]["headers"]["authorization"] = "Bearer <HS256 JWT>"
            results.append(rec)
            status = "ok " if "result" in rec else "err"
            print(f"[{status}] {case.id}: {rec.get('error') or rec['result']['video_uri']}")
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(results)} cases → {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
