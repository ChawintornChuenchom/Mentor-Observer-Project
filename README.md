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