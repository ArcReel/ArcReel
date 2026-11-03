"""共享 fixtures：尽量在运行期构造测试样本，避免二进制入库。

例外：PDF 因 pdf_oxide 暂无稳定的 Python 创建 API，改用 data/ 下预生成的
真实 PDF（来源详见 data/SOURCES.md）。
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Literal

import pytest

_PDF_DATA_DIR = Path(__file__).parent / "data"


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


@pytest.fixture
def epub_factory(tmp_path: Path):
    """构造一个含 N 章的 .epub，直接按 OCF/OPF 规范写 zip。

    - ``toc``：目录来源，``"ncx+nav"``（默认）、``"ncx"``、``"nav"`` 或 ``None``（无目录）。
    - ``toc_entries``：覆盖目录条目，元素为 ``(标题, 相对 OPF 目录的 href)``；默认每章一条。
    - ``nav_id``：nav 文档的 manifest id；nav 文档总是放进 spine，验证它会被排除。
    - ``opf_dir``：OPF 所在目录，章节、NCX、nav 与它同目录。
    """

    def _make(
        chapter_titles_and_bodies: list[tuple[str, str]],
        filename: str = "sample.epub",
        *,
        toc: Literal["ncx+nav", "ncx", "nav"] | None = "ncx+nav",
        toc_entries: list[tuple[str, str]] | None = None,
        nav_id: str = "nav",
        opf_dir: str = "OEBPS",
    ) -> Path:
        chapters = [
            (f"chap_{idx}.xhtml", title, body) for idx, (title, body) in enumerate(chapter_titles_and_bodies, start=1)
        ]
        if toc_entries is None:
            toc_entries = [(title, href) for href, title, _ in chapters]
        prefix = f"{opf_dir}/" if opf_dir else ""
        use_ncx = toc in ("ncx+nav", "ncx")
        use_nav = toc in ("ncx+nav", "nav")

        manifest = [
            f'<item id="ch{idx}" href="{href}" media-type="application/xhtml+xml"/>'
            for idx, (href, _, _) in enumerate(chapters, start=1)
        ]
        spine = [f'<itemref idref="ch{idx}"/>' for idx in range(1, len(chapters) + 1)]
        files: dict[str, str] = {}
        if use_ncx:
            manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
            points = "".join(
                f'<navPoint id="p{i}"><navLabel><text>{title}</text></navLabel><content src="{href}"/></navPoint>'
                for i, (title, href) in enumerate(toc_entries, start=1)
            )
            files[prefix + "toc.ncx"] = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                f"<head/><docTitle><text>Test Book</text></docTitle><navMap>{points}</navMap></ncx>"
            )
        if use_nav:
            manifest.append(
                f'<item id="{nav_id}" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
            )
            spine.insert(0, f'<itemref idref="{nav_id}"/>')
            links = "".join(f'<li><a href="{href}">{title}</a></li>' for title, href in toc_entries)
            files[prefix + "nav.xhtml"] = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
                f'<body><nav epub:type="toc"><ol>{links}</ol></nav></body></html>'
            )
        for href, title, body in chapters:
            files[prefix + href] = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml">'
                f"<head><title>{title}</title></head><body><h1>{title}</h1><p>{body}</p></body></html>"
            )

        spine_attr = ' toc="ncx"' if use_ncx else ""
        files[prefix + "content.opf"] = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="id">test-id</dc:identifier><dc:title>Test Book</dc:title><dc:language>zh</dc:language>'
            f"</metadata><manifest>{''.join(manifest)}</manifest>"
            f"<spine{spine_attr}>{''.join(spine)}</spine></package>"
        )
        container = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            f'<rootfiles><rootfile full-path="{prefix}content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>"
        )

        out = tmp_path / filename
        with zipfile.ZipFile(out, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            zf.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
            for name, content in files.items():
                zf.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)
        return out

    return _make


class _PdfFactory:
    """调用得到文本 PDF，``scanned()`` 得到扫描件 PDF。"""

    def __call__(self) -> Path:
        return _PDF_DATA_DIR / "sample_text.pdf"

    def scanned(self) -> Path:
        return _PDF_DATA_DIR / "sample_scanned.pdf"


@pytest.fixture
def pdf_factory() -> _PdfFactory:
    """返回预生成的 PDF fixture 路径（来自真实 PDF，见 data/SOURCES.md）。"""
    return _PdfFactory()
