# 源文件格式扩展 · 设计文档

**Change ID**: `source-format-support-expansion`
**日期**: 2026-04-20
**状态**: Design (Approved)
**关联提案**: `docs/source-format-support-expansion-proposal.md`

---

## 1. 背景与目标

ArcReel 当前 `ALLOWED_EXTENSIONS["source"]` 声明支持 `.txt / .md / .doc / .docx`，但下游所有消费者（`ProjectManager._read_source_files`、agent skill `normalize_drama_script.py` / `split_episode.py` / `peek_split_point.py`、`analyze-characters-clues` agent）均通过硬编码 `encoding="utf-8"` 读取，且 `_read_source_files` 用 `try/except Exception` 静默跳过失败文件。三类直接故障：

1. **非 UTF-8 编码 `.txt` 触发"源目录为空"误导性错误**：中文小说常见的 GBK / GB18030 / Big5 / UTF-16 编码读取时抛 `UnicodeDecodeError` 被静默吞掉，`source_content` 最终为空字符串，`generate_overview()` 抛"source 目录为空"——文件明明在，用户根本无法定位真实原因。
2. **`.docx` 上传成功但解析必定失败**：`ALLOWED_EXTENSIONS` 接收 `.docx`，但 `_read_source_files` 的 `glob` 过滤器只取 `.txt/.md`，且没有任何转换逻辑。
3. **电子书格式完全缺失**：EPUB 是网文主流分发格式，文字型 PDF 是小说常见载体，用户必须先手动转码，转码过程又容易踩编码坑。

**根本原因**：缺少统一的源文件加载层，每个消费者各自裸读文件。

**目标**：以最小侵入面建立一次性的"上传时规范化"层，把多格式 → UTF-8 纯文本转换收敛到上传路由内完成，下游消费者零感知。

## 2. 范围与非目标

### 2.1 本次范围

- 一次性 ship 5 种格式：`.txt`、`.md`、`.docx`、`.epub`、`.pdf`
- 移除 `.doc`（二进制 Word，需 150 MB 系统依赖，存量极少；UI 引导用户转 `.docx`）
- 新增 `lib/source_loader/` 包，引入 `FormatExtractor` 协议
- 上传路由集成规范化、文件名冲突协商、原文件备份
- 启动时执行幂等历史项目编码迁移
- 前端：扩展 `accept`、新增冲突确认弹窗、原文件下载入口、首次上传自动触发分析

### 2.2 非目标（明确 YAGNI）

- 扫描版 PDF 的 OCR
- `.doc` / MOBI（前者依赖重，后者 Amazon 已弃用，UI 引导用 Calibre 转 EPUB）
- 异步任务队列化上传（50MB 限制下同步即可，需求超标再迁）
- LLM 智能识别章节边界（C 方案的章节标记由 extractor 直出，不调 LLM）
- chardet 7.0（AI 重写引发 relicense 争议，观望半年再评）

## 3. 架构总览

新增 `lib/source_loader/` 包，作为"上传时一次性规范化"的独立模块，与 `server/routers/files.py`、`lib/project_manager.py` **单向解耦**——上传路由调它，读路径完全不感知它。

```
lib/source_loader/
├── __init__.py          # 导出 SourceLoader, NormalizeResult, Errors
├── base.py              # 协议：FormatExtractor（抽象接口）
├── txt.py               # charset-normalizer + 四层解码
├── docx.py              # docx2txt → mammoth 兜底
├── epub.py              # ebooklib + BeautifulSoup4 + 章节标记注入
├── pdf.py               # PyMuPDFExtractor（AGPL 主线；商业 fork 可换 pypdfium2）
├── loader.py            # SourceLoader.load(path, dst_dir) 编排 + 分发
└── errors.py            # SourceLoaderError 体系
```

### 3.1 职责边界

- **`FormatExtractor` 协议**：`extract(path: Path) -> ExtractedText`，每种格式一个实现，**只负责把原文件读成结构化文本**，不负责 IO 写入、不负责错误翻译
- **`SourceLoader.load(src, dst_dir) -> NormalizeResult`**：唯一对外入口，分发到对应 extractor → 写 `dst_dir/<stem>.txt` → 决定是否备份 `raw/` → 返回 `NormalizeResult(normalized_path, raw_path or None, used_encoding, chapter_count)`
- **错误 i18n 映射**在 `server/routers/files.py` 完成，`source_loader/` 保持 i18n 无关，方便测试与复用
- **冲突检测**在 `loader.py`，路由层只决定 HTTP 状态码

