from lib.source_loader.docx import DocxExtractor


def test_docx_extracts_paragraphs(docx_factory):
    src = docx_factory(["第一段：开篇。", "第二段：转折。"])
    result = DocxExtractor().extract(src)
    assert "第一段：开篇。" in result.text
    assert "第二段：转折。" in result.text
    assert result.used_encoding is None
    assert result.chapter_count == 0


def test_docx_falls_back_to_mammoth(monkeypatch, docx_factory):
    """docx2txt 抛错 → 回退到 mammoth.convert_to_markdown。"""
    src = docx_factory(["回退路径内容"])

    import lib.source_loader.docx as mod

    def _boom(_path):
        raise RuntimeError("simulated docx2txt failure")

    monkeypatch.setattr(mod.docx2txt, "process", _boom)
    result = DocxExtractor().extract(src)
    assert "回退路径内容" in result.text
