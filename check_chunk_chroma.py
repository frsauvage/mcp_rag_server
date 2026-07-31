import argparse
import os
import chromadb

def _select_collection(client: "chromadb.ClientAPI", requested_name: str) -> str | None:
    """
    Recupere le nom de collection a utiliser. Si `requested_name` n'existe pas
    dans la base, propose interactivement la liste des collections disponibles
    (l'utilisateur n'a generalement pas cette liste sous la main).
    """
    existing = [c.name for c in client.list_collections()]

    if requested_name in existing:
        return requested_name

    print(f"Collection '{requested_name}' introuvable dans cette base ChromaDB.")
    if not existing:
        print("Aucune collection disponible dans cette base.")
        return None

    print("Collections disponibles :")
    for idx, name in enumerate(existing, 1):
        print(f"  {idx}. {name}")

    choice = input("Selectionnez une collection (numero ou nom, Entree pour annuler) : ").strip()
    if not choice:
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(existing):
        return existing[int(choice) - 1]
    if choice in existing:
        return choice

    print(f"Choix invalide : '{choice}'")
    return None


def inspect_chunks_for_file(project_root: str, relative_file_path: str, collection_name: str = "code_chunks"):
    """
    Se connecte à ChromaDB, extrait et affiche tous les chunks
    associés à un fichier spécifique.
    """
    # 1. Connexion au client ChromaDB (la base est stockee dans le repertoire CIBLE, cf. store.py)
    # Si vous utilisez un client persistant local :
    chroma_path = os.path.join(project_root, "chroma_db")
    client = chromadb.PersistentClient(path=chroma_path)

    # Si vous utilisez le client HTTP/Serveur, décommentez plutôt ceci :
    # client = chromadb.HttpClient(host="localhost", port=8000)

    # 2. Récupération de la collection (code_chunks par défaut, sélection interactive sinon)
    collection_name = _select_collection(client, collection_name)
    if collection_name is None:
        return

    try:
        collection = client.get_collection(name=collection_name)
    except Exception as e:
        print(f"Impossible d'ouvrir la collection '{collection_name}': {e}")
        return

    # 3. Requête avec filtre sur les métadonnées
    # ChromaDB utilise l'opérateur $eq (égal à) pour le filtrage direct
    print(f"--- Recherche des chunks pour : {relative_file_path} ---")
    results = collection.get(
        where={"relative_path": relative_file_path},
        include=["documents", "metadatas"] # On demande le texte brut + les métadonnées
    )

    documents = results.get("documents", [])
    metadatas = results.get("metadatas", [])
    ids = results.get("ids", [])

    if not ids:
        print("Aucun chunk trouvé dans la base pour ce fichier. Vérifiez :")
        print(f"1. Si le fichier a bien été indexé.")
        print(f"2. La syntaxe exacte du chemin relatif stocké (ex: 'src/main.cpp' vs 'main.cpp').")
        return

    print(f"Trouvé : {len(ids)} chunk(s)\n")

    # 4. Affichage trié par ligne de début pour une lecture naturelle
    # On combine les données pour pouvoir les trier par la clé 'start_line'
    all_chunks = []
    for i in range(len(ids)):
        all_chunks.append({
            "id": ids[i],
            "content": documents[i],
            "metadata": metadatas[i]
        })
    
    # Tri par ligne de départ pour valider la chronologie du fichier
    all_chunks.sort(key=lambda x: x["metadata"].get("start_line", 0))

    # Imprimer le résultat
    for idx, chunk in enumerate(all_chunks, 1):
        meta = chunk["metadata"]
        print(f"{"="*20} CHUNK DB #{idx} (ID: {chunk['id']}) {"="*20}")
        print(f"Symbole : {meta.get('symbol_name', 'Inconnu')}")
        print(f"Type    : {meta.get('chunk_type', 'Inconnu')}")
        print(f"Lignes  : {meta.get('start_line')} à {meta.get('end_line')}")
        print(f"SHA-256 : {meta.get('file_hash')}")
        
        # Décoder la liste des symboles référencés (sauvegardée en chaîne séparée par des '|')
        refs = meta.get('symbols_referenced', '')
        print(f"Refs    : {refs.split('|') if refs else []}")
        
        print("-" * 50)
        print("CONTENU EN BASE :")
        print(chunk["content"])
        print(f"{"="*60}\n")

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_chunk_chroma.py",
        description="Affiche les chunks stockes en base ChromaDB pour un fichier donne.",
    )
    parser.add_argument(
        "-r", "--root",
        dest="dossier_projet",
        metavar="DOSSIER_PROJET",
        required=True,
        help="Racine du projet indexe (ex: 'G:\\Mon Drive\\Cours\\Cours C++')",
    )
    parser.add_argument(
        "-f", "--file",
        dest="fichier_a_verifier",
        metavar="FICHIER",
        required=True,
        help="Chemin relatif du fichier tel qu'indexe (ex: 'Formation\\async.cpp')",
    )
    parser.add_argument(
        "-c", "--collection",
        dest="collection_name",
        metavar="COLLECTION",
        default="code_chunks",
        help="Nom de la collection ChromaDB a interroger (defaut : code_chunks). "
             "Si introuvable, la liste des collections disponibles est proposee interactivement.",
    )
    return parser


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    inspect_chunks_for_file(args.dossier_projet, args.fichier_a_verifier, args.collection_name)