"""ComfyUI HTTP interfaces: upload assets, submit workflows, query history, retrieve artifacts.

Render endpoint ``auth`` once for all routes: native ComfyUI lacks authentication, needed only behind reverse
proxies sharing headers/query parameters. Per-route rendering would risk omission.

Only protocol shapes here, requests/raw responses, never terminal business interpretation: success/failure/queue
states require ``output``-bound nodes, belonging to backends. Raise codes only for broken protocols,
failed uploads/rejected submissions.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from arcreel_market_core.auth_section import render_auth
from arcreel_market_core.video_backend_contract import IMAGE_MIME_TYPES, ProviderResponseStage
from lib.backends.backend_runtime import (
    faststart_video_artifact,
    request_with_scoped_credentials,
    should_retry_submit,
    stream_to_file,
    submit_post,
    url_origin,
    with_artifact_retry,
)
from lib.backends.http_status_errors import redacted_status_error
from lib.custom_provider.comfyui.failures import NODE_ERRORS, UPLOAD_FAILED, ComfyuiError
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import retry_async

logger = logging.getLogger(__name__)

#: Uploads use this server ``input`` subdirectory, separate from user-uploaded images.
UPLOAD_SUBFOLDER = "arcreel"

#: Submission client prefixes identify callers in ComfyUI queues.
CLIENT_ID_PREFIX = "arcreel-"

#: ComfyUI ``type``: ``input`` is image-loader-referenceable input space.
_INPUT_TYPE = "input"

#: Evidence callbacks share declarative stage/raw shapes, connected by backends to task diagnostics.
RecordResponse = Callable[[ProviderResponseStage, object], Awaitable[None]]


def normalize_comfyui_base_url(base_url: str) -> str:
    """Add schemes/remove trailing slashes.

    Never strip versions: ComfyUI routes live at roots (``/prompt`` / ``/history``), unlike declarative
    ``base_url``/definition doubled versions such as ``/v1/v2``.

    Schemaless hosts receive ``https://`` because httpx rejects relative URLs and custom-provider ``base_url``
    is unconstrained strings.
    """
    stripped = base_url.strip().rstrip("/")
    if stripped and "://" not in stripped:
        stripped = f"https://{stripped}"
    return stripped


def media_reference(subfolder: str, name: str) -> str:
    """Combine response ``subfolder`` / ``name`` into image-loader references."""
    return f"{subfolder}/{name}" if subfolder else name


def upload_filename(task_id: str, key: str, path: Path, index: int | None = None) -> str:
    """Upload names: ``<task_id>-<key>[-<序号>].<ext>``.

    Task ids prevent concurrent collisions on one ComfyUI: ``overwrite=true`` overwrites by filenames,
    so identical first-frame names would replace each other's inputs.
    """
    suffix = path.suffix.lower() or ".png"
    serial = "" if index is None else f"-{index + 1}"
    return f"{task_id}-{key}{serial}{suffix}"


def _mime_of(path: Path) -> str:
    """Actual MIME by extension.

    ComfyUI validates multipart content types; some deployments reject
    ``application/octet-stream`` outright.
    """
    return IMAGE_MIME_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "image/png"


class ComfyuiClient:
    """Connect to ComfyUI through endpoint definitions/provider configuration."""

    def __init__(self, *, base_url: str, api_key: str, definition: Mapping[str, Any]) -> None:
        self._base_url = normalize_comfyui_base_url(base_url)
        self._headers, self._query = render_auth(definition.get("auth") or {}, api_key=api_key)

    @property
    def base_url(self) -> str:
        return self._base_url

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _authed_url(self, path: str) -> str:
        """Route addresses with query credentials.

        ``request_with_scoped_credentials`` requires caller-composed first-hop auth queries, restoring only same-origin
        redirects; never pass ``params``, which would replace composed queries. ``/view`` with its own
        parameters instead passes credentials/parameters together to first-hop ``stream_to_file`` ``params``.
        """
        url = httpx.URL(self._url(path))
        return str(url.copy_merge_params(self._query) if self._query else url)

    @property
    def _credential_origin(self) -> tuple[str, str, int | None]:
        """Credentials may target only this ComfyUI's own origin.

        Proxies commonly redirect ``/view`` to object storage/CDNs; httpx strips cross-origin
        ``Authorization`` only, while auth permits arbitrary headers. Automatic ``follow_redirects`` would leak
        ``X-API-Key`` to third parties.
        """
        return url_origin(self._base_url)

    async def upload_image(self, http: httpx.AsyncClient, path: Path, *, filename: str) -> str:
        """Upload into server ``input``, returning loader-readable references.

        Derive from **response** ``subfolder`` / ``name``, never requests: servers may rename collisions
        or normalize directories, making request-derived workflow references point to nonexistent files.
        """
        try:
            payload = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise ComfyuiError(UPLOAD_FAILED, detail=str(exc)) from exc
        try:
            response = await request_with_scoped_credentials(
                http,
                "POST",
                self._authed_url("/upload/image"),
                headers=self._headers,
                json=None,
                auth_query=self._query,
                files={"image": (filename, payload, _mime_of(path))},
                data={"subfolder": UPLOAD_SUBFOLDER, "overwrite": "true", "type": _INPUT_TYPE},
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            raise ComfyuiError(UPLOAD_FAILED, detail=str(redacted_status_error(exc))) from None
        except (httpx.HTTPError, ValueError) as exc:
            raise ComfyuiError(UPLOAD_FAILED, detail=str(exc)) from exc
        if not isinstance(body, Mapping) or not body.get("name"):
            raise ComfyuiError(UPLOAD_FAILED, detail="upload response did not name the stored file")
        return media_reference(str(body.get("subfolder") or ""), str(body["name"]))

    async def submit_prompt(
        self,
        http: httpx.AsyncClient,
        workflow: Mapping[str, Any],
        *,
        client_id: str,
        record: RecordResponse | None = None,
    ) -> str:
        """Submit actual workflows for server ``prompt_id``.

        ``node_errors`` on 200 fails like 400: ComfyUI accepts graphs but rejects particular node
        parameters through either shape. Accepting them would poll forever without artifacts.

        Optional ``record``: images have no evidence channel (``ImageGenerationRequest`` lacks diagnostic callbacks),
        and no-op callbacks would falsely imply one exists.
        """

        async def note(stage: ProviderResponseStage, body: object) -> None:
            if record is not None:
                await record(stage, body)

        async def post() -> httpx.Response:
            response = await request_with_scoped_credentials(
                http,
                "POST",
                self._authed_url("/prompt"),
                headers=self._headers,
                json={"prompt": dict(workflow), "client_id": client_id},
                auth_query=self._query,
            )
            if response.status_code >= 400:
                body = _body_or_text(response)
                await note("submit", body)
                if response.status_code == 400:
                    # ComfyUI 400 workflow rejection has the same cause as 200 node_errors;
                    # submit_post status routing would instead label generic upstream rejection.
                    raise _node_errors_error(body, workflow)
            return response

        response = await retry_async(lambda: submit_post(post, provider="comfyui"), retry_if=should_retry_submit)
        body = _body_or_text(response)
        await note("submit", body)
        if isinstance(body, Mapping) and body.get("node_errors"):
            raise _node_errors_error(body, workflow)
        prompt_id = str(body.get("prompt_id") or "").strip() if isinstance(body, Mapping) else ""
        if not prompt_id:
            raise ComfyuiError(NODE_ERRORS, nodes=0, summary="submit response did not contain a prompt_id")
        logger.info("ComfyUI 已提交: %s", format_kwargs_for_log({"prompt_id": prompt_id, "client_id": client_id}))
        return prompt_id

    async def fetch_history(self, http: httpx.AsyncClient, prompt_id: str) -> Mapping[str, Any] | None:
        """Fetch execution history, ``None`` before records exist.

        Accept ``{prompt_id: {...}}`` envelopes and direct root entries across versions/proxies;
        hardcoding either would leave other deployments polling forever.

        Unreadable 2xx bodies, non-JSON proxy pages/``{"error": ...}``, are not absorbed:
        like declarative channels, deterministic failure rather than continued polling.
        """
        response = await request_with_scoped_credentials(
            http,
            "GET",
            self._authed_url(f"/history/{prompt_id}"),
            headers=self._headers,
            json=None,
            auth_query=self._query,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise redacted_status_error(exc) from None
        body = response.json()
        if not isinstance(body, Mapping) or not body:
            return None
        wrapped = body.get(prompt_id)
        entry = wrapped if isinstance(wrapped, Mapping) else body
        return entry or None

    async def queue_snapshot(self, http: httpx.AsyncClient) -> tuple[list[str], list[str]] | None:
        """``GET /queue`` running/pending lists, extracting ``prompt_id`` in that order.

        Array entries ``[序号, prompt_id, prompt, 待执行节点, 额外数据]`` store ids at index 1; also accept
        proxy-rewritten ``{"prompt_id": ...}`` objects.

        Unreadable queues return ``None``, never empty lists: absence drives lost-task checks, so misread
        HTML would falsely kill active executions. Missing/nonarray lists also mean unreadable,
        including valid JSON 200 ``{"error": "restarting"}`` without queue content.
        ``None`` covers unreadable bodies only; HTTP failures propagate for caller-specific
        terminal-vs-unknown handling.
        """
        response = await request_with_scoped_credentials(
            http,
            "GET",
            self._authed_url("/queue"),
            headers=self._headers,
            json=None,
            auth_query=self._query,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise redacted_status_error(exc) from None
        try:
            body = response.json()
        except ValueError:
            return None
        if not isinstance(body, Mapping):
            return None
        running, pending = body.get("queue_running"), body.get("queue_pending")
        if not isinstance(running, list) or not isinstance(pending, list):
            return None
        return _queue_ids(running), _queue_ids(pending)

    async def server_version(self, http: httpx.AsyncClient) -> str | None:
        """``GET /system_stats`` ``system.comfyui_version``; ``None`` when unreadable.

        Same field as connectivity tests. Old versions/proxies omit it; absence is not failure,
        letting callers use legacy routes.
        """
        response = await request_with_scoped_credentials(
            http,
            "GET",
            self._authed_url("/system_stats"),
            headers=self._headers,
            json=None,
            auth_query=self._query,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Addresses include query credentials; raw HTTPStatusError messages would leak them into logs.
            raise redacted_status_error(exc) from None
        body = response.json()
        system = body.get("system") if isinstance(body, Mapping) else None
        version = system.get("comfyui_version") if isinstance(system, Mapping) else None
        # Nonstrings mean unreadable; callers use legacy routes, valid on new versions too.
        return version.strip() or None if isinstance(version, str) else None

    async def cancel_job(self, http: httpx.AsyncClient, prompt_id: str) -> None:
        """``POST /api/jobs/{id}/cancel`` handles queued/running execution together on new versions."""
        await self._post_and_raise(http, f"/api/jobs/{prompt_id}/cancel", body=None)

    async def drop_from_queue(self, http: httpx.AsyncClient, prompt_id: str) -> None:
        """``POST /queue {"delete": [id]}`` cancels queued execution on legacy versions."""
        await self._post_and_raise(http, "/queue", body={"delete": [prompt_id]})

    async def interrupt(self, http: httpx.AsyncClient) -> None:
        """``POST /interrupt`` stops **current execution**, ignoring ids, on legacy versions.

        Callers must confirm their own task is running, otherwise they stop someone else's work.
        """
        await self._post_and_raise(http, "/interrupt", body=None)

    async def _post_and_raise(self, http: httpx.AsyncClient, path: str, *, body: object | None) -> None:
        response = await request_with_scoped_credentials(
            http,
            "POST",
            self._authed_url(path),
            headers=self._headers,
            json=body,
            auth_query=self._query,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise redacted_status_error(exc) from None

    async def download_output(
        self,
        http: httpx.AsyncClient,
        artifact: Mapping[str, Any],
        output_path: Path,
        *,
        max_wait: float,
        max_bytes: int,
    ) -> None:
        """Retrieve artifact entries into ``output_path``, aborting bodies above ``max_bytes``.

        Same-origin ``/view`` shares credentials; ignore VHS ``fullpath``, server-local absolute
        paths readable only coincidentally with colocated deployments.
        """

        async def once() -> None:
            await stream_to_file(
                http,
                self._url("/view"),
                output_path,
                max_bytes=max_bytes,
                headers=self._headers,
                # Credentials/artifact parameters share first-hop params: ``stream_to_file`` ``params`` replaces entire
                # queries, erasing URL-composed values. Same-origin redirects restore through ``auth_query``.
                params={
                    **self._query,
                    "filename": str(artifact.get("filename") or ""),
                    "subfolder": str(artifact.get("subfolder") or ""),
                    "type": str(artifact.get("type") or "output"),
                },
                credential_origin=self._credential_origin,
                auth_query=self._query,
            )

        await with_artifact_retry(once, label="comfyui artifact download", max_wait=max_wait)
        # Remux outside download budgets so successful artifacts cannot become download timeouts through remux duration.
        await faststart_video_artifact(output_path)


def _queue_ids(entries: object) -> list[str]:
    if not isinstance(entries, list):
        return []
    found: list[str] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            candidate = entry.get("prompt_id")
        elif isinstance(entry, list) and len(entry) > 1:
            candidate = entry[1]
        else:
            continue
        if isinstance(candidate, str) and candidate:
            found.append(candidate)
    return found


def _body_or_text(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return response.text


def _node_errors_error(body: object, workflow: Mapping[str, Any]) -> ComfyuiError:
    """Encode rejected submissions: params retain node counts/first summaries, with full diagnostics in evidence columns.

    Summaries use ``<节点标题或 class_type>: <message>``: users see titles in ComfyUI,
    while node ids alone require manually counting graph nodes.
    """
    node_errors = body.get("node_errors") if isinstance(body, Mapping) else None
    if isinstance(node_errors, Mapping) and node_errors:
        node_id, first = next(iter(node_errors.items()))
        summary = _node_error_summary(str(node_id), first, workflow)
        return ComfyuiError(NODE_ERRORS, nodes=len(node_errors), summary=summary)
    return ComfyuiError(NODE_ERRORS, nodes=0, summary=_rejection_summary(body))


def _node_error_summary(node_id: str, detail: object, workflow: Mapping[str, Any]) -> str:
    """Prefer titles to class_type, matching user-named canvas nodes.

    Titles live in workflow ``_meta``, absent from ``node_errors``. When edited titles differ from reported class_type,
    users otherwise must count node ids to identify failures.
    """
    if not isinstance(detail, Mapping):
        return node_id
    node = workflow.get(node_id)
    meta = node.get("_meta") if isinstance(node, Mapping) else None
    title = str(meta.get("title") or "").strip() if isinstance(meta, Mapping) else ""
    label = title or str(detail.get("class_type") or "").strip() or node_id
    errors = detail.get("errors")
    message = ""
    if isinstance(errors, list) and errors and isinstance(errors[0], Mapping):
        message = str(errors[0].get("message") or "").strip()
    return f"{label}: {message}" if message else label


def _rejection_summary(body: object) -> str:
    """Message summaries without ``node_errors``, for whole-graph errors/proxy-generated 400."""
    error = body.get("error") if isinstance(body, Mapping) else None
    if isinstance(error, Mapping):
        return str(error.get("message") or error.get("type") or "").strip() or "prompt was refused"
    return "prompt was refused"


def client_id_for(job_label: str) -> str:
    """Submission client identifiers: ``arcreel-<任务标识>``.

    ComfyUI queues group by ``client_id``; prefixes distinguish ArcReel from manual executions.
    Shared by submission/preview, which must show actual shapes.
    """
    return f"{CLIENT_ID_PREFIX}{job_label}"
