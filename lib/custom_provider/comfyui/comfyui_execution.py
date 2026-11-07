"""Submitted ComfyUI execution: poll terminals, classify outcomes, retrieve/persist, stopping remotes on cancellation/timeouts.

After obtaining ``prompt_id``, images/videos share four questions: is execution complete, did it succeed, which artifact,
and how to stop unwanted remotes. All are media-independent except extension allowlists
in :mod:`.artifacts`. Duplicated backends would eventually diverge
in unusual lost-task/stop branches.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from lib.backends.artifact_download_guard import ARTIFACT_MAX_BYTES_BY_MEDIA_TYPE, artifact_http_client
from lib.backends.backend_runtime import poll_with_retry, should_retry_poll
from lib.backends.container_sniff import CONTAINER_HEAD_BYTES
from lib.custom_provider.comfyui.artifacts import (
    container_matches,
    filename_of,
    history_digest,
    output_artifacts,
    output_nodes_of,
    pick_artifact,
    terminal_failure,
)
from lib.custom_provider.comfyui.comfyui_client import ComfyuiClient, RecordResponse
from lib.custom_provider.comfyui.failures import (
    JOB_LOST,
    OUTPUT_CONTAINER_MISMATCH,
    OUTPUT_MISSING,
    OUTPUT_TYPE_MISMATCH,
    ComfyuiError,
)

logger = logging.getLogger(__name__)

#: Per-HTTP-call generation timeout.
HTTP_TIMEOUT_SECONDS = 60

#: Remote-stop timeout, much shorter: after cancellation, unresponsive services
#: must not delay worker shutdown by minutes.
_STOP_TIMEOUT_SECONDS = 15

#: From this version, ``POST /api/jobs/{id}/cancel`` handles queued/running tasks together;
#: older versions separate queue deletion and active interruption.
_JOB_CANCEL_MIN_VERSION = (0, 26, 0)


@dataclass(frozen=True)
class PickedArtifact:
    """Selected artifacts plus available counts.

    Callers interpret ``count``: videos add task ``result.warnings``, images log only because
    ``ImageGenerationResult`` lacks warning slots; each channel decides wording.
    """

    artifact: Mapping[str, Any]
    count: int

    @property
    def filename(self) -> str:
        return filename_of(self.artifact)


class ComfyuiExecution:
    """Postsubmission generation around one ``prompt_id`` on one ComfyUI service.

    Construction records connections/definitions without requests. Backends own generation HTTP clients for uploads/submission,
    passed into retrieval methods. Stopping instead creates separate short-timeout clients.
    """

    def __init__(self, *, client: ComfyuiClient, definition: Mapping[str, Any], media_type: str) -> None:
        self._client = client
        self._media_type = media_type
        self._output_nodes = output_nodes_of(definition)

    async def fetch_artifact(
        self,
        http: httpx.AsyncClient,
        prompt_id: str,
        *,
        output_path: Path,
        poll_timeout_seconds: float,
        record: RecordResponse | None = None,
        lost_error: Callable[[str], BaseException] | None = None,
    ) -> PickedArtifact:
        """Poll terminals, select/download into ``output_path``; stop remotes if local execution is abandoned.

        ``lost_error`` replaces missing-execution exceptions: generation uses
        ``comfyui_job_lost`` for locally retryable generation, resume requires ``ResumeExpiredError``
        so workers settle pending calls instead of retrying generation.

        Stop handling wraps polling/retrieval only: before submission no ``prompt_id`` exists; ``submit_post``
        handles ambiguous submission. User GPUs remain occupied by abandoned ComfyUI execution otherwise.
        """
        try:
            entry = await self._poll_to_terminal(
                http, prompt_id, poll_timeout_seconds=poll_timeout_seconds, record=record, lost_error=lost_error
            )
            picked = self._pick(entry)
            if record is not None:
                await record("result", {"artifact": dict(picked.artifact), "count": picked.count})
            await self._client.download_output(
                http,
                picked.artifact,
                output_path,
                max_wait=poll_timeout_seconds,
                max_bytes=ARTIFACT_MAX_BYTES_BY_MEDIA_TYPE[self._media_type],
            )
            await self._verify_container(output_path, picked)
            return picked
        except (asyncio.CancelledError, TimeoutError):
            await self.stop_remote(prompt_id)
            raise

    def view_url(self, artifact: Mapping[str, Any]) -> str:
        """Artifact retrieval addresses on this ComfyUI, retained as version provenance."""
        query = urlencode(
            {
                "filename": filename_of(artifact),
                "subfolder": str(artifact.get("subfolder") or ""),
                "type": str(artifact.get("type") or "output"),
            }
        )
        return f"{self._client.base_url}/view?{query}"

    # Polling/retrieval.

    async def _poll_to_terminal(
        self,
        http: httpx.AsyncClient,
        prompt_id: str,
        *,
        poll_timeout_seconds: float,
        record: RecordResponse | None,
        lost_error: Callable[[str], BaseException] | None,
    ) -> Mapping[str, Any]:
        """Poll until history contains execution, interpreting outcomes and raising failure codes."""

        # Single-element lists wrap execution records: ``poll_with_retry`` uses is_done, and record presence
        # itself means terminal; empty lists mean not yet executed.
        async def poll_once() -> list[Mapping[str, Any]]:
            entry = await self._client.fetch_history(http, prompt_id)
            if entry is None:
                entry = await self._history_or_lost(http, prompt_id, lost_error=lost_error)
            if entry is None:
                return []
            if record is not None:
                await record("poll", history_digest(entry, self._output_nodes))
            return [entry]

        found = await poll_with_retry(
            poll_fn=poll_once,
            # History records imply terminal states: ComfyUI writes only after success/failure/interruption.
            is_done=bool,
            is_failed=lambda _found: None,
            max_wait=poll_timeout_seconds,
            retry_if=should_retry_poll,
            label="comfyui",
        )
        entry = found[0]
        failure = terminal_failure(entry)
        if failure is not None:
            raise failure
        return entry

    def _pick(self, entry: Mapping[str, Any]) -> PickedArtifact:
        """Select media-compatible artifacts from successful terminal histories."""
        artifacts = output_artifacts(entry, self._output_nodes)
        if not artifacts:
            raise ComfyuiError(OUTPUT_MISSING, nodes=" / ".join(self._output_nodes))
        artifact = pick_artifact(artifacts, self._media_type)
        if artifact is None:
            raise ComfyuiError(OUTPUT_TYPE_MISMATCH, filename=filename_of(artifacts[0]), media_type=self._media_type)
        if len(artifacts) > 1:
            logger.warning("ComfyUI 产物共 %d 个，取: %s", len(artifacts), filename_of(artifact))
        return PickedArtifact(artifact=artifact, count=len(artifacts))

    async def _verify_container(self, output_path: Path, picked: PickedArtifact) -> None:
        """Verify persisted containers; delete mismatches and report container errors.

        History extension checks alone permit webm bytes named ``.mp4``, while persistence resource paths/served MIME
        follow extensions. Remove files instead of leaving artifact paths:
        failed calls otherwise leave unreadable files misclassified as already-existing versions.
        """
        head = await asyncio.to_thread(_read_head, output_path)
        if container_matches(head, self._media_type):
            return
        await asyncio.to_thread(
            output_path.unlink, True
        )  # hard-delete: temp -- newly downloaded container-mismatched output
        raise ComfyuiError(OUTPUT_CONTAINER_MISMATCH, filename=picked.filename, media_type=self._media_type)

    async def _history_or_lost(
        self, http: httpx.AsyncClient, prompt_id: str, *, lost_error: Callable[[str], BaseException] | None
    ) -> Mapping[str, Any] | None:
        """With empty history, verify execution remains queued, otherwise classify lost.

        ComfyUI restart loses queues/in-flight execution before history writes; clients otherwise see
        perpetually empty history until global timeout.

        Queue/history are independent queries: completion between them may leave execution out of queues yet absent
        from first history reads. Requery history after queue absence, accepting matches normally and distinguishing
        races from genuine loss.
        """
        snapshot = await self._queue_snapshot(http)
        if snapshot is None:
            return None
        running, pending = snapshot
        if prompt_id in running or prompt_id in pending:
            return None
        entry = await self._client.fetch_history(http, prompt_id)
        if entry is not None:
            return entry
        if lost_error is not None:
            raise lost_error(prompt_id)
        raise ComfyuiError(JOB_LOST, prompt_id=prompt_id)

    async def _queue_snapshot(self, http: httpx.AsyncClient) -> tuple[list[str], list[str]] | None:
        """Queue snapshots for loss checks; ``None`` on unreadable queues continues polling without spending failure budgets.

        ``/queue`` merely assists loss detection; task addresses are ``/history``, already healthy this round.
        Forwarding auxiliary ``/queue`` HTTP failures into ``poll_with_retry`` would let proxies blocking
        only ``/queue`` fail still-generating tasks after ten empty rounds. Unknown
        is safer than false terminal failure here.
        """
        try:
            return await self._client.queue_snapshot(http)
        except httpx.HTTPError:
            logger.info("ComfyUI 队列读取失败，本轮不判丢失", exc_info=True)
            return None

    # Stop remotes.

    async def stop_remote(self, prompt_id: str) -> None:
        """Best-effort remote stops: failures log only, preserving local state machines.

        Separate short-timeout clients instead of generation clients: after cancellations/timeouts,
        unresponsive ComfyUI services must not prolong worker shutdown by generation-sized timeouts. These clients also use
        :func:`~lib.backends.artifact_download_guard.artifact_http_client` destination validation;
        rejected destinations log like other failures.

        Query current ``/system_stats`` versions, not construction caches: servers may upgrade between calls,
        and this decision matters only at cancellation. Unreadable versions use legacy routes.
        """
        try:
            async with artifact_http_client(timeout=_STOP_TIMEOUT_SECONDS) as http:
                if _supports_job_cancel(await self._server_version(http)):
                    await self._client.cancel_job(http, prompt_id)
                    return
                snapshot = await self._client.queue_snapshot(http)
                if snapshot is None:
                    return
                running, pending = snapshot
                if prompt_id in pending:
                    await self._client.drop_from_queue(http, prompt_id)
                elif prompt_id in running:
                    # ``/interrupt`` targets current execution, ignoring ids. Sending when running excludes
                    # this task would stop someone else's work.
                    await self._client.interrupt(http)
        except Exception:
            logger.warning("ComfyUI 远端叫停失败 prompt_id=%s", prompt_id, exc_info=True)

    async def _server_version(self, http: httpx.AsyncClient) -> str | None:
        try:
            return await self._client.server_version(http)
        except Exception:
            # Unreadable versions are not failures: old services/proxies omit them; legacy routes still stop execution.
            logger.info("ComfyUI 版本读取失败，按低版本路径叫停", exc_info=True)
            return None


def _read_head(path: Path) -> bytes:
    """Leading persisted artifact bytes, sufficient for container checks."""
    with open(path, "rb") as handle:
        return handle.read(CONTAINER_HEAD_BYTES)


def _supports_job_cancel(version: str | None) -> bool:
    """Whether ComfyUI supports ``POST /api/jobs/{id}/cancel``.

    Unreadable/non-``x.y.z`` versions mean unsupported: old queue+delete/interrupt routes work on newer versions too,
    costing two extra requests if misclassified; guessing new support wrongly yields 404 without stopping anything.
    """
    if not version:
        return False
    matched = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", version.strip())
    if matched is None:
        return False
    major, minor, patch = matched.groups()
    return (int(major), int(minor), int(patch or 0)) >= _JOB_CANCEL_MIN_VERSION
