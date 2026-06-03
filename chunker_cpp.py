from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

# Imports stricts depuis le fichier de base commun
from code_chunker import (
    CodeChunk,
    _is_worth_chunking,
    _strip_file_header,
    file_hash,
    MAX_CHUNK_CHARS
)

logger = logging.getLogger("code_chunker.cpp")


class CppChunker:
    """Extrait des chunks sémantiques depuis du code C++ via tree-sitter."""

    CAPTURE_TYPES = {"function_definition", "class_specifier", "struct_specifier"}

    def __init__(self):
        self._parser = None

    def _get_parser(self):
        if self._parser is not None:
            return self._parser
        try:
            import tree_sitter_cpp as tscpp
            from tree_sitter import Language, Parser
            self._parser = Parser(Language(tscpp.language()))
        except ImportError:
            raise ImportError(
                "tree-sitter-cpp non installé. "
                "Lancez : pip install tree-sitter tree-sitter-cpp"
            )
        return self._parser

    def chunk(self, path: Path, root: Path) -> List[CodeChunk]:
        source_bytes = path.read_bytes()
        if source_bytes.startswith(b"\xef\xbb\xbf"):
            source_bytes = source_bytes[3:]
            
        source = source_bytes.decode("utf-8", errors="replace")
        source = _strip_file_header(source, "cpp")
        
        if not _is_worth_chunking(source, "cpp"):
            logger.debug(f"Fichier ignoré — pas assez de code : {path.name}")
            return []
            
        source_bytes = source.encode("utf-8")
        fhash = file_hash(path)
        relative = str(path.relative_to(root))
        chunks: List[CodeChunk] = []

        try:
            parser = self._get_parser()
        except ImportError as e:
            logger.error(str(e))
            return []

        tree = parser.parse(source_bytes)
        self._walk(tree.root_node, source_bytes, path, relative, fhash, chunks, namespace_stack=[])
        logger.info(f"Extracted {len(chunks)} chunks from {relative}")
        return chunks

    def _walk(self, node, source_bytes, path, relative, fhash, chunks, namespace_stack):
        if node.type == "namespace_definition":
            ns_name = self._child_text(node, source_bytes, "namespace_identifier")
            self._walk_children(node, source_bytes, path, relative, fhash, chunks,
                                namespace_stack + ([ns_name] if ns_name else []))
            return

        if node.type in self.CAPTURE_TYPES:
            chunk = self._make_chunk(node, source_bytes, path, relative, fhash, namespace_stack)
            if chunk:
                chunks.append(chunk)
            
            if node.type in ("class_specifier", "struct_specifier"):
                type_name = self._get_type_name(node, source_bytes)
                self._walk_children(node, source_bytes, path, relative, fhash, chunks,
                                    namespace_stack + ([type_name] if type_name else []))
            return

        self._walk_children(node, source_bytes, path, relative, fhash, chunks, namespace_stack)

    def _walk_children(self, node, source_bytes, path, relative, fhash, chunks, namespace_stack):
        for child in node.children:
            self._walk(child, source_bytes, path, relative, fhash, chunks, namespace_stack)

    def _child_text(self, node, source_bytes, child_type) -> Optional[str]:
        for child in node.children:
            if child.type == child_type:
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        return None

    def _get_type_name(self, node, source_bytes) -> Optional[str]:
        for child in node.children:
            if child.type in ("type_identifier", "name"):
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        return None

    def _get_function_name(self, node, source_bytes) -> Optional[str]:
        """Extrait le nom complet (méthode/fonction) via l'API tree-sitter de manière robuste."""
        declarator = node.child_by_field_name("declarator")
        if not declarator:
            return None
        
        curr = declarator
        while curr.children:
            if curr.type == "qualified_identifier":
                name_node = curr.child_by_field_name("name")
                if name_node:
                    return source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
            
            for child in curr.children:
                if child.type in ("field_identifier", "identifier", "operator_name", "destructor_name"):
                    return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
            curr = curr.children[0]
            
        return source_bytes[curr.start_byte:curr.end_byte].decode("utf-8", errors="replace")

    def _collect_cpp_refs(self, node, source_bytes) -> List[str]:
        refs = set()
        self._collect_refs_rec(node, source_bytes, refs)
        return sorted(refs)

    def _collect_refs_rec(self, node, source_bytes, refs):
        if node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func:
                name = source_bytes[func.start_byte:func.end_byte].decode("utf-8", errors="replace")
                refs.add(name.split("::")[-1].split(".")[-1].split("->")[-1].strip())
        for child in node.children:
            self._collect_refs_rec(child, source_bytes, refs)

    def _make_chunk(self, node, source_bytes, path, relative, fhash, namespace_stack) -> Optional[CodeChunk]:
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        content = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

        if len(content) > MAX_CHUNK_CHARS and node.type in ("class_specifier", "struct_specifier"):
            content = self._cpp_class_skeleton(node, source_bytes)
            
        if node.type == "function_definition":
            local_name = self._get_function_name(node, source_bytes) or "unknown"
            chunk_type = "method" if namespace_stack else "free_function"
        else:
            local_name = self._get_type_name(node, source_bytes) or "unknown"
            chunk_type = "class" if node.type == "class_specifier" else "struct"

        qualified_name = "::".join(namespace_stack + [local_name]) if namespace_stack else local_name
        header = f"// File: {relative}\n\n"

        return CodeChunk(
            content=header + content,
            file_path=str(path), relative_path=relative,
            language="cpp", chunk_type=chunk_type,
            symbol_name=qualified_name,
            start_line=start_line, end_line=end_line,
            file_hash=fhash,
            symbols_referenced=self._collect_cpp_refs(node, source_bytes),
        )

    def _cpp_class_skeleton(self, node, source_bytes: bytes) -> str:
        lines = []
        for child in node.children:
            if child.type == "field_declaration_list":
                for member in child.children:
                    if member.type == "function_definition":
                        sig = self._get_function_signature(member, source_bytes)
                        lines.append(f"  {sig} {{...}}")
                    else:
                        text = source_bytes[member.start_byte:member.end_byte].decode("utf-8", errors="replace")
                        if text.strip():
                            lines.append(f"  {text.strip()}")
            else:
                text = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                if text.strip() and child.type != "field_declaration_list":
                    lines.append(text)
        return "\n".join(lines)

    def _get_function_signature(self, node, source_bytes: bytes) -> str:
        body_node = node.child_by_field_name("body")
        if body_node:
            sig_bytes = source_bytes[node.start_byte:body_node.start_byte]
            return sig_bytes.decode("utf-8", errors="replace").strip()
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()


_cpp_chunker = CppChunker()

def chunk_cpp(path: Path, root: Path) -> List[CodeChunk]:
    return _cpp_chunker.chunk(path, root)