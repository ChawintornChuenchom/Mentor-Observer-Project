import json
import re
from pathlib import Path
from openai import OpenAI
from config import MODEL_MENTOR, MODEL_SYNTHESIZER, OPENROUTER_API_KEY
from rag import RAG
from observer import Observer
from cost_tracker import CostTracker

CHARACTERS_DIR = Path("characters")
LESSONS_DIR    = Path("lessons")

# จำนวน message ล่าสุดที่ส่งแบบเต็มๆ ที่เก่ากว่านี้จะถูกยุบเป็น rolling summary
RECENT_WINDOW = 12
# ปล่อยให้ history โตถึงเท่านี้ก่อนค่อยยุบ (กันไม่ให้เรียก summarizer ทุก turn)
FOLD_TRIGGER  = RECENT_WINDOW * 2

OPENING_INSTRUCTION = ("[เริ่ม session ใหม่ ทักทายนักเรียน แนะนำบทเรียนสั้นๆ "
                       "แล้วเริ่มปูพื้นฐานหัวข้อแรก (ขั้น 1)]")

DONE_NOTE = ("[นักเรียนทำคะแนนผ่านครบทุกวัตถุประสงค์หลักแล้ว — รอบถัดไปให้แจ้งนักเรียน"
             "ด้วยน้ำเสียงตามคาแรกเตอร์ว่าเรียนจบบทนี้แล้ว จะออก (พิมพ์ quit) หรือถามอะไรต่อก็ได้]")


def load_character(name: str) -> str:
    path = CHARACTERS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"ไม่พบไฟล์ character: {path}")
    return path.read_text(encoding="utf-8")


