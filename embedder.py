"""
embedder.py — Gestion des embeddings (appel API + retry + truncation)
"""
from __future__ import annotations

import os
import logging
import time
from typing import List, Optional

from mcp_rag_client_llm import embed_client, EMBED_MODEL

MAX_EMBED_CHARS = int(os.getenv("MAX_EMBED_CHARS", "2000"))  # Limite stricte pour nomic-embed-text

logger = logging.getLogger("embedder")


def embed_texts(texts: List[str], max_retries: int = 3) -> Optional[List[List[float]]]:
    results = []
    for text in texts:
        if not _is_embed_size_valid(text):
            return None
        embedding = _embed_one(text, max_retries)
        if embedding is None:
            return None
        results.append(embedding)
    return results


def embed_query(question: str, max_retries: int = 3) -> Optional[List[float]]:
    if not _is_embed_size_valid(question):
        return None
    return _embed_one(question, max_retries)


def _embed_one(text: str, max_retries: int) -> Optional[List[float]]:
    for attempt in range(max_retries):
        try:
            response = embed_client.embeddings.create(model=EMBED_MODEL, input=[text])
            if not response or not response.data:
                logger.error(f"Réponse vide d'embedding pour le modèle {EMBED_MODEL}")
                time.sleep(2 ** attempt)
                continue
            return response.data[0].embedding
        except Exception as e:
            wait = 2 ** attempt
            error_msg = str(e)
            # Logs plus détaillés pour Ollama
            logger.error(
                f"Embedding échoué (tentative {attempt + 1}/{max_retries}) — Model: {EMBED_MODEL}\n"
                f"  Erreur: {error_msg}\n"
                f"  Taille: {len(text)} chars"
            )
            if attempt < max_retries - 1:
                logger.info(f"  Nouvelle tentative dans {wait}s...")
                time.sleep(wait)
    
    logger.error(f"Échec définitif d'embedding après {max_retries} tentatives pour {len(text)} chars")
    return None


def _is_embed_size_valid(text: str) -> bool:
    if len(text) <= MAX_EMBED_CHARS:
        return True
    logger.error(
        f"Chunk trop long pour l'embedding ({len(text)} > {MAX_EMBED_CHARS} chars). "
        "Vérifie le chunker ou ajuste MAX_EMBED_CHARS."
    )
    return False