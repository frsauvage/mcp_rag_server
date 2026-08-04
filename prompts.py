"""
prompts.py — Chargement des templates de prompts depuis prompts/*.yaml

Chaque fichier YAML expose au minimum une clé "template" (str.format()-compatible).
Le chargement est mis en cache : un fichier n'est lu qu'une seule fois par nom.
"""
from functools import lru_cache
from pathlib import Path

import yaml

PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> dict:
    """Charge et parse prompts/<name>.yaml. Résultat mis en cache."""
    path = PROMPTS_DIR / f"{name}.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)
