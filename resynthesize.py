"""สร้าง objectives.json ใหม่ของบทเรียนที่มีอยู่แล้ว จาก ChromaDB เดิม (ไม่ต้อง OCR/index ซ้ำ)

ใช้เมื่ออัปเดต template (templates/lo, templates/softskills) หรือ prompt ของ Synthesizer
คำทักทายที่ cache ไว้ (greetings/) จะถูกลบ เพราะสร้างจาก Sub LO ชุดเดิม
"""
import shutil
from openai import OpenAI
from config import OPENROUTER_API_KEY
from cost_tracker import CostTracker
from main import print_objectives
from mentor import LESSONS_DIR, select_option
from rag import RAG
from synthesizer import Synthesizer


def main():
    subjects = sorted(d.name for d in LESSONS_DIR.iterdir() if d.is_dir())
    subject  = select_option("เลือกรายวิชา", subjects)
    lessons  = sorted(
        d.name for d in (LESSONS_DIR / subject).iterdir()
        if d.is_dir() and (d / "chroma_db").exists()
    )
    if not lessons:
        print("⚠️  ไม่พบบทเรียนที่ index แล้ว")
        return
    lesson      = select_option("เลือกบทเรียน", lessons)
    lesson_path = LESSONS_DIR / subject / lesson

    client  = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)
    tracker = CostTracker()
    rag     = RAG(str(lesson_path), client, cost_tracker=tracker)

    objectives = Synthesizer(client, rag, cost_tracker=tracker).synthesize(str(lesson_path))
    if "error" in objectives:
        return

    greetings = lesson_path / "greetings"
    if greetings.exists():
        shutil.rmtree(greetings)
        print("  ลบคำทักทายเดิมแล้ว (จะสร้างใหม่ตอนนักเรียนเข้าเรียนครั้งแรก)")

    print_objectives(objectives)
    tracker.print_summary()


if __name__ == "__main__":
    main()
