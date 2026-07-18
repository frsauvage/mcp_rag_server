"""Tests unitaires pour pdf_chunker.py."""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from pdf_chunker import (
    MAX_CHUNK_CHARS,
    MIN_SECTION_CHARS,
    DocChunk,
    PdfChunker,
    _extract_chapter,
    _pdf_hash,
)

# ── _extract_chapter ─────────────────────────────────────────────────────────

class TestExtractChapter:
    @pytest.mark.parametrize("title,expected", [
        ("3.2 Configuration", "3.2"),
        ("1 Introduction", "1"),
        ("10.1.2 Details", "10.1.2"),
        ("chapitre 5 Overview", "5"),
        ("CHAPITRE 2", "2"),
        ("Introduction", ""),
        ("", ""),
        ("Advanced Setup", ""),
        ("2024 Release Notes", "2024"),
    ])
    def test_extract(self, title, expected):
        assert _extract_chapter(title) == expected


# ── DocChunk ─────────────────────────────────────────────────────────────────

class TestDocChunk:
    def _make(self, **kwargs) -> DocChunk:
        defaults = dict(
            content="Some content here",
            file_path="/docs/manual.pdf",
            relative_path="manual.pdf",
            chunk_type="section",
            symbol_name="Introduction",
            page_start=1,
            page_end=3,
            level=1,
            file_hash="abc" * 21 + "a",  # 64 chars
        )
        defaults.update(kwargs)
        return DocChunk(**defaults)

    def test_chunk_id_is_24_hex(self):
        chunk = self._make()
        assert len(chunk.chunk_id) == 24
        assert all(c in "0123456789abcdef" for c in chunk.chunk_id)

    def test_chunk_id_stable(self):
        a = self._make()
        b = self._make()
        assert a.chunk_id == b.chunk_id

    def test_chunk_id_changes_with_page(self):
        a = self._make(page_start=1)
        b = self._make(page_start=5)
        assert a.chunk_id != b.chunk_id

    def test_chunk_id_changes_with_symbol_name(self):
        a = self._make(symbol_name="Intro")
        b = self._make(symbol_name="Conclusion")
        assert a.chunk_id != b.chunk_id

    def test_to_metadata_keys(self):
        chunk = self._make()
        meta = chunk.to_metadata()
        required = {
            "file_path", "relative_path", "language", "chunk_type",
            "symbol_name", "chapter", "start_line", "end_line",
            "file_hash", "symbols_referenced", "page_start", "page_end", "level",
        }
        assert required.issubset(set(meta.keys()))

    def test_to_metadata_language_is_pdf(self):
        chunk = self._make()
        assert chunk.to_metadata()["language"] == "pdf"

    def test_to_metadata_start_line_equals_page_start(self):
        chunk = self._make(page_start=7, page_end=9)
        meta = chunk.to_metadata()
        assert meta["start_line"] == 7
        assert meta["end_line"] == 9

    def test_to_metadata_symbols_referenced_empty(self):
        chunk = self._make()
        assert chunk.to_metadata()["symbols_referenced"] == ""


# ── _pdf_hash ─────────────────────────────────────────────────────────────────

class TestPdfHash:
    def test_returns_sha256(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"pdf content")
        result = _pdf_hash(f)
        expected = hashlib.sha256(b"pdf content").hexdigest()
        assert result == expected


# ── PdfChunker._split_text ────────────────────────────────────────────────────

class TestSplitText:
    def setup_method(self):
        self.chunker = PdfChunker()

    def test_short_text_unchanged(self):
        text = "Short text."
        result = self.chunker._split_text(text, MAX_CHUNK_CHARS)
        assert result == [text]

    def test_long_text_split_into_multiple(self):
        # Deux paragraphes chacun plus long que MAX_CHUNK_CHARS/2
        para = "word " * 200  # ~1000 chars
        text = para + "\n\n" + para
        result = self.chunker._split_text(text, MAX_CHUNK_CHARS)
        assert len(result) >= 1
        for piece in result:
            assert len(piece) <= MAX_CHUNK_CHARS + 10  # petit slack pour strip

    def test_empty_pieces_filtered(self):
        # Long text with a very short paragraph → short piece filtered by MIN_SECTION_CHARS
        long_para = "x" * 2000
        short_para = "y" * 10
        text = long_para + "\n\n" + short_para
        result = self.chunker._split_text(text, MAX_CHUNK_CHARS)
        assert all(len(p) >= MIN_SECTION_CHARS for p in result)

    def test_very_long_paragraph_split(self):
        # Un seul paragraphe de 5000 chars → doit être découpé
        para = "a" * 5000
        result = self.chunker._split_text(para, MAX_CHUNK_CHARS)
        assert len(result) > 1
        for piece in result:
            assert len(piece) <= MAX_CHUNK_CHARS


# ── PdfChunker (mocked fitz) ──────────────────────────────────────────────────

def _make_mock_doc(pages_text: list[str], toc: list = None):
    """Crée un faux document fitz."""
    mock_doc = MagicMock()
    mock_doc.page_count = len(pages_text)
    mock_doc.get_toc.return_value = toc or []

    mock_pages = []
    for text in pages_text:
        page = MagicMock()
        page.get_text.return_value = text
        mock_pages.append(page)

    mock_doc.__getitem__ = lambda self, i: mock_pages[i]
    mock_doc.__iter__ = lambda self: iter(mock_pages)
    return mock_doc


class TestPdfChunkerByPage:
    def test_chunk_by_page_returns_docchunks(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"fake pdf")

        pages = ["x" * 200, "y" * 200, "z" * 200]
        mock_doc = _make_mock_doc(pages)

        with patch("fitz.open", return_value=mock_doc):
            chunks = PdfChunker().chunk(f, tmp_path)

        assert len(chunks) == 3
        assert all(isinstance(c, DocChunk) for c in chunks)

    def test_chunk_by_page_type_is_page(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"fake pdf")
        pages = ["x" * 200]
        mock_doc = _make_mock_doc(pages)

        with patch("fitz.open", return_value=mock_doc):
            chunks = PdfChunker().chunk(f, tmp_path)

        assert all(c.chunk_type == "page" for c in chunks)

    def test_short_pages_skipped(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"fake pdf")
        pages = ["short", "x" * 200]  # first too short
        mock_doc = _make_mock_doc(pages)

        with patch("fitz.open", return_value=mock_doc):
            chunks = PdfChunker().chunk(f, tmp_path)

        assert len(chunks) == 1


class TestPdfChunkerByToc:
    def test_chunk_by_toc_returns_sections(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"fake pdf")

        toc = [
            [1, "Introduction", 1],
            [1, "Installation", 2],
            [1, "Usage", 3],
        ]
        pages = ["intro text " * 20, "install text " * 20, "usage text " * 20]
        mock_doc = _make_mock_doc(pages, toc=toc)

        with patch("fitz.open", return_value=mock_doc):
            chunks = PdfChunker().chunk(f, tmp_path)

        assert len(chunks) > 0
        assert all(c.chunk_type == "section" for c in chunks)

    def test_chunk_by_toc_symbol_names_from_toc(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"fake pdf")

        toc = [[1, "Introduction", 1], [1, "Conclusion", 2]]
        pages = ["intro " * 30, "conclusion " * 30]
        mock_doc = _make_mock_doc(pages, toc=toc)

        with patch("fitz.open", return_value=mock_doc):
            chunks = PdfChunker().chunk(f, tmp_path)

        names = {c.symbol_name for c in chunks}
        assert any("Introduction" in n for n in names)
