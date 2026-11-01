"""内置模型的任务类型桶归属：直读目录快照与 backend 能力声明。"""

from __future__ import annotations

from lib.backends.generation_type_buckets import builtin_model_buckets
from lib.catalog import query as catalog


def _builtin_buckets(provider_id: str, model_id: str):
    model_info = catalog.get_model(provider_id, model_id)
    assert model_info is not None
    return builtin_model_buckets(provider_id, model_id, model_info)


def test_grok_lite_is_not_an_r2v_candidate():
    """xAI 只给 1.5 与经典版写了参考生视频；lite 带参考图被供应商拒收，不进 r2v 桶。"""
    assert _builtin_buckets("grok", "grok-imagine-video-1.5-lite") == frozenset({"i2v"})
    assert "r2v" in _builtin_buckets("grok", "grok-imagine-video-1.5")
    assert "r2v" in _builtin_buckets("grok", "grok-imagine-video")
