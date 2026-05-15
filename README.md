# 🤖 MCP RAG Server — Analyse de codebase Python/C++ par LLM

Serveur MCP qui permet à un LLM de comprendre et analyser une large codebase (Python et C++) via un pipeline RAG : chunking syntaxique, embedding vectoriel, cache persistant, et retrieval avec expansion des dépendances.

---

## 🚀 Installation

See [INSTALL](INSTALL.md)

## 🚀 Configuration

# Configurer l'environnement

```bat
cp .env.example .env
```

# Editer .env et renseigner les variables obligatoires

### ⚙️ Variables d'environnement (.env)

| Variable  | Obligatoire | Description |
|---|---|---|
| `API_KEY` | ✅ | Clé API commune LLM + embedding |
| `LLM_BASE_URL` | ✅ | URL de l'endpoint LLM |
| `LLM_MODEL` | ✅ | Modèle LLM de génération |
| `EMBED_BASE_URL` | ✅ | URL de l'endpoint embedding |
| `EMBED_MODEL` | ✅ | Modèle d'embedding (ex: bge-m3) |
| `PATH_CA` | ✅ | Chemin vers le certificat SSL |
| `PATH_LOGS` | Non | Répertoire des logs (défaut: `./logs`) |
| `CHROMA_PERSIST_DIR` | Non | Répertoire ChromaDB (défaut: `./chroma_db`) |
| `EMBED_BATCH_SIZE` | Non | Taille de batch embedding (défaut: `128`) |
| `RETRIEVAL_TOP_K` | Non | Chunks par recherche (défaut: `5`) |
| `MAX_CONTEXT_CHARS` | Non | Taille max du contexte LLM (défaut: `8000`) |
| `MAX_HISTORY_TURNS` | Non | Tours de conversation mémorisés (défaut: `5`) |

---

## 🖥️ Utilisation en ligne de commande

```bash
# Indexer une ou plusieurs codebases (à faire avant toute query)
python mcp_rag_server.py --index D:\mon\projet docs

# Interroger la codebase (mode interactif avec mémoire)
python mcp_rag_server.py --query

# Vider la base (pour réindexer from scratch)
python mcp_rag_server.py --clean

# Debugger le chunking d'un fichier
python mcp_rag_server.py --debug-chunk mon_fichier.cpp

# Lancer le serveur MCP (pour Continue / Claude)
python mcp_rag_server.py
```

> ⚠️ **Important** : l'indexation peut prendre plusieurs minutes sur une large codebase.
> Effectuez-la en ligne de commande, pas depuis Continue/Claude, pour éviter les timeouts.

---

## 🔧 Outils MCP exposés

| Outil | Description |
|---|---|
| `index_codebase` | Indexe une codebase dans ChromaDB (avec cache SHA-256) |
| `query_codebase` | Question en langage naturel sur le code indexé |
| `analyze_file` | Analyse un fichier avec contexte RAG |
| `analyze_code` | Analyse un snippet de code fourni directement |
| `get_index_stats` | Statistiques de la base vectorielle |
| `clear_index` | Vide complètement la base |

---

## 📄 Documentation PDF

Placez vos PDFs de documentation (specs, wiki, architecture) dans le répertoire `docs/` uniquement si vous souhaitez centraliser les fichiers. Ce n'est PAS obligatoire : l'indexation fonctionne sur tout répertoire que vous passez en argument à `--index`.

```
mcp_rag_server/
    |-- docs/
    |   |-- architecture.pdf
    |   |-- specs.pdf
    |   `-- ...
    `-- ...
```

Les PDFs seront indexés si le répertoire contenant `docs/` est passé à `--index`, ou si vous passez explicitement `docs` comme argument. Le chunking se fait par section selon la table des matières (TOC), avec le numéro de page en métadonnée pour citer les sources dans les réponses LLM.

> 💡 Les PDFs doivent être natifs (générés depuis Word, Confluence, wiki...), pas des scans.

---

## 🏗️ Architecture

```
mcp_rag_server.py        <- Point d'entrée MCP + CLI
    |-- indexer.py       <- Scan fichiers + chunking + indexation
    |-- retriever.py     <- Recherche sémantique + construction prompt
    |-- store.py         <- ChromaDB + embedding + cache SHA-256
    |-- chunker.py       <- Chunking syntaxique Python (ast) et C++ (tree-sitter)
    |-- pdf_chunker.py   <- Chunking PDF par section (TOC)
    |-- embedder.py      <- Appels embedding avec retry
    |-- mcp_rag_client_llm.py  <- Configuration LLM + client embedding
    |-- docs/            <- PDFs de documentation à indexer
    |-- chroma_db/       <- Base vectorielle persistante (créée automatiquement)
    `-- logs/            <- Fichiers de log (créés automatiquement)
```

### Flux d'indexation

```
Répertoire
    -> [indexer.py]   scan des fichiers .py / .cpp / .h / .pdf
    -> [chunker.py]   découpage syntaxique par fonction/classe/section
    -> [store.py]     cache SHA-256 (fichier inchangé = ignoré)
                      embedding par batch -> ChromaDB
```

### Flux de query

```
Question
    -> [retriever.py] embedding question -> top-K chunks similaires
                      expansion des dépendances (symbols_referenced)
                      construction du prompt avec contexte
    -> [LLM]          réponse
```

---

## 🔍 Chunking

**🐍 Python** : via `ast` (stdlib). Extrait fonctions, méthodes, classes avec leurs signatures. Les classes trop grandes sont remplacées par leur squelette (signatures seules).

**⚙️ C++** : via `tree-sitter`. Extrait fonctions libres, méthodes, classes, structs avec noms qualifiés complets (`Namespace::Class::method`). Les fonctions trop grandes sont découpées en sous-chunks sur les frontières de blocs.

**📄 PDF** : via `pymupdf`. Découpe par section selon la table des matières (TOC). Fallback par page si pas de TOC.

Le cache SHA-256 garantit que seuls les fichiers modifiés sont re-embeddés lors d'une réindexation.

---

## 📁 Répertoires exclus

```python
EXCLUDED_ROOT_DIRS = {"Delivery", "Build", "test", "tests", "OSS", "SDD"}
EXCLUDED_DIRS      = {"__pycache__", ".git", ".venv", "venv", "node_modules"}
```

Modifiez `indexer.py` pour adapter à votre projet.
