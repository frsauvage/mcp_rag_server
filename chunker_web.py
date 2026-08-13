"""
chunker_web.py — Chunking de pages web crawlées (RecursiveUrlLoader)

Réutilise DocChunk (chunker_pdf.py) tel quel : le cache de store.py (upsert_chunks)
ne connaît que cette interface, pas le type d'origine du contenu. file_path = URL
de la page (clé de cache), file_hash = SHA-256 du texte extrait (pas de mtime web).
"""
import hashlib
import logging
import re
import os
import getpass
from typing import List, Optional

from bs4 import BeautifulSoup
from langchain_community.document_loaders import RecursiveUrlLoader

from chunker_pdf import DocChunk
from mcp_rag_client_llm import PATH_CA

logger = logging.getLogger("chunker_web")
_wam_session_cache: dict[str, str] = {}  # évite de redemander le mdp à chaque appel dans un même run

MIN_SECTION_CHARS = 100
MAX_CHUNK_CHARS = 1700  # même limite que PDF/MD

WEB_CRAWL_DEPTH = int(os.getenv("WEB_CRAWL_DEPTH", "2"))
WEB_CRAWL_MAX_PAGES = int(os.getenv("WEB_CRAWL_MAX_PAGES", "200"))

WEB_CRAWL_JSESSIONID = os.getenv("WEB_CRAWL_JSESSIONID", None)
WEB_CRAWL_WAM_COOKIE_NAME = os.getenv("WEB_CRAWL_WAM_COOKIE_NAME", None)
WEB_CRAWL_WAM_COOKIE_KEY = os.getenv("WEB_CRAWL_WAM_COOKIE_KEY", None)

WEB_CRAWL_EXCLUDE_DIRS = tuple(
    d.strip() for d in os.getenv("WEB_CRAWL_EXCLUDE_DIRS", "").split(",") if d.strip()
)

AUTH_WALL_MARKERS = (
    "user couldn't be identified",
    "web access management",
    "captcha",
    "$captchaScriptName",
    "$captchaAttributes",
    "TGI/email",

)

# Marqueur inséré avant chaque titre h1/h2/h3 pour permettre un découpage par
# section après extraction (get_text() aplatit sinon toute la page en un seul
# bloc, sans double saut de ligne exploitable par un split sur paragraphes).
SECTION_MARKER = "§§SECTION§§"
SECTION_RE = re.compile(rf"{re.escape(SECTION_MARKER)}(.*?){re.escape(SECTION_MARKER)}")

def _extract_text(html: str) -> str:
    """Extrait le texte en insérant un marqueur+titre avant chaque h1/h2/h3,
    pour permettre un découpage par section (voir _split_into_sections)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    for heading in soup.find_all(["h1", "h2", "h3"]):
        title = heading.get_text(strip=True)
        heading.insert_before(f"\n\n{SECTION_MARKER}{title}{SECTION_MARKER}\n\n")

    return soup.get_text(separator="\n", strip=True)


def _split_into_sections(text: str, default_title: str) -> List[tuple[str, str]]:
    """Découpe le texte marqué en [(titre_section, contenu), ...].

    Si aucun marqueur n'a été inséré (page sans h1/h2/h3), retourne une seule
    section avec default_title (généralement le <title> de la page).
    """
    parts = SECTION_RE.split(text)
    # re.split avec groupe capturant alterne : [avant_1er_marqueur, titre1, contenu1, titre2, contenu2, ...]
    if len(parts) == 1:
        return [(default_title, text)]

    sections: List[tuple[str, str]] = []
    preamble = parts[0].strip()
    if len(preamble) >= MIN_SECTION_CHARS:
        sections.append((default_title, preamble))

    for i in range(1, len(parts), 2):
        title = parts[i].strip() or default_title
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if content:
            sections.append((title, content))

    return sections or [(default_title, text)]


def _is_auth_wall(text: str) -> bool:
    lowered = text.lower()
    hits = sum(1 for marker in AUTH_WALL_MARKERS if marker in lowered)
    return hits >= 2


def _split_text(text: str, max_chars: int) -> List[str]:
    """Découpe par paragraphes si une section dépasse max_chars."""
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    def flush():
        if current:
            chunks.append("\n\n".join(current).strip())

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            flush()
            current.clear()
            chunks.extend(
                paragraph[i:i + max_chars].strip()
                for i in range(0, len(paragraph), max_chars)
                if paragraph[i:i + max_chars].strip()
            )
            current_len = 0
            continue
        sep = 2 if current else 0
        if current_len + len(paragraph) + sep <= max_chars:
            current.append(paragraph)
            current_len += len(paragraph) + sep
        else:
            flush()
            current = [paragraph]
            current_len = len(paragraph)

    flush()
    return [c for c in chunks if len(c) >= MIN_SECTION_CHARS] or [text[:max_chars]]


def crawl_and_chunk(
    root_url: str,
    depth: int = WEB_CRAWL_DEPTH,
    max_pages: Optional[int] = WEB_CRAWL_MAX_PAGES,
) -> List[DocChunk]:
    """
    Crawle root_url et retourne des DocChunk compatibles avec CodeStore.upsert_chunks().
    Chaque page est découpée par section (h1/h2/h3), comme chunker_md pour le Markdown ;
    une section trop longue est ensuite re-découpée par paragraphes (_split_text).
    """
    
    if not WEB_CRAWL_JSESSIONID:
        raise RuntimeError("no WEB_CRAWL_JSESSIONID configured in .env")

    if not WEB_CRAWL_WAM_COOKIE_NAME:
        raise RuntimeError("no WEB_CRAWL_WAM_COOKIE_NAME configured in .env")

    cookie_header = f"JSESSIONID={os.getenv("WEB_CRAWL_JSESSIONID")}; " \
        f"{WEB_CRAWL_WAM_COOKIE_NAME}={os.getenv("WEB_CRAWL_WAM_COOKIE_KEY")}"
    
    loader = RecursiveUrlLoader(
        url=root_url,
        max_depth=depth,
        extractor=_extract_text,
        prevent_outside=True,
        exclude_dirs=WEB_CRAWL_EXCLUDE_DIRS,
        timeout=10,
        headers={"Cookie": cookie_header} or None,
    )
    documents = loader.load()
    if max_pages is not None:
        documents = documents[:max_pages]

    chunks: List[DocChunk] = []

    for doc in documents:
        source = doc.metadata.get("source", root_url)
        page_title = (doc.metadata.get("title") or source).strip()
        text = doc.page_content.strip()

        if len(text) < MIN_SECTION_CHARS:
            continue

        sections = _split_into_sections(text, default_title=page_title)

        for level, (section_title, section_text) in enumerate(sections, start=1):
            if len(section_text) < MIN_SECTION_CHARS:
                continue

            fhash = hashlib.sha256(section_text.encode("utf-8")).hexdigest()
            pieces = _split_text(section_text, MAX_CHUNK_CHARS)

            for part_index, piece in enumerate(pieces, start=1):
                part_title = section_title if len(pieces) == 1 else f"{section_title} (part {part_index}/{len(pieces)})"
                header = f"# Page web : {source}\n# Section : {part_title}\n\n"
                chunks.append(DocChunk(
                    content=header + piece,
                    file_path=source,
                    relative_path=source,
                    chunk_type="webpage",
                    symbol_name=part_title,
                    page_start=level,
                    page_end=len(sections),
                    level=level,
                    file_hash=fhash,
                    language="web",
                ))

    logger.info(f"{root_url} : {len(documents)} pages crawlées, {len(chunks)} chunks générés")
    return chunks