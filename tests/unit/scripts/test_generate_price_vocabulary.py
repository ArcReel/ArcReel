"""价格编辑器的词表：前端用的 TS 文件由代码里的封闭集合生成，不手写第二份。"""

import json
from pathlib import Path

from arcreel_catalog_core import DIMENSION_MEDIA_TYPES, METERS
from scripts.generate_price_vocabulary import OUTPUT_PATH, render

_SNAPSHOT = Path(__file__).resolve().parents[3] / "lib/catalog/snapshot/catalog.json"


def test_generated_vocabulary_is_up_to_date():
    assert OUTPUT_PATH.read_text(encoding="utf-8") == render(), (
        "词表已过期，运行 `uv run python scripts/generate_price_vocabulary.py` 重新生成"
    )


def test_rendered_vocabulary_carries_meters_and_dimensions():
    text = render()

    assert 'characters: { kind: "count", per: 10000, mediaTypes: ["audio"] },' in text
    assert 'audio_output_tokens: { kind: "token", per: 1000000, mediaTypes: ["audio"] },' in text
    assert 'reference_to_video: { kind: "flag", mediaTypes: ["video"] },' in text


def test_factory_snapshot_only_uses_dimensions_and_meters_of_its_media_type():
    entries = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))["entries"]
    for entry in entries:
        for meter in (entry.get("price") or {}).get("meters", []):
            assert entry["media_type"] in METERS[meter["meter"]].media_types
            for dim in meter["dims"]:
                assert entry["media_type"] in DIMENSION_MEDIA_TYPES[dim], (entry["name"], dim)
