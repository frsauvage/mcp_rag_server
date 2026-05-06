"""
embedder.py — Gestion des embeddings (appel API + retry + truncation)
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from mcp_rag_client_llm import embed_client, EMBED_MODEL

logger = logging.getLogger("embedder")


def embed_texts(texts: List[str], max_retries: int = 3) -> Optional[List[List[float]]]:
    """
    Calcule les embeddings d'une liste de textes.
    Appel unitaire pour isoler les erreurs par chunk.
    """
    results = []
    for text in texts:
        embedding = _embed_one(text, max_retries)
        if embedding is None:
            return None
        results.append(embedding)
    return results


def embed_query(question: str, max_retries: int = 3) -> Optional[List[float]]:
    """Calcule l'embedding d'une question (usage retrieval)."""
    return _embed_one(question, max_retries)


def _embed_one(text: str, max_retries: int) -> Optional[List[float]]:
    for attempt in range(max_retries):
        try:
            response = embed_client.embeddings.create(model=EMBED_MODEL, input=[text])
            return response.data[0].embedding
        except Exception as e:
            wait = 2 ** attempt
            logger.warning(
                f"Embedding échoué (tentative {attempt + 1}/{max_retries}) : {e}\n"
                f"  Taille : {len(text)} chars — Début : {text[:100]!r}"
            )
            time.sleep(wait)
    return None