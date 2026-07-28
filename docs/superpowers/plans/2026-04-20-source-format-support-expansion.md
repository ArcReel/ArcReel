# 源文件格式扩展 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 ArcReel 上传入口增加 `.txt / .md / .docx / .epub / .pdf` 多格式支持并一次性规范化为 UTF-8 文本，下游消费者零改动；新增前端冲突协商弹窗、首次上传自动触发分析、原始格式下载入口；启动时幂等迁移历史项目编码。

**Architecture:** 新增 `lib/source_loader/` 包（`FormatExtractor` 协议 + 5 种实现 + `SourceLoader` 编排），上传路由集成；`lib/project_manager.py:_read_source_files` 移除 `try/except` 静默跳过；`server/app.py` lifespan 增加幂等启动迁移；前端新增 `ConflictModal` 与 `WelcomeCanvas` 自动分析行为。

**Tech Stack:** Python 3.12 / FastAPI / pytest（asyncio_mode=auto，覆盖率 ≥80%）/ React 19 + TS / Vitest / pnpm；新依赖通过 `uv add` 引入：`charset-normalizer / docx2txt / mammoth / ebooklib / beautifulsoup4 / lxml / pymupdf`。

**Reference Spec:** `docs/superpowers/specs/2026-04-20-source-format-support-expansion-design.md`

---

## 文件结构（决策锁定）

### 后端新增

| 路径 | 责任 |
|---|---|
| `lib/source_loader/__init__.py` | 公开 API：导出 `SourceLoader / NormalizeResult` 与所有 errors |
| `lib/source_loader/errors.py` | 异常体系：`SourceLoaderError` / `UnsupportedFormatError` / `SourceDecodeError` / `CorruptFileError` / `FileSizeExceededError` / `ConflictError` |
| `lib/source_loader/base.py` | `FormatExtractor` Protocol + `ExtractedText` / `NormalizeResult` dataclass |
| `lib/source_loader/txt.py` | `TxtExtractor`：4 层解码（BOM → UTF-8 → charset-normalizer → gb18030+replace） |
| `lib/source_loader/docx.py` | `DocxExtractor`：`docx2txt` → `mammoth` 兜底 |
| `lib/source_loader/epub.py` | `EpubExtractor`：`ebooklib` + 章节标题三级退化 + `# 标题` 注入 |
| `lib/source_loader/pdf.py` | `PyMuPDFExtractor`：`fitz` + 扫描件检测 |
| `lib/source_loader/loader.py` | `SourceLoader.load()` 编排：分发 extractor + 写入 + raw 备份决策 + 冲突检测 |
| `lib/source_loader/migration.py` | `migrate_project_source_encoding(project_dir)` 历史项目重编码 |

### 后端修改

| 路径 | 改动 |
|---|---|
| `server/routers/files.py` | `ALLOWED_EXTENSIONS["source"]` 扩展、`upload_file` 集成 SourceLoader 与冲突 409、`delete_source_file` 级联 raw |
| `server/app.py` | lifespan 启动钩子调用 `migrate_source_encoding_on_startup` |
| `lib/project_manager.py` | `_read_source_files` 移除静默跳过，遇非 UTF-8 抛 `SourceDecodeError` |
| `lib/i18n/zh/errors.py` / `lib/i18n/en/errors.py` | 新增 i18n key |

### 测试新增

| 路径 | 覆盖 |
|---|---|
| `tests/source_loader/conftest.py` | 共享 fixtures（运行期构造 EPUB/PDF 等） |
| `tests/source_loader/fixtures/` | 静态二进制 fixture（DOCX 等） |
| `tests/source_loader/test_errors.py` | 异常携带字段断言 |
| `tests/source_loader/test_txt.py` | 4 层解码全路径覆盖 |
| `tests/source_loader/test_docx.py` | docx2txt 主路径 + mammoth 兜底 |
| `tests/source_loader/test_epub.py` | 章节注入 / spine 顺序 / 无 toc 退化 |
| `tests/source_loader/test_pdf.py` | 文字型抽取 + 扫描件检测 |
| `tests/source_loader/test_loader.py` | 编排 / raw 备份 / 冲突检测 / 多格式分发 |
| `tests/source_loader/test_migration.py` | 启动迁移幂等 / 失败隔离 / 标记 |
| `tests/test_files_router.py`（扩展） | 新格式上传 / 冲突 409 / 级联删除 |
| `tests/test_project_manager_more.py`（扩展） | `_read_source_files` 抛 `SourceDecodeError` |

### 前端新增 / 修改

| 路径 | 改动 |
|---|---|
| `frontend/src/components/canvas/ConflictModal.tsx` | 新增组件，三按钮 |
| `frontend/src/components/canvas/ConflictModal.test.tsx` | 新增测试 |
| `frontend/src/components/canvas/WelcomeCanvas.tsx` | `accept` 扩展、首次上传自动分析、错误时弹冲突弹窗 |
| `frontend/src/components/canvas/WelcomeCanvas.test.tsx` | 新增 / 扩展用例 |
| `frontend/src/components/canvas/OverviewCanvas.tsx` | `handleUpload` 接入冲突协商 |
| `frontend/src/components/layout/AssetSidebar.tsx` | 文件条目"📎 原始格式"按钮 |
| `frontend/src/api.ts` | `uploadFile` 增加 `onConflict` 参数；`ConflictError` 类导出；新增 `getOriginalSourceUrl` 辅助 |
| `frontend/src/api.test.ts` | 上传 + 冲突测试 |
| `frontend/src/i18n/{zh,en}/common.ts` | 新 key |
| `frontend/src/i18n/{zh,en}/errors.ts` | 新 key |
| `frontend/src/i18n/{zh,en}/dashboard.ts` | 上传 toast 文案微调 |

---

## Task 1：引入 Python 依赖

**Files:**
- Modify: `pyproject.toml`（自动）/ `uv.lock`（自动）

- [ ] **Step 1: 通过 uv add 一次性安装**

```bash
uv add charset-normalizer docx2txt mammoth ebooklib beautifulsoup4 lxml pymupdf
```

预期：`pyproject.toml` 的 `[project] dependencies` 多出 7 个条目，`uv.lock` 更新；命令打印 `Resolved N packages`。

- [ ] **Step 2: 验证可导入**

```bash
uv run python -c "import charset_normalizer, docx2txt, mammoth, ebooklib, bs4, lxml, fitz; print('ok')"
```

预期：输出 `ok`。

- [ ] **Step 3: 验证 lockfile 一致性**

```bash
uv lock --check
```

预期：`Resolved` / 无 diff。

- [ ] **Step 4: 提交**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): 新增源文件解析依赖（charset-normalizer / docx2txt / mammoth / ebooklib / bs4 / lxml / pymupdf）"
```

---

## Task 2：errors 异常体系

**Files:**
- Create: `lib/source_loader/__init__.py`
- Create: `lib/source_loader/errors.py`
- Create: `tests/source_loader/__init__.py`
- Create: `tests/source_loader/test_errors.py`

- [ ] **Step 1: 写失败测试 `tests/source_loader/test_errors.py`**

```python
from lib.source_loader.errors import (
    SourceLoaderError,
    UnsupportedFormatError,
    SourceDecodeError,
    CorruptFileError,
    FileSizeExceededError,
    ConflictError,
)


def test_unsupported_format_error_carries_ext():
    err = UnsupportedFormatError(ext=".doc")
    assert isinstance(err, SourceLoaderError)
    assert err.ext == ".doc"
    assert ".doc" in str(err)


def test_source_decode_error_carries_filename_and_tried():
    err = SourceDecodeError(filename="novel.txt", tried_encodings=["utf-8", "gbk"])
    assert err.filename == "novel.txt"
    assert err.tried_encodings == ["utf-8", "gbk"]
    assert "novel.txt" in str(err)
    assert "utf-8" in str(err)


def test_corrupt_file_error_carries_reason():
    err = CorruptFileError(filename="x.pdf", reason="scanned")
    assert err.filename == "x.pdf"
    assert err.reason == "scanned"


def test_file_size_exceeded_error_carries_sizes():
    err = FileSizeExceededError(filename="big.pdf", size_bytes=60_000_000, limit_bytes=50_000_000)
    assert err.size_bytes == 60_000_000
    assert err.limit_bytes == 50_000_000


def test_conflict_error_carries_existing_and_suggested():
    err = ConflictError(existing="novel.txt", suggested_name="novel_1")
    assert err.existing == "novel.txt"
    assert err.suggested_name == "novel_1"
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run python -m pytest tests/source_loader/test_errors.py -v
```

预期：`ModuleNotFoundError: No module named 'lib.source_loader'`

- [ ] **Step 3: 创建 `lib/source_loader/__init__.py`（暂留空）**

```python
"""源文件解析与规范化层。

上传路由调用 SourceLoader.load() 把 .txt/.md/.docx/.epub/.pdf 转成 UTF-8 纯文本，
下游消费者零感知。
"""
```

- [ ] **Step 4: 实现 `lib/source_loader/errors.py`**

```python
"""SourceLoader 异常体系。

路由层根据异常类型映射到不同 HTTP 状态：
- UnsupportedFormatError → 400
- SourceDecodeError      → 422
- CorruptFileError       → 422
- FileSizeExceededError  → 413
- ConflictError          → 409
"""


class SourceLoaderError(Exception):
    pass


class UnsupportedFormatError(SourceLoaderError):
    def __init__(self, ext: str):
        self.ext = ext
        super().__init__(f"Unsupported source format: {ext}")


class SourceDecodeError(SourceLoaderError):
    def __init__(self, filename: str, tried_encodings: list[str | None]):
        self.filename = filename
        self.tried_encodings = [e for e in tried_encodings if e]
        super().__init__(
            f"Failed to decode {filename} (tried: {', '.join(self.tried_encodings) or 'n/a'})"
        )


class CorruptFileError(SourceLoaderError):
    def __init__(self, filename: str, reason: str):
        self.filename = filename
        self.reason = reason
        super().__init__(f"{filename} corrupt or unreadable: {reason}")


class FileSizeExceededError(SourceLoaderError):
    def __init__(self, filename: str, size_bytes: int, limit_bytes: int):
        self.filename = filename
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"{filename} ({size_bytes} bytes) exceeds limit ({limit_bytes} bytes)"
        )


class ConflictError(SourceLoaderError):
    def __init__(self, existing: str, suggested_name: str):
        self.existing = existing
        self.suggested_name = suggested_name
        super().__init__(f"File conflict: {existing}; suggested rename: {suggested_name}")
```

- [ ] **Step 5: 创建 tests 目录与初始化**

```bash
mkdir -p tests/source_loader/fixtures
touch tests/source_loader/__init__.py
```

- [ ] **Step 6: 运行测试确认通过**

```bash
uv run python -m pytest tests/source_loader/test_errors.py -v
```

预期：5 个测试全部 PASS。

- [ ] **Step 7: lint + format**

```bash
uv run ruff check lib/source_loader tests/source_loader && uv run ruff format lib/source_loader tests/source_loader
```

- [ ] **Step 8: 提交**

```bash
git add lib/source_loader tests/source_loader
git commit -m "feat(source_loader): 异常体系骨架（SourceLoaderError 与 5 类子异常）"
```

---

## Task 3：base 协议与数据类

**Files:**
- Create: `lib/source_loader/base.py`
- Create: `tests/source_loader/test_base.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/source_loader/test_base.py
from pathlib import Path

from lib.source_loader.base import ExtractedText, FormatExtractor, NormalizeResult


def test_extracted_text_defaults():
    e = ExtractedText(text="hello")
    assert e.text == "hello"
    assert e.used_encoding is None
    assert e.chapter_count == 0


def test_normalize_result_required_and_defaults():
    r = NormalizeResult(
        normalized_path=Path("/p/source/a.txt"),
        raw_path=None,
        used_encoding="utf-8",
        chapter_count=0,
        original_filename="a.txt",
    )
    assert r.normalized_path.name == "a.txt"
    assert r.raw_path is None
    assert r.used_encoding == "utf-8"
    assert r.chapter_count == 0
    assert r.original_filename == "a.txt"


