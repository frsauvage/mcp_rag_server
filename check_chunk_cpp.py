import sys
from pathlib import Path

# On s'assure que le dossier courant est dans le path de recherche Python
sys.path.append(str(Path(__file__).parent))

from code_chunker import chunk_code

def main():
    # Définition des chemins
    root_dir = Path(r'G:\Mon Drive\Cours\Cours C++\test\Formation').resolve()
    cpp_file = root_dir / "async.cpp"
    
    if not cpp_file.exists():
        print(f"Erreur : Créez d'abord le fichier {cpp_file.name}")
        return

    print(f"--- Analyse de {cpp_file.name} ---")
    
    # Appel du chunker C++ via le point d'entrée
    chunks = chunk_code(path=cpp_file, root=root_dir, ext=".cpp")
    
    print(f"Nombre de chunks extraits : {len(chunks)}\n")
    
    for i, chunk in enumerate(chunks, 1):
        print(f"=== CHUNK {i} ===")
        print(f"Type de symbole : {chunk.chunk_type}")
        print(f"Nom qualifié   : {chunk.symbol_name}")
        print(f"Lignes         : {chunk.start_line} à {chunk.end_line}")
        print(f"Références     : {chunk.symbols_referenced}")
        print("-" * 40)
        print("CONTENU DU CHUNK :")
        print(chunk.content)
        print("=" * 40)
        print("\n")

if __name__ == "__main__":
    main()