# -*- coding: utf-8 -*-
import os
import sys
import io
import logging
from pathlib import Path
import httpx
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from openai import OpenAI

llm_client = None

# Charger les variables d'environnement depuis le fichier .env
load_dotenv(encoding='utf-8')

# Configuration du logging
PATH_LOGS = os.getenv("PATH_LOGS", "")
LOG_DIR = Path(PATH_LOGS)
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "mcp_client_llm.log"


stdout_handler = logging.StreamHandler(
    io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        stdout_handler,
    ]
)

logger = logging.getLogger("mcp_client_llm")

# Initialisation du client HTTP avec le certificat de sécurité (optionnel)
# Configuration
PATH_CA = os.getenv("PATH_CA", "")
print(f"PATH_CA={PATH_CA}")

# Déterminer la configuration SSL
use_custom_ca = False
if PATH_CA:
    ca_path = Path(PATH_CA)
    if ca_path.exists():
        os.environ["SSL_CERT_FILE"] = PATH_CA
        os.environ["REQUESTS_CA_BUNDLE"] = PATH_CA
        use_custom_ca = True
        print(f"Using custom CA certificate: {PATH_CA}")
    else:
        logger.warning(f"CA certificate not found at {PATH_CA}, using system certificates")
        print(f"Warning: CA certificate not found at {PATH_CA}")

# Définir la configuration de vérification SSL
# True = utiliser les certificats système
# Un chemin = utiliser ce certificat personnalisé
http_verify = PATH_CA if use_custom_ca else True
print(f"SSL verification mode: {'custom CA' if use_custom_ca else 'system certificates'}")

client = httpx.Client(verify=http_verify)

# Configuration
API_KEY = os.getenv("API_KEY", "")
if not API_KEY:
    logger.error("error API_KEY!!")
    exit(1)
print(f"API_KEY={API_KEY}")
LLM_MODEL = os.getenv("LLM_MODEL", "mistral")
print(f"LLM_MODEL={LLM_MODEL}")
if not LLM_MODEL:
    logger.error("error LLM_MODEL!!")
    exit(1)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "mistral")
print(f"LLM_BASE_URL={LLM_BASE_URL}")
if not LLM_BASE_URL:
    logger.error("error LLM_BASE_URL!!")
    exit(1)

# Client Mistral
# llm_client = ChatMistralAI(
#     api_key=API_KEY,
#     model=LLM_MODEL,
#     endpoint=LLM_BASE_URL,
#     http_async_client=httpx.AsyncClient(verify=PATH_CA),
# )
llm_client = ChatOpenAI(
    model_name=LLM_MODEL,
    streaming=False,
    api_key=API_KEY,
    base_url=LLM_BASE_URL,
    temperature=0,
    http_async_client=httpx.AsyncClient(verify=http_verify),
)

print("MistralSDK client created")
"""
mcp_rag_client_embed.py — Client embedding compatible OpenAI
"""

EMBED_BASE_URL = os.getenv("EMBED_BASE_URL", "")
EMBED_MODEL = os.getenv("EMBED_MODEL", None)

print(f"EMBED_BASE_URL={EMBED_BASE_URL}")
print(f"EMBED_MODEL={EMBED_MODEL}")

embed_client = OpenAI(
    api_key=os.environ["API_KEY"],
    base_url=EMBED_BASE_URL,
    http_client=client
)

# Test de connexion à l'API embedding au démarrage
try:
    print("Test de connexion à l'API embedding...")
    test_response = embed_client.embeddings.create(
        model=EMBED_MODEL,
        input=["test"]
    )
    print(f"✓ Embedding OK ({EMBED_MODEL})")
except Exception as e:
    logger.error(f"ERREUR: Connexion au service d'embedding échouée!")
    logger.error(f"  URL: {EMBED_BASE_URL}")
    logger.error(f"  Modèle: {EMBED_MODEL}")
    logger.error(f"  Détail: {e}")
    print(f"✗ Erreur embedding: {e}")

print("OpenAI embedded client created")