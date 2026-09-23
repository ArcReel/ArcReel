# PDF Fixture 来源

均取自 pdf_oxide 项目的 [`tests/fixtures/`](https://github.com/yfedoseev/pdf_oxide/tree/main/tests/fixtures)，
与运行时依赖同源，可避免后续 pdf_oxide 解析行为变更引入的样本兼容性漂移。

## sample_text.pdf

- **来源**：`tests/fixtures/1.pdf`
- **许可证**：MIT / Apache-2.0
- **下载日期**：2026-05-11
- **内容**：7 页中文证券研究报告，每页 600+ CJK 字符
- **用途**：测试 `PdfOxideExtractor` 正常文本抽取与页间分隔

## sample_scanned.pdf

- **来源**：`tests/fixtures/encrypted_objstm.pdf`
- **许可证**：MIT / Apache-2.0
- **下载日期**：2026-05-11
- **内容**：3 页，无文字图层（`extract_chars()` 返回空）
- **用途**：测试扫描件检测路径
