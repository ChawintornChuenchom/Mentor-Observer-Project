import json
from pathlib import Path
from openai import OpenAI
from config import MODEL_MENTOR, OPENROUTER_API_KEY
from rag import RAG
from observer import Observer
from cost_tracker import CostTracker

CHARACTERS_DIR = Path("characters")
LESSONS_DIR    = Path("lessons")


def load_character(name: str) -> str:
    path = CHARACTERS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"ไม่พบไฟล์ character: {path}")
    return path.read_text(encoding="utf-8")


def build_mentor_prompt(character_text: str, objectives: dict, rag_context: str) -> str:
    sub_los = "\n".join(
        f"- {lo['id']}: {lo['statement']}"
        for lo in objectives.get("sub_los", [])
    )
    return f"""คุณคือ AI-Mentor ตามคาแรกเตอร์และกฎต่อไปนี้:

{character_text}

---
บทเรียน: {objectives.get('lesson_title', '')}
วัตถุประสงค์หลัก: {objectives.get('main_lo', '')}

Sub LO ที่ต้องสอนให้ครบ:
{sub_los}

---
กฎการสอนที่ต้องปฏิบัติเสมอ:
- ใช้คำถามนำให้นักเรียนคิดเอง ห้ามบอกคำตอบตรงๆ
- ถ้านักเรียนตอบสั้นหรือไม่มีเหตุผล ให้ถามกลับว่า "ทำไมถึงคิดแบบนั้น?"
- ห้ามสรุปเนื้อหาให้นักเรียนก่อนที่นักเรียนจะลองสรุปเอง
- ก่อนไปหัวข้อถัดไป นักเรียนต้องสรุปด้วยคำพูดตัวเองก่อน
- ห้ามบอกว่านักเรียนผ่านหรือไม่ผ่าน
- ห้ามพูดถึง AI-Observer ต่อหน้านักเรียน

---
เนื้อหาอ้างอิงจาก RAG (ใช้สอนนักเรียน อย่าอ่านออกมาตรงๆ):
{rag_context}

---
trigger Observer เมื่อนักเรียน:
- อธิบายแนวคิดด้วยคำพูดตัวเองได้
- แก้โจทย์พร้อมอธิบายเหตุผลได้
- ตั้งคำถามที่แสดงว่ากำลัง process ข้อมูลจริงๆ
- สรุปความเข้าใจด้วยคำพูดตัวเองได้

ถ้าได้รับ [OBSERVER_FEEDBACK: ...] ให้ใช้ข้อมูลนั้นปรับวิธีสอน แต่ห้ามบอกนักเรียน

output ต้องเป็น JSON เสมอ ห้ามมี markdown:
{{
  "reply": "ข้อความที่จะพูดกับนักเรียน",
  "trigger_observer": true หรือ false,
  "trigger_lo": ["s1"] หรือ [],
  "trigger_reason": "เหตุผลสั้นๆ"
}}"""


def parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def select_option(label: str, options: list[str]) -> str:
    print(f"\n{label}:")
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        choice = input("เลือก: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]
        print("  กรุณาเลือกอีกครั้ง")


def main(client: OpenAI):
    print("=" * 55)
    print("  AI-Mentor System | Student Session")
    print("=" * 55)

    # เลือก character
    char_files = sorted([f.stem for f in CHARACTERS_DIR.glob("*.md")])
    if not char_files:
        print("⚠️  ไม่พบไฟล์ character")
        return
    character_name = select_option("เลือก Mentor", char_files)
    character_text = load_character(character_name)

    # เลือกวิชา
    subjects = sorted([d.name for d in LESSONS_DIR.iterdir() if d.is_dir()])
    if not subjects:
        print("⚠️  ยังไม่มีบทเรียน กรุณาให้อาจารย์รัน main.py ก่อน")
        return
    subject = select_option("เลือกรายวิชา", subjects)

    # เลือกบทเรียน
    lessons = sorted([
        d.name for d in (LESSONS_DIR / subject).iterdir()
        if d.is_dir() and (d / "objectives.json").exists()
    ])
    if not lessons:
        print("⚠️  ไม่พบบทเรียนที่พร้อมใช้งาน")
        return
    lesson = select_option("เลือกบทเรียน", lessons)

    lesson_path = LESSONS_DIR / subject / lesson

    # โหลด objectives
    with open(lesson_path / "objectives.json", encoding="utf-8") as f:
        objectives = json.load(f)

    # โหลด RAG, Observer, Tracker
    print(f"\nกำลังโหลด {subject} / {lesson}...")
    rag      = RAG(str(lesson_path), client)
    observer = Observer(client, objectives)
    tracker  = CostTracker()

    # RAG context เริ่มต้น
    rag_context = "\n\n".join(
        rag.query(objectives.get("main_lo", "เนื้อหาหลัก"), n_results=5)
    )
    system_prompt    = build_mentor_prompt(character_text, objectives, rag_context)
    chat_history     = []
    hard_scores      = {lo["id"]: None for lo in objectives.get("sub_los", [])}
    pending_feedback = None

    print(f"\n{'=' * 55}")
    print(f"  {character_name} | {subject} / {lesson}")
    print(f"  พิมพ์ 'quit' เพื่อจบ session")
    print(f"{'=' * 55}\n")

    # ── Mentor ทักทาย ──────────────────────────────────────
    opening_resp = client.chat.completions.create(
        model=MODEL_MENTOR,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": "[เริ่ม session ใหม่ ทักทายนักเรียนและแนะนำบทเรียน]"}
        ]
    )

    opening_raw = opening_resp.choices[0].message.content
    try:
        opening_data = parse_json(opening_raw)
        opening_msg  = opening_data.get("reply", opening_raw)
    except json.JSONDecodeError:
        opening_msg = opening_raw

    # track ค่าคำทักทาย (student_msg = "" เพราะยังไม่มีนักเรียนพิมพ์)
    if opening_resp.usage:
        tracker.track_mentor(
            opening_resp.usage.prompt_tokens,
            opening_resp.usage.completion_tokens,
            mentor_msg=opening_msg,
            student_msg=""
        )

    print(f"Mentor: {opening_msg}\n")
    chat_history.append({"role": "assistant", "content": opening_msg})

    # ── Main loop ───────────────────────────────────────────
    while True:
        user_input = input("นักเรียน: ").strip()

        # ── QUIT ──
        if user_input.lower() == "quit":
            soft_result = observer.evaluate_soft(chat_history)

            if hasattr(observer, '_last_usage') and observer._last_usage:
                tracker.track_observer(
                    observer._last_usage.prompt_tokens,
                    observer._last_usage.completion_tokens,
                    "soft_skill_eval"
                )

            print(f"\n{'=' * 55}")
            print(f"  สรุปผลการเรียน — {character_name}")
            print(f"  {subject} / {lesson}")
            print(f"{'=' * 55}")

            print(f"\n📊 Hard Skills (เต็ม 3):")
            all_hard_pass = True
            for lo_id, score in hard_scores.items():
                if score is None:
                    status, label = "⬜", "null"
                    all_hard_pass = False
                elif score == 3:
                    status, label = "✅", "3/3"
                else:
                    status, label = "❌", f"{score}/3"
                    all_hard_pass = False
                print(f"  {status} {lo_id}: {label}")

            print(f"\n🧠 Soft Skills (เต็ม 3):")
            soft_labels = {
                "curiosity":         "Curiosity",
                "persistence":       "Persistence",
                "critical_thinking": "Critical Thinking",
                "learning_speed":    "Learning Speed"
            }
            for sk_id, label in soft_labels.items():
                data    = soft_result.get(sk_id, {})
                score   = data.get("s")
                summary = data.get("e", "")
                if score is None:
                    status, score_label = "⬜", "null"
                elif score == 3:
                    status, score_label = "✅", "3/3"
                else:
                    status, score_label = "❌", f"{score}/3"
                print(f"  {status} {label}: {score_label}")
                if summary:
                    print(f"     └─ {summary}")

            student_msg_count = sum(1 for m in chat_history if m["role"] == "user")
            print(f"\n  Observer ถูกเรียก {observer.call_count} ครั้ง")
            print(f"  นักเรียนส่ง {student_msg_count} messages")
            print(f"  ผล Hard Skill: {'ผ่านทุกข้อ ✅' if all_hard_pass else 'ยังไม่ผ่านครบ ❌'}")

            tracker.save_chat_csv(subject, lesson, character_name)
            tracker.print_summary()
            break

        if not user_input:
            continue

        chat_history.append({"role": "user", "content": user_input})

        # อัปเดต RAG context
        rag_context   = "\n\n".join(rag.query(user_input, n_results=5))
        system_prompt = build_mentor_prompt(character_text, objectives, rag_context)

        # สร้าง messages
        messages = [{"role": "system", "content": system_prompt}]
        for msg in chat_history[:-1]:
            messages.append(msg)

        last_content = user_input
        if pending_feedback:
            last_content = (
                f"[OBSERVER_FEEDBACK: {json.dumps(pending_feedback, ensure_ascii=False)}]\n\n"
                + last_content
            )
            pending_feedback = None

        messages.append({"role": "user", "content": last_content})

        # ── Mentor ตอบ ──
        resp = client.chat.completions.create(
            model=MODEL_MENTOR,
            messages=messages
        )

        raw = resp.choices[0].message.content
        try:
            mentor_data = parse_json(raw)
        except json.JSONDecodeError:
            mentor_data = {
                "reply":            raw,
                "trigger_observer": False,
                "trigger_lo":       [],
                "trigger_reason":   ""
            }

        reply          = mentor_data.get("reply", "")
        will_trigger   = mentor_data.get("trigger_observer", False)
        trigger_lo     = mentor_data.get("trigger_lo", [])
        trigger_reason = mentor_data.get("trigger_reason", "")

        # track Mentor cost
        current_row = None
        if resp.usage:
            current_row = tracker.track_mentor(
                resp.usage.prompt_tokens,
                resp.usage.completion_tokens,
                mentor_msg=reply,
                student_msg=user_input
            )

        chat_history.append({"role": "assistant", "content": reply})
        print(f"\nMentor: {reply}")

        # ── Observer ──
        if will_trigger and trigger_lo:
            print(f"\n  [🔍 Observer → {trigger_lo} | {trigger_reason}]")
            feedback    = observer.evaluate_hard(chat_history, trigger_lo)
            obs_summary = ""

            for item in feedback.get("hard", []):
                lo_id    = item.get("id")
                score    = item.get("s")
                evidence = item.get("e", "")
                if lo_id in hard_scores and score is not None:
                    hard_scores[lo_id] = score
                obs_summary += f"{lo_id}={score} "
                status = "✅" if score == 3 else "❌" if score is not None else "⬜"
                print(f"  [{status} Hard {lo_id} = {score}/3 | {evidence}]")

            for note in feedback.get("n", []):
                if "PROMPT_INJECTION" in note.upper():
                    print(f"  [⚠️  {note}]")

            # track Observer cost
            if hasattr(observer, '_last_usage') and observer._last_usage:
                tracker.track_observer(
                    observer._last_usage.prompt_tokens,
                    observer._last_usage.completion_tokens,
                    obs_summary.strip(),
                    row_ref=current_row
                )

            pending_feedback = feedback

        print()


if __name__ == "__main__":
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY
    )
    main(client)