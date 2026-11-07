"""Custom-provider backend wrappers.

Wrap existing OpenAI/Gemini/etc. backends, overriding name/model properties.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from arcreel_catalog_core import CatalogEntry, VideoFacts
from arcreel_market_core.video_backend_contract import (
    VideoBackend,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.audio_backends.base import (
    AudioBackend,
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.backends.image_backends.base import (
    ImageBackend,
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
)
from lib.backends.provider_assets import InferenceCredentialRef, inference_credential, provider_asset_roles
from lib.backends.text_backends.base import TextBackend, TextCapability, TextGenerationRequest, TextGenerationResult
from lib.backends.video_backends.name_family import catalog_entry_of
from lib.billing.metering import Usage
from lib.custom_provider.metering import custom_video_usage, with_image_counts
from lib.custom_provider.path_overrides import NO_PATH_OVERRIDES, PathOverrides, endpoint_guard, replaying_base_url


class CustomTextBackend:
    """Custom-provider text wrapper."""

    def __init__(self, *, provider_id: str, delegate: TextBackend, model: str) -> None:
        self._provider_id = provider_id
        self._delegate = delegate
        self._model = model

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return self._delegate.capabilities

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        return await self._delegate.generate(request)


class CustomImageBackend:
    """Custom-provider image wrapper.

    ``capabilities`` declares implemented channels; ``model_capabilities`` permits model-side capabilities
    from whole-dimension entry overrides, unconstrained for ``None``, injected by factories. Execution
    intersects both (:attr:`effective_capabilities`), matching bucket candidates. ``entry_reference_limit``
    is matching-entry maxima (``None`` unlisted; ComfyUI ignores entries), jointly evaluated with channels through
    :func:`lib.backends.image_capabilities.backend_reference_image_limit`.
    """

    def __init__(
        self,
        *,
        provider_id: str,
        delegate: ImageBackend,
        model: str,
        model_capabilities: frozenset[ImageCapability] | None = None,
        entry_reference_limit: int | None = None,
        reference_limit_override: int | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._delegate = delegate
        self._model = model
        self._model_capabilities = model_capabilities
        self._entry_reference_limit = entry_reference_limit
        self.reference_limit_override = reference_limit_override

    def with_model_facts(
        self,
        model_capabilities: frozenset[ImageCapability] | None,
        *,
        entry_reference_limit: int | None,
        reference_limit_override: int | None = None,
    ) -> CustomImageBackend:
        """Return new instances injecting model-side T2I/I2I/reference maxima, factory-resolved from rows/entries/overrides."""
        return CustomImageBackend(
            provider_id=self._provider_id,
            delegate=self._delegate,
            model=self._model,
            model_capabilities=model_capabilities,
            entry_reference_limit=entry_reference_limit,
            reference_limit_override=reference_limit_override,
        )

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._delegate.capabilities

    @property
    def model_capabilities(self) -> frozenset[ImageCapability] | None:
        return self._model_capabilities

    @property
    def entry_reference_limit(self) -> int | None:
        return self._entry_reference_limit

    @property
    def effective_capabilities(self) -> frozenset[ImageCapability]:
        """T2I/I2I implemented by channels and permitted by model facts."""
        implemented = frozenset(self.capabilities)
        return implemented if self._model_capabilities is None else implemented & self._model_capabilities

    async def reference_image_limit(self) -> int | None:
        # Channel limits come from wrapped declarations.
        return await self._delegate.reference_image_limit()

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        result = await self._delegate.generate(request)
        # Undeclared metering channels (ComfyUI/declarative images/OpenAI Chat images) report generic counts; preserve built-in
        # measurements and supplement counts, leaving entry price tables to price (docs/adr/0100).
        return replace(result, usage=with_image_counts(result.usage, len(request.reference_images)))


class CustomAudioBackend:
    """Custom-provider speech-synthesis wrapper."""

    def __init__(self, *, provider_id: str, delegate: AudioBackend, model: str) -> None:
        self._provider_id = provider_id
        self._delegate = delegate
        self._model = model

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return self._delegate.capabilities

    def list_voices(self) -> list[VoiceOption]:
        return self._delegate.list_voices()

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        return await self._delegate.synthesize(request)


class CustomVideoBackend:
    """Custom-provider video wrapper.

    Factories may inject effective ``video_capabilities`` (joint system/entries ⊕ user overrides), replacing forwarding
    wrapped declarations, which provide channel facts only; entries/overrides must narrow or flip execution capabilities.
    Without injection, such as direct endpoint-closure construction, forward declarations.
    """

    def __init__(
        self,
        *,
        provider_id: str,
        delegate: VideoBackend,
        model: str,
        video_capabilities: VideoCapabilities | None = None,
        capability_overrides: dict[str, object] | None = None,
        entry_facts: VideoFacts | None = None,
        endpoint: str | None = None,
        path_overrides: PathOverrides = NO_PATH_OVERRIDES,
    ) -> None:
        self._provider_id = provider_id
        self._delegate = delegate
        self._model = model
        self._video_capabilities = video_capabilities
        self._capability_overrides = capability_overrides or {}
        self._entry_facts = entry_facts
        self._endpoint = endpoint
        self._path_overrides = path_overrides

    @property
    def endpoint(self) -> str | None:
        """Construction endpoint keys from ENDPOINT_REGISTRY, factory-injected.

        Endpoints determine protocols, the actual construction granularity. Model endpoints can change within media,
        so submitted-task resume compares endpoints, not provider/models alone (``docs/adr/0054``).
        None when bypassing factories: resume skips comparisons, matching legacy tasks without persisted endpoints.
        """
        return self._endpoint

    @property
    def endpoint_guard(self) -> str | None:
        """Checkpoint-frozen protocols: endpoints plus construction path overrides (``docs/adr/0101``).

        Changed overrides fail resume as protocol drift, like changed endpoints (``docs/adr/0007``).
        """
        return None if self._endpoint is None else endpoint_guard(self._endpoint, self._path_overrides)

    def with_video_capabilities(
        self,
        capabilities: VideoCapabilities,
        *,
        overrides: dict[str, object] | None = None,
        entry_facts: VideoFacts | None = None,
    ) -> CustomVideoBackend:
        """Return new effective-capability instances, keeping wrappers immutable after construction.

        ``overrides`` is filtered sparse values from `filter_valid_overrides`; ``entry_facts`` is corresponding entry
        video facts, ``None`` for ComfyUI. `video_capabilities_for_tier` uses both against tier-aware bases
        under identical rules. Pass separately from ``capabilities``, complete jointly evaluated/overridden results for context-free
        `video_capabilities`. Combining them would short-circuit tier queries into full composed results; see
        `video_capabilities_for_tier`.
        """
        return CustomVideoBackend(
            provider_id=self._provider_id,
            delegate=self._delegate,
            model=self._model,
            video_capabilities=capabilities,
            capability_overrides=overrides,
            entry_facts=entry_facts,
            endpoint=self._endpoint,
            path_overrides=self._path_overrides,
        )

    def with_endpoint(self, endpoint: str, *, path_overrides: PathOverrides = NO_PATH_OVERRIDES) -> CustomVideoBackend:
        """Return new instances retaining construction endpoints/path overrides, preserving postconstruction immutability."""
        return CustomVideoBackend(
            provider_id=self._provider_id,
            delegate=self._delegate,
            model=self._model,
            video_capabilities=self._video_capabilities,
            capability_overrides=self._capability_overrides,
            entry_facts=self._entry_facts,
            endpoint=endpoint,
            path_overrides=path_overrides,
        )

    def with_catalog_entry(self, entry: CatalogEntry | None) -> CustomVideoBackend:
        """Return new instances whose wrapped protocol identifies capabilities/asset slots through ``entry``; unchanged if protocols ignore entries.

        ``entry`` matches this model, bindings first/current names otherwise. :meth:`video_capabilities_for_tier`
        uses protocol declarations as bases, requiring the same entry as factory-composed capabilities; request models remain unchanged.
        Asset slots: :attr:`provider_asset_roles`.
        """
        rebind = getattr(self._delegate, "with_catalog_entry", None)
        if not callable(rebind):
            return self
        # Protocol with_catalog_entry returns same-class copies, name_family.EntryBoundCapabilities.
        delegate = cast("VideoBackend", rebind(entry))
        return CustomVideoBackend(
            provider_id=self._provider_id,
            delegate=delegate,
            model=self._model,
            video_capabilities=self._video_capabilities,
            capability_overrides=self._capability_overrides,
            entry_facts=self._entry_facts,
            endpoint=self._endpoint,
            path_overrides=self._path_overrides,
        )

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def video_capabilities(self) -> VideoCapabilities:
        if self._video_capabilities is not None:
            return self._video_capabilities
        return self._delegate.video_capabilities

    @property
    def inference_credential(self) -> InferenceCredentialRef | None:
        """Wrapped protocol inference credentials, namely this custom-provider configuration."""
        return inference_credential(self._delegate)

    @property
    def provider_asset_roles(self) -> frozenset[str]:
        """Forward wrapped asset declarations: ``asset://`` acceptance depends on protocols/entries, unchanged by wrappers.

        Factories provide entries through :meth:`with_catalog_entry`; without them, directly constructed protocol backends
        cannot identify entries and declare no slots.
        """
        return provider_asset_roles(self._delegate)

    def video_capabilities_for_tier(self, service_tier: str, resolution: str | None = None) -> VideoCapabilities:
        """Narrow by request tiers using wrapped tier-aware queries, such as Kling;
        detect with `getattr`, matching `media_generator`, falling back to context-free
        `video_capabilities`. Jointly evaluate factory entry facts, then sparse user overrides,
        leaving other fields evaluated. Without injected facts, such as ComfyUI/direct construction, skip entry evaluation.

        Never short-circuit to factory-composed `self._video_capabilities`: these use
        context-free system declarations, conservative for tier-sensitive backends. Factory paths always
        populate this field; short-circuiting would disable production tier awareness,
        silently dropping Pro-acceptable last frames.
        """
        tier_aware = getattr(self._delegate, "video_capabilities_for_tier", None)
        base = (
            tier_aware(service_tier, resolution=resolution)
            if tier_aware is not None
            else self._delegate.video_capabilities
        )
        if self._entry_facts is not None:
            from lib.backends.video_capabilities import effective_video_capabilities

            base = effective_video_capabilities(base, self._entry_facts)
        if self._capability_overrides:
            # Postmerge invariants match synthesize; this path also merges bases ⊕ sparse overrides.
            # Omitting checks would permit direct ⊕ limit0 combinations rejected by composition.
            from lib.custom_provider.capabilities import enforce_audio_capability_invariant, merge_overrides

            return enforce_audio_capability_invariant(
                merge_overrides(base, self._capability_overrides),
                endpoint=self._delegate.name,
                model_id=self._model,
            )
        return base

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # Inject execution endpoints so downstream protocols persist them with job_id after submission,
        # allowing resume drift checks. Replace instead of mutate: callers own requests,
        # and wrappers must leave no side effects.
        if self._endpoint is not None:
            request = replace(request, execution_endpoint=self._endpoint)
        return await self._delegate.generate(request)

    @property
    def catalog_entry(self) -> CatalogEntry | None:
        """Wrapped model-identifying entries, supplied by :meth:`with_catalog_entry`; ``None`` without them."""
        return catalog_entry_of(self._delegate)

    def metered_usage(
        self,
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None = None,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage:
        """Request metering: wrapped declarations when present/recognized, generic for unknown models or missing declarations.

        ``entry`` corresponds to models, taken from :attr:`catalog_entry` during ``backend_metered_usage`` settlement;
        wrapped channels identify models through it. :mod:`lib.custom_provider.metering`.
        """
        return custom_video_usage(getattr(self._delegate, "metered_usage", None), model, request, result, entry=entry)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        # Forward downstream; unsupported resume raises NotImplementedError,
        # marked [resume_unsupported] by orphan handlers. Replay submitted bases so overrides recognize resume requests.
        with replaying_base_url(request.submitted_base_url):
            return await self._delegate.resume_video(job_id, request)
