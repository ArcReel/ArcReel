"""PROTOTYPE — #3091：把 PROVIDER_REGISTRY 转成「模型条目 + 内置供应商清单」的新结构，作原型的出厂快照。

用法（仓库根目录）：python frontend/src/prototype/model-catalog/export_snapshot.py
旧的 10 种定价形状按 ADR 0099 换算成「计量项 × 选择维度」；ViduDelegate 没有单价，导出为空价目表（未定价）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from lib.billing.pricing import types as T  # noqa: E402
from lib.config.registry import PROVIDER_REGISTRY  # noqa: E402

MAKER_RULES = [
    (r"^(gemini|veo|imagen|nano)", "google"),
    (r"^(gpt|sora|dall|o\d|chatgpt)", "openai"),
    (r"^(doubao|seed)", "bytedance"),
    (r"^grok", "xai"),
    (r"^(qwen|wan|cosyvoice|qwq|sambert)", "alibaba"),
    (r"^deepseek", "deepseek"),
    (r"^(kimi|moonshot)", "moonshot"),
    (r"^glm", "zhipu"),
    (r"^(minimax|hailuo|speech|s2v|t2v|i2v|image-01|music)", "minimax"),
    (r"^kling", "kuaishou"),
    (r"^vidu", "shengshu"),
    (r"^agnes", "agnes"),
]
PROVIDER_MAKER = {
    "gemini-aistudio": "google", "gemini-vertex": "google", "ark": "bytedance", "grok": "xai",
    "openai": "openai", "vidu": "shengshu", "dashscope": "alibaba", "minimax": "minimax",
    "kling": "kuaishou", "agnes": "agnes",
}


def maker_of(provider_id: str, model_id: str) -> str:
    low = model_id.lower()
    for pattern, maker in MAKER_RULES:
        if re.match(pattern, low):
            return maker
    return PROVIDER_MAKER.get(provider_id, "unknown")


def rates_for(rates: dict, model_id: str, default_model: str):
    return rates.get(model_id) or rates.get(default_model)


def row(when: dict, price: float) -> dict:
    return {"when": when, "price": price}


def convert(p, model_id: str) -> dict:
    """返回 {currency, meters:[{meter, unit, dims, rows}]}。"""
    if p is None:
        return {"currency": None, "meters": []}
    cur = p.currency
    if isinstance(p, T.ViduDelegate):
        return {"currency": cur, "meters": []}
    if isinstance(p, T.PerToken):
        r = rates_for(p.rates, model_id, p.default_model) or {}
        meters = [{"meter": "input_tokens", "unit": "per_1m", "dims": [], "rows": [row({}, r.get("input", 0))]}]
        if "cached_input" in r:
            meters.append({"meter": "cache_read_tokens", "unit": "per_1m", "dims": [], "rows": [row({}, r["cached_input"])]})
        meters.append({"meter": "output_tokens", "unit": "per_1m", "dims": [], "rows": [row({}, r.get("output", 0))]})
        return {"currency": cur, "meters": meters}
    if isinstance(p, T.PerImageFlat):
        return {"currency": cur, "meters": [{"meter": "output_images", "unit": "per_item", "dims": [], "rows": [row({}, rates_for(p.rates, model_id, p.default_model) or 0)]}]}
    if isinstance(p, T.PerImageByResolution):
        r = rates_for(p.rates, model_id, p.default_model) or {}
        return {"currency": cur, "meters": [{"meter": "output_images", "unit": "per_item", "dims": ["resolution"], "rows": [row({"resolution": k}, v) for k, v in r.items()]}]}
    if isinstance(p, T.PerImageOpenAIToken):
        r = rates_for(p.token_rates, model_id, p.default_model) or {}
        names = {"text_in": "input_tokens", "image_in": "image_input_tokens", "text_out": "output_tokens", "image_out": "image_output_tokens", "text_cached_in": "cache_read_tokens", "image_cached_in": "image_cache_read_tokens"}
        return {"currency": cur, "meters": [{"meter": names.get(k, k), "unit": "per_1m", "dims": [], "rows": [row({}, v)]} for k, v in r.items()]}
    if isinstance(p, T.PerSecondMatrix):
        r = rates_for(p.rates, model_id, p.default_model) or {}
        dims = {"resolution_audio": ["resolution", "audio"], "resolution_only": ["resolution"], "flat": []}[p.dimensions]
        rows = []
        for (res, audio), v in r.items():
            when = {}
            if "resolution" in dims:
                when["resolution"] = res
            if "audio" in dims:
                when["audio"] = audio
            rows.append(row(when, v))
        return {"currency": cur, "meters": [{"meter": "output_seconds", "unit": "per_second", "dims": dims, "rows": rows}]}
    if isinstance(p, T.PerVideoBucket):
        r = rates_for(p.rates, model_id, p.default_model) or {}
        return {"currency": cur, "meters": [{"meter": "requests", "unit": "per_item", "dims": ["resolution", "duration"], "rows": [row({"resolution": res, "duration": d}, v) for (res, d), v in r.items()]}]}
    if isinstance(p, T.PerSecondTiered):
        r = rates_for(p.rates, model_id, p.default_model) or {}
        return {"currency": cur, "meters": [{"meter": "output_seconds", "unit": "per_second", "dims": ["quality", "audio"], "rows": [row({"quality": q, "audio": a}, v) for (q, a), v in r.items()]}]}
    if isinstance(p, T.PerTokenVideo):
        r = rates_for(p.rates, model_id, p.default_model) or {}
        return {"currency": cur, "meters": [{"meter": "video_tokens", "unit": "per_1m", "dims": ["service_tier", "audio"], "rows": [row({"service_tier": t, "audio": a}, v) for (t, a), v in r.items()]}]}
    if isinstance(p, T.PerCharacter):
        return {"currency": cur, "meters": [{"meter": "characters", "unit": "per_10k", "dims": [], "rows": [row({}, rates_for(p.rates, model_id, p.default_model) or 0)]}]}
    raise TypeError(type(p))


entries: dict[str, dict] = {}
providers = []
for pid, meta in PROVIDER_REGISTRY.items():
    pmodels = []
    for key, info in meta.models.items():
        maker = maker_of(pid, key)
        entry_id = f"{maker}/{key}"
        # 方舟 Agent Plan 与方舟是同一批模型，只是 API 模型名写法不同：引用方舟的条目
        if pid == "ark-agent-plan":
            norm = key.replace(".", "-")
            twin = next((k for k in PROVIDER_REGISTRY["ark"].models if k.startswith(norm + "-")), None)
            if twin:
                entry_id = f"{maker}/{twin}"
        entry = {
            "id": entry_id,
            "maker": maker,
            "name": key,
            "displayName": info.display_name,
            "mediaType": info.media_type,
            "capabilities": list(info.capabilities),
            "durations": list(info.supported_durations),
            "resolutions": list(info.resolutions),
            "durationConstraints": dict(info.duration_resolution_constraints),
            "maxOutputTokens": info.max_output_tokens,
            "price": convert(info.pricing, key),
        }
        # 共用条目：同一身份已存在时保留先出现的一份（AI Studio 与 Vertex 的 Gemini 共用）
        entries.setdefault(entry_id, entry)
        pmodels.append({
            "entryId": entry_id,
            "apiModelName": info.api_model_name or key,
            "isDefault": info.default,
            "hidden": info.hidden,
        })
    providers.append({"id": pid, "displayName": meta.display_name, "mediaTypes": meta.media_types, "models": pmodels})

out = Path(__file__).with_name("snapshot.json")
out.write_text(json.dumps({"entries": list(entries.values()), "providers": providers}, ensure_ascii=False, indent=1))
print(f"{len(entries)} entries, {sum(len(p['models']) for p in providers)} provider models -> {out}")
