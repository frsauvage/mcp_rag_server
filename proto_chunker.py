"""
proto_chunker.py — Chunking syntaxique pour fichiers Protocol Buffers (.proto)

Stratégie :
  - 1 chunk par message (avec ses champs)
  - 1 chunk par service (avec tous ses RPCs)
  - 1 chunk par enum
  - Le package et les imports sont ajoutés en tête de chaque chunk
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("proto_chunker")

MIN_CHUNK_LINES = 2

PROTO_EXTENSIONS = {".proto"}


# ---------------------------------------------------------------------------
# Réutilise CodeChunk de chunker.py
# ---------------------------------------------------------------------------

from code_chunker import CodeChunk, file_hash


# ---------------------------------------------------------------------------
# ProtoChunker
# ---------------------------------------------------------------------------

class ProtoChunker:
    """
    Parse les fichiers .proto sans dépendance externe (regex sur la syntaxe proto).
    Capture : messages, services (avec RPCs), enums.
    """

    # Patterns de détection des blocs de premier niveau
    BLOCK_START = re.compile(
        r'^(message|service|enum|oneof)\s+(\w+)\s*\{', re.MULTILINE
    )

    def chunk(self, path: Path, root: Path) -> List[CodeChunk]:
        source = path.read_text(encoding="utf-8-sig", errors="replace")
        fhash = file_hash(path)
        relative = str(path.relative_to(root))
        chunks: List[CodeChunk] = []

        header = self._extract_header(source)  # package + imports
        blocks = self._extract_blocks(source)

        for block_type, block_name, block_content, start_line, end_line in blocks:
            if end_line - start_line < MIN_CHUNK_LINES:
                continue

            chunk_header = f"// File: {relative}\n{header}\n\n" if header else f"// File: {relative}\n\n"

            chunks.append(CodeChunk(
                content=chunk_header + block_content,
                file_path=str(path),
                relative_path=relative,
                language="proto",
                chunk_type=block_type,      # "message" | "service" | "enum"
                symbol_name=block_name,
                start_line=start_line,
                end_line=end_line,
                file_hash=fhash,
                symbols_referenced=self._extract_refs(block_content, block_type),
            ))

        return chunks

    def _extract_header(self, source: str) -> str:
        """Extrait le package et les imports (contexte minimal pour chaque chunk)."""
        lines = []
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("syntax") or stripped.startswith("package") or stripped.startswith("import") or stripped.startswith("option"):
                lines.append(line)
        return "\n".join(lines)

    def _extract_blocks(self, source: str) -> List[tuple]:
        """
        Extrait les blocs message/service/enum avec leur contenu complet.
        Gère les accolades imbriquées.
        Returns list of (block_type, block_name, content, start_line, end_line)
        """
        results = []
        lines = source.splitlines()
        i = 0

        while i < len(lines):
            line = lines[i]
            match = re.match(r'^\s*(message|service|enum)\s+(\w+)\s*\{', line)
            if match:
                block_type = match.group(1)
                block_name = match.group(2)
                start_line = i + 1  # 1-indexé
                depth = 0
                block_lines = []

                # Capturer jusqu'à la fermeture de l'accolade racine
                while i < len(lines):
                    block_lines.append(lines[i])
                    depth += lines[i].count('{') - lines[i].count('}')
                    if depth == 0 and block_lines:
                        break
                    i += 1

                end_line = i + 1
                results.append((
                    block_type,
                    block_name,
                    "\n".join(block_lines),
                    start_line,
                    end_line,
                ))
            i += 1

        return results

    def _extract_refs(self, content: str, block_type: str) -> List[str]:
        """
        Extrait les références :
        - Pour les messages : types des champs (autres messages)
        - Pour les services : types des requêtes et réponses RPC
        """
        refs = set()
        if block_type == "service":
            # rpc MyMethod (RequestType) returns (ResponseType)
            for match in re.finditer(r'rpc\s+\w+\s*\(\s*(\w+)\s*\)\s*returns\s*\(\s*(\w+)\s*\)', content):
                refs.add(match.group(1))
                refs.add(match.group(2))
        elif block_type == "message":
            # proto3 : les champs peuvent ne pas avoir de label
            # ex: MyType field_name = 1;  ou  repeated MyType field_name = 1;
            for match in re.finditer(
                r'(?:repeated|map\s*<\w+\s*,\s*)?\s*([A-Z]\w+)\s+\w+\s*=\s*\d+',
                content
            ):
                refs.add(match.group(1))
        return sorted(refs)


# ---------------------------------------------------------------------------
# Dispatcher public
# ---------------------------------------------------------------------------

_proto_chunker = ProtoChunker()


def chunk_proto(path: Path, root: Path) -> List[CodeChunk]:
    try:
        logger.info(f"Chunking proto : {path.relative_to(root)}")
        result = _proto_chunker.chunk(path, root)
        return [c for c in (result or []) if c is not None]
    except Exception as e:
        logger.error(f"Chunking proto échoué pour {path}: {e}")
        return []
