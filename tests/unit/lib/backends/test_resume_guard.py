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
        # 升级前经 ark-seedance 提交，模型现在挂它的任一后继
        ("ark-seedance", "ark-seedance-2"),
        ("ark-seedance", "ark-seedance-1"),
        ("dashscope-async-video", "dashscope-always-on-video"),
        (endpoint_guard("dashscope-async-video", _PATHS), endpoint_guard("dashscope-wan3-video", _PATHS)),
        (builtin_endpoint_guard("agnes-video"), builtin_endpoint_guard("agnes-video-2-5")),
        (builtin_endpoint_guard("gemini-video"), builtin_endpoint_guard("gemini-vertex-video")),
        (endpoint_guard("ark-seedance", _PATHS), endpoint_guard("ark-seedance-2-5", _PATHS)),
        # 升级前提交的内置任务没有守卫，当下仍是内置供应商
        (None, builtin_endpoint_guard("ark-seedance-2")),
        (builtin_endpoint_guard("ark-seedance-2"), builtin_endpoint_guard("ark-seedance-2")),
        (builtin_endpoint_guard("kling-video-model-path"), builtin_endpoint_guard("kling-video-model-path")),
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
        # 可灵旧版 API 与新版 API（模型名在路径里）之间换了协议，退役的 kling-video 也不以新版为后继
        ("kling-video", "kling-video-model-path"),
        (builtin_endpoint_guard("kling-video-pro-audio"), builtin_endpoint_guard("kling-video-model-path")),
        (builtin_endpoint_guard("kling-video-model-path"), builtin_endpoint_guard("kling-video-audio")),
        (endpoint_guard("kling-video-silent", _PATHS), endpoint_guard("kling-video-model-path", _PATHS)),
        # 换了协议
        ("ark-seedance", "openai-video"),
        # 后继之间互换仍是换了端点
        ("ark-seedance-1", "ark-seedance-2"),
        ("dashscope-wan3-video", "dashscope-always-on-video"),
        (builtin_endpoint_guard("agnes-video-2"), builtin_endpoint_guard("agnes-video-2-5")),
        # 内置供应商下用户添加的模型提交后换了端点
        (builtin_endpoint_guard("ark-seedance-2"), builtin_endpoint_guard("ark-seedance-1")),
        # 路径覆盖变了
        (endpoint_guard("ark-seedance", _PATHS), "ark-seedance-2"),
        # 内置与自定义之间切换，即使端点同名
        ("ark-seedance", builtin_endpoint_guard("ark-seedance-2")),
        (builtin_endpoint_guard("ark-seedance-2"), "ark-seedance-2"),
        (None, "ark-seedance-2"),
        ("ark-seedance", None),
    ],
)
def test_changed_protocols_are_rejected(submitted: str | None, current: str | None) -> None:
    assert not resume_guard_accepts(submitted, current)
