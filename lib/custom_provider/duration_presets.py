"""Name-inferred duration presets.

Sources: lmarena video-model Top 20, 2026-05 snapshot, plus common aggregator names.
First PRESETS match wins; no family match → DEFAULT_FALLBACK (``[4, 8]``).

Only two uses (docs/adr/0104, 0105): new-entry duration prefills and unmatched provider-model
runtime fallbacks (:mod:`lib.custom_provider.model_facts`). Never persist fallback results or prefill on provider-model saves,
which would masquerade as user-configured overrides.

Ambiguity: identical model_id, such as sora-2-pro, may accept different durations at first-party OpenAI versus aggregators.
Presets are heuristics only; users encode channel restrictions in overrides.
"""

from __future__ import annotations

import re

from lib.backends.video_backends.dashscope import classify_wan_model

DEFAULT_FALLBACK: list[int] = [4, 8]

# Wan 3.0 arbitrary 2–30s; source lib/config/registry.py wan3.0-video supported_durations.
_WAN3_DURATIONS: list[int] = list(range(2, 31))
# Wan 2.7 arbitrary 2–15s; source lib/config/registry.py wan2.7-{t2v,i2v,r2v} supported_durations.
_WAN27_DURATIONS: list[int] = list(range(2, 16))
# Alibaba HappyHorse arbitrary 3–15s.
_HAPPYHORSE_DURATIONS: list[int] = list(range(3, 16))

# wan3/wan2.7/happyhorse share classify_wan_model, the sole lib.backends.video_backends.dashscope
# classification entry, never additional regexes here. Endpoint routing/DashScopeVideoBackend capability profiles
# consume identical conclusions, preventing three drifting match widths. Other PRESETS families (sora/veo/kling)
# remain vendor-keyword matches outside this classifier, as does final generic wan fallback below.

# Most-specific first; return first match. Fully expand ranges into discrete sets.
PRESETS: list[tuple[re.Pattern[str], list[int]]] = [
    # First-party OpenAI Sora: strict regex, optional -pro and -YYYY-MM-DD.
    (re.compile(r"^sora-2(-pro)?(-\d{4}-\d{2}-\d{2})?$", re.I), [4, 8, 12]),
    # Third-party Sora-Pro variants, commonly 6/10/12/16/20.
    (re.compile(r"sora.*pro", re.I), [6, 10, 12, 16, 20]),
    # Google Veo including fast / lite / preview.
    (re.compile(r"veo-?\d", re.I), [4, 6, 8]),
    # All Kling v1/v2/v2.5/v2.6/v3.0/o1/turbo/pro/omni/standard.
    (re.compile(r"kling[-.]?(o1|v?[123](\.\d+)?)", re.I), [5, 10]),
    # Runway Gen family.
    (re.compile(r"^(runway[-.]?)?gen-?\d", re.I), [5, 8, 10]),
    # Luma Ray / Dream Machine
    (re.compile(r"\bray-?\d", re.I), [5, 10]),
    # ByteDance Dreamina / Seedance arbitrary 4–15s.
    (re.compile(r"dreamina|seedance", re.I), list(range(4, 16))),
    # ByteDance Dreamina.
    (re.compile(r"jimeng", re.I), list(range(4, 16))),
    # xAI Grok Imagine arbitrary 1–15s.
    (re.compile(r"grok[-.]?imagine", re.I), list(range(1, 16))),
    # Vidu Q arbitrary 1–16s.
    (re.compile(r"vidu", re.I), list(range(1, 17))),
    # PixVerse V5/V5.5/V5.6/V6 arbitrary 1–15s.
    (re.compile(r"pixverse|^v[56](\.\d+)?$", re.I), list(range(1, 16))),
    # MiniMax H3 arbitrary 4–15s; no token overlap with hailuo below, grouped near MiniMax families.
    # Source: lib/config/registry.py MiniMax-H3 supported_durations.
    (re.compile(r"minimax-h3", re.I), list(range(4, 16))),
    # MiniMax Hailuo fixed 6s.
    (re.compile(r"hailuo", re.I), [6]),
    # Other Wan families not classified wan3/wan2.7, including non-2.7 Wan 2.x minors.
    # Accept hyphens/underscores without identifier boundaries: heuristic fallback for third-party names containing wan,
    # such as "swan3"/"wan20", is closer to common values than DEFAULT_FALLBACK, and users must review presets
    # in inputs anyway. infer_supported_durations already classifies wan3/wan2.7/happyhorse via classify_wan_model
    # and returns before reaching this rule.
    (re.compile(r"wan[-_]?\d", re.I), [4, 5]),
    # Pika
    (re.compile(r"pika", re.I), [3, 5, 10]),
]


def infer_supported_durations(model_id: str) -> list[int]:
    """Heuristically infer supported_durations from model_id.

    Always return independent nonempty sorted/deduplicated positive-integer lists, safe for callers to modify.
    """
    classification = classify_wan_model(model_id)
    if classification.family == "wan3":
        return list(_WAN3_DURATIONS)
    # Unimplemented wan2.7 videoedit / s2v / v2v (classify_wan_model
    # has_known_modality) have different limits from t2v/i2v/r2v; skip family tiers for generic presets below.
    if classification.family == "wan2.7" and classification.has_known_modality:
        return list(_WAN27_DURATIONS)
    if classification.family == "happyhorse":
        return list(_HAPPYHORSE_DURATIONS)
    for pattern, durations in PRESETS:
        if pattern.search(model_id):
            return list(durations)
    return list(DEFAULT_FALLBACK)
