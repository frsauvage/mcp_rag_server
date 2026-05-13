# md_chunker.py
from __future__ import annotations
import hashlib
import logging
import re
from pathlib import Path
from typing import List
from pdf_chunker import DocChunk, _extract_chapter, _pdf_hash

logger = logging.getLogger("md_chunker")
MIN_SECTION_CHARS = 100

MD_EXTENSIONS = {".md", ".rst"}

def chunk_markdown(path: Path, root: Path) -> List[DocChunk]:
    try:
        source = path.read_text(encoding="utf-8-sig", errors="replace")
        fhash = _pdf_hash(path)
        relative = str(path.relative_to(root))

        # Découper sur les titres # ## ###
        sections = re.split(r'^(#{1,3} .+)$', source, flags=re.MULTILINE)

        chunks = []
        title = path.stem
        current_text = ""
        page = 1

        for part in sections:
            if re.match(r'^#{1,3} ', part):
                if current_text.strip() and len(current_text.strip()) >= MIN_SECTION_CHARS:
                    chapter = _extract_chapter(title)
                    header = f"# Document : {relative}\n# Section : {title}\n\n"
                    chunks.append(DocChunk(
                        content=header + current_text.strip(),
                        file_path=str(path),
                        relative_path=relative,
                        chunk_type="section",
                        symbol_name=title,
                        chapter=chapter,
                        page_start=page,
                        page_end=page,
                        level=part.count('#', 0, part.index(' ')),
                        file_hash=fhash,
                    ))
                    page += 1
                title = part.lstrip('#').strip()
                current_text = ""
            else:
                current_text += part

        # Dernière section
        if current_text.strip() and len(current_text.strip()) >= MIN_SECTION_CHARS:
            chapter = _extract_chapter(title)
            header = f"# Document : {relative}\n# Section : {title}\n\n"
            chunks.append(DocChunk(
                content=header + current_text.strip(),
                file_path=str(path),
                relative_path=relative,
                chunk_type="section",
                symbol_name=title,
                chapter=chapter,
                page_start=page,
                page_end=page,
                level=1,
                file_hash=fhash,
            ))

        logger.info(f"{path.name} : {len(chunks)} sections extraites")
        return chunks

    except Exception as e:
        logger.error(f"Chunking markdown échoué pour {path}: {e}")
        return []
