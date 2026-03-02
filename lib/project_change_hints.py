"""
Lightweight project change hint bus used by the workspace realtime layer.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock
from typing import Any, Callable, Iterable, Literal

logger = logging.getLogger(__name__)

ProjectChangeSource = Literal["webui", "worker", "filesystem"]
ProjectChangeListener = Callable[[str, ProjectChangeSource, tuple[str, ...]], None]
ProjectChangeBatch = dict[str, Any]
ProjectChangeBatchListener = Callable[
    [str, ProjectChangeSource, tuple[ProjectChangeBatch, ...]],
    None,
]

_current_source: ContextVar[ProjectChangeSource] = ContextVar(
    "project_change_source",
    default="filesystem",
)
_listeners: list[ProjectChangeListener] = []
_batch_listeners: list[ProjectChangeBatchListener] = []
_listeners_lock = RLock()


def get_project_change_source() -> ProjectChangeSource:
    """Return the current source label for project mutations."""
    return _current_source.get()


@contextmanager
def project_change_source(source: ProjectChangeSource):
    """Temporarily tag project mutations with a source label."""
    token = _current_source.set(source)
    try:
        yield
    finally:
        _current_source.reset(token)


def emit_project_change_hint(
    project_name: str,
    source: ProjectChangeSource | None = None,
    changed_paths: Iterable[str] | None = None,
) -> None:
    """Notify listeners that project files were just written."""
    resolved_source = source or get_project_change_source()
    paths = tuple(dict.fromkeys(str(path) for path in (changed_paths or ())))
    with _listeners_lock:
        listeners = list(_listeners)

    for listener in listeners:
        try:
            listener(project_name, resolved_source, paths)
        except Exception:
            logger.exception("项目变更 hint listener 执行失败")


def register_project_change_listener(
    listener: ProjectChangeListener,
) -> Callable[[], None]:
    """Register a listener. Returns an unregister callback."""
    with _listeners_lock:
        _listeners.append(listener)

    def unregister() -> None:
        with _listeners_lock:
            try:
                _listeners.remove(listener)
            except ValueError:
                return

    return unregister


def emit_project_change_batch(
    project_name: str,
    changes: Iterable[ProjectChangeBatch],
    source: ProjectChangeSource | None = None,
) -> None:
    """Notify listeners with a ready-to-broadcast project change batch."""
    resolved_source = source or get_project_change_source()
    payload = tuple(dict(change) for change in changes if isinstance(change, dict))
    if not payload:
        return

    with _listeners_lock:
        listeners = list(_batch_listeners)

    for listener in listeners:
        try:
            listener(project_name, resolved_source, payload)
        except Exception:
            logger.exception("项目变更 batch listener 执行失败")


def register_project_change_batch_listener(
    listener: ProjectChangeBatchListener,
) -> Callable[[], None]:
    """Register a batch listener. Returns an unregister callback."""
    with _listeners_lock:
        _batch_listeners.append(listener)

    def unregister() -> None:
        with _listeners_lock:
            try:
                _batch_listeners.remove(listener)
            except ValueError:
                return

    return unregister
