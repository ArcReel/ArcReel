"""Estimated built-in provider image usage (docs/adr/0100).

Estimates and settlement share calculators: channels report actual metered items at settlement and
derive estimates from request facts (built-in ``estimate_usage`` implementations). Selection dimensions
come from fields actually sent to providers (docs/adr/0056).
"""

from __future__ import annotations

from dataclasses import replace

from lib.backends.backend_assembly.specs import builtin_model_spec
from lib.backends.image_backends.base import ImageRequestFacts
from lib.backends.image_backends.registry import channel_usage_estimator
from lib.billing.metering import Usage
from lib.catalog import query as catalog


def estimate_builtin_image_usage(provider_id: str, facts: ImageRequestFacts) -> Usage:
    """Estimate one built-in image call; raise ``ValueError`` when providers lack image-call channels.

    Channels estimate by model entries (``facts.entry``): catalog-referenced entries for official models,
    bound entries or live name lookup results for user-added models.
    """
    estimator = channel_usage_estimator(builtin_model_spec(provider_id, "image", facts.model).registry_backend)
    return estimator(replace(facts, entry=catalog.provider_model_entry(provider_id, facts.model, "image")))
