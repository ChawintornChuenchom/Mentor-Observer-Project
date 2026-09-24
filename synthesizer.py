import json
import os
from openai import OpenAI
from config import MODEL_SYNTHESIZER
from json_utils import parse_json
from rag import RAG
import template_loader as tpl

MAX_SUB_LOS = 5   # เพดานตายตัว — ทุกบทเรียนต้องไม่เกินนี้ ไม่ว่าเนื้อหาจะยาว/ซับซ้อนแค่ไหน
SCHEMA_VERSION = 2   # 2 = มี prompt_groups, rubric ต่อ sub_lo และ softskills

SYNTHESIZER_PROMPT = """คุณคือระบบสังเคราะห์วัตถุประสงค์การเรียนรู้จากเนื้อหาบทเรียน

อ่านเนื้อหาที่ให้มาแล้วสร้าง output ต่อไปนี้:
1. แบ่งเนื้อหาเป็น chunk ตามหัวข้อหรือความหมาย
2. สังเคราะห์ main_lo: เป้าหมายรวมของบทเรียนใน 1 ประโยค ใช้คำกริยาที่วัดได้
3. สังเคราะห์ sub_los: แต่ละข้อต้องวัดได้จากบทสนทนา ไม่ซ้ำซ้อน
4. ระบุ missing_coverage: เนื้อหาที่วัดจากบทสนทนาไม่ได้

กฎสำคัญ:
- ใช้คำกริยาที่วัดได้: อธิบาย แก้ แยก วิเคราะห์ ยกตัวอย่าง
- ห้ามใช้: เข้าใจ รู้ เรียนรู้
- tag: core = ต้องผ่านทุกข้อ, supporting = เสริม
- สร้าง sub_los **ไม่เกิน 5 ข้อเด็ดขาด ไม่ว่าเนื้อหาจะยาวหรือซับซ้อนแค่ไหน** (เนื้อหาน้อยจะได้แค่ 2-3 ข้อก็ได้)
  ถ้าเนื้อหามีหัวข้อย่อยมากกว่า 5 เรื่อง ให้ยุบรวมหัวข้อที่ใกล้เคียง/ต่อเนื่องกันเข้าเป็นข้อเดียว
  แทนที่จะแตกเป็นหลายข้อ — 1 ข้อที่ครอบคลุมกว้างดีกว่าแตกเป็นหลาย sub_lo ที่แคบ
  ⚠️ ห้ามฝืนแตกหัวข้อให้ครบจำนวนใดๆ ถ้าเนื้อหาน้อย — 2 ข้อที่ดีดีกว่า 5 ข้อที่ซ้ำซ้อน
- รวมหัวข้อที่ใกล้เคียงกันเป็นข้อเดียว อย่าแตกย่อยจนซ้ำซ้อน
  (เช่น "ตั้งสมมติฐาน" + "ตรวจสอบสมมติฐาน" + "สรุปผลเทียบสมมติฐาน" = รวมเป็น 1-2 ข้อ)
- ⚠️ ระวังวัตถุประสงค์ "คู่ขนาน" — ถ้าเจอ sub_lo 2 ข้อที่ใช้ทักษะ/เกณฑ์เดียวกัน แต่แยกไปใช้กับ
  2 กลุ่มที่เป็นคู่ตรงข้ามกัน ห้ามแยกเป็น 2 ข้อ ให้รวมเป็นข้อเดียวที่จำแนก/เปรียบเทียบทั้งสองฝั่งพร้อมกัน
  ตัวอย่างผิด: "ระบุตัวสะกดที่ทำให้เป็นคำตายได้" แยกจาก "ระบุตัวสะกดที่ทำให้เป็นคำเป็นได้"
             "อธิบายลักษณะคำตายในมาตรา ก กา" แยกจาก "อธิบายลักษณะคำเป็นในมาตรา ก กา"
  ตัวอย่างถูก: "จำแนกมาตราตัวสะกดที่ทำให้พยางค์เป็นคำเป็นหรือคำตายได้" (ข้อเดียว ครอบคลุมทั้งคู่)
- เรียง sub_los ตามลำดับการสอนจริง: s1 = พื้นฐานสุด (นิยาม/ข้อเท็จจริง) → กลางๆ (ความเข้าใจ)
  → ข้อท้ายๆ = ซับซ้อนสุด (วิเคราะห์/ประยุกต์) นักเรียนต้องเรียน s1 ก่อนถึงจะทำข้อถัดไปได้

ทุก sub_lo ต้องมี field "type" เป็นอย่างใดอย่างหนึ่ง:
- "conceptual" = วัดการเข้าใจ / วิเคราะห์ / เปรียบเทียบ / ประยุกต์แนวคิด
    Mentor จะปูพื้นฐานให้ก่อน แล้วค่อยใช้ Socratic ถามให้คิดต่อยอด
    ห้ามเป็นแค่การจำโครงสร้างเอกสาร (เช่น "มีกี่หน่วย" "ชื่อหน่วยคืออะไร"
    "สารบัญมีอะไรบ้าง") เพราะนั่นคือ recall ไม่ใช่ critical thinking
- "factual" = ข้อมูลเชิงข้อเท็จจริง / โครงสร้างเอกสาร / นิยามเฉพาะ / ตัวเลข-ชื่อ
    ที่ต้องจำตรงตัว เถียงไม่ได้ Mentor บอกข้อมูลนี้ตรงๆ ได้เลยแล้วค่อยถามต่อยอด
    ไม่ต้องเล่นเกมให้นักเรียนทายคำตอบ

แนวทาง: sub_lo ส่วนใหญ่ควรเป็น "conceptual" ให้ "factual" เฉพาะข้อที่เป็น
ข้อเท็จจริง/โครงสร้างล้วนๆ ซึ่งการถามแบบ Socratic จะไร้ประโยชน์

output เป็น JSON เท่านั้น ห้ามมี markdown:
{
  "lesson_title": "ชื่อบทเรียน",
  "chunks": [{"id": "c1", "summary": "สรุปสั้นๆ"}],
  "main_lo": "นักเรียนสามารถ...",
  "sub_los": [
    {
      "id": "s1",
      "statement": "นักเรียนสามารถ...",
      "tag": "core",
      "type": "conceptual",
      "evidence_chunks": ["c1"],
      "prompt_group": "P03",
      "content_type": "BIO-M"
    }
  ],
  "missing_coverage": ["เหตุผล..."]
}"""

