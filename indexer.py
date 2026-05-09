"""
indexer.py — Orchestration du scan de fichiers, chunking et indexation

Responsabilités :
  - Scanner un répertoire pour trouver tous les fichiers de code supportés
  - Appeler chunk_file() sur chaque fichier
  - Passer les chunks à CodeStore.upsert_chunks() (qui gère le cache)
  - Retourner un rapport d'indexation structuré

Ce module est appelé exclusivement par le handler MCP "index_codebase".
Il ne contient pas de logique d'embedding ni de retrieval.
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from chunker import chunk_file, ALL_EXTENSIONS, CodeChunk
from store import CodeStore

logger = logging.getLogger("indexer")

EXCLUDED_FILENAMES = {
    "license.py", "licence.py", "copyright.py",
    "setup.py", "conf.py",  # souvent du boilerplate pur
}

EXCLUDED_PATTERNS = {"**/generated/**", "**/migrations/**", "**/_version.py", "**/rtbx_**"}

EXCLUDED_DIRS = {"compVideoLib", "lib_ModuleVideoGeneration", "__pycache__", ".git", ".venv", "venv", "node_modules"}
EXCLUDED_ROOT_DIRS = {"Delivery", "Build", "test", "tests", "OSS", "SDD"}

# ---------------------------------------------------------------------------
# Rapport d'indexation
# ---------------------------------------------------------------------------

@dataclass
class IndexReport:
    directory: str
    files_found: int = 0
    files_indexed: int = 0       # Fichiers effectivement embeddés (nouveaux/modifiés)
    files_cached: int = 0        # Fichiers ignorés (cache valide)
    chunks_generated: int = 0
    chunks_embedded: int = 0
    failed_files: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"   Indexation terminée — {self.directory}",
            f"   Fichiers trouvés    : {self.files_found}",
            f"   Fichiers indexés    : {self.files_indexed} (nouveaux/modifiés)",
            f"   Fichiers en cache   : {self.files_cached} (inchangés, ignorés)",
            f"   Chunks générés      : {self.chunks_generated}",
            f"   Chunks embeddés     : {self.chunks_embedded}",
        ]
        if self.failed_files:
            lines.append(f"     Erreurs ({len(self.failed_files)} fichiers) :")
            for f in self.failed_files[:10]:  # Limite l'affichage
                lines.append(f"      - {f}")
            if len(self.failed_files) > 10:
                lines.append(f"      ... et {len(self.failed_files) - 10} autres")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------

class Indexer:
    """
    Orchestre le scan → chunking → indexation d'une codebase.

    Usage depuis mcp_rag_server.py :
        indexer = Indexer(store)
        report = await indexer.index_directory("/path/to/project", recursive=True)
    """

    def __init__(self, store: CodeStore):
        self.store = store

    async def index_directory(
        self,
        directory: str,
        recursive: bool = True,
        force_reindex: bool = False,
    ) -> IndexReport:
        """
        Alias pour indexer un seul répertoire.
        """
        return await self.index_directories(
            directories=[directory],
            recursive=recursive,
            force_reindex=force_reindex,
        )

    async def index_directories(
        self,
        directories: list[str],
        recursive: bool = True,
        force_reindex: bool = False,
    ) -> IndexReport:
        """
        Indexe plusieurs répertoires distincts.

        Args:
            directories  : Liste de répertoires à scanner
            recursive    : Scanner les sous-répertoires
            force_reindex: Vider la base avant d'indexer (réindexation complète)

        Returns:
            IndexReport avec le détail de l'opération
        """
        resolved_dirs = [Path(d).resolve() for d in directories]
        missing = [str(d) for d in resolved_dirs if not d.exists()]
        if missing:
            raise ValueError(f"Répertoires introuvables : {', '.join(missing)}")

        report = IndexReport(directory=", ".join(str(d) for d in resolved_dirs))

        if force_reindex:
            print("force_reindex=True → vidage de la base")
            self.store.clear()

        all_chunks: List[CodeChunk] = []
        all_files: List[Path] = []

        for dir_path in resolved_dirs:
            files = self._scan_files(dir_path, recursive)
            all_files.extend(files)
            print(f"Scan : {len(files)} fichiers trouvés dans {dir_path}")

            for file_path in files:
                try:
                    chunks = await asyncio.to_thread(chunk_file, file_path, dir_path)
                    all_chunks.extend(c for c in (chunks or []) if c is not None)
                except Exception as e:
                    logger.warning(f"Chunking échoué pour {file_path}: {e}")
                    report.failed_files.append(str(file_path))

        report.files_found = len(all_files)
        report.chunks_generated = len(all_chunks)
        print(f"Chunking : {len(all_chunks)} chunks générés")

        embedded = await asyncio.to_thread(self.store.upsert_chunks, all_chunks)
        report.chunks_embedded = embedded

        indexed_files = {c.file_path for c in all_chunks}
        report.files_indexed = len(indexed_files)
        report.files_cached = report.files_found - report.files_indexed - len(report.failed_files)

        stats = self.store.stats()
        print(
            f"Indexation terminée — base : {stats['total_chunks']} chunks / "
            f"{stats['total_files_indexed']} fichiers"
        )
        return report

    def _scan_files(self, dir_path: Path, recursive: bool) -> List[Path]:
        """Retourne les fichiers de code supportés dans le répertoire."""
        pattern = "**/*" if recursive else "*"
        return [
            p for p in dir_path.glob(pattern)
            if p.is_file()
            and p.suffix.lower() in ALL_EXTENSIONS
            and p.name.lower() not in EXCLUDED_FILENAMES
            and not any(part.lower() in EXCLUDED_DIRS for part in p.relative_to(dir_path).parts)
            and p.relative_to(dir_path).parts[0] not in EXCLUDED_ROOT_DIRS
        ]