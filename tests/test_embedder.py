"""Tests unitaires pour embedder.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_max_embed_chars(monkeypatch):
    """Fixe MAX_EMBED_CHARS à 100 pour les tests de taille et isole les mocks."""
    monkeypatch.setenv("MAX_EMBED_CHARS", "100")
    import importlib

    import embedder
    importlib.reload(embedder)
    # Réinitialise l'état des mocks entre chaque test (side_effect, call_count, return_value)
    embedder.embed_client.reset_mock()
    embedder.embed_client_code.reset_mock()
    # reset_mock() ne réinitialise pas side_effect par défaut — le faire explicitement
    embedder.embed_client.embeddings.create.reset_mock(return_value=True, side_effect=True)
    embedder.embed_client_code.embeddings.create.reset_mock(return_value=True, side_effect=True)
    yield
    importlib.reload(embedder)


def _fake_response(vector: list[float]) -> MagicMock:
    resp = MagicMock()
    resp.data = [MagicMock(embedding=vector)]
    return resp


# ── _is_embed_size_valid ──────────────────────────────────────────────────────

class TestIsEmbedSizeValid:
    def test_short_text_valid(self):
        import embedder
        assert embedder._is_embed_size_valid("a" * 50) is True

    def test_exact_limit_valid(self):
        import embedder
        assert embedder._is_embed_size_valid("a" * embedder.MAX_EMBED_CHARS) is True

    def test_too_long_invalid(self):
        import embedder
        assert embedder._is_embed_size_valid("a" * (embedder.MAX_EMBED_CHARS + 1)) is False

    def test_empty_string_valid(self):
        import embedder
        assert embedder._is_embed_size_valid("") is True


# ── _embed_one ────────────────────────────────────────────────────────────────

class TestEmbedOne:
    def test_returns_vector_on_success(self):
        import embedder
        vec = [0.1, 0.2, 0.3]
        embedder.embed_client.embeddings.create.return_value = _fake_response(vec)
        result = embedder._embed_one("hello", max_retries=1)
        assert result == vec

    def test_returns_none_on_exception_after_retries(self):
        import embedder
        embedder.embed_client.embeddings.create.side_effect = Exception("connection refused")
        with patch("time.sleep"):
            result = embedder._embed_one("hello", max_retries=2)
        assert result is None

    def test_retries_on_empty_response(self):
        import embedder
        empty_resp = MagicMock()
        empty_resp.data = []
        embedder.embed_client.embeddings.create.return_value = empty_resp
        with patch("time.sleep"):
            result = embedder._embed_one("hello", max_retries=2)
        assert result is None
        assert embedder.embed_client.embeddings.create.call_count >= 2

    def test_retries_correct_number_of_times(self):
        import embedder
        embedder.embed_client.embeddings.create.side_effect = Exception("err")
        with patch("time.sleep"):
            embedder._embed_one("hello", max_retries=3)
        assert embedder.embed_client.embeddings.create.call_count == 3

    def test_succeeds_on_second_attempt(self):
        import embedder
        vec = [1.0, 2.0]
        embedder.embed_client.embeddings.create.side_effect = [
            Exception("first fail"),
            _fake_response(vec),
        ]
        with patch("time.sleep"):
            result = embedder._embed_one("hello", max_retries=3)
        assert result == vec


# ── embed_texts ───────────────────────────────────────────────────────────────

class TestEmbedTexts:
    def test_returns_list_of_vectors(self):
        import embedder
        vec = [0.5, 0.6]
        embedder.embed_client.embeddings.create.return_value = _fake_response(vec)
        result = embedder.embed_texts(["a" * 10, "b" * 10])
        assert result is not None
        assert len(result) == 2
        assert result[0] == vec

    def test_returns_none_if_any_text_too_long(self):
        import embedder
        long_text = "x" * (embedder.MAX_EMBED_CHARS + 1)
        result = embedder.embed_texts(["short", long_text])
        assert result is None

    def test_empty_list_returns_empty(self):
        import embedder
        result = embedder.embed_texts([])
        assert result == []

    def test_returns_none_on_embed_failure(self):
        import embedder
        embedder.embed_client.embeddings.create.side_effect = Exception("fail")
        with patch("time.sleep"):
            result = embedder.embed_texts(["a" * 10])
        assert result is None


# ── embed_query ───────────────────────────────────────────────────────────────

class TestEmbedQuery:
    def test_returns_vector_for_valid_query(self):
        import embedder
        vec = [0.1, 0.9]
        embedder.embed_client.embeddings.create.return_value = _fake_response(vec)
        result = embedder.embed_query("what is X?")
        assert result == vec

    def test_returns_none_for_too_long_query(self):
        import embedder
        result = embedder.embed_query("q" * (embedder.MAX_EMBED_CHARS + 1))
        assert result is None

    def test_returns_none_on_api_error(self):
        import embedder
        embedder.embed_client.embeddings.create.side_effect = Exception("timeout")
        with patch("time.sleep"):
            result = embedder.embed_query("query", max_retries=1)
        assert result is None


# ── Routage par domaine (code vs texte) ────────────────────────────────────────

class TestDomainRouting:
    def test_default_domain_uses_text_client(self):
        import embedder
        vec = [0.1, 0.2]
        embedder.embed_client_text.embeddings.create.return_value = _fake_response(vec)
        result = embedder.embed_query("question")
        assert result == vec
        embedder.embed_client_text.embeddings.create.assert_called_once()

    def test_code_domain_uses_code_client(self):
        import embedder
        vec = [0.3, 0.4]
        embedder.embed_client_code.embeddings.create.return_value = _fake_response(vec)
        result = embedder.embed_query("def foo(): pass", domain="code")
        assert result == vec
        embedder.embed_client_code.embeddings.create.assert_called_once()

    def test_code_domain_does_not_call_text_client(self):
        import embedder
        embedder.embed_client_code.embeddings.create.return_value = _fake_response([0.5])
        embedder.embed_query("code snippet", domain="code")
        embedder.embed_client_text.embeddings.create.assert_not_called()

    def test_embed_texts_routes_by_domain(self):
        import embedder
        vec = [0.6, 0.7]
        embedder.embed_client_code.embeddings.create.return_value = _fake_response(vec)
        result = embedder.embed_texts(["a" * 10], domain="code")
        assert result == [vec]
