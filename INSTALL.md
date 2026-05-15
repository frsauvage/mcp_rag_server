# 🚀 Installation Guide — MCP RAG Server (Mistral)

## 📌 Overview

This guide explains how to install, configure, and run the MCP RAG Server (Python + ChromaDB + MCP tools).

# 🧰 1. Prerequisites

## 🐍 Python

- Python 3.13+
- Should be installed manually (zip-based installation supported)

⚠️ Do NOT modify your own `PYTHON_HOME` if ever **MTG** software is installed  
⚠️ Python would only be available via virtual environment activation (safe)

## 🧑‍💻 Git (optional)

Used for repository management and optional anonymized commits.

### Git anonymous config

git config --global user.email "<23147820+frsauvage@users.noreply.github.com>"
git config --global user.name "Francine Sauvage"

## 📦 Repository

git clone <repo-url>
cd mcp_rag_server

# ⚙️ Python Installation & Setup

## 📥 Manual installation

- Download Python 3.13+
- Unzip anywhere
- Ensure python.exe executable is accessible

## 🧪 Virtual environment

### Option A — venv

```bash
python -m venv venv
venv\Scripts\activate
```

### Option B — uv (optional)

Create %APPDATA%/uv/.env:

```cmd
ARTIFACT_USER=<TGI>
ARTIFACT_PASSWORD=<your_artifactory_password>
ARTIFACT_URL=<mirror_url>
UV_INSTALL_DIR=/d/uv
UV_PYTHON=/path/to/python.exe
UV_INDEX_USERNAME=<TGI>
UV_INDEX_PASSWORD=<your_artifactory_password>
```

Create %APPDATA%/uv/uv.toml:

```bash
system-certs = true
cache-dir = "/path/to/tgi/MyApp/.uv/cache"
python-install-mirror = "https://<TGI>:<password>@<ARTIFACTORY_URL>/astral-sh/python-build-standalone/releases/download"
[[index]]
url = "https://<TGI>:<password>@<ARTIFACTORY_URL>/api/pypi/.../simple"
default = true
```

## uv Installation & Setup

Run the following command:

```bash
uv-installer.sh
```

# 📦 Project Setup

## Activate your virtual environment

```bash
cd mcp_rag_server
venv\Scripts\activate
```

## Synchronise your dependencies

```bash
uv sync -v
```

# 🔌 Agent Configuration (Continue)

Edit .continue/config.yaml:

```yaml
mcpServers:

- name: mcp-rag-server
    command: ${MCP_RAG_PROJECT_ROOT}\.venv\Scripts\python.exe
    args:
  - ${MCP_RAG_PROJECT_ROOT}\mcp_rag_server.py
    env:
      API_KEY: ${secrets.MISTRAL_API_KEY}
      MCP_RAG_PROJECT_ROOT: ${secrets.MCP_RAG_PROJECT_ROOT}
```

⚠️ Ensure all paths are correct and venv exists.

# 🧠 MCP Tools (Agent Interface)

3 tools are exposed:

## 🧹 clean

- Clears vector database
- Manual only
- No automatic reset

## 📦 index

- Indexes a full codebase
- Requires directory path
- Does NOT clear existing data

## ❓ query

- Answers questions using RAG over indexed code

# 🔄 Workflows

## First usage

index(directory)
query(question)

## Re-index project

index(directory)

## Reset + new project

clean()
index(directory)

## Explore code

query(question)

# 🖼️ Visual Documentation

- **Archi** :
  ![Architecture](images/flux_archi.png)

- **Clean flow** :
  ![Clean flow](images/flux_nettoyage.png)

- **Indexation flow** :
  ![Indexation flow](images/flux_indexation.png)

- **Request flow** :
  ![Request flow](images/flux_requete.png)

#### Examples

  ![Exemple de requête](images/example_request_clear.png)

# 🤖 8. Ollama (optional)

ollama pull nomic-embed-text
ollama pull mistral
ollama pull gpt-oss

Update models:

for /f "tokens=1" %i in ('ollama list') do ollama pull %i

# 🧯 9. Troubleshooting

- Ensure venv is activated
- Verify API keys
- Check dependencies: pip list

If MCP fails:

- check Python path in config
- ensure mcp_rag_server.py exists
- ensure venv is active

# ✅ Summary

- Python 3.13+
- venv or uv supported
- MCP exposes 3 tools
- no automatic reset
- manual lifecycle: clean → index → query
