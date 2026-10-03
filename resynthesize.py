"""สร้าง objectives.json ใหม่ของบทเรียนที่มีอยู่แล้ว จากไฟล์ข้อความเดิมที่ OCR/index ไว้แล้ว (ไม่ต้องทำซ้ำ)

ใช้เมื่ออัปเดต template (templates/lo, templates/softskills) หรือ prompt ของ Synthesizer
มี 3 โหมด:
1. สร้างใหม่ทั้งหมด — คำทักทายที่ cache ไว้ (greetings/) จะถูกลบ เพราะสร้างจาก Sub LO ชุดเดิม
2. สร้างเฉพาะ soft skill ใหม่ (ขั้น 4) — ใช้ sub_los เดิม
3. ดึง soft skill ที่ไม่ถูกเลือกกลับมา (add_softskill) — รัน 4B สกิลเดียว
"""
import json
import shutil
from openai import OpenAI
from config import OPENROUTER_API_KEY
from cost_tracker import CostTracker
from main import print_objectives
from mentor import LESSONS_DIR, select_option
from run_log import RunLog
from synthesizer import Synthesizer

MODES = ["สร้างใหม่ทั้งหมด", "สร้างเฉพาะ soft skill ใหม่ (ขั้น 4)", "ดึง soft skill ที่ไม่ถูกเลือกกลับมา"]


def _ask_add_softskill(synth: Synthesizer, lesson_path) -> dict | None:
    objectives = json.loads((lesson_path / "objectives.json").read_text(encoding="utf-8"))
    not_selected = objectives.get("softskills_not_selected", [])
    if not not_selected:
        print("⚠️  บทนี้ไม่มีสกิลที่ไม่ถูกเลือก")
        return None
    choice   = select_option("เลือกสกิล", [f"{s['id']} — {s.get('reason', '')}" for s in not_selected])
    skill_id = choice.split(" ", 1)[0]

    for lo in objectives["sub_los"]:
        print(f"  {lo['id']}: {lo['statement']}")
    linked = [i.strip() for i in input("linked_sub_los (คั่นด้วย , เช่น s2,s3): ").split(",") if i.strip()]
    sub_lo = select_option("sub_lo ที่จะปรับกิจกรรม", linked) if linked else ""
    how    = input("วิธีปรับกิจกรรม (how): ").strip()
    return synth.add_softskill(str(lesson_path), skill_id, linked, {"sub_lo": sub_lo, "how": how})


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
    mode        = MODES.index(select_option("เลือกโหมด", MODES))

    client  = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)
    tracker = CostTracker()
    tracker.run_log = RunLog(process="resynth", subject=subject, lesson=lesson, tracker=tracker)
    synth   = Synthesizer(client, cost_tracker=tracker)

    if mode == 0:
        objectives = synth.synthesize(str(lesson_path))
        if "error" in objectives:
            tracker.run_log.close()
            return
        greetings = lesson_path / "greetings"
        if greetings.exists():
            shutil.rmtree(greetings)
            print("  ลบคำทักทายเดิมแล้ว (จะสร้างใหม่ตอนนักเรียนเข้าเรียนครั้งแรก)")
    elif mode == 1:
        objectives = synth.resynthesize_softskills(str(lesson_path))
    else:
        try:
            objectives = _ask_add_softskill(synth, lesson_path)
        except ValueError as e:
            print(f"⚠️  {e}")
            objectives = None
        if objectives is None:
            tracker.run_log.close()
            return

    print_objectives(objectives)
    tracker.run_log.close()
    tracker.print_summary()


if __name__ == "__main__":
    main()
