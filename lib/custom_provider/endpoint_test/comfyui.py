"""Two ComfyUI endpoint-test modes: request previews and connection tests.

Preview actual ``/prompt`` POST bodies using runtime :func:`build_workflow`,
``auth`` rendering, and ``client_id`` shapes. Only three semantic-preserving substitutions: masked credentials into
``auth``, asset size summaries because server references exist only after uploads, and placeholder ``prompt_id``.
These correspond one-to-one with declarative substitutions.

Also explain computed dimensions/frames/seeds/deleted nodes: dozens of JSON nodes alone cannot explain
how selected 720p becomes actual pixels.

No response validation here: ComfyUI reads three fixed keys from ``output``-bound nodes without configurable
paths (``docs/adr/0081``); :mod:`.modes` already rejects that mode.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from random import Random
from secrets import token_hex
from typing import Any
from urllib.parse import urlencode

from arcreel_market_core.auth_section import declares_credentials, render_auth
from lib.custom_provider.comfyui.comfyui_backend import ComfyuiVideoBackend
from lib.custom_provider.comfyui.comfyui_client import client_id_for, normalize_comfyui_base_url
from lib.custom_provider.comfyui.comfyui_image_backend import ComfyuiImageBackend
from lib.custom_provider.comfyui.request_builder import BuiltWorkflow, MediaInputs, build_workflow
from lib.custom_provider.path_overrides import NO_PATH_OVERRIDES, PathOverrides

from .inputs import EndpointTestAssets, EndpointTestCredentials, EndpointTestParameters
from .preview import (
    UNRESOLVED_API_KEY,
    UNRESOLVED_BASE_URL,
    PreviewedRequest,
    RequestPreview,
    masked_api_key,
    restore_mask_in_url,
)
from .trial_run import TrialRunTarget, provider_from_base_url

#: Postsubmission-only values remain placeholders, matching declarative ``{{ task_id }}``.
UNRESOLVED_PROMPT_ID = "{{ prompt_id }}"

#: Connection-test job-name prefixes in ComfyUI, followed by random suffixes.
_TRIAL_RUN_LABEL_PREFIX = "endpoint-test-"


@dataclass(frozen=True)
class ComfyuiConversions:
    """Preview conversions explaining workflow values from generation requests.

    ``width`` / ``height`` / ``frames`` / ``seed`` = ``None`` means dimensions do not drive workflows,
    unbound or fixed with only one size dimension bound. Preserve literals, exposing that
    project resolution choices have no effect on these endpoints.
    """

    #: Actual workflow fingerprints, identical to version metadata, verifying source graphs per version.
    workflow_sha256: str
    #: Conversion inputs: request ratio/resolution tier/duration.
    aspect_ratio: str
    resolution: str | None
    duration_seconds: float | None
    #: Conversion outputs: workflow pixels/frames/seeds.
    width: int | None
    height: int | None
    frames: int | None
    seed: int | None
    #: Exclusion text split from bodies into negative nodes.
    negative_prompt: str
    #: Deleted nodes for this reference-count edit; empty tuples mean no deletions.
    dropped_nodes: tuple[str, ...]

    @classmethod
    def of(cls, built: BuiltWorkflow, parameters: EndpointTestParameters) -> ComfyuiConversions:
        return cls(
            workflow_sha256=built.workflow_sha256,
            aspect_ratio=parameters.aspect_ratio,
            resolution=parameters.resolution,
            duration_seconds=parameters.duration_seconds,
            width=built.width,
            height=built.height,
            frames=built.frames,
            seed=built.seed,
            negative_prompt=built.negative_prompt,
            dropped_nodes=built.dropped_nodes,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "workflow_sha256": self.workflow_sha256,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "duration_seconds": self.duration_seconds,
            "width": self.width,
            "height": self.height,
            "frames": self.frames,
            "seed": self.seed,
            "negative_prompt": self.negative_prompt,
            "dropped_nodes": list(self.dropped_nodes),
        }


def preview_comfyui_request(
    definition: Mapping[str, Any],
    parameters: EndpointTestParameters,
    *,
    credentials: EndpointTestCredentials | None = None,
    assets: EndpointTestAssets | None = None,
    placeholder_missing_assets: bool = True,
    path_overrides: PathOverrides = NO_PATH_OVERRIDES,
    rng: Random | None = None,
) -> RequestPreview:
    """Render ``POST /prompt`` bodies/conversions from shared-validator-approved definitions.

    Include polling ``GET /history/{prompt_id}`` because it shares submission credentials; reverse-proxied ComfyUI
    users need to verify identical headers across every route. Omit artifact download:
    ``/view`` query parameters require returned artifacts; fabricated filenames are not previews.

    ComfyUI lacks path overrides (``docs/adr/0101``), so ``path_overrides`` always empty.

    ``rng`` injects random seeds through the same :func:`build_workflow` seam; ``policy: random`` produces new seeds
    on each preview, matching actual submissions.
    """
    api_key = masked_api_key(credentials.api_key) if credentials else UNRESOLVED_API_KEY
    base_url = normalize_comfyui_base_url(credentials.base_url) if credentials and credentials.base_url else ""
    headers, query = render_auth(definition.get("auth") or {}, api_key=api_key)
    built = build_workflow(
        definition,
        prompt=parameters.prompt,
        aspect_ratio=parameters.aspect_ratio,
        resolution=parameters.resolution,
        # Pass duration unchanged like backends: unbound ``frames``/unreadable fps makes construction skip conversion.
        duration_seconds=parameters.duration_seconds,
        media=_preview_media(definition, assets, placeholder_missing=placeholder_missing_assets),
        rng=rng,
    )
    return RequestPreview(
        submit=PreviewedRequest(
            method="POST",
            url=_preview_url(base_url, "/prompt", query),
            headers=dict(headers),
            # ``client_id`` construction matches actual requests; previews must show the same shape users recognize
            # as ArcReel jobs in ComfyUI queues.
            body={"prompt": built.workflow, "client_id": client_id_for(UNRESOLVED_PROMPT_ID)},
        ),
        poll=PreviewedRequest(
            method="GET",
            url=_preview_url(base_url, f"/history/{UNRESOLVED_PROMPT_ID}", query),
            headers=dict(headers),
            body=None,
        ),
        result=None,
        conversions=ComfyuiConversions.of(built, parameters).to_payload(),
    )


def comfyui_credential_needs(definition: Mapping[str, Any]) -> tuple[bool, bool]:
    """Whether definitions need ``base_url`` / ``api_key``.

    ``base_url`` always required: all routes live under service roots, without absolute definition URLs. ``api_key``
    only when ``auth`` actually sends credentials; native ComfyUI has none, unlike reverse-proxy deployments.
    Nonempty means entries in either table (:func:`declares_credentials`), not the ``auth`` object
    itself. Valid ``{"headers": {}}`` renders no headers; requiring api_key would exclude unauthenticated
    ComfyUI from previews/connection tests.
    """
    return True, declares_credentials(definition.get("auth") or {})


def comfyui_target(
    definition: Mapping[str, Any],
    credentials: EndpointTestCredentials,
    parameters: EndpointTestParameters,
    *,
    provider: str | None = None,
) -> TrialRunTarget:
    """Inline-definition targets: construct media-specific backends through production paths.

    Image/video endpoints share submission/polling/retrieval/stopping execution and differ only in request/result types;
    select classes by ``media_type``, without separate image test paths.

    Skip capability gates (:attr:`TrialRunTarget.gate_capabilities`): ComfyUI costs fixed zero and derives capabilities
    from bindings, while inline-definition entries cannot access those derivations.

    Omit rendered submission requests in results: actual random seeds/asset names resolve only at submission,
    so previews would falsely imply actual submitted values. Inspect request shapes in preview cards.
    """
    label = provider or provider_from_base_url(credentials.base_url)
    media_type = str(definition.get("media_type") or "video")
    # ComfyUI queue names: ``client_id`` = ``arcreel-<job_label>``, with uploads named accordingly.
    # Connection tests lack workers/task_id; backend random hex would leave users unable to identify newly tested jobs
    # in manual queues. Keep random suffixes because repeated tests on one endpoint
    # otherwise collide and overwrite uploaded assets.
    job_label = f"{_TRIAL_RUN_LABEL_PREFIX}{token_hex(4)}"

    async def build() -> ComfyuiVideoBackend | ComfyuiImageBackend:
        backend_class = ComfyuiImageBackend if media_type == "image" else ComfyuiVideoBackend
        return backend_class(
            provider_id=label,
            model=parameters.model,
            base_url=credentials.base_url,
            api_key=credentials.api_key,
            definition=definition,
            job_label=job_label,
        )

    return TrialRunTarget(
        provider=label,
        model=parameters.model,
        build_backend=build,
        definition=definition,
        media_type=media_type,
        gate_capabilities=False,
    )


def _preview_url(base_url: str, path: str, query: Mapping[str, str]) -> str:
    """Route preview addresses; absent credentials leave host placeholders, preserving request shapes."""
    url = f"{base_url or UNRESOLVED_BASE_URL}{path}"
    return restore_mask_in_url(f"{url}?{urlencode(query)}") if query else url


def _preview_media(
    definition: Mapping[str, Any],
    assets: EndpointTestAssets | None,
    *,
    placeholder_missing: bool,
) -> MediaInputs:
    """Replace assets with summaries in loader nodes.

    True ``placeholder_missing`` for standalone previews generates summaries for bound-but-unuploaded slots:
    emptiness would trigger graph edits deleting loaders/downstream nodes, showing half-missing graphs despite users
    supplying assets on real calls. References use **actual uploaded counts** only: fewer references than slots is exactly
    what triggers graph edits, with deleted nodes explained by conversions, a critical preview outcome.
    """
    bindings: Mapping[str, Any] = definition.get("bindings") or {}
    uploaded = assets.items("reference_images") if assets else []
    slots = len(_targets(bindings.get("reference_images")))
    if uploaded:
        references = tuple(_summary("reference_images", item.content) for item in uploaded[:slots])
    else:
        references = tuple(_placeholder("reference_images") for _ in range(slots)) if placeholder_missing else ()
    return MediaInputs(
        start_image=_single_media(assets, bindings, "start_image", placeholder_missing=placeholder_missing),
        end_image=_single_media(assets, bindings, "end_image", placeholder_missing=placeholder_missing),
        reference_images=references,
    )


def _single_media(
    assets: EndpointTestAssets | None,
    bindings: Mapping[str, Any],
    key: str,
    *,
    placeholder_missing: bool,
) -> str | None:
    if not _targets(bindings.get(key)):
        return None
    raw = assets.single(key) if assets else None
    if raw is not None:
        return _summary(key, raw.content)
    return _placeholder(key) if placeholder_missing else None


def _summary(key: str, content: bytes) -> str:
    return f"<{key}, {len(content)} bytes>"


def _placeholder(key: str) -> str:
    return f"<{key} not uploaded>"


def _targets(raw: object) -> list[Mapping[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [target for target in raw if isinstance(target, Mapping)]
