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
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from langchain_core.messages import HumanMessage

from chunker import chunk_file, ALL_EXTENSIONS, CodeChunk
from store import CodeStore
from mcp_rag_client_llm import llm_client
from prompts import load_prompt

logger = logging.getLogger("indexer")

EXCLUDED_FILENAMES = {
    "license.py", "licence.py", "copyright.py",
    "setup.py", "conf.py",  # souvent du boilerplate pur
    "__init__.py",
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
        Indexe un répertoire.

        Si un fichier AGENT.md existe à la racine du répertoire, il est lu et
        soumis au LLM pour en extraire d'éventuelles exclusions de répertoires
        propres au RAG de recherche (voir _get_agent_md_exclusions). En l'absence
        de AGENT.md, aucune exclusion supplémentaire n'est appliquée.

        Args:
            directory    : Répertoire à indexer
            recursive    : Scanner les sous-répertoires
            force_reindex: Vider la base avant d'indexer (réindexation complète)

        Returns:
            IndexReport avec le détail de l'opération
        """
        path = Path(directory).resolve()
        if not path.is_dir():
            raise ValueError(f"Répertoire introuvable : {path}")

        report = IndexReport(directory=str(path))

        if force_reindex:
            logger.info("force_reindex=True → vidage de la base")
            self.store.clear()

        agent_exclusions = await self._get_agent_md_exclusions(path)
        if agent_exclusions:
            logger.info(f"Exclusions RAG issues de AGENT.md : {sorted(agent_exclusions)}")

        files = self._scan_files(path, recursive, agent_exclusions)
        logger.info(f"Scan : {len(files)} fichiers trouvés dans {path}")

        all_chunks: List[CodeChunk] = []

        for file_path in files:
            try:
                chunks = await asyncio.to_thread(chunk_file, file_path, path)
                all_chunks.extend(c for c in (chunks or []) if c is not None)
            except Exception as e:
                logger.warning(f"Chunking échoué pour {file_path}: {e}")
                report.failed_files.append(str(file_path))

        report.files_found = len(files)
        report.chunks_generated = len(all_chunks)
        logger.info(f"Chunking : {len(all_chunks)} chunks générés")

        embedded = await asyncio.to_thread(self.store.upsert_chunks, all_chunks)
        report.chunks_embedded = embedded

        indexed_files = {c.file_path for c in all_chunks}
        report.files_indexed = len(indexed_files)
        report.files_cached = report.files_found - report.files_indexed - len(report.failed_files)

        stats = self.store.stats()
        logger.info(
            f"Indexation terminée — base : {stats['total_chunks']} chunks / "
            f"{stats['total_files_indexed']} fichiers"
        )
        return report

    def _scan_files(
        self,
        dir_path: Path,
        recursive: bool,
        extra_excluded_prefixes: frozenset[str] = frozenset(),
    ) -> List[Path]:
        """Retourne les fichiers de code supportés dans le répertoire."""
        pattern = "**/*" if recursive else "*"
        return [
            p for p in dir_path.glob(pattern)
            if p.is_file()
            and p.suffix.lower() in ALL_EXTENSIONS
            and p.name.lower() not in EXCLUDED_FILENAMES
            and not any(part.lower() in EXCLUDED_DIRS for part in p.relative_to(dir_path).parts)
            and p.relative_to(dir_path).parts[0] not in EXCLUDED_ROOT_DIRS
            and not _is_under_excluded_prefix(p.relative_to(dir_path).parent, extra_excluded_prefixes)
        ]

    async def _get_agent_md_exclusions(self, dir_path: Path) -> frozenset[str]:
        """
        Lit AGENT.md à la racine du répertoire indexé (s'il existe) et demande
        au LLM d'en extraire les exclusions de répertoires propres au RAG de
        recherche. Retourne un ensemble vide si AGENT.md est absent, illisible,
        ou si le LLM ne trouve/renvoie rien d'exploitable.
        """
        agent_file = dir_path / "AGENT.md"
        if not agent_file.is_file():
            return frozenset()

        try:
            content = agent_file.read_text(encoding="utf-8-sig", errors="replace")
        except Exception as e:
            logger.warning(f"Lecture de {agent_file} échouée : {e}")
            return frozenset()

        if not llm_client:
            logger.warning("llm_client non configuré — exclusions AGENT.md ignorées")
            return frozenset()

        prompt = load_prompt("agent_exclusions")["template"].format(agent_file_content=content)

        try:
            message = HumanMessage(content=prompt)
            # response_format force le mode JSON côté serveur (endpoint OpenAI-
            # compatible d'Ollama) plutôt que de compter uniquement sur la
            # consigne du prompt. .bind() ne modifie pas le llm_client partagé
            # (utilisé par ailleurs pour les réponses RAG en texte libre).
            structured_llm = llm_client.bind(response_format={"type": "json_object"})
            response = await asyncio.to_thread(structured_llm.invoke, [message])
            raw = str(response.content).strip()
        except Exception as e:
            logger.warning(f"Appel LLM pour les exclusions AGENT.md échoué : {e}")
            return frozenset()

        try:
            # Filet de sécurité : même en mode JSON, un modèle local peut ajouter
            # du texte autour — on extrait le premier objet JSON trouvé plutôt
            # que d'exiger que toute la réponse soit du JSON pur.
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if not match:
                raise ValueError("aucun objet JSON trouvé dans la réponse")
            data = json.loads(match.group(0))
            excluded = data.get("exclusions", []) if isinstance(data, dict) else data
            if not isinstance(excluded, list):
                raise ValueError("champ 'exclusions' non conforme (liste attendue)")
            return frozenset(str(p).strip().strip("/\\") for p in excluded if str(p).strip())
        except Exception as e:
            logger.warning(f"Réponse LLM inexploitable pour les exclusions AGENT.md ({e}) : {raw!r}")
            return frozenset()


def _is_under_excluded_prefix(rel_dir: Path, excluded_prefixes: frozenset[str]) -> bool:
    """True si rel_dir est égal à ou sous un des chemins de excluded_prefixes."""
    if not excluded_prefixes:
        return False
    parts = rel_dir.parts
    for prefix in excluded_prefixes:
        prefix_parts = Path(prefix).parts
        if parts[:len(prefix_parts)] == prefix_parts:
            return True
    return False