"""
embedder.py — Gestion des embeddings (appel API + retry + truncation)
"""
from __future__ import annotations

import os
import logging
import time
from typing import List, Optional

from mcp_rag_client_llm import (
    embed_client,
    embed_client_code,
    embed_client_text,
    EMBED_MODEL_CODE,
    EMBED_MODEL_TEXT,
)

MAX_EMBED_CHARS = int(os.getenv("MAX_EMBED_CHARS", "2000"))  # Limite stricte pour nomic-embed-text
# Fenêtres spécifiques par domaine (fallback sur MAX_EMBED_CHARS si non renseignées) :
# un modèle d'embedding code peut avoir une fenêtre de contexte différente du modèle texte.
MAX_EMBED_CHARS_CODE = int(os.getenv("MAX_EMBED_CHARS_CODE", str(MAX_EMBED_CHARS)))
MAX_EMBED_CHARS_TEXT = int(os.getenv("MAX_EMBED_CHARS_TEXT", str(MAX_EMBED_CHARS)))

logger = logging.getLogger("embedder")

# domain -> (client, modèle, limite de taille)
_DOMAIN_CONFIG = {
    "code": (embed_client_code, EMBED_MODEL_CODE, MAX_EMBED_CHARS_CODE),
    "text": (embed_client_text, EMBED_MODEL_TEXT, MAX_EMBED_CHARS_TEXT),
}


def embed_texts(texts: List[str], domain: str = "text", max_retries: int = 3) -> Optional[List[List[float]]]:
    results = []
    for text in texts:
        if not _is_embed_size_valid(text, domain):
            return None
        embedding = _embed_one(text, max_retries, domain)
        if embedding is None:
            return None
        results.append(embedding)
    return results


def embed_query(question: str, domain: str = "text", max_retries: int = 3) -> Optional[List[float]]:
    if not _is_embed_size_valid(question, domain):
        return None
    return _embed_one(question, max_retries, domain)


def _embed_one(text: str, max_retries: int, domain: str = "text") -> Optional[List[float]]:
    client, model, _ = _DOMAIN_CONFIG[domain]
    for attempt in range(max_retries):
        try:
            response = client.embeddings.create(model=model, input=[text])
            if not response or not response.data:
                logger.error(f"Réponse vide d'embedding pour le modèle {model}")
                time.sleep(2 ** attempt)
                continue
            return response.data[0].embedding
        except Exception as e:
            wait = 2 ** attempt
            error_msg = str(e)
            # Logs plus détaillés pour Ollama
            logger.error(
                f"Embedding échoué (tentative {attempt + 1}/{max_retries}) — Model: {model}\n"
                f"  Erreur: {error_msg}\n"
                f"  Taille: {len(text)} chars"
            )
            if attempt < max_retries - 1:
                logger.info(f"  Nouvelle tentative dans {wait}s...")
                time.sleep(wait)

    logger.error(f"Échec définitif d'embedding après {max_retries} tentatives pour {len(text)} chars")
    return None


def _is_embed_size_valid(text: str, domain: str = "text") -> bool:
    _, _, max_chars = _DOMAIN_CONFIG[domain]
    if len(text) <= max_chars:
        return True
    logger.error(
        f"Chunk trop long pour l'embedding ({len(text)} > {max_chars} chars, domaine={domain}). "
        "Vérifie le chunker ou ajuste MAX_EMBED_CHARS."
    )
    return False