def test_format_extractor_protocol_runtime_check():
    class _Stub:
        def extract(self, path: Path) -> ExtractedText:
            return ExtractedText(text="x")

    stub = _Stub()
    # Protocol 仅做静态检查；运行期通过 hasattr 验证 duck-typing
    assert hasattr(stub, "extract")
    assert isinstance(stub.extract(Path(".")), ExtractedText)
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run python -m pytest tests/source_loader/test_base.py -v
```

预期：`ModuleNotFoundError: No module named 'lib.source_loader.base'`

- [ ] **Step 3: 实现 `lib/source_loader/base.py`**

```python
"""协议与数据类。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class ExtractedText:
    text: str
    used_encoding: str | None = None
    chapter_count: int = 0


@dataclass
class NormalizeResult:
    normalized_path: Path
    raw_path: Path | None
    used_encoding: str | None
    chapter_count: int
    original_filename: str


class FormatExtractor(Protocol):
    def extract(self, path: Path) -> ExtractedText: ...
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run python -m pytest tests/source_loader/test_base.py -v
```

预期：3 个测试 PASS。

- [ ] **Step 5: lint + format + 提交**

```bash
uv run ruff check lib/source_loader tests/source_loader && uv run ruff format lib/source_loader tests/source_loader
git add lib/source_loader/base.py tests/source_loader/test_base.py
git commit -m "feat(source_loader): FormatExtractor 协议与数据类"
```

---

## Task 4：TXT / MD 四层解码

**Files:**
- Create: `lib/source_loader/txt.py`
- Create: `tests/source_loader/test_txt.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/source_loader/test_txt.py
import pytest

from lib.source_loader.errors import SourceDecodeError
from lib.source_loader.txt import TxtExtractor, decode_txt


def test_decode_utf8_bom():
    raw = b"\xef\xbb\xbf\xe4\xbd\xa0\xe5\xa5\xbd"  # "你好"
    text, enc = decode_txt(raw)
    assert text == "你好"
    assert enc == "utf-8-sig"


def test_decode_utf16_le_bom():
    raw = "\u4f60\u597d".encode("utf-16-le")
    raw = b"\xff\xfe" + raw
    text, enc = decode_txt(raw)
    assert text == "你好"
    assert enc == "utf-16-le"


def test_decode_utf16_be_bom():
    raw = "\u4f60\u597d".encode("utf-16-be")
    raw = b"\xfe\xff" + raw
    text, enc = decode_txt(raw)
    assert text == "你好"
    assert enc == "utf-16-be"


def test_decode_pure_utf8_no_bom():
    raw = "中文小说内容".encode("utf-8")
    text, enc = decode_txt(raw)
    assert text == "中文小说内容"
    assert enc == "utf-8"


def test_decode_gbk_via_charset_normalizer():
    raw = ("第一章 起点。" * 50).encode("gbk")
    text, enc = decode_txt(raw)
    assert "起点" in text
    # charset-normalizer 通常返回 gbk / gb18030 / cp936 之一
    assert enc and enc.lower() in {"gbk", "gb18030", "cp936"}


def test_decode_big5_via_charset_normalizer():
    raw = ("第一章 起點。" * 50).encode("big5")
    text, enc = decode_txt(raw)
    assert "起點" in text
    assert enc and "big5" in enc.lower()


def test_decode_random_bytes_raises():
    raw = bytes(range(256)) * 200
    with pytest.raises(SourceDecodeError) as exc_info:
        decode_txt(raw)
    assert "utf-8" in exc_info.value.tried_encodings
    assert "gb18030" in exc_info.value.tried_encodings


def test_extractor_writes_via_decode(tmp_path):
    src = tmp_path / "novel.txt"
    src.write_bytes("内容".encode("gbk"))
    result = TxtExtractor().extract(src)
    assert "内容" in result.text
    assert result.used_encoding and result.used_encoding.lower() in {"gbk", "gb18030", "cp936"}
    assert result.chapter_count == 0
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run python -m pytest tests/source_loader/test_txt.py -v
```

预期：`ModuleNotFoundError`。

- [ ] **Step 3: 实现 `lib/source_loader/txt.py`**

```python
"""TXT/MD 文本解码：4 层策略。

1. BOM 优先（UTF-8-SIG / UTF-16 LE/BE）
2. 严格 UTF-8
3. charset-normalizer 概率检测（chaos < 0.5）
4. gb18030 + errors='replace' 兜底（>5% 乱码判定失败）
"""

import logging
from pathlib import Path

import charset_normalizer

from .base import ExtractedText
from .errors import SourceDecodeError

logger = logging.getLogger(__name__)

_REPLACE_THRESHOLD = 0.05


def decode_txt(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le"), "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be"), "utf-16-be"

    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    best = charset_normalizer.from_bytes(raw).best()
    detected_enc: str | None = None
    if best is not None and best.chaos is not None and best.chaos < 0.5 and best.encoding:
        detected_enc = best.encoding
        try:
            return raw.decode(best.encoding), best.encoding
        except (UnicodeDecodeError, LookupError):
            pass

    decoded = raw.decode("gb18030", errors="replace")
    if decoded:
        replace_ratio = decoded.count("\ufffd") / len(decoded)
    else:
        replace_ratio = 0.0
    if replace_ratio > _REPLACE_THRESHOLD:
        raise SourceDecodeError(
            filename="<bytes>",
            tried_encodings=["utf-8", detected_enc, "gb18030"],
        )
    if "\ufffd" in decoded:
        logger.warning(
            "gb18030 fallback with %d replacements (ratio=%.4f)",
            decoded.count("\ufffd"),
            replace_ratio,
        )
    return decoded, "gb18030-lossy"


class TxtExtractor:
    def extract(self, path: Path) -> ExtractedText:
        raw = path.read_bytes()
        try:
            text, enc = decode_txt(raw)
        except SourceDecodeError as exc:
            raise SourceDecodeError(filename=path.name, tried_encodings=exc.tried_encodings) from exc
        return ExtractedText(text=text, used_encoding=enc, chapter_count=0)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run python -m pytest tests/source_loader/test_txt.py -v
```

预期：8 个测试 PASS。

- [ ] **Step 5: lint + format + 提交**

```bash
uv run ruff check lib/source_loader/txt.py tests/source_loader/test_txt.py && uv run ruff format lib/source_loader/txt.py tests/source_loader/test_txt.py
git add lib/source_loader/txt.py tests/source_loader/test_txt.py
git commit -m "feat(source_loader): TXT/MD 四层解码（BOM → UTF-8 → charset-normalizer → gb18030 兜底）"
```

---

## Task 5：DOCX 抽取

**Files:**
- Create: `lib/source_loader/docx.py`
- Create: `tests/source_loader/conftest.py`
- Create: `tests/source_loader/test_docx.py`

- [ ] **Step 1: 写 `tests/source_loader/conftest.py` 提供 docx fixture**

我们用 `python-docx` 程序化构造 docx，避免在仓库提交二进制（已经间接依赖 `python-docx`，因为 docx2txt 自身处理 zip+xml；如果未安装，使用 `zipfile` 直接构造最小合法 docx）。优先尝试 python-docx，缺失时跳过测试。

```python
# tests/source_loader/conftest.py
"""共享 fixtures：尽量在运行期构造测试样本，避免二进制入库。"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def docx_factory(tmp_path: Path):
    """构造一个含两段文本的 .docx；无 python-docx 时跳过。"""
    docx_mod = pytest.importorskip("docx", reason="需要 python-docx 构造 fixture")

    def _make(paragraphs: list[str], filename: str = "sample.docx") -> Path:
        doc = docx_mod.Document()
        for p in paragraphs:
            doc.add_paragraph(p)
        out = tmp_path / filename
        doc.save(out)
        return out

    return _make
```

- [ ] **Step 2: 加 `python-docx` 为开发依赖**

```bash
uv add --dev python-docx
```

预期：`pyproject.toml` 的 `[dependency-groups]` 或 `[tool.uv]` dev 段新增 `python-docx`。

- [ ] **Step 3: 写失败测试**

```python
# tests/source_loader/test_docx.py
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
```

- [ ] **Step 4: 运行确认失败**

```bash
uv run python -m pytest tests/source_loader/test_docx.py -v
```

预期：`ModuleNotFoundError: lib.source_loader.docx`。

- [ ] **Step 5: 实现 `lib/source_loader/docx.py`**

```python
"""DOCX 抽取：docx2txt 主路径 + mammoth 兜底。"""

import logging
import re
from pathlib import Path

import docx2txt
import mammoth

from .base import ExtractedText
from .errors import CorruptFileError

logger = logging.getLogger(__name__)

# mammoth 输出的 Markdown 语法标记：仅去除会污染字符计数的标记，保留段落结构
_MD_MARK_PATTERN = re.compile(r"(\*\*|__|\#{1,6}\s+|`+)")


def _strip_markdown_marks(text: str) -> str:
    return _MD_MARK_PATTERN.sub("", text)


class DocxExtractor:
    def extract(self, path: Path) -> ExtractedText:
        try:
            text = docx2txt.process(str(path)) or ""
            if text.strip():
                return ExtractedText(text=text, used_encoding=None, chapter_count=0)
            logger.warning("docx2txt 返回空文本，回退到 mammoth: %s", path.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("docx2txt 抽取失败 (%s)，回退到 mammoth: %s", exc, path.name)

        try:
            with path.open("rb") as fh:
                result = mammoth.convert_to_markdown(fh)
            md_text = _strip_markdown_marks(result.value or "")
            if not md_text.strip():
                raise CorruptFileError(filename=path.name, reason="DOCX 抽取结果为空")
            return ExtractedText(text=md_text, used_encoding=None, chapter_count=0)
        except CorruptFileError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CorruptFileError(filename=path.name, reason=f"DOCX 解析失败: {exc}") from exc
```

- [ ] **Step 6: 运行测试**

```bash
uv run python -m pytest tests/source_loader/test_docx.py -v
```

预期：2 个测试 PASS。

- [ ] **Step 7: lint + format + 提交**

```bash
uv run ruff check lib/source_loader/docx.py tests/source_loader && uv run ruff format lib/source_loader/docx.py tests/source_loader
git add lib/source_loader/docx.py tests/source_loader/conftest.py tests/source_loader/test_docx.py pyproject.toml uv.lock
git commit -m "feat(source_loader): DOCX 抽取（docx2txt 主路径 + mammoth 兜底）"
```

---

## Task 6：EPUB 抽取与章节标题注入

**Files:**
- Create: `lib/source_loader/epub.py`
- Modify: `tests/source_loader/conftest.py`
- Create: `tests/source_loader/test_epub.py`

- [ ] **Step 1: 在 conftest 增加 EPUB fixture**

向 `tests/source_loader/conftest.py` 追加：

```python
@pytest.fixture
def epub_factory(tmp_path: Path):
    """构造一个含 N 章 + toc 的 .epub。"""
    ebooklib = pytest.importorskip("ebooklib", reason="需要 ebooklib")
    from ebooklib import epub

    def _make(
        chapter_titles_and_bodies: list[tuple[str, str]],
        filename: str = "sample.epub",
        with_toc: bool = True,
    ) -> Path:
        book = epub.EpubBook()
        book.set_identifier("test-id")
        book.set_title("Test Book")
        book.set_language("zh")

        chapters = []
        for idx, (title, body) in enumerate(chapter_titles_and_bodies, start=1):
            ch = epub.EpubHtml(
                title=title,
                file_name=f"chap_{idx}.xhtml",
                lang="zh",
            )
            ch.content = f"<html><body><h1>{title}</h1><p>{body}</p></body></html>"
            book.add_item(ch)
            chapters.append(ch)

        if with_toc:
            book.toc = tuple(chapters)
            book.add_item(epub.EpubNcx())
            book.add_item(epub.EpubNav())
            book.spine = ["nav", *chapters]
        else:
            # 不写入 toc / nav；spine 直接列章节
            book.spine = list(chapters)

        out = tmp_path / filename
        epub.write_epub(out, book)
        return out

    return _make
```

- [ ] **Step 2: 写失败测试**

```python
# tests/source_loader/test_epub.py
from lib.source_loader.epub import EpubExtractor


def test_epub_injects_chapter_markers_and_counts(epub_factory):
    src = epub_factory(
        [
            ("第一章 起点", "第一章正文内容。"),
            ("第二章 转折", "第二章正文内容。"),
            ("第三章 终局", "第三章正文内容。"),
        ]
    )
    result = EpubExtractor().extract(src)
    assert result.chapter_count == 3
    assert "# 第一章 起点" in result.text
    assert "# 第二章 转折" in result.text
    assert "# 第三章 终局" in result.text
    # 章节顺序与 spine 一致
    pos1 = result.text.find("第一章正文")
    pos2 = result.text.find("第二章正文")
    pos3 = result.text.find("第三章正文")
    assert 0 < pos1 < pos2 < pos3


def test_epub_falls_back_to_index_when_no_toc(epub_factory):
    src = epub_factory(
        [
            ("不会被使用的标题1", "正文1。"),
            ("不会被使用的标题2", "正文2。"),
        ],
        with_toc=False,
    )
    result = EpubExtractor().extract(src)
    assert result.chapter_count == 2
    # 没有 toc → 标题降级为 "第 N 章"
    assert "# 第 1 章" in result.text
    assert "# 第 2 章" in result.text
```

- [ ] **Step 3: 运行确认失败**

```bash
uv run python -m pytest tests/source_loader/test_epub.py -v
```

预期：`ModuleNotFoundError`。

- [ ] **Step 4: 实现 `lib/source_loader/epub.py`**

```python
"""EPUB 抽取：按 spine 顺序遍历章节，注入 # 标题 标记。"""

import logging
from pathlib import Path

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from .base import ExtractedText
from .errors import CorruptFileError

logger = logging.getLogger(__name__)


def _resolve_titles(book: epub.EpubBook, doc_items: list) -> list[str]:
    """三级退化：toc → nav.xhtml → "第 N 章"。"""
    by_href: dict[str, str] = {}

    def _walk(toc):
        for entry in toc:
            if isinstance(entry, tuple):
                section, children = entry
                if hasattr(section, "href") and section.href:
                    by_href[section.href.split("#")[0]] = section.title
                _walk(children)
            elif hasattr(entry, "href") and entry.href:
                by_href[entry.href.split("#")[0]] = entry.title

    if book.toc:
        _walk(book.toc)

    titles: list[str] = []
    for idx, item in enumerate(doc_items, start=1):
        title = by_href.get(item.file_name)
        if not title:
            title = f"第 {idx} 章"
        titles.append(title)
    return titles


class EpubExtractor:
    def extract(self, path: Path) -> ExtractedText:
        try:
            book = epub.read_epub(str(path))
        except Exception as exc:  # noqa: BLE001
            raise CorruptFileError(filename=path.name, reason=f"EPUB 解析失败: {exc}") from exc

        # 按 spine 顺序拿到 ITEM_DOCUMENT
        spine_ids = [s[0] for s in book.spine]
        items_by_id = {it.id: it for it in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)}
        doc_items = [items_by_id[sid] for sid in spine_ids if sid in items_by_id]

        # 跳过 nav 项（idref="nav"）
        doc_items = [it for it in doc_items if it.id != "nav"]
        if not doc_items:
            raise CorruptFileError(filename=path.name, reason="EPUB 不含正文章节")

        titles = _resolve_titles(book, doc_items)

        parts: list[str] = []
        for title, item in zip(titles, doc_items, strict=True):
            soup = BeautifulSoup(item.get_content(), "lxml")
            body_text = soup.get_text("\n").strip()
            parts.append(f"\n\n# {title}\n\n{body_text}")

        return ExtractedText(
            text="\n".join(parts).lstrip(),
            used_encoding=None,
            chapter_count=len(doc_items),
        )
