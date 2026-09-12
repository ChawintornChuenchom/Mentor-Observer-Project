import json
import os
from openai import OpenAI
from config import MODEL_SYNTHESIZER
from rag import RAG

MAX_SUB_LOS = 5   # เพดานตายตัว — ทุกบทเรียนต้องไม่เกินนี้ ไม่ว่าเนื้อหาจะยาว/ซับซ้อนแค่ไหน

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
      "evidence_chunks": ["c1"]
    }
  ],
  "missing_coverage": ["เหตุผล..."]
}"""

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

            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                parts = raw.split("```")
                raw   = parts[1] if len(parts) > 1 else raw[3:]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            try:
                groups = json.loads(raw, strict=False).get("merged_groups", [])
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

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYNTHESIZER_PROMPT},
                {"role": "user",   "content": f"เนื้อหาบทเรียน:\n\n{content}"}
            ]
        )

        if self.cost_tracker is not None and getattr(response, "usage", None):
            self.cost_tracker.track_synthesizer(response.usage)

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw   = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            objectives = json.loads(raw, strict=False)   # strict=False: กัน newline ตัวจริงใน string
        except json.JSONDecodeError:
            print("  ⚠️  parse JSON ไม่ได้ บันทึก raw text แทน")
            objectives = {"error": raw}

        # กัน type หายหรือผิดค่า → default เป็น conceptual (ปลอดภัยสุดสำหรับ Mentor)
        for lo in objectives.get("sub_los", []):
            if lo.get("type") not in ("conceptual", "factual"):
                lo["type"] = "conceptual"

        if "sub_los" in objectives:
            objectives = self._consolidate(objectives)

        output_path = os.path.join(lesson_path, "objectives.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(objectives, f, ensure_ascii=False, indent=2)

        print(f"  บันทึก objectives.json แล้ว ✅")
        return objectives