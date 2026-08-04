"""Tests unitaires pour chunker_python.py (PythonChunker)."""
from pathlib import Path

from chunker_python import PythonChunker

ROOT = Path("/project")


def _write(tmp_path: Path, name: str, source: str) -> Path:
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return p


class TestPythonChunkerBasics:
    def test_empty_file_returns_empty(self, tmp_path):
        f = _write(tmp_path, "empty.py", "")
        chunker = PythonChunker()
        assert chunker.chunk(f, tmp_path) == []

    def test_syntax_error_returns_empty(self, tmp_path):
        f = _write(tmp_path, "bad.py", "def foo(:\n    pass\n")
        chunker = PythonChunker()
        assert chunker.chunk(f, tmp_path) == []

    def test_only_comments_returns_empty(self, tmp_path):
        source = "\n".join(f"# comment {i}" for i in range(30))
        f = _write(tmp_path, "comments.py", source)
        assert PythonChunker().chunk(f, tmp_path) == []


class TestPythonChunkerFunctions:
    SOURCE = """\
import os
import sys


def alpha(x):
    # add one to x
    result = x + 1
    return result


def beta(x, y):
    # multiply x by y
    result = x * y
    return result


def gamma():
    # return a constant
    value = 0
    return value
"""

    def test_detects_top_level_functions(self, tmp_path):
        f = _write(tmp_path, "funcs.py", self.SOURCE)
        chunks = PythonChunker().chunk(f, tmp_path)
        names = {c.symbol_name for c in chunks}
        assert "alpha" in names
        assert "beta" in names

    def test_chunk_type_is_function(self, tmp_path):
        f = _write(tmp_path, "funcs.py", self.SOURCE)
        chunks = PythonChunker().chunk(f, tmp_path)
        for c in chunks:
            if c.symbol_name in ("alpha", "beta"):
                assert c.chunk_type == "function"

    def test_language_is_python(self, tmp_path):
        f = _write(tmp_path, "funcs.py", self.SOURCE)
        chunks = PythonChunker().chunk(f, tmp_path)
        assert all(c.language == "python" for c in chunks)

    def test_relative_path_set(self, tmp_path):
        f = _write(tmp_path, "funcs.py", self.SOURCE)
        chunks = PythonChunker().chunk(f, tmp_path)
        assert all(c.relative_path == "funcs.py" for c in chunks)

    def test_imports_in_content(self, tmp_path):
        f = _write(tmp_path, "funcs.py", self.SOURCE)
        chunks = PythonChunker().chunk(f, tmp_path)
        for c in chunks:
            if c.symbol_name == "alpha":
                assert "import os" in c.content

    def test_short_function_excluded(self, tmp_path):
        source = "def tiny():\n    pass\n"
        # pad to get past _is_worth_chunking
        pad = "\n".join(f"x_{i} = {i}" for i in range(10))
        f = _write(tmp_path, "tiny.py", pad + "\n" + source)
        chunks = PythonChunker().chunk(f, tmp_path)
        names = {c.symbol_name for c in chunks}
        assert "tiny" not in names


class TestPythonChunkerClasses:
    SOURCE = """\
import logging


class MyClass:
    \"\"\"A sample class.\"\"\"

    x: int = 0

    def method_one(self, a, b, c):
        # sum all three arguments
        result = a + b + c
        return result

    def method_two(self, val):
        # update and return x
        self.x = val
        return self.x
"""

    def test_detects_class(self, tmp_path):
        f = _write(tmp_path, "cls.py", self.SOURCE)
        chunks = PythonChunker().chunk(f, tmp_path)
        names = {c.symbol_name for c in chunks}
        assert "MyClass" in names

    def test_class_chunk_type(self, tmp_path):
        f = _write(tmp_path, "cls.py", self.SOURCE)
        chunks = PythonChunker().chunk(f, tmp_path)
        cls_chunks = [c for c in chunks if c.symbol_name == "MyClass"]
        assert cls_chunks[0].chunk_type == "class"

    def test_detects_methods(self, tmp_path):
        f = _write(tmp_path, "cls.py", self.SOURCE)
        chunks = PythonChunker().chunk(f, tmp_path)
        names = {c.symbol_name for c in chunks}
        assert "MyClass::method_one" in names
        assert "MyClass::method_two" in names

    def test_method_chunk_type(self, tmp_path):
        f = _write(tmp_path, "cls.py", self.SOURCE)
        chunks = PythonChunker().chunk(f, tmp_path)
        for c in chunks:
            if "::" in c.symbol_name:
                assert c.chunk_type == "method"

    def test_method_prefix_from_class(self, tmp_path):
        f = _write(tmp_path, "cls.py", self.SOURCE)
        chunks = PythonChunker().chunk(f, tmp_path)
        method_names = [c.symbol_name for c in chunks if c.chunk_type == "method"]
        assert all(m.startswith("MyClass::") for m in method_names)


class TestPythonChunkerMetadata:
    SOURCE = """\
def compute(a, b):
    # use helper to process a
    result = some_helper(a)
    return result + b


def helper(x):
    # double the input value
    doubled = x * 2
    return doubled


def another():
    # delegate to helper with a constant
    value = helper(10)
    return value
"""

    def test_start_end_lines_set(self, tmp_path):
        f = _write(tmp_path, "meta.py", self.SOURCE)
        chunks = PythonChunker().chunk(f, tmp_path)
        for c in chunks:
            assert c.start_line >= 1
            assert c.end_line >= c.start_line

    def test_file_hash_set(self, tmp_path):
        f = _write(tmp_path, "meta.py", self.SOURCE)
        chunks = PythonChunker().chunk(f, tmp_path)
        assert all(len(c.file_hash) == 64 for c in chunks)

    def test_symbols_referenced_extracted(self, tmp_path):
        f = _write(tmp_path, "meta.py", self.SOURCE)
        chunks = PythonChunker().chunk(f, tmp_path)
        compute = next(c for c in chunks if c.symbol_name == "compute")
        assert "some_helper" in compute.symbols_referenced

    def test_chunk_id_unique_per_chunk(self, tmp_path):
        f = _write(tmp_path, "meta.py", self.SOURCE)
        chunks = PythonChunker().chunk(f, tmp_path)
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids))
