"""
store.py — Persistance vectorielle (ChromaDB) avec cache par hash de fichier

Responsabilités :
  - Stocker et retrouver des CodeChunk via leurs embeddings
  - Invalider le cache uniquement si le contenu d'un fichier a changé (SHA-256)
  - Batcher les appels à gpt-embed pour les performances
  - Exposer une interface simple : upsert_chunks(), query(), stats(), clear()

Ce module ne sait rien du MCP ni du LLM de génération : il ne fait que de l'embedding
et de la recherche vectorielle. C'est le retriever.py qui orchestre la logique RAG.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import List, Optional

import chromadb
from chromadb.config import Settings

# Import du client embedding depuis le module dédié
from mcp_rag_client_llm import embed_client

from code_chunker import CodeChunk
from embedder import embed_texts, embed_query

logger = logging.getLogger("store")

# ---------------------------------------------------------------------------
# Configuration (surchargeable via .env)
# ---------------------------------------------------------------------------

# Taille de batch pour gpt-embed (max API : 512, on reste conservateur)
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "128"))

# ---------------------------------------------------------------------------
# CodeStore
# ---------------------------------------------------------------------------

class CodeStore:
    """
    Encapsule ChromaDB + embedding Mistral.

    Deux collections ChromaDB internes :
      - "code_chunks"  : les chunks avec leurs embeddings (collection principale)
      - "file_hashes"  : file_path → SHA-256 (lookup O(1) pour le cache, sans embedding)

    Exemple d'utilisation (depuis indexer.py ou retriever.py) :
        store = CodeStore(persist_dir="./chroma_db")
        store.upsert_chunks(chunks)          # indexation avec cache
        results = store.query("shutdown sequence", top_k=10)
    """

    COLLECTION_NAME      = "code_chunks"
    HASH_COLLECTION_NAME = "file_hashes"

    def __init__(self, persist_dir: str):
        import shutil
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._client = chromadb.PersistentClient(
                path=str(self.persist_dir),
                settings=Settings(anonymized_telemetry=False),
                tenant="default_tenant",
                database="default_database",
            )
        except (chromadb.errors.InternalError, chromadb.errors.NotFoundError) as e:
            if "malformed" in str(e).lower() or "not found" in str(e).lower():
                logger.warning("Base ChromaDB corrompue ou tenant introuvable — suppression et recreation automatique.")
                shutil.rmtree(self.persist_dir)
                self.persist_dir.mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(
                    path=str(self.persist_dir),
                    settings=Settings(anonymized_telemetry=False),
                    tenant="default_tenant",
                    database="default_database",
                )
            else:
                raise

        # Collection principale : embeddings + métadonnées
        # embedding_function=None : on fournit nos propres vecteurs via gpt-embed.
        # Sans ça, ChromaDB tente de télécharger un modèle HuggingFace au démarrage.
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,
        )

        # Collection secondaire : hashes de fichiers (pas d'embedding du tout)
        self._hash_collection = self._client.get_or_create_collection(
            name=self.HASH_COLLECTION_NAME,
            embedding_function=None,
        )

        # Remplacer par :
        self._embedder = embed_client

        print(
            f"CodeStore prêt — {self._collection.count()} chunks, "
            f"{self._hash_collection.count()} fichiers indexés "
            f"(persist: {self.persist_dir})"
        )

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def is_file_cached(self, file_path: str, current_hash: str) -> bool:
        """True si le fichier est déjà indexé avec exactement ce hash."""
        try:
            result = self._hash_collection.get(ids=[_stable_id(file_path)])
            if result["ids"]:
                return result["metadatas"][0].get("file_hash", "") == current_hash
        except Exception:
            pass
        return False

    def _save_file_hash(self, file_path: str, file_hash: str):
        self._hash_collection.upsert(
            ids=[_stable_id(file_path)],
            embeddings=[[0.0]],
            documents=[""],
            metadatas=[{"file_path": file_path, "file_hash": file_hash}],
        )

    def _delete_file_chunks(self, file_path: str):
        """Supprime tous les chunks d'un fichier (avant de le ré-indexer)."""
        try:
            self._collection.delete(where={"file_path": file_path})
        except Exception as e:
            logger.warning(f"Suppression chunks échouée pour {file_path}: {e}")

    # ------------------------------------------------------------------
    # Indexation
    # ------------------------------------------------------------------

    def upsert_chunks(self, chunks: List[CodeChunk]) -> int:
        """
        Indexe une liste de chunks avec gestion du cache.

        Logique par fichier :
          - Hash identique → fichier ignoré (cache valide)
          - Hash différent → anciens chunks supprimés, nouveaux embeddés
          - Fichier nouveau → embeddé directement

        Retourne le nombre de chunks effectivement embeddés.
        """
        if not chunks:
            return 0

        # Grouper par fichier
        by_file: dict[str, list[CodeChunk]] = {}
        for c in chunks:
            by_file.setdefault(c.file_path, []).append(c)

        to_embed: List[CodeChunk] = []
        cache_hits = 0

        for file_path, file_chunks in by_file.items():
            fhash = file_chunks[0].file_hash
            if self.is_file_cached(file_path, fhash):
                cache_hits += 1
                continue
            self._delete_file_chunks(file_path)
            to_embed.extend(file_chunks)

        if cache_hits:
            print(f"Cache : {cache_hits} fichier(s) ignorés (inchangés)")

        if not to_embed:
            print("Rien à embedder — tout le cache est valide")
            return 0

        embedded = self._embed_and_store(to_embed)

        print(f"Embedding : {len(to_embed)} chunks ({len(by_file) - cache_hits} fichiers nouveaux/modifiés)")

        return embedded

    def _embed_and_store(self, chunks: List[CodeChunk]) -> int:
        # Grouper par fichier pour sauvegarder le hash après chaque fichier complet
        by_file: dict[str, list[CodeChunk]] = {}
        for c in chunks:
            by_file.setdefault(c.file_path, []).append(c)

        total = 0
        for file_path, file_chunks in by_file.items():
            # Embedder tous les chunks du fichier
            file_embedded = 0
            for i in range(0, len(file_chunks), EMBED_BATCH_SIZE):
                batch = file_chunks[i : i + EMBED_BATCH_SIZE]
                embeddings = self._embed_with_retry([c.content for c in batch])
                if embeddings is None:
                    logger.error(f"❌ Batch échoué pour {file_path} ({len(batch)} chunks) — fichier ignoré")
                    print(f"  ❌ Erreur: Embedding échoué pour {file_path}")
                    break
                self._collection.upsert(
                    ids=[c.chunk_id for c in batch],
                    documents=[c.content for c in batch],
                    embeddings=embeddings,
                    metadatas=[c.to_metadata() for c in batch],
                )
                file_embedded += len(batch)
                total += len(batch)
                print(f"  ✓ {total} chunks indexés")

            # Hash sauvegardé uniquement si TOUS les chunks du fichier sont embeddés
            if file_embedded == len(file_chunks):
                self._save_file_hash(file_path, file_chunks[0].file_hash)
            elif file_embedded > 0:
                logger.warning(f"Indexation partielle pour {file_path}: {file_embedded}/{len(file_chunks)} chunks")

        return total

    def _embed_with_retry(self, texts: List[str]) -> Optional[List[List[float]]]:
        return embed_texts(texts)

    # ------------------------------------------------------------------
    # Retrieval bas niveau (utilisé par retriever.py)
    # ------------------------------------------------------------------

    def similarity_search(
        self,
        question: str,
        top_k: int,
        language_filter: Optional[str] = None,
        chapter_filter: Optional[str] = None,
    ) -> List[dict]:
        """
        Recherche sémantique pure : embedding de la question → top_k chunks.

        Retourne une liste de dicts :
          { "content": str, "metadata": dict, "distance": float }
        triés par distance croissante (plus proche = plus pertinent).
        """
        if self._collection.count() == 0:
            return []

        query_embedding = embed_query(question)
        if query_embedding is None:
            return []

        kwargs = dict(
            query_embeddings=[query_embedding],
            n_results=min(top_k, self._collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        where = {}
        if language_filter:
            where["language"] = language_filter
        if chapter_filter:
            where["chapter"] = chapter_filter
        if where:
            kwargs["where"] = where

        results = self._collection.query(**kwargs)

        return [
            {"content": doc, "metadata": meta, "distance": dist}
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        ]

    def get_chunks_by_symbol(self, symbol_name: str, limit: int = 3) -> List[dict]:
        """
        Récupère des chunks dont le symbol_name correspond exactement.
        Utilisé par retriever.py pour l'expansion des dépendances.
        """
        try:
            result = self._collection.get(
                where={"symbol_name": symbol_name},
                include=["documents", "metadatas"],
                limit=limit,
            )
            return [
                {"content": doc, "metadata": meta, "distance": 0.5}
                for doc, meta in zip(result["documents"], result["metadatas"])
            ]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        return {
            "total_chunks": self._collection.count(),
            "total_files_indexed": self._hash_collection.count(),
            "persist_dir": str(self.persist_dir),
        }

    def delete_file(self, file_path: str):
        """Retire un fichier de l'index (chunks + hash)."""
        self._delete_file_chunks(file_path)
        try:
            self._hash_collection.delete(ids=[_stable_id(file_path)])
        except Exception:
            pass

    def clear(self):
        """Vide complètement la base vectorielle."""
        try:
            self._client.delete_collection(self.COLLECTION_NAME)
        except Exception:
            pass  # Collection might not exist
        try:
            self._client.delete_collection(self.HASH_COLLECTION_NAME)
        except Exception:
            pass  # Collection might not exist
        
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,
        )
        self._hash_collection = self._client.get_or_create_collection(
            name=self.HASH_COLLECTION_NAME,
            embedding_function=None,
        )
        print("Vectorial base cleaned")


def _stable_id(file_path: str) -> str:
    """ID déterministe pour la collection de hashes."""
    return hashlib.sha256(file_path.encode()).hexdigest()[:16]
