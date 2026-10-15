"""路径覆盖的校验、生效值与续跑比对标识。"""

from __future__ import annotations

import httpx
import pytest

from lib.custom_provider.path_overrides import (
    EndpointRequest,
    effective_path_overrides,
    endpoint_guard,
    path_override_layer,
    path_problem,
)

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
