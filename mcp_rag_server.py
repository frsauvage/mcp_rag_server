#!/usr/bin/env python3
"""
mcp_rag_server.py — Serveur MCP avec RAG complet sur codebase Python/C++

Point d'entrée du projet. Expose 3 outils MCP :

  index   Indexe une codebase dans ChromaDB (avec cache par hash)
  query   Question en langage naturel -> RAG -> reponse LLM
  clean   Vide la base (pour une reindexation complete)

Architecture interne :
  mcp_rag_server.py
      |-- indexer.py     <- scan + chunking + appel au store
      |-- retriever.py   <- retrieval semantique + expansion dependances + prompt
      |-- store.py       <- ChromaDB + embedding + cache
      |-- chunker.py     <- chunking syntaxique Python (ast) et C++ (tree-sitter)
      |-- mcp_client_llm.py  <- configuration du LLM de generation
"""
import argparse
import asyncio
import logging
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from store import CodeStore
from indexer import Indexer
from retriever import Retriever
from mcp_rag_client_llm import llm_client

console = Console()

load_dotenv(encoding='utf-8')

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

PATH_LOGS = os.getenv("PATH_LOGS", "./logs")
LOG_DIR = Path(PATH_LOGS)
LOG_DIR.mkdir(parents=True, exist_ok=True)

_log_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Fichier : reçoit DEBUG et au-dessus
file_handler = logging.FileHandler(LOG_DIR / "mcp_rag_server.log", encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(_log_formatter)

# stdout : reçoit uniquement INFO et au-dessus
stream_handler = logging.StreamHandler(
    io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
)
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(_log_formatter)

logging.basicConfig(
    level=logging.DEBUG,  # le root logger doit laisser passer DEBUG jusqu'au FileHandler
    handlers=[file_handler, stream_handler],
)
logger = logging.getLogger("mcp_rag_server")
logging.getLogger("httpx").setLevel(logging.WARNING)

def _build_arg_parser() -> argparse.ArgumentParser:
    """Construit le parseur d'arguments CLI.

    --chroma_db est une option globale et independante des commandes
    (index/clean/query/debug-chunk), ce qui evite toute confusion avec
    leurs propres arguments (ex: le repertoire de --index).
    """
    parser = argparse.ArgumentParser(
        prog="mcp_rag_server.py",
        description="Serveur MCP RAG - indexation et interrogation d'une codebase.",
    )
    parser.add_argument(
        "--chroma_db",
        metavar="CHEMIN",
        default=os.getenv("CHROMA_PERSIST_DIR", "./chroma_db"),
        help="Dossier de la base ChromaDB (defaut : $CHROMA_PERSIST_DIR ou ./chroma_db)",
    )
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument(
        "--index",
        metavar="REPERTOIRE",
        help="Indexe un repertoire",
    )
    commands.add_argument(
        "--clean",
        action="store_true",
        help="Vide la base vectorielle",
    )
    commands.add_argument(
        "--query",
        action="store_true",
        help="Lance une session RAG interactive",
    )
    commands.add_argument(
        "--debug-chunk",
        metavar="FICHIER",
        help="Affiche le resultat du chunking pour un fichier (debug)",
    )
    return parser

args = _build_arg_parser().parse_args()

# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _print_log_errors(log_file: Path):
    """Affiche uniquement les lignes ERROR du fichier de log."""
    if not log_file.exists():
        return
    try:
        errors = []
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                if "ERROR" in line or "error" in line.lower():
                    errors.append(line.strip())
        if errors:
            console.print("\n⚠️  Erreurs détectées dans les logs :")
            for error in errors[-10:]:
                console.print(f"  {error}", style="red")
    except Exception as e:
        pass

def _paginate_output(text: str, lines_per_page: int = 40) -> None:
    """Affiche le texte par pages si plus de lines_per_page lignes."""
    lines = text.split('\n')

    if len(lines) <= lines_per_page:
        console.print(Markdown(text))
        return

    page = 0
    while page * lines_per_page < len(lines):
        start = page * lines_per_page
        end = min(start + lines_per_page, len(lines))
        page_text = '\n'.join(lines[start:end])

        console.print(Markdown(page_text))

        if end < len(lines):
            progress = f"[dim]Page {page + 1}/{(len(lines) + lines_per_page - 1) // lines_per_page} — Appuyez sur Entrée pour continuer...[/dim]"
            console.print(progress)
            try:
                input()
            except EOFError:
                break

        page += 1

# ---------------------------------------------------------------------------
# Initialisation des composants
# ---------------------------------------------------------------------------

store     = CodeStore(persist_dir=args.chroma_db)
indexer   = Indexer(store)
retriever = Retriever(store)

# ---------------------------------------------------------------------------
# Serveur MCP
# ---------------------------------------------------------------------------

server = Server("llm-code-reader")
logger.info("MCP Server initialized")


async def _llm_call(prompt: str) -> str:
    if not llm_client:
        return "Erreur : llm_client not configured."
    try:
        message = HumanMessage(content=prompt)
        response = await asyncio.to_thread(llm_client.invoke, [message])
        return response.content
    except Exception as e:
        logger.error(f"Erreur LLM : {e}")
        return f"Erreur LLM : {e}"

# ---------------------------------------------------------------------------
# Definition des outils
# ---------------------------------------------------------------------------

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="clean",
            description="Nettoie complètement la base vectorielle (reset).",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="index",
            description=(
                "Indexe un codebase. "
                "À utiliser après un clean ou quand on change de projet."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "directory": {"type": "string"},
                    "force_reindex": {"type": "boolean", "default": False}
                },
                "required": ["directory"]
            }
        ),
        types.Tool(
            name="query",
            description=(
                "Pose une question sur le code indexé."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {"type": "string"}
                },
                "required": ["question"]
            }
        ),
    ]

