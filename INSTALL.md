# Installation - Mistral

## Prérequis

- Python 3.13
- Un repo avec ces commandes pour rendre anonyme github:
git config --global user.email "<23147820+frsauvage@users.noreply.github.com>"
git config --global user.name "Francine Sauvage"

## Installation

### 1. Créer l'environnement virtuel

```cmd
<\path\to>\python.exe -m venv venv
venv\Scripts\activate
```

### 2. Installer les dépendances

```cmd
pip install -e .
```

### 3. Configuration Continue

Éditez votre fichier `config.json` de Continue (`.continue/config.json`):

```json
{
  "models": [
    {
      "title": "Mistral Codestral",
      "provider": "mistral",
      "model": "codestral-latest",
      "apiKey": "..."
    }
  ],
  "mcpServers": {
    "code-reader": {
      "command": "python.exe",
      "args": ["G:\\Mon Drive\\IA\\mcp_rag_server\\mcp_rag_server.py"],
      "env": {
        "API_KEY": ""
      }
    }
  }
}
```

**Important**: Adaptez les chemins selon votre installation.

### ajout du modèle local (si ollama)

```batch
ollama pull nomic-embed-text
ollama pull mistral
ollama pull gpt-oss
```

### mise à jour des modèles locaux (si ollama)

```batch
for /f "tokens=1" %i in ('ollama list') do ollama pull %i
```

## Dépannage

- Vérifiez que le venv est activé
- Vérifiez que la clé API est correcte
- Vérifiez que les dépendances sont installées: `pip list`
