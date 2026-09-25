import json
from pathlib import Path
from tkinter import Tk, filedialog
from openai import OpenAI
from config import OPENROUTER_API_KEY
from rag import RAG, pdf_to_txt, images_to_txt
from synthesizer import Synthesizer
from cost_tracker import CostTracker
from run_log import RunLog

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
        title="เลือกไฟล์บทเรียน (เลือกได้หลายไฟล์)",
        filetypes=[
            ("ไฟล์บทเรียน", "*.pdf *.docx *.pptx *.txt *.md "
                            "*.jpg *.jpeg *.png *.webp *.bmp *.tiff"),
            ("PDF",          "*.pdf"),
            ("รูปภาพ",       "*.jpg *.jpeg *.png *.webp *.bmp *.tiff"),
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

    tracker.run_log = RunLog(process="setup", subject=subject, lesson=lesson, tracker=tracker)

    print(f"\n📚 {subject} / {lesson}")

    # เปิด file explorer เลือกไฟล์
    files = pick_files()

    if not files:
        print("⚠️  ไม่ได้เลือกไฟล์ ออกจากระบบ")
        tracker.run_log.close()
        return

    # copy ไฟล์ที่เลือกไปยัง lesson_path
    import shutil
    print(f"\nคัดลอกไฟล์ {len(files)} ไฟล์:")
    for f in files:
        dest = lesson_path / f.name
        shutil.copy2(f, dest)
        print(f"  ✅ {f.name}")

    # OCR: แปลงไฟล์ PDF + รูปภาพ เป็นข้อความ (.txt) ก่อน แล้วค่อยเอาไป index
    print("\n[1/3] กำลังแปลงไฟล์ PDF / รูปภาพ เป็นข้อความ (OCR)...")
    try:
        pdf_to_txt(str(lesson_path), client, cost_tracker=tracker)
        images_to_txt(str(lesson_path), client, cost_tracker=tracker)
    except ImportError as e:
        print(f"  ⚠️  {e}")
        print("  ออกจากระบบ — ติดตั้งแล้วรันใหม่")
        tracker.run_log.close()
        return

    # RAG: เอาไฟล์ข้อความไปทำ ChromaDB
    print("\n[2/3] กำลังทำ RAG...")
    rag = RAG(str(lesson_path), client, cost_tracker=tracker)
    total_chunks = rag.index_files()

    if not total_chunks:
        print("\n⚠️  ไม่มีเนื้อหาถูก index — หยุดก่อนสังเคราะห์วัตถุประสงค์")
        tracker.run_log.close()
        tracker.print_summary()
        return

    # Synthesizer
    print("\n[3/3] กำลังสังเคราะห์วัตถุประสงค์...")
    synth = Synthesizer(client, cost_tracker=tracker)
    try:
        objectives = synth.synthesize(str(lesson_path))
    except ValueError as e:
        print(f"\n⚠️  {e}")
        tracker.run_log.close()
        tracker.print_summary()
        return

    print_objectives(objectives)
    tracker.run_log.close()
    tracker.print_summary()
    print("\nนักเรียนสามารถเริ่มเรียนได้แล้วโดยรัน: python mentor.py")


def print_objectives(objectives: dict):
    print("\n" + "=" * 55)
    print("✅ Setup เสร็จสมบูรณ์!")

    groups = objectives.get("prompt_groups", [])
    if groups:
        print(f"\n🧭 มุมมองการประเมิน: {', '.join(groups)}")
        if objectives.get("group_reason"):
            print(f"   {objectives['group_reason']}")

    print(f"\n📋 Main LO:\n  {objectives.get('main_lo', '-')}")

    sub_los = objectives.get("sub_los", [])
    print(f"\nSub LOs ({len(sub_los)} ข้อ):")
    for lo in sub_los:
        tag = "⭐" if lo.get("tag") == "core" else "  "
        print(f"  {tag} {lo['id']}: {lo['statement']}")
        rubric = lo.get("rubric") or {}
        for level in ("3", "2", "1", "0"):
            if rubric.get(level):
                print(f"       {level} = {rubric[level]}")

    soft = objectives.get("softskills", [])
    if soft:
        print(f"\n🧠 ตัวบ่งชี้ soft skill: {len(soft)} สกิล ({', '.join(s['id'] for s in soft)})")

    missing = objectives.get("missing_coverage", [])
    if missing:
        print(f"\nเนื้อหาที่ไม่ได้วัด ({len(missing)} รายการ):")
        for m in missing:
            print(f"  - {m}")


if __name__ == "__main__":
    main()