# ต่อท้าย SYNTHESIZER_PROMPT เมื่อขั้นเลือกมุมมองได้กลุ่มมา
LO_TEMPLATE_SECTION = """

---
มุมมองการประเมินที่เลือกไว้สำหรับบทเรียนนี้ — ใช้ "เน้นดูอะไร", "ป้าย", "Sub LO", "ไม่ตั้งเป็น Sub LO"
ของ template ประกอบการสร้าง sub_los (ถ้าขัดกับกฎสำคัญข้างบน ให้ยึดกฎข้างบน):
- prompt_group = id ของมุมมองที่ Sub LO นั้นมาจาก
- content_type = ป้ายของ template ที่ตรงกับ Sub LO นั้น
- สร้าง Sub LO เฉพาะป้ายที่พบจริงในเนื้อหา ห้ามสร้างเพื่อให้ครบทุกป้าย

{templates}"""

# ── ขั้นเลือกมุมมองการประเมิน (P01–P18) ─────────────────────
SELECT_GROUPS_PROMPT = """คุณคือระบบวิเคราะห์เนื้อหาบทเรียนเพื่อเลือก "มุมมองการประเมิน"

{index}

---
ทำตาม "วิธีเลือก" ข้างบน ตัดสินจากเนื้อหาที่ให้มาเท่านั้น ไม่ใช่ชื่อวิชา
เลือก prompt_groups ไม่เกิน 2 กลุ่ม (ถ้าไม่มีกลุ่มไหนเข้าเลย ตอบ [])

output เป็น JSON เท่านั้น ห้ามมี markdown:
{{
  "prompt_groups": ["P03"],
  "group_reason": "เหตุผลสั้นๆ ว่าทำไมเลือกกลุ่มเหล่านี้",
  "dropped_groups": ["กลุ่มที่ตัดทิ้งพร้อมเหตุผล (ถ้ามี)"]
}}"""

# ── ขั้นสร้าง rubric hard skill ต่อ sub_lo (หลัง consolidate แล้ว) ──
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

# ── ขั้นสร้างตัวบ่งชี้ soft skill ครบ S01–S12 ────────────────
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


