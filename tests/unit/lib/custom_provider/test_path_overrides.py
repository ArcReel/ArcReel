"""路径覆盖的校验、生效值与续跑比对标识。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.custom_provider.endpoints import ENDPOINT_REGISTRY
from lib.custom_provider.path_overrides import (
    EndpointRequest,
    effective_path_overrides,
    endpoint_guard,
    path_override_layer,
    path_problem,
    replaying_base_url,
)
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http

_POLL = EndpointRequest(
    name="poll",
    display_name_key="cp_request_poll",
    default_path="/v1/videos/{{ task_id }}",
    placeholders=("model", "task_id"),
)


@pytest.mark.parametrize(
    ("path", "problem"),
    [
        ("/api/videos/{{ task_id }}?model={{model}}", None),
        ("api/videos", "path_override_invalid_path"),
        ("/api/videos /{{ task_id }}", "path_override_invalid_path"),
        ("/api/videos/{{ task_id }", "path_override_invalid_path"),
        ("/api/videos/{{ task_id }}#frag", "path_override_invalid_path"),
        ("/api/videos/{{ result_id }}", "path_override_placeholder_not_allowed"),
        ("/api/{{ api_key }}", "path_override_placeholder_not_allowed"),
    ],
)
def test_path_problem(path: str, problem: str | None):
    assert path_problem(_POLL, path) == problem


def test_overrides_for_requests_the_endpoint_no_longer_declares_are_ignored():
    stored = {"poll": "/api/videos/{{ task_id }}", "result": "/api/result"}

    assert effective_path_overrides(stored, [_POLL]) == {"poll": "/api/videos/{{ task_id }}"}


def test_guard_equals_the_endpoint_key_without_overrides_and_changes_with_them():
    assert endpoint_guard("ce-3", {}) == "ce-3"
    assert endpoint_guard("ce-3", {"poll": "/a"}) != endpoint_guard("ce-3", {"poll": "/b"})


def test_layer_matches_base_url_paths_with_percent_escapes_and_keeps_them():
    base = "https://relay.test/proxy%2Ftenant"
    layer = path_override_layer([_POLL], {"poll": "/tasks/{{ task_id }}"}, base, base, "m")
    assert layer is not None
    request = httpx.Request("GET", f"{base}/v1/videos/a%2Fb")

    layer.rewrite(request)

    assert request.url.raw_path == b"/proxy%2Ftenant/tasks/a%2Fb"


@pytest.mark.parametrize("version", ["", "/v1"])
async def test_resume_after_base_path_change_still_hits_the_overrides(tmp_path: Path, version: str):
    # 提交时 base_url 是 relay.test/old，在途改成 /new 后重启续跑：轮询与取件按提交时的 base 命中覆盖
    overrides = {"poll": "/api/tasks/{{ task_id }}", "download": "/api/tasks/{{ task_id }}/file"}
    provider = SimpleNamespace(provider_id="cp", base_url=f"https://relay.test/new{version}", api_key="k")
    backend = ENDPOINT_REGISTRY["openai-video"].build_backend(cast("Any", provider), "sora-2", overrides)
    video = {"id": "vid-a", "object": "video", "model": "sora-2", "status": "completed", "progress": 100}
    video |= {"seconds": "8", "size": "720x1280", "created_at": 1}

    with capture_http() as router, bounded_poll_clock():
        poll = router.get(f"https://relay.test/old{version}/api/tasks/vid-a").mock(
            return_value=httpx.Response(200, json=video)
        )
        router.get(f"https://relay.test/old{version}/api/tasks/vid-a/file").mock(
            return_value=httpx.Response(200, content=b"resumed")
        )

        await backend.resume_video(
            "vid-a",
            VideoGenerationRequest(
                prompt="p",
                output_path=tmp_path / "out.mp4",
                duration_seconds=8,
                submitted_base_url="https://relay.test/old/v1",
            ),
        )

    assert poll.call_count == 1
    assert (tmp_path / "out.mp4").read_bytes() == b"resumed"


async def test_dashscope_resume_restores_the_replaced_base_suffix(tmp_path: Path):
    provider = SimpleNamespace(provider_id="cp", base_url="https://relay.test/new/compatible-mode/v1", api_key="k")
    backend = ENDPOINT_REGISTRY["dashscope-async-video"].build_backend(
        cast("Any", provider), "wan2.6-t2v", {"poll": "/proxy/tasks/{{ task_id }}"}
    )
    with capture_http() as router, bounded_poll_clock():
        poll = router.get("https://relay.test/old/compatible-mode/v1/proxy/tasks/t").mock(
            return_value=httpx.Response(
                200, json={"output": {"task_status": "SUCCEEDED", "video_url": "https://cdn.test/resumed.mp4"}}
            )
        )
        router.get("https://cdn.test/resumed.mp4").mock(return_value=httpx.Response(200, content=b"resumed"))

        await backend.resume_video(
            "t",
            VideoGenerationRequest(
                prompt="p", output_path=tmp_path / "out.mp4", submitted_base_url="https://relay.test/old/api/v1"
            ),
        )

    assert poll.call_count == 1
    assert (tmp_path / "out.mp4").read_bytes() == b"resumed"


@pytest.mark.parametrize(
    ("current_base", "submitted_base", "sent", "expected"),
    [
        # 归一化补了 /v1：提交时的原样路径按同一差量还原
        ("https://relay.test/new", "https://relay.test/old/v1", "/old/v1/v1/videos/t", "/old/tasks/t"),
        # 原样值本身就带版本段：归一化不改路径
        ("https://relay.test/new/v1", "https://relay.test/old/v1", "/old/v1/v1/videos/t", "/old/v1/tasks/t"),
    ],
)
def test_layer_matches_replayed_requests_against_the_submitted_base(
    current_base: str, submitted_base: str, sent: str, expected: str
):
    normalized = current_base if current_base.endswith("/v1") else f"{current_base}/v1"
    layer = path_override_layer([_POLL], {"poll": "/tasks/{{ task_id }}"}, current_base, normalized, "m")
    assert layer is not None
    request = httpx.Request("GET", f"https://relay.test{sent}")

    with replaying_base_url(submitted_base):
        layer.rewrite(request)

    assert request.url.raw_path == expected.encode()


def test_layer_leaves_requests_outside_both_bases_untouched():
    layer = path_override_layer(
        [_POLL], {"poll": "/tasks/{{ task_id }}"}, "https://relay.test/new", "https://relay.test/new", "m"
    )
    assert layer is not None
    request = httpx.Request("GET", "https://cdn.test/files/v1/videos/t")

    with replaying_base_url("https://relay.test/old"):
        layer.rewrite(request)

    assert request.url.raw_path == b"/files/v1/videos/t"
