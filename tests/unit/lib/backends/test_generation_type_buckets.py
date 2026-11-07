"""Built-in task buckets read catalog snapshots/backend capabilities directly."""

from __future__ import annotations

from lib.backends.generation_type_buckets import builtin_model_buckets
from lib.catalog import query as catalog


def _builtin_buckets(provider_id: str, model_id: str):
    model_info = catalog.get_model(provider_id, model_id)
    assert model_info is not None
    return builtin_model_buckets(provider_id, model_id, model_info)


def test_grok_lite_is_not_an_r2v_candidate():
    """xAI reference-video is declared only for 1.5/classic; providers reject lite references, so exclude r2v."""
    assert _builtin_buckets("grok", "grok-imagine-video-1.5-lite") == frozenset({"i2v"})
    assert "r2v" in _builtin_buckets("grok", "grok-imagine-video-1.5")
    assert "r2v" in _builtin_buckets("grok", "grok-imagine-video")
