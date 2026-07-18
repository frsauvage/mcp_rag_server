"""Tests unitaires pour code_chunker.py."""
from __future__ import annotations

import hashlib

from code_chunker import (
    MIN_CODE_LINES,
    CodeChunk,
    _extract_lines,
    _is_worth_chunking,
    _strip_file_header,
    file_hash,
)

# ── CodeChunk ────────────────────────────────────────────────────────────────

class TestCodeChunk:
    def _make(self, **kwargs) -> CodeChunk:
        defaults = dict(
            content="def foo(): pass",
            file_path="/proj/foo.py",
            relative_path="foo.py",
            language="python",
            chunk_type="function",
            symbol_name="foo",
            start_line=1,
            end_line=3,
            file_hash="abc123",
        )
        defaults.update(kwargs)
        return CodeChunk(**defaults)

    def test_chunk_id_is_24_hex_chars(self):
        chunk = self._make()
        assert len(chunk.chunk_id) == 24
        assert all(c in "0123456789abcdef" for c in chunk.chunk_id)

    def test_chunk_id_stable(self):
        a = self._make()
        b = self._make()
        assert a.chunk_id == b.chunk_id

    def test_chunk_id_changes_with_file_path(self):
        a = self._make(file_path="/proj/foo.py")
        b = self._make(file_path="/proj/bar.py")
        assert a.chunk_id != b.chunk_id

    def test_chunk_id_changes_with_start_line(self):
        a = self._make(start_line=1)
        b = self._make(start_line=10)
        assert a.chunk_id != b.chunk_id

    def test_to_metadata_keys(self):
        chunk = self._make(symbols_referenced=["foo", "bar"])
        meta = chunk.to_metadata()
        expected_keys = {
            "file_path", "relative_path", "language", "chunk_type",
            "symbol_name", "chapter", "start_line", "end_line",
            "file_hash", "symbols_referenced",
        }
        assert expected_keys == set(meta.keys())

    def test_to_metadata_symbols_pipe_joined(self):
        chunk = self._make(symbols_referenced=["alpha", "beta", "gamma"])
        assert chunk.to_metadata()["symbols_referenced"] == "alpha|beta|gamma"

    def test_to_metadata_empty_symbols(self):
        chunk = self._make(symbols_referenced=[])
        assert chunk.to_metadata()["symbols_referenced"] == ""

    def test_to_metadata_chapter_default(self):
        chunk = self._make()
        assert chunk.to_metadata()["chapter"] == ""

    def test_to_metadata_chapter_set(self):
        chunk = self._make(chapter="3.2")
        assert chunk.to_metadata()["chapter"] == "3.2"


# ── file_hash ────────────────────────────────────────────────────────────────

class TestFileHash:
    def test_returns_sha256_hex(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_bytes(b"hello")
        result = file_hash(f)
        expected = hashlib.sha256(b"hello").hexdigest()
        assert result == expected

    def test_different_content_different_hash(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_bytes(b"aaa")
        b.write_bytes(b"bbb")
        assert file_hash(a) != file_hash(b)

    def test_same_content_same_hash(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_bytes(b"same content")
        b.write_bytes(b"same content")
        assert file_hash(a) == file_hash(b)


# ── _extract_lines ────────────────────────────────────────────────────────────

class TestExtractLines:
    SOURCE = "line1\nline2\nline3\nline4\nline5"

    def test_single_line(self):
        assert _extract_lines(self.SOURCE, 1, 1) == "line1"

    def test_range(self):
        assert _extract_lines(self.SOURCE, 2, 4) == "line2\nline3\nline4"

    def test_full(self):
        assert _extract_lines(self.SOURCE, 1, 5) == self.SOURCE

    def test_last_line(self):
        assert _extract_lines(self.SOURCE, 5, 5) == "line5"


# ── _is_worth_chunking ────────────────────────────────────────────────────────

class TestIsWorthChunking:
    def _code(self, n: int) -> str:
        return "\n".join(f"x = {i}" for i in range(n))

    def test_too_few_lines(self):
        assert not _is_worth_chunking("x = 1\ny = 2", "python")

    def test_enough_lines(self):
        source = self._code(MIN_CODE_LINES + 1)
        assert _is_worth_chunking(source, "python")

    def test_too_many_lines(self):
        source = self._code(10_001)
        assert not _is_worth_chunking(source, "python")

    def test_all_comments_python(self):
        source = "\n".join(f"# comment {i}" for i in range(20))
        assert not _is_worth_chunking(source, "python")

    def test_all_comments_cpp(self):
        source = "\n".join(f"// comment {i}" for i in range(20))
        assert not _is_worth_chunking(source, "cpp")

    def test_mixed_python(self):
        lines = ["# comment"] * 5 + [f"x = {i}" for i in range(10)]
        assert _is_worth_chunking("\n".join(lines), "python")


# ── _strip_file_header ────────────────────────────────────────────────────────

class TestStripFileHeader:
    def test_python_strips_hash_comments(self):
        source = "# License\n# GPL\n\ndef foo():\n    pass\n"
        result = _strip_file_header(source, "python")
        assert result.startswith("def foo()")

    def test_python_no_header(self):
        source = "def foo():\n    pass\n"
        result = _strip_file_header(source, "python")
        assert "def foo()" in result

    def test_cpp_strips_block_comment(self):
        source = "/* License\n * GPL\n */\n#include <stdio.h>\n"
        result = _strip_file_header(source, "cpp")
        assert "#include" in result

    def test_cpp_strips_line_comments(self):
        source = "// Copyright\n// 2024\n#include <stdio.h>\n"
        result = _strip_file_header(source, "cpp")
        assert "#include" in result
