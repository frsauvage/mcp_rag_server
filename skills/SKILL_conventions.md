# Conventions de développement

## Style général

- Langue : commentaires et noms de variables en français
- Indentation : 4 espaces (Python), 2 espaces (C++)
- Longueur de ligne max : 120 caractères

## Nommage

- Classes : PascalCase
- Fonctions / méthodes : snake_case
- Constantes : UPPER_SNAKE_CASE
- Fichiers Python : snake_case.py
- Fichiers C++ : PascalCase.cpp / PascalCase.h

## Gestion des erreurs

- Toujours logger avant de lever une exception
- Utiliser des exceptions métier spécifiques plutôt que des exceptions génériques
- Ne jamais swallower silencieusement une exception en production

## Tests

- Un test par comportement, pas par fonction
- Nommer les tests : test_<contexte>_<comportement_attendu>
- Les fixtures partagées vont dans conftest.py
