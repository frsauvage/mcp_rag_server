"""Tests unitaires pour retriever.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from retriever import Retriever, _build_context, _chunk_id

# ── Helpers ───────────────────────────────────────────────────────────────────

def _chunk(
    file_path: str = "/proj/foo.py",
    start_line: int = 1,
    symbol: str = "my_func",
    language: str = "python",
    relative: str = "foo.py",
    end_line: int = 10,
    content: str = "def my_func(): pass",
    distance: float = 0.2,
    symbols_referenced: str = "",
    chapter: str = "",
    lexical: bool = False,
) -> dict:
    return {
        "content": content,
        "metadata": {
            "file_path": file_path,
            "relative_path": relative,
            "language": language,
            "chunk_type": "function",
            "symbol_name": symbol,
            "start_line": start_line,
            "end_line": end_line,
            "file_hash": "abc123",
            "symbols_referenced": symbols_referenced,
            "chapter": chapter,
        },
        "distance": distance,
        "lexical_match": lexical,
    }


def _make_store(chunks=None, total=10):
    store = MagicMock()
    store.stats.return_value = {"total_chunks": total, "total_files_indexed": 1}
    store.similarity_search.return_value = chunks or []
    store.get_chunks_by_symbol.return_value = []
    return store


# ── _chunk_id ─────────────────────────────────────────────────────────────────

class TestChunkId:
    def test_format(self):
        c = _chunk(file_path="/a/b.py", start_line=5)
        assert _chunk_id(c) == "/a/b.py:5"

    def test_unique_per_position(self):
        a = _chunk(file_path="/a.py", start_line=1)
        b = _chunk(file_path="/a.py", start_line=2)
        assert _chunk_id(a) != _chunk_id(b)


# ── _build_context ────────────────────────────────────────────────────────────

class TestBuildContext:
    def test_empty_chunks_returns_empty(self):
        assert _build_context([]) == ""

    def test_single_chunk_in_output(self):
        c = _chunk(symbol="foo", content="def foo(): pass")
        result = _build_context([c], max_chars=5000)
        assert "foo" in result
        assert "def foo(): pass" in result

    def test_sorted_by_distance(self):
        near = _chunk(symbol="near", distance=0.1, content="near content")
        far = _chunk(symbol="far", distance=0.9, content="far content", start_line=50)
        result = _build_context([far, near], max_chars=5000)
        assert result.index("near content") < result.index("far content")

    def test_truncated_at_max_chars(self):
        chunks = [_chunk(symbol=f"f{i}", start_line=i * 10, content="x" * 500, distance=float(i) / 10)
                  for i in range(20)]
        result = _build_context(chunks, max_chars=1000)
        assert len(result) <= 1200  # slack pour les headers

    def test_header_contains_file_info(self):
        c = _chunk(relative="src/utils.py", symbol="helper", start_line=5, end_line=15)
        result = _build_context([c], max_chars=5000)
        assert "src/utils.py" in result
        assert "helper" in result

    def test_chapter_label_when_present(self):
        c = _chunk(chapter="3.2")
        result = _build_context([c], max_chars=5000)
        assert "3.2" in result

    def test_no_chapter_label_when_absent(self):
        c = _chunk(chapter="")
        result = _build_context([c], max_chars=5000)
        assert "Chapitre" not in result


# ── Retriever._extract_chapter ────────────────────────────────────────────────

class TestExtractChapter:
    def setup_method(self):
        self.r = Retriever(MagicMock())

    @pytest.mark.parametrize("question,expected", [
        ("Explique le chapitre 3", "3"),
        ("Voir chapitre.4 pour plus", "4"),
        ("chapter 2.1 details", "2.1"),
        ("Chapter 10 overview", "10"),
        ("Aucune mention de chapitre", ""),
        ("", ""),
    ])
    def test_extract(self, question, expected):
        assert self.r._extract_chapter(question) == expected


# ── Retriever.retrieve ────────────────────────────────────────────────────────

class TestRetrieverRetrieve:
    def test_empty_store_returns_empty(self):
        store = _make_store(total=0)
        r = Retriever(store)
        assert r.retrieve("any question") == []

    def test_returns_similarity_results(self):
        chunks = [_chunk(symbol="alpha"), _chunk(symbol="beta", start_line=20)]
        store = _make_store(chunks=chunks)
        r = Retriever(store)
        result = r.retrieve("some question", expand_deps=False)
        assert len(result) == 2

    def test_calls_similarity_search_with_question(self):
        store = _make_store(chunks=[_chunk()])
        r = Retriever(store)
        r.retrieve("my question", top_k=5, expand_deps=False)
        store.similarity_search.assert_called_once()
        args = store.similarity_search.call_args
        assert "my question" in args[0] or args[1].get("question") == "my question" or args[0][0] == "my question"

    def test_chapter_filter_applied_when_in_question(self):
        store = _make_store(chunks=[_chunk()])
        r = Retriever(store)
        r.retrieve("voir chapitre 5 pour les détails", expand_deps=False)
        call_kwargs = store.similarity_search.call_args
        # Doit passer chapter_filter="5"
        all_args = list(call_kwargs[0]) + list(call_kwargs[1].values())
        assert "5" in all_args

    def test_no_chapter_filter_without_keyword(self):
        store = _make_store(chunks=[_chunk()])
        r = Retriever(store)
        r.retrieve("how does shutdown work?", expand_deps=False)
        call_kwargs = store.similarity_search.call_args[1]
        assert call_kwargs.get("chapter_filter", "") == ""

    def test_language_filter_forwarded(self):
        store = _make_store(chunks=[_chunk()])
        r = Retriever(store)
        r.retrieve("question", language_filter="cpp", expand_deps=False)
        args = store.similarity_search.call_args
        assert "cpp" in list(args[0]) + list(args[1].values())


# ── Retriever._expand ─────────────────────────────────────────────────────────

class TestRetrieverExpand:
    def test_expand_adds_dependency_chunks(self):
        base = _chunk(symbol="foo", symbols_referenced="bar")
        dep = _chunk(symbol="bar", start_line=100, content="def bar(): pass")

        store = _make_store()
        store.get_chunks_by_symbol.return_value = [dep]

        r = Retriever(store)
        result = r._expand([base], depth=1)

        symbols = {c["metadata"]["symbol_name"] for c in result}
        assert "foo" in symbols
        assert "bar" in symbols

    def test_expand_no_duplicates(self):
        base = _chunk(symbol="foo", symbols_referenced="bar")
        dep = _chunk(symbol="bar", start_line=100)

        store = _make_store()
        store.get_chunks_by_symbol.return_value = [dep]

        r = Retriever(store)
        # Call expand twice conceptually — dep should appear only once
        result = r._expand([base, dep], depth=1)

        ids = [_chunk_id(c) for c in result]
        assert len(ids) == len(set(ids))

    def test_expand_depth_zero_no_expansion(self):
        base = _chunk(symbol="foo", symbols_referenced="bar")
        store = _make_store()
        r = Retriever(store)
        result = r._expand([base], depth=0)
        assert len(result) == 1
        store.get_chunks_by_symbol.assert_not_called()

    def test_expand_sets_synthetic_distance(self):
        base = _chunk(symbol="foo", symbols_referenced="bar")
        dep = _chunk(symbol="bar", start_line=100)
        store = _make_store()
        store.get_chunks_by_symbol.return_value = [dep]
        r = Retriever(store)
        result = r._expand([base], depth=1)
        added = [c for c in result if c["metadata"]["symbol_name"] == "bar"]
        assert added[0]["distance"] == 0.5

    def test_expand_empty_refs_no_extra_chunks(self):
        base = _chunk(symbol="foo", symbols_referenced="")
        store = _make_store()
        r = Retriever(store)
        result = r._expand([base], depth=1)
        assert len(result) == 1


# ── Retriever.build_prompt ────────────────────────────────────────────────────

class TestBuildPrompt:
    def test_empty_store_returns_fallback_prompt(self):
        store = _make_store(total=0)
        r = Retriever(store)
        prompt, nb = r.build_prompt("what is X?")
        assert nb == 0
        assert "vide" in prompt or "Aucun" in prompt

    def test_returns_prompt_with_context(self):
        chunks = [_chunk(symbol="func_a", content="def func_a(): pass")]
        store = _make_store(chunks=chunks)
        r = Retriever(store)
        prompt, nb = r.build_prompt("what does func_a do?", expand_deps=False)
        assert nb > 0
        assert "func_a" in prompt
        assert "Question" in prompt

    def test_returns_nb_chunks_used(self):
        chunks = [_chunk(symbol=f"f{i}", start_line=i * 10) for i in range(5)]
        store = _make_store(chunks=chunks)
        r = Retriever(store)
        _, nb = r.build_prompt("question", expand_deps=False)
        assert nb == 5
