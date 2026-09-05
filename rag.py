import hashlib
from pathlib import Path
from openai import OpenAI
from config import MODEL_EMBEDDING

import chromadb
from chromadb.utils import embedding_functions

try:
    import pypdf
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

try:
    from docx import Document as DocxDocument
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    from pptx import Presentation
    HAS_PPTX = True
except ImportError:
    HAS_PPTX = False


def extract_text(file_path: str) -> str:
    path = Path(file_path)
    ext  = path.suffix.lower()

    if ext in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")

    elif ext == ".pdf":
        if not HAS_PDF:
            raise ImportError("ติดตั้ง pypdf ก่อน: pip install pypdf")
        reader = pypdf.PdfReader(file_path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    elif ext == ".docx":
        if not HAS_DOCX:
            raise ImportError("ติดตั้ง python-docx ก่อน: pip install python-docx")
        doc = DocxDocument(file_path)
        return "\n".join(p.text for p in doc.paragraphs)

    elif ext == ".pptx":
        if not HAS_PPTX:
            raise ImportError("ติดตั้ง python-pptx ก่อน: pip install python-pptx")
        prs   = Presentation(file_path)
        texts = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text"):
                    texts.append(shape.text)
        return "\n".join(texts)

    else:
        raise ValueError(f"ไม่รองรับไฟล์ประเภท: {ext}")


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    words  = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk.strip())
        i += chunk_size - overlap
    return chunks


class RAG:
    def __init__(self, lesson_path: str, client: OpenAI, cost_tracker=None):
        self.lesson_path = Path(lesson_path)
        self.db_path     = str(self.lesson_path / "chroma_db")

        self.ef = embedding_functions.OpenAIEmbeddingFunction(
            api_key=client.api_key,
            api_base="https://openrouter.ai/api/v1",
            model_name=MODEL_EMBEDDING
        )

        self.cost_tracker = cost_tracker
        self.chroma     = chromadb.PersistentClient(path=self.db_path)
        self.collection = self.chroma.get_or_create_collection(
            name="lesson",
            embedding_function=self.ef
        )

    def index_files(self):
        supported = {".txt", ".md", ".pdf", ".docx", ".pptx"}
        files = [
            f for f in self.lesson_path.iterdir()
            if f.is_file() and f.suffix.lower() in supported
        ]

        if not files:
            print("  ไม่พบไฟล์ที่รองรับในโฟลเดอร์นี้")
            return

        total_chunks = 0
        for file in files:
            print(f"  กำลังประมวลผล: {file.name}")
            try:
                text   = extract_text(str(file))
                chunks = chunk_text(text)

                ids, docs, metas = [], [], []
                for i, chunk in enumerate(chunks):
                    chunk_id = hashlib.md5(f"{file.name}:{i}".encode()).hexdigest()
                    ids.append(chunk_id)
                    docs.append(chunk)
                    metas.append({"source": file.name, "chunk": i})

                batch = 50
                for b in range(0, len(ids), batch):
                    self.collection.upsert(
                        ids=ids[b:b+batch],
                        documents=docs[b:b+batch],
                        metadatas=metas[b:b+batch]
                    )

                total_chunks += len(chunks)
                print(f"    → {len(chunks)} chunks")

            except Exception as e:
                print(f"    ⚠️  ข้ามไฟล์ {file.name}: {e}")

        print(f"\n  รวม {total_chunks} chunks เก็บใน ChromaDB แล้ว ✅")

    def query(self, question: str, n_results: int = 5) -> list[str]:
        count = self.collection.count()
        if count == 0:
            return []
        results = self.collection.query(
            query_texts=[question],
            n_results=min(n_results, count)
        )
        return results["documents"][0] if results["documents"] else []