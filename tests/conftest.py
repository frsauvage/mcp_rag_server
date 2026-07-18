"""
conftest.py — Fixtures partagées et mocks des dépendances externes.

Doit être chargé avant tout import de module projet qui dépend de :
  - mcp_rag_client_llm  (connexion Ollama au démarrage)
  - chromadb            (base vectorielle)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# ── 1. Ajouter la racine du projet au sys.path ───────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── 2. Mocker mcp_rag_client_llm AVANT tout import de module projet ──────────
_mock_llm = MagicMock()
_mock_llm.EMBED_MODEL = "test-embed-model"
_mock_llm.LLM_MODEL = "test-llm-model"
_mock_llm.embed_client = MagicMock()
_mock_llm.llm_client = MagicMock()
sys.modules.setdefault("mcp_rag_client_llm", _mock_llm)

# ── 3. Mocker chromadb (évite l'initialisation SQLite) ───────────────────────
_mock_chroma = MagicMock()
sys.modules.setdefault("chromadb", _mock_chroma)
sys.modules.setdefault("chromadb.config", MagicMock())
sys.modules.setdefault("chromadb.errors", MagicMock())
