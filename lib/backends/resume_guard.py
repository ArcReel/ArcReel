"""Video-task resume protocol guard: freeze submission endpoints, compare at resume, allow protocol-equivalent split successors.

Execution checkpoints store ``endpoint_guard`` (``lib.script.reference_video.execution_checkpoint``) as:

- Custom providers: endpoint keys with any path overrides (``lib.custom_provider.path_overrides.endpoint_guard``).
- Built-in providers: ``builtin:`` plus the model-bound endpoint name (:func:`builtin_endpoint_guard`).
  Preupgrade built-in tasks lack guards (``None``) and compatibly permit current built-in endpoints;
  switching endpoints before old tasks complete may still break resume.

Resume only polls/retrieves results, so ask whether current endpoints can find tasks under submission
protocols. Allow identical endpoints or protocol-equivalent successors of split/retired submitted endpoints
(:data:`RETIRED_ENDPOINT_SUCCESSORS`). Otherwise reject changed protocols.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Built-in guard prefix distinguishes custom endpoint keys, preventing same-name built-in/custom switches from passing.
BUILTIN_GUARD_PREFIX = "builtin:"

# Path-override separator in custom guards, matching lib.custom_provider.path_overrides.endpoint_guard.
_PATHS_SEPARATOR = " paths="

#: Retired split endpoints → protocol-equivalent successors sharing create/poll APIs, differing only in bodies/metering.
#: Old-endpoint tasks can retrieve through any successor. Register one row per split; switching between successors still rejects changed endpoints.
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
    "dashscope-async-video": frozenset(
        {
            "dashscope-video",
            "dashscope-always-on-video",
            "dashscope-reference-video",
            "dashscope-reference-voice-video",
            "dashscope-wan3-video",
        }
    ),
    "agnes-video": frozenset({"agnes-video-2", "agnes-video-2-5"}),
    "gemini-video": frozenset({"gemini-vertex-video"}),
}


def builtin_endpoint_guard(endpoint: str) -> str:
    """Guard for built-in video tasks submitted through ``endpoint``."""
    return f"{BUILTIN_GUARD_PREFIX}{endpoint}"


def _split(guard: str) -> tuple[str, str, str]:
    """Split guards into built-in prefix, endpoint name, path-override portion; absent portions are empty strings."""
    prefix = BUILTIN_GUARD_PREFIX if guard.startswith(BUILTIN_GUARD_PREFIX) else ""
    endpoint, separator, paths = guard.removeprefix(prefix).partition(_PATHS_SEPARATOR)
    return prefix, endpoint, separator + paths


def resume_guard_accepts(submitted: str | None, current: str | None) -> bool:
    """Whether tasks frozen under ``submitted`` can resume through current ``current`` guards.

    - Equal guards: allow.
    - ``submitted`` is ``None``: preupgrade built-in tasks allow current built-in providers, rejecting custom providers.
    - Split/retired submitted endpoints: allow successors only if built-in/custom ownership and path overrides remain unchanged.
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
