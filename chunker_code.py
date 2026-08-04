import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

logger = logging.getLogger("chunker_code")

CODE_EXTENSIONS = {".py", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx"}

MIN_CHUNK_LINES = 3
MAX_CHUNK_CHARS = 1700   # Limite corps de chunk (header ajouté après)
MIN_CODE_LINES = 3
MAX_CODE_LINES = 10_000  # au-delà → probablement du code généré/minifié


@dataclass
class CodeChunk:
    content: str                          # Texte brut du chunk (avec header de contexte)
    file_path: str                        # Chemin absolu du fichier source
    relative_path: str                    # Chemin relatif à la racine du projet
    language: str                         # "python" | "cpp"
    chunk_type: str                       # "function" | "method" | "class" | "struct" | "free_function"
    symbol_name: str                      # Nom qualifié complet
    start_line: int
    end_line: int
    file_hash: str                        # SHA-256 du fichier source entier
    symbols_referenced: List[str] = field(default_factory=list)
    chapter: str = ""

    @property
    def chunk_id(self) -> str:
        """Identifiant stable et unique basé sur (file_path, start_line)."""
        raw = f"{self.file_path}:{self.start_line}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def to_metadata(self) -> dict:
        """Sérialise les métadonnées pour ChromaDB."""
        return {
            "file_path": self.file_path,
            "relative_path": self.relative_path,
            "language": self.language,
            "chunk_type": self.chunk_type,
            "symbol_name": self.symbol_name,
            "chapter": self.chapter,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "file_hash": self.file_hash,
            "symbols_referenced": "|".join(self.symbols_referenced),
        }


def file_hash(path: Path) -> str:
    """Calcule le SHA-256 du contenu d'un fichier."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _extract_lines(source: str, start: int, end: int) -> str:
    """Extrait les lignes [start, end] (1-indexé, inclusif)."""
    return "\n".join(source.splitlines()[start - 1: end])


def _is_worth_chunking(source: str, language: str) -> bool:
    lines = source.splitlines()
    nb_lines = len(lines)
    if nb_lines < MIN_CODE_LINES or nb_lines > MAX_CODE_LINES:
        return False
    if language == "cpp":
        comment_lines = [l for l in lines if l.strip().startswith("//") or l.strip().startswith("*") or l.strip().startswith("/*")]
    else:
        comment_lines = [l for l in lines if l.strip().startswith("#")]
    code_lines = [l for l in lines if l.strip() and l not in comment_lines]
    return len(code_lines) / nb_lines > 0.1


def _strip_file_header(source: str, language: str) -> str:
    lines = source.splitlines(keepends=True)
    i = 0
    if language == "cpp":
        in_block = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("/*"):
                in_block = True
            if in_block and "*/" in stripped:
                i += 1
                break
            if not in_block and stripped and not stripped.startswith("//"):
                break
    else:
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                break
    return "".join(lines[i:])


def chunk_code(path: Path, root: Path, ext: str) -> List[CodeChunk]:
    """Point d'entrée principal pour le découpage de fichiers de code."""
    if ext == ".py":
        from chunker_python import chunk_python
        return chunk_python(path, root)
    elif ext in {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx"}:
        from chunker_cpp import chunk_cpp
        return chunk_cpp(path, root)
    return []