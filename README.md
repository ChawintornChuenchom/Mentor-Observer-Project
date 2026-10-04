# AI-Mentor-Observer

## Requirements

- openai
- python-dotenv
- chromadb
- pymupdf (จำเป็นสำหรับอ่านไฟล์ PDF)

## Installation

```
pip install -r requirements.txt
```

## ขั้นตอนการทำงาน (main.py)

1. เลือกไฟล์บทเรียน (PDF / DOCX / PPTX / TXT / MD / รูปภาพ — เลือกได้หลายไฟล์พร้อมกัน)
2. **[1/3]** PDF และรูปภาพถูก OCR ด้วย vision LLM (`MODEL_OCR`) เป็น `.txt`
   (PDF → ไฟล์ละ `.txt` · รูปภาพทุกภาพ → รวมเป็น `images.txt` ไฟล์เดียว เรียงตามชื่อไฟล์)
3. **[2/3]** นำไฟล์ข้อความไป index เป็น ChromaDB
4. **[3/3]** สังเคราะห์วัตถุประสงค์การเรียนรู้ (`objectives.json`)

> ถ้า OCR ไม่ได้ข้อความ ระบบจะหยุดก่อนขั้นสังเคราะห์ (ไม่เดาเนื้อหาอีกต่อไป)
## `objectives.json`

ไม่มี `schema_version` และไม่รองรับไฟล์รุ่นเก่า — เปลี่ยน prompt/template แล้วให้รัน `python resynthesize.py` ใหม่ทุกบท

| ฟิลด์ | ความหมาย |
|---|---|
| `summary`, `main_los[]` | สรุปวัตถุประสงค์ของบท 1 ประโยค + Main LO (≤6) พร้อม `observable_evidence`, `mentor_activity`, `rubric` 0–3 |
| `main_los[].source_chunks` | ป้าย `[cN]` ของเนื้อหาที่ Main LO นั้นอยู่ (เรียงตามเลข) — ขั้น 4B ดึงข้อความตามป้ายนี้ |
| `softskills[]` | soft skill ที่เลือกจากวัตถุประสงค์ 0–5 สกิล: `id` (S01–S12), `linked_main_los`, `reason`, `required_activity: {main_lo, how}`, `lesson_indicators` 1–5 |
| `softskills_not_selected[]` | สกิลที่ไม่เลือก `{id, reason}` — ดึงกลับมาได้ด้วย `resynthesize.py` โหมด "ดึง soft skill ที่ไม่ถูกเลือกกลับมา" |
| `prompt_groups`, `group_reason`, `missing_coverage` | มุมมองการประเมิน P01–P18 ที่เลือก และเนื้อหาที่วัดจากบทสนทนาไม่ได้ |

ขั้นสร้าง soft skill (รายละเอียดใน `templates/softskills/_selection_prompt.md`):
- **4A** เลือกสกิลจาก summary + main_los ตาม `templates/softskills/_index.md` (ไม่ส่งเนื้อหาบทเรียน) → โค้ดตรวจผล
- **4B** ต่อสกิลที่ผ่าน: เขียนตัวบ่งชี้ 1–5 จาก rubric ของไฟล์ S + เนื้อหาตาม `source_chunks`
  ของ main_lo ที่ link → เขียนไม่ได้ (`feasible: false`) หรือไม่ครบหลังลองใหม่ ย้ายไป `softskills_not_selected`

`resynthesize.py` มี 3 โหมด: สร้างใหม่ทั้งหมด · สร้างเฉพาะ soft skill ใหม่ (ขั้น 4 จาก main_los เดิม) · ดึงสกิลที่ไม่ถูกเลือกกลับมา
