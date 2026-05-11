"""
chunker.py — Interface générale de chunking (dispatcher modulaire)

Ce module expose :
  - ALL_EXTENSIONS : toutes les extensions supportées
  - chunk_file() : fonction unique qui dispatche vers le bon chunker

Architecture modulaire :
  chunker.py (ce fichier)     <- Interface unifiée
    |-- code_chunker.py       <- Chunking code (Python, C++)
    |-- pdf_chunker.py        <- Chunking PDF
    |-- md_chunker.py         <- Chunking Markdown / RST
    |-- proto_chunker.py      <- Chunking Protocol Buffers
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Union

from code_chunker import chunk_code, CODE_EXTENSIONS, CodeChunk
from pdf_chunker import chunk_pdf, PDF_EXTENSIONS, DocChunk
from md_chunker import chunk_markdown, MD_EXTENSIONS
from proto_chunker import chunk_proto, PROTO_EXTENSIONS

logger = logging.getLogger("chunker")

# ---------------------------------------------------------------------------
# Extensions supportées (centralisées ici)
# ---------------------------------------------------------------------------

ALL_EXTENSIONS  = CODE_EXTENSIONS | PDF_EXTENSIONS | MD_EXTENSIONS | PROTO_EXTENSIONS


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
    logger.info(f"Chunking : {path.relative_to(root)}...")

    try:
        if ext in PDF_EXTENSIONS:
            return _clean(chunk_pdf(path, root))
        elif ext in MD_EXTENSIONS:
            return _clean(chunk_markdown(path, root))
        elif ext in PROTO_EXTENSIONS:
            return _clean(chunk_proto(path, root))
        elif ext in CODE_EXTENSIONS:
            return _clean(chunk_code(path, root, ext))
        else:
            return []

    except Exception as e:
        logger.error(f"Chunking échoué pour {path}: {e}")
        return []

def _clean(chunks) -> list:
    """Filtre les None et les listes vides."""
    return [c for c in (chunks or []) if c is not None]
