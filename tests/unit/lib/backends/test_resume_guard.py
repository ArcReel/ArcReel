"""续跑协议守卫：退役端点名映射到协议等价的后继，其余变化一律拒绝。"""

import pytest

from lib.backends.resume_guard import builtin_endpoint_guard, resume_guard_accepts
from lib.custom_provider.path_overrides import endpoint_guard

_PATHS = {"poll": "/v2/tasks/{{ task_id }}"}


@pytest.mark.parametrize(
    ("submitted", "current"),
    [
        ("grok-video", "grok-video-classic"),
        ("vidu-video", "vidu-video-q3-turbo"),
        ("kling-video", "kling-video-pro-audio"),
        (builtin_endpoint_guard("kling-video"), builtin_endpoint_guard("kling-video-audio")),
        (endpoint_guard("kling-video", _PATHS), endpoint_guard("kling-video-silent", _PATHS)),
        ("ark-seedance", "ark-seedance-2"),
        ("ark-seedance", "ark-seedance-1"),
        ("dashscope-async-video", "dashscope-always-on-video"),
        (endpoint_guard("dashscope-async-video", _PATHS), endpoint_guard("dashscope-wan3-video", _PATHS)),
        (builtin_endpoint_guard("agnes-video"), builtin_endpoint_guard("agnes-video-2-5")),
        (builtin_endpoint_guard("gemini-video"), builtin_endpoint_guard("gemini-vertex-video")),
        (endpoint_guard("ark-seedance", _PATHS), endpoint_guard("ark-seedance-2-5", _PATHS)),
        (None, builtin_endpoint_guard("ark-seedance-2")),
        (builtin_endpoint_guard("ark-seedance-2"), builtin_endpoint_guard("ark-seedance-2")),
        (builtin_endpoint_guard("kling-video-model-path"), builtin_endpoint_guard("kling-video-model-path")),
        (builtin_endpoint_guard("gemini-interactions-video"), builtin_endpoint_guard("gemini-interactions-video")),
        (
            builtin_endpoint_guard("gemini-vertex-interactions-video"),
            builtin_endpoint_guard("gemini-vertex-interactions-video"),
        ),
    ],
)
def test_equivalent_protocols_resume(submitted: str | None, current: str) -> None:
    assert resume_guard_accepts(submitted, current)


@pytest.mark.parametrize(
    ("submitted", "current"),
    [
        ("vidu-video", "openai-video"),
        ("grok-video", "kling-video-audio"),
        ("kling-video", "kling-video-pro-audio paths={}"),
        ("kling-video-audio", "kling-video-pro-audio"),
        ("kling-video", "kling-video-model-path"),
        (builtin_endpoint_guard("kling-video-pro-audio"), builtin_endpoint_guard("kling-video-model-path")),
        (builtin_endpoint_guard("kling-video-model-path"), builtin_endpoint_guard("kling-video-audio")),
        (endpoint_guard("kling-video-silent", _PATHS), endpoint_guard("kling-video-model-path", _PATHS)),
        (builtin_endpoint_guard("gemini-video"), builtin_endpoint_guard("gemini-interactions-video")),
        (builtin_endpoint_guard("gemini-interactions-video"), builtin_endpoint_guard("gemini-video")),
        (builtin_endpoint_guard("gemini-vertex-video"), builtin_endpoint_guard("gemini-vertex-interactions-video")),
        (builtin_endpoint_guard("gemini-vertex-interactions-video"), builtin_endpoint_guard("gemini-vertex-video")),
        ("ark-seedance", "openai-video"),
        ("ark-seedance-1", "ark-seedance-2"),
        ("dashscope-wan3-video", "dashscope-always-on-video"),
        (builtin_endpoint_guard("agnes-video-2"), builtin_endpoint_guard("agnes-video-2-5")),
        (builtin_endpoint_guard("ark-seedance-2"), builtin_endpoint_guard("ark-seedance-1")),
        (endpoint_guard("ark-seedance", _PATHS), "ark-seedance-2"),
        ("ark-seedance", builtin_endpoint_guard("ark-seedance-2")),
        (builtin_endpoint_guard("ark-seedance-2"), "ark-seedance-2"),
        (None, "ark-seedance-2"),
        ("ark-seedance", None),
    ],
)
def test_changed_protocols_are_rejected(submitted: str | None, current: str | None) -> None:
    assert not resume_guard_accepts(submitted, current)