```

- [ ] **Step 5: 运行测试**

```bash
uv run python -m pytest tests/source_loader/test_epub.py -v
```

预期：2 个测试 PASS。

- [ ] **Step 6: lint + format + 提交**

```bash
uv run ruff check lib/source_loader/epub.py tests/source_loader && uv run ruff format lib/source_loader/epub.py tests/source_loader
git add lib/source_loader/epub.py tests/source_loader/conftest.py tests/source_loader/test_epub.py
git commit -m "feat(source_loader): EPUB 抽取（spine 顺序 + 章节标题三级退化注入）"
```

---

## Task 7：PDF 抽取（PyMuPDF）

**Files:**
- Create: `lib/source_loader/pdf.py`
- Modify: `tests/source_loader/conftest.py`
- Create: `tests/source_loader/test_pdf.py`

- [ ] **Step 1: 在 conftest 增加 PDF fixture（用 PyMuPDF 现场构造）**

```python
@pytest.fixture
def pdf_factory(tmp_path: Path):
    """构造文字型或扫描型 PDF。"""
    fitz = pytest.importorskip("fitz", reason="需要 PyMuPDF")

    def _make(pages_text: list[str], filename: str = "sample.pdf") -> Path:
        doc = fitz.open()
        for body in pages_text:
            page = doc.new_page()
            page.insert_text((72, 72), body, fontsize=12)
        out = tmp_path / filename
        doc.save(out)
        doc.close()
        return out

    def _make_scanned(num_pages: int, filename: str = "scanned.pdf") -> Path:
        # 仅插入空白页 → 模拟扫描件无文本
        doc = fitz.open()
        for _ in range(num_pages):
            doc.new_page()
        out = tmp_path / filename
        doc.save(out)
        doc.close()
        return out

    _make.scanned = _make_scanned
    return _make
```

- [ ] **Step 2: 写失败测试**

```python
# tests/source_loader/test_pdf.py
import pytest

from lib.source_loader.errors import CorruptFileError
from lib.source_loader.pdf import PyMuPDFExtractor


def test_pdf_extracts_text(pdf_factory):
    src = pdf_factory(["这是第一页内容。", "这是第二页内容。"])
    result = PyMuPDFExtractor().extract(src)
    assert "第一页" in result.text
    assert "第二页" in result.text
    # 页间双换行
    assert "\n\n" in result.text
    assert result.chapter_count == 0


def test_pdf_scanned_raises(pdf_factory):
    src = pdf_factory.scanned(num_pages=3)
    with pytest.raises(CorruptFileError) as exc_info:
        PyMuPDFExtractor().extract(src)
    assert "扫描" in exc_info.value.reason or "OCR" in exc_info.value.reason
```

- [ ] **Step 3: 运行确认失败**

```bash
uv run python -m pytest tests/source_loader/test_pdf.py -v
```

预期：`ModuleNotFoundError`。

- [ ] **Step 4: 实现 `lib/source_loader/pdf.py`**

```python
"""PDF 抽取：PyMuPDF 主线，扫描件检测后明确报错。"""

import logging
from pathlib import Path

import fitz  # PyMuPDF

from .base import ExtractedText
from .errors import CorruptFileError

logger = logging.getLogger(__name__)

_SCANNED_CHARS_PER_PAGE = 50


class PyMuPDFExtractor:
    def extract(self, path: Path) -> ExtractedText:
        try:
            doc = fitz.open(str(path))
        except Exception as exc:  # noqa: BLE001
            raise CorruptFileError(filename=path.name, reason=f"PDF 打开失败: {exc}") from exc

        try:
            pages_text: list[str] = []
            for page in doc:
                pages_text.append(page.get_text("text"))
        finally:
            doc.close()

        full = "\n\n".join(pages_text).strip()
        page_count = max(len(pages_text), 1)

        if len(full) / page_count < _SCANNED_CHARS_PER_PAGE:
            raise CorruptFileError(
                filename=path.name,
                reason="疑似扫描版 PDF，需 OCR，本次不支持",
            )

        return ExtractedText(text=full, used_encoding=None, chapter_count=0)
```

- [ ] **Step 5: 运行测试**

```bash
uv run python -m pytest tests/source_loader/test_pdf.py -v
```

预期：2 个测试 PASS。

- [ ] **Step 6: lint + format + 提交**

```bash
uv run ruff check lib/source_loader/pdf.py tests/source_loader && uv run ruff format lib/source_loader/pdf.py tests/source_loader
git add lib/source_loader/pdf.py tests/source_loader/conftest.py tests/source_loader/test_pdf.py
git commit -m "feat(source_loader): PDF 抽取（PyMuPDF + 扫描件检测）"
```

---

## Task 8：SourceLoader 编排（分发 / 写入 / raw 备份 / 冲突）

**Files:**
- Create: `lib/source_loader/loader.py`
- Modify: `lib/source_loader/__init__.py`
- Create: `tests/source_loader/test_loader.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/source_loader/test_loader.py
from pathlib import Path

import pytest

from lib.source_loader import SourceLoader
from lib.source_loader.errors import (
    ConflictError,
    FileSizeExceededError,
    UnsupportedFormatError,
)


