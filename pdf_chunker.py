"""
pdf_chunker.py — Chunking de PDFs par section (via table des matières pymupdf)

Stratégie :
  - Extraction de la TOC (table des matières) via pymupdf
  - Chaque section = 1 chunk (texte entre page début et page début section suivante)
  - Fallback par page si pas de TOC
  - Métadonnées : titre section, page début, page fin, niveau hiérarchique
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("pdf_chunker")

MIN_SECTION_CHARS = 100  # Section trop courte → ignorée
MAX_CHUNK_CHARS = 1700   # Limite corps de chunk (header ajouté après, nomic~2000 chars max)
CHAPTER_RE = re.compile(r'^(?:chapitre\s*)?(\d+(?:\.\d+)*)(?:[\.\s:-]+.*)?$', re.IGNORECASE)


def _extract_chapter(title: str) -> str:
    title = title.strip()
    match = CHAPTER_RE.match(title)
    return match.group(1) if match else ""

@dataclass
class DocChunk:
    content: str
    file_path: str
    relative_path: str
    chunk_type: str          # "section" | "page"
    symbol_name: str         # Titre de la section
    page_start: int
    page_end: int
    level: int               # Niveau hiérarchique TOC (1=H1, 2=H2, ...)
    file_hash: str
    chapter: str = ""

    @property
    def chunk_id(self) -> str:
        raw = f"{self.file_path}:p{self.page_start}:{self.symbol_name}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_metadata(self) -> dict:
        return {
            "file_path": self.file_path,
            "relative_path": self.relative_path,
            "language": "pdf",
            "chunk_type": self.chunk_type,
            "symbol_name": self.symbol_name,
            "chapter": self.chapter,
            "start_line": self.page_start,   # réutilise start_line pour ChromaDB
            "end_line": self.page_end,
            "file_hash": self.file_hash,
            "symbols_referenced": "",
            "page_start": self.page_start,
            "page_end": self.page_end,
            "level": self.level,
        }


def _pdf_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


class PdfChunker:
    """
    Chunke un PDF par section en utilisant la TOC pymupdf.
    Fallback par page si pas de TOC.
    """

    def chunk(self, path: Path, root: Path) -> List[DocChunk]:
        try:
            import fitz  # pymupdf
        except ImportError:
            raise ImportError("pymupdf non installé. Lancez : pip install pymupdf")

        fhash = _pdf_hash(path)
        relative = str(path.relative_to(root))
        doc = fitz.open(str(path))

        toc = doc.get_toc()  # [[level, title, page], ...]
        if toc:
            print(f"{path.name} : TOC détectée ({len(toc)} sections)")
            chunks = self._chunk_by_toc(doc, toc, path, relative, fhash)
        else:
            print(f"{path.name} : pas de TOC, fallback par page")
            chunks = self._chunk_by_page(doc, path, relative, fhash)

        doc.close()
        return chunks

    def _chunk_by_toc(self, doc, toc, path, relative, fhash) -> List[DocChunk]:
        chunks = []
        nb_pages = doc.page_count

        for i, (level, title, page_start) in enumerate(toc):
            # Page de fin = page de début de la section suivante - 1
            if i + 1 < len(toc):
                page_end = toc[i + 1][2] - 1
            else:
                page_end = nb_pages

            # Extraire le texte des pages de la section (1-indexé → 0-indexé)
            text = self._extract_text(doc, page_start - 1, page_end - 1)

            if len(text.strip()) < MIN_SECTION_CHARS:
                logger.debug(f"Section ignorée (trop courte) : {title}")
                continue

            text_pieces = self._split_text(text, MAX_CHUNK_CHARS)
            for part_index, piece in enumerate(text_pieces, start=1):
                part_title = title
                if len(text_pieces) > 1:
                    part_title = f"{title} (part {part_index}/{len(text_pieces)})"
                header = f"# Document : {relative}\n# Section : {part_title} (p. {page_start}–{page_end})\n\n"
                chunks.append(DocChunk(
                    content=header + piece,
                    file_path=str(path),
                    relative_path=relative,
                    chapter=_extract_chapter(title),
                    chunk_type="section",
                    symbol_name=part_title,
                    page_start=page_start,
                    page_end=page_end,
                    level=level,
                    file_hash=fhash,
                ))

        return chunks

    def _chunk_by_page(self, doc, path, relative, fhash) -> List[DocChunk]:
        """Fallback : 1 chunk par page."""
        chunks = []
        for page_num in range(doc.page_count):
            page = doc[page_num]
            text = page.get_text("text").strip()
            if len(text) < MIN_SECTION_CHARS:
                continue

            text_pieces = self._split_text(text, MAX_CHUNK_CHARS)
            for part_index, piece in enumerate(text_pieces, start=1):
                part_title = f"Page {page_num + 1}"
                if len(text_pieces) > 1:
                    part_title = f"Page {page_num + 1} (part {part_index}/{len(text_pieces)})"
                header = f"# Document : {relative}\n# {part_title}\n\n"
                chunks.append(DocChunk(
                    content=header + piece,
                    file_path=str(path),
                    relative_path=relative,
                    chapter="",
                    chunk_type="page",
                    symbol_name=part_title,
                    page_start=page_num + 1,
                    page_end=page_num + 1,
                    level=0,
                    file_hash=fhash,
                ))
        return chunks

    def _extract_text(self, doc, page_start: int, page_end: int) -> str:
        """Extrait le texte des pages [page_start, page_end] (0-indexé)."""
        parts = []
        for i in range(page_start, min(page_end + 1, doc.page_count)):
            parts.append(doc[i].get_text("text"))
        return "\n\n".join(parts)

    def _split_text(self, text: str, max_chars: int) -> List[str]:
        """Découpe un texte long en morceaux plus petits, sans couper les paragraphes si possible."""
        if len(text) <= max_chars:
            return [text]

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
        chunks: List[str] = []
        current = []
        current_len = 0

        def flush_current():
            if current:
                chunks.append("\n\n".join(current).strip())

        for paragraph in paragraphs:
            if len(paragraph) > max_chars:
                flush_current()
                current = []
                current_len = 0
                chunks.extend(self._split_long_paragraph(paragraph, max_chars))
                continue

            if current_len + len(paragraph) + (2 if current else 0) <= max_chars:
                if current:
                    current.append(paragraph)
                    current_len += len(paragraph) + 2
                else:
                    current.append(paragraph)
                    current_len += len(paragraph)
            else:
                flush_current()
                current = [paragraph]
                current_len = len(paragraph)

        flush_current()
        return [chunk for chunk in chunks if len(chunk) >= MIN_SECTION_CHARS]

    def _split_long_paragraph(self, paragraph: str, max_chars: int) -> List[str]:
        """Découpe un paragraphe trop long en morceaux de taille max_chars."""
        return [paragraph[i:i + max_chars].strip() for i in range(0, len(paragraph), max_chars) if paragraph[i:i + max_chars].strip()]


_pdf_chunker = PdfChunker()

PDF_EXTENSIONS = {".pdf"}


def chunk_pdf(path: Path, root: Path) -> List[DocChunk]:
    try:
        print(f"Chunking PDF : {path.relative_to(root)}")
        result = _pdf_chunker.chunk(path, root)
        return [c for c in (result or []) if c is not None]
    except Exception as e:
        logger.error(f"Chunking PDF échoué pour {path}: {e}")
        return []