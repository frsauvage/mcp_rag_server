"""Tests unitaires pour md_chunker.py."""
from __future__ import annotations

from pathlib import Path

from md_chunker import MIN_SECTION_CHARS, chunk_markdown


def _write(tmp_path: Path, name: str, source: str) -> Path:
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return p


class TestChunkMarkdownBasics:
    def test_empty_file_returns_empty(self, tmp_path):
        f = _write(tmp_path, "empty.md", "")
        assert chunk_markdown(f, tmp_path) == []

    def test_no_headers_no_sections(self, tmp_path):
        # Texte court sans titres → section unique mais trop courte
        f = _write(tmp_path, "short.md", "Hello world.")
        assert chunk_markdown(f, tmp_path) == []

    def test_error_returns_empty(self, tmp_path):
        # Fichier inexistant → exception gérée → []
        missing = tmp_path / "missing.md"
        result = chunk_markdown(missing, tmp_path)
        assert result == []


class TestChunkMarkdownSections:
    CONTENT = """\
# Introduction

This is the introduction section with enough text to be useful.
It spans multiple lines and provides context.

## Installation

To install the package, run the following command in your terminal.
Make sure you have Python 3.11 or above installed before proceeding.

### Advanced Setup

Advanced setup instructions go here with extra detail.
This section explains configuration and optional dependencies.

# Usage

Basic usage examples are provided in this section.
Refer to the README for more detailed documentation and examples.
"""

    def test_returns_docchunks(self, tmp_path):
        from pdf_chunker import DocChunk
        f = _write(tmp_path, "doc.md", self.CONTENT)
        chunks = chunk_markdown(f, tmp_path)
        assert all(isinstance(c, DocChunk) for c in chunks)

    def test_chunk_type_is_section(self, tmp_path):
        f = _write(tmp_path, "doc.md", self.CONTENT)
        chunks = chunk_markdown(f, tmp_path)
        assert all(c.chunk_type == "section" for c in chunks)

    def test_relative_path(self, tmp_path):
        f = _write(tmp_path, "doc.md", self.CONTENT)
        chunks = chunk_markdown(f, tmp_path)
        assert all(c.relative_path == "doc.md" for c in chunks)

    def test_header_in_content(self, tmp_path):
        f = _write(tmp_path, "doc.md", self.CONTENT)
        chunks = chunk_markdown(f, tmp_path)
        assert all("# Document" in c.content for c in chunks)

    def test_section_titles_in_content(self, tmp_path):
        f = _write(tmp_path, "doc.md", self.CONTENT)
        chunks = chunk_markdown(f, tmp_path)
        titles = {c.symbol_name for c in chunks}
        assert "Introduction" in titles or any("Introduction" in t for t in titles)

    def test_minimum_section_length_respected(self, tmp_path):
        f = _write(tmp_path, "doc.md", self.CONTENT)
        chunks = chunk_markdown(f, tmp_path)
        for c in chunks:
            # Le contenu brut sans header doit dépasser MIN_SECTION_CHARS
            assert len(c.content) >= MIN_SECTION_CHARS

    def test_file_hash_set(self, tmp_path):
        f = _write(tmp_path, "doc.md", self.CONTENT)
        chunks = chunk_markdown(f, tmp_path)
        assert all(len(c.file_hash) == 64 for c in chunks)

    def test_page_numbers_increment(self, tmp_path):
        f = _write(tmp_path, "doc.md", self.CONTENT)
        chunks = chunk_markdown(f, tmp_path)
        pages = [c.page_start for c in chunks]
        assert pages == sorted(pages)


class TestChunkMarkdownChapterExtraction:
    def test_numeric_title_extracts_chapter(self, tmp_path):
        content = "# 3.2 Configuration\n\n" + "x" * 200
        f = _write(tmp_path, "chap.md", content)
        chunks = chunk_markdown(f, tmp_path)
        if chunks:
            assert chunks[-1].chapter == "3.2"

    def test_non_numeric_title_empty_chapter(self, tmp_path):
        content = "# Introduction\n\n" + "x" * 200
        f = _write(tmp_path, "intro.md", content)
        chunks = chunk_markdown(f, tmp_path)
        if chunks:
            assert chunks[-1].chapter == ""
