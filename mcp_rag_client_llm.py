import os
import logging
from pathlib import Path
import httpx
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from openai import OpenAI

llm_client = None

# Charger les variables d'environnement depuis le fichier .env
load_dotenv()

# Configuration du logging
PATH_LOGS = os.getenv("PATH_LOGS", "")
LOG_DIR = Path(PATH_LOGS)
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "mcp_client_llm.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("mcp_client_llm")

# Initialisation du client HTTP avec le certificat de sécurité
# Configuration
PATH_CA = os.getenv("PATH_CA", "")
print(f"PATH_CA={PATH_CA}")
if not PATH_CA:
    logger.error("error PATH_CA!!")
    exit(1)

# Dire à httpx/certifi d'utiliser ton CA — pas de http_client du tout
os.environ["SSL_CERT_FILE"] = PATH_CA
os.environ["REQUESTS_CA_BUNDLE"] = PATH_CA

client = httpx.Client(verify=PATH_CA)

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
    http_async_client=httpx.AsyncClient(verify=PATH_CA),
)

print("MistralSDK client created")
"""
mcp_rag_client_embed.py — Client embedding compatible OpenAI
"""

embed_client = OpenAI(
    api_key=os.environ["API_KEY"],
    base_url=os.environ["EMBED_BASE_URL"],
    http_client=client
)

EMBED_MODEL = os.getenv("EMBED_MODEL", None)

print("OpenAI embeded client created")