### 3.2 为什么这样切

- extractor 协议化让"商业 fork 用 pypdfium2 替代 PyMuPDF"就是新增一个实现 + 配置切换，业务零改
- `errors.py` 单独成文件，路由层 `isinstance` 分派 HTTP 状态更清晰
- 不把"写入 `source/`"塞进 extractor，是为了让 extractor 可独立单测（给 `.docx` 路径，断言返回结构化文本），不需要文件系统上下文

## 4. 关键决策

### 决策 1：上传时规范化 vs 延迟解析

**选择**：上传时立即转 `.txt` 并持久化，原文件保留在 `source/raw/`。

**原因**：下游 `read_text()` 调用点多（5+ 处），逐个改造风险高；解析是 CPU 密集操作，上传时一次做避免重复消耗；规范化产物便于用户直接查看和调试字符切分。

### 决策 2：分格式专用库 vs 通用方案

**选择**：专用库组合（charset-normalizer / docx2txt / mammoth / ebooklib / PyMuPDF）。

**原因**：markitdown 输出带 Markdown 标记污染字符计数，且不支持 mobi；docling 镜像 1-9 GB 不适配 2vCPU/4GB droplet。专用库组合总 ~50–60 MB，零系统依赖，中文质量最佳。

### 决策 3：PDF 走抽象接口，主线用 PyMuPDF

**选择**：抽象 `PDFExtractor` 协议，主线 `PyMuPDFExtractor`，预留 `PyPDFium2Extractor` 供商业 fork。

**原因**：PyMuPDF 中文质量和速度双冠（200 页 1–2s vs pypdf 10–30s），且与 ArcReel 主线 AGPL-3.0 完全兼容；商业 fork 通过 adapter 切换 pypdfium2（Apache-2.0），无需重构业务代码。

### 决策 4：解码失败显式抛错，不再静默跳过

**选择**：`SourceLoader` 检测到解码失败时抛 `SourceDecodeError`，携具体文件名与尝试过的编码列表；`_read_source_files` 移除 `try/except Exception` 静默跳过逻辑。

**原因**：当前"静默跳过 + 最终报'源目录为空'"严重误导用户，是本次问题直接起因。改为显式失败后用户能立即定位真实原因。兜底路径（`gb18030 + errors="replace"`）保证极端情况下仍能拿到文本，只在日志记录 replace 次数供 QA。

### 决策 5：EPUB / PDF 输出粒度——单文件 + 章节标记

**选择**：EPUB / PDF 全文拼接成单个 `<stem>.txt`，但在章节边界插入 `\n\n# {章节标题}\n\n` 标记。

**原因**：完全向后兼容下游 `split_episode.py` 的"单文件按字数切"模型；同时保留 EPUB 自带结构信息给用户参考；避免"每章一集还是多章合一集"的语义模糊。EPUB 章节标题取自 `nav.xhtml` / `toc.ncx`，取不到退化为 `# 第 N 章`。PDF 无可靠章节信号，仅做页间双换行。

### 决策 6：文件名冲突——前端二次确认

**选择**：后端默认 `on_conflict=fail` 返回 409 + 建议名；前端弹模态让用户选「替换 / 保留两者 / 取消」。

**原因**：直接覆盖会丢用户数据；直接拒绝打断流程；自动改名会污染下游（`_read_source_files` 把两份都拼进 overview）。二次确认在低频冲突场景下成本可接受。

### 决策 7：原文件备份策略

**选择**：

