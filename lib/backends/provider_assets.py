"""Boundary between call channels and provider material references: actual inference credentials, eligible image slots, and input-image rejection.

Material-library scope depends on explicit inference-credential bindings (``lib.asset_library.bindings``).
Channels therefore report credentials actually used for requests, never reread by orchestration from
provider names/configuration. Channel protocols and model declarations determine reference support:
only declared slots receive ``asset://`` references; all others send image content.

Independent of channel packages: both built-in/custom assembly construct credential references,
and orchestration can read declarations without depending on concrete channels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

#: Reference-to-video image slot, excluding first/last frames; see :func:`provider_asset_roles`.
REFERENCE_IMAGE_ROLE = "reference_image"


@dataclass(frozen=True)
class InferenceCredentialRef:
    """Inference credentials actually used by a channel to send requests.

    ``base_url`` retains raw credential/custom-provider configuration values; ``default_base_url`` is
    the actual destination when raw values are empty, matching binding-confirmation digest calculation.
    ``api_key`` is used only in-process for binding checks, never logs/checkpoints.
    """

    credential_type: Literal["builtin", "custom"]
    credential_id: int
    api_key: str
    base_url: str | None
    default_base_url: str | None = None

    def __repr__(self) -> str:
        return (
            f"InferenceCredentialRef(credential_type={self.credential_type!r}, "
            f"credential_id={self.credential_id!r}, base_url={self.base_url!r})"
        )


def inference_credential(backend: object) -> InferenceCredentialRef | None:
    """Channel-reported inference credentials, or None when omitted by non-library protocols/test doubles."""
    value = getattr(backend, "inference_credential", None)
    return value if isinstance(value, InferenceCredentialRef) else None


def provider_asset_roles(backend: object) -> frozenset[str]:
    """Slots declared by this channel/model to accept material references instead of image content; empty when undeclared."""
    value = getattr(backend, "provider_asset_roles", None)
    return value if isinstance(value, frozenset) else frozenset()


#: Ark input-image moderation error-code prefix, e.g. ``InputImageSensitiveContentDetected.PrivacyInformation``.
_INPUT_IMAGE_REJECTION_PREFIX = "InputImageSensitiveContentDetected"


def is_input_image_rejection(provider_code: object) -> bool:
    return isinstance(provider_code, str) and provider_code.startswith(_INPUT_IMAGE_REJECTION_PREFIX)


class ProviderInputImageRejectedError(RuntimeError):
    """Provider refused the request because an input image failed moderation.

    ``reference_index`` is the rejected image's 0-based request-reference index, set only when reliably
    known. ``sent_as_asset`` identifies material-reference submission. Orchestration names affected
    asset/derivative images and suggests provider-library upload for ordinary images; never upload
    automatically or retry generation.
    """

    def __init__(
        self,
        *,
        provider_code: str,
        provider_reason: str | None,
        reference_index: int | None,
        sent_as_asset: bool,
    ) -> None:
        self.provider_code = provider_code
        self.provider_reason = provider_reason
        self.reference_index = reference_index
        self.sent_as_asset = sent_as_asset
        self.code = "provider_input_image_rejected"
        self.params: dict[str, Any] = {"error_code": provider_code}
        if provider_reason:
            self.params["provider_reason"] = provider_reason
        super().__init__(f"{provider_code}: {provider_reason or ''}".strip())

    def name_reference(self, *, index: int, name: str) -> ProviderInputImageRejectedError:
        """Name rejected references: ``index`` is the 1-based prompt image number; ``name`` identifies the asset/derivative."""
        named = ProviderInputImageRejectedError(
            provider_code=self.provider_code,
            provider_reason=self.provider_reason,
            reference_index=self.reference_index,
            sent_as_asset=self.sent_as_asset,
        )
        named.code = "provider_input_asset_rejected_at" if self.sent_as_asset else "provider_input_image_rejected_at"
        named.params = {**self.params, "index": index, "name": name}
        return named


__all__ = [
    "REFERENCE_IMAGE_ROLE",
    "InferenceCredentialRef",
    "ProviderInputImageRejectedError",
    "inference_credential",
    "is_input_image_rejection",
    "provider_asset_roles",
]
