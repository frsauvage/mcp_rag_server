#!/usr/bin/env python3
"""
mcp_rag_server.py — Serveur MCP avec RAG complet sur codebase Python/C++

Point d'entrée du projet. Expose 6 outils MCP :

  index_codebase    Indexe une codebase dans ChromaDB (avec cache par hash)
  query_codebase    Question en langage naturel -> RAG -> reponse LLM
  analyze_code      Analyse un snippet de code fourni directement
  analyze_file      Lit un fichier et l'analyse (avec contexte RAG)
  get_index_stats   Statistiques de la base vectorielle
  clear_index       Vide la base (pour une reindexation complete)

Architecture interne :
  mcp_rag_server.py
      |-- indexer.py     <- scan + chunking + appel au store
      |-- retriever.py   <- retrieval semantique + expansion dependances + prompt
      |-- store.py       <- ChromaDB + embedding + cache
      |-- chunker.py     <- chunking syntaxique Python (ast) et C++ (tree-sitter)
      |-- mcp_client_llm.py  <- configuration du LLM de generation
"""
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

from store import CodeStore
from indexer import Indexer
from retriever import Retriever
from mcp_rag_client_llm import llm_client

load_dotenv(encoding='utf-8')

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

PATH_LOGS = os.getenv("PATH_LOGS", "./logs")
LOG_DIR = Path(PATH_LOGS)
LOG_DIR.mkdir(parents=True, exist_ok=True)

stream_handler = logging.StreamHandler(
    io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "mcp_rag_server.log", encoding='utf-8'),
        stream_handler,
    ],
)
logger = logging.getLogger("mcp_rag_server")
logging.getLogger("httpx").setLevel(logging.WARNING)

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
            print("\n⚠️  Erreurs détectées dans les logs :")
            for error in errors[-10:]:  # Afficher les 10 dernières erreurs
                print(f"  {error}")
    except Exception as e:
        pass  # Silencieux si lecture échoue

# ---------------------------------------------------------------------------
# Initialisation des composants
# ---------------------------------------------------------------------------

CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")

store     = CodeStore(persist_dir=CHROMA_PERSIST_DIR)
indexer   = Indexer(store)
retriever = Retriever(store)

# ---------------------------------------------------------------------------
# Serveur MCP
# ---------------------------------------------------------------------------

server = Server("llm-code-reader")
print("Serveur MCP initialise")


async def _llm_call(prompt: str) -> str:
    if not llm_client:
        return "Erreur : llm_client non configure."
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
            name="index_codebase",
            description=(
                "Indexe une codebase entiere (Python + C++) dans la base vectorielle ChromaDB. "
                "Les fichiers inchanges sont ignores grace au cache SHA-256. "
                "A appeler manuellement avant toute requete sur la codebase, "
                "et apres chaque modification significative du code source."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Chemin racine de la codebase a indexer"
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Scanner recursivement les sous-repertoires (defaut : true)",
                        "default": True
                    },
                    "force_reindex": {
                        "type": "boolean",
                        "description": "Vider la base et tout reindexer (ignore le cache)",
                        "default": False
                    }
                },
                "required": ["directory"]
            }
        ),
        types.Tool(
            name="query_codebase",
            description=(
                "Pose une question en langage naturel sur la codebase indexee. "
                "Le RAG retrouve les chunks pertinents (recherche semantique + expansion "
                "des dependances inter-fichiers) et les soumet au LLM avec la question."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Question sur le code (ex: 'Comment fonctionne le thread shutdown ?')"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Nombre de chunks initiaux a recuperer (defaut : 10)",
                        "default": 10
                    },
                    "language_filter": {
                        "type": "string",
                        "description": "Filtrer par langage : 'python' ou 'cpp' (optionnel)"
                    },
                    "expand_dependencies": {
                        "type": "boolean",
                        "description": "Inclure les chunks des symboles references (defaut : true)",
                        "default": True
                    }
                },
                "required": ["question"]
            }
        ),
        types.Tool(
            name="analyze_code",
            description=(
                "Analyse un snippet de code fourni directement, sans passer par la base vectorielle. "
                "Utile pour analyser un extrait isole ou du code non indexe."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Le code source a analyser"
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Instruction d'analyse (ex: 'Explique ce code', 'Trouve les bugs')"
                    }
                },
                "required": ["code", "prompt"]
            }
        ),
        types.Tool(
            name="analyze_file",
            description=(
                "Lit un fichier source et l'analyse avec le LLM. "
                "Enrichit automatiquement le contexte avec les chunks RAG lies "
                "(dependances du fichier dans la codebase indexee)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Chemin absolu ou relatif du fichier a analyser"
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Instruction d'analyse"
                    }
                },
                "required": ["file_path", "prompt"]
            }
        ),
        types.Tool(
            name="get_index_stats",
            description="Retourne les statistiques de la base vectorielle (fichiers indexes, nombre de chunks, repertoire).",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="clear_index",
            description=(
                "Vide completement la base vectorielle (chunks + cache des hashes). "
                "La prochaine execution de index_codebase reindexera tout from scratch."
            ),
            inputSchema={"type": "object", "properties": {}}
        ),
    ]