def build_static_system_prompt(character_text: str, objectives: dict) -> str:
    """สร้าง system prompt ส่วนที่ "นิ่ง" ตลอด session — สร้างครั้งเดียวตอนเริ่ม

    ห้ามใส่อะไรที่เปลี่ยนรายเทิร์น (RAG context, หัวข้อปัจจุบัน ฯลฯ) ลงในนี้
    เพราะ Gemini 2.5 Flash ใช้ implicit caching ได้ต่อเมื่อ prefix ของ messages เหมือนเดิม
    ส่วนที่เปลี่ยนทุกเทิร์นให้ไปแนบท้ายข้อความ user ล่าสุดแทน
    """
    sub_lo_lines = []
    for lo in objectives.get("sub_los", []):
        lo_type = lo.get("type", "conceptual")
        sub_lo_lines.append(f"- {lo['id']} [{lo_type}]: {lo['statement']}")
    sub_los = "\n".join(sub_lo_lines)

    return f"""คุณคือ AI-Mentor ตามคาแรกเตอร์และกฎต่อไปนี้:

{character_text}

---
บทเรียน: {objectives.get('lesson_title', '')}
วัตถุประสงค์หลัก: {objectives.get('main_lo', '')}

Sub LO ที่ต้องสอนให้ครบ (ในวงเล็บเหลี่ยมคือประเภท):
{sub_los}

ประเภทของ Sub LO:
- [conceptual] = วัดความเข้าใจ / วิเคราะห์ / เปรียบเทียบ / ประยุกต์
- [factual] = ข้อมูล / นิยาม / ตัวเลข-ชื่อ / โครงสร้าง ที่ต้องจำตรงตัว
  → บอกข้อมูลจาก [บริบทอ้างอิง] ตรงๆ ได้เลย ไม่ต้องให้ทาย แล้วค่อยถามต่อยอดเชิงเข้าใจ

---
วิธีสอนแต่ละหัวข้อ (ทำตามลำดับ ห้ามข้ามขั้น 1):

1. ปูพื้นฐานก่อน — อธิบายศัพท์หลัก แนวคิด และข้อเท็จจริงที่จำเป็น สั้นกระชับ ตามสไตล์คาแรกเตอร์
   ดึงเนื้อหาจาก [บริบทอ้างอิง] ให้นักเรียน "มีของ" พอจะคิดต่อได้
   ตัวอย่าง: ชีวะ → บอกชื่อออร์แกเนลล์หลัก + หน้าที่คร่าวๆ ก่อน |
            คณิต → บอกนิยาม / สูตร / ขั้นตอนพื้นฐาน ก่อนให้ลองทำ |
            ประวัติศาสตร์ → บอกว่าใครเป็นใคร เหตุการณ์อะไร ยุคไหน ก่อน
   ⚠️ ห้ามถามคำถามที่นักเรียนซึ่งเพิ่งเรียนเรื่องนี้ครั้งแรกไม่มีทางตอบได้

2. เช็คความเข้าใจ — ถามคำถามง่ายๆ ที่ตอบได้จากสิ่งที่เพิ่งอธิบาย ถ้าตอบไม่ได้ให้อธิบายซ้ำอีกแบบ

3. Socratic — พอมีพื้นแล้ว ค่อยถามให้วิเคราะห์ / เปรียบเทียบ / ประยุกต์ / อธิบายเหตุผล
   ช่วงนี้ไม่เฉลยคำตอบของคำถามวิเคราะห์ตรงๆ ให้ค่อยๆ นำด้วยคำถาม
   ถ้านักเรียนตอบสั้นหรือไม่มีเหตุผล ให้ถามกลับ "ทำไมถึงคิดแบบนั้น?"

4. สรุปปิดหัวข้อ — ให้นักเรียนสรุปด้วยคำพูดตัวเองก่อนไปหัวข้อถัดไป (ห้ามสรุปแทนนักเรียน)

หลักคิด: Socratic = ชวนคิดต่อยอด *หลัง* นักเรียนมีพื้นฐานแล้ว ไม่ใช่การกั๊กความรู้พื้นฐานไว้ให้ทาย
ประเมินเองว่านักเรียนอยู่ขั้นไหน: ถ้าเพิ่งเริ่มหัวข้อ / บอกว่าไม่รู้ / ไม่เคยเรียนมาก่อน → กลับไปขั้น 1

---
กฎการสอนที่ต้องปฏิบัติเสมอ:
- ยึด [หัวข้อที่กำลังสอน] ที่แนบมากับข้อความนักเรียนเป็นหลัก อย่าหลุดประเด็น
- ถ้านักเรียนพิมพ์เรื่องนอกบทเรียน (บ่น / หงุดหงิด / คุยเล่น) ให้ตอบสั้นๆ ตามคาแรกเตอร์
  แล้วดึงกลับเข้าหัวข้อปัจจุบัน อย่าดึงเนื้อหาอื่นมาตอบให้หลุดทาง
- ใช้เฉพาะข้อมูลใน [บริบทอ้างอิง] ที่แนบมา อย่าเดาเนื้อหาเอง ถ้าบริบทไม่พอให้ถามนักเรียนกลับ
- ห้ามกุตัวเลข ข้อมูล หรือตารางผลการทดลองที่ไม่มีใน [บริบทอ้างอิง] — ถ้าไม่มีตัวอย่างให้สอนหลักการแทน
- reply เป็นข้อความสนทนาปกติ พิมพ์แบบคุยกับนักเรียน ห้ามใช้ตาราง markdown / **ตัวหนา** / หัวข้อย่อยซับซ้อน
- ห้ามบอกว่านักเรียนผ่านหรือไม่ผ่าน
- ห้ามพูดถึง AI-Observer ต่อหน้านักเรียน

---
trigger Observer เมื่อนักเรียน:
- อธิบายแนวคิดด้วยคำพูดตัวเองได้
- แก้โจทย์พร้อมอธิบายเหตุผลได้
- ตั้งคำถามที่แสดงว่ากำลัง process ข้อมูลจริงๆ
- สรุปความเข้าใจด้วยคำพูดตัวเองได้

ถ้าได้รับ [OBSERVER_FEEDBACK: ...] ให้ใช้ข้อมูลนั้นปรับวิธีสอน แต่ห้ามบอกนักเรียน
ถ้าได้รับ [นักเรียนทำคะแนนผ่านครบ...] ให้แจ้งนักเรียนว่าเรียนจบบทนี้แล้ว ออก (quit) หรือถามต่อได้

output ต้องเป็น JSON เสมอ ห้ามมี markdown:
{{
  "reply": "ข้อความที่จะพูดกับนักเรียน",
  "current_lo": "s1",
  "trigger_observer": true หรือ false,
  "trigger_lo": ["s1"] หรือ []
}}

current_lo = id ของ Sub LO ที่กำลังสอนอยู่ ณ ข้อความนี้ (เลือกจากรายการด้านบน 1 ค่าเสมอ)"""


def summarize_history(client: OpenAI, messages: list, prev_summary: str | None,
                      tracker: CostTracker | None = None) -> str:
    """ยุบข้อความเก่าเป็น rolling summary สั้นๆ 1 ก้อน (เรียก MODEL_SYNTHESIZER ครั้งเดียว)"""
    convo = "\n".join(
        f"{'นักเรียน' if m['role'] == 'user' else 'Mentor'}: {m['content']}"
        for m in messages
    )
    base = f"สรุปเดิม (รวมเข้าไปด้วย):\n{prev_summary}\n\n" if prev_summary else ""

    resp = client.chat.completions.create(
        model=MODEL_SYNTHESIZER,
        messages=[
            {"role": "system", "content":
                "สรุปบทสนทนาการสอนต่อไปนี้เป็นภาษาไทยสั้นๆ ไม่เกิน 6 บรรทัด "
                "เก็บเฉพาะ: หัวข้อที่สอนไปแล้ว, สิ่งที่นักเรียนเข้าใจ/ยังไม่เข้าใจ, "
                "ความเข้าใจผิดที่พบ ตอบเป็นข้อความสรุปล้วนๆ ไม่มีเกริ่นนำ"},
            {"role": "user", "content": f"{base}บทสนทนา:\n{convo}"}
        ]
    )
    if tracker is not None and getattr(resp, "usage", None):
        tracker.track_synthesizer(
            resp.usage.prompt_tokens or 0,
            resp.usage.completion_tokens or 0
        )
    return resp.choices[0].message.content.strip()


def parse_json(raw: str) -> dict:
    """แปลง output ของ LLM เป็น dict

    strict=False สำคัญมาก — Gemini มักใส่ newline/tab ตัวจริงในค่า string ของ JSON
    ซึ่ง json.loads ปกติจะ reject ("Invalid control character") ทำให้ทั้งระบบพัง
    """
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw[3:]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw, strict=False)


def salvage_mentor(raw: str, default_lo: str | None) -> dict:
    """กู้ field จาก output ที่ parse เป็น JSON ไม่ได้ — ดีกว่าโชว์ JSON ดิบให้นักเรียน"""
    m = re.search(
        r'"reply"\s*:\s*"(.*?)"\s*,\s*"(?:current_lo|trigger_observer|trigger_lo)"',
        raw, re.S,
    )
    reply = (m.group(1).replace('\\"', '"').replace('\\n', '\n').strip()
             if m else raw)
    lo   = re.search(r'"current_lo"\s*:\s*"(s\d+)"', raw)
    trig = re.search(r'"trigger_observer"\s*:\s*(true|false)', raw)
    tlo  = re.findall(r'"(s\d+)"', raw.split("trigger_lo", 1)[1]) if "trigger_lo" in raw else []
    return {
        "reply":            reply,
        "current_lo":        lo.group(1) if lo else default_lo,
        "trigger_observer": (trig.group(1) == "true") if trig else False,
        "trigger_lo":        tlo,
    }


def _sys_msg(static_system_prompt: str) -> dict:
    """system message ของ Mentor พร้อม cache_control

    Gemini implicit caching ผ่าน OpenRouter ไม่ทำงาน (ทดสอบแล้ว cached=0)
    ต้องใส่ cache_control เอง — turn ที่ hit cache ถูกลง ~50% (prompt คงที่ ~2,300 tok)
    """
    return {
        "role": "system",
        "content": [{
            "type": "text",
            "text": static_system_prompt,
            "cache_control": {"type": "ephemeral"},
        }],
    }


def load_or_make_greeting(client: OpenAI, lesson_path: Path, character_name: str,
                          static_system_prompt: str, mentor_extra: dict,
                          tracker: CostTracker) -> str:
    """คำทักทายเปิด session เหมือนกันทุกคนที่เรียน (บท × character) เดียวกัน
    → เจนครั้งเดียว เก็บไฟล์ไว้ คนต่อไปอ่าน verbatim ไม่เสีย token

    ถ้าแก้ objectives.json หรือไฟล์ character แล้วอยากให้เจนใหม่ ให้ลบโฟลเดอร์ greetings/
    """
    greet_file = lesson_path / "greetings" / f"{character_name}.txt"
    if greet_file.exists() and greet_file.stat().st_size > 0:
        return greet_file.read_text(encoding="utf-8")

    resp = client.chat.completions.create(
        model=MODEL_MENTOR,
        messages=[
            _sys_msg(static_system_prompt),
            {"role": "user", "content": OPENING_INSTRUCTION},
        ],
        extra_body=mentor_extra,
    )
    raw = resp.choices[0].message.content
    try:
        msg = parse_json(raw).get("reply", raw)
    except json.JSONDecodeError:
        msg = salvage_mentor(raw, None)["reply"]

    if resp.usage:
        tracker.track_mentor(
            resp.usage.prompt_tokens, resp.usage.completion_tokens,
            mentor_msg=msg, student_msg="",
        )

    greet_file.parent.mkdir(parents=True, exist_ok=True)
    greet_file.write_text(msg, encoding="utf-8")
    return msg


def all_core_passed(sub_los_list: list, hard_scores: dict) -> bool:
    """นักเรียนผ่านครบทุก sub_lo ที่ tag = core (ถ้าไม่มี core เลย ใช้ทุกข้อ)"""
    targets = [lo["id"] for lo in sub_los_list if lo.get("tag") == "core"] \
        or [lo["id"] for lo in sub_los_list]
    return bool(targets) and all(hard_scores.get(i) == 3 for i in targets)


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

    # ── system prompt ส่วน static (สร้างครั้งเดียว ไม่แก้อีกตลอด session) ──
    static_system_prompt = build_static_system_prompt(character_text, objectives)

    # sticky-route ตาม (บท × character) ไม่ผูกกับเวลา/คน → นักเรียนที่เรียนบทเดียวกัน
    # route ไป provider เดียวกัน ใช้ prompt cache ร่วมกันได้ (กองกลาง best-effort)
    session_id   = re.sub(r"\s+", "-", f"mentor-{subject}-{lesson}-{character_name}")
    mentor_extra = {"session_id": session_id}

    # ── state ระดับ session ──
    sub_los_list = objectives.get("sub_los", [])
    sub_lo_map   = {lo["id"]: lo for lo in sub_los_list}
    current_sub_lo = next(
        (lo for lo in sub_los_list if lo.get("tag") == "core"),
        sub_los_list[0] if sub_los_list else None
    )

    chat_history     = []
    hard_scores      = {lo["id"]: None for lo in sub_los_list}
    pending_feedback = None
    pending_note     = None   # ข้อความสั่ง Mentor รอบถัดไป (เช่น แจ้งเรียนจบ)
    done_announced   = False  # แจ้ง "ผ่านครบทุกข้อ" ไปแล้วหรือยัง
    history_summary  = None   # rolling summary ของข้อความที่ถูกยุบไปแล้ว
    summary_covers   = 0      # จำนวน message แรกของ chat_history ที่ยุบเข้า summary แล้ว
    session_events   = []     # บันทึกย่อรายรอบ (ป้อนให้ soft eval ตอนจบ แทน transcript เต็ม)

    print(f"\n{'=' * 55}")
    print(f"  {character_name} | {subject} / {lesson}")
    print(f"  พิมพ์ 'quit' เพื่อจบ session")
    print(f"{'=' * 55}\n")

    # ── Mentor ทักทาย (เจนครั้งเดียวต่อ บท×character แล้วใช้ซ้ำทุกคน) ──
    opening_msg = load_or_make_greeting(
        client, lesson_path, character_name,
        static_system_prompt, mentor_extra, tracker,
    )
    print(f"Mentor: {opening_msg}\n")
    chat_history.append({"role": "assistant", "content": opening_msg})

    # ── Main loop ───────────────────────────────────────────
    while True:
        user_input = input("นักเรียน: ").strip()

        # ── QUIT ──
        if user_input.lower() == "quit":
            soft_result = observer.evaluate_soft(
                chat_history, history_summary, session_events
            )

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

        # ── บันทึกเหตุการณ์รายรอบ (ไม่เรียก LLM) — ใช้ตอน soft eval ──
        event = {
            "t":   len(session_events) + 1,
            "len": len(user_input),
            "q":   user_input.rstrip().endswith(
                       ("?", "ไหม", "มั้ย", "หรอ", "เหรอ", "รึเปล่า", "หรือไม่")),
        }
        if any(w in user_input for w in ("บอกคำตอบ", "เฉลย", "ยอมแพ้", "ไม่ไหว")):
            event["giveup"] = True
        session_events.append(event)

        # ── RAG: ผสม statement ของหัวข้อปัจจุบันเข้ากับสิ่งที่นักเรียนพิมพ์ ──
        # กันกรณีนักเรียนพิมพ์เรื่องนอกเนื้อหา แล้วได้ context ที่ไม่เกี่ยวเลย
        if current_sub_lo:
            rag_query = f"{current_sub_lo['statement']} {user_input}"
        else:
            rag_query = user_input
        rag_context = "\n\n".join(rag.query(rag_query, n_results=5))

        # ── sliding window: ยุบ history เก่าเมื่อโตเกิน FOLD_TRIGGER ──
        # (ยุบเป็นชุด ไม่ยุบทีละคู่ทุก turn — เรียก summarizer แค่ทุกๆ ~RECENT_WINDOW turn)
        n_hist = len(chat_history)
        if n_hist - summary_covers > FOLD_TRIGGER:
            fold_upto = n_hist - RECENT_WINDOW
            if fold_upto % 2 == 0:        # ให้ recent เริ่มด้วย message ของนักเรียนเสมอ
                fold_upto -= 1
            if fold_upto > summary_covers:
                history_summary = summarize_history(
                    client, chat_history[summary_covers:fold_upto],
                    history_summary, tracker
                )
                summary_covers = fold_upto

        recent = chat_history[summary_covers:]   # ข้อความล่าสุดที่ส่งแบบเต็ม

        # ── สร้าง messages: [static system+cache] + [summary] + [recent] + [last user] ──
        messages = [_sys_msg(static_system_prompt)]
        if history_summary:
            messages.append({
                "role": "system",
                "content": f"[สรุปบทสนทนาช่วงต้นที่ผ่านมา]\n{history_summary}"
            })
        for msg in recent[:-1]:
            messages.append(msg)

        # ข้อความล่าสุด: แนบหัวข้อปัจจุบัน + RAG context ท้ายสุด (ส่วนที่เปลี่ยนทุกเทิร์น)
        lo_marker = ""
        if current_sub_lo:
            lo_marker = (
                f"[หัวข้อที่กำลังสอน: {current_sub_lo['id']} "
                f"({current_sub_lo.get('type', 'conceptual')}) — "
                f"{current_sub_lo['statement']}]\n"
            )
        last_content = (
            f"{lo_marker}"
            f"[บริบทอ้างอิง]\n{rag_context}\n\n"
            f"---\nนักเรียน: {user_input}"
        )
        if pending_feedback:
            last_content = (
                f"[OBSERVER_FEEDBACK: {json.dumps(pending_feedback, ensure_ascii=False)}]\n\n"
                + last_content
            )
            pending_feedback = None
        if pending_note:
            last_content = pending_note + "\n\n" + last_content
            pending_note = None

        messages.append({"role": "user", "content": last_content})

        # ── Mentor ตอบ ──
        resp = client.chat.completions.create(
            model=MODEL_MENTOR,
            messages=messages,
            extra_body=mentor_extra
        )

        raw = resp.choices[0].message.content
        try:
            mentor_data = parse_json(raw)
        except json.JSONDecodeError:
            mentor_data = salvage_mentor(
                raw, current_sub_lo["id"] if current_sub_lo else None
            )

        reply        = mentor_data.get("reply", "")
        will_trigger = mentor_data.get("trigger_observer", False)
        trigger_lo   = mentor_data.get("trigger_lo", [])

        # อัปเดตหัวข้อปัจจุบันตามที่ Mentor ระบุ
        new_lo = mentor_data.get("current_lo")
        if new_lo in sub_lo_map:
            current_sub_lo = sub_lo_map[new_lo]
        event["lo"] = current_sub_lo["id"] if current_sub_lo else None

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
            print(f"\n  [🔍 Observer → {trigger_lo}]")
            feedback    = observer.evaluate_hard(chat_history, trigger_lo)
            obs_summary = ""

            turn_hard = {}
            for item in feedback.get("hard", []):
                lo_id    = item.get("id")
                score    = item.get("s")
                evidence = item.get("e", "")
                if lo_id in hard_scores and score is not None:
                    hard_scores[lo_id] = score
                    turn_hard[lo_id] = score
                obs_summary += f"{lo_id}={score} "
                status = "✅" if score == 3 else "❌" if score is not None else "⬜"
                print(f"  [{status} Hard {lo_id} = {score}/3 | {evidence}]")
            if turn_hard:
                event["hard"] = turn_hard

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

            # นักเรียนผ่านครบทุกวัตถุประสงค์หลัก → สั่ง Mentor แจ้งรอบถัดไป (ครั้งเดียว)
            if not done_announced and all_core_passed(sub_los_list, hard_scores):
                done_announced = True
                pending_note   = DONE_NOTE
                print("  [🎉 นักเรียนผ่านครบทุกวัตถุประสงค์หลักแล้ว]")

        print()


if __name__ == "__main__":
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY
    )
    main(client)