"""Declarative JSON submit/poll channels: media-independent runtime engines with assembled video backends.

Lives in ``lib.custom_provider``, not ``lib.backends.video_backends``: inputs are endpoint definitions,
requiring templates/response extraction, while ``pyproject.toml``
``[tool.importlinter]`` forbids backend reverse dependencies on ``lib.custom_provider``. Like
``endpoints.py`` backend assembly, upper layers consume lower ones without lower knowledge of declarative definitions.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
from collections.abc import Awaitable, Callable, Collection, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.endpoint_definition import (
    AssetData,
    JsonPathEvaluationError,
    RenderedRequest,
    TemplateRenderError,
    build_context,
    definition_media_type,
    encode_inputs,
    extract_value,
    map_status,
    render_request,
)
from arcreel_market_core.validation_messages import ValidationMessage
from arcreel_market_core.video_backend_contract import (
    IMAGE_MIME_TYPES,
    ProviderJobStatus,
    ProviderResponseStage,
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.artifact_download_guard import VIDEO_ARTIFACT_MAX_BYTES, artifact_http_client
from lib.backends.backend_runtime import (
    ProviderJobIdPersistenceMixin,
    faststart_video_artifact,
    notify_provider_response_to,
    poll_with_retry,
    request_with_scoped_credentials,
    should_retry_poll,
    should_retry_submit,
    stream_to_file,
    submit_post,
    url_origin,
    with_artifact_retry,
)
from lib.backends.http_status_errors import redacted_status_error
from lib.backends.video_backends.metering import video_usage
from lib.billing.metering import Usage
from lib.config.url_utils import strip_base_url_version
from lib.custom_provider.path_overrides import NO_PATH_OVERRIDES, PathOverrides, raw_base_url
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import NonRetryableError, retry_async
from lib.infra.validation_messages import default_translate

_HTTP_TIMEOUT_SECONDS = 60
logger = logging.getLogger(__name__)


def _url_without_auth_query(rendered: RenderedRequest) -> str:
    """Log URLs stripped of plaintext ``auth.query`` credentials.

    ``format_kwargs_for_log`` masks by keys, missing credentials inside URL strings; rendered
    ``auth_query`` identifies parameters to strip before logging.
    """
    if not rendered.auth_query:
        return rendered.url
    head, separator, query = rendered.url.partition("?")
    if not separator:
        return rendered.url
    secrets = {quote(name, safe="") for name in rendered.auth_query}
    kept = [item for item in query.split("&") if item.partition("=")[0] not in secrets]
    return f"{head}?{'&'.join(kept)}" if kept else head


#: Strings above this length composed entirely of base64 become asset summaries; real prompts include spaces/punctuation.
_BASE64_LOG_THRESHOLD = 256
_BASE64_PATTERN = re.compile(r"[A-Za-z0-9+/=\r\n]+")


def _asset_payloads(context: Mapping[str, object]) -> frozenset[str]:
    """Asset values encoded in one rendering. Logs remove by source, never size heuristics: 1×1 PNGs encode
    below any threshold, otherwise leaking entire media."""
    inputs = context.get("inputs")
    if not isinstance(inputs, Mapping):
        return frozenset()
    payloads: set[str] = set()
    for value in inputs.values():
        candidates = value if isinstance(value, list) else [value]
        payloads.update(item for item in candidates if isinstance(item, str) and item)
    return frozenset(payloads)


def _body_for_log(value: object, assets: frozenset[str]) -> object:
    """Log bodies: ``encoding: "base64"`` yields bare strings unrecognized by generic
    formatters, unlike prefixed data URIs; truncation still exposes reconstructible media prefixes.

    Prefer source matching against same-render ``assets``, replacing entire occurrences regardless of size,
    without requiring whole-string equality. Definitions may embed ``"prefix{{ inputs.first_frame }}"` mixed text,
    neither equal nor pure-base64. Replace longest first, preventing shorter values consuming parts of longer ones.

    Shape fallback covers unmatched strings, such as postrender concatenation; real prompts include whitespace/punctuation,
    avoiding matches."""
    if isinstance(value, str):
        redacted = value
        for payload in sorted(assets, key=len, reverse=True):
            redacted = redacted.replace(payload, f"<asset:{len(payload)} chars>")
        if redacted != value:
            return redacted
        if len(value) >= _BASE64_LOG_THRESHOLD and _BASE64_PATTERN.fullmatch(value):
            return f"<base64:{len(value)} chars>"
        return value
    if isinstance(value, list):
        return [_body_for_log(item, assets) for item in value]
    if isinstance(value, dict):
        return {key: _body_for_log(item, assets) for key, item in value.items()}
    return value


def _headers_with_auth_masked(headers: Mapping[str, str], auth: Mapping[str, Any] | None) -> dict[str, str]:
    """Log headers: ``auth.headers`` permits arbitrary names such as ``Ocp-Apim-Subscription-Key``;
    generic ``format_kwargs_for_log`` sensitive-key matching misses them, so mask whole definition-declared names."""
    auth_names = {name.lower() for name in (auth or {}).get("headers", {})}
    return {name: "***" if name.lower() in auth_names else value for name, value in headers.items()}


#: Three URL-rendering sections.
REQUEST_SECTIONS = ("submit", "poll", "result")
#: URL templates directly beginning base_url + explicit versions.
_VERSIONED_BASE_URL = re.compile(r"^\s*\{\{\s*base_url\s*\}\}/v\d")


def request_urls(definition: Mapping[str, Any]) -> list[str]:
    """URL templates from three definition sections, skipping absent sections.

    base_url requirements/version-stripping consider whole definitions: hardcoded submission hosts with base_url-only polling
    are valid; submission-only inspection would disagree with actual URLs.
    """
    urls: list[str] = []
    for section in REQUEST_SECTIONS:
        node = definition.get(section)
        if isinstance(node, Mapping):
            urls.append(str(node.get("url", "")))
    return urls


def strips_base_url_version(definition: Mapping[str, Any]) -> bool:
    """Strip trailing configured versions if any definition URL begins with ``{{ base_url }}`` plus explicit versions."""
    return any(_VERSIONED_BASE_URL.match(url) for url in request_urls(definition))


def normalize_declarative_base_url(base_url: str, definition: Mapping[str, Any]) -> str:
    """Add schemes/remove trailing slashes; strip configured versions for explicit definition versions, avoiding ``/v1/v2``.

    Schemaless hosts such as ``api.aimlapi.com`` receive ``https://`` because httpx rejects relative
    URLs and legacy custom ``base_url`` is unconstrained. Preview/execution share normalization,
    preventing displayed/submitted URL drift.
    """
    stripped = base_url.strip().rstrip("/")
    if stripped and "://" not in stripped:
        stripped = f"https://{stripped}"
    if strips_base_url_version(definition):
        return strip_base_url_version(stripped)
    return stripped


#: Raw configured base_url variable for overridden sections.
OVERRIDE_BASE_URL = "override_base_url"


def apply_path_overrides(definition: Mapping[str, Any], path_overrides: PathOverrides) -> Mapping[str, Any]:
    """Replace overridden section URLs with raw base_url + override paths, preserving all else."""
    if not path_overrides:
        return definition
    rewritten = dict(definition)
    for section, path in path_overrides.items():
        node = definition.get(section)
        if isinstance(node, Mapping):
            rewritten[section] = {**node, "url": f"{{{{ {OVERRIDE_BASE_URL} }}}}{path}"}
    return rewritten


class DeclarativeRuntimeError(RuntimeError):
    """Declarative failures with persistent/localizable stable codes."""

    def __init__(self, code: str, *, detail: str | ValidationMessage) -> None:
        self.code = code
        self.params = {
            "detail": {"key": detail.key, "params": dict(detail.params)}
            if isinstance(detail, ValidationMessage)
            else detail
        }
        super().__init__(detail.render(default_translate) if isinstance(detail, ValidationMessage) else detail)


@dataclass(frozen=True)
class JobState:
    """Media-independent response interpretation: status/errors/secondary-retrieval ids."""

    body: object
    status: ProviderJobStatus
    #: Raw provider statuses; ``None`` for ``failure``-derived/caller-supplied tiers.
    provider_status: str | None
    error: str | None
    result_id: str | None


def _failure_reason(state: JobState) -> str:
    """User reasons for provider failure: extracted error messages first, otherwise raw provider statuses.

    Mapping folds ``failed`` / ``cancelled`` into ``failed``; generic provider-failure messages
    cannot distinguish rejection from cancellation.
    """
    if state.error:
        return state.error
    if state.provider_status:
        return f"provider reported failure (status: {state.provider_status})"
    return "provider reported failure"


@dataclass(frozen=True)
class ProviderState(JobState):
    """Video interpretations: :class:`JobState` plus artifact URLs/billed durations."""

    video_url: str | None
    duration_seconds: int | None


@contextmanager
def response_extract_guard() -> Generator[None]:
    """Normalize ``extract`` evaluation failures into stable codes shared by media interpreters."""
    try:
        yield
    except JsonPathEvaluationError as exc:
        raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=exc.message) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=str(exc)) from exc


def extract_job_status(
    body: object,
    extract: Mapping[str, Any],
    *,
    status_map: Mapping[str, str] | None = None,
    status: ProviderJobStatus | None = None,
) -> tuple[ProviderJobStatus, str | None]:
    """Read ``extract`` ``status`` / ``failure``; any ``failure`` match fails.

    Return status tiers/raw statuses; raw strings only when mappings derive tiers from them. Given ``status``
    skips status paths, since secondary retrieval executes only after successful polling.
    """
    failure = extract_value(extract["failure"], body) if "failure" in extract else None
    if status is not None:
        return (ProviderJobStatus.FAILED if failure is not None else status), None
    raw = extract_value(extract.get("status"), body)
    mapped = map_status(raw, status_map)
    if failure is not None:
        return ProviderJobStatus.FAILED, None
    return mapped, text_or_none(raw)


def extract_provider_state(
    body: object,
    extract: Mapping[str, Any],
    *,
    status_map: Mapping[str, str] | None = None,
    status: ProviderJobStatus | None = None,
) -> ProviderState:
    """Read response bodies through video ``extract`` sections; sole runtime/validation interpretation.

    Response validation can replace paid calls only by matching runtime decisions exactly. Duplicated
    ``extract``/``failure``/status fallbacks could invert conclusions precisely when users most
    trust validation.
    """
    with response_extract_guard():
        job_status, provider_status = extract_job_status(body, extract, status_map=status_map, status=status)
        return ProviderState(
            body=body,
            status=job_status,
            provider_status=provider_status,
            video_url=extract_text(extract.get("video_url"), body),
            error=extract_text(extract.get("error"), body),
            result_id=extract_text(extract.get("result_id"), body),
            duration_seconds=extract_duration((extract.get("usage") or {}).get("duration_seconds"), body),
        )


def text_or_none(value: object | None) -> str | None:
    """Normalize extracted text; blank/absent → ``None``."""
    if value is None:
        return None
    return str(value).strip() or None


def extract_text(spec: object | None, body: object) -> str | None:
    """Extract strings through one rule; absent/blank → ``None``."""
    if spec is None:
        return None
    return text_or_none(extract_value(spec, body))


def extract_duration(spec: object | None, body: object) -> int | None:
    """Billed ``usage.duration_seconds``; half-up rounding then out-of-range means unreported."""
    if spec is None:
        return None
    raw = extract_value(spec, body)
    try:
        value = int(Decimal(str(raw)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except JsonPathEvaluationError:
        raise
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if 0 < value <= MAX_BILLED_DURATION_SECONDS else None


class StateReader[StateT: JobState](Protocol):
    """Response readers for one ``extract`` section, matching :func:`extract_provider_state` signatures."""

    def __call__(
        self,
        body: object,
        extract: Mapping[str, Any],
        *,
        status_map: Mapping[str, str] | None = None,
        status: ProviderJobStatus | None = None,
    ) -> StateT: ...


#: Request origins ``(scheme, host, port)``, :func:`url_origin`.
Origin = tuple[str, str, int | None]
#: Template asset inputs: single/list/absent files.
AssetPaths = Path | list[Path] | None


@dataclass(frozen=True)
class JobCall:
    """Media-independent generation inputs: polling budgets/response evidence."""

    poll_timeout_seconds: float
    on_provider_response: Callable[[ProviderResponseStage, object], Awaitable[None]] | None
    #: Diagnostic log call identifiers when evidence persistence fails.
    label: str | None


@dataclass(frozen=True)
class JobOutcome[StateT: JobState]:
    """Readings after provider success.

    ``result_state`` is secondary-retrieval interpretation, ``None`` without ``result``; ``trusted_origins``
    holds credential-visited request origins, determining artifact-download credential inclusion.
    """

    poll_state: StateT
    result_state: StateT | None
    trusted_origins: frozenset[Origin]


class DeclarativeJobEngine[StateT: JobState]:
    """Media-independent declarative submission/polling/status mapping/secondary retrieval/downloads.

    Assemblers provide media variables/assets, response ``read_state`` artifact fields, size
    limits/results. All errors normalize to stable :class:`DeclarativeRuntimeError` codes.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        definition: Mapping[str, Any],
        provider: str,
        read_state: StateReader[StateT],
        log_label: str,
        path_overrides: PathOverrides = NO_PATH_OVERRIDES,
    ) -> None:
        self._api_key = api_key
        self._raw_base_url = raw_base_url(base_url)
        self._base_url = normalize_declarative_base_url(base_url, definition)
        self._source_definition = definition
        self._model = model
        # Override URLs use raw bases without normalization (docs/adr/0101); version stripping still follows
        # original definitions, preserving unoverridden sections.
        self._definition = apply_path_overrides(definition, path_overrides)
        self._provider = provider
        self._read_state = read_state
        self._log_label = log_label

    @property
    def base_url(self) -> str:
        """Configured raw hosts with schemes/trailing slashes normalized only, persisted on submission for resume replay."""
        return self._raw_base_url

    def request_context(
        self,
        variables: Mapping[str, object],
        assets: Mapping[str, AssetPaths],
        *,
        submitted_base_url: str | None = None,
        require_declared_inputs: bool = False,
    ) -> dict[str, object]:
        """Build template contexts; assemblers supply media-specific variables/assets beyond credentials/hosts/models.

        ``require_declared_inputs`` true only on submission. Resumed requests lack assets,
        and validators forbid poll/result inputs. Required-input checks on resume would reject every
        paid required-image endpoint before first polling.
        """
        declarations = self._definition.get("inputs") or {}
        try:
            # Asset reads belong inside guards: preparation-to-execution deletions raise OSError, otherwise bypassing
            # stable codes and persisting untranslated raw worker errors.
            loaded = {
                name: self._assets(paths) if isinstance(paths, list) else self._asset(paths)
                for name, paths in assets.items()
            }
            encoded = encode_inputs(declarations, loaded)
            # Missing required assets fail locally: templates remove whole-placeholder keys, otherwise sending incomplete
            # requests that providers still create/bill.
            missing = (
                [name for name, value in encoded.items() if declarations[name].get("required") and not value]
                if require_declared_inputs
                else []
            )
            if missing:
                raise DeclarativeRuntimeError(
                    "declarative_template_render_failed",
                    detail=f"required inputs are missing: {', '.join(sorted(missing))}",
                )
            return build_context(
                {
                    "api_key": self._api_key,
                    # Resume replays submission hosts, None during submission; hosts are connection, not protocol dimensions.
                    "base_url": (
                        normalize_declarative_base_url(submitted_base_url, self._source_definition)
                        if submitted_base_url
                        else self._base_url
                    ),
                    OVERRIDE_BASE_URL: raw_base_url(submitted_base_url) if submitted_base_url else self._raw_base_url,
                    "model": self._model,
                    **variables,
                },
                encoded,
                self._definition.get("defaults"),
                media_type=definition_media_type(self._definition),
            )
        except TemplateRenderError as exc:
            raise DeclarativeRuntimeError("declarative_template_render_failed", detail=exc.message) from exc
        except OSError as exc:
            raise DeclarativeRuntimeError("declarative_template_render_failed", detail=str(exc)) from exc

    @staticmethod
    def _asset(path: Path | None) -> AssetData | None:
        if path is None:
            return None
        mime = (
            IMAGE_MIME_TYPES.get(path.suffix.lower())
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream"
        )
        return AssetData(mime, path.read_bytes())

    @classmethod
    def _assets(cls, paths: list[Path] | None) -> list[AssetData] | None:
        return [asset for path in paths or [] if (asset := cls._asset(path)) is not None] or None

    def _render(self, section: Mapping[str, Any], context: Mapping[str, object]):
        try:
            return render_request(
                section,
                context,
                enum_maps=self._definition.get("enum_maps"),
                auth=self._definition.get("auth"),
            )
        except TemplateRenderError as exc:
            raise DeclarativeRuntimeError("declarative_template_render_failed", detail=exc.message) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise DeclarativeRuntimeError("declarative_template_render_failed", detail=str(exc)) from exc

    def _endpoint_origin(self, section: Mapping[str, Any], context: Mapping[str, object]) -> Origin:
        """Origins of rendered request addresses, actual credential destinations.

        Use rendered URLs, not response URLs after redirects, where cross-origin credentials already
        vanished. Trusting endpoints would send credentials to unverified third-party hosts.
        """
        return url_origin(self._render(section, context).url)

    async def _send(
        self,
        client: httpx.AsyncClient,
        section: Mapping[str, Any],
        context: Mapping[str, object],
    ) -> httpx.Response:
        response = await self._send_without_status(client, section, context)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise redacted_status_error(exc) from None
        return response

    async def submit(self, client: httpx.AsyncClient, context: Mapping[str, object], call: JobCall) -> str:
        """Submit requests and obtain provider task ids."""
        section = self._definition["submit"]

        async def operation() -> httpx.Response:
            async def post() -> httpx.Response:
                response = await self._send_without_status(client, section, context)
                if response.status_code >= 400:
                    await self._record_response(response, call, "submit")
                return response

            return await submit_post(
                post,
                provider=self._provider,
            )

        response = await retry_async(operation, retry_if=should_retry_submit)
        body = await self._response_body(response, call, "submit")
        try:
            job_id = extract_value(section["extract"]["task_id"], body)
            error = extract_text(section["extract"].get("error"), body)
        except JsonPathEvaluationError as exc:
            raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=exc.message) from exc
        except (TypeError, ValueError) as exc:
            raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=str(exc)) from exc
        # accept="scalar" can match numbers/bools, contractually stringified downstream;
        # str conversion matches artifact URLs/result_id semantics.
        task_id = str(job_id).strip() if job_id is not None and not isinstance(job_id, (list, dict)) else ""
        if not task_id:
            raise DeclarativeRuntimeError(
                "declarative_response_extract_failed",
                detail=error or "submit response did not contain a provider task id",
            )
        return task_id

    async def _send_without_status(
        self,
        client: httpx.AsyncClient,
        section: Mapping[str, Any],
        context: Mapping[str, object],
    ) -> httpx.Response:
        rendered = self._render(section, context)
        logger.info(
            "%s: %s",
            self._log_label,
            format_kwargs_for_log(
                {
                    "method": rendered.method,
                    "url": _url_without_auth_query(rendered),
                    "headers": _headers_with_auth_masked(rendered.headers, self._definition.get("auth")),
                    "body": _body_for_log(rendered.body, _asset_payloads(context)),
                }
            ),
        )
        # Submission/polling/secondary retrieval carry rendered auth; manually follow redirects, dropping cross-origin credentials.
        return await request_with_scoped_credentials(
            client,
            rendered.method,
            rendered.url,
            headers=rendered.headers,
            json=rendered.body if rendered.body is not None else None,
            auth_query=rendered.auth_query,
        )

    async def poll(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        call: JobCall,
        *,
        context: Mapping[str, object],
        is_resume: bool,
    ) -> JobOutcome[StateT]:
        """Poll until provider success; perform secondary retrieval when ``result`` exists.

        Provider failure raises terminal ``poll_with_retry`` errors; resume polling 404 raises
        :class:`ResumeExpiredError`. Assemblers validate artifact-field completeness.
        """
        poll_context = {**context, "task_id": job_id}
        # Definitions may target hosts distinct from base_url, often producing artifacts there. Trust only origins
        # actually credential-visited: submissions before polling, also vendor origins on resume;
        # polling/result origins enter after successful retrieval (fetch). All-absolute definitions may never request
        # configured base_url, so configuration-based trust could leak credentials to unverified origins.
        # Other object-storage/CDN requests carry none.
        trusted_origins = {
            url_origin(self._render({"method": "GET", "url": self._definition["submit"]["url"]}, context).url)
        }

        async def fetch(
            section: Mapping[str, Any],
            section_context: Mapping[str, object],
            *,
            stage: ProviderResponseStage,
            expire_on_404: bool,
        ) -> object:
            """Retrieve once, always recording failed responses.

            Polling 404 alone means remote task absence, because it addresses tasks themselves. result
            404 means unready artifacts; resume expiry would permanently discard successful paid tasks,
            so leave it to retrieval-budget retries.
            """
            try:
                response = await self._send(client, section, section_context)
            except httpx.HTTPStatusError as exc:
                await self._record_response(exc.response, call, stage)
                if expire_on_404 and is_resume and exc.response.status_code == 404:
                    raise ResumeExpiredError(job_id=job_id, provider=self._provider) from exc
                raise
            trusted_origins.add(self._endpoint_origin(section, section_context))
            return await self._response_body(response, call, stage)

        async def poll_once() -> StateT:
            return self._extract_state(
                # Default true: resume 404 immediately means absent tasks; definitions with transient 404 on active jobs
                # use false, retrying to budget exhaustion.
                await fetch(
                    self._definition["poll"],
                    poll_context,
                    stage="poll",
                    expire_on_404=bool(self._definition["poll"].get("expire_on_404", True)),
                ),
                self._definition["poll"]["extract"],
            )

        final = await poll_with_retry(
            poll_fn=poll_once,
            is_done=lambda state: state.status is ProviderJobStatus.SUCCEEDED,
            # Terminal failures require nonempty reasons; None would make poll_with_retry continue
            # until max_wait, despite already-failed provider jobs.
            is_failed=lambda state: _failure_reason(state) if state.status is ProviderJobStatus.FAILED else None,
            max_wait=call.poll_timeout_seconds,
            retry_if=should_retry_poll,
            label=self._provider,
        )
        if "result" not in self._definition:
            return JobOutcome(poll_state=final, result_state=None, trusted_origins=frozenset(trusted_origins))
        result_context = {**poll_context, "result_id": final.result_id}

        async def fetch_result() -> object:
            return await fetch(self._definition["result"], result_context, stage="result", expire_on_404=False)

        # Provider tasks already succeeded/billed: secondary retrieval/downloads share postcreation idempotent
        # retrieval budgets, preserving paid outputs through 429/5xx/temporary 404.
        # Exhaustion also uses artifact_download_failed: retry-download resume repeats polling/secondary
        # retrieval, identical to download recovery without resubmission.
        try:
            result_body = await with_artifact_retry(
                fetch_result,
                label=f"{self._provider} result",
                retry_if=should_retry_poll,
                max_wait=call.poll_timeout_seconds,
            )
        except (ResumeExpiredError, DeclarativeRuntimeError, NonRetryableError):
            raise
        except Exception as exc:
            raise DeclarativeRuntimeError("artifact_download_failed", detail=str(exc)) from exc
        result_state = self._extract_state(
            result_body,
            self._definition["result"]["extract"],
            status=ProviderJobStatus.SUCCEEDED,
        )
        return JobOutcome(poll_state=final, result_state=result_state, trusted_origins=frozenset(trusted_origins))

    def _extract_state(
        self,
        body: object,
        extract: Mapping[str, Any],
        *,
        status: ProviderJobStatus | None = None,
    ) -> StateT:
        return self._read_state(body, extract, status_map=self._definition.get("status_map"), status=status)

    @staticmethod
    async def _response_body(response: httpx.Response, call: JobCall, stage: ProviderResponseStage) -> object:
        try:
            body = response.json()
        except ValueError as exc:
            # Non-JSON 2xx, proxy HTML/truncated responses: record raw bodies before raising. Evidence left at previous
            # polling responses would mislead exactly when current failures need inspection.
            await _notify(call, stage, response.text)
            raise DeclarativeRuntimeError(
                "declarative_response_extract_failed", detail="provider response was not valid JSON"
            ) from exc
        await _notify(call, stage, body)
        return body

    @staticmethod
    async def _record_response(response: httpx.Response, call: JobCall, stage: ProviderResponseStage) -> None:
        """Record failed responses, retaining truncated raw non-JSON bodies without introducing additional task failures."""
        try:
            body: object = response.json()
        except ValueError:
            body = response.text
        await _notify(call, stage, body)

    async def download(
        self,
        client: httpx.AsyncClient,
        url: str,
        output_path: Path,
        context: Mapping[str, object],
        *,
        max_bytes: int,
        max_wait: float,
        trusted_origins: Collection[Origin],
    ) -> None:
        """Retrieve artifact URLs into ``output_path``, aborting bodies above ``max_bytes``."""
        # Only origins already visited with credentials render auth; other signed object-storage/CDN
        # URLs use bare requests, avoiding broken signatures/query credentials in third-party access logs.
        # Artifact origins scope credentials throughout redirects, dropping them on origin changes.
        credential_origin = url_origin(url)
        rendered = (
            self._render({"method": "GET", "url": url}, context) if credential_origin in trusted_origins else None
        )

        async def download_once() -> None:
            await stream_to_file(
                client,
                rendered.url if rendered is not None else url,
                output_path,
                max_bytes=max_bytes,
                headers=rendered.headers if rendered is not None else None,
                credential_origin=credential_origin if rendered is not None else None,
                auth_query=rendered.auth_query if rendered is not None else None,
            )

        try:
            await with_artifact_retry(
                download_once,
                label=f"{self._provider} artifact download",
                max_wait=max_wait,
            )
        except NonRetryableError:
            # Invalid destinations/oversized bodies cannot improve through retrieval, never classify retry-download.
            raise
        except Exception as exc:
            raise DeclarativeRuntimeError("artifact_download_failed", detail=str(exc)) from exc
        # Remux outside download budgets, preventing successful downloads timing out due to remux duration.
        await faststart_video_artifact(output_path)


async def _notify(call: JobCall, stage: ProviderResponseStage, body: object) -> None:
    await notify_provider_response_to(call.on_provider_response, stage, body, label=call.label)


class DeclarativeVideoBackend(ProviderJobIdPersistenceMixin):
    """Declarative video channels assembling request/artifact semantics over :class:`DeclarativeJobEngine`."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        definition: Mapping[str, Any],
        provider: str,
        path_overrides: PathOverrides = NO_PATH_OVERRIDES,
    ) -> None:
        self._engine = DeclarativeJobEngine(
            api_key=api_key,
            base_url=base_url,
            model=model,
            definition=definition,
            provider=provider,
            read_state=extract_provider_state,
            log_label="声明式视频请求",
            path_overrides=path_overrides,
        )
        self._model = model
        self._definition = definition
        self._provider = provider

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def video_capabilities(self) -> VideoCapabilities:
        # Deferred imports avoid capability composition → endpoints.py → this-module cycles.
        from lib.custom_provider.capabilities import video_capabilities_from_definition

        return video_capabilities_from_definition(self._definition)

    def metered_usage(
        self,
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None = None,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage:
        """Request metering through :func:`declarative_video_usage`, definition-driven independently of models/entries."""
        del model, entry
        return declarative_video_usage(self._definition, request, result)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        context = await asyncio.to_thread(self._request_context, request, require_declared_inputs=True)
        async with artifact_http_client(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            job_id = await self._engine.submit(client, context, _job_call(request))
            # Persist submission hosts for resume: changed provider base_url would query old jobs on new hosts,
            # falsely expire/discard paid tasks.
            await self._persist_provider_job_id(
                request, job_id, provider=self._provider, endpoint=self._engine.base_url
            )
            return await self._poll_download(client, job_id, request, context=context, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        context = await asyncio.to_thread(self._request_context, request)
        async with artifact_http_client(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            return await self._poll_download(client, job_id, request, context=context, is_resume=True)

    def _request_context(
        self, request: VideoGenerationRequest, *, require_declared_inputs: bool = False
    ) -> dict[str, object]:
        return self._engine.request_context(
            {
                "prompt": request.prompt,
                "duration": request.duration_seconds,
                "duration_seconds": request.duration_seconds,
                "aspect_ratio": request.aspect_ratio,
                "resolution": request.resolution,
                "generate_audio": request.generate_audio,
                "seed": request.seed,
            },
            {
                "start_image": request.start_image,
                "end_image": request.end_image,
                "reference_images": request.reference_images,
                "reference_audio_files": request.reference_audio_files,
            },
            submitted_base_url=request.submitted_base_url,
            require_declared_inputs=require_declared_inputs,
        )

    async def _poll_download(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        request: VideoGenerationRequest,
        *,
        context: Mapping[str, object],
        is_resume: bool,
    ) -> VideoGenerationResult:
        outcome = await self._engine.poll(client, job_id, _job_call(request), context=context, is_resume=is_resume)
        final = outcome.poll_state
        video_url = final.video_url
        duration = final.duration_seconds
        if outcome.result_state is not None:
            video_url = outcome.result_state.video_url
            duration = outcome.result_state.duration_seconds or duration
            final = outcome.result_state
        if not video_url:
            raise DeclarativeRuntimeError(
                "declarative_response_extract_failed",
                detail=final.error or "provider reported success but no video URL matched the definition",
            )
        await self._engine.download(
            client,
            video_url,
            request.output_path,
            context,
            max_bytes=VIDEO_ARTIFACT_MAX_BYTES,
            max_wait=request.poll_timeout_seconds,
            trusted_origins=outcome.trusted_origins,
        )
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=self._provider,
            model=self._model,
            duration_seconds=duration or request.duration_seconds,
            video_uri=video_url,
            task_id=job_id,
            generate_audio=request.generate_audio,
        )


def declarative_video_usage(
    definition: Mapping[str, Any], request: VideoGenerationRequest, result: VideoGenerationResult | None = None
) -> Usage:
    """Declarative video request metering (:mod:`lib.backends.video_backends.metering`).

    Resolution follows requests, otherwise ``defaults.resolution``, matching context fallback. Audio follows declared forms,
    using request values when toggleable.
    """
    # Same deferred-import rationale as DeclarativeVideoBackend.video_capabilities.
    from lib.custom_provider.capabilities import video_capabilities_from_definition

    defaults = definition.get("defaults")
    default_resolution = defaults.get("resolution") if isinstance(defaults, Mapping) else None
    resolution = request.resolution or (default_resolution if isinstance(default_resolution, str) else None)
    audio_track = video_capabilities_from_definition(definition).audio_track
    audio = {VideoAudioMode.ALWAYS_OFF: False, VideoAudioMode.ALWAYS_ON: True}.get(audio_track, request.generate_audio)
    return video_usage(request, result, seconds=request.duration_seconds, resolution=resolution, audio=audio)


def _job_call(request: VideoGenerationRequest) -> JobCall:
    return JobCall(
        poll_timeout_seconds=request.poll_timeout_seconds,
        on_provider_response=request.on_provider_response,
        label=request.task_id,
    )
