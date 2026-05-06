"""
retriever.py — Retrieval RAG et construction du contexte LLM

Responsabilités :
  - Orchestrer la recherche sémantique (via CodeStore.similarity_search)
  - Étendre les résultats avec les symboles référencés (expansion des dépendances)
  - Assembler les chunks en un bloc de contexte prêt à être injecté dans un prompt LLM
  - Fournir un prompt complet (contexte + question) au mcp_rag_server.py

C'est ici que réside la logique "comprendre TOUT le code avant de répondre" :
on ne se contente pas du top-K sémantique, on tire aussi le fil des dépendances
pour reconstituer le contexte d'interdépendance.
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from store import CodeStore

logger = logging.getLogger("retriever")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Nombre de chunks retournés par la recherche sémantique initiale
DEFAULT_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "10"))

# Profondeur d'expansion des dépendances
#   0 = pas d'expansion (sémantique pure)
#   1 = ajoute les chunks des symboles directement référencés
#   2 = ajoute aussi les dépendances de dépendances (attention au coût)
DEPENDENCY_DEPTH = int(os.getenv("DEPENDENCY_EXPANSION_DEPTH", "1"))

# Taille max du contexte injecté dans le prompt (en caractères)
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "14000"))


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------

class Retriever:
    """
    Orchestre le retrieval RAG en deux passes :

    Passe 1 — Recherche sémantique
        Embedding de la question → similarité cosinus → top_k chunks

    Passe 2 — Expansion des dépendances (si depth > 0)
        Pour chaque chunk retourné, on regarde ses `symbols_referenced`
        et on fetch les chunks des symboles correspondants dans la base.
        Cela permet de "tirer le fil" des interdépendances sans charger
        toute la codebase.

    Usage depuis mcp_rag_server.py :
        retriever = Retriever(store)
        prompt = retriever.build_prompt(question="Comment fonctionne le shutdown ?")
        answer = await llm_call(prompt)
    """

    def __init__(self, store: CodeStore):
        self.store = store

    def retrieve(
        self,
        question: str,
        top_k: int = DEFAULT_TOP_K,
        language_filter: Optional[str] = None,
        expand_deps: bool = True,
    ) -> List[dict]:
        """
        Retourne les chunks pertinents pour une question.

        Args:
            question        : La question en langage naturel
            top_k           : Nombre de chunks de la passe sémantique
            language_filter : "python" | "cpp" | None
            expand_deps     : Activer l'expansion des dépendances

        Returns:
            Liste de dicts { content, metadata, distance }
            triée par pertinence (distance cosinus croissante)
        """
        if self.store.stats()["total_chunks"] == 0:
            logger.warning("La base est vide — lancez d'abord index_codebase")
            return []

        # Passe 1 : recherche sémantique
        chunks = self.store.similarity_search(question, top_k, language_filter)
        print(f"Passe 1 : {len(chunks)} chunks récupérés")

        if not chunks:
            return []

        # Passe 2 : expansion des dépendances
        if expand_deps and DEPENDENCY_DEPTH > 0:
            chunks = self._expand(chunks, depth=DEPENDENCY_DEPTH)
            print(f"Passe 2 : {len(chunks)} chunks après expansion")

        return chunks

    def build_prompt(
        self,
        question: str,
        top_k: int = DEFAULT_TOP_K,
        language_filter: Optional[str] = None,
        expand_deps: bool = True,
    ) -> tuple[str, int]:
        """
        Construit le prompt complet (contexte RAG + question) prêt pour le LLM.

        Returns:
            (prompt: str, nb_chunks_used: int)
        """
        chunks = self.retrieve(question, top_k, language_filter, expand_deps)
        if not chunks:
            return (
                f"Question : {question}\n\n"
                "(Aucun contexte de code trouvé. La base est peut-être vide.)",
                0,
            )

        context = _build_context(chunks, max_chars=MAX_CONTEXT_CHARS)
        prompt = (
            "Tu es un expert en analyse de code (Python et C++).\n"
            "Voici des extraits de code pertinents issus de la codebase :\n\n"
            f"{context}\n\n"
            "---\n"
            f"Question : {question}\n\n"
            "Réponds précisément en t'appuyant sur les extraits fournis. "
            "Cite les fichiers et les noms de symboles concernés."
        )
        return prompt, len(chunks)

    def build_file_prompt(
        self,
        file_content: str,
        file_path: str,
        user_prompt: str,
    ) -> str:
        """
        Construit un prompt pour l'analyse d'un fichier unique,
        enrichi avec le contexte RAG des symboles liés.
        """
        # Cherche le contexte RAG lié au contenu de ce fichier
        rag_chunks = self.retrieve(
            question=f"{user_prompt} file:{file_path}",
            top_k=6,
            expand_deps=True,
        )
        rag_context = _build_context(rag_chunks, max_chars=6_000) if rag_chunks else ""

        parts = []
        if rag_context:
            parts.append(f"Contexte issu de la codebase :\n{rag_context}\n")
        parts.append(
            f"{user_prompt}\n\n"
            f"Fichier analysé : {file_path}\n\n"
            f"```\n{file_content}\n```"
        )
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Expansion des dépendances
    # ------------------------------------------------------------------

    def _expand(self, chunks: List[dict], depth: int) -> List[dict]:
        """
        Ajoute les chunks des symboles référencés, récursivement jusqu'à `depth`.
        Évite les doublons via un ensemble d'IDs déjà vus.
        """
        seen_ids = {_chunk_id(c) for c in chunks}
        result = list(chunks)

        current_level = chunks
        for _ in range(depth):
            next_level = []
            for chunk in current_level:
                refs_raw = chunk["metadata"].get("symbols_referenced", "")
                refs = [r for r in refs_raw.split("|") if r]
                for symbol in refs:
                    for dep_chunk in self.store.get_chunks_by_symbol(symbol):
                        cid = _chunk_id(dep_chunk)
                        if cid not in seen_ids:
                            seen_ids.add(cid)
                            dep_chunk["distance"] = 0.5  # Distance synthétique
                            result.append(dep_chunk)
                            next_level.append(dep_chunk)
            current_level = next_level
            if not current_level:
                break

        return result


# ---------------------------------------------------------------------------
# Construction du contexte
# ---------------------------------------------------------------------------

def _build_context(chunks: List[dict], max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """
    Assemble les chunks en un bloc de contexte pour le LLM.

    - Triés par distance croissante (plus pertinent en premier)
    - Tronqués proprement à max_chars (jamais au milieu d'un chunk)
    - Chaque chunk est annoté avec son fichier, symbole et lignes
    """
    sorted_chunks = sorted(chunks, key=lambda c: c.get("distance", 1.0))
    parts: List[str] = []
    total = 0

    for chunk in sorted_chunks:
        meta = chunk["metadata"]
        header = (
            f"\n--- [{meta['language'].upper()}] {meta['symbol_name']} "
            f"| {meta['relative_path']} L{meta['start_line']}–{meta['end_line']} ---\n"
        )
        block = header + chunk["content"]

        if total + len(block) > max_chars:
            remaining = max_chars - total
            if remaining > len(header) + 100:
                parts.append(block[:remaining] + "\n[... tronqué ...]")
            break

        parts.append(block)
        total += len(block)

    return "\n".join(parts)


def _chunk_id(chunk: dict) -> str:
    meta = chunk["metadata"]
    return f"{meta['file_path']}:{meta['start_line']}"