def test_load_txt_utf8_no_raw_backup(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    src = tmp_path / "novel.txt"
    src.write_bytes("纯 UTF-8 内容".encode("utf-8"))

    result = SourceLoader.load(src, project_source, original_filename="novel.txt")
    assert result.normalized_path == project_source / "novel.txt"
    assert result.normalized_path.read_text(encoding="utf-8") == "纯 UTF-8 内容"
    assert result.raw_path is None
    assert result.used_encoding == "utf-8"
    assert result.original_filename == "novel.txt"


def test_load_gbk_txt_writes_raw_backup(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    src = tmp_path / "old_novel.txt"
    src.write_bytes(("第一章\n" * 30).encode("gbk"))

    result = SourceLoader.load(src, project_source, original_filename="old_novel.txt")
    assert result.normalized_path.read_text(encoding="utf-8").startswith("第一章")
    assert result.raw_path == project_source / "raw" / "old_novel.txt"
    assert result.raw_path.read_bytes().startswith(b"\xb5\xda")  # GBK "第"
    assert result.used_encoding and result.used_encoding.lower() != "utf-8"


def test_load_docx_writes_raw_backup(tmp_path: Path, docx_factory):
    project_source = tmp_path / "source"
    project_source.mkdir()
    src = docx_factory(["docx 内容"])

    result = SourceLoader.load(src, project_source, original_filename=src.name)
    assert result.normalized_path == project_source / src.with_suffix(".txt").name
    assert "docx 内容" in result.normalized_path.read_text(encoding="utf-8")
    assert result.raw_path == project_source / "raw" / src.name
    assert result.raw_path.exists()


def test_load_unsupported_format_raises(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    src = tmp_path / "x.doc"
    src.write_bytes(b"binary")
    with pytest.raises(UnsupportedFormatError) as exc_info:
        SourceLoader.load(src, project_source, original_filename="x.doc")
    assert exc_info.value.ext == ".doc"


def test_load_size_limit_raises(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    src = tmp_path / "big.txt"
    src.write_bytes(b"a" * 100)
    with pytest.raises(FileSizeExceededError):
        SourceLoader.load(
            src, project_source, original_filename="big.txt", max_bytes=50
        )


def test_detect_conflict_finds_existing_normalized(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    (project_source / "novel.txt").write_text("已存在", encoding="utf-8")

    has_conflict, suggested = SourceLoader.detect_conflict("novel.epub", project_source)
    assert has_conflict is True
    assert suggested == "novel_1"


def test_detect_conflict_finds_existing_raw(tmp_path: Path):
    project_source = tmp_path / "source"
    (project_source / "raw").mkdir(parents=True)
    (project_source / "raw" / "novel.epub").write_bytes(b"raw")

    has_conflict, suggested = SourceLoader.detect_conflict("novel.epub", project_source)
    assert has_conflict is True
    assert suggested == "novel_1"


def test_detect_conflict_no_conflict(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    has_conflict, suggested = SourceLoader.detect_conflict("novel.epub", project_source)
    assert has_conflict is False
    assert suggested == "novel"


def test_load_on_conflict_fail_raises(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    (project_source / "novel.txt").write_text("已存在", encoding="utf-8")

    src = tmp_path / "novel.txt"
    src.write_bytes("新内容".encode("utf-8"))
    with pytest.raises(ConflictError) as exc_info:
        SourceLoader.load(
            src, project_source, original_filename="novel.txt", on_conflict="fail"
        )
    assert exc_info.value.suggested_name == "novel_1"


def test_load_on_conflict_replace_overwrites(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    (project_source / "novel.txt").write_text("旧内容", encoding="utf-8")

    src = tmp_path / "novel.txt"
    src.write_bytes("新内容".encode("utf-8"))
    result = SourceLoader.load(
        src, project_source, original_filename="novel.txt", on_conflict="replace"
    )
    assert result.normalized_path.read_text(encoding="utf-8") == "新内容"


def test_load_on_conflict_rename_uses_suggested(tmp_path: Path):
    project_source = tmp_path / "source"
    project_source.mkdir()
    (project_source / "novel.txt").write_text("已存在", encoding="utf-8")

    src = tmp_path / "novel.txt"
    src.write_bytes("新内容".encode("utf-8"))
    result = SourceLoader.load(
        src, project_source, original_filename="novel.txt", on_conflict="rename"
    )
    assert result.normalized_path == project_source / "novel_1.txt"
    assert result.original_filename == "novel_1.txt"


def test_load_chapter_count_propagates_from_epub(tmp_path: Path, epub_factory):
    project_source = tmp_path / "source"
    project_source.mkdir()
    src = epub_factory([("第一章", "正文1"), ("第二章", "正文2")])
    result = SourceLoader.load(src, project_source, original_filename=src.name)
    assert result.chapter_count == 2
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run python -m pytest tests/source_loader/test_loader.py -v
```

预期：`ModuleNotFoundError`。

- [ ] **Step 3: 实现 `lib/source_loader/loader.py`**

```python
"""SourceLoader：编排各 extractor，处理冲突、raw 备份与原子写入。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from .base import ExtractedText, NormalizeResult
from .docx import DocxExtractor
from .epub import EpubExtractor
from .errors import (
    ConflictError,
    FileSizeExceededError,
    UnsupportedFormatError,
)
from .pdf import PyMuPDFExtractor
from .txt import TxtExtractor

OnConflict = Literal["fail", "replace", "rename"]

_EXTRACTORS = {
    ".txt": TxtExtractor,
    ".md": TxtExtractor,
    ".docx": DocxExtractor,
    ".epub": EpubExtractor,
    ".pdf": PyMuPDFExtractor,
}


class SourceLoader:
    SUPPORTED_EXTS = frozenset(_EXTRACTORS.keys())
    DEFAULT_MAX_BYTES = 50 * 1024 * 1024

    @classmethod
    def detect_conflict(
        cls, original_filename: str, dst_dir: Path
    ) -> tuple[bool, str]:
        """返回 (has_conflict, suggested_stem).

        冲突条件：
        - dst_dir/<stem>.txt 存在
        - dst_dir/raw/<original_filename> 存在
        suggested_stem 从 stem_1, stem_2, ... 递增到不冲突为止。
        """
        stem = Path(original_filename).stem
        normalized = dst_dir / f"{stem}.txt"
        raw = dst_dir / "raw" / original_filename

        if not normalized.exists() and not raw.exists():
            return False, stem

        idx = 1
        while True:
            candidate_stem = f"{stem}_{idx}"
            candidate_norm = dst_dir / f"{candidate_stem}.txt"
            candidate_raw = (
                dst_dir / "raw" / f"{candidate_stem}{Path(original_filename).suffix}"
            )
            if not candidate_norm.exists() and not candidate_raw.exists():
                return True, candidate_stem
            idx += 1

    @classmethod
    def load(
        cls,
        src: Path,
        dst_dir: Path,
        *,
        original_filename: str | None = None,
        on_conflict: OnConflict = "fail",
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> NormalizeResult:
        original_filename = original_filename or src.name
        ext = Path(original_filename).suffix.lower()

        if ext not in cls.SUPPORTED_EXTS:
            raise UnsupportedFormatError(ext=ext)

        size = src.stat().st_size
        if size > max_bytes:
            raise FileSizeExceededError(
                filename=original_filename, size_bytes=size, limit_bytes=max_bytes
            )

        # 冲突协商
        has_conflict, suggested_stem = cls.detect_conflict(original_filename, dst_dir)
        target_stem = Path(original_filename).stem
        effective_filename = original_filename
        if has_conflict:
            if on_conflict == "fail":
                raise ConflictError(
                    existing=f"{target_stem}.txt", suggested_name=suggested_stem
                )
            if on_conflict == "rename":
                target_stem = suggested_stem
                effective_filename = f"{suggested_stem}{ext}"
            # on_conflict == "replace" → 沿用原 stem，覆盖

        extracted = _EXTRACTORS[ext]().extract(src)
        normalized_path = dst_dir / f"{target_stem}.txt"
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_path.write_text(extracted.text, encoding="utf-8")

        raw_path = cls._maybe_backup_raw(
            src=src,
            ext=ext,
            extracted=extracted,
            dst_dir=dst_dir,
            effective_filename=effective_filename,
        )

        return NormalizeResult(
            normalized_path=normalized_path,
            raw_path=raw_path,
            used_encoding=extracted.used_encoding,
            chapter_count=extracted.chapter_count,
            original_filename=effective_filename,
        )

    @staticmethod
    def _maybe_backup_raw(
        *,
        src: Path,
        ext: str,
        extracted: ExtractedText,
        dst_dir: Path,
        effective_filename: str,
    ) -> Path | None:
        # 决策 7：纯 UTF-8 .txt/.md 不备份；其余一律备份
        if ext in {".txt", ".md"} and extracted.used_encoding == "utf-8":
            return None
        raw_dir = dst_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / effective_filename
        shutil.copyfile(src, raw_path)
        return raw_path
```

- [ ] **Step 4: 更新 `lib/source_loader/__init__.py`**

```python
"""源文件解析与规范化层。

上传路由调用 SourceLoader.load() 把 .txt/.md/.docx/.epub/.pdf 转成 UTF-8 纯文本，
下游消费者零感知。
"""

from .base import ExtractedText, FormatExtractor, NormalizeResult
from .errors import (
    ConflictError,
    CorruptFileError,
    FileSizeExceededError,
    SourceDecodeError,
    SourceLoaderError,
    UnsupportedFormatError,
)
from .loader import OnConflict, SourceLoader

__all__ = [
    "ConflictError",
    "CorruptFileError",
    "ExtractedText",
    "FileSizeExceededError",
    "FormatExtractor",
    "NormalizeResult",
    "OnConflict",
    "SourceDecodeError",
    "SourceLoader",
    "SourceLoaderError",
    "UnsupportedFormatError",
]
```

- [ ] **Step 5: 运行测试**

```bash
uv run python -m pytest tests/source_loader/test_loader.py -v
```

预期：12 个测试 PASS。

- [ ] **Step 6: 全包覆盖率体检**

```bash
uv run python -m pytest tests/source_loader/ --cov=lib.source_loader --cov-report=term-missing -v
```

预期：`lib/source_loader/*` 行覆盖 ≥ 90%。

- [ ] **Step 7: lint + format + 提交**

```bash
uv run ruff check lib/source_loader && uv run ruff format lib/source_loader tests/source_loader
git add lib/source_loader/loader.py lib/source_loader/__init__.py tests/source_loader/test_loader.py
git commit -m "feat(source_loader): SourceLoader 编排（分发 + raw 备份 + 冲突协商）"
```

---

## Task 9：历史项目编码迁移

**Files:**
- Create: `lib/source_loader/migration.py`
- Create: `tests/source_loader/test_migration.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/source_loader/test_migration.py
from pathlib import Path

from lib.source_loader.migration import migrate_project_source_encoding


def _make_project(tmp_path: Path, name: str) -> Path:
    project_dir = tmp_path / name
    (project_dir / "source").mkdir(parents=True)
    return project_dir


def test_migration_rewrites_non_utf8_txt_in_place(tmp_path: Path):
    project = _make_project(tmp_path, "p1")
    target = project / "source" / "novel.txt"
    target.write_bytes(("第一章\n" * 30).encode("gbk"))

    summary = migrate_project_source_encoding(project)
    assert summary.migrated == [target.name]
    assert summary.failed == []
    assert target.read_text(encoding="utf-8").startswith("第一章")
    # 原文件备份到 source/raw/
    assert (project / "source" / "raw" / "novel.txt").exists()


def test_migration_skips_already_utf8(tmp_path: Path):
    project = _make_project(tmp_path, "p2")
    target = project / "source" / "novel.txt"
    target.write_text("已是 UTF-8", encoding="utf-8")

    summary = migrate_project_source_encoding(project)
    assert summary.migrated == []
    assert summary.skipped == [target.name]
    # 原内容不变
    assert target.read_text(encoding="utf-8") == "已是 UTF-8"
    # 不创建 raw 备份
    assert not (project / "source" / "raw").exists()


def test_migration_records_failures_without_raising(tmp_path: Path):
    project = _make_project(tmp_path, "p3")
    bad = project / "source" / "garbage.txt"
    bad.write_bytes(bytes(range(256)) * 200)

    summary = migrate_project_source_encoding(project)
    assert summary.failed == [bad.name]
    # 文件未被改动
    assert bad.read_bytes().startswith(bytes([0, 1, 2, 3]))


def test_migration_no_source_dir_is_noop(tmp_path: Path):
    project = tmp_path / "empty_project"
    project.mkdir()
    summary = migrate_project_source_encoding(project)
    assert summary.migrated == []
    assert summary.skipped == []
    assert summary.failed == []
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run python -m pytest tests/source_loader/test_migration.py -v
```

预期：`ModuleNotFoundError`。

- [ ] **Step 3: 实现 `lib/source_loader/migration.py`**

```python
"""历史项目源文件编码迁移。

启动时由 server/app.py lifespan 调用：扫描 projects/<name>/source/*.{txt,md}，
非 UTF-8 文件用 SourceLoader 重编码并备份原文件到 source/raw/。
单文件失败被记录，不影响其它文件 / 项目 / server 启动。
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .errors import SourceDecodeError
from .txt import decode_txt

logger = logging.getLogger(__name__)


@dataclass
class MigrationSummary:
    migrated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def migrate_project_source_encoding(project_dir: Path) -> MigrationSummary:
    summary = MigrationSummary()
    source_dir = project_dir / "source"
    if not source_dir.exists():
        return summary

    for file_path in sorted(source_dir.iterdir()):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in {".txt", ".md"}:
            continue

        raw_bytes = file_path.read_bytes()
        try:
            raw_bytes.decode("utf-8")
            summary.skipped.append(file_path.name)
            continue
        except UnicodeDecodeError:
            pass

        try:
            text, used_enc = decode_txt(raw_bytes)
        except SourceDecodeError as exc:
            logger.warning(
                "迁移失败：无法解码 %s（尝试 %s）",
                file_path,
                ", ".join(exc.tried_encodings),
            )
            summary.failed.append(file_path.name)
            continue

        backup_dir = source_dir / "raw"
        backup_dir.mkdir(exist_ok=True)
        backup_path = backup_dir / file_path.name
        if not backup_path.exists():
            shutil.copyfile(file_path, backup_path)
        file_path.write_text(text, encoding="utf-8")
        logger.info(
            "迁移成功：%s（%s → utf-8），原文件备份到 %s",
            file_path,
            used_enc,
            backup_path,
        )
        summary.migrated.append(file_path.name)

    return summary
```

- [ ] **Step 4: 运行测试**

```bash
uv run python -m pytest tests/source_loader/test_migration.py -v
```

预期：4 个测试 PASS。

- [ ] **Step 5: lint + format + 提交**

```bash
uv run ruff check lib/source_loader/migration.py tests/source_loader/test_migration.py && uv run ruff format lib/source_loader/migration.py tests/source_loader/test_migration.py
git add lib/source_loader/migration.py tests/source_loader/test_migration.py
git commit -m "feat(source_loader): 历史项目源文件编码迁移辅助函数"
```

---

## Task 10：后端 i18n 新增 key

**Files:**
- Modify: `lib/i18n/zh/errors.py`
- Modify: `lib/i18n/en/errors.py`
- Test: `tests/test_i18n_consistency.py`（既有 CI 校验）

- [ ] **Step 1: 在 `lib/i18n/zh/errors.py` MESSAGES 字典内 Files 段后追加**

```python
    # Source loader
    "source_unsupported_format": "不支持的源文件格式：{ext}（支持 .txt / .md / .docx / .epub / .pdf）",
    "source_decode_failed": "源文件「{filename}」解码失败（已尝试：{tried}）",
    "source_corrupt_file": "源文件「{filename}」无法解析：{reason}",
    "source_too_large": "源文件「{filename}」过大（{size_mb} MB > {limit_mb} MB）",
    "source_conflict": "源文件「{existing}」已存在，建议改名为「{suggested}」",
```

- [ ] **Step 2: 在 `lib/i18n/en/errors.py` 对应位置追加**

```python
    "source_unsupported_format": "Unsupported source format: {ext} (supported: .txt / .md / .docx / .epub / .pdf)",
    "source_decode_failed": "Failed to decode source file '{filename}' (tried: {tried})",
    "source_corrupt_file": "Source file '{filename}' is not parseable: {reason}",
    "source_too_large": "Source file '{filename}' is too large ({size_mb} MB > {limit_mb} MB)",
    "source_conflict": "Source file '{existing}' already exists; suggested rename: '{suggested}'",
```

- [ ] **Step 3: 运行 i18n 一致性测试**

```bash
uv run python -m pytest tests/test_i18n_consistency.py -v
```

预期：PASS（zh / en key 集合一致）。

- [ ] **Step 4: 提交**

```bash
git add lib/i18n
git commit -m "i18n(backend): 源文件解析错误信息（zh/en）"
```

---

## Task 11：files 路由扩展（ALLOWED_EXTENSIONS + upload 集成 SourceLoader）

**Files:**
- Modify: `server/routers/files.py`
- Modify: `tests/test_files_router.py`

- [ ] **Step 1: 检查既有测试结构**

```bash
uv run python -m pytest tests/test_files_router.py -v --collect-only 2>&1 | head -40
```

记下既有 fixture 的项目 / 客户端构造方式。后续测试沿用相同模式。

- [ ] **Step 2: 写新增的 router 失败测试**

在 `tests/test_files_router.py` 末尾追加：

```python
# ==================== Source 多格式上传 ====================

import io


def _upload_source(client, project_name: str, filename: str, content: bytes, on_conflict: str | None = None):
    url = f"/api/v1/projects/{project_name}/upload/source"
    if on_conflict:
        url += f"?on_conflict={on_conflict}"
    return client.post(
        url,
        files={"file": (filename, io.BytesIO(content), "application/octet-stream")},
    )


def test_upload_source_utf8_txt_normalized(client_with_project):
    client, project_name = client_with_project
    resp = _upload_source(client, project_name, "novel.txt", "纯 UTF-8".encode("utf-8"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["normalized"] is True
    assert body["used_encoding"] == "utf-8"
    assert body["original_kept"] is False
    assert body["chapter_count"] == 0


def test_upload_source_gbk_txt_normalized_and_raw_kept(client_with_project):
    client, project_name = client_with_project
    raw = ("第一章\n" * 30).encode("gbk")
    resp = _upload_source(client, project_name, "old.txt", raw)
    assert resp.status_code == 200
    body = resp.json()
    assert body["normalized"] is True
    assert body["used_encoding"] and body["used_encoding"].lower() != "utf-8"
    assert body["original_kept"] is True


def test_upload_source_doc_rejected_with_400(client_with_project):
    client, project_name = client_with_project
    resp = _upload_source(client, project_name, "x.doc", b"binary")
    assert resp.status_code == 400


def test_upload_source_conflict_returns_409_with_suggestion(client_with_project):
    client, project_name = client_with_project
    _upload_source(client, project_name, "novel.txt", "首次".encode("utf-8"))
    resp = _upload_source(client, project_name, "novel.txt", "再次".encode("utf-8"))
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["existing"] == "novel.txt"
    assert body["detail"]["suggested_name"] == "novel_1"


def test_upload_source_on_conflict_replace(client_with_project):
    client, project_name = client_with_project
    _upload_source(client, project_name, "novel.txt", "旧内容".encode("utf-8"))
    resp = _upload_source(
        client, project_name, "novel.txt", "新内容".encode("utf-8"), on_conflict="replace"
    )
    assert resp.status_code == 200
    # 通过 GET 拉文本验证已替换
    get_resp = client.get(f"/api/v1/projects/{project_name}/source/novel.txt")
    assert get_resp.status_code == 200
    assert get_resp.text == "新内容"


def test_upload_source_on_conflict_rename(client_with_project):
    client, project_name = client_with_project
    _upload_source(client, project_name, "novel.txt", "首次".encode("utf-8"))
    resp = _upload_source(
        client, project_name, "novel.txt", "新版".encode("utf-8"), on_conflict="rename"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "novel_1.txt"


def test_delete_source_cascades_raw(client_with_project, tmp_path):
    client, project_name = client_with_project
    raw = ("第一章\n" * 30).encode("gbk")
    _upload_source(client, project_name, "to_delete.txt", raw)
    # 上传后应当存在 raw 备份
    from lib import PROJECT_ROOT
    raw_path = PROJECT_ROOT / "projects" / project_name / "source" / "raw" / "to_delete.txt"
    assert raw_path.exists()

    resp = client.delete(f"/api/v1/projects/{project_name}/source/to_delete.txt")
    assert resp.status_code == 200
    assert not raw_path.exists()
```

如果既有 `tests/test_files_router.py` 没有 `client_with_project` fixture，先在 `tests/conftest.py` 或本测试文件顶部添加（参考既有 router 测试中的项目创建惯例）。如果项目根使用 `tmp_path` monkeypatch `PROJECT_ROOT`，沿用同样写法。

- [ ] **Step 3: 运行确认失败**

```bash
uv run python -m pytest tests/test_files_router.py -v -k "source"
```

预期：上述 7 个新测试 FAIL（路由仍接受 .doc、不返回 `normalized` 等字段、无 409 行为、删除不级联）。

- [ ] **Step 4: 修改 `server/routers/files.py:37-44` 的 ALLOWED_EXTENSIONS**

```python
# 允许的文件类型
ALLOWED_EXTENSIONS = {
    "source": [".txt", ".md", ".docx", ".epub", ".pdf"],
    "character": [".png", ".jpg", ".jpeg", ".webp"],
    "character_ref": [".png", ".jpg", ".jpeg", ".webp"],
    "scene": [".png", ".jpg", ".jpeg", ".webp"],
    "prop": [".png", ".jpg", ".jpeg", ".webp"],
    "storyboard": [".png", ".jpg", ".jpeg", ".webp"],
}
```

- [ ] **Step 5: 修改 `server/routers/files.py:upload_file` 集成 SourceLoader**

将 `upload_file` 函数签名增加 `on_conflict` query 参数，把 source 分支整体替换为 SourceLoader 调用。完整改写如下：

```python
@router.post("/projects/{project_name}/upload/{upload_type}")
async def upload_file(
    project_name: str,
    upload_type: str,
    _user: CurrentUser,
    _t: Translator,
    file: UploadFile = File(...),
    name: str = None,
    on_conflict: str = "fail",
):
    """
    上传文件

    Args:
        project_name: 项目名称
        upload_type: 上传类型 (source/character/prop/storyboard)
        file: 上传的文件
        name: 可选，用于角色/道具名称，或分镜 ID
        on_conflict: source 类型独有 — fail / replace / rename
    """
    if upload_type not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=_t("invalid_upload_type", upload_type=upload_type))

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS[upload_type]:
        raise HTTPException(
            status_code=400,
            detail=_t("unsupported_image_type", ext=ext, allowed=", ".join(ALLOWED_EXTENSIONS[upload_type])),
        )

    if upload_type == "source":
        return await _handle_source_upload(
            project_name=project_name,
            file=file,
            on_conflict=on_conflict,
            _t=_t,
        )

    # 既有非 source 分支保持不变
    try:
        content = await file.read()

        def _sync():
            # ... 原有逻辑保持不变 ...
            ...

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(_t("server_error") if False else "上传失败"))
```

> ⚠️ 切勿误删既有非 source 分支。建议用最小侵入方式：在原 `upload_file` 函数最开头插入 source 分支早返；其余非 source 代码原样保留。

在文件顶部 import 区追加：

```python
import tempfile
from lib.source_loader import (
    ConflictError,
    CorruptFileError,
    FileSizeExceededError,
    NormalizeResult,
    SourceDecodeError,
    SourceLoader,
    UnsupportedFormatError,
)
```

在文件末尾或合适位置添加 `_handle_source_upload`：

```python
async def _handle_source_upload(
    *,
    project_name: str,
    file: UploadFile,
    on_conflict: str,
    _t: Translator,
):
    if on_conflict not in {"fail", "replace", "rename"}:
        raise HTTPException(status_code=400, detail="on_conflict must be fail/replace/rename")

    try:
        project_dir = get_project_manager().get_project_path(project_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))

    source_dir = project_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    original_filename = file.filename

    def _sync() -> NormalizeResult:
        with tempfile.NamedTemporaryFile(
            suffix=Path(original_filename).suffix, delete=False
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            return SourceLoader.load(
                tmp_path,
                source_dir,
                original_filename=original_filename,
                on_conflict=on_conflict,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    try:
        result = await asyncio.to_thread(_sync)
    except UnsupportedFormatError as exc:
        raise HTTPException(
            status_code=400,
            detail=_t("source_unsupported_format", ext=exc.ext),
        )
    except FileSizeExceededError as exc:
        raise HTTPException(
            status_code=413,
            detail=_t(
                "source_too_large",
                filename=exc.filename,
                size_mb=round(exc.size_bytes / 1024 / 1024, 1),
                limit_mb=round(exc.limit_bytes / 1024 / 1024, 1),
            ),
        )
    except SourceDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=_t(
                "source_decode_failed",
                filename=exc.filename,
                tried=", ".join(exc.tried_encodings),
            ),
        )
    except CorruptFileError as exc:
        raise HTTPException(
            status_code=422,
            detail=_t(
                "source_corrupt_file", filename=exc.filename, reason=exc.reason
            ),
        )
    except ConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "existing": exc.existing,
                "suggested_name": exc.suggested_name,
                "message": _t(
                    "source_conflict",
                    existing=exc.existing,
                    suggested=exc.suggested_name,
                ),
            },
        )

    relative_path = f"source/{result.normalized_path.name}"
    return {
        "success": True,
        "filename": result.normalized_path.name,
        "path": relative_path,
        "url": f"/api/v1/files/{project_name}/{relative_path}",
        "normalized": True,
        "original_kept": result.raw_path is not None,
        "original_filename": result.original_filename,
        "used_encoding": result.used_encoding,
        "chapter_count": result.chapter_count,
    }
```

- [ ] **Step 6: 修改 `delete_source_file` 增加级联**

定位 `server/routers/files.py:381-410` 的 `delete_source_file`，在 `source_path.unlink()` 之后加级联：

```python
            if source_path.exists():
                source_path.unlink()
                # 级联删除原文件备份（同 stem，任意扩展名）
                raw_dir = project_dir / "source" / "raw"
                if raw_dir.exists():
                    stem = source_path.stem
                    for raw_file in raw_dir.iterdir():
                        if raw_file.is_file() and raw_file.stem == stem:
                            raw_file.unlink()
                return {"success": True}
```

- [ ] **Step 7: 运行测试**

```bash
uv run python -m pytest tests/test_files_router.py -v -k "source"
```

预期：7 个新测试 PASS；老测试不退化。

- [ ] **Step 8: 全量 router 测试 + lint**

```bash
uv run python -m pytest tests/test_files_router.py -v
uv run ruff check server/routers/files.py && uv run ruff format server/routers/files.py
```

- [ ] **Step 9: 提交**

```bash
git add server/routers/files.py tests/test_files_router.py
git commit -m "feat(server): files 路由集成 SourceLoader（多格式 / 冲突 409 / 级联删除）"
```

---

## Task 12：移除 `_read_source_files` 静默跳过

**Files:**
- Modify: `lib/project_manager.py:1538-1574`
- Modify: `tests/test_project_manager_more.py`

- [ ] **Step 1: 写失败测试 — 在 `tests/test_project_manager_more.py` 追加**

```python
def test_read_source_files_raises_on_non_utf8(tmp_path, monkeypatch):
    from lib.project_manager import ProjectManager
    from lib.source_loader.errors import SourceDecodeError

    pm = ProjectManager(tmp_path)
    project_dir = tmp_path / "demo"
    (project_dir / "source").mkdir(parents=True)
    bad = project_dir / "source" / "broken.txt"
    bad.write_bytes(bytes(range(256)) * 200)

    with pytest.raises(SourceDecodeError) as exc_info:
        pm._read_source_files("demo")
    assert exc_info.value.filename == "broken.txt"
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run python -m pytest tests/test_project_manager_more.py::test_read_source_files_raises_on_non_utf8 -v
```

预期：当前实现 `try/except Exception` 静默吞，测试 FAIL。

- [ ] **Step 3: 修改 `lib/project_manager.py:1538-1574`**

将 `_read_source_files` 改为：

```python
    def _read_source_files(self, project_name: str, max_chars: int = 50000) -> str:
        """
        读取项目 source 目录下的所有 UTF-8 文本文件内容。

        非 UTF-8 文件会抛 SourceDecodeError —— 上传路径已统一规范化为 UTF-8，
        启动迁移已修历史项目；这里若仍遇到非 UTF-8，说明用户绕过 API 直接拷贝
        文件，需显式报错而非"源目录为空"误导。
        """
        from .source_loader.errors import SourceDecodeError
        from .source_loader.txt import decode_txt

        project_dir = self.get_project_path(project_name)
        source_dir = project_dir / "source"

        if not source_dir.exists():
            return ""

        contents = []
        total_chars = 0
        for file_path in sorted(source_dir.glob("*")):
            if not (file_path.is_file() and file_path.suffix.lower() in [".txt", ".md"]):
                continue

            raw = file_path.read_bytes()
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                # 走 SourceLoader 的解码诊断，把 tried_encodings 带回
                try:
                    decode_txt(raw)
                except SourceDecodeError as decode_exc:
                    raise SourceDecodeError(
                        filename=file_path.name,
                        tried_encodings=decode_exc.tried_encodings,
                    ) from exc
                raise SourceDecodeError(
                    filename=file_path.name,
                    tried_encodings=["utf-8"],
                ) from exc

            remaining = max_chars - total_chars
            if remaining <= 0:
                break
            if len(content) > remaining:
                content = content[:remaining]
            contents.append(f"--- {file_path.name} ---\n{content}")
            total_chars += len(content)

        return "\n\n".join(contents)
```

- [ ] **Step 4: 运行 project_manager 测试**

```bash
uv run python -m pytest tests/test_project_manager_more.py -v
```

预期：所有用例（含新增）PASS。

- [ ] **Step 5: 修改 `generate_overview` 错误转译（可选——若已有 generic ValueError 仍可用就不动）**

打开 `lib/project_manager.py:1576-1592`，**不要**再保留 `if not source_content: raise ValueError("source 目录为空")`——但因为静默跳过已移除，空目录依然合法（用户根本没传文件）。保持原句不变。`SourceDecodeError` 自然透传到调用栈，由路由层转译。

确认搜索：

```bash
grep -n "source 目录为空" lib/project_manager.py
```

预期：仅一处，且行为合理。

- [ ] **Step 6: lint + 提交**

```bash
uv run ruff check lib/project_manager.py tests/test_project_manager_more.py && uv run ruff format lib/project_manager.py tests/test_project_manager_more.py
git add lib/project_manager.py tests/test_project_manager_more.py
git commit -m "fix(project_manager): _read_source_files 移除静默跳过，非 UTF-8 抛 SourceDecodeError"
```

---

## Task 13：startup 钩子执行幂等迁移

**Files:**
- Modify: `server/app.py:57-78`（lifespan 内）
- Create: `tests/test_startup_source_migration.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_startup_source_migration.py
import asyncio
from pathlib import Path

from server.app import _migrate_source_encoding_on_startup  # 即将新增的内部函数


def test_startup_migration_creates_marker_after_run(tmp_path: Path):
    project = tmp_path / "p1"
    (project / "source").mkdir(parents=True)
    (project / "source" / "n.txt").write_bytes(("第一章\n" * 30).encode("gbk"))

    summary = asyncio.run(_migrate_source_encoding_on_startup(tmp_path))

    marker = project / ".arcreel" / "source_encoding_migrated"
    assert marker.exists()
    assert "p1" in summary  # 返回每项目的简报


def test_startup_migration_skips_already_marked(tmp_path: Path):
    project = tmp_path / "p1"
    (project / "source").mkdir(parents=True)
    bad = project / "source" / "n.txt"
    bad.write_bytes(("第一章\n" * 30).encode("gbk"))
    marker_dir = project / ".arcreel"
    marker_dir.mkdir()
    (marker_dir / "source_encoding_migrated").touch()

    asyncio.run(_migrate_source_encoding_on_startup(tmp_path))
    # 文件未被重写（仍是 GBK）
    assert bad.read_bytes().startswith("第一章".encode("gbk"))


def test_startup_migration_isolates_project_failures(tmp_path: Path, monkeypatch):
    good = tmp_path / "good"
    (good / "source").mkdir(parents=True)
    (good / "source" / "ok.txt").write_text("已是 UTF-8", encoding="utf-8")

    bad = tmp_path / "bad"
    (bad / "source").mkdir(parents=True)
    (bad / "source" / "broken.txt").write_bytes(bytes(range(256)) * 200)

    # 即使 bad 项目内文件解码失败，迁移函数本身不应抛错（只记录到 errors.log）
    summary = asyncio.run(_migrate_source_encoding_on_startup(tmp_path))
    assert (good / ".arcreel" / "source_encoding_migrated").exists()
    assert (bad / ".arcreel" / "source_encoding_migrated").exists()
    assert (bad / ".arcreel" / "migration_errors.log").exists()
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run python -m pytest tests/test_startup_source_migration.py -v
```

预期：`ImportError: cannot import name '_migrate_source_encoding_on_startup'`。

- [ ] **Step 3: 在 `server/app.py` 添加内部函数**

在 import 段加：

```python
from lib.source_loader.migration import migrate_project_source_encoding
```

在文件适当位置（lifespan 之前）加：

```python
async def _migrate_source_encoding_on_startup(projects_root: Path) -> dict[str, dict]:
    """对每个项目执行幂等编码迁移。失败被捕获并写日志，不阻塞启动。"""
    summary: dict[str, dict] = {}
    if not projects_root.exists():
        return summary

    def _run_one(project_dir: Path) -> dict:
        marker_dir = project_dir / ".arcreel"
        marker = marker_dir / "source_encoding_migrated"
        if marker.exists():
            return {"skipped": True}
        try:
            result = migrate_project_source_encoding(project_dir)
            marker_dir.mkdir(exist_ok=True)
            marker.touch()
            if result.failed:
                err_log = marker_dir / "migration_errors.log"
                err_log.write_text(
                    "\n".join(f"FAILED: {name}" for name in result.failed) + "\n",
                    encoding="utf-8",
                )
            return {
                "migrated": result.migrated,
                "skipped": result.skipped,
                "failed": result.failed,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "源文件编码迁移失败 project=%s，已跳过，server 继续启动",
                project_dir.name,
            )
            try:
                marker_dir.mkdir(exist_ok=True)
                (marker_dir / "migration_errors.log").write_text(
                    f"FATAL: {exc}\n", encoding="utf-8"
                )
                marker.touch()
            except Exception:  # noqa: BLE001
                pass
            return {"error": str(exc)}

    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        summary[project_dir.name] = await asyncio.to_thread(_run_one, project_dir)
    return summary
```

- [ ] **Step 4: 在 lifespan 注册（紧接 `cleanup_stale_backups` 之后）**

在 `server/app.py` lifespan startup 段，`await asyncio.to_thread(cleanup_stale_backups, projects_root, 7)` 之后加：

```python
    # 源文件编码迁移（幂等；失败不阻塞启动）
    source_migration_summary = await _migrate_source_encoding_on_startup(projects_root)
    migrated_total = sum(
        len(s.get("migrated") or []) for s in source_migration_summary.values()
    )
    failed_total = sum(
        len(s.get("failed") or []) for s in source_migration_summary.values()
    )
    if migrated_total or failed_total:
        logger.info(
            "源文件编码迁移完成：migrated=%d failed=%d projects=%d",
            migrated_total,
            failed_total,
            len(source_migration_summary),
        )
```

- [ ] **Step 5: 运行测试**

```bash
uv run python -m pytest tests/test_startup_source_migration.py -v
```

预期：3 个测试 PASS。

- [ ] **Step 6: lint + 提交**

```bash
uv run ruff check server/app.py tests/test_startup_source_migration.py && uv run ruff format server/app.py tests/test_startup_source_migration.py
git add server/app.py tests/test_startup_source_migration.py
git commit -m "feat(server): startup 钩子幂等迁移历史项目源文件编码"
```

---

## Task 14：前端 i18n 新增 key

**Files:**
- Modify: `frontend/src/i18n/zh/common.ts`
- Modify: `frontend/src/i18n/en/common.ts`
- Modify: `frontend/src/i18n/zh/errors.ts`
- Modify: `frontend/src/i18n/en/errors.ts`
- Modify: `frontend/src/i18n/zh/dashboard.ts`
- Modify: `frontend/src/i18n/en/dashboard.ts`

- [ ] **Step 1: 在 `frontend/src/i18n/en/common.ts` 加新 key**

打开文件后，在合适位置（例如末尾 export 对象内）追加：

```ts
  conflict_modal_title: 'A file with this name already exists',
  conflict_modal_desc: 'How would you like to handle "{{filename}}"?',
  keep_both: 'Keep both',
  replace: 'Replace',
  cancel: 'Cancel',
  download_original: 'Download original format',
```

- [ ] **Step 2: 同步 `frontend/src/i18n/zh/common.ts`**

```ts
  conflict_modal_title: '同名文件已存在',
  conflict_modal_desc: '该如何处理 “{{filename}}”？',
  keep_both: '保留两者',
  replace: '替换',
  cancel: '取消',
  download_original: '下载原始格式',
```

- [ ] **Step 3: 在 `frontend/src/i18n/en/errors.ts` 加新 key**

```ts
  source_unsupported_format: 'Unsupported source format: {{ext}}',
  source_decode_failed: 'Failed to decode "{{filename}}" (tried: {{tried}})',
  source_corrupt_file: 'Source file "{{filename}}" cannot be parsed: {{reason}}',
  source_too_large: 'Source file "{{filename}}" is too large ({{size_mb}} MB > {{limit_mb}} MB)',
  source_conflict: 'Source file "{{existing}}" already exists',
```

- [ ] **Step 4: 同步 `frontend/src/i18n/zh/errors.ts`**

```ts
  source_unsupported_format: '不支持的源文件格式：{{ext}}',
  source_decode_failed: '源文件「{{filename}}」解码失败（已尝试：{{tried}}）',
  source_corrupt_file: '源文件「{{filename}}」无法解析：{{reason}}',
  source_too_large: '源文件「{{filename}}」过大（{{size_mb}} MB > {{limit_mb}} MB）',
  source_conflict: '源文件「{{existing}}」已存在',
```

- [ ] **Step 5: 在 `frontend/src/i18n/{en,zh}/dashboard.ts` 增加规范化结果 toast**

`zh`：

```ts
  source_normalized_toast: '已规范化「{{filename}}」（来源编码：{{encoding}}）',
  source_normalized_toast_with_chapters: '已规范化「{{filename}}」（{{chapters}} 章 · 来源编码：{{encoding}}）',
```

`en`：

```ts
  source_normalized_toast: '"{{filename}}" normalized (source encoding: {{encoding}})',
  source_normalized_toast_with_chapters: '"{{filename}}" normalized ({{chapters}} chapters; source encoding: {{encoding}})',
```

- [ ] **Step 6: typecheck**

```bash
cd frontend && pnpm exec tsc --noEmit
```

预期：通过。`zh` 与 `en` key 集合对齐（zh 文件 `satisfies Record<keyof typeof enXxx, string>` 已强制约束）。

- [ ] **Step 7: 提交**

```bash
git add frontend/src/i18n
git commit -m "i18n(frontend): 源文件冲突弹窗 / 规范化 toast / 错误信息（zh+en）"
```

---

## Task 15：前端 API.uploadFile 增加冲突参数与 ConflictError

**Files:**
- Modify: `frontend/src/api.ts:614-638`
- Modify: `frontend/src/api.test.ts`

- [ ] **Step 1: 写失败测试 — 在 `frontend/src/api.test.ts` 追加**

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { API, ConflictError } from '@/api';

beforeEach(() => {
  globalThis.fetch = vi.fn();
});

describe('uploadFile (source) onConflict', () => {
  it('passes on_conflict query when provided', async () => {
    (globalThis.fetch as any).mockResolvedValueOnce(
      new Response(JSON.stringify({ success: true, path: 'source/a.txt', url: '/x' }), { status: 200 })
    );
    await API.uploadFile('p', 'source', new File(['x'], 'a.txt'), null, { onConflict: 'replace' });
    const url = (globalThis.fetch as any).mock.calls[0][0] as string;
    expect(url).toContain('on_conflict=replace');
  });

  it('throws ConflictError on 409 with structured detail', async () => {
    (globalThis.fetch as any).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: { existing: 'a.txt', suggested_name: 'a_1', message: 'conflict' },
        }),
        { status: 409 }
      )
    );
    await expect(
      API.uploadFile('p', 'source', new File(['x'], 'a.txt'))
    ).rejects.toBeInstanceOf(ConflictError);
  });

  it('ConflictError carries existing and suggestedName', async () => {
    (globalThis.fetch as any).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: { existing: 'a.txt', suggested_name: 'a_1', message: 'conflict' },
        }),
        { status: 409 }
      )
    );
    try {
      await API.uploadFile('p', 'source', new File(['x'], 'a.txt'));
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(ConflictError);
      expect((err as ConflictError).existing).toBe('a.txt');
      expect((err as ConflictError).suggestedName).toBe('a_1');
    }
  });
});
```

- [ ] **Step 2: 运行确认失败**

```bash
cd frontend && pnpm exec vitest run src/api.test.ts -t "onConflict"
```

预期：`ConflictError is not defined` / 签名不匹配。

- [ ] **Step 3: 修改 `frontend/src/api.ts:614-638`**

替换为：

```ts
  // ==================== 文件管理 ====================

  static async uploadFile(
    projectName: string,
    uploadType: string,
    file: File,
    name: string | null = null,
    options: { onConflict?: "fail" | "replace" | "rename" } = {}
  ): Promise<{
    success: boolean;
    path: string;
    url: string;
    filename?: string;
    normalized?: boolean;
    original_kept?: boolean;
    original_filename?: string;
    used_encoding?: string | null;
    chapter_count?: number;
  }> {
    const formData = new FormData();
    formData.append("file", file);

    const params = new URLSearchParams();
    if (name) params.set("name", name);
    if (uploadType === "source" && options.onConflict) {
      params.set("on_conflict", options.onConflict);
    }
    const qs = params.toString();
    const url = `/projects/${encodeURIComponent(projectName)}/upload/${uploadType}${qs ? "?" + qs : ""}`;

    const response = await fetch(`${API_BASE}${url}`, withAuth({
      method: "POST",
      body: formData,
    }));

    if (response.status === 409) {
      let detail: any = null;
      try {
        const body = (await response.json()) as { detail?: any };
        detail = body?.detail;
      } catch {
        /* ignore */
      }
      throw new ConflictError(
        detail?.existing ?? file.name,
        detail?.suggested_name ?? file.name.replace(/(\.[^.]+)?$/, "_1$1"),
        detail?.message ?? "conflict"
      );
    }

    await throwIfNotOk(response, "上传失败");
    return response.json();
  }
```

在文件靠前的 `ErrorResponse` 之后导出 `ConflictError`：

```ts
export class ConflictError extends Error {
  constructor(
    public readonly existing: string,
    public readonly suggestedName: string,
    message: string
  ) {
    super(message);
    this.name = "ConflictError";
  }
}
```

注意：原 `frontend/src/api.test.ts:397` 的既有测试断言 URL 含 `?name=x%20y`，签名变化后应保持兼容（仅多了个可选 `options` 参数）。验证：

- [ ] **Step 4: 运行测试**

```bash
cd frontend && pnpm exec vitest run src/api.test.ts
```

预期：新增 3 个测试 + 既有 uploadFile 测试 PASS。

- [ ] **Step 5: typecheck + 提交**

```bash
cd frontend && pnpm exec tsc --noEmit
cd ..
git add frontend/src/api.ts frontend/src/api.test.ts
git commit -m "feat(frontend): API.uploadFile 增加 onConflict + ConflictError"
```

---

## Task 16：ConflictModal 组件

**Files:**
- Create: `frontend/src/components/canvas/ConflictModal.tsx`
- Create: `frontend/src/components/canvas/ConflictModal.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/canvas/ConflictModal.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConflictModal } from './ConflictModal';
import { I18nextProvider } from 'react-i18next';
import i18n from '@/i18n';

function renderModal(props: Parameters<typeof ConflictModal>[0]) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ConflictModal {...props} />
    </I18nextProvider>
  );
}

describe('ConflictModal', () => {
  it('renders with existing filename', () => {
    renderModal({
      existing: 'novel.txt',
      suggestedName: 'novel_1',
      onResolve: vi.fn(),
    });
    expect(screen.getByText(/novel\.txt/)).toBeTruthy();
  });

  it('calls onResolve("replace") when Replace clicked', () => {
    const onResolve = vi.fn();
    renderModal({ existing: 'novel.txt', suggestedName: 'novel_1', onResolve });
    fireEvent.click(screen.getByRole('button', { name: /replace|替换/i }));
    expect(onResolve).toHaveBeenCalledWith('replace');
  });

  it('calls onResolve("rename") when Keep both clicked', () => {
    const onResolve = vi.fn();
    renderModal({ existing: 'novel.txt', suggestedName: 'novel_1', onResolve });
    fireEvent.click(screen.getByRole('button', { name: /keep both|保留两者/i }));
    expect(onResolve).toHaveBeenCalledWith('rename');
  });

  it('calls onResolve("cancel") when Cancel clicked', () => {
    const onResolve = vi.fn();
    renderModal({ existing: 'novel.txt', suggestedName: 'novel_1', onResolve });
    fireEvent.click(screen.getByRole('button', { name: /cancel|取消/i }));
    expect(onResolve).toHaveBeenCalledWith('cancel');
  });
});
```

- [ ] **Step 2: 运行确认失败**

```bash
cd frontend && pnpm exec vitest run src/components/canvas/ConflictModal.test.tsx
```

预期：`Cannot find module './ConflictModal'`。

- [ ] **Step 3: 实现 `frontend/src/components/canvas/ConflictModal.tsx`**

```tsx
import { useTranslation } from "react-i18next";

export type ConflictResolution = "replace" | "rename" | "cancel";

interface ConflictModalProps {
  existing: string;
  suggestedName: string;
  onResolve: (decision: ConflictResolution) => void;
}

export function ConflictModal({ existing, suggestedName, onResolve }: ConflictModalProps) {
  const { t } = useTranslation("common");
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="conflict-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div className="w-full max-w-md rounded-xl border border-gray-700 bg-gray-900 p-6 shadow-xl">
        <h2 id="conflict-modal-title" className="text-lg font-semibold text-gray-100">
          {t("conflict_modal_title")}
        </h2>
        <p className="mt-2 text-sm text-gray-400">
          {t("conflict_modal_desc", { filename: existing })}
        </p>
        <p className="mt-3 text-xs text-gray-500">
          {`→ ${suggestedName}`}
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => onResolve("cancel")}
            className="rounded-md border border-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800"
          >
            {t("cancel")}
          </button>
          <button
            type="button"
            onClick={() => onResolve("rename")}
            className="rounded-md border border-gray-700 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-800"
          >
            {t("keep_both")}
          </button>
          <button
            type="button"
            onClick={() => onResolve("replace")}
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-500"
          >
            {t("replace")}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: 运行测试**

```bash
cd frontend && pnpm exec vitest run src/components/canvas/ConflictModal.test.tsx
```

预期：4 个测试 PASS。

- [ ] **Step 5: 提交**

```bash
cd ..
git add frontend/src/components/canvas/ConflictModal.tsx frontend/src/components/canvas/ConflictModal.test.tsx
git commit -m "feat(frontend): ConflictModal 组件（保留两者 / 替换 / 取消）"
```

---

## Task 17：WelcomeCanvas 集成（accept 扩展 + 自动分析 + 冲突协商）

**Files:**
- Modify: `frontend/src/components/canvas/WelcomeCanvas.tsx`
- Modify: `frontend/src/components/canvas/OverviewCanvas.tsx`
- Create / Modify: `frontend/src/components/canvas/WelcomeCanvas.test.tsx`

- [ ] **Step 1: 写失败测试 — 检查现有 WelcomeCanvas.test 文件存在性**

```bash
ls frontend/src/components/canvas/WelcomeCanvas.test.tsx 2>/dev/null && echo "exists" || echo "missing"
```

若存在 → 在末尾追加；若不存在 → 创建。下面假设新建（已存在则跳过 import / 重复 setup）。

```tsx
// frontend/src/components/canvas/WelcomeCanvas.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { WelcomeCanvas } from './WelcomeCanvas';
import i18n from '@/i18n';
import { API } from '@/api';

vi.mock('@/api', () => ({
  API: {
    listFiles: vi.fn().mockResolvedValue({ files: { source: [] } }),
  },
}));

function renderWelcome(props: Partial<Parameters<typeof WelcomeCanvas>[0]>) {
  return render(
    <I18nextProvider i18n={i18n}>
      <WelcomeCanvas
        projectName="p"
        onUpload={props.onUpload ?? vi.fn().mockResolvedValue(undefined)}
        onAnalyze={props.onAnalyze ?? vi.fn().mockResolvedValue(undefined)}
        {...props}
      />
    </I18nextProvider>
  );
}

describe('WelcomeCanvas auto-analyze on first upload', () => {
  beforeEach(() => {
    (API.listFiles as any).mockResolvedValue({ files: { source: [] } });
  });

  it('triggers onAnalyze automatically after first upload from idle', async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined);
    const onAnalyze = vi.fn().mockResolvedValue(undefined);
    renderWelcome({ onUpload, onAnalyze });

    const input = await screen.findByLabelText(/upload|上传/i);
    const file = new File(['x'], 'novel.txt', { type: 'text/plain' });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(file));
    await waitFor(() => expect(onAnalyze).toHaveBeenCalledTimes(1));
  });

  it('does NOT auto-trigger analyze when uploading from has_sources', async () => {
    (API.listFiles as any).mockResolvedValue({
      files: { source: [{ name: 'existing.txt', size: 10, url: '/x' }] },
    });
    const onUpload = vi.fn().mockResolvedValue(undefined);
    const onAnalyze = vi.fn();
    renderWelcome({ onUpload, onAnalyze });

    const input = await screen.findByLabelText(/upload|上传/i);
    const file = new File(['x'], 'second.docx');
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(onUpload).toHaveBeenCalled());
    expect(onAnalyze).not.toHaveBeenCalled();
  });
});

describe('WelcomeCanvas accept extension', () => {
  it('accepts .docx, .epub, .pdf in input accept attribute', async () => {
    renderWelcome({});
    const input = (await screen.findByLabelText(/upload|上传/i)) as HTMLInputElement;
    expect(input.accept).toContain('.docx');
    expect(input.accept).toContain('.epub');
    expect(input.accept).toContain('.pdf');
  });
});
```

- [ ] **Step 2: 运行确认失败**

```bash
cd frontend && pnpm exec vitest run src/components/canvas/WelcomeCanvas.test.tsx
```

预期：accept 不含新格式 / auto-analyze 未触发 → 多个测试 FAIL。

- [ ] **Step 3: 修改 `frontend/src/components/canvas/WelcomeCanvas.tsx:121,187,223`**

把所有 `accept=".txt,.md"` 改为 `accept=".txt,.md,.docx,.epub,.pdf"`，并把第 121 行的拖拽校验改为：

```tsx
const ALLOWED = [".txt", ".md", ".docx", ".epub", ".pdf"];
// ...
if (file && ALLOWED.some((ext) => file.name.toLowerCase().endsWith(ext))) {
  voidCall(processFile(file));
}
```

修改 `processFile`：在调用 `setPhase("has_sources")` 之前增加首次上传判断。完整新版本：

```tsx
const processFile = useCallback(
  async (file: File) => {
    if (!onUpload) return;
    setFileName(file.name);
    setError(null);

    const wasIdle = sourceFiles.length === 0;

    setPhase("uploading");
    try {
      await onUpload(file);
    } catch (err) {
      setError(`${t("upload_failed")}${(err as Error).message}`);
      setPhase(sourceFiles.length > 0 ? "has_sources" : "idle");
      return;
    }

    setSourceFiles((prev) => {
      const name = `source/${file.name}`;
      return prev.includes(name) ? prev : [...prev, name];
    });
    useAppStore.getState().invalidateSourceFiles();

    if (wasIdle && onAnalyze) {
      // 首次上传：自动触发分析，跳过 has_sources 等待手动点击
      setPhase("analyzing");
      try {
        await onAnalyze();
        setPhase("done");
      } catch (err) {
        setError(`${t("analysis_failed")}${(err as Error).message}`);
        setPhase("has_sources");
      }
      return;
    }

    setPhase("has_sources");
  },
  [onUpload, onAnalyze, sourceFiles.length, t],
);
```

- [ ] **Step 4: 修改 OverviewCanvas.handleUpload 接入 ConflictModal**

在 `frontend/src/components/canvas/OverviewCanvas.tsx` 顶部追加：

```tsx
import { ConflictError } from '@/api';
import { ConflictModal, type ConflictResolution } from './ConflictModal';
```

在组件内增加状态：

```tsx
const [conflictPrompt, setConflictPrompt] = useState<{
  file: File;
  existing: string;
  suggestedName: string;
  resolve: (d: ConflictResolution) => void;
} | null>(null);
```

把 `handleUpload` 改写为：

```tsx
const handleUpload = useCallback(
  async (file: File) => {
    const tryUpload = async (
      onConflict?: "fail" | "replace" | "rename"
    ): Promise<void> => {
      const res = await API.uploadFile(projectName, "source", file, null, {
        onConflict,
      });
      // toast 文案：根据 chapters 选择不同 key
      const filename = res.filename ?? file.name;
      const enc = res.used_encoding ?? "utf-8";
      const chapters = res.chapter_count ?? 0;
      const key = chapters > 0
        ? "source_normalized_toast_with_chapters"
        : "source_normalized_toast";
      useAppStore
        .getState()
        .pushToast(
          tRef.current(key, { filename, encoding: enc, chapters }),
          "success"
        );
    };

    try {
      await tryUpload();
    } catch (err) {
      if (err instanceof ConflictError) {
        const decision = await new Promise<ConflictResolution>((resolve) => {
          setConflictPrompt({
            file,
            existing: err.existing,
            suggestedName: err.suggestedName,
            resolve,
          });
        });
        setConflictPrompt(null);
        if (decision === "cancel") return;
        await tryUpload(decision);
      } else {
        throw err;
      }
    }
  },
  [projectName],
);
```

在 OverviewCanvas 渲染末尾附加：

```tsx
{conflictPrompt && (
  <ConflictModal
    existing={conflictPrompt.existing}
    suggestedName={conflictPrompt.suggestedName}
    onResolve={conflictPrompt.resolve}
  />
)}
```

- [ ] **Step 5: 运行测试**

```bash
cd frontend && pnpm exec vitest run src/components/canvas/WelcomeCanvas.test.tsx
```

预期：所有测试 PASS。

- [ ] **Step 6: typecheck**

```bash
cd frontend && pnpm exec tsc --noEmit
```

- [ ] **Step 7: 提交**

```bash
cd ..
git add frontend/src/components/canvas/WelcomeCanvas.tsx frontend/src/components/canvas/WelcomeCanvas.test.tsx frontend/src/components/canvas/OverviewCanvas.tsx
git commit -m "feat(frontend): WelcomeCanvas 接受多格式 + 首次上传自动分析；OverviewCanvas 接入冲突弹窗"
```

---

## Task 18：AssetSidebar 增加"📎 原始格式"按钮

**Files:**
- Modify: `frontend/src/components/layout/AssetSidebar.tsx`

注：本任务依赖 `listFiles` 返回结构能区分"是否有原始备份"。简化策略：前端不依赖额外字段，而是直接探测 `source/raw/<filename>` 是否可访问（HEAD 请求）。但这样需要每个文件一次额外网络。**更优**：扩展后端 `list_project_files` 返回 source 子项时附带 `raw_filename: string | null`。

- [ ] **Step 1: 扩展后端 `list_project_files` 返回 source 项的 raw_filename**

打开 `server/routers/files.py:267-307`，把 source 分支单独处理：

```python
            for subdir, file_list in files.items():
                subdir_path = project_dir / subdir
                if not subdir_path.exists():
                    continue
                # source 子目录额外列出 raw 备份映射
                raw_by_stem: dict[str, str] = {}
                if subdir == "source":
                    raw_dir = subdir_path / "raw"
                    if raw_dir.exists():
                        for raw_f in raw_dir.iterdir():
                            if raw_f.is_file():
                                raw_by_stem[raw_f.stem] = raw_f.name
                for f in subdir_path.iterdir():
                    if f.is_file() and not f.name.startswith("."):
                        entry = {
                            "name": f.name,
                            "size": f.stat().st_size,
                            "url": f"/api/v1/files/{project_name}/{subdir}/{f.name}",
                        }
                        if subdir == "source":
                            entry["raw_filename"] = raw_by_stem.get(Path(f.name).stem)
                        file_list.append(entry)
```

并在 `tests/test_files_router.py` 加测试：

```python
def test_list_files_source_includes_raw_filename(client_with_project):
    client, project_name = client_with_project
    raw = ("第一章\n" * 30).encode("gbk")
    _upload_source(client, project_name, "old.txt", raw)
    resp = client.get(f"/api/v1/projects/{project_name}/files")
    body = resp.json()
    source = body["files"]["source"]
    entry = next(e for e in source if e["name"] == "old.txt")
    assert entry["raw_filename"] == "old.txt"


def test_list_files_source_raw_filename_none_for_pure_utf8(client_with_project):
    client, project_name = client_with_project
    _upload_source(client, project_name, "novel.txt", "纯 UTF-8".encode("utf-8"))
    resp = client.get(f"/api/v1/projects/{project_name}/files")
    body = resp.json()
    entry = next(e for e in body["files"]["source"] if e["name"] == "novel.txt")
    assert entry["raw_filename"] is None
```

- [ ] **Step 2: 运行 router 测试**

```bash
uv run python -m pytest tests/test_files_router.py -v -k "raw_filename or source"
```

预期：所有 PASS。

- [ ] **Step 3: 修改前端 `AssetSidebar.tsx`**

在 source 文件渲染条目（line 295 附近 `sourceFiles.map((name) => {`）旁，找到包含每个文件的 `<li>`，在末尾追加 `📎 download_original` 按钮。需要先把 `loadSourceFiles` 拉回的数据从字符串数组扩展为带 `rawFilename` 的对象：

```tsx
type SourceItem = { name: string; rawFilename: string | null };
const [sourceFiles, setSourceFiles] = useState<SourceItem[]>([]);
// loadSourceFiles 内部：
const res = await API.listFiles(projectName);
const items: SourceItem[] = (res.files?.source ?? []).map((f: any) => ({
  name: f.name,
  rawFilename: f.raw_filename ?? null,
}));
setSourceFiles(items);
```

文件列表渲染中：

```tsx
{sourceFiles.map((item) => {
  const filePath = `/source/${encodeURIComponent(item.name)}`;
  const active = isActive(filePath);
  return (
    <li key={item.name} className="...">
      <a href={filePath}>{item.name}</a>
      {item.rawFilename && (
        <a
          href={`${API_BASE}/files/${encodeURIComponent(projectName)}/source/raw/${encodeURIComponent(item.rawFilename)}`}
          target="_blank"
          rel="noreferrer"
          title={t("download_original")}
          className="ml-1 text-xs"
        >
          📎
        </a>
      )}
    </li>
  );
})}
```

按既有 AssetSidebar 的样式风格调整 className，保持视觉一致。

- [ ] **Step 4: typecheck + 既有测试**

```bash
cd frontend && pnpm exec tsc --noEmit && pnpm exec vitest run src/components/layout
```

- [ ] **Step 5: 提交**

```bash
cd ..
git add server/routers/files.py tests/test_files_router.py frontend/src/components/layout/AssetSidebar.tsx
git commit -m "feat(frontend): AssetSidebar 显示原始格式下载入口（依赖 list_files 新字段 raw_filename）"
```

---

## Task 19：端到端冒烟（手工 + 文档）

**Files:** 仅运行命令 + 手工核对

- [ ] **Step 1: 全量后端测试 + 覆盖率**

```bash
uv run python -m pytest --cov=lib.source_loader --cov=server.routers.files --cov-report=term-missing
```

预期：`lib/source_loader` 覆盖 ≥90%；总体不低于既有水平；无 FAIL。

- [ ] **Step 2: 全量前端测试 + 构建**

```bash
cd frontend && pnpm check && pnpm build
cd ..
```

预期：typecheck PASS；vitest 全 PASS；vite build 成功。

- [ ] **Step 3: 启动 server 跑冒烟**

```bash
uv run uvicorn server.app:app --port 1241 &
SERVER_PID=$!
sleep 5
curl -s http://127.0.0.1:1241/health | jq .
kill $SERVER_PID
```

预期：日志中能看到 `源文件编码迁移完成` 一行（即使 migrated=0 也无 ERROR）；`/health` 返回 `{"status":"ok"}`。

- [ ] **Step 4: 手工核对清单**（在浏览器内）

启动开发模式 `uv run uvicorn server.app:app --reload --port 1241` + `cd frontend && pnpm dev`，按以下顺序操作并核对：

1. 创建新项目 → 进入 Welcome 视图
2. 上传一个 GBK 编码的 `.txt` → 看 toast 显示来源编码 `gbk` → 上传后**自动**进入 analyzing 阶段（不需手动点）
3. 同名再上传一个 `.epub` → 弹冲突弹窗 → 选「保留两者」→ 文件列表多出 `<stem>_1.txt` + 📎 按钮
4. 选「替换」复测：替换后 `.txt` 内容更新，📎 链接到新原始文件
5. 选「取消」复测：弹窗关闭，无任何改动
6. 上传一个 `.doc` → 前端 accept 已不允许选择；若强行 POST 后端返回 400
7. 上传一个无文本的 PDF（手动构造 / 用一个空白图扫描件）→ 422，文案明确"疑似扫描版"
8. 删除一个有原文件备份的 `.txt` → `source/raw/` 中对应文件也消失

- [ ] **Step 5: 写 release notes 摘要追加到 CHANGELOG（如有）或 PR body**

```markdown
## 源文件格式扩展

- 上传支持 .txt / .md / .docx / .epub / .pdf 五种格式（移除 .doc）
- 非 UTF-8 .txt 自动检测并规范化为 UTF-8，原文件备份到 source/raw/
- EPUB 自动注入 `# 章节标题` 标记，便于人工切分
- 同名文件冲突时弹窗协商（保留两者 / 替换 / 取消）
- 首次上传自动触发分析
- 启动时一次性迁移历史项目源文件编码（幂等，失败不阻塞启动；明细见 projects/<name>/.arcreel/migration_errors.log）
```

- [ ] **Step 6: 最终提交**

如有 CHANGELOG 更新：

```bash
git add CHANGELOG.md  # 如有
git commit -m "docs(changelog): 记录源文件格式扩展"
```

---

## 自审复核（针对设计文档）

执行计划前对照 `docs/superpowers/specs/2026-04-20-source-format-support-expansion-design.md` 的章节复核：

| Spec 章节 | 实施任务 | 状态 |
|---|---|---|
| §3 架构 / lib/source_loader 包 | Task 2-9 | ✓ |
| §4 决策 1 上传时规范化 | Task 8 SourceLoader.load 同步写入 | ✓ |
| §4 决策 2 专用库组合 | Task 1 uv add | ✓ |
| §4 决策 3 PDF 抽象接口 | Task 7 + base.FormatExtractor 协议 | ✓ |
| §4 决策 4 解码失败显式抛错 | Task 4 + Task 12 _read_source_files 改写 | ✓ |
| §4 决策 5 EPUB 章节标记单文件 | Task 6 EpubExtractor 注入 # 标题 | ✓ |
| §4 决策 6 冲突 409 + 前端二次确认 | Task 11 / 15 / 16 / 17 | ✓ |
| §4 决策 7 raw 备份策略 | Task 8 _maybe_backup_raw 分支 + Task 18 raw_filename | ✓ |
| §4 决策 8 启动迁移 | Task 9 / 13 | ✓ |
| §4 决策 9 首次上传自动分析 | Task 17 processFile wasIdle 分支 | ✓ |
| §5 上传 / 冲突 / 下游零改动 | Task 11 / 12 | ✓ |
| §6 各格式解码细节 | Task 4 / 5 / 6 / 7 | ✓ |
| §7 错误 i18n | Task 10 / 14 | ✓ |
| §8 startup 迁移 | Task 9 / 13 | ✓ |
| §9 前端变更（含原始下载） | Task 14-18 | ✓ |
| §10 测试矩阵 | Task 2-13 + Task 15-17 | ✓ |
| §11 依赖 | Task 1 | ✓ |
| §12 风险与缓解 | 各任务步骤涵盖（asyncio.to_thread / 三道防线 / 三级退化等） | ✓ |
| §13 影响清单 | 全任务覆盖 | ✓ |

**类型一致性检查**：

- `NormalizeResult` 字段名（`normalized_path` / `raw_path` / `used_encoding` / `chapter_count` / `original_filename`）在 Task 3 / 8 / 11 一致
- `SourceLoader.load` 签名（`src` / `dst_dir` / `original_filename` / `on_conflict` / `max_bytes`）在 Task 8 测试与实现一致
- `ConflictError` 字段（`existing` / `suggested_name`）在 Task 2 / 8 / 11 / 15 一致
- 前端 `ConflictError`（`existing` / `suggestedName`）在 Task 15 / 17 一致（注意 camelCase）
- `ConflictResolution` 类型（`"replace" | "rename" | "cancel"`）在 Task 16 / 17 一致

**Placeholder 扫描**：无 TBD / TODO / "fill in details" 等占位；所有步骤含具体代码或命令。
