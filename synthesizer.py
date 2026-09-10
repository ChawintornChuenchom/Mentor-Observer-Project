import json
import os
from openai import OpenAI
from config import MODEL_SYNTHESIZER
from rag import RAG

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
- สร้าง sub_los 6-10 ข้อเท่านั้น — รวมหัวข้อที่ใกล้เคียงกันเป็นข้อเดียว อย่าแตกย่อยจนซ้ำซ้อน
  (เช่น "ตั้งสมมติฐาน" + "ตรวจสอบสมมติฐาน" + "สรุปผลเทียบสมมติฐาน" = รวมเป็น 1-2 ข้อ)
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


class Synthesizer:
    def __init__(self, client: OpenAI, rag: RAG, cost_tracker=None):
        self.client       = client
        self.rag          = rag
        self.model        = MODEL_SYNTHESIZER
        self.cost_tracker = cost_tracker

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

        usage = getattr(response, "usage", None)
        if self.cost_tracker is not None and usage:
            self.cost_tracker.track_synthesizer(
                getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0
            )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw   = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            objectives = json.loads(raw)
        except json.JSONDecodeError:
            print("  ⚠️  parse JSON ไม่ได้ บันทึก raw text แทน")
            objectives = {"error": raw}

        # กัน type หายหรือผิดค่า → default เป็น conceptual (ปลอดภัยสุดสำหรับ Mentor)
        for lo in objectives.get("sub_los", []):
            if lo.get("type") not in ("conceptual", "factual"):
                lo["type"] = "conceptual"

        output_path = os.path.join(lesson_path, "objectives.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(objectives, f, ensure_ascii=False, indent=2)

        print(f"  บันทึก objectives.json แล้ว ✅")
        return objectives