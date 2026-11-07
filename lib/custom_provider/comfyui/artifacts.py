"""Terminal execution interpretation/artifact selection shared by both media channels.

ComfyUI history never directly states success: it combines node outputs/execution events. Final events determine outcomes;
``output`` bindings identify artifacts; extensions alone distinguish images/videos because
``VHS_VideoCombine`` also exports animated webp, making node types insufficient.

History extensions cannot establish persisted byte containers, so provide file-header predicates
(:func:`container_matches`) for postdownload execution checks.

Pure selection/interpretation functions know history/bindings, independent of backend request/result
objects. One ``media_type`` extension table prevents channels disagreeing
about expected outputs and accepting/rejecting different formats.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from arcreel_market_core.comfyui.bindings import targets_of
from lib.backends.container_sniff import sniff_container

from .failures import EXECUTION_ERROR, INTERRUPTED, ComfyuiError

#: ``media_type`` → allowed artifact extensions.
#:
#: Images deliberately exclude ``.gif`` / ``.apng``: animations imply wrong output bindings,
#: usually video-composition nodes; persisting as storyboard images would retain first frames only. Videos
#: likewise exclude animations, which are not completed-video containers.
#:
#: Three video extensions share ISO BMFF, matching reference-video uploads
#: (``server.services.currency.upload_finalize.UPLOAD_VIDEO_EXTENSIONS``): completed artifacts enter canonical
#: ``lib.project.resource_paths`` ``.mp4`` paths with unchanged bytes, requiring compatible containers.
#: ``.webm`` is excluded: placing those bytes in ``.mp4`` misstates extensions/served MIME.
ARTIFACT_SUFFIXES_BY_MEDIA_TYPE: Mapping[str, frozenset[str]] = {
    "image": frozenset({".png", ".jpg", ".jpeg", ".webp"}),
    "video": frozenset({".mp4", ".mov", ".m4v"}),
}

#: ``media_type`` → allowed container MIME, corresponding to extension tables: three video extensions
#: all sniff as ``video/mp4`` ISO BMFF, so videos have one entry.
ARTIFACT_CONTAINERS_BY_MEDIA_TYPE: Mapping[str, frozenset[str]] = {
    "image": frozenset({"image/png", "image/jpeg", "image/webp"}),
    "video": frozenset({"video/mp4"}),
}

#: Three history output keys, read only on ``output``-bound nodes.
_ARTIFACT_KEYS = ("images", "gifs", "audio")

#: Two ``status.messages`` event names indicating failed outputs.
_EXECUTION_ERROR_EVENT = "execution_error"
_EXECUTION_INTERRUPTED_EVENT = "execution_interrupted"

#: ``comfyui_execution_error`` ``node`` placeholders when failed nodes are unknown.
_UNKNOWN_NODE = "-"


def output_nodes_of(definition: Mapping[str, Any]) -> list[str]:
    """``output``-bound node ids in binding order."""
    return [str(target["node"]) for target in targets_of((definition.get("bindings") or {}).get("output"))]


def output_artifacts(entry: Mapping[str, Any], output_nodes: Sequence[str]) -> list[Mapping[str, Any]]:
    """Files from bound ``output`` nodes, by binding order then each node's three-key order.

    Read bound nodes only: side branches such as ``PreviewImage`` also write history artifacts,
    so whole-graph scans could mistake previews for outputs. Skip all ``type != "output"``;
    ``temp`` means intermediate previews that servers may delete anytime.
    """
    outputs = entry.get("outputs")
    if not isinstance(outputs, Mapping):
        return []
    found: list[Mapping[str, Any]] = []
    for node_id in output_nodes:
        node = outputs.get(node_id)
        if not isinstance(node, Mapping):
            continue
        for key in _ARTIFACT_KEYS:
            items = node.get(key)
            if not isinstance(items, list):
                continue
            found.extend(
                item for item in items if isinstance(item, Mapping) and str(item.get("type") or "") == "output"
            )
    return found


def pick_artifact(artifacts: Sequence[Mapping[str, Any]], media_type: str) -> Mapping[str, Any] | None:
    """First artifact matching ``media_type``; ``None`` without matches.

    Select by extensions, not first items: bound nodes can emit ``images`` and ``gifs`` simultaneously, thumbnails plus videos;
    :func:`output_artifacts` follows ``_ARTIFACT_KEYS`` order, unrelated to actual final artifacts.

    Unlisted extensions/``media_type`` never match, letting callers report
    ``comfyui_output_type_mismatch`` for wrong bindings, instead of persisting
    inappropriate files as version artifacts.
    """
    allowed = ARTIFACT_SUFFIXES_BY_MEDIA_TYPE.get(media_type, frozenset())
    return next((item for item in artifacts if Path(filename_of(item)).suffix.lower() in allowed), None)


def expected_suffixes_text(media_type: str) -> str:
    """Allowed ``media_type`` extension lists for errors; empty for unregistered media.

    Readers recompute ``comfyui_output_type_mismatch`` lists from persisted ``media_type``; never persist allowlists,
    which project static tables rather than failure facts and would drift against historical records.
    """
    return " / ".join(sorted(ARTIFACT_SUFFIXES_BY_MEDIA_TYPE.get(media_type, frozenset())))


def filename_of(artifact: Mapping[str, Any]) -> str:
    """Artifact filenames; empty if missing, tolerated by extension checks/error wording."""
    return str(artifact.get("filename") or "")


def container_matches(head: bytes, media_type: str) -> bool:
    """Whether headers match ``media_type`` containers; false for unregistered ``media_type``.

    Extension tables cover history strings only; save nodes may write webm bytes under ``.mp4``
    names. Persisted bytes are served with extension-derived MIME, requiring header rechecks.

    Magic recognition lives in :func:`lib.backends.container_sniff.sniff_container`; here classify recognized
    containers as media-compatible. Three video extensions all use ISO BMFF, excluding ``video/webm``.
    """
    return sniff_container(head) in ARTIFACT_CONTAINERS_BY_MEDIA_TYPE.get(media_type, frozenset())


def terminal_failure(entry: Mapping[str, Any]) -> ComfyuiError | None:
    """Terminal success/failure: failure codes, or ``None`` for success.

    Read **final** ``status.messages`` events, never ``status_str`` / ``completed``: earlier nodes may fail while later
    nodes finish normally, so any-error checks falsely reject outputs;
    interrupted executions also set ``completed`` true.

    Null ``status`` in some versions/proxies cannot classify: use outputs, treating any as completion;
    without outputs use execution-failure fallback, avoiding eventual output-missing errors that misdiagnose
    environment failures as wrong bindings.
    """
    status = entry.get("status")
    if not isinstance(status, Mapping):
        if _has_outputs(entry):
            return None
        return ComfyuiError(
            EXECUTION_ERROR,
            node=_UNKNOWN_NODE,
            detail="ComfyUI reported no execution status and no outputs",
        )
    event, data = _last_message(status)
    if event == _EXECUTION_ERROR_EVENT:
        return ComfyuiError(EXECUTION_ERROR, node=_error_node(data), detail=_error_detail(data))
    if event == _EXECUTION_INTERRUPTED_EVENT:
        return ComfyuiError(INTERRUPTED)
    return None


def history_digest(entry: Mapping[str, Any], output_nodes: Sequence[str]) -> dict[str, Any]:
    """Evidence history summaries retaining statuses/``output`` nodes only.

    Whole histories include every node's outputs and could inflate diagnostic columns to hundreds of KB;
    only these two sections matter for investigation.
    """
    outputs = entry.get("outputs")
    kept = (
        {node_id: outputs[node_id] for node_id in output_nodes if node_id in outputs}
        if isinstance(outputs, Mapping)
        else {}
    )
    return {"status": entry.get("status"), "outputs": kept}


def _last_message(status: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    """Final ``status.messages`` ``[事件名, 数据]`` event; empty names without messages."""
    messages = status.get("messages")
    if not isinstance(messages, list) or not messages:
        return "", {}
    last = messages[-1]
    if not isinstance(last, list) or not last:
        return "", {}
    data = last[1] if len(last) > 1 and isinstance(last[1], Mapping) else {}
    return str(last[0] or ""), data


def _error_node(data: Mapping[str, Any]) -> str:
    """User-facing failed-node names: ``node_type`` from canvases, otherwise node ids."""
    return str(data.get("node_type") or data.get("node_id") or _UNKNOWN_NODE)


def _error_detail(data: Mapping[str, Any]) -> str:
    """Exception summaries retain message/type only, not hundreds of traceback lines, since entire reasons persist."""
    message = str(data.get("exception_message") or "").strip()
    exception_type = str(data.get("exception_type") or "").strip()
    if message and exception_type:
        return f"{exception_type}: {message}"
    return message or exception_type or "node raised an exception"


def _has_outputs(entry: Mapping[str, Any]) -> bool:
    outputs = entry.get("outputs")
    return isinstance(outputs, Mapping) and bool(outputs)
