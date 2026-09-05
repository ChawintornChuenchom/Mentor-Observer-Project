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
      "evidence_chunks": ["c1"]
    }
  ],
  "missing_coverage": ["เหตุผล..."]
}"""


class Synthesizer:
    def __init__(self, client: OpenAI, rag: RAG, cost_tracker=None):
        self.client = client
        self.rag    = rag
        self.model  = MODEL_SYNTHESIZER

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

        content = "\n\n---\n\n".join(unique_chunks[:20])

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYNTHESIZER_PROMPT},
                {"role": "user",   "content": f"เนื้อหาบทเรียน:\n\n{content}"}
            ]
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

        output_path = os.path.join(lesson_path, "objectives.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(objectives, f, ensure_ascii=False, indent=2)

        print(f"  บันทึก objectives.json แล้ว ✅")
        return objectives