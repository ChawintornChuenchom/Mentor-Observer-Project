import json
from pathlib import Path
from tkinter import Tk, filedialog
from openai import OpenAI
from config import OPENROUTER_API_KEY
from rag import RAG
from synthesizer import Synthesizer
from cost_tracker import CostTracker

LESSONS_DIR = Path("lessons")
LESSONS_DIR.mkdir(exist_ok=True)

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)


def pick_files() -> list[Path]:
    """เปิด file explorer ให้เลือกไฟล์บทเรียน"""
    root = Tk()
    root.withdraw()        # ซ่อน window หลัก
    root.attributes("-topmost", True)

    paths = filedialog.askopenfilenames(
        title="เลือกไฟล์บทเรียน",
        filetypes=[
            ("ไฟล์บทเรียน", "*.pdf *.docx *.pptx *.txt *.md"),
            ("PDF",          "*.pdf"),
            ("Word",         "*.docx"),
            ("PowerPoint",   "*.pptx"),
            ("Text",         "*.txt *.md"),
            ("ทุกไฟล์",      "*.*"),
        ]
    )
    root.destroy()
    return [Path(p) for p in paths]


def ask(label: str) -> str:
    val = input(f"{label}: ").strip()
    while not val:
        val = input(f"{label}: ").strip()
    return val


def main():
    tracker = CostTracker()

    print("=" * 55)
    print("  AI-Mentor System | Teacher Setup")
    print("=" * 55)

    # กรอกชื่อวิชาและบทเรียนครั้งเดียว
    subject = ask("\nชื่อรายวิชา")
    lesson  = ask("ชื่อบทเรียน")

    lesson_path = LESSONS_DIR / subject / lesson
    lesson_path.mkdir(parents=True, exist_ok=True)

    print(f"\n📚 {subject} / {lesson}")

    # เปิด file explorer เลือกไฟล์
    files = pick_files()

    if not files:
        print("⚠️  ไม่ได้เลือกไฟล์ ออกจากระบบ")
        return

    # copy ไฟล์ที่เลือกไปยัง lesson_path
    import shutil
    print(f"\nคัดลอกไฟล์ {len(files)} ไฟล์:")
    for f in files:
        dest = lesson_path / f.name
        shutil.copy2(f, dest)
        print(f"  ✅ {f.name}")

    # RAG
    print("\n[1/2] กำลังทำ RAG...")
    rag = RAG(str(lesson_path), client, cost_tracker=tracker)
    rag.index_files()

    # Synthesizer
    print("\n[2/2] กำลังสังเคราะห์วัตถุประสงค์...")
    synth      = Synthesizer(client, rag, cost_tracker=tracker)
    objectives = synth.synthesize(str(lesson_path))

    # แสดงผล
    print("\n" + "=" * 55)
    print("✅ Setup เสร็จสมบูรณ์!")
    print(f"\n📋 Main LO:\n  {objectives.get('main_lo', '-')}")

    sub_los = objectives.get("sub_los", [])
    print(f"\nSub LOs ({len(sub_los)} ข้อ):")
    for lo in sub_los:
        tag = "⭐" if lo.get("tag") == "core" else "  "
        print(f"  {tag} {lo['id']}: {lo['statement']}")

    missing = objectives.get("missing_coverage", [])
    if missing:
        print(f"\nเนื้อหาที่ไม่ได้วัด ({len(missing)} รายการ):")
        for m in missing:
            print(f"  - {m}")

    tracker.print_summary()
    print("\nนักเรียนสามารถเริ่มเรียนได้แล้วโดยรัน: python mentor.py")


if __name__ == "__main__":
    main()