class Synthesizer:
    def __init__(self, client: OpenAI, rag: RAG, cost_tracker=None):
        self.client       = client
        self.rag          = rag
        self.model        = MODEL_SYNTHESIZER
        self.cost_tracker = cost_tracker

    def _ask(self, step: str, system: str, user: str) -> dict | None:
        """เรียก LLM หนึ่งขั้น — ล้มเหลวคืน None (ขั้นเสริมไม่ควรทำให้ทั้ง setup พัง)"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ]
            )
        except Exception as e:
            print(f"  ⚠️  {step} ไม่สำเร็จ ({e})")
            return None

        if self.cost_tracker is not None and getattr(response, "usage", None):
            self.cost_tracker.track_synthesizer(response.usage)

        try:
            return parse_json(response.choices[0].message.content or "")
        except json.JSONDecodeError:
            print(f"  ⚠️  parse ผล{step}ไม่ได้")
            return None

    def _select_groups(self, content: str) -> dict:
        """เลือกมุมมองการประเมิน P01–P18 ไม่เกิน 2 กลุ่ม"""
        print("  🧭 เลือกมุมมองการประเมิน...")
        result = self._ask(
            "เลือกมุมมองการประเมิน",
            SELECT_GROUPS_PROMPT.format(index=tpl.lo_index()),
            f"เนื้อหาบทเรียน:\n\n{content}"
        ) or {}
        valid  = set(tpl.lo_group_ids())
        groups = [g for g in result.get("prompt_groups", []) if g in valid][:2]
        print(f"     → {', '.join(groups) or '(ไม่เข้ากลุ่มใด ใช้กฎหลักอย่างเดียว)'}")
        return {
            "prompt_groups":  groups,
            "group_reason":   result.get("group_reason", ""),
            "dropped_groups": result.get("dropped_groups", []),
        }

    @staticmethod
    def _templates_text(groups: list[str]) -> str:
        return "\n\n---\n\n".join(tpl.lo_template(g) for g in groups)

    def _add_hard_rubrics(self, objectives: dict, groups: list[str]) -> None:
        """เติม observable_evidence / mentor_activity / rubric 0–3 ให้ sub_lo (แก้ใน objectives)"""
        print("  📏 สร้าง rubric hard skill ต่อ Sub LO...")
        sub_los = objectives.get("sub_los", [])
        listing = [
            {k: lo.get(k) for k in ("id", "statement", "type", "prompt_group", "content_type")}
            for lo in sub_los
        ]
        templates = self._templates_text(groups)
        result = self._ask(
            "สร้าง rubric hard skill",
            HARD_RUBRIC_PROMPT.format(
                templates=f"template ของมุมมองที่ใช้:\n\n{templates}" if templates else ""
            ),
            f"main_lo: {objectives.get('main_lo', '')}\n\n"
            f"sub_los:\n{json.dumps(listing, ensure_ascii=False, indent=2)}"
        ) or {}

        by_id = {r.get("id"): r for r in result.get("rubrics", [])}
        for lo in sub_los:
            r = by_id.get(lo["id"])
            if not r:
                continue
            for key in ("observable_evidence", "mentor_activity", "rubric"):
                if r.get(key):
                    lo[key] = r[key]
        missing = [lo["id"] for lo in sub_los if not lo.get("rubric")]
        if missing:
            print(f"     ⚠️  ไม่มี rubric: {', '.join(missing)} (Observer จะใช้เกณฑ์กลางแทน)")
        else:
            print(f"     → ครบ {len(sub_los)} ข้อ")

    def _build_softskills(self, objectives: dict) -> list[dict]:
        """สร้าง lesson_indicators ครบทุกสกิล S01–S12"""
        print("  🧠 สร้างตัวบ่งชี้ soft skill ครบทุกสกิล...")
        skills  = tpl.softskills()
        sub_los = [
            {k: lo.get(k) for k in ("id", "statement", "type", "mentor_activity")}
            for lo in objectives.get("sub_los", [])
        ]
        result = self._ask(
            "สร้างตัวบ่งชี้ soft skill",
            SOFT_INDICATOR_PROMPT.format(
                index=tpl.softskill_index(),
                skills="\n\n---\n\n".join(s["text"] for s in skills.values()),
                skill_ids=", ".join(skills),
                example=tpl.section(tpl.softskill_selection_prompt(), "ตัวอย่าง lesson_indicators"),
            ),
            f"main_lo: {objectives.get('main_lo', '')}\n\n"
            f"sub_los:\n{json.dumps(sub_los, ensure_ascii=False, indent=2)}"
        ) or {}

        by_id   = {s.get("id"): s for s in result.get("softskills", [])}
        ordered = [by_id[sid] for sid in skills if sid in by_id]
        missing = [sid for sid in skills if sid not in by_id]
        if missing:
            print(f"     ⚠️  ไม่ได้ตัวบ่งชี้ของ {', '.join(missing)}")
        print(f"     → {len(ordered)}/{len(skills)} สกิล")
        return ordered

    def _consolidate(self, objectives: dict) -> dict:
        """pass 2: หาคู่ขนาน/ซ้ำซ้อน แล้วรวมให้เหลือไม่เกิน MAX_SUB_LOS ข้อ (deterministic —
        ไม่พึ่งว่า pass 1 จะทำตามกฎเรื่องจำนวน/คู่ขนานเองได้ครบ)"""
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
                ]
            )
        except Exception as e:
            print(f"  ⚠️  ตรวจสอบความซ้ำซ้อนไม่สำเร็จ ({e}) ข้ามขั้นนี้")
            groups = []
        else:
            if self.cost_tracker is not None and getattr(response, "usage", None):
                self.cost_tracker.track_synthesizer(response.usage)

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

    def synthesize(self, lesson_path: str) -> dict:
        print("  กำลังสังเคราะห์วัตถุประสงค์...")

        queries = [
            "เนื้อหาหลักของบทเรียนนี้คืออะไร",
            "นักเรียนต้องเรียนรู้อะไรบ้าง",
            "ทักษะและความสามารถที่คาดหวัง",
            "หัวข้อย่อยและรายละเอียดสำคัญ"
        ]

        all_chunks = []
        for q in queries:
            all_chunks.extend(self.rag.query(q, n_results=5))

        seen, unique_chunks = set(), []
        for c in all_chunks:
            if c not in seen:
                seen.add(c)
                unique_chunks.append(c)

        if not unique_chunks:
            raise ValueError(
                "ไม่มีเนื้อหาใน ChromaDB — สังเคราะห์วัตถุประสงค์ไม่ได้ "
                "(ตรวจสอบว่าไฟล์ถูกแปลงเป็นข้อความและ index สำเร็จหรือไม่)"
            )

        content = "\n\n---\n\n".join(unique_chunks[:20])

        # ขั้น 1: เลือกมุมมองการประเมิน → ใช้ template ของกลุ่มนั้นประกอบการสร้าง LO
        selection = self._select_groups(content)
        groups    = selection["prompt_groups"]
        system    = SYNTHESIZER_PROMPT
        if groups:
            system += LO_TEMPLATE_SECTION.format(templates=self._templates_text(groups))

        print("  📋 สร้างวัตถุประสงค์...")
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": f"เนื้อหาบทเรียน:\n\n{content}"}
            ]
        )

        if self.cost_tracker is not None and getattr(response, "usage", None):
            self.cost_tracker.track_synthesizer(response.usage)

        raw = response.choices[0].message.content
        try:
            objectives = parse_json(raw)
        except json.JSONDecodeError:
            print("  ⚠️  parse JSON ไม่ได้ บันทึก raw text แทน")
            objectives = {"error": raw}

        # กัน type หายหรือผิดค่า → default เป็น conceptual (ปลอดภัยสุดสำหรับ Mentor)
        for lo in objectives.get("sub_los", []):
            if lo.get("type") not in ("conceptual", "factual"):
                lo["type"] = "conceptual"
            if lo.get("prompt_group") not in groups:
                lo["prompt_group"] = groups[0] if len(groups) == 1 else None

        if "sub_los" in objectives:
            objectives = self._consolidate(objectives)

            # rubric ต้องสร้างหลัง consolidate — การรวม sub_lo ทำให้ rubric เดิมใช้ไม่ได้
            self._add_hard_rubrics(objectives, groups)
            objectives["softskills"]     = self._build_softskills(objectives)
            objectives["prompt_groups"]  = groups
            objectives["group_reason"]   = selection["group_reason"]
            objectives["schema_version"] = SCHEMA_VERSION
            objectives.setdefault("missing_coverage", []).extend(selection["dropped_groups"])

        output_path = os.path.join(lesson_path, "objectives.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(objectives, f, ensure_ascii=False, indent=2)

        print(f"  บันทึก objectives.json แล้ว ✅")
        return objectives