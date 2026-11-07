"""Three endpoint-test modes and supported definition ``kind`` values.

Modes answer progressively deeper questions about one definition, but not every kind supports all. Response validation requires
user-configurable extraction paths; otherwise it tests fixed code without useful information
(``docs/adr/0081``). Unsupported modes are not invalid definitions, so callers produce structured rejections, not definition diagnostics.

Only catalogs/pure predicates here, no runtimes. Three HTTP entry points and services share support matrices, preventing
admission followed by execution-time discovery that kinds lack implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from arcreel_market_core.endpoint_definition import COMFYUI_KIND, DECLARATIVE_KIND


class EndpointTestMode(StrEnum):
    """Preview request, validate response, test connection; values identify modes only, never transmitted."""

    PREVIEW_REQUEST = "preview-request"
    CHECK_RESPONSE = "check-response"
    TRIAL_RUN = "trial-run"


#: ``kind`` → supported modes; unlisted kinds support none.
_MODES_BY_KIND: Mapping[str, frozenset[EndpointTestMode]] = {
    DECLARATIVE_KIND: frozenset(EndpointTestMode),
    # ComfyUI lacks response validation: artifact extraction reads three fixed keys of the ``output``-bound node, without
    # user-configurable extraction paths, so tests would exercise fixed code only (``docs/adr/0081``).
    COMFYUI_KIND: frozenset({EndpointTestMode.PREVIEW_REQUEST, EndpointTestMode.TRIAL_RUN}),
}


#: All testable kinds, matching :data:`.kinds.SUPPORTED_KINDS`; support matrices without implementations
#: would admit requests before discovering nothing can run.
TESTABLE_KINDS = frozenset(_MODES_BY_KIND)


def supports_test_mode(kind: str, mode: EndpointTestMode) -> bool:
    """Whether this definition ``kind`` supports this mode."""
    return mode in _MODES_BY_KIND.get(kind, frozenset())