# ---------------------------------------------------------------------------
# Gestionnaire des appels d'outils
# ---------------------------------------------------------------------------

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    args = arguments or {}

    # ------------------------------------------------------------------ index_codebase
    if name == "index_codebase":
        directory     = args["directory"]
        recursive     = args.get("recursive", True)
        force_reindex = args.get("force_reindex", False)

        try:
            report = await indexer.index_directory(
                directory=directory,
                recursive=recursive,
                force_reindex=force_reindex,
            )
            stats = store.stats()
            summary = report.summary()
            summary += (
                f"\n   Total en base       : {stats['total_chunks']} chunks "
                f"/ {stats['total_files_indexed']} fichiers"
            )
            return [types.TextContent(type="text", text=summary)]
        except ValueError as e:
            return [types.TextContent(type="text", text=f"Erreur : {e}")]

    # ------------------------------------------------------------------ query_codebase
    elif name == "query_codebase":
        import time
        question    = args["question"]
        top_k       = args.get("top_k", 10)
        lang_filter = args.get("language_filter")
        expand_deps = args.get("expand_dependencies", True)

        t0 = time.perf_counter()
        prompt, nb_chunks = await asyncio.to_thread(
            retriever.build_prompt, question, top_k, lang_filter, expand_deps
        )

        if nb_chunks == 0:
            return [types.TextContent(type="text", text="Aucun chunk pertinent trouve.")]

        answer = await _llm_call(prompt)
        t_total = time.perf_counter() - t0

        print(f"query_codebase : {t_total:.2f}s")
        return [types.TextContent(type="text", text=f"{answer}\n\nTemps total : {t_total:.2f}s")]

    # ------------------------------------------------------------------ analyze_code
    elif name == "analyze_code":
        code   = args["code"]
        prompt = args["prompt"]
        full_prompt = f"{prompt}\n\n```\n{code}\n```"
        result = await _llm_call(full_prompt)
        return [types.TextContent(type="text", text=result)]

    # ------------------------------------------------------------------ analyze_file
    elif name == "analyze_file":
        file_path   = args["file_path"]
        user_prompt = args["prompt"]
        try:
            path = Path(file_path)
            if not path.exists():
                raise ValueError(f"Fichier introuvable : {file_path}")
            content = path.read_text(encoding="utf-8", errors="replace")

            prompt = await asyncio.to_thread(
                retriever.build_file_prompt,
                content, str(path), user_prompt
            )
            result = await _llm_call(prompt)
            return [types.TextContent(type="text", text=f"Analyse de {file_path} :\n\n{result}")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Erreur : {e}")]

    # ------------------------------------------------------------------ get_index_stats
    elif name == "get_index_stats":
        stats = store.stats()
        report = (
            f"Statistiques de l'index\n"
            f"   Chunks indexes     : {stats['total_chunks']}\n"
            f"   Fichiers indexes   : {stats['total_files_indexed']}\n"
            f"   Repertoire Chroma  : {stats['persist_dir']}\n"
        )
        return [types.TextContent(type="text", text=report)]

    # ------------------------------------------------------------------ clear_index
    elif name == "clear_index":
        store.clear()
        return [types.TextContent(
            type="text",
            text="Base vectorielle videe. Relancez index_codebase pour reindexer."
        )]

    else:
        raise ValueError(f"Outil inconnu : {name}")


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------

async def main():
    if not llm_client:
        print("ATTENTION : llm_client non configure (voir mcp_client_llm.py et .env).")
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

    if len(sys.argv) < 2:
        # Mode serveur MCP normal
        asyncio.run(main())

    elif sys.argv[1] == "--debug-chunk":
        # python mcp_rag_server.py --debug-chunk chemin/fichier.py
        from chunker import chunk_file
        path = Path(sys.argv[2])
        chunks = chunk_file(path, path.parent)
        print(f"{len(chunks)} chunks trouves dans {path.name}")
        for c in chunks:
            print(f"  [{c.chunk_type:12}] {c.symbol_name:40} L{c.start_line}-{c.end_line}")
            if c.symbols_referenced:
                print(f"    -> refs: {', '.join(c.symbols_referenced)}")

    elif sys.argv[1] == "--clean":
        print("Vidage de la base vectorielle...")
        store.clear()
        print("Base videe.")

    elif sys.argv[1] == "--index":
        if len(sys.argv) < 3:
            logger.error("Usage : python mcp_rag_server.py --index <repertoire> [<repertoire2> ...]")
            sys.exit(1)
        directories = sys.argv[2:]

        async def run_index():
            print(f"Indexation de {', '.join(directories)}...")
            report = await indexer.index_directories(directories=directories, recursive=True)
            print(report.summary())
            stats = store.stats()
            print(f"Total en base : {stats['total_chunks']} chunks / {stats['total_files_indexed']} fichiers")
            print(f"Stockage : {stats['persist_dir']}")
            
            # Afficher les erreurs du log
            _print_log_errors(LOG_DIR / "mcp_rag_server.log")
        asyncio.run(run_index())

    elif sys.argv[1] == "--query":
        async def test_query():
            import time
            print("=== Test query_codebase (sans reindexation) ===")
            stats = store.stats()
            print(f"Base : {stats['total_chunks']} chunks / {stats['total_files_indexed']} fichiers")

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
                print(f"{nb_chunks} chunk(s) recupere(s) en {t_retrieval:.2f}s -- envoi au LLM...")

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

                print(f"Reponse :\n{answer}")
                print(f"Retrieval : {t_retrieval:.2f}s | LLM : {t_llm:.2f}s | Total : {t_retrieval + t_llm:.2f}s")
                print(f"Historique : {len(history)} tour(s)")

        asyncio.run(test_query())

    else:
        print("Usage :")
        print("  python mcp_rag_server.py                         -> serveur MCP")
        print("  python mcp_rag_server.py --index <repertoire>    -> indexation")
        print("  python mcp_rag_server.py --clean                 -> vider la base")
        print("  python mcp_rag_server.py --query                 -> test RAG interactif")
        print("  python mcp_rag_server.py --debug-chunk <fichier> -> debug chunker")