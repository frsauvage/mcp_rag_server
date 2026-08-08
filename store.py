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
import hashlib
import logging
import os
from pathlib import Path
from typing import List, Optional

import chromadb
from chromadb.config import Settings

# Import du client embedding depuis le module dédié
from mcp_rag_client_llm import embed_client

from chunker_code import CodeChunk
from chunker import is_code_from_file_extension
from embedder import embed_texts, embed_query

logger = logging.getLogger("store")

# ---------------------------------------------------------------------------
# Configuration (surchargeable via .env)
# ---------------------------------------------------------------------------

# Taille de batch pour gpt-embed (max API : 512, on reste conservateur)
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "128"))
MAX_RERANK = int(os.getenv("MAX_RERANK", "500"))  # Nombre de résultats à récupérer pour le reranking (priorité code)

# ---------------------------------------------------------------------------
# CodeStore
# ---------------------------------------------------------------------------

class CodeStore:
    """
    Encapsule ChromaDB + embedding (deux modèles : code et texte).

    Trois collections ChromaDB internes :
      - "code_chunks"  : chunks Python/C++/Proto, embeddés avec le modèle "code"
      - "doc_chunks"   : chunks PDF/Markdown, embeddés avec le modèle "texte"
      - "file_hashes"  : file_path → SHA-256 (lookup O(1) pour le cache, sans embedding)

    Deux collections séparées car ChromaDB indexe chaque collection avec une
    dimension de vecteur fixe (HNSW) — deux modèles d'embedding aux dimensions
    différentes ne peuvent pas cohabiter dans la même collection. Le domaine
    (code vs texte) est déterminé par l'extension du fichier via
    chunker.is_code_from_file_extension().

    Exemple d'utilisation (depuis indexer.py ou retriever.py) :
        store = CodeStore(persist_dir="./chroma_db")
        store.upsert_chunks(chunks)          # indexation avec cache
        results = store.query("shutdown sequence", top_k=10)
    """

    COLLECTION_NAME      = "code_chunks"
    DOC_COLLECTION_NAME  = "doc_chunks"
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

        # Collection code : embeddings + métadonnées (Python/C++/Proto)
        # embedding_function=None : on fournit nos propres vecteurs via gpt-embed.
        # Sans ça, ChromaDB tente de télécharger un modèle HuggingFace au démarrage.
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,
        )

        # Collection texte : embeddings + métadonnées (PDF/Markdown), modèle distinct
        self._doc_collection = self._client.get_or_create_collection(
            name=self.DOC_COLLECTION_NAME,
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

        logger.info(
            f"CodeStore prêt — {self._collection.count()} chunks code, "
            f"{self._doc_collection.count()} chunks doc, "
            f"{self._hash_collection.count()} fichiers indexés "
            f"(persist: {self.persist_dir})"
        )

    def _collection_for(self, domain: str):
        return self._collection if domain == "code" else self._doc_collection

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
        """Supprime tous les chunks d'un fichier (avant de le ré-indexer), dans les deux collections."""
        for collection in (self._collection, self._doc_collection):
            try:
                collection.delete(where={"file_path": file_path})
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
            logger.info(f"Cache : {cache_hits} fichier(s) ignorés (inchangés)")

        if not to_embed:
            logger.info("Rien à embedder — tout le cache est valide")
            return 0

        embedded = self._embed_and_store(to_embed)

        logger.info(f"Embedding : {len(to_embed)} chunks ({len(by_file) - cache_hits} fichiers nouveaux/modifiés)")

        return embedded

    def _embed_and_store(self, chunks: List[CodeChunk]) -> int:
        # Grouper par fichier pour sauvegarder le hash après chaque fichier complet
        by_file: dict[str, list[CodeChunk]] = {}
        for c in chunks:
            by_file.setdefault(c.file_path, []).append(c)

        total = 0
        for file_path, file_chunks in by_file.items():
            # Le domaine (code vs texte) est le même pour tous les chunks d'un
            # fichier donné : il ne dépend que de son extension.
            domain = "code" if is_code_from_file_extension(file_chunks[0].relative_path) else "text"
            collection = self._collection_for(domain)

            # Embedder tous les chunks du fichier
            file_embedded = 0
            for i in range(0, len(file_chunks), EMBED_BATCH_SIZE):
                batch = file_chunks[i : i + EMBED_BATCH_SIZE]
                embeddings = self._embed_with_retry([c.content for c in batch], domain)
                if embeddings is None:
                    logger.error(f"❌ Batch échoué pour {file_path} ({len(batch)} chunks) — fichier ignoré")
                    logger.info(f"  ❌ Erreur: Embedding échoué pour {file_path}")
                    break
                collection.upsert(
                    ids=[c.chunk_id for c in batch],
                    documents=[c.content for c in batch],
                    embeddings=embeddings,
                    metadatas=[c.to_metadata() for c in batch],
                )
                file_embedded += len(batch)
                total += len(batch)
                logger.debug(f"  ✓ {total} chunks indexés pour {file_path} (domaine={domain})")

            # Hash sauvegardé uniquement si TOUS les chunks du fichier sont embeddés
            if file_embedded == len(file_chunks):
                self._save_file_hash(file_path, file_chunks[0].file_hash)
            elif file_embedded > 0:
                logger.warning(f"Indexation partielle pour {file_path}: {file_embedded}/{len(file_chunks)} chunks")

        return total

    def _embed_with_retry(self, texts: List[str], domain: str = "text") -> Optional[List[List[float]]]:
        return embed_texts(texts, domain=domain)

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
        Recherche hybride sur les deux collections (code/texte) :
        1. Recherche par mots exacts (lexicale)
        2. Recherche sémantique (vectorielle, embedding de la question calculé
           séparément par domaine — modèle code vs modèle texte)
        3. Fusion avec priorité absolue aux correspondances lexicales exactes,
           puis application du reranking par type de fichier.

        language_filter, si fourni, est appliqué comme filtre de métadonnées
        (where_clause) : les collections dont aucun chunk ne correspond
        renverront simplement un résultat vide.
        """
        where_clause = {}
        if language_filter:
            where_clause["language"] = language_filter
        if chapter_filter:
            where_clause["chapter"] = chapter_filter

        exact_chunks: List[dict] = []
        semantic_chunks: List[dict] = []

        for domain in ("code", "text"):
            collection = self._collection_for(domain)
            if collection.count() == 0:
                continue

            exact_chunks.extend(self._lexical_search(collection, question, where_clause))
            semantic_chunks.extend(self._semantic_search(collection, question, domain, where_clause))

        combined_chunks = self._merge_dedup(exact_chunks, semantic_chunks)
        final_chunks = self._rerank_hybrid(combined_chunks)
        return final_chunks[:top_k]

    def _lexical_search(self, collection, question: str, where_clause: dict) -> List[dict]:
        """Recherche par mots exacts (sous-chaîne) dans une collection."""
        try:
            # ChromaDB permet de chercher des sous-chaînes dans les documents
            results = collection.get(
                where_document={"$contains": question},
                where=where_clause if where_clause else None,
                include=["documents", "metadatas"],
                limit=MAX_RERANK
            )
        except Exception as e:
            logger.warning(f"Recherche lexicale échouée : {e}")
            return []

        if not results or not results["ids"]:
            return []
        return [
            {
                "content": doc,
                "metadata": meta,
                "distance": 0.0,  # Score parfait (0.0 = distance minimale) car mot exact
                "lexical_match": True,  # Flag pour le tri personnalisé
            }
            for doc, meta in zip(results["documents"], results["metadatas"])
        ]

    def _semantic_search(self, collection, question: str, domain: str, where_clause: dict) -> List[dict]:
        """Recherche vectorielle dans une collection, embedding calculé pour le domaine donné."""
        query_embedding = embed_query(question, domain=domain)
        if query_embedding is None:
            return []

        kwargs = dict(
            query_embeddings=[query_embedding],
            n_results=min(MAX_RERANK, collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        if where_clause:
            kwargs["where"] = where_clause

        results = collection.query(**kwargs)
        if not results or not results["documents"]:
            return []
        return [
            {"content": doc, "metadata": meta, "distance": dist, "lexical_match": False}
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        ]

    def _merge_dedup(self, exact_chunks: List[dict], semantic_chunks: List[dict]) -> List[dict]:
        """Fusionne lexical + sémantique, priorité au lexical, dédoublonné par contenu."""
        seen_contents = set()
        combined_chunks = []
        for chunk in exact_chunks + semantic_chunks:
            if chunk["content"] not in seen_contents:
                seen_contents.add(chunk["content"])
                combined_chunks.append(chunk)
        return combined_chunks

    def _rerank_hybrid(self, chunks: List[dict]) -> List[dict]:
        """
        Trie la pile selon l'ordre de priorité suivant :
        1. Match Lexical (Mot exact) ET Fichier de Code (.py, .cpp, .proto...)
        2. Match Lexical (Mot exact) ET Autre fichier
        3. Match Sémantique ET Fichier de Code
        4. Match Sémantique ET Autre fichier

        Au sein de chaque groupe, les éléments restent triés par distance.
        """
        lexical_code = []
        lexical_other = []
        semantic_code = []
        semantic_other = []

        for chunk in chunks:
            is_code = is_code_from_file_extension(chunk["metadata"].get("relative_path", ""))
            is_lexical = chunk.get("lexical_match", False)

            if is_lexical and is_code:
                lexical_code.append(chunk)
            elif is_lexical and not is_code:
                lexical_other.append(chunk)
            elif not is_lexical and is_code:
                semantic_code.append(chunk)
            else:
                semantic_other.append(chunk)

        # Recombinaison de la pile dans l'ordre strict des priorités
        return lexical_code + lexical_other + semantic_code + semantic_other

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
        code_count = self._collection.count()
        doc_count = self._doc_collection.count()
        return {
            "total_chunks": code_count + doc_count,
            "total_chunks_code": code_count,
            "total_chunks_doc": doc_count,
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
        for name in (self.COLLECTION_NAME, self.DOC_COLLECTION_NAME, self.HASH_COLLECTION_NAME):
            try:
                self._client.delete_collection(name)
            except Exception:
                pass  # Collection might not exist

        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,
        )
        self._doc_collection = self._client.get_or_create_collection(
            name=self.DOC_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,
        )
        self._hash_collection = self._client.get_or_create_collection(
            name=self.HASH_COLLECTION_NAME,
            embedding_function=None,
        )
        logger.info("Vectorial base cleaned")


def _stable_id(file_path: str) -> str:
    """ID déterministe pour la collection de hashes."""
    return hashlib.sha256(file_path.encode()).hexdigest()[:16]
