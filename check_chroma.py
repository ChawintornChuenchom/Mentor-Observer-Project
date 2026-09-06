import sys
import io
import chromadb

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

lesson_path = sys.argv[1] if len(sys.argv) > 1 else "lessons/ชีวะ/บท 1"
db_path = f"{lesson_path}/chroma_db"

client = chromadb.PersistentClient(path=db_path)
collection = client.get_or_create_collection(name="lesson")

count = collection.count()
print(f"DB: {db_path}")
print(f"Collection: {collection.name}")
print(f"Total chunks: {count}\n")

if count:
    data = collection.get(limit=count, include=["documents", "metadatas"])
    for i, (doc, meta) in enumerate(zip(data["documents"], data["metadatas"])):
        print(f"--- chunk {i} | source={meta.get('source')} | idx={meta.get('chunk')} ---")
        print(doc[:200].replace("\n", " "))
        print()
