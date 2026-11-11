"""价格编辑器的词表：前端用的 TS 文件由代码里的封闭集合生成，不手写第二份。"""

from scripts.generate_price_vocabulary import OUTPUT_PATH, render


def test_generated_vocabulary_is_up_to_date():
    assert OUTPUT_PATH.read_text(encoding="utf-8") == render(), (
        "词表已过期，运行 `uv run python scripts/generate_price_vocabulary.py` 重新生成"
    )


def test_rendered_vocabulary_carries_meters_and_dimensions():
    text = render()

    assert 'characters: { kind: "count", per: 10000, mediaTypes: ["audio"] },' in text
    assert 'audio_output_tokens: { kind: "token", per: 1000000, mediaTypes: ["audio"] },' in text
    assert 'reference_to_video: { kind: "flag", mediaTypes: ["video"] },' in text
