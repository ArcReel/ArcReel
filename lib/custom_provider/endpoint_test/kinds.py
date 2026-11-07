"""Endpoint-test implementations by ``kind``: preview rendering, connection-test assembly, and credential requirements.

:mod:`.modes` holds pure runtime-free support catalogs shared by three HTTP entries; this module implements supported
operations. Tables align by kind, guarded by
``test_the_support_matrix_and_the_implementations_cover_the_same_kinds``.

Routers know no specific kinds: retrieve implementations by definition ``kind``, then share all remaining steps
across both kinds. Credential normalization, asset staging, accounting, writes, and cancellation must never duplicate per endpoint kind.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from arcreel_market_core.endpoint_definition import COMFYUI_KIND, DECLARATIVE_KIND
from lib.custom_provider.endpoints import declarative_requires_api_key, declarative_requires_base_url
from lib.custom_provider.path_overrides import NO_PATH_OVERRIDES, PathOverrides

from .comfyui import comfyui_credential_needs, comfyui_target, preview_comfyui_request
from .inputs import EndpointTestAssets, EndpointTestCredentials, EndpointTestParameters
from .preview import RequestPreview, preview_request
from .trial_run import TrialRunTarget, declarative_target


class PreviewFn(Protocol):
    """Render one request preview."""

    def __call__(
        self,
        definition: Mapping[str, Any],
        parameters: EndpointTestParameters,
        *,
        credentials: EndpointTestCredentials | None = None,
        assets: EndpointTestAssets | None = None,
        placeholder_missing_assets: bool = True,
        path_overrides: PathOverrides = NO_PATH_OVERRIDES,
    ) -> RequestPreview: ...


class TrialTargetFn(Protocol):
    """Assemble connection-test targets from inline definitions/credentials."""

    def __call__(
        self,
        definition: Mapping[str, Any],
        credentials: EndpointTestCredentials,
        parameters: EndpointTestParameters,
        *,
        provider: str | None = None,
    ) -> TrialRunTarget: ...


@dataclass(frozen=True)
class KindTestSupport:
    """Endpoint-test implementation for one kind."""

    preview: PreviewFn
    #: Whether definitions require ``base_url`` / ``api_key``; neither means calls need no credentials.
    credential_needs: Callable[[Mapping[str, Any]], tuple[bool, bool]]
    trial_target: TrialTargetFn
    #: Whether connection results include rendered submission requests, also serving presubmission render gates.
    #: ComfyUI omits them: actual workflow seeds/asset reference names resolve only at submission.
    trial_run_request_preview: bool


def _declarative_credential_needs(definition: Mapping[str, Any]) -> tuple[bool, bool]:
    return declarative_requires_base_url(definition), declarative_requires_api_key(definition)


_SUPPORT_BY_KIND: Mapping[str, KindTestSupport] = {
    DECLARATIVE_KIND: KindTestSupport(
        preview=preview_request,
        credential_needs=_declarative_credential_needs,
        trial_target=declarative_target,
        trial_run_request_preview=True,
    ),
    COMFYUI_KIND: KindTestSupport(
        preview=preview_comfyui_request,
        credential_needs=comfyui_credential_needs,
        trial_target=comfyui_target,
        trial_run_request_preview=False,
    ),
}


#: All implemented kinds, matching :data:`.modes.TESTABLE_KINDS`.
SUPPORTED_KINDS = frozenset(_SUPPORT_BY_KIND)


def support_for_kind(kind: str) -> KindTestSupport:
    """Endpoint-test implementations for ``kind``.

    Raises:
        KeyError: Unlisted kinds. Callers first apply :func:`~.modes.supports_test_mode` structured rejections;
            kinds reaching here necessarily have implementations.
    """
    return _SUPPORT_BY_KIND[kind]
