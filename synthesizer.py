import hashlib
import json
import os
from openai import OpenAI
from config import MODEL_SYNTHESIZER, SYNTH_MAX_CHARS, SYNTH_PART_CHARS
from json_utils import parse_json
from rag import lesson_full_text
import template_loader as tpl

MAX_SUB_LOS    = 6   # เพดานตายตัว — ทุกบทเรียนต้องไม่เกินนี้ ไม่ว่าเนื้อหาจะยาว/ซับซ้อนแค่ไหน
SCHEMA_VERSION = 3   # 3 = อ่านเนื้อหาเต็มบท (ไม่ใช่ RAG top-k), rubric สร้างพร้อม LO ในขั้นเดียว, prompt cache

# ── ขั้น 1: เลือกมุมมองการประเมิน (P01–P18) ─────────────────
SELECT_GROUPS_PROMPT = """คุณคือระบบวิเคราะห์เนื้อหาบทเรียนเพื่อเลือก "มุมมองการประเมิน"

{index}

---
ทำตาม "วิธีเลือก" ข้างบน ตัดสินจากเนื้อหาที่แนบมาเท่านั้น ไม่ใช่ชื่อวิชา
เลือก prompt_groups ไม่เกิน 2 กลุ่ม (ถ้าไม่มีกลุ่มไหนเข้าเลย ตอบ [])

output เป็น JSON เท่านั้น ห้ามมี markdown:
{{
  "prompt_groups": ["P03"],
  "group_reason": "เหตุผลสั้นๆ ว่าทำไมเลือกกลุ่มเหล่านี้",
  "dropped_groups": ["กลุ่มที่ตัดทิ้งพร้อมเหตุผล (ถ้ามี)"]
}}"""