- 非 UTF-8 `.txt` / `.md` 与所有 `.docx` / `.epub` / `.pdf` → 备份到 `source/raw/`
- 已是 UTF-8 的 `.txt` / `.md` → **不备份**（零损解码，备份意义不大且占空间）
- 删除规范化 `.txt` 时**级联删除**对应 `raw/` 中同 stem 文件
- 前端文件侧栏**不显示 raw/**，但在文件条目上加"📎 原始格式"按钮跳转下载接口

### 决策 8：存量项目编码迁移——启动时自动执行

**选择**：`server/main.py` lifespan startup hook 内执行幂等迁移，单项目失败隔离不阻塞 server 启动。

**原因**：用户零感知；幂等标记保证不重复执行；失败隔离保证 server 总能拉起；标记按项目粒度，支持从备份恢复旧项目后自动重跑。

### 决策 9：首次上传后自动触发分析

**选择**：`WelcomeCanvas.processFile` 在上传前 `phase === "idle"` 时（首次上传，0 → 1），上传成功后自动调 `startAnalysis()`。后续追加文件仍走"has_sources → 手动点击"，不打断多文件上传节奏。

**原因**：减少首次上手摩擦；保留多文件场景的用户控制权。

## 5. 数据流

### 5.1 上传链路

```
[Frontend WelcomeCanvas]
    │  POST /projects/{name}/upload/source  (default on_conflict=fail)
    ▼
[server/routers/files.py · upload_file]
    │  1. 校验扩展名 ∈ {.txt, .md, .docx, .epub, .pdf}
    │  2. 校验文件大小 ≤ SOURCE_MAX_UPLOAD_BYTES (默认 50 MB)
    │  3. 计算目标 stem，预检冲突：
    │     - source/<stem>.txt 存在？
    │     - source/raw/<original_filename> 存在？
    │     若冲突且 on_conflict=fail → 409 + {existing, suggested_name: "<stem>_1"}
    │  4. 写入临时文件 → asyncio.to_thread(SourceLoader.load, tmp, project_dir/"source")
    │  5. SourceLoader 内部：
    │     a) 分发 extractor 抽文本
    │     b) EPUB: 章节间注入 "\n\n# {章节标题}\n\n"
    │     c) 写 source/<stem>.txt (UTF-8)
    │     d) 决定是否备份 → 写 source/raw/<original_filename>
    │  6. 返回 {filename, path, url, normalized: true,
    │           original_kept: bool, used_encoding, chapter_count}
    ▼
[Frontend Toast]
    "已规范化为 UTF-8 文本（检测到 GBK / 含 12 章 / ...）"
    └─ 若 phase 进入此次上传前为 idle → 自动调 startAnalysis()
```

### 5.2 冲突分支

```
Frontend POST → 409 {existing, suggested_name}
  │
  ▼
ConflictModal: "novel.txt 已存在"
  ├─ [替换]      → POST 重发 ?on_conflict=replace
  ├─ [保留两者] → POST 重发 ?on_conflict=rename  (后端用 suggested_name 自动改名)
  └─ [取消]      → 关闭，stays in idle/has_sources
```

### 5.3 下游读路径（验证零改动）

- `lib/project_manager.py:_read_source_files` 仍 `glob("*")` 仅含顶层 `.txt/.md`，`source/raw/` 子目录天然被 `is_file()` 过滤
- **唯一改动**：移除 `try/except Exception` 静默跳过，遇到非 UTF-8 直接抛 `SourceDecodeError`（升级后理论上不会发生——上传已规范化、启动迁移已修历史；若仍发生说明用户绕过 API 直接拷文件，需要显式报错）
- agent 脚本 `normalize_drama_script.py` / `split_episode.py` / `peek_split_point.py` 全部不动——它们读的就是已规范化的 UTF-8 `.txt`

## 6. 各格式解码细节

### 6.1 TXT / MD 四层解码

```python
def decode_txt(raw: bytes) -> tuple[str, str]:
    # 1. BOM 优先（UTF-8-SIG / UTF-16 LE/BE）
    if raw.startswith(b'\xef\xbb\xbf'):
        return raw[3:].decode('utf-8'), 'utf-8-sig'
    if raw.startswith(b'\xff\xfe'):
        return raw[2:].decode('utf-16-le'), 'utf-16-le'
    if raw.startswith(b'\xfe\xff'):
        return raw[2:].decode('utf-16-be'), 'utf-16-be'
    # 2. 严格 UTF-8
    try:
        return raw.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        pass
    # 3. charset-normalizer 概率检测（仅接受 chaos < 0.5）
    best = charset_normalizer.from_bytes(raw).best()
    if best and best.chaos < 0.5:
        try:
            return raw.decode(best.encoding), best.encoding
        except UnicodeDecodeError:
            pass
    # 4. 兜底 gb18030 + errors='replace'
    decoded = raw.decode('gb18030', errors='replace')
    replace_count = decoded.count('\ufffd')
    if len(decoded) and replace_count / len(decoded) > 0.05:  # >5% 乱码 → 判定失败
        raise SourceDecodeError(tried=['utf-8', best.encoding if best else None, 'gb18030'])
    if replace_count:
        logger.warning("gb18030 fallback with %d replacements", replace_count)
    return decoded, 'gb18030-lossy'
```

### 6.2 DOCX

- 主路径 `docx2txt.process(path)`（段落保留好）
- 失败回退 `mammoth.convert_to_markdown(path)`（更健壮但产物含 MD 标记，需正则去 `**`/`__`/`#` 标记）
- 两者都失败 → `CorruptFileError("DOCX 解析失败：xxx")`

### 6.3 EPUB

- `ebooklib.epub.read_epub(path)` → 按 `book.spine` 顺序遍历 `ITEM_DOCUMENT`
- 每项 `BeautifulSoup(html, 'lxml').get_text('\n')`
- 章节标题来源三级退化：
  1. `book.toc` → 项 `title`
  2. `nav.xhtml` 内 `<a>` 文本
  3. `f"第 {idx + 1} 章"`
- 在每章内容前插入 `\n\n# {标题}\n\n`
- `chapter_count` 写入 `NormalizeResult` 给前端 Toast 展示

### 6.4 PDF（PyMuPDFExtractor）

- `fitz.open(path)` → 遍历 `page.get_text("text")` → 页间双换行 `\n\n`
- **扫描件检测**：若全文 `len(text.strip()) / pages < 50 字符/页` → `CorruptFileError("疑似扫描版 PDF，需 OCR，本次不支持")`
- 不尝试章节切分（PDF 无可靠章节信号），由用户自行用 `split_episode.py`

## 7. 错误处理与 i18n

所有异常继承 `SourceLoaderError`，路由层映射：

| 异常 | HTTP | i18n key (errors.py) |
|---|---|---|
| `UnsupportedFormatError` | 400 | `source_unsupported_format` |
| `SourceDecodeError` | 422 | `source_decode_failed`（携 `tried_encodings`） |
| `CorruptFileError` | 422 | `source_corrupt_file`（携 `reason`） |
| `FileSizeExceededError` | 413 | `source_too_large`（携 `limit_mb`） |

**后端 i18n**：`lib/i18n/zh/errors.py` 与 `lib/i18n/en/errors.py` 同步新增 key（与现有 `Translator` 依赖注入风格一致）。`_read_source_files` 内抛 `SourceDecodeError` 复用同一条 i18n，所以概述生成失败时前端看到的是「`X.txt` 解码失败（已尝试 utf-8, gbk, gb18030）」，不再是误导的「源目录为空」。

**前端 i18n**：`frontend/src/i18n/{zh,en}/errors.ts` 与 `common.ts` 同步新增对应翻译。

## 8. 启动时迁移

```python
# server/main.py · lifespan startup
async def migrate_source_encoding_on_startup():
    projects_root = PROJECT_ROOT / "projects"
    if not projects_root.exists():
        return
    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir():
            continue
        marker = project_dir / ".arcreel" / "source_encoding_migrated"
        if marker.exists():
            continue
        try:
            await asyncio.to_thread(_migrate_project_source_encoding, project_dir)
            marker.parent.mkdir(exist_ok=True)
            marker.touch()
        except Exception:
            logger.exception(
                "源文件编码迁移失败 project=%s，已跳过，server 继续启动",
                project_dir.name,
            )
            # 失败明细写 project_dir / ".arcreel" / "migration_errors.log"
```

`_migrate_project_source_encoding(project_dir)`：

1. 扫 `project_dir / "source" / *.{txt,md}`（仅顶层）
2. 对每个文件：先尝试 `read_bytes() + utf-8 strict decode` —— 成功即跳过（已是 UTF-8）
3. 失败 → 走 `SourceLoader.txt.decode_txt()` 四层解码 → 备份原文件到 `source/raw/<filename>` → 覆写为 UTF-8
4. 任意单文件失败 → 写 `migration_errors.log`，继续下一个

特性：

- 幂等（按项目打标，删标记可手动重跑）
- 单项目失败不影响其它项目、不阻塞 server 启动
- 一般项目 <1s
- release notes 写明"首次启动会执行一次源文件编码规范化，日志会汇总需人工处理的文件"

## 9. 前端变更

| 文件 | 改动 |
|---|---|
| `frontend/src/components/canvas/WelcomeCanvas.tsx` | (a) `accept=".txt,.md,.docx,.epub,.pdf"`；(b) `handleDrop` 文件后缀校验同步扩展；(c) `processFile` 末尾：若上传前 `phase === "idle"`，上传成功后**自动调 `startAnalysis()`** 代替进入 `has_sources`；(d) 上传成功 Toast 展示 `normalized + used_encoding + chapter_count` |
| `frontend/src/components/canvas/ConflictModal.tsx` | **新增**：props `{existing, suggested_name, original_filename}`；三按钮 `[替换 / 保留两者 / 取消]`；用户选择后 resolver Promise 返回策略串 |
| `frontend/src/api.ts` | `uploadSourceFile(projectName, file, onConflict?)` 新增 `onConflict` 参数；遇 409 抛带 `suggestedName` 的结构化错误供调用方捕获 |
| `frontend/src/components/layout/AssetSidebar.tsx` | source 文件条目新增"📎 原始格式"按钮（仅当 `original_kept=true`），点击 `window.open('/api/v1/files/{project}/source/raw/{original_filename}')` |
| `frontend/src/i18n/{zh,en}/common.ts` | 新 key：`conflict_modal_title` / `conflict_modal_desc` / `keep_both` / `replace` / `cancel` / `normalized_from_encoding` / `epub_chapters_detected` / `download_original` |
| `frontend/src/i18n/{zh,en}/errors.ts` | 新 key：`source_unsupported_format` / `source_decode_failed` / `source_corrupt_file` / `source_too_large` |

前端**不需要**轮询或 SSE——规范化在上传路由同步 `asyncio.to_thread` 里完成，50MB 以内典型延迟 1–3s，与 `style-image` LLM 分析等同量级。

## 10. 测试矩阵

```
tests/source_loader/
├── conftest.py
├── fixtures/
│   ├── utf8_novel.txt              # 零边界 UTF-8
│   ├── gbk_novel.txt               # 简体 GBK
│   ├── big5_novel.txt              # 繁体 Big5
│   ├── utf16le_novel.txt           # UTF-16 LE (BOM)
│   ├── corrupt_bytes.txt           # 随机字节 → 预期 SourceDecodeError
│   ├── sample.docx                 # 含标题 / 段落 / 表格
│   ├── sample.epub                 # 5 章，含封面 / toc.ncx
│   ├── sample_text.pdf             # 文字型
│   └── sample_scanned.pdf          # 扫描件 → 预期 CorruptFileError
├── test_txt.py                     # 四层解码路径全覆盖
├── test_docx.py                    # docx2txt 主路径 + mammoth 兜底
├── test_epub.py                    # 章节标题注入 / spine 顺序 / 无 toc 退化
├── test_pdf.py                     # 文字型解析 + 扫描件判定
├── test_loader_conflict.py         # stem 冲突 → 409 / rename 建议
├── test_loader_raw_backup.py       # UTF-8 .txt 跳过 raw / GBK .txt 写 raw
└── test_migration.py               # 启动迁移幂等 / 失败隔离 / 标记机制

tests/
├── test_files_router.py            # 扩展：新格式上传 / 冲突流程 / 原文件下载 / 级联删除
└── test_project_manager.py         # _read_source_files 去掉 try/except 后非 UTF-8 抛 SourceDecodeError

frontend/src/components/canvas/
├── WelcomeCanvas.test.tsx          # 新增："idle → upload → 自动调 onAnalyze"、
│                                   #       "has_sources → 追加上传 → 不调 onAnalyze"
└── ConflictModal.test.tsx          # 新增：三按钮分支与 resolver Promise
```

**覆盖率目标**：`lib/source_loader/` ≥ 90%，CI `--cov-fail-under` 提档。

## 11. 依赖变更

通过 `uv add` 一次性引入，自动写入 `pyproject.toml` 与 `uv.lock`，版本号取当时 PyPI 最新稳定版（不预先 pin minimum，避免人为下限与 lockfile 漂移）：

```bash
uv add charset-normalizer docx2txt mammoth ebooklib beautifulsoup4 lxml pymupdf
```

预期总体积 ~50–60 MB，全部纯 Python / 预编译 wheel，**零系统依赖**。引入后通过 `uv lock --check` 与本地 `uv run python -m pytest tests/source_loader/` 验证安装链路与导入。

## 12. 风险与缓解

| 风险 | 缓解 |
|---|---|
| PyMuPDF AGPL 传染商业 fork | `FormatExtractor` 协议化，商业 fork 切 `pypdfium2` 成本 <50 行 |
| charset-normalizer 对短文本误判 | `chaos < 0.5` 门槛 + `gb18030 + replace` 兜底 + `>5% 乱码判定失败` 三道防线 |
| 50MB PDF 解析阻塞 event loop | `asyncio.to_thread` + 上传侧强制 50MB 硬限 + `> 20MB` 时前端进度提示 |
| 启动迁移误改用户文件 | 非 UTF-8 检测失败即放弃、原文件备份到 `raw/`、失败明细写 log 便于回滚 |
| EPUB 章节标题乱码 / 缺失 | `toc → nav.xhtml → 硬编码 "第 N 章"` 三级退化 |
| MOBI 用户体验落差 | 上传 UI 新增引导文案："MOBI 请先用 Calibre 转 EPUB" |
| `_read_source_files` 移除静默跳过后历史项目报错 | 启动迁移 + 错误消息明确化（不再误导为"目录为空"） |
| 扫描版 PDF 用户期望落差 | 显式 `CorruptFileError("疑似扫描版，需 OCR")`，UI 引导文案说明 OCR 不在范围 |

## 13. 影响清单

- **新增依赖**：`charset-normalizer` / `docx2txt` / `mammoth` / `ebooklib` / `beautifulsoup4` / `lxml` / `pymupdf`（合计 ~50–60 MB，零系统依赖）
- **新增文件**：
  - `lib/source_loader/` 整个包（`base.py` / `txt.py` / `docx.py` / `epub.py` / `pdf.py` / `loader.py` / `errors.py`）
  - `tests/source_loader/` 含 fixtures 与全部单测
  - `frontend/src/components/canvas/ConflictModal.tsx`
- **修改文件**：
  - `server/routers/files.py`：上传路由集成 SourceLoader、冲突 409；`DELETE` 路由扩展级联删除 `source/raw/<同 stem>.*`。原文件下载**复用现有** `GET /files/{project}/{path:path}`（`source/raw/<filename>` 子路径已被既有路径校验覆盖，无需新增路由）
  - `lib/project_manager.py:_read_source_files`：移除 `try/except Exception` 静默跳过
  - `server/main.py`：lifespan startup 增加 `migrate_source_encoding_on_startup()`
  - `pyproject.toml`：新依赖
  - `frontend/src/components/canvas/WelcomeCanvas.tsx`：扩展 accept、首次上传自动分析、Toast 展示规范化结果
  - `frontend/src/components/layout/AssetSidebar.tsx`：原始格式下载按钮
  - `frontend/src/api.ts`：`onConflict` 参数与 409 错误结构化
  - `frontend/src/i18n/{zh,en}/{common,errors}.json`：新 key
- **不受影响**：下游 agent 脚本（`normalize_drama_script.py` / `peek_split_point.py` / `split_episode.py` / `_text_utils.py`）、subagent prompt、数据模型、项目结构

## 14. 能力声明（OpenSpec）

### New Capabilities

- `source-file-parsing`：多格式源文件统一解析层，输出 UTF-8 纯文本，解码失败显式报错，支持原文件归档与冲突协商

### Modified Capabilities

- `file-upload`：`ALLOWED_EXTENSIONS["source"]` 扩展为 `[".txt", ".md", ".docx", ".epub", ".pdf"]`（移除 `.doc`），上传后自动转换为规范化 UTF-8 `.txt`；新增 `on_conflict` 参数与 409 协商
