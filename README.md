# 🤖 MCP RAG Server — Analyse de codebase Python/C++ par LLM

Serveur MCP qui permet à un LLM de comprendre et analyser une large codebase (Python et C++) via un pipeline RAG : chunking syntaxique, embedding vectoriel, cache persistant, et retrieval avec expansion des dépendances.

---

## 🚀 Installation

See [INSTALL](INSTALL.md)

## 📄 AGENT.md

### Dans ce projet (la source)

Rôle : le fichier [`AGENT.md`](AGENT.md) à la racine de ce projet - source du serveur MCP - est là pour permettre l'installation MCP dans ces sources uniquement : il définit le comportement de l'agent (system prompt) qui pilote les outils MCP exposés par ce serveur.

### Dans le répertoire indexé (la cible)

Rôle : lors d'un `--index` / `index()`, si un fichier `AGENT.md` existe à la racine du répertoire **cible** (le répertoire indexé, pas ce projet), il est lu et soumis au LLM pour en extraire d'éventuelles exclusions de répertoires propres au RAG de recherche, en plus des exclusions par défaut d'`indexer.py`.

- **Pas d'`AGENT.md`** à cette racine → aucune exclusion supplémentaire n'est appliquée.
- **Avec `AGENT.md`** → décrivez une section dédiée aux exclusions RAG, par exemple :

```markdown
  ## Exclusions RAG de recherche

  Exclure de l'indexation :
  - legacy
  - vendor/third_party
```

- Le LLM ne cherche **que** cette section : le reste du fichier (règles de comportement d'agent, autres instructions...) est ignoré pour cet usage.
- Les chemins renvoyés sont relatifs à la racine indexée.
- Seul le nom exact `AGENT.md` est reconnu (pas `MISTRAL.md`).

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
| `PATH_CA` | Non | Chemin vers un certificat SSL custom (défaut: certificats système) |
| `PATH_LOGS` | Non | Répertoire des logs (défaut: `./logs`) |
| `CHROMA_PERSIST_DIR` | Non | Répertoire ChromaDB (défaut: `./chroma_db`, surchargeable via `--chroma_db`) |
| `EMBED_BATCH_SIZE` | Non | Taille de batch embedding (défaut: `128`) |
| `RETRIEVAL_TOP_K` | Non | Chunks par recherche (défaut: `10`) |
| `MAX_RERANK` | Non | Résultats récupérés avant reranking (défaut: `500`) |
| `MAX_CONTEXT_CHARS` | Non | Taille max du contexte LLM (défaut: `14000`) |
| `MAX_EMBED_CHARS` | Non | Taille max d'un chunk pour l'embedding (défaut: `2000`) |
| `MAX_HISTORY_TURNS` | Non | Tours de conversation mémorisés (défaut: `5`) |
| `WEB_CRAWL_DEPTH` | Non | Profondeur max du crawl `--url` (défaut: `2`) |
| `WEB_CRAWL_MAX_PAGES` | Non | Nombre max de pages crawlées par `--url` (défaut: `200`) |
| `WEB_CRAWL_EXCLUDE_DIRS` | Non | Sous-chemins exclus du crawl (URLs complètes séparées par des virgules) |
| `WEB_CRAWL_JSESSIONID` | Non* | Cookie de session pour un wiki protégé par authentification |
| `WEB_CRAWL_OTHER_COOKIE_NAME` | Non* | Nom du 2ème cookie de session (si le wiki en requiert plusieurs) |
| `WEB_CRAWL_OTHER_COOKIE_VALUE` | Non* | Valeur du 2ème cookie de session |

\* Obligatoires uniquement si le wiki cible est protégé par authentification (SSO/OAuth/WAM).

---

## 🖥️ Utilisation en ligne de commande

```bash
# Indexer une codebase (à faire avant toute query) — un seul répertoire par appel
python mcp_rag_server.py --index D:\mon\projet

# Indexer une page wiki et ses liens (crawl récursif, profondeur configurable via .env)
python mcp_rag_server.py --url https://wiki.corp.com/page-de-depart

# Interroger la codebase (mode interactif avec mémoire)
python mcp_rag_server.py --query

# Vider la base (pour réindexer from scratch)
python mcp_rag_server.py --clean

# Debugger le chunking d'un fichier
python mcp_rag_server.py --debug-chunk mon_fichier.cpp

# Lancer le serveur MCP (pour Continue / Claude)
python mcp_rag_server.py

# Utiliser une base ChromaDB différente (optionnel, valable pour toutes les commandes)
python mcp_rag_server.py --chroma_db D:\autre\chroma_db --index D:\mon\projet
```

> ⚠️ **Important** : l'indexation peut prendre plusieurs minutes sur une large codebase.
> Effectuez-la en ligne de commande, pas depuis Continue/Claude, pour éviter les timeouts.

---

## 🔧 Outils MCP exposés

| Outil | Description |
|---|---|
| `index` | Indexe une codebase dans ChromaDB (avec cache SHA-256), n'écrase pas les données existantes |
| `query` | Question en langage naturel sur le code indexé |
| `clean` | Vide complètement la base vectorielle (destructif) |

---

## 📄 Documentation PDF

Placez vos PDFs de documentation (specs, wiki, architecture) dans le répertoire `docs/` uniquement si vous souhaitez centraliser les fichiers. Ce n'est PAS obligatoire : l'indexation fonctionne sur tout répertoire que vous passez en argument à `--index`.
