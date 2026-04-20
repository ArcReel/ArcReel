# 源文件格式扩展 · Release Notes

**日期**: 2026-04-20
**分支**: `feat/source-file-format-extension`

## 亮点

- 上传支持 `.txt / .md / .docx / .epub / .pdf` 五种格式（移除 `.doc`）
- 非 UTF-8 `.txt` 自动检测并规范化为 UTF-8；原文件备份到 `source/raw/`
- EPUB 自动注入 `# 章节标题` 标记，便于人工切分与识别结构
- 同名文件冲突时前端弹窗协商（保留两者 / 替换 / 取消）
- 首次上传自动触发分析（减少"有文件但没点击开始"的 UX 摩擦）
- 启动时一次性迁移历史项目源文件编码（幂等，失败不阻塞启动；明细见 `projects/<name>/.arcreel/migration_errors.log`）

## 架构

新增 `lib/source_loader/` 包，上传路由 → SourceLoader 规范化 → 下游 `_read_source_files` 零改动。

## 依赖

新增 Python 包：`charset-normalizer / docx2txt / mammoth / ebooklib / beautifulsoup4 / lxml / pymupdf`。零系统依赖，总 ~50-60 MB。

## 升级注意

- 首次启动会对 `projects/` 下所有现存项目执行一次性编码规范化迁移
- 失败项目不阻塞 server，明细写入该项目的 `.arcreel/migration_errors.log`
- 如需重跑某项目的迁移，删除其 `.arcreel/source_encoding_migrated` 标记文件后重启 server

## 已知限制

- 扫描版 PDF 不支持（明确返回 422 提示使用 OCR）
- MOBI 不支持（引导用户用 Calibre 转 EPUB）
- FATAL 级迁移失败项会打标"已完成"，需手动删除标记重试
