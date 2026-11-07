"""Shared fixtures: construct samples at runtime where possible to avoid committed binaries.

Exception: pdf_oxide lacks a stable Python creation API, so PDFs use pregenerated real files
under data/ (sources in data/SOURCES.md).
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Literal

import pytest

_PDF_DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture
def docx_factory(tmp_path: Path):
    """Construct a two-paragraph .docx; skip without python-docx."""
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
    """Construct an N-chapter .epub by writing an OCF/OPF-compliant zip directly.

    - ``toc``: table-of-contents source, ``"ncx+nav"`` (default), ``"ncx"``, ``"nav"``, or ``None`` (no TOC).
    - ``toc_entries``: override TOC entries as ``(标题, 相对 OPF 目录的 href)``; default one per chapter.
    - ``nav_id``: nav-document manifest ID; always include nav in spine to verify exclusion.
    - ``opf_dir``: OPF directory, shared by chapters, NCX, and nav.
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
    """Calling yields a text PDF; ``scanned()`` yields a scanned PDF."""

    def __call__(self) -> Path:
        return _PDF_DATA_DIR / "sample_text.pdf"

    def scanned(self) -> Path:
        return _PDF_DATA_DIR / "sample_scanned.pdf"


@pytest.fixture
def pdf_factory() -> _PdfFactory:
    """Return pregenerated real-PDF fixture paths; see data/SOURCES.md."""
    return _PdfFactory()
