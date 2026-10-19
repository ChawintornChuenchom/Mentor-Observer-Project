import hashlib
import json
import os
import re
from openai import OpenAI
from config import (MODEL_SYNTHESIZER, SYNTH_MAX_CHARS, SYNTH_PART_CHARS, SYNTH_TEMPERATURE,
                    SYNTH_REASONING_EFFORT)
from json_utils import parse_json
from rag import lesson_full_text
import template_loader as tpl

MAX_MAIN_LOS    = 6   # เพดานตายตัว — ทุกบทเรียนต้องไม่เกินนี้ ไม่ว่าเนื้อหาจะยาว/ซับซ้อนแค่ไหน
MAX_SOFTSKILLS = 5   # ขั้น 4A เลือกได้ 0–5 สกิล ไม่มีขั้นต่ำ
FORBIDDEN_INDICATOR_PHRASES = ("ตอบถูก", "คำนวณถูก", "ทำตามขั้นตอนได้")

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
ใช้ป้ายเหล่านี้อ้างอิงใน source_chunks ตรงๆ ห้ามสร้างป้ายใหม่หรือเดาป้ายที่ไม่มีในเนื้อหา
source_chunks ของแต่ละ main_lo ต้องชี้ช่วงที่เนื้อหาของ main_lo นั้นอยู่จริง (ครบทุกช่วงที่เกี่ยวข้อง)
เพราะขั้นสร้างตัวบ่งชี้ soft skill จะดึงข้อความของป้ายเหล่านี้ไปใช้แทนเนื้อหาทั้งบท

งาน:
1. สังเคราะห์ main_los: แต่ละข้อต้องวัดได้จากบทสนทนา ไม่ซ้ำซ้อน
2. ระบุ missing_coverage: เนื้อหาที่วัดจากบทสนทนาไม่ได้
3. ต่อ main_lo ทุกข้อ เขียนเกณฑ์ประเมิน (observable_evidence, mentor_activity, rubric) ตามหัวข้อด้านล่าง

กฎสำคัญ (การสร้าง main_lo):
- สร้าง main_los จากเนื้อหาที่แนบมาโดยตรง (ยังไม่ต้องเขียนสรุปวัตถุประสงค์ของบท ระบบจะสรุปจาก main_los ภายหลัง)
  ใช้ tag บอกความสำคัญ: core = สาระหลักที่บทเรียนต้องการให้ทำได้, supporting = เสริม
- ใช้คำกริยาที่วัดได้: อธิบาย แก้ แยก วิเคราะห์ ยกตัวอย่าง — ห้ามใช้: เข้าใจ รู้ เรียนรู้
- tag: core = ต้องผ่านทุกข้อ, supporting = เสริม
- สร้าง main_los **ไม่เกิน {MAX_MAIN_LOS} ข้อเด็ดขาด ไม่ว่าเนื้อหาจะยาวหรือซับซ้อนแค่ไหน**
  (เนื้อหาน้อยจะได้แค่ 2-3 ข้อก็ได้) ถ้าเนื้อหามีหัวข้อย่อยมากกว่า {MAX_MAIN_LOS} เรื่อง ให้ยุบรวมหัวข้อ
  ที่ใกล้เคียง/ต่อเนื่องกันเข้าเป็นข้อเดียว แทนที่จะแตกเป็นหลายข้อ — 1 ข้อครอบคลุมกว้างดีกว่าแตกแคบๆ หลายข้อ
  ⚠️ ห้ามฝืนแตกหัวข้อให้ครบจำนวนใดๆ ถ้าเนื้อหาน้อย — 2 ข้อที่ดีดีกว่า {MAX_MAIN_LOS} ข้อที่ซ้ำซ้อน
- ⚠️ ระวังวัตถุประสงค์ "คู่ขนาน" — ถ้าเจอ main_lo 2 ข้อที่ใช้ทักษะ/เกณฑ์เดียวกัน แต่แยกไปใช้กับ
  2 กลุ่มที่เป็นคู่ตรงข้ามกัน ห้ามแยกเป็น 2 ข้อ ให้รวมเป็นข้อเดียวที่จำแนก/เปรียบเทียบทั้งสองฝั่งพร้อมกัน
  ตัวอย่างผิด: "ระบุตัวสะกดที่ทำให้เป็นคำตายได้" แยกจาก "ระบุตัวสะกดที่ทำให้เป็นคำเป็นได้"
  ตัวอย่างถูก: "จำแนกมาตราตัวสะกดที่ทำให้พยางค์เป็นคำเป็นหรือคำตายได้" (ข้อเดียว ครอบคลุมทั้งคู่)
- เรียง main_los ตามลำดับการสอนจริง: s1 = พื้นฐานสุด → กลางๆ (ความเข้าใจ) → ข้อท้ายๆ = ซับซ้อนสุด

ทุก main_lo ต้องมี field "type":
- "conceptual" = วัดการเข้าใจ / วิเคราะห์ / เปรียบเทียบ / ประยุกต์แนวคิด
    ห้ามเป็นแค่การจำโครงสร้างเอกสาร (เช่น "มีกี่หน่วย" "ชื่อหน่วยคืออะไร") เพราะนั่นคือ recall ไม่ใช่ critical thinking
- "factual" = ข้อมูลเชิงข้อเท็จจริง / โครงสร้างเอกสาร / นิยามเฉพาะ / ตัวเลข-ชื่อ ที่ต้องจำตรงตัว เถียงไม่ได้
แนวทาง: main_lo ส่วนใหญ่ควรเป็น "conceptual" ให้ "factual" เฉพาะข้อที่เป็นข้อเท็จจริง/โครงสร้างล้วนๆ

เกณฑ์ประเมิน — ต่อ main_lo ทุกข้อ:
ความหมายของระดับคงที่ ห้ามเปลี่ยน — 3 = ผ่าน อธิบายหรือแก้โจทย์ได้ด้วยตัวเองชัดเจน,
2 = ใกล้ผ่าน เข้าใจแต่ยังต้องการความช่วยเหลือบ้าง, 1 = ยังไม่ผ่าน มีหลักฐานว่าเข้าใจบ้างแต่ยังไม่พอ,
0 = มีความเข้าใจผิดที่สำคัญ
- observable_evidence: สิ่งที่นักเรียนต้องพูด/ทำให้เห็นในแชท จึงจะนับเป็นหลักฐาน
- mentor_activity: กิจกรรม/คำถามที่ Mentor ใช้เปิดโอกาสให้นักเรียนแสดงหลักฐานนั้น
- rubric: 1 ประโยคต่อระดับ บอกว่า "ระดับนี้ของ main_lo ข้อนี้ นักเรียนพูดออกมาหน้าตาเป็นอย่างไร"
  เจาะจงเนื้อหาของ main_lo ข้อนั้น ห้ามเขียนกว้างๆ ที่ใช้ได้กับทุกข้อ
  ระดับ 0 ให้ระบุความเข้าใจผิดที่พบบ่อยของเนื้อหานี้

