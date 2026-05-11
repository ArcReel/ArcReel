# PDF Fixture 来源

## sample_text.pdf

- **来源**：PyMuPDF 项目测试资源 [`tests/resources/test_4546.pdf`](https://github.com/pymupdf/PyMuPDF)
- **许可证**：AGPLv3（与本项目同许可证，兼容）
- **下载日期**：2026-05-11
- **内容**：2 页，中文海运托运单模板；每页 480+ 个 CJK 字符
- **用途**：测试 `PdfOxideExtractor` 正常文本抽取与页间分隔

## sample_scanned.pdf

- **来源**：PyMuPDF 项目测试资源 [`tests/resources/test_toc_count.pdf`](https://github.com/pymupdf/PyMuPDF)
- **许可证**：AGPLv3（与本项目同许可证，兼容）
- **下载日期**：2026-05-11
- **内容**：6 页，无文字图层（`extract_chars()` 返回空）
- **用途**：测试扫描件检测路径
