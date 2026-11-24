# PDF 测试样本来源

两份样本于 2026-05-11 引入。本地文件与下列 `pdf_oxide` 上游版本中的对应文件逐字节一致，来源仓库采用 [MIT](https://github.com/yfedoseev/pdf_oxide/blob/4eae6d7adf3ff22dedc90fe096e4640945d075f8/LICENSE-MIT) 或 [Apache-2.0](https://github.com/yfedoseev/pdf_oxide/blob/4eae6d7adf3ff22dedc90fe096e4640945d075f8/LICENSE-APACHE) 许可。

| 本地文件 | 上游文件 | 验证用途 |
|---|---|---|
| [`sample_text.pdf`](sample_text.pdf) | [`tests/fixtures/1.pdf`](https://github.com/yfedoseev/pdf_oxide/blob/4eae6d7adf3ff22dedc90fe096e4640945d075f8/tests/fixtures/1.pdf) | 从 7 页中文证券研究报告中提取文本并按页分隔。 |
| [`sample_scanned.pdf`](sample_scanned.pdf) | [`tests/fixtures/encrypted_objstm.pdf`](https://github.com/yfedoseev/pdf_oxide/blob/4eae6d7adf3ff22dedc90fe096e4640945d075f8/tests/fixtures/encrypted_objstm.pdf) | 使用 `extract_chars()` 结果为空的 3 页样本，验证抽取器把无文字层 PDF 报为扫描件。 |

两种行为由 [PDF 抽取测试](../test_pdf.py)覆盖。