output เป็น JSON เท่านั้น ห้ามมี markdown:
{{
  "lesson_title": "ชื่อบทเรียน",
  "main_los": [
    {{
      "id": "s1",
      "statement": "นักเรียนสามารถ...",
      "tag": "core",
      "type": "conceptual",
      "source_chunks": ["c1", "c3"],
      "prompt_group": "<id มุมมองที่เลือก>",
      "content_type": "<ป้ายจากหัวข้อ 'ป้าย' ของ template นั้น>",
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
มุมมองการประเมินที่เลือกไว้สำหรับบทเรียนนี้ — ใช้ "เน้นดูอะไร", "ป้าย", "Main LO", "ไม่ตั้งเป็น Main LO"
ของ template ประกอบการสร้าง main_los (ถ้าขัดกับกฎสำคัญข้างบน ให้ยึดกฎข้างบน):
- prompt_group = id ของมุมมองที่ Main LO นั้นมาจาก
- content_type = ป้ายของ template ที่ตรงกับ Main LO นั้น
- สร้าง Main LO เฉพาะป้ายที่พบจริงในเนื้อหา ห้ามสร้างเพื่อให้ครบทุกป้าย

{templates}"""

# ── ใช้สร้าง rubric ใหม่เฉพาะ main_lo ที่ถูกรวมหลัง consolidate (rubric เดิมใช้ไม่ได้แล้ว) ──
HARD_RUBRIC_PROMPT = """คุณคือระบบสร้างเกณฑ์ประเมิน Hard Skill ต่อ Main LO สำหรับ AI-Observer

ความหมายของระดับคงที่ ห้ามเปลี่ยน:
- 3 = ผ่าน อธิบายหรือแก้โจทย์ได้ด้วยตัวเองชัดเจน
- 2 = ใกล้ผ่าน เข้าใจแต่ยังต้องการความช่วยเหลือบ้าง
- 1 = ยังไม่ผ่าน มีหลักฐานว่าเข้าใจบ้างแต่ยังไม่พอ
- 0 = มีความเข้าใจผิดที่สำคัญ

ทุก Main LO ให้เขียน:
- observable_evidence: สิ่งที่นักเรียนต้องพูด/ทำให้เห็นในแชท จึงจะนับเป็นหลักฐาน
- mentor_activity: กิจกรรม/คำถามที่ Mentor ใช้เปิดโอกาสให้นักเรียนแสดงหลักฐานนั้น
- rubric: 1 ประโยคต่อระดับ บอกว่า "ระดับนี้ของ Main LO ข้อนี้ นักเรียนพูดออกมาหน้าตาเป็นอย่างไร"
  เจาะจงเนื้อหาของ Main LO ข้อนั้น ห้ามเขียนกว้างๆ ที่ใช้ได้กับทุกข้อ
  ระดับ 0 ให้ระบุความเข้าใจผิดที่พบบ่อยของเนื้อหานี้

{templates}

output เป็น JSON เท่านั้น ห้ามมี markdown — ครบทุก Main LO ที่ได้รับ:
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

# ── ขั้น 4A: เลือก soft skill จากวัตถุประสงค์ของบท (ไม่ส่งเนื้อหาบทเรียน) ──
SOFT_SELECT_PROMPT = """คุณคือระบบเลือก Soft Skill ที่ประเมินได้จากวัตถุประสงค์ของบทเรียน

{index}

---
summary: {summary}

main_los:
{main_los}

---
งาน: ตัดสินทีละสกิล S01–S12 ว่าเลือกหรือไม่ ตาม "วิธีเลือก" ข้างบน
- ตัดสินจาก summary และ main_los ข้างบนเท่านั้น
- linked_main_los: main_lo id ที่เปิดโอกาสให้แสดงสกิลนี้ (อย่างน้อย 1)
- reason (สกิลที่เลือก): 1 ประโยค บอกว่า main_lo ข้อไหนให้นักเรียนทำอะไร ที่ตรงกับ "ประเมินได้เมื่อ" ของสกิลนี้ ต้องอ้าง main_lo id
- required_activity: main_lo = id ที่จะปรับกิจกรรม (ต้องอยู่ใน linked_main_los) · how = 1–2 ประโยค บอกว่า Mentor ปรับวิธีทำ mentor_activity เดิมอย่างไร กิจกรรมต้องยังทำให้ main_lo นั้นบรรลุ ห้ามเพิ่มหัวข้อหรือข้อมูลที่ไม่มีในบทเรียน
- reason (สกิลที่ไม่เลือก): 1 ประโยค บอกว่า main_los ขาดอะไร
- ทุกสกิล S01–S12 ต้องอยู่ใน softskills หรือ softskills_not_selected อย่างใดอย่างหนึ่ง ครั้งเดียว
- softskills มีได้ 0–5 สกิล

ตัวอย่าง required_activity (บทอาณาจักรธนบุรี — ใช้ดูรูปแบบเท่านั้น):
ดี — S01, main_lo s2 (วิเคราะห์เหตุผลที่เลือกธนบุรีเป็นราชธานี):
  "แทนที่จะถามตรงๆ ว่าทำไมเลือกธนบุรี ให้ Mentor เสนอข้อสรุป 'ธนบุรีถูกเลือกเพราะใกล้ทะเลอย่างเดียว' แล้วให้นักเรียนวิจารณ์ว่าขาดเหตุผลใด"
  (ยังสอน s2 อยู่ แค่จัดรูปให้นักเรียนได้วิจารณ์)
ไม่ดี — S06, main_lo s1 (ลำดับเหตุการณ์):
  "ให้แหล่งข้อมูลสองแหล่งที่ระบุปีต่างกันให้นักเรียนประเมิน"
  (บทเรียนไม่มีแหล่งข้อมูลที่สอง Mentor ต้องแต่งขึ้นเอง → ห้ามเลือก S06)

output เป็น JSON เท่านั้น ห้ามมี markdown:
{{
  "softskills": [
    {{
      "id": "S01",
      "linked_main_los": ["s2"],
      "reason": "...",
      "required_activity": {{"main_lo": "s2", "how": "..."}}
    }}
  ],
  "softskills_not_selected": [
    {{"id": "S06", "reason": "..."}}
  ]
}}"""

# ── ขั้น 4B: เขียนตัวบ่งชี้ของสกิลที่เลือก ทีละสกิล จาก rubric ของสกิลนั้น + เนื้อหาตาม source_chunks ──
SOFT_INDICATOR_PROMPT = """คุณคือระบบเขียนตัวบ่งชี้ Soft Skill ของบทเรียน

สกิล:
{skill_text}

---
main_los ที่เชื่อมกับสกิลนี้:
{linked}

กิจกรรมที่ Mentor จะใช้: {activity}

เนื้อหาบทเรียนที่เกี่ยวข้อง:
{chunks}

---
งาน: เขียน lesson_indicators ระดับ 1–5 ระดับละ 1 ประโยค บอกว่า "ระดับนี้ของสกิลนี้ ในบทเรียนนี้ นักเรียนพูดหรือทำอะไร"
- แต่ละระดับคือ rubric ระดับเดียวกันของสกิลนี้ ยกตัวอย่างด้วยเนื้อหาของบทเรียนนี้
  ห้ามเปลี่ยนความหมาย ห้ามเพิ่มเงื่อนไขใหม่ ห้ามทำให้ยากหรือง่ายกว่า rubric
- ใช้เนื้อหาจาก "เนื้อหาบทเรียนที่เกี่ยวข้อง" เท่านั้น ห้ามอ้างข้อมูลที่ไม่มีในนั้น
- วัดพฤติกรรมของสกิล ไม่ใช่ความถูกต้องของเนื้อหา — เนื้อหาเป็นแค่บริบท
  ห้ามใช้ "ตอบถูก" "คำนวณถูก" "ทำตามขั้นตอนได้" "รู้ว่า…" "อธิบายได้ว่า…(เนื้อหา)" เป็นตัวบ่งชี้
- พฤติกรรมทุกระดับต้องเกิดได้ในกิจกรรมของ Mentor ข้างบน
- ถ้าเขียนระดับ 3 ที่ผูกกับ main_los และเนื้อหานี้ไม่ได้ ให้ตอบ feasible: false พร้อม reason

ตัวอย่างรูปแบบ — S02 ในบท "สมการเชิงเส้นตัวแปรเดียว"
{example}

ตัวอย่างการเทียบกับ rubric — S01 ระดับ 2 (rubric: "ตั้งข้อสงสัยได้เมื่อถูกกระตุ้น แต่ไม่ให้เหตุผล")
ดี: "เมื่อ Mentor ถามว่าข้อสรุปนี้ครบไหม จึงบอกว่าไม่ครบ แต่บอกไม่ได้ว่าขาดอะไร"
ไม่ดี: "เลือกตอบว่าเหตุผลข้อใดสำคัญที่สุดแต่ไม่อธิบาย" (การเลือกคำตอบไม่ใช่การตั้งข้อสงสัย — ความหมายเพี้ยนจาก rubric)
ไม่ดี: "อธิบายได้ว่าธนบุรีถูกเลือกเพราะใกล้ทะเลและดูแลง่าย" (วัดเนื้อหา = hard skill)

output เป็น JSON เท่านั้น ห้ามมี markdown:
{{"feasible": true, "lesson_indicators": {{"1": "...", "2": "...", "3": "...", "4": "...", "5": "..."}}}}
หรือ
{{"feasible": false, "reason": "..."}}"""

# ขั้น 3 ทำงานเฉพาะเมื่อขั้น 2 ได้ main_lo เกิน MAX_MAIN_LOS — หน้าที่เดียวคือการันตีเพดาน โดยรวมให้น้อยที่สุด
# (เดิมเรียกทุกครั้งที่มี ≥2 ข้อ แล้วโมเดลรวมหัวข้อคนละเรื่องเข้าด้วยกันทั้งที่ไม่เกินเพดาน)
# ไม่ต้องอ่านเนื้อหาเต็มบทซ้ำ (ทำงานกับรายชื่อ main_lo + source_chunks เท่านั้น) จึงไม่ผ่าน _ask_cached
CONSOLIDATE_PROMPT = f"""คุณคือระบบลดจำนวนวัตถุประสงค์การเรียนรู้ (main_lo) ให้ไม่เกิน {MAX_MAIN_LOS} ข้อ โดยรวมให้น้อยที่สุด

ตอนนี้จำนวน main_lo เกินเพดาน ต้องลดลงอย่างน้อยตามจำนวนที่ระบุในข้อความ user
ยิ่งรวมน้อยยิ่งดี — ห้ามรวมเกินจำนวนที่ต้องลด

ลำดับการเลือกคู่ที่จะรวม:
1. คู่ขนาน — สองข้อวัดทักษะเดียวกันกับ "สองฝั่งของเรื่องเดียวกัน" ที่ควรจำแนก/เปรียบเทียบพร้อมกัน
   ตัวอย่างใช่: "ระบุตัวสะกดที่ทำให้เป็นคำตาย" กับ "ระบุตัวสะกดที่ทำให้เป็นคำเป็น"
   ตัวอย่างไม่ใช่: "อธิบายเหตุผลที่เลือกธนบุรีเป็นราชธานี" กับ "อธิบายนโยบายเศรษฐกิจสมัยธนบุรี"
     (ใช้คำกริยาเดียวกัน แต่คนละหัวข้อ — นี่ไม่ใช่คู่ขนาน)
2. ข้อที่เนื้อหาซ้อนกัน — มี source_chunks ร่วมกัน และ statement พูดถึงเรื่องเดียวกัน
3. ถ้ายังไม่พอ จึงรวมข้อที่อยู่ติดกันในลำดับการสอนและสัมพันธ์กันมากที่สุด

ห้ามรวม:
- ข้อที่ source_chunks ไม่ซ้อนกันเลย ยกเว้นไม่มีทางเลือกอื่นให้ลดได้ครบ
- core กับ supporting ถ้ายังมีคู่อื่นให้เลือก

new_statement ต้องวัดได้จากบทสนทนาในข้อเดียว — ถ้ารวมแล้ว statement ต้องไล่หลายหัวข้อ แปลว่าไม่ควรรวมคู่นั้น

output เป็น JSON เท่านั้น ห้ามมี markdown (from_ids รวมได้มากกว่า 2 ข้อในกลุ่มเดียว เรียงกลุ่มจากที่ควรรวมที่สุดก่อน):
{{"merged_groups": [
  {{"from_ids": ["s2", "s3"], "new_statement": "นักเรียนสามารถ...", "tag": "core", "type": "conceptual", "reason": "เหตุผลสั้นๆ ว่าทำไมคู่นี้"}}
]}}"""

# ── ขั้น 3.5: summary สรุปจาก main_los ชุดสุดท้าย (หลัง consolidate) — ไม่ส่งเนื้อหาบท ──
# (ไม่ใช่ f-string และไม่ผ่าน .format จึงใช้ { } เดี่ยวได้)
SUMMARY_PROMPT = """คุณคือระบบเขียนสรุปวัตถุประสงค์ของบทเรียน (summary) 1 ประโยค โดยสรุปจากวัตถุประสงค์หลัก (main_los) ที่ให้มาเท่านั้น

กฎ:
- 1 ประโยค ขึ้นต้นด้วย "นักเรียนสามารถ" ใช้คำกริยาที่วัดได้ (อธิบาย วิเคราะห์ เปรียบเทียบ จำแนก …) ห้ามใช้ เข้าใจ รู้ เรียนรู้
- สรุปสิ่งที่ main_los ข้อ core ร่วมกันวัด · main_los ข้อ supporting ใส่ได้ถ้าไม่ทำให้ประโยคยาวเกิน
- ห้ามพูดถึงเรื่องที่ไม่อยู่ใน main_los
- ห้ามพูดถึงเรื่องที่อยู่ใน missing_coverage (เป็นเรื่องที่บทเรียนไม่มีหรือวัดจากบทสนทนาไม่ได้)
- ห้ามเพิ่มขอบเขตที่กว้างกว่า main_los รวมกัน

output เป็น JSON เท่านั้น ห้ามมี markdown:
{"summary": "นักเรียนสามารถ..."}"""

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


def _label_num(label: str) -> int:
    m = re.fullmatch(r"c(\d+)", str(label))
    return int(m.group(1)) if m else 0


def sort_labels(labels) -> list[str]:
    """ป้าย [cN] ไม่ซ้ำ เรียงตามเลข (ไม่ใช่ตามตัวอักษร ที่จะได้ c10 ก่อน c2)"""
    return sorted(set(labels), key=_label_num)


def merge_main_los(members: list[dict], statement: str, tag, type_) -> tuple[dict, list[str]]:
    """main_lo ใหม่จากการรวม members (เรียงตามลำดับการสอน) — คืน (main_lo, คำเตือน)
    ไม่พก observable_evidence/mentor_activity/rubric มาด้วย (statement เปลี่ยน ต้องสร้างใหม่)"""
    warnings = []
    if tag not in ("core", "supporting"):
        tag = "core" if any(m.get("tag") == "core" for m in members) else "supporting"
    if type_ not in ("conceptual", "factual"):
        type_ = "conceptual" if any(m.get("type") == "conceptual" for m in members) else "factual"

    chunk_sets = [set(m.get("source_chunks", [])) for m in members]
    if not any(a & b for i, a in enumerate(chunk_sets) for b in chunk_sets[i + 1:]):
        warnings.append("รวมคนละส่วนของบท (source_chunks ไม่ซ้อนกัน)")

    merged = {"id": members[0]["id"], "statement": statement, "tag": tag, "type": type_,
              "source_chunks": sort_labels(c for s in chunk_sets for c in s)}
    for key in ("prompt_group", "content_type"):
        values = {m.get(key) for m in members}
        merged[key] = members[0].get(key)
        if len(values) > 1:
            warnings.append(f"{key} ต่างกัน {sorted(map(str, values))} — ใช้ของข้อแรก")
    return merged, warnings


def _replace_members(current: list[dict], members: list[dict], merged: dict) -> list[dict]:
    """แทน members ใน current ด้วย merged ที่ตำแหน่งของ member ตัวแรกสุด (คงลำดับการสอน)"""
    ids, out, placed = {id(m) for m in members}, [], False
    for lo in current:
        if id(lo) not in ids:
            out.append(lo)
        elif not placed:
            out.append(merged)
            placed = True
    return out


def chunks_by_label(chunks: list[str], labels) -> str:
    """ข้อความของป้าย [cN] จาก chunks ดิบ (ป้าย cN = chunks[N-1] ทั้งโหมดเต็มและโหมดสรุปเป็นก้อน
    เพราะการสรุปเก็บป้ายเดิมไว้) — ไม่ซ้ำ เรียงตามเลข ป้ายที่ไม่มีจริงถูกข้าม"""
    out = []
    for label in sort_labels(labels):
        n = _label_num(label)
        if 1 <= n <= len(chunks):
            out.append(f"[{label}] {chunks[n - 1]}")
    return "\n\n".join(out)


def _mentions_id(text: str, main_lo_id: str) -> bool:
    # \b ใช้ไม่ได้กับไทย (อักษรไทยนับเป็น \w) จึงเช็คเฉพาะอักษรละติน/ตัวเลขรอบๆ
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(main_lo_id)}(?!\d)", text or "") is not None


def validate_selection(result: dict | None, main_lo_ids: list[str],
                       skill_ids: list[str]) -> tuple[list[dict], list[dict], list[str]]:
    """ตัวตรวจหลัง 4A (โค้ด ไม่ใช่ LLM) — คืน (selected, not_selected, รายการที่แก้/เตือน)
    ผลลัพธ์การันตีว่า id ของสองฝั่งรวมกันเท่ากับ skill_ids พอดี ฝั่งละครั้งเดียว"""
    log: list[str] = []
    if not isinstance(result, dict):
        log.append("4A ล้มเหลว — ทุกสกิลเข้า not_selected")
        return [], [{"id": sid, "reason": "4A ล้มเหลว"} for sid in skill_ids], log

    known = set(skill_ids)

    def entries(key: str) -> list[dict]:
        out = []
        for item in result.get(key) or []:
            sid = item.get("id") if isinstance(item, dict) else None
            if not isinstance(sid, str) or not re.fullmatch(r"S\d{2}", sid) or sid not in known:
                log.append(f"{key}: ทิ้ง id ที่ไม่รู้จัก {sid!r}")
                continue
            out.append(item)
        return out

    sel_raw, not_raw = entries("softskills"), entries("softskills_not_selected")
    sel_ids = [s["id"] for s in sel_raw]
    in_both = set(sel_ids) & {s["id"] for s in not_raw}

    selected:     list[dict] = []
    not_selected: dict[str, dict] = {}

    def drop(sid: str, reason: str):
        if sid not in not_selected:
            not_selected[sid] = {"id": sid, "reason": reason}

    for s in not_raw:
        drop(s["id"], s.get("reason") or "")

    seen = set()
    sub_set = set(main_lo_ids)
    for s in sel_raw:
        sid = s["id"]
        if sid in seen:
            log.append(f"{sid} ซ้ำใน softskills — เก็บตัวแรก")
            continue
        seen.add(sid)
        if sid in in_both:
            log.append(f"{sid} อยู่ทั้งสองฝั่ง — ถือว่าไม่เลือก")
            continue
        linked = s.get("linked_main_los")
        if not isinstance(linked, list) or not linked or not all(i in sub_set for i in linked):
            log.append(f"{sid} linked_main_los ไม่ถูกต้อง {linked!r} — ย้ายไป not_selected")
            drop(sid, f"linked_main_los ไม่ถูกต้อง: {s.get('reason', '')}")
            continue
        act = s.get("required_activity")
        if (not isinstance(act, dict) or act.get("main_lo") not in linked
                or not str(act.get("how") or "").strip()):
            log.append(f"{sid} required_activity ไม่ถูกต้อง {act!r} — ย้ายไป not_selected")
            drop(sid, f"required_activity ไม่ถูกต้อง: {s.get('reason', '')}")
            continue
        reason = s.get("reason") or ""
        if not reason.strip() or not any(_mentions_id(reason, i) for i in linked):
            log.append(f"{sid} reason ไม่อ้าง main_lo id ที่ link ไว้ (เตือนเท่านั้น)")
        selected.append({
            "id":                sid,
            "linked_main_los":    linked,
            "reason":            reason,
            "required_activity": {"main_lo": act["main_lo"], "how": act["how"].strip()},
        })

    # ตรวจความถูกต้องรายสกิลก่อน แล้วค่อยตัดเพดาน — สกิลที่ผิดรูปไม่ควรกินโควตาของสกิลที่ถูก
    for s in selected[MAX_SOFTSKILLS:]:
        log.append(f"{s['id']} เกิน {MAX_SOFTSKILLS} สกิล — ย้ายไป not_selected")
        drop(s["id"], f"เกิน {MAX_SOFTSKILLS} สกิล")
    selected = selected[:MAX_SOFTSKILLS]

    chosen = {s["id"] for s in selected}
    for sid in skill_ids:
        if sid in chosen:
            not_selected.pop(sid, None)
        elif sid not in not_selected:
            log.append(f"{sid} โมเดลไม่ได้ระบุ — เพิ่มใน not_selected")
            drop(sid, "โมเดลไม่ได้ระบุ")

    return selected, [not_selected[sid] for sid in skill_ids if sid in not_selected], log


def validate_indicator(result: dict | None) -> tuple[str, dict | str | None, list[str]]:
    """ตัวตรวจหลัง 4B ของสกิลเดียว — คืน (status, payload, คำเตือน)
    status: "ok" (payload = lesson_indicators) · "infeasible" (payload = reason) · "invalid" (ควร retry)"""
    if not isinstance(result, dict):
        return "invalid", None, ["parse JSON ไม่ได้"]
    if result.get("feasible") is False:
        return "infeasible", str(result.get("reason") or ""), []
    ind = result.get("lesson_indicators")
    levels = ("1", "2", "3", "4", "5")
    if not isinstance(ind, dict):
        return "invalid", None, ["ไม่มี lesson_indicators"]
    missing = [lv for lv in levels if not isinstance(ind.get(lv), str) or not ind[lv].strip()]
    if missing:
        return "invalid", None, [f"lesson_indicators ขาดระดับ {missing}"]
    warnings = [
        f"ระดับ {lv} มีวลีต้องห้าม '{ph}'"
        for lv in levels for ph in FORBIDDEN_INDICATOR_PHRASES if ph in ind[lv]
    ]
    return "ok", {lv: ind[lv].strip() for lv in levels}, warnings


def _has_key(obj, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(_has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key(v, key) for v in obj)
    return False


def check_before_write(objectives: dict, skill_ids: list[str]) -> None:
    """ตรวจข้อ 5.3 ก่อนเขียน objectives.json — ผิดคือ bug ของโค้ด จึง assert"""
    assert not _has_key(objectives, "evidence_chunks"), "ยังมี key evidence_chunks ใน objectives"
    ids = [s["id"] for s in objectives.get("softskills", [])] + \
          [s["id"] for s in objectives.get("softskills_not_selected", [])]
    assert sorted(ids) == sorted(skill_ids), f"soft skill สองฝั่งรวมกันไม่ครบ/ซ้ำ: {ids}"


class Synthesizer:
    def __init__(self, client: OpenAI, cost_tracker=None):
        self.client        = client
        self.model         = MODEL_SYNTHESIZER
        self.cost_tracker  = cost_tracker
        self._extra        = {}
        self._cache_written = False

    def _log(self, step: str, event: str, detail: str = "", model: str = ""):
        if self.cost_tracker is not None and self.cost_tracker.run_log is not None:
            self.cost_tracker.run_log.event(step, event, detail=detail, model=model)

    def _create(self, step: str, messages: list[dict]):
        """จุดเดียวที่ Synthesizer เรียกโมเดล — temperature=SYNTH_TEMPERATURE ทุกขั้น (+ reasoning effort
        ถ้าตั้งไว้), track ค่าใช้จ่าย และบันทึกชื่อโมเดลที่ provider ใช้จริง + temperature ลง run log
        · ล้มเหลวคืน None (โมเดลที่ไม่รองรับ temperature: OpenRouter ตัดพารามิเตอร์นี้ทิ้งเอง)"""
        extra = dict(self._extra)
        if SYNTH_REASONING_EFFORT:
            extra["reasoning"] = {"effort": SYNTH_REASONING_EFFORT}
        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=messages, temperature=SYNTH_TEMPERATURE,
                extra_body=extra,
            )
        except Exception as e:
            print(f"  ⚠️  {step} ไม่สำเร็จ ({e})")
            self._log(step, "error", str(e)[:200])
            return None

        actual = getattr(response, "model", None) or self.model
        detail = f"temperature={SYNTH_TEMPERATURE}"
        if SYNTH_REASONING_EFFORT:
            detail += f" reasoning_effort={SYNTH_REASONING_EFFORT}"
        self._log(step, "call", detail, model=str(actual))
        if self.cost_tracker is not None and getattr(response, "usage", None):
            self.cost_tracker.track_synthesizer(response.usage, step=step)
        return response

    def _parse(self, step: str, response) -> dict | None:
        raw = response.choices[0].message.content or ""
        try:
            return parse_json(raw)
        except json.JSONDecodeError:
            print(f"  ⚠️  parse ผล{step}ไม่ได้")
            self._log(step, "parse_error", raw[:200])
            return None

    def _ask_cached(self, step: str, content_text: str, instruction_text: str) -> dict | None:
        """เรียก LLM หนึ่งขั้น โดยส่ง content_text เป็น block แรกพร้อม cache_control (เขียน/อ่าน cache
        ตาม byte เดียวกันทุกขั้นของ synthesize() ครั้งนี้) ตามด้วย instruction_text ของขั้นนั้น (ไม่ cache
        เพราะเปลี่ยนทุกขั้น) — ขั้นเสริมล้มเหลวคืน None ไม่ทำให้ทั้ง setup พัง"""
        response = self._create(step, [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": content_text,
                     "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": instruction_text},
                ],
            },
            {"role": "user", "content": "ทำงานตามคำสั่งข้างบนจากเนื้อหาที่แนบมา"},
        ])
        if response is None:
            return None

        if getattr(response, "usage", None):
            details = getattr(response.usage, "prompt_tokens_details", None)
            cached  = (getattr(details, "cached_tokens", 0) or 0) if details else 0
            if self._cache_written and cached == 0:
                print(f"     ⚠️  cache miss ที่ขั้น {step} (cached_tokens=0)")
            self._cache_written = True

        return self._parse(step, response)

    def _ask_plain(self, step: str, instruction_text: str,
                   user_text: str = "ทำงานตามคำสั่งข้างบน") -> dict | None:
        """เรียก LLM โดยไม่แนบเนื้อหาเต็มบท (ขั้น 3, 3.5, 4A, 4B) — ล้มเหลวหรือ parse ไม่ได้คืน None"""
        response = self._create(step, [
            {"role": "system", "content": instruction_text},
            {"role": "user",   "content": user_text},
        ])
        return None if response is None else self._parse(step, response)

    def _summarize_part(self, part_text: str, i: int, n: int) -> str:
        print(f"     สรุปก้อน {i}/{n}...")
        response = self._create(f"summarize_part_{i}", [
            {"role": "system", "content": PART_SUMMARY_PROMPT},
            {"role": "user",   "content": part_text},
        ])
        if response is None:
            print(f"     ⚠️  สรุปก้อน {i} ไม่สำเร็จ — ใช้เนื้อหาดิบของก้อนนี้แทน")
            return part_text
        return response.choices[0].message.content or part_text

    def _read_content(self, lesson_path: str) -> tuple[str, list[str]]:
        """คืน (content_text ที่จะให้ LLM อ่านในขั้น 1/2, chunks ดิบ — ขั้น 4B ดึงข้อความตามป้ายจากนี่) — content_text คือเนื้อหาเต็ม
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
        """ขั้น 2: main_los + rubric ต่อข้อ พร้อมกันในขั้นเดียว (อ่าน cache ของ content) — summary สร้างทีหลัง
        จาก main_los ชุดสุดท้าย (ขั้น 3.5) ถ้าโมเดลยังส่ง summary มาจะถูกทิ้ง"""
        print("  📋 สร้างวัตถุประสงค์ + rubric...")
        instruction = SYNTH_LO_RUBRIC_PROMPT
        if groups:
            instruction += LO_TEMPLATE_SECTION.format(templates=self._templates_text(groups))
        result = self._ask_cached("lo_rubric", content, instruction)
        if result is not None:
            if "summary" in result:
                self._log("lo_rubric", "warn", f"ทิ้ง summary ที่โมเดลส่งมา: {result.pop('summary')}")
            self._log("lo_rubric", "result", f"{len(result.get('main_los', []))} main_los")
        return result

    def _add_hard_rubrics_for(self, objectives: dict, groups: list[str], content: str,
                              ids: list[str]) -> None:
        """สร้าง rubric ใหม่เฉพาะ main_lo ที่ถูกรวมหลัง consolidate — rubric เดิมของแต่ละข้อที่ถูกรวม
        ใช้ไม่ได้แล้วเพราะ statement เปลี่ยน (อ่าน cache ของ content เดิม ไม่ต้องอ่านเนื้อหาซ้ำเต็มราคา)"""
        main_los = objectives.get("main_los", [])
        targets = [lo for lo in main_los if lo["id"] in ids]
        if not targets:
            return
        print(f"  📏 สร้าง rubric ใหม่หลังรวม main_lo: {', '.join(ids)}")
        listing = [
            {k: lo.get(k) for k in ("id", "statement", "type", "prompt_group", "content_type")}
            for lo in targets
        ]
        templates = self._templates_text(groups)
        instruction = HARD_RUBRIC_PROMPT.format(
            templates=f"template ของมุมมองที่ใช้:\n\n{templates}" if templates else ""
        ) + f"\n\nmain_los:\n{json.dumps(listing, ensure_ascii=False, indent=2)}"
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

    def _select_softskills(self, objectives: dict) -> tuple[list[dict], list[dict]]:
        """ขั้น 4A: เลือก soft skill 0–5 ตัวจาก summary + main_los (ไม่ส่งเนื้อหาบทเรียน) + ตัวตรวจข้อ 5.1"""
        print("  🧠 เลือก soft skill จากวัตถุประสงค์...")
        main_los = objectives.get("main_los", [])
        listing = [
            {k: lo.get(k) for k in
             ("id", "statement", "type", "tag", "mentor_activity", "observable_evidence")}
            for lo in main_los
        ]
        instruction = SOFT_SELECT_PROMPT.format(
            index=tpl.softskill_index(),
            summary=objectives.get("summary", ""),
            main_los=json.dumps(listing, ensure_ascii=False, indent=2),
        )
        result = self._ask_plain("soft_select", instruction)
        if not isinstance(result, dict):
            print("     ลองใหม่อีกครั้ง...")
            result = self._ask_plain("soft_select", instruction)

        selected, not_selected, fixes = validate_selection(
            result, [lo["id"] for lo in main_los], list(tpl.softskills())
        )
        for msg in fixes:
            print(f"     ⚠️  {msg}")
            self._log("soft_select", "warn", msg)
        print(f"     → เลือก {', '.join(s['id'] for s in selected) or '(ไม่มี)'}")
        self._log("soft_select", "result",
                  f"selected={[s['id'] for s in selected]} "
                  f"not_selected={[s['id'] for s in not_selected]}")
        return selected, not_selected

    def _build_indicator(self, skill: dict, objectives: dict,
                         chunks: list[str]) -> tuple[dict | None, str]:
        """ขั้น 4B ของสกิลเดียว + ตัวตรวจข้อ 5.2 — คืน (lesson_indicators, "") ถ้าผ่าน
        หรือ (None, เหตุผลที่ย้ายไป not_selected)"""
        sid    = skill["id"]
        step   = f"soft_indicator_{sid}"
        by_id  = {lo["id"]: lo for lo in objectives.get("main_los", [])}
        linked = [by_id[i] for i in skill["linked_main_los"] if i in by_id]

        labels = [c for lo in linked for c in lo.get("source_chunks", [])]
        if labels:
            lesson_text = chunks_by_label(chunks, labels)
        else:
            msg = f"{sid}: main_lo ที่ link ไม่มี source_chunks — ใช้เนื้อหาทั้งบท"
            print(f"     ⚠️  {msg}")
            self._log(step, "warn", msg)
            lesson_text = chunks_by_label(chunks, [f"c{i}" for i in range(1, len(chunks) + 1)])

        instruction = SOFT_INDICATOR_PROMPT.format(
            skill_text=tpl.softskill_rubric_text(sid),
            linked=json.dumps(
                [{k: lo.get(k) for k in ("id", "statement", "mentor_activity", "observable_evidence")}
                 for lo in linked],
                ensure_ascii=False, indent=2,
            ),
            activity=skill["required_activity"]["how"],
            chunks=lesson_text,
            example=tpl.section(tpl.softskill_selection_prompt(), "ตัวอย่าง lesson_indicators"),
        )

        for attempt in (1, 2):
            status, payload, warnings = validate_indicator(self._ask_plain(step, instruction))
            for msg in warnings:
                print(f"     ⚠️  {sid}: {msg}")
                self._log(step, "warn", f"{sid}: {msg}")
            if status == "ok":
                return payload, ""
            if status == "infeasible":
                self._log(step, "warn", f"{sid} feasible=false: {payload}")
                return None, f"4B: {payload}"
            if attempt == 1:
                print(f"     ลอง {sid} ใหม่อีกครั้ง...")
        self._log(step, "warn", f"{sid} ตัวบ่งชี้ไม่ครบหลัง retry — ย้ายไป not_selected")
        return None, "4B: ตัวบ่งชี้ไม่ครบหลังลองใหม่"

    def build_softskills(self, objectives: dict, chunks: list[str]) -> dict:
        """ขั้น 4 ทั้งหมด: 4A เลือก แล้ว 4B ทีละสกิลที่ผ่าน — เขียน softskills /
        softskills_not_selected ลง objectives (สกิลที่ 4B ไม่ผ่านถูกย้ายไป not_selected)"""
        selected, not_selected = self._select_softskills(objectives)
        kept = []
        for skill in selected:
            print(f"  ✏️  เขียนตัวบ่งชี้ {skill['id']}...")
            indicators, why = self._build_indicator(skill, objectives, chunks)
            if indicators is None:
                print(f"     ⚠️  {skill['id']} ย้ายไป not_selected ({why})")
                not_selected.append({"id": skill["id"], "reason": why})
                continue
            kept.append({**skill, "lesson_indicators": indicators})

        order = list(tpl.softskills())
        objectives["softskills"] = kept
        objectives["softskills_not_selected"] = sorted(not_selected, key=lambda s: order.index(s["id"]))
        return objectives

    def _load_for_softskills(self, lesson_path: str) -> tuple[dict, list[str], str]:
        self._extra = {"session_id": f"synth-{hashlib.sha256(lesson_path.encode()).hexdigest()[:12]}"}
        output_path = os.path.join(lesson_path, "objectives.json")
        with open(output_path, encoding="utf-8") as f:
            objectives = json.load(f)
        if "main_los" not in objectives:
            raise ValueError("objectives.json ไม่มี main_los — รัน Synthesizer เต็มก่อน")
        _, chunks = lesson_full_text(lesson_path, self.client, self.cost_tracker)
        return objectives, chunks, output_path

    def _write(self, objectives: dict, output_path: str) -> None:
        check_before_write(objectives, list(tpl.softskills()))
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(objectives, f, ensure_ascii=False, indent=2)

    def resynthesize_softskills(self, lesson_path: str) -> dict:
        """รันเฉพาะขั้น 4 ใหม่จาก objectives.json ที่มีอยู่ (main_los เดิม)"""
        objectives, chunks, output_path = self._load_for_softskills(lesson_path)
        self.build_softskills(objectives, chunks)
        self._run_hallucination_checks(objectives)
        self._write(objectives, output_path)
        print("  บันทึก objectives.json แล้ว ✅")
        return objectives

    def add_softskill(self, lesson_path: str, skill_id: str, linked_main_los: list[str],
                      required_activity: dict) -> dict:
        """ดึงสกิลที่ไม่ถูกเลือกกลับมา: ตรวจข้อมูลที่ครูให้ → รัน 4B สกิลเดียว → ย้ายจาก
        softskills_not_selected ไป softskills แล้วเขียนกลับ (4B ไม่ผ่าน = ไม่เปลี่ยนไฟล์)"""
        objectives, chunks, output_path = self._load_for_softskills(lesson_path)

        if any(s["id"] == skill_id for s in objectives.get("softskills", [])):
            raise ValueError(f"{skill_id} ถูกเลือกอยู่แล้ว")
        if not any(s["id"] == skill_id for s in objectives.get("softskills_not_selected", [])):
            raise ValueError(f"{skill_id} ไม่อยู่ใน softskills_not_selected")
        sub_ids = {lo["id"] for lo in objectives["main_los"]}
        if not linked_main_los or not set(linked_main_los) <= sub_ids:
            raise ValueError(f"linked_main_los ต้องไม่ว่างและอยู่ใน {sorted(sub_ids)}")
        if (required_activity.get("main_lo") not in linked_main_los
                or not str(required_activity.get("how") or "").strip()):
            raise ValueError("required_activity.main_lo ต้องอยู่ใน linked_main_los และ how ต้องไม่ว่าง")
        if len(objectives.get("softskills", [])) >= MAX_SOFTSKILLS:
            raise ValueError(f"บทนี้มี {MAX_SOFTSKILLS} สกิลแล้ว (เพดาน) — ต้องเอาสกิลอื่นออกก่อน")

        skill = {
            "id":                skill_id,
            "linked_main_los":    linked_main_los,
            "reason":            "ครูเพิ่มเอง",
            "required_activity": {"main_lo": required_activity["main_lo"],
                                  "how": required_activity["how"].strip()},
        }
        indicators, why = self._build_indicator(skill, objectives, chunks)
        if indicators is None:
            print(f"  ⚠️  เพิ่ม {skill_id} ไม่สำเร็จ ({why}) — ไม่เปลี่ยนไฟล์")
            return objectives

        order = list(tpl.softskills())
        objectives["softskills"] = sorted(
            objectives.get("softskills", []) + [{**skill, "lesson_indicators": indicators}],
            key=lambda s: order.index(s["id"]),
        )
        objectives["softskills_not_selected"] = [
            s for s in objectives["softskills_not_selected"] if s["id"] != skill_id
        ]
        self._write(objectives, output_path)
        print(f"  เพิ่ม {skill_id} แล้ว ✅")
        return objectives

    def _consolidate(self, objectives: dict) -> dict:
        """ขั้น 3: การันตีว่า main_lo ไม่เกิน MAX_MAIN_LOS โดยรวมให้น้อยที่สุด — ไม่เกินเพดานอยู่แล้วไม่แตะเลย
        (ไม่เรียก LLM ไม่ renumber) · เกินเพดาน: ใช้กลุ่มที่ LLM เสนอทีละกลุ่มจนพอแล้วหยุด ยังเกิน →
        รวมแบบ mechanical · ข้อที่ถูกรวมไม่มี rubric → synthesize() ส่งเข้า rubric_remerge"""
        subs = objectives.get("main_los", [])
        n = len(subs)
        if n <= MAX_MAIN_LOS:
            print(f"  ไม่ต้องรวม main_lo ({n} ≤ {MAX_MAIN_LOS} ข้อ)")
            self._log("consolidate", "skip", json.dumps(
                {"skipped": True, "reason": "n ≤ MAX", "n": n}, ensure_ascii=False))
            return objectives

        listing = "\n".join(
            f"{lo['id']} [{lo.get('tag')}, {lo.get('type')}] "
            f"chunks={','.join(lo.get('source_chunks', []))}: {lo['statement']}"
            for lo in subs
        )
        prompt_user = (
            f"ตอนนี้มี {n} ข้อ เพดาน {MAX_MAIN_LOS} ข้อ "
            f"ต้องลดอย่างน้อย {n - MAX_MAIN_LOS} ข้อ:\n\n{listing}"
        )
        self._log("consolidate", "input", prompt_user)
        result = self._ask_plain("consolidate", CONSOLIDATE_PROMPT, prompt_user)
        self._log("consolidate", "output", json.dumps(result, ensure_ascii=False)[:2000])
        groups = result.get("merged_groups", []) if isinstance(result, dict) else []
        if not isinstance(groups, list):
            groups = []

        by_id   = {lo["id"]: lo for lo in subs}
        current = list(subs)
        used: set[str] = set()

        for gi, g in enumerate(groups):
            if len(current) <= MAX_MAIN_LOS:
                self._log("consolidate", "skip_group",
                          f"ถึงเพดานแล้ว — ข้าม {len(groups) - gi} กลุ่มที่เหลือ")
                break
            ids = g.get("from_ids") if isinstance(g, dict) else None
            if (not isinstance(ids, list) or len(ids) < 2 or len(set(ids)) != len(ids)
                    or not all(i in by_id for i in ids)):
                self._log("consolidate", "skip_group", f"from_ids ไม่ถูกต้อง: {ids!r}")
                continue
            if used & set(ids):
                self._log("consolidate", "skip_group", f"{ids} ใช้ id ซ้ำกับกลุ่มก่อนหน้า")
                continue

            members = [by_id[i] for i in ids]
            statement = str(g.get("new_statement") or "").strip()
            if not statement:
                statement = " รวมถึง".join(m["statement"] for m in members)
                self._log("consolidate", "warn", f"{ids} ไม่มี new_statement — ต่อ statement เดิม")
            merged, warnings = merge_main_los(members, statement, g.get("tag"), g.get("type"))
            for w in warnings:
                self._log("consolidate", "warn", f"{ids} {w}")
            current = _replace_members(current, members, merged)
            used.update(ids)
            print(f"  🔀 รวม {', '.join(ids)} → {statement}")
            self._log("consolidate", "merge", f"{', '.join(ids)} → {statement} "
                                              f"(reason: {g.get('reason', '')})")

        # LLM ล้ม/รวมไม่พอ: รวมคู่ติดกันที่ source_chunks ซ้อนกันมากสุด (เสมอ → คู่ท้ายสุด) จนไม่เกินเพดาน
        while len(current) > MAX_MAIN_LOS:
            overlaps = [len(set(a.get("source_chunks", [])) & set(b.get("source_chunks", [])))
                        for a, b in zip(current, current[1:])]
            i = max(range(len(overlaps)), key=lambda k: (overlaps[k], k))
            a, b = current[i], current[i + 1]
            merged, warnings = merge_main_los([a, b], f"{a['statement']} รวมถึง{b['statement']}",
                                             None, None)
            for w in warnings:
                self._log("consolidate", "warn", f"mechanical {a['id']}+{b['id']} {w}")
            current = current[:i] + [merged] + current[i + 2:]
            print(f"  ⚠️  รวมแบบ mechanical {a['id']} + {b['id']} (chunks ร่วม {overlaps[i]})")
            self._log("consolidate", "mechanical_merge",
                      f"{a['id']} + {b['id']} chunks ร่วม {overlaps[i]}")

        for i, lo in enumerate(current, start=1):
            lo["id"] = f"s{i}"
        objectives["main_los"] = current
        return objectives

    def _build_summary(self, objectives: dict) -> str:
        """ขั้น 3.5: summary 1 ประโยค สรุปจาก main_los ชุดสุดท้าย + missing_coverage (ไม่ส่งเนื้อหาบท)
        ล้มเหลว 2 ครั้ง → "" (Mentor/4A ใช้ .get จึงรับค่าว่างได้)"""
        print("  🎯 สรุป summary ของบทจาก main_los...")
        listing = "\n".join(
            f"{lo['id']} [{lo.get('tag')}, {lo.get('type')}]: {lo['statement']}"
            for lo in objectives.get("main_los", [])
        )
        missing = "\n".join(f"- {m}" for m in objectives.get("missing_coverage", [])) or "- (ไม่มี)"
        prompt_user = (f"บทเรียน: {objectives.get('lesson_title', '')}\n\n"
                       f"main_los:\n{listing}\n\nmissing_coverage:\n{missing}")
        self._log("summary", "input", prompt_user)

        for attempt in (1, 2):
            result = self._ask_plain("summary", SUMMARY_PROMPT, prompt_user)
            self._log("summary", "output", json.dumps(result, ensure_ascii=False)[:1000])
            summary = result.get("summary") if isinstance(result, dict) else None
            if isinstance(summary, str) and summary.strip():
                self._log("summary", "result", summary.strip())
                print(f"     → {summary.strip()}")
                return summary.strip()
            if attempt == 1:
                print("     ลองใหม่อีกครั้ง...")
        print("     ⚠️  สร้าง summary ไม่สำเร็จ — ใช้ค่าว่าง")
        self._log("summary", "warn", "สร้าง summary ไม่สำเร็จหลังลองใหม่ — summary = ''")
        return ""

    def _run_hallucination_checks(self, objectives: dict) -> None:
        """ตรวจด้วยโค้ด (ไม่ใช้ LLM) แล้ว log คำเตือนถ้าไม่ผ่าน — ไม่บล็อกการบันทึกไฟล์"""
        main_los = objectives.get("main_los", [])
        if len(main_los) > MAX_MAIN_LOS:
            msg = f"main_los เกินเพดาน: {len(main_los)} > {MAX_MAIN_LOS}"
            print(f"     ⚠️  {msg}")
            self._log("check", "warn", msg)

        for lo in main_los:
            rubric = lo.get("rubric") or {}
            missing = [lv for lv in ("0", "1", "2", "3") if not rubric.get(lv)]
            if missing:
                self._log("check", "warn", f"{lo['id']} rubric ขาดระดับ {missing}")

        for sk in objectives.get("softskills", []):
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

        for lo in objectives.get("main_los", []):
            if lo.get("type") not in ("conceptual", "factual"):
                lo["type"] = "conceptual"
            if lo.get("prompt_group") not in groups:
                lo["prompt_group"] = groups[0] if len(groups) == 1 else None
            # โมเดลอาจยังใช้ชื่อฟิลด์เก่า — ย้ายมาเป็น source_chunks แทนที่จะทิ้ง
            if "evidence_chunks" in lo:
                self._log("check", "warn", f"{lo.get('id')} ใช้ชื่อฟิลด์เก่า evidence_chunks → source_chunks")
                lo.setdefault("source_chunks", lo.pop("evidence_chunks"))
            # กันหลอน: source_chunks ต้องอ้างป้ายที่มีอยู่จริงในเนื้อหาเท่านั้น (ขั้น 4B ใช้ดึงเนื้อหา)
            bad = [c for c in lo.get("source_chunks", []) if c not in valid_chunk_ids]
            if bad:
                print(f"     ⚠️  {lo.get('id')}: source_chunks อ้างป้ายที่ไม่มีจริง {bad}")
                self._log("check", "warn", f"{lo.get('id')} source_chunks ไม่มีจริง: {bad}")
            lo["source_chunks"] = sort_labels(c for c in lo.get("source_chunks", []) if c in valid_chunk_ids)

        if "main_los" in objectives:
            # ขั้น 3: consolidate (ไม่ต้องอ่านเนื้อหาเต็ม)
            objectives = self._consolidate(objectives)

            # main_lo ที่ถูกรวมจะไม่มี key "rubric" เลย (dict ใหม่ที่ _consolidate สร้าง ไม่ได้พก rubric
            # เดิมมาด้วย เพราะ statement เปลี่ยนแล้วใช้ต่อไม่ได้) → สร้าง rubric ใหม่เฉพาะข้อพวกนี้
            needs_rubric = [lo["id"] for lo in objectives["main_los"] if not lo.get("rubric")]
            if needs_rubric:
                self._add_hard_rubrics_for(objectives, groups, content, needs_rubric)

            # ขั้น 3.5: summary สรุปจาก main_los ชุดสุดท้าย (missing_coverage ต้องครบก่อน — ห้ามพูดถึง)
            objectives.setdefault("missing_coverage", []).extend(selection["dropped_groups"])
            summary    = self._build_summary(objectives)
            objectives = {"lesson_title": objectives.get("lesson_title", ""), "summary": summary,
                          **{k: v for k, v in objectives.items() if k != "lesson_title"}}

            # ขั้น 4: soft skills — 4A เลือกจาก summary + main_los, 4B ตัวบ่งชี้ทีละสกิลจาก source_chunks
            # (ไม่แนบเนื้อหาเต็มบท จึงไม่ผ่าน cache ของ content)
            self.build_softskills(objectives, chunks)
            objectives["prompt_groups"] = groups
            objectives["group_reason"]  = selection["group_reason"]

            self._run_hallucination_checks(objectives)

        output_path = os.path.join(lesson_path, "objectives.json")
        if "main_los" in objectives:
            self._write(objectives, output_path)
        else:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(objectives, f, ensure_ascii=False, indent=2)

        print(f"  บันทึก objectives.json แล้ว ✅")
        return objectives
