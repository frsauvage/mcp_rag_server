"""
chunker.py — Interface générale de chunking (dispatcher modulaire)

Ce module expose :
  - ALL_EXTENSIONS : toutes les extensions supportées
  - chunk_file() : fonction unique qui dispatche vers le bon chunker

Architecture modulaire :
  chunker.py (ce fichier)     <- Interface unifiée
    |-- chunker_code.py       <- Chunking code (Python, C++)
    |-- chunker_pdf.py        <- Chunking PDF
    |-- chunker_md.py         <- Chunking Markdown / RST
    |-- chunker_proto.py      <- Chunking Protocol Buffers
"""
import logging
from pathlib import Path
from typing import List, Union

from chunker_code import CODE_EXTENSIONS, PYTHON_EXTENSIONS, CPP_EXTENSIONS, CodeChunk
from chunker_python import chunk_python
from chunker_cpp import chunk_cpp
from chunker_pdf import chunk_pdf, PDF_EXTENSIONS, DocChunk
from chunker_md import chunk_markdown, MD_EXTENSIONS
from chunker_proto import chunk_proto, PROTO_EXTENSIONS

logger = logging.getLogger("chunker")

# ---------------------------------------------------------------------------
# Extensions supportées (centralisées ici)
# ---------------------------------------------------------------------------

ALL_EXTENSIONS  = CODE_EXTENSIONS | PDF_EXTENSIONS | MD_EXTENSIONS | PROTO_EXTENSIONS

# ---------------------------------------------------------------------------
# Domaine d'embedding (code vs texte) — utilisé par store.py pour router
# chaque chunk vers le modèle d'embedding et la collection ChromaDB adaptés.
# ---------------------------------------------------------------------------

CODE_DOMAIN_EXTENSIONS = CODE_EXTENSIONS | PROTO_EXTENSIONS
TEXT_DOMAIN_EXTENSIONS = PDF_EXTENSIONS | MD_EXTENSIONS


def is_code_from_file_extension(relative_path: str) -> bool:
    """True pour Python/C++/Proto, False pour PDF/Markdown."""
    return Path(relative_path).suffix.lower() in CODE_DOMAIN_EXTENSIONS


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
        elif ext in PYTHON_EXTENSIONS:
            return _clean(chunk_python(path, root))
        elif ext in CPP_EXTENSIONS:
            return _clean(chunk_cpp(path, root))
        else:
            return []

    except Exception as e:
        logger.error(f"Chunking échoué pour {path}: {e}")
        return []

def _clean(chunks) -> list:
    """Filtre les None et les listes vides."""
    return [c for c in (chunks or []) if c is not None]
