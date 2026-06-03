from pathlib import Path
from indexer import Indexer
from store import CodeStore

store = CodeStore(persist_dir="./chroma_db")
indexer = Indexer(store)

dir_path = Path('G:/Mon Drive/Cours/Cours C++')
files = indexer._scan_files(dir_path, recursive=True)

print(f'Fichiers trouvés: {len(files)}')
async_files = [f for f in files if 'async' in f.name.lower()]
print(f'Fichiers avec \"async\": {len(async_files)}')
for f in async_files:
    print(f'  ✓ {f}')