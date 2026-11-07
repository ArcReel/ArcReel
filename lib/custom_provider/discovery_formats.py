"""Model discovery ``discovery_format`` catalog and endpoint-definition ``kind`` attachment rules.

``discovery_format`` chooses discovery/connectivity interfaces, never model-call protocols,
which live on model-attached endpoints. Usually independent: OpenAI-protocol provider
models can attach any declarative endpoint.

``comfyui`` is the sole bidirectional exception: workflows submit only to ComfyUI services,
which accept only workflows. Mismatches remain invisible at save time until generation transport fails,
so pairing rules belong to writes (``docs/adr/0081``).

Constants/pure predicates only, without runtime-loading imports, shared by write validation/capacity loading/frontend catalogs.
"""

from __future__ import annotations

from arcreel_market_core.endpoint_definition import COMFYUI_KIND

#: ComfyUI connectivity directly calls ``/system_stats``; discovery is inapplicable.
COMFYUI_DISCOVERY_FORMAT = "comfyui"


def is_comfyui_protocol(discovery_format: str) -> bool:
    """Whether providers declare ComfyUI protocols."""
    return discovery_format == COMFYUI_DISCOVERY_FORMAT


def endpoint_attachment_holds(*, endpoint_kind: str, discovery_format: str) -> bool:
    """Whether model endpoint × provider protocol attachments hold.

    ComfyUI endpoints/providers are each other's sole counterpart; allow all other combinations. Both is-ComfyUI
    answers must match, expressed as one equality rather than two drifting branches.
    """
    return (endpoint_kind == COMFYUI_KIND) == is_comfyui_protocol(discovery_format)
