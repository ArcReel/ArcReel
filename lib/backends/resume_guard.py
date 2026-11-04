"""视频任务续跑的协议守卫：提交时冻结调用端点，续跑时比对，端点拆分改名后协议等价的仍可续跑。

守卫值写进执行检查点的 ``endpoint_guard``（见 ``lib.script.reference_video.execution_checkpoint``），取值两种：

- 自定义供应商：调用端点键，有路径覆盖时连同覆盖（``lib.custom_provider.path_overrides.endpoint_guard``）；
- 内置供应商：``builtin:`` 加模型挂接的内置调用端点名（:func:`builtin_endpoint_guard`）。升级前提交的内置任务没有
  守卫（``None``），按提交时的内置通道续跑。

续跑只轮询并取回结果，所以判据是「当下的端点能否按提交时的协议查到这个任务」：端点相同，或提交时的端点已经拆分
退役、当下的端点是它协议等价的后继（:data:`RETIRED_ENDPOINT_SUCCESSORS`）。其余情况视为换了协议，拒绝续跑。
"""

from __future__ import annotations

from collections.abc import Mapping

#: 内置供应商守卫的前缀：与自定义供应商的端点键区分开，内置与自定义之间切换不会因端点同名而放行。
BUILTIN_GUARD_PREFIX = "builtin:"

# 自定义守卫里路径覆盖部分的分隔，与 lib.custom_provider.path_overrides.endpoint_guard 一致。
_PATHS_SEPARATOR = " paths="

#: 拆分后退役的端点名 → 协议等价的后继端点。后继共用创建任务与轮询接口，只是请求体与计量不同，提交时挂旧端点的
#: 任务经任一后继都能取回结果。拆分一个端点时在这里登记一行；后继之间互换不在此列，仍按换了端点拒绝。
RETIRED_ENDPOINT_SUCCESSORS: Mapping[str, frozenset[str]] = {
    "grok-video": frozenset({"grok-video-classic", "grok-video-mixed"}),
    "vidu-video": frozenset(
        {
            "vidu-video-" + name
            for name in (
                "generic",
                "q3-pro",
                "q3-turbo",
                "q3-fast",
                "q3-reference",
                "q2",
                "q2-pro",
                "q2-fast",
                "q1",
                "q1-classic",
                "2",
            )
        }
    ),
    "kling-video": frozenset(
        {"kling-video-pro-silent", "kling-video-pro-audio", "kling-video-audio", "kling-video-silent"}
    ),
    "ark-seedance": frozenset({"ark-seedance-1", "ark-seedance-2", "ark-seedance-2-5"}),
}


def builtin_endpoint_guard(endpoint: str) -> str:
    """内置供应商经 ``endpoint`` 提交的视频任务的守卫值。"""
    return f"{BUILTIN_GUARD_PREFIX}{endpoint}"


def _split(guard: str) -> tuple[str, str, str]:
    """守卫拆成（内置前缀、端点名、路径覆盖部分）；没有的部分为空串。"""
    prefix = BUILTIN_GUARD_PREFIX if guard.startswith(BUILTIN_GUARD_PREFIX) else ""
    endpoint, separator, paths = guard.removeprefix(prefix).partition(_PATHS_SEPARATOR)
    return prefix, endpoint, separator + paths


def resume_guard_accepts(submitted: str | None, current: str | None) -> bool:
    """提交时冻结的守卫 ``submitted`` 下的任务能否经当下的守卫 ``current`` 续跑。

    - 两者相同：放行。
    - ``submitted`` 为 ``None``：升级前提交的内置任务，当下仍是内置供应商时放行，换成自定义供应商时拒绝。
    - 提交时的端点已拆分退役：当下的端点是它的后继，且内置与自定义的归属、路径覆盖都没变时放行。
    """
    if submitted == current:
        return True
    if current is None:
        return False
    if submitted is None:
        return current.startswith(BUILTIN_GUARD_PREFIX)
    submitted_prefix, submitted_endpoint, submitted_paths = _split(submitted)
    current_prefix, current_endpoint, current_paths = _split(current)
    return (
        submitted_prefix == current_prefix
        and submitted_paths == current_paths
        and current_endpoint in RETIRED_ENDPOINT_SUCCESSORS.get(submitted_endpoint, frozenset())
    )
