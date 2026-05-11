"""
code_chunker.py — Chunking syntaxique pour Python (ast) et C++ (tree-sitter)

Chaque chunk porte des métadonnées riches :
  - symbol_name   : nom qualifié complet (ex: MyNamespace::MyClass::my_method)
  - chunk_type    : "function" | "method" | "class" | "struct" | "free_function"
  - start_line / end_line : localisation dans le fichier source
  - file_hash     : SHA-256 du fichier entier (utilisé par le cache dans store.py)
  - symbols_referenced : liste des symboles appelés (pour l'expansion des dépendances)
"""
from __future__ import annotations

import ast
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("code_chunker")

CODE_EXTENSIONS = {".py", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx"}

MIN_CHUNK_LINES = 3
MAX_CHUNK_CHARS = 1700   # Limite corps de chunk (header ajouté après)

# ---------------------------------------------------------------------------
# Structure de données commune
# ---------------------------------------------------------------------------

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

    @property
    def chunk_id(self) -> str:
        """Identifiant stable et unique basé sur (file_path, start_line)."""
        raw = f"{self.file_path}:{self.start_line}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_metadata(self) -> dict:
        """
        Sérialise les métadonnées pour ChromaDB.
        ChromaDB n'accepte que des scalaires → les listes sont sérialisées en string séparé par |.
        """
        return {
            "file_path": self.file_path,
            "relative_path": self.relative_path,
            "language": self.language,
            "chunk_type": self.chunk_type,
            "symbol_name": self.symbol_name,
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


# Taille minimale et maximale raisonnable pour un fichier de code
MIN_CODE_LINES = 3
MAX_CODE_LINES = 10_000  # au-delà → probablement du code généré/minifié

def _is_worth_chunking(source: str, language: str = "python") -> bool:
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
            # Lignes // hors bloc → aussi du header
            if not in_block and stripped and not stripped.startswith("//"):
                break
    else:
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                break

    return "".join(lines[i:])

# ---------------------------------------------------------------------------
# Chunker Python — via le module ast de la stdlib
# ---------------------------------------------------------------------------

class PythonChunker:
    """
    Extrait des chunks sémantiques depuis du code Python.

    Stratégie :
      - Classe entière     → 1 chunk (vue d'ensemble de la classe)
      - Méthode de classe  → 1 chunk par méthode (préfixée ClassName::method_name)
      - Fonction libre     → 1 chunk par fonction
      - Les imports sont ajoutés en tête de chaque chunk (contexte minimal)
    """

    def chunk(self, path: Path, root: Path) -> List[CodeChunk]:
        # utf-8-sig absorbe silencieusement le BOM (U+FEFF) des fichiers créés sous Windows
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
                # Vue d'ensemble de la classe
                chunks.append(self._make_class_chunk(node, source, import_block, path, relative, fhash))
                # Une méthode = un chunk
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        chunks.append(
                            self._make_function_chunk(item, source, import_block, path, relative, fhash, prefix=node.name)
                        )

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if self._is_top_level(node, tree):
                    chunks.append(
                        self._make_function_chunk(node, source, import_block, path, relative, fhash, prefix=None)
                    )

        return chunks

    # -- Helpers --

    def _is_top_level(self, node: ast.AST, tree: ast.Module) -> bool:
        """Vérifie qu'un nœud fonction est directement sous le module (pas dans une classe)."""
        for parent in ast.walk(tree):
            if isinstance(parent, ast.ClassDef) and node in ast.walk(parent):
                return False
        return True

    def _collect_imports(self, source: str) -> str:
        """Collecte toutes les lignes d'import du fichier."""
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
        """Extrait les noms des fonctions/classes appelées dans un nœud."""
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

    def _make_function_chunk(self, node, source, import_block, path, relative, fhash, prefix):
        start, end = node.lineno, node.end_lineno or node.lineno
        if end - start < MIN_CHUNK_LINES:
            return None  # chunk trop petit, pas utile au RAG
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

    def _make_class_chunk(self, node, source, import_block, path, relative, fhash):
        start, end = node.lineno, node.end_lineno or node.lineno
        body = _extract_lines(source, start, end)
        header = f"# File: {relative}\n{import_block}\n\n" if import_block else f"# File: {relative}\n\n"

        if len(body) > MAX_CHUNK_CHARS:
            # Reconstruire la classe avec seulement les signatures des méthodes
            # Le corps est dans les chunks méthodes individuels
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
        """
        Reconstruit la classe avec seulement :
        - la déclaration de classe
        - les docstrings
        - les signatures des méthodes (sans leur corps)
        """
        lines = []
        # Ligne de déclaration de la classe
        lines.append(_extract_lines(source, node.lineno, node.lineno))

        for item in node.body:
            if isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant):
                # Docstring de classe
                lines.append(_extract_lines(source, item.lineno, item.end_lineno or item.lineno))
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Signature uniquement (première ligne + docstring si présente)
                lines.append(_extract_lines(source, item.lineno, item.lineno) + " ...")
            elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                # Attributs de classe
                lines.append(_extract_lines(source, item.lineno, item.end_lineno or item.lineno))

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Chunker C++ — via tree-sitter
# ---------------------------------------------------------------------------

class CppChunker:
    """
    Extrait des chunks sémantiques depuis du code C++ via tree-sitter.

    tree-sitter parse le code en AST concret sans compilation.
    On capture : fonctions libres, méthodes, classes, structs.
    Les namespaces sont utilisés pour qualifier les noms (Ns::Class::method).

    Installation requise :
        pip install tree-sitter tree-sitter-cpp
    """

    CAPTURE_TYPES = {"function_definition", "class_specifier", "struct_specifier"}

    def __init__(self):
        self._parser = None  # Lazy init

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
        # Supprimer le BOM UTF-8 (EF BB BF) si présent — courant sur fichiers Windows
        if source_bytes.startswith(b"\xef\xbb\xbf"):
            source_bytes = source_bytes[3:]
        source = source_bytes.decode("utf-8", errors="replace")
        source = _strip_file_header(source, "cpp")
        if not _is_worth_chunking(source):
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
            # Descendre dans les classes/structs pour capturer les méthodes
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
        for child in node.children:
            if child.type in ("function_declarator", "pointer_declarator", "reference_declarator"):
                return self._get_function_name(child, source_bytes)
            if child.type in ("identifier", "qualified_identifier", "operator_name", "destructor_name"):
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        return None

    def _collect_cpp_refs(self, node, source_bytes) -> List[str]:
        refs = set()
        self._collect_refs_rec(node, source_bytes, refs)
        return sorted(refs)

    def _collect_refs_rec(self, node, source_bytes, refs):
        if node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func:
                name = source_bytes[func.start_byte:func.end_byte].decode("utf-8", errors="replace")
                refs.add(name.split("::")[-1])
        for child in node.children:
            self._collect_refs_rec(child, source_bytes, refs)

    def _make_chunk(self, node, source_bytes, path, relative, fhash, namespace_stack) -> Optional[CodeChunk]:
        start_line = node.start_point[0] + 1  # tree-sitter est 0-indexé
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
        """
        Reconstruit la classe C++ avec seulement les déclarations,
        en remplaçant les corps de méthodes par {} 
        """
        lines = []
        for child in node.children:
            if child.type == "field_declaration_list":
                for member in child.children:
                    if member.type == "function_definition":
                        # Garder la signature, remplacer le corps par {}
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
        """Extrait la signature d'une fonction sans son corps."""
        for child in node.children:
            if child.type == "compound_statement":
                # Tout ce qui est avant le corps
                sig_bytes = source_bytes[node.start_byte:child.start_byte]
                return sig_bytes.decode("utf-8", errors="replace").strip()
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Instances des chunkers code
# ---------------------------------------------------------------------------

_python_chunker = PythonChunker()
_cpp_chunker    = CppChunker()

def chunk_code(path: Path, root: Path, ext: str) -> List[CodeChunk]:
    if ext == ".py":
        return _python_chunker.chunk(path, root)
    else:
        return _cpp_chunker.chunk(path, root)