# ---------------------------------------------------------------------------
# Gestionnaire des appels d'outils
# ---------------------------------------------------------------------------
@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None):

    args = arguments or {}

    # ---------------- CLEAN
    if name == "clean":
        store.clear()

        return [types.TextContent(
            type="text",
            text="✅ Base vectorielle nettoyée."
        )]

    # ---------------- INDEX
    elif name == "index":
        directory = args["directory"]
        force = args.get("force_reindex", False)

        try:
            report = await indexer.index_directory(
                directory=directory,
                force_reindex=force,
            )

            stats = store.stats()

            return [types.TextContent(
                type="text",
                text=(
                    f"✅ Indexation terminée pour {directory}\n\n"
                    + report.summary()
                    + f"\nTotal: {stats['total_chunks']} chunks / "
                      f"{stats['total_files_indexed']} fichiers"
                )
            )]

        except Exception as e:
            return [types.TextContent(
                type="text",
                text=f"❌ Erreur index: {e}"
            )]

    # ---------------- QUERY
    elif name == "query":
        question = args["question"]

        stats = store.stats()
        if stats["total_chunks"] == 0:
            return [types.TextContent(
                type="text",
                text="⚠️ La base est vide. Lance d'abord un index."
            )]

        try:
            prompt, nb_chunks = await asyncio.to_thread(
                retriever.build_prompt,
                question,
                10,
                None,
                True
            )

            if nb_chunks == 0:
                return [types.TextContent(
                    type="text",
                    text="Aucun résultat pertinent trouvé."
                )]

            answer = await _llm_call(prompt)

            return [types.TextContent(
                type="text",
                text=answer
            )]

        except Exception as e:
            return [types.TextContent(
                type="text",
                text=f"❌ Erreur query: {e}"
            )]

    else:
        raise ValueError(f"Outil inconnu : {name}")

# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------

async def main():
    if not llm_client:
        logger.info("ATTENTION : llm_client non configure (voir mcp_client_llm.py et .env).")
        return

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="mcp-rag-server",
                server_version="2.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":

    if args.debug_chunk:
        # python mcp_rag_server.py --debug-chunk chemin/fichier.py
        from chunker import chunk_file
        from chunker_code import CodeChunk
        from chunker_pdf import DocChunk
        path = Path(args.debug_chunk)
        chunks = chunk_file(path, path.parent)
        logger.info(f"{len(chunks)} chunks trouves dans {path.name}")
        for c in chunks:
            if isinstance(c, CodeChunk):
                logger.info(f"  [{c.chunk_type:12}] {c.symbol_name:40} L{c.start_line}-{c.end_line}")
                if hasattr(c, 'symbols_referenced') and c.symbols_referenced:
                    logger.info(f"    -> refs: {', '.join(c.symbols_referenced)}")
            elif isinstance(c, DocChunk):
                logger.info(f"  [{c.chunk_type:12}] {c.symbol_name:40} P{c.page_start}-{c.page_end}")
            else:
                logger.info(f"  [{c.chunk_type:12}] {c.symbol_name:40}")

    if args.clean:
        logger.info("Cleaning vectorial store...")
        store.clear()
        logger.info("Base emptied.")

    if args.index:
        directory = args.index

        async def run_index():
            logger.info(f"Indexation de {directory}...")
            report = await indexer.index_directory(directory=directory)
            logger.info(report.summary())
            stats = store.stats()
            logger.info(f"Total en base : {stats['total_chunks']} chunks / {stats['total_files_indexed']} fichiers")
            logger.info(f"Storage : {stats['persist_dir']}")

            # Afficher les erreurs du log
            _print_log_errors(LOG_DIR / "mcp_rag_server.log")
        asyncio.run(run_index())

    if args.query:
        async def test_query():
            import time

            stats = store.stats()
            logger.info(f"Base : {stats['total_chunks']} chunks / {stats['total_files_indexed']} fichiers")

            if stats['total_chunks'] == 0:
                logger.warning("La base est vide -- lancez d'abord : python mcp_rag_server.py --index <repertoire>")
                return

            history: list[dict] = []
            MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "5"))

            default_top_k = int(os.getenv("RETRIEVAL_TOP_K", "10"))

            while True:
                question = input("\n  Question (vide pour quitter) : ").strip()
                if not question:
                    break

                t0 = time.perf_counter()
                top_k = default_top_k

                prompt, nb_chunks = await asyncio.to_thread(
                    retriever.build_prompt, question, top_k, None, False
                )
                t_retrieval = time.perf_counter() - t0
                logger.info(f"{nb_chunks} chunk(s) recupere(s) en {t_retrieval:.2f}s -- envoi au LLM...")

                history_text = ""
                if history:
                    history_text = "Historique de la conversation :\n"
                    for turn in history[-MAX_HISTORY_TURNS:]:
                        history_text += f"Q: {turn['question']}\nR: {turn['answer']}\n\n"
                    history_text += "---\n\n"

                t1 = time.perf_counter()
                answer = await _llm_call(history_text + prompt)
                t_llm = time.perf_counter() - t1

                history.append({"question": question, "answer": answer})


                _paginate_output(answer, lines_per_page=30)
                console.print(f"[cyan]Retrieval[/cyan]: {t_retrieval:.2f}s | [cyan]LLM[/cyan]: {t_llm:.2f}s | [bold]Total[/bold]: {t_retrieval + t_llm:.2f}s")
                console.print(f"[dim]Historique: {len(history)} tour(s)[/dim]")

        asyncio.run(test_query())

    if not any([args.debug_chunk, args.clean, args.index, args.query]):
        # Aucune commande -> mode serveur MCP normal
        logger.info("Starting MCP RAG server...")
        asyncio.run(main())
