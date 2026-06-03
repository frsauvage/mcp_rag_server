from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import List, Optional

# Imports stricts depuis le fichier de base commun
from code_chunker import (
    CodeChunk,
    _extract_lines,
    _is_worth_chunking,
    _strip_file_header,
    file_hash,
    MAX_CHUNK_CHARS,
    MIN_CHUNK_LINES
)

logger = logging.getLogger("code_chunker.python")


class PythonChunker:
    """Extrait des chunks sémantiques depuis du code Python."""

    def chunk(self, path: Path, root: Path) -> List[CodeChunk]:
        source = path.read_text(encoding="utf-8-sig", errors="replace")
        source = _strip_file_header(source, "python")
        if not _is_worth_chunking(source, "python"):
            logger.debug(f"Fichier ignoré — pas assez de code : {path.name}")
            return []
        fhash = file_hash(path)
        relative = str(path.relative_to(root))
        chunks: List[CodeChunk] = []

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as e:
            logger.warning(f"SyntaxError dans {path}: {e} — fichier ignoré")
            return []

        import_block = self._collect_imports(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                chunks.append(self._make_class_chunk(node, source, import_block, path, relative, fhash))
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        chunk = self._make_function_chunk(item, source, import_block, path, relative, fhash, prefix=node.name)
                        if chunk:
                            chunks.append(chunk)

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if self._is_top_level(node, tree):
                    chunk = self._make_function_chunk(node, source, import_block, path, relative, fhash, prefix=None)
                    if chunk:
                        chunks.append(chunk)

        return chunks

    def _is_top_level(self, node: ast.AST, tree: ast.Module) -> bool:
        for parent in ast.walk(tree):
            if isinstance(parent, ast.ClassDef) and node in ast.walk(parent):
                return False
        return True

    def _collect_imports(self, source: str) -> str:
        lines = []
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    start = node.lineno
                    end = getattr(node, "end_lineno", start)
                    lines.append(_extract_lines(source, start, end))
        except SyntaxError:
            pass
        return "\n".join(lines)

    def _extract_refs(self, node: ast.AST) -> List[str]:
        refs = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Name):
                    refs.add(child.func.id)
                elif isinstance(child.func, ast.Attribute):
                    refs.add(child.func.attr)
        return sorted(refs)

    def _function_skeleton(self, node: ast.AST, source: str) -> str:
        lines = [_extract_lines(source, node.lineno, node.lineno)]
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            lines.append(_extract_lines(source, node.body[0].lineno, node.body[0].end_lineno or node.body[0].lineno))
        lines.append("    ...")
        return "\n".join(lines)

    def _make_function_chunk(self, node, source, import_block, path, relative, fhash, prefix) -> Optional[CodeChunk]:
        start, end = node.lineno, node.end_lineno or node.lineno
        if end - start < MIN_CHUNK_LINES:
            return None
        body = _extract_lines(source, start, end)
        if len(body) > MAX_CHUNK_CHARS:
            body = self._function_skeleton(node, source)
        symbol = f"{prefix}::{node.name}" if prefix else node.name
        header = f"# File: {relative}\n{import_block}\n\n" if import_block else f"# File: {relative}\n\n"
        return CodeChunk(
            content=header + body,
            file_path=str(path), relative_path=relative,
            language="python",
            chunk_type="method" if prefix else "function",
            symbol_name=symbol,
            start_line=start, end_line=end,
            file_hash=fhash,
            symbols_referenced=self._extract_refs(node),
        )

    def _make_class_chunk(self, node, source, import_block, path, relative, fhash) -> CodeChunk:
        start, end = node.lineno, node.end_lineno or node.lineno
        body = _extract_lines(source, start, end)
        header = f"# File: {relative}\n{import_block}\n\n" if import_block else f"# File: {relative}\n\n"

        if len(body) > MAX_CHUNK_CHARS:
            body = self._class_skeleton(node, source)        

        return CodeChunk(
            content=header + body,
            file_path=str(path), relative_path=relative,
            language="python", chunk_type="class",
            symbol_name=node.name,
            start_line=start, end_line=end,
            file_hash=fhash,
            symbols_referenced=self._extract_refs(node),
        )

    def _class_skeleton(self, node: ast.ClassDef, source: str) -> str:
        lines = [_extract_lines(source, node.lineno, node.lineno)]
        for item in node.body:
            if isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant):
                lines.append(_extract_lines(source, item.lineno, item.end_lineno or item.lineno))
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lines.append(_extract_lines(source, item.lineno, item.lineno) + " ...")
            elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                lines.append(_extract_lines(source, item.lineno, item.end_lineno or item.lineno))
        return "\n".join(lines)


_python_chunker = PythonChunker()

def chunk_python(path: Path, root: Path) -> List[CodeChunk]:
    return _python_chunker.chunk(path, root)