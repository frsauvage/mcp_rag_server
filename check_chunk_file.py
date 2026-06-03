import chromadb

def inspect_chunks_for_file(project_root: str, relative_file_path: str):
    """
    Se connecte à ChromaDB, extrait et affiche tous les chunks
    associés à un fichier spécifique.
    """
    # 1. Connexion au client ChromaDB (adaptez le chemin selon store.py)
    # Si vous utilisez un client persistant local :
    client = chromadb.PersistentClient(path="./chroma_db")
    
    # Si vous utilisez le client HTTP/Serveur, décommentez plutôt ceci :
    # client = chromadb.HttpClient(host="localhost", port=8000)

    # 2. Récupération de la collection (remplacez par le vrai nom de votre collection)
    collection_name = "code_chunks"
    try:
        collection = client.get_collection(name=collection_name)
    except Exception as e:
        print(f"Impossible de trouver la collection '{collection_name}': {e}")
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

if __name__ == "__main__":
    # --- CONFIGURATION DE TEST ---
    # Le chemin relatif tel qu'il apparaîtrait en faisant : str(path.relative_to(root))
    FICHIER_A_VERIFIER = r'Formation\async.cpp' 
    DOSSIER_PROJET = "G:\Mon Drive\Cours\Cours C++"
    
    inspect_chunks_for_file(DOSSIER_PROJET, FICHIER_A_VERIFIER)