# ── ขั้น 2: สร้าง LO + rubric hard skill พร้อมกันในขั้นเดียว ──
SYNTH_LO_RUBRIC_PROMPT = f"""คุณคือระบบสังเคราะห์วัตถุประสงค์การเรียนรู้และเกณฑ์ประเมิน จากเนื้อหาบทเรียนเต็มบท

เนื้อหาที่แนบมาถูกแบ่งเป็นช่วงและติดป้าย [c1] [c2] ... ตามลำดับที่ปรากฏในเอกสารจริง
ใช้ป้ายเหล่านี้อ้างอิงใน evidence_chunks ตรงๆ ห้ามสร้างป้ายใหม่หรือเดาป้ายที่ไม่มีในเนื้อหา

งาน:
1. สังเคราะห์ main_lo: เป้าหมายรวมของบทเรียนใน 1 ประโยค ใช้คำกริยาที่วัดได้
2. สังเคราะห์ sub_los: แต่ละข้อต้องวัดได้จากบทสนทนา ไม่ซ้ำซ้อน
3. ระบุ missing_coverage: เนื้อหาที่วัดจากบทสนทนาไม่ได้
4. ต่อ sub_lo ทุกข้อ เขียนเกณฑ์ประเมิน (observable_evidence, mentor_activity, rubric) ตามหัวข้อด้านล่าง

กฎสำคัญ (การสร้าง sub_lo):
- ใช้คำกริยาที่วัดได้: อธิบาย แก้ แยก วิเคราะห์ ยกตัวอย่าง — ห้ามใช้: เข้าใจ รู้ เรียนรู้
- tag: core = ต้องผ่านทุกข้อ, supporting = เสริม
- สร้าง sub_los **ไม่เกิน {MAX_SUB_LOS} ข้อเด็ดขาด ไม่ว่าเนื้อหาจะยาวหรือซับซ้อนแค่ไหน**
  (เนื้อหาน้อยจะได้แค่ 2-3 ข้อก็ได้) ถ้าเนื้อหามีหัวข้อย่อยมากกว่า {MAX_SUB_LOS} เรื่อง ให้ยุบรวมหัวข้อ
  ที่ใกล้เคียง/ต่อเนื่องกันเข้าเป็นข้อเดียว แทนที่จะแตกเป็นหลายข้อ — 1 ข้อครอบคลุมกว้างดีกว่าแตกแคบๆ หลายข้อ
  ⚠️ ห้ามฝืนแตกหัวข้อให้ครบจำนวนใดๆ ถ้าเนื้อหาน้อย — 2 ข้อที่ดีดีกว่า {MAX_SUB_LOS} ข้อที่ซ้ำซ้อน
- ⚠️ ระวังวัตถุประสงค์ "คู่ขนาน" — ถ้าเจอ sub_lo 2 ข้อที่ใช้ทักษะ/เกณฑ์เดียวกัน แต่แยกไปใช้กับ
  2 กลุ่มที่เป็นคู่ตรงข้ามกัน ห้ามแยกเป็น 2 ข้อ ให้รวมเป็นข้อเดียวที่จำแนก/เปรียบเทียบทั้งสองฝั่งพร้อมกัน
  ตัวอย่างผิด: "ระบุตัวสะกดที่ทำให้เป็นคำตายได้" แยกจาก "ระบุตัวสะกดที่ทำให้เป็นคำเป็นได้"
  ตัวอย่างถูก: "จำแนกมาตราตัวสะกดที่ทำให้พยางค์เป็นคำเป็นหรือคำตายได้" (ข้อเดียว ครอบคลุมทั้งคู่)
- เรียง sub_los ตามลำดับการสอนจริง: s1 = พื้นฐานสุด → กลางๆ (ความเข้าใจ) → ข้อท้ายๆ = ซับซ้อนสุด

ทุก sub_lo ต้องมี field "type":
- "conceptual" = วัดการเข้าใจ / วิเคราะห์ / เปรียบเทียบ / ประยุกต์แนวคิด
    ห้ามเป็นแค่การจำโครงสร้างเอกสาร (เช่น "มีกี่หน่วย" "ชื่อหน่วยคืออะไร") เพราะนั่นคือ recall ไม่ใช่ critical thinking
- "factual" = ข้อมูลเชิงข้อเท็จจริง / โครงสร้างเอกสาร / นิยามเฉพาะ / ตัวเลข-ชื่อ ที่ต้องจำตรงตัว เถียงไม่ได้
แนวทาง: sub_lo ส่วนใหญ่ควรเป็น "conceptual" ให้ "factual" เฉพาะข้อที่เป็นข้อเท็จจริง/โครงสร้างล้วนๆ

เกณฑ์ประเมิน — ต่อ sub_lo ทุกข้อ:
ความหมายของระดับคงที่ ห้ามเปลี่ยน — 3 = ผ่าน อธิบายหรือแก้โจทย์ได้ด้วยตัวเองชัดเจน,
2 = ใกล้ผ่าน เข้าใจแต่ยังต้องการความช่วยเหลือบ้าง, 1 = ยังไม่ผ่าน มีหลักฐานว่าเข้าใจบ้างแต่ยังไม่พอ,
0 = มีความเข้าใจผิดที่สำคัญ
- observable_evidence: สิ่งที่นักเรียนต้องพูด/ทำให้เห็นในแชท จึงจะนับเป็นหลักฐาน
- mentor_activity: กิจกรรม/คำถามที่ Mentor ใช้เปิดโอกาสให้นักเรียนแสดงหลักฐานนั้น
- rubric: 1 ประโยคต่อระดับ บอกว่า "ระดับนี้ของ sub_lo ข้อนี้ นักเรียนพูดออกมาหน้าตาเป็นอย่างไร"
  เจาะจงเนื้อหาของ sub_lo ข้อนั้น ห้ามเขียนกว้างๆ ที่ใช้ได้กับทุกข้อ
  ระดับ 0 ให้ระบุความเข้าใจผิดที่พบบ่อยของเนื้อหานี้

output เป็น JSON เท่านั้น ห้ามมี markdown:
{{
  "lesson_title": "ชื่อบทเรียน",
  "main_lo": "นักเรียนสามารถ...",
  "sub_los": [
    {{
      "id": "s1",
      "statement": "นักเรียนสามารถ...",
      "tag": "core",
      "type": "conceptual",
      "evidence_chunks": ["c1", "c3"],
      "prompt_group": "P03",
      "content_type": "BIO-M",
      "observable_evidence": "...",
      "mentor_activity": "...",
      "rubric": {{"0": "...", "1": "...", "2": "...", "3": "..."}}
    }}
  ],
  "missing_coverage": ["เหตุผล..."]
}}"""

# ต่อท้าย SYNTH_LO_RUBRIC_PROMPT เมื่อขั้นเลือกมุมมองได้กลุ่มมา
LO_TEMPLATE_SECTION = """

---
มุมมองการประเมินที่เลือกไว้สำหรับบทเรียนนี้ — ใช้ "เน้นดูอะไร", "ป้าย", "Sub LO", "ไม่ตั้งเป็น Sub LO"
ของ template ประกอบการสร้าง sub_los (ถ้าขัดกับกฎสำคัญข้างบน ให้ยึดกฎข้างบน):
- prompt_group = id ของมุมมองที่ Sub LO นั้นมาจาก
- content_type = ป้ายของ template ที่ตรงกับ Sub LO นั้น
- สร้าง Sub LO เฉพาะป้ายที่พบจริงในเนื้อหา ห้ามสร้างเพื่อให้ครบทุกป้าย

{templates}"""

# ── ใช้สร้าง rubric ใหม่เฉพาะ sub_lo ที่ถูกรวมหลัง consolidate (rubric เดิมใช้ไม่ได้แล้ว) ──
HARD_RUBRIC_PROMPT = """คุณคือระบบสร้างเกณฑ์ประเมิน Hard Skill ต่อ Sub LO สำหรับ AI-Observer

ความหมายของระดับคงที่ ห้ามเปลี่ยน:
- 3 = ผ่าน อธิบายหรือแก้โจทย์ได้ด้วยตัวเองชัดเจน
- 2 = ใกล้ผ่าน เข้าใจแต่ยังต้องการความช่วยเหลือบ้าง
- 1 = ยังไม่ผ่าน มีหลักฐานว่าเข้าใจบ้างแต่ยังไม่พอ
- 0 = มีความเข้าใจผิดที่สำคัญ

ทุก Sub LO ให้เขียน:
- observable_evidence: สิ่งที่นักเรียนต้องพูด/ทำให้เห็นในแชท จึงจะนับเป็นหลักฐาน
- mentor_activity: กิจกรรม/คำถามที่ Mentor ใช้เปิดโอกาสให้นักเรียนแสดงหลักฐานนั้น
- rubric: 1 ประโยคต่อระดับ บอกว่า "ระดับนี้ของ Sub LO ข้อนี้ นักเรียนพูดออกมาหน้าตาเป็นอย่างไร"
  เจาะจงเนื้อหาของ Sub LO ข้อนั้น ห้ามเขียนกว้างๆ ที่ใช้ได้กับทุกข้อ
  ระดับ 0 ให้ระบุความเข้าใจผิดที่พบบ่อยของเนื้อหานี้

{templates}

output เป็น JSON เท่านั้น ห้ามมี markdown — ครบทุก Sub LO ที่ได้รับ:
{{
  "rubrics": [
    {{
      "id": "s1",
      "observable_evidence": "...",
      "mentor_activity": "...",
      "rubric": {{"0": "...", "1": "...", "2": "...", "3": "..."}}
    }}
  ]
}}"""

# ── ขั้น 4: สร้างตัวบ่งชี้ soft skill ครบ S01–S12 ────────────
SOFT_INDICATOR_PROMPT = """คุณคือระบบสร้างตัวบ่งชี้ Soft Skill ตามบทเรียน

{index}

---
รายละเอียดสกิลทั้งหมด:

{skills}

---
งาน: สร้างตัวบ่งชี้ให้ **ครบทุกสกิล {skill_ids}** (ไม่ต้องเลือก ไม่ต้องตัดทิ้ง)
- linked_sub_los: sub_lo id ที่เปิดโอกาสให้แสดงสกิลนี้ ([] ถ้าไม่มี)
- required_activity: กิจกรรมที่ Mentor ต้องทำเพิ่มในบทเรียนนี้ เพื่อให้สกิลนี้มีโอกาสแสดงออก
- ห้ามแก้ความหมายของระดับใน rubric กลาง
- เขียน lesson_indicators 1 ประโยคต่อระดับ บอกว่า "ระดับนี้หน้าตาเป็นอย่างไรในบทเรียนนี้"
- ตัวบ่งชี้ต้องเป็นพฤติกรรมทางความคิด/การสื่อสาร ไม่ใช่ความถูกต้องของเนื้อหา
- ระดับ 3 = สิ่งที่คาดหวังจากบทเรียนนี้ ระดับ 5 ต้องมีการถ่ายโอนหรือทำได้เองเกินที่สอน
- ห้ามใช้ "ตอบถูก" "คำนวณถูก" "ทำตามขั้นตอนได้" เป็นตัวบ่งชี้

ตัวอย่าง lesson_indicators — S02 ในบท "สมการเชิงเส้นตัวแปรเดียว"
{example}

output เป็น JSON เท่านั้น ห้ามมี markdown:
{{
  "softskills": [
    {{
      "id": "S01",
      "linked_sub_los": ["s1"],
      "required_activity": "...",
      "lesson_indicators": {{"1": "...", "2": "...", "3": "...", "4": "...", "5": "..."}}
    }}
  ]
}}"""

# ผ่าน pass เดียว โมเดลมักหลุดกฎเรื่องจำนวน/คู่ขนาน เพราะแข่งกับงานอื่นในพรอมต์เดียวกัน
# (ทดสอบแล้ว: ให้ตัวอย่างชัดเจนแล้วยังแยกคู่ใหม่ที่ไม่ได้ยกตัวอย่างไว้ และยังเกินจำนวนที่ขอ)
# เลยแยกเป็น pass 2 ที่ทำงานเดียวคือ "รวบให้เหลือไม่เกิน MAX_SUB_LOS ข้อ" ไม่ต้องแข่งกับงานอื่น
# ไม่ต้องอ่านเนื้อหาเต็มบทซ้ำ (ทำงานกับรายชื่อ sub_lo เท่านั้น) จึงไม่ผ่าน _ask_cached
CONSOLIDATE_PROMPT = f"""คุณคือระบบรวบรัดวัตถุประสงค์การเรียนรู้ (sub_lo) ให้เหลือไม่เกิน {MAX_SUB_LOS} ข้อ

หน้าที่ของคุณ:
1. หา "คู่ขนาน" ก่อน — sub_lo สองข้อขึ้นไปที่ใช้ทักษะ/เกณฑ์เดียวกัน แต่แยกไปใช้กับ 2 กลุ่มที่เป็นคู่ตรงข้ามกัน
   (เช่น "อธิบาย X ของคำเป็น" แยกจาก "อธิบาย X ของคำตาย") หรือเนื้อหาซ้ำกันเกือบทั้งหมด → รวมเป็นข้อเดียว
   ที่จำแนก/เปรียบเทียบทั้งสองฝั่งพร้อมกัน
2. รวมคู่ขนานแล้วนับดูว่าเหลือกี่ข้อ — ถ้ายังเกิน {MAX_SUB_LOS} ข้อ ให้รวมหัวข้อที่เนื้อหาใกล้เคียง/
   ต่อเนื่องกันมากที่สุดเพิ่มเติม (เลือกคู่ที่สัมพันธ์กันมากสุดก่อน) จนกว่าจะเหลือ **ไม่เกิน {MAX_SUB_LOS} ข้อ**
   นี่คือเป้าหมายที่ต้องทำให้ถึง ไม่ใช่ทางเลือก
3. ห้ามรวมข้อที่เนื้อหาไม่เกี่ยวข้องกันเลยแบบขอไปที — เลือกรวมเฉพาะคู่ที่สัมพันธ์กันจริง
4. ถ้ามี ≤{MAX_SUB_LOS} ข้ออยู่แล้วและไม่มีคู่ขนาน ให้ตอบ merged_groups เป็น [] ว่างเปล่า

output เป็น JSON เท่านั้น ห้ามมี markdown (from_ids รวมได้มากกว่า 2 ข้อในกลุ่มเดียว):
{{"merged_groups": [
  {{"from_ids": ["s2", "s3"], "new_statement": "นักเรียนสามารถ...", "tag": "core", "type": "conceptual"}}
]}}"""

# ── สรุปเนื้อหาเป็นก้อนๆ เมื่อยาวเกิน SYNTH_MAX_CHARS (ดู docs/plan-synthesizer-runlog.md หัวข้อ A2) ──
PART_SUMMARY_PROMPT = """คุณคือระบบสรุปเนื้อหาบทเรียนสำหรับป้อนให้ระบบสร้างวัตถุประสงค์การเรียนรู้ต่อ

สรุปแบบมีโครงสร้าง ไม่ใช่ความเรียง เก็บป้าย [cN] เดิมของแต่ละประเด็นไว้ (อ้างอิงได้) ห้ามเติมเนื้อหาที่ไม่มีในต้นฉบับ
ต่อประเด็น/หัวข้อย่อย ให้ระบุ: chunks (ป้าย [cN] ที่เกี่ยวข้อง), หัวข้อ, แนวคิดหลัก, ตัวอย่าง,
ตัวเลข/ข้อมูลสำคัญ, ความเข้าใจผิดที่พบบ่อย (ถ้ามี), สัญญาณ soft skill (การทดลอง ตัวเลข จริยธรรม กรณีศึกษา ถ้ามี)
— สิ่งเหล่านี้คือสิ่งที่ rubric และการเลือกมุมมองการประเมินต้องใช้ ห้ามตัดทิ้ง

output เป็น JSON เท่านั้น ห้ามมี markdown:
{"topics": [{"chunks": ["c1", "c2"], "heading": "...", "key_concepts": "...", "examples": "...",
"data": "...", "misconceptions": "...", "soft_signals": "..."}]}"""


def split_into_parts(chunks: list[str], part_chars: int) -> list[list[tuple[int, str]]]:
    """แบ่ง chunks (เรียงตามลำดับเอกสาร) เป็นก้อนๆ ตัดที่รอยต่อ chunk เท่านั้น (ไม่ตัดกลาง chunk)
    คืนแต่ละก้อนเป็น list ของ (เลขป้าย [cN] เริ่มจาก 1, เนื้อหา chunk) — pure function ไม่มี I/O"""
    parts: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    size = 0
    for i, c in enumerate(chunks, start=1):
        if current and size + len(c) > part_chars:
            parts.append(current)
            current, size = [], 0
        current.append((i, c))
        size += len(c)
    if current:
        parts.append(current)
    return parts


class Synthesizer:
    def __init__(self, client: OpenAI, cost_tracker=None):
        self.client        = client
        self.model         = MODEL_SYNTHESIZER
        self.cost_tracker  = cost_tracker
        self._extra        = {}
        self._cache_written = False

    def _log(self, step: str, event: str, detail: str = ""):
        if self.cost_tracker is not None and self.cost_tracker.run_log is not None:
            self.cost_tracker.run_log.event(step, event, detail=detail)

    def _ask_cached(self, step: str, content_text: str, instruction_text: str) -> dict | None:
        """เรียก LLM หนึ่งขั้น โดยส่ง content_text เป็น block แรกพร้อม cache_control (เขียน/อ่าน cache
        ตาม byte เดียวกันทุกขั้นของ synthesize() ครั้งนี้) ตามด้วย instruction_text ของขั้นนั้น (ไม่ cache
        เพราะเปลี่ยนทุกขั้น) — ขั้นเสริมล้มเหลวคืน None ไม่ทำให้ทั้ง setup พัง"""
        messages = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": content_text,
                     "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": instruction_text},
                ],
            },
            {"role": "user", "content": "ทำงานตามคำสั่งข้างบนจากเนื้อหาที่แนบมา"},
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=messages, extra_body=self._extra,
            )
        except Exception as e:
            print(f"  ⚠️  {step} ไม่สำเร็จ ({e})")
            self._log(step, "error", str(e)[:200])
            return None

        if self.cost_tracker is not None and getattr(response, "usage", None):
            self.cost_tracker.track_synthesizer(response.usage, step=step)
            details = getattr(response.usage, "prompt_tokens_details", None)
            cached  = (getattr(details, "cached_tokens", 0) or 0) if details else 0
            if self._cache_written and cached == 0:
                print(f"     ⚠️  cache miss ที่ขั้น {step} (cached_tokens=0)")
            self._cache_written = True

        try:
            return parse_json(response.choices[0].message.content or "")
        except json.JSONDecodeError:
            print(f"  ⚠️  parse ผล{step}ไม่ได้")
            self._log(step, "parse_error", (response.choices[0].message.content or "")[:200])
            return None

    def _summarize_part(self, part_text: str, i: int, n: int) -> str:
        print(f"     สรุปก้อน {i}/{n}...")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": PART_SUMMARY_PROMPT},
                    {"role": "user",   "content": part_text},
                ],
                extra_body=self._extra,
            )
        except Exception as e:
            print(f"     ⚠️  สรุปก้อน {i} ไม่สำเร็จ ({e}) — ใช้เนื้อหาดิบของก้อนนี้แทน")
            self._log(f"summarize_part_{i}", "error", str(e)[:200])
            return part_text

        if self.cost_tracker is not None and getattr(response, "usage", None):
            self.cost_tracker.track_synthesizer(response.usage, step=f"summarize_part_{i}")

        return response.choices[0].message.content or part_text

    def _read_content(self, lesson_path: str) -> tuple[str, list[str]]:
        """คืน (content_text ที่จะให้ LLM อ่านในขั้น 1/2/4, chunks ดิบ) — content_text คือเนื้อหาเต็ม
        ติดป้าย [cN] ตามลำดับเอกสาร หรือสรุปเป็นก้อนๆ ถ้ายาวเกิน SYNTH_MAX_CHARS"""
        labeled, chunks = lesson_full_text(lesson_path, self.client, self.cost_tracker)
        if not chunks:
            raise ValueError(
                "ไม่มีเนื้อหาในโฟลเดอร์บทเรียน — สังเคราะห์วัตถุประสงค์ไม่ได้ "
                "(ตรวจสอบว่าไฟล์ถูกแปลงเป็นข้อความแล้วหรือไม่)"
            )

        if len(labeled) <= SYNTH_MAX_CHARS:
            self._log("read_content", "info", f"{len(chunks)} chunks, {len(labeled)} ตัวอักษร, โหมดเต็ม")
            return labeled, chunks

        print(f"  📚 เนื้อหายาว {len(labeled)} ตัวอักษร เกิน {SYNTH_MAX_CHARS} — สรุปเป็นก้อนก่อน...")
        self._log("read_content", "info",
                  f"{len(chunks)} chunks, {len(labeled)} ตัวอักษร เกิน SYNTH_MAX_CHARS → โหมดสรุปเป็นก้อน")
        parts = split_into_parts(chunks, SYNTH_PART_CHARS)
        summaries = []
        for i, part_chunks in enumerate(parts, start=1):
            part_text = "\n\n".join(f"[c{ci}] {c}" for ci, c in part_chunks)
            summaries.append(self._summarize_part(part_text, i, len(parts)))
        content = "\n\n---\n\n".join(summaries)

        summary_path = os.path.join(lesson_path, "summary.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump({"parts": len(parts), "summaries": summaries}, f, ensure_ascii=False, indent=2)
        print(f"     บันทึกสรุปไว้ที่ {summary_path} (ตรวจสอบได้)")

        return content, chunks

    def _select_groups(self, content: str) -> dict:
        """เลือกมุมมองการประเมิน P01–P18 ไม่เกิน 2 กลุ่ม (เขียน cache ของ content ในขั้นนี้)"""
        print("  🧭 เลือกมุมมองการประเมิน...")
        result = self._ask_cached(
            "select_groups", content, SELECT_GROUPS_PROMPT.format(index=tpl.lo_index())
        ) or {}
        valid  = set(tpl.lo_group_ids())
        groups = [g for g in result.get("prompt_groups", []) if g in valid][:2]
        print(f"     → {', '.join(groups) or '(ไม่เข้ากลุ่มใด ใช้กฎหลักอย่างเดียว)'}")
        self._log("select_groups", "result",
                  f"groups={groups} reason={result.get('group_reason', '')}")
        return {
            "prompt_groups":  groups,
            "group_reason":   result.get("group_reason", ""),
            "dropped_groups": result.get("dropped_groups", []),
        }

    @staticmethod
    def _templates_text(groups: list[str]) -> str:
        return "\n\n---\n\n".join(tpl.lo_template(g) for g in groups)

    def _build_lo_and_rubric(self, content: str, groups: list[str]) -> dict | None:
        """ขั้น 2: main_lo + sub_los + rubric ต่อข้อ พร้อมกันในขั้นเดียว (อ่าน cache ของ content)"""
        print("  📋 สร้างวัตถุประสงค์ + rubric...")
        instruction = SYNTH_LO_RUBRIC_PROMPT
        if groups:
            instruction += LO_TEMPLATE_SECTION.format(templates=self._templates_text(groups))
        result = self._ask_cached("lo_rubric", content, instruction)
        if result is not None:
            self._log("lo_rubric", "result", f"{len(result.get('sub_los', []))} sub_los")
        return result

    def _add_hard_rubrics_for(self, objectives: dict, groups: list[str], content: str,
                              ids: list[str]) -> None:
        """สร้าง rubric ใหม่เฉพาะ sub_lo ที่ถูกรวมหลัง consolidate — rubric เดิมของแต่ละข้อที่ถูกรวม
        ใช้ไม่ได้แล้วเพราะ statement เปลี่ยน (อ่าน cache ของ content เดิม ไม่ต้องอ่านเนื้อหาซ้ำเต็มราคา)"""
        sub_los = objectives.get("sub_los", [])
        targets = [lo for lo in sub_los if lo["id"] in ids]
        if not targets:
            return
        print(f"  📏 สร้าง rubric ใหม่หลังรวม sub_lo: {', '.join(ids)}")
        listing = [
            {k: lo.get(k) for k in ("id", "statement", "type", "prompt_group", "content_type")}
            for lo in targets
        ]
        templates = self._templates_text(groups)
        instruction = HARD_RUBRIC_PROMPT.format(
            templates=f"template ของมุมมองที่ใช้:\n\n{templates}" if templates else ""
        ) + (
            f"\n\nmain_lo: {objectives.get('main_lo', '')}\n\n"
            f"sub_los:\n{json.dumps(listing, ensure_ascii=False, indent=2)}"
        )
        result = self._ask_cached("rubric_remerge", content, instruction) or {}

        by_id = {r.get("id"): r for r in result.get("rubrics", [])}
        for lo in targets:
            r = by_id.get(lo["id"])
            if not r:
                continue
            for key in ("observable_evidence", "mentor_activity", "rubric"):
                if r.get(key):
                    lo[key] = r[key]
        still_missing = [lo["id"] for lo in targets if not lo.get("rubric")]
        if still_missing:
            print(f"     ⚠️  ยังไม่มี rubric: {', '.join(still_missing)} (Observer จะใช้เกณฑ์กลางแทน)")
            self._log("rubric_remerge", "warn", f"ยังไม่มี rubric: {still_missing}")

    def _build_softskills(self, content: str, objectives: dict) -> list[dict]:
        """ขั้น 4: สร้าง lesson_indicators ครบทุกสกิล S01–S12 (อ่าน cache ของ content)"""
        print("  🧠 สร้างตัวบ่งชี้ soft skill ครบทุกสกิล...")
        skills  = tpl.softskills()
        sub_los = [
            {k: lo.get(k) for k in ("id", "statement", "type", "mentor_activity")}
            for lo in objectives.get("sub_los", [])
        ]
        instruction = SOFT_INDICATOR_PROMPT.format(
            index=tpl.softskill_index(),
            skills="\n\n---\n\n".join(s["text"] for s in skills.values()),
            skill_ids=", ".join(skills),
            example=tpl.section(tpl.softskill_selection_prompt(), "ตัวอย่าง lesson_indicators"),
        ) + (
            f"\n\nmain_lo: {objectives.get('main_lo', '')}\n\n"
            f"sub_los:\n{json.dumps(sub_los, ensure_ascii=False, indent=2)}"
        )
        result = self._ask_cached("softskills", content, instruction) or {}

        by_id   = {s.get("id"): s for s in result.get("softskills", [])}
        ordered = [by_id[sid] for sid in skills if sid in by_id]
        missing = [sid for sid in skills if sid not in by_id]
        if missing:
            print(f"     ⚠️  ไม่ได้ตัวบ่งชี้ของ {', '.join(missing)}")
            self._log("softskills", "warn", f"ไม่ได้ตัวบ่งชี้ของ {missing}")
        print(f"     → {len(ordered)}/{len(skills)} สกิล")
        return ordered

    def _consolidate(self, objectives: dict) -> dict:
        """ขั้น 3: หาคู่ขนาน/ซ้ำซ้อน แล้วรวมให้เหลือไม่เกิน MAX_SUB_LOS ข้อ (deterministic — ไม่พึ่งว่า
        ขั้น 2 จะทำตามกฎเรื่องจำนวน/คู่ขนานเองได้ครบ) ทำงานกับรายชื่อ sub_lo เท่านั้น ไม่ต้องอ่านเนื้อหาเต็ม"""
        subs = objectives.get("sub_los", [])
        if len(subs) < 2:
            return objectives

        listing = "\n".join(f"{lo['id']}: {lo['statement']}" for lo in subs)
        prompt_user = f"ตอนนี้มี {len(subs)} ข้อ ต้องเหลือไม่เกิน {MAX_SUB_LOS} ข้อ:\n\n{listing}"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": CONSOLIDATE_PROMPT},
                    {"role": "user",   "content": prompt_user},
                ],
                extra_body=self._extra,
            )
        except Exception as e:
            print(f"  ⚠️  ตรวจสอบความซ้ำซ้อนไม่สำเร็จ ({e}) ข้ามขั้นนี้")
            self._log("consolidate", "error", str(e)[:200])
            groups = []
        else:
            if self.cost_tracker is not None and getattr(response, "usage", None):
                self.cost_tracker.track_synthesizer(response.usage, step="consolidate")

            try:
                groups = parse_json(response.choices[0].message.content).get("merged_groups", [])
            except json.JSONDecodeError:
                print("  ⚠️  parse ผลตรวจความซ้ำซ้อนไม่ได้ ข้ามขั้นนี้")
                groups = []

        by_id     = {lo["id"]: lo for lo in subs}
        order     = {lo["id"]: i for i, lo in enumerate(subs)}
        merged_id = set()
        new_subs  = []

        for g in groups:
            ids = [i for i in g.get("from_ids", []) if i in by_id and i not in merged_id]
            if len(ids) < 2:
                continue
            merged_id.update(ids)
            evidence = []
            for i in ids:
                evidence.extend(by_id[i].get("evidence_chunks", []))
            new_subs.append({
                "id":              ids[0],
                "statement":       g.get("new_statement") or by_id[ids[0]]["statement"],
                "tag":             g.get("tag") or (
                    "core" if any(by_id[i].get("tag") == "core" for i in ids) else "supporting"
                ),
                "type":            g.get("type") or by_id[ids[0]].get("type", "conceptual"),
                "evidence_chunks": sorted(set(evidence)),
                "prompt_group":    by_id[ids[0]].get("prompt_group"),
                "content_type":    by_id[ids[0]].get("content_type"),
            })
            print(f"  🔀 รวม {', '.join(ids)} → {new_subs[-1]['statement']}")
            self._log("consolidate", "merge", f"{', '.join(ids)} → {new_subs[-1]['statement']}")

        for lo in subs:
            if lo["id"] not in merged_id:
                new_subs.append(lo)

        if not groups:
            print("  ไม่พบ sub_lo ที่ซ้ำซ้อน" if len(subs) <= MAX_SUB_LOS
                  else "  ⚠️  โมเดลไม่ได้รวมให้ตามที่ขอ จะรวมแบบ mechanical แทน")

        # เรียงกลับตามลำดับเดิม (ใช้ตำแหน่ง id แรกสุดของแต่ละก้อน)
        new_subs.sort(key=lambda lo: order.get(lo["id"], len(subs)))

        # การันตีเพดาน: ถ้า pass 2 (LLM) รวมไม่พอ ให้รวมแบบ mechanical ต่อจนกว่าจะไม่เกิน
        # MAX_SUB_LOS จริงๆ — กันกรณีโมเดลไม่ทำตามจำนวนที่ขอ (เจอมาแล้วว่าไว้ใจอย่างเดียวไม่พอ)
        while len(new_subs) > MAX_SUB_LOS:
            a, b = new_subs[-2], new_subs[-1]
            new_subs[-2:] = [{
                "id":              a["id"],
                "statement":       f"{a['statement']} รวมถึง{b['statement']}",
                "tag":             "core" if (a.get("tag") == "core" or b.get("tag") == "core")
                                    else "supporting",
                "type":            a.get("type", "conceptual"),
                "evidence_chunks": sorted(set(
                    a.get("evidence_chunks", []) + b.get("evidence_chunks", [])
                )),
                "prompt_group":    a.get("prompt_group"),
                "content_type":    a.get("content_type"),
            }]

        for i, lo in enumerate(new_subs, start=1):
            lo["id"] = f"s{i}"

        objectives["sub_los"] = new_subs
        return objectives

    def _run_hallucination_checks(self, objectives: dict) -> None:
        """ตรวจด้วยโค้ด (ไม่ใช้ LLM) แล้ว log คำเตือนถ้าไม่ผ่าน — ไม่บล็อกการบันทึกไฟล์"""
        sub_los = objectives.get("sub_los", [])
        if len(sub_los) > MAX_SUB_LOS:
            msg = f"sub_los เกินเพดาน: {len(sub_los)} > {MAX_SUB_LOS}"
            print(f"     ⚠️  {msg}")
            self._log("check", "warn", msg)

        for lo in sub_los:
            rubric = lo.get("rubric") or {}
            missing = [lv for lv in ("0", "1", "2", "3") if not rubric.get(lv)]
            if missing:
                self._log("check", "warn", f"{lo['id']} rubric ขาดระดับ {missing}")

        skills   = objectives.get("softskills", [])
        ids      = {s.get("id") for s in skills}
        expected = set(tpl.softskills())
        if ids != expected:
            self._log("check", "warn", f"soft skill ไม่ครบ: ขาด {sorted(expected - ids)}")
        for sk in skills:
            indicators = sk.get("lesson_indicators", {})
            missing = [lv for lv in ("1", "2", "3", "4", "5") if not indicators.get(lv)]
            if missing:
                self._log("check", "warn", f"{sk.get('id')} lesson_indicators ขาดระดับ {missing}")

    def synthesize(self, lesson_path: str) -> dict:
        print("  กำลังสังเคราะห์วัตถุประสงค์...")
        # sticky routing: ทุกขั้นของ synthesize() นี้ต้องไปตกที่ provider เดียวกันเพื่อ hit prompt cache
        self._extra = {"session_id": f"synth-{hashlib.sha256(lesson_path.encode()).hexdigest()[:12]}"}
        self._cache_written = False

        content, chunks = self._read_content(lesson_path)
        valid_chunk_ids = {f"c{i}" for i in range(1, len(chunks) + 1)}

        # ขั้น 1: เลือกมุมมองการประเมิน (เขียน cache ของ content)
        selection = self._select_groups(content)
        groups    = selection["prompt_groups"]

        # ขั้น 2: LO + rubric พร้อมกัน (อ่าน cache ของ content)
        objectives = self._build_lo_and_rubric(content, groups)
        if objectives is None:
            objectives = {"error": "สร้างวัตถุประสงค์ไม่สำเร็จ (LLM error หรือ parse JSON ไม่ได้)"}
            output_path = os.path.join(lesson_path, "objectives.json")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(objectives, f, ensure_ascii=False, indent=2)
            return objectives

        for lo in objectives.get("sub_los", []):
            if lo.get("type") not in ("conceptual", "factual"):
                lo["type"] = "conceptual"
            if lo.get("prompt_group") not in groups:
                lo["prompt_group"] = groups[0] if len(groups) == 1 else None
            # กันหลอน: evidence_chunks ต้องอ้างป้ายที่มีอยู่จริงในเนื้อหาเท่านั้น
            bad = [c for c in lo.get("evidence_chunks", []) if c not in valid_chunk_ids]
            if bad:
                print(f"     ⚠️  {lo.get('id')}: evidence_chunks อ้างป้ายที่ไม่มีจริง {bad}")
                self._log("check", "warn", f"{lo.get('id')} evidence_chunks ไม่มีจริง: {bad}")
                lo["evidence_chunks"] = [c for c in lo.get("evidence_chunks", []) if c in valid_chunk_ids]

        if "sub_los" in objectives:
            # ขั้น 3: consolidate (ไม่ต้องอ่านเนื้อหาเต็ม)
            objectives = self._consolidate(objectives)

            # sub_lo ที่ถูกรวมจะไม่มี key "rubric" เลย (dict ใหม่ที่ _consolidate สร้าง ไม่ได้พก rubric
            # เดิมมาด้วย เพราะ statement เปลี่ยนแล้วใช้ต่อไม่ได้) → สร้าง rubric ใหม่เฉพาะข้อพวกนี้
            needs_rubric = [lo["id"] for lo in objectives["sub_los"] if not lo.get("rubric")]
            if needs_rubric:
                self._add_hard_rubrics_for(objectives, groups, content, needs_rubric)

            # ขั้น 4: soft skills (อ่าน cache ของ content)
            objectives["softskills"]     = self._build_softskills(content, objectives)
            objectives["prompt_groups"]  = groups
            objectives["group_reason"]   = selection["group_reason"]
            objectives["schema_version"] = SCHEMA_VERSION
            objectives.setdefault("missing_coverage", []).extend(selection["dropped_groups"])

            self._run_hallucination_checks(objectives)

        output_path = os.path.join(lesson_path, "objectives.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(objectives, f, ensure_ascii=False, indent=2)

        print(f"  บันทึก objectives.json แล้ว ✅")
        return objectives
