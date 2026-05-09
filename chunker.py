"""
chunker.py — Interface générale de chunking (dispatcher modulaire)

Ce module expose :
  - ALL_EXTENSIONS : toutes les extensions supportées
  - PDF_EXTENSIONS : extensions PDF
  - CODE_EXTENSIONS : extensions code (Python, C++)
  - chunk_file() : fonction unique qui dispatche vers le bon chunker

Architecture modulaire :
  chunker.py (ce fichier)     ← Interface unifiée
    |-- code_chunker.py       ← Chunking code (Python, C++)
    |-- pdf_chunker.py        ← Chunking PDF
    |-- (extensible)          ← Ajouter d'autres chunkers ici
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Union

from code_chunker import CodeChunk, PythonChunker, CppChunker
from pdf_chunker import chunk_pdf, PDF_EXTENSIONS, DocChunk

logger = logging.getLogger("chunker")

# ---------------------------------------------------------------------------
# Extensions supportées (centralisées ici)
# ---------------------------------------------------------------------------

CODE_EXTENSIONS = {".py", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx"}
ALL_EXTENSIONS = CODE_EXTENSIONS | PDF_EXTENSIONS

# ---------------------------------------------------------------------------
# Instances des chunkers
# ---------------------------------------------------------------------------

_python_chunker = PythonChunker()
_cpp_chunker = CppChunker()


# ---------------------------------------------------------------------------
# Interface publique
# ---------------------------------------------------------------------------

def chunk_file(
    path: Path,
    root: Path,
) -> List[Union[CodeChunk, DocChunk]]:
    """
    Point d'entrée unique du module de chunking.
    Dispatche vers le chunker approprié selon l'extension du fichier.

    Args:
        path : chemin absolu du fichier à chunker
        root : chemin racine du projet (pour calculer relative_path)

    Returns:
        Liste de chunks (CodeChunk ou DocChunk selon le type de fichier)
        [] si le fichier n'est pas supporté ou en cas d'erreur
    """
    ext = path.suffix.lower()

    try:
        if ext == ".pdf":
            return _chunk_pdf_wrapper(path, root)
        elif ext in CODE_EXTENSIONS:
            return _chunk_code_wrapper(path, root, ext)
        else:
            return []

    except Exception as e:
        logger.error(f"Chunking échoué pour {path}: {e}")
        return []


def _chunk_code_wrapper(path: Path, root: Path, ext: str) -> List[CodeChunk]:
    """Wrapper pour le chunking du code (Python, C++)."""
    try:
        if ext == ".py":
            result = _python_chunker.chunk(path, root)
        else:  # C++
            result = _cpp_chunker.chunk(path, root)

        return [c for c in (result or []) if c is not None]
    except Exception as e:
        logger.error(f"Chunking code échoué pour {path}: {e}")
        return []


def _chunk_pdf_wrapper(path: Path, root: Path) -> List[DocChunk]:
    """Wrapper pour le chunking du PDF."""
    try:
        result = chunk_pdf(path, root)
        return [c for c in (result or []) if c is not None]
    except Exception as e:
        logger.error(f"Chunking PDF échoué pour {path}: {e}")
        return []