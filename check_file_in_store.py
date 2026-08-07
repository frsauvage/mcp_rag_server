from pathlib import Path
from indexer import Indexer
from store import CodeStore

dir_path = Path('G:/Mon Drive/Cours/Cours C++')

store = CodeStore(persist_dir=str(dir_path / "chroma_db"))
indexer = Indexer(store)
files = indexer._scan_files(dir_path)

print(f'Fichiers trouvés: {len(files)}')
async_files = [f for f in files if 'async' in f.name.lower()]
print(f'Fichiers avec \"async\": {len(async_files)}')
for f in async_files:
    print(f'  ✓ {f}')