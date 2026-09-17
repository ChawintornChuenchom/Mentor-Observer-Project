import os
from dotenv import load_dotenv

load_dotenv()

# นักเรียนแต่ละคนมี OpenRouter API key แยกกันเอง (คนละบัญชี คนละงบ คนละ rate limit)
# → cache ของ static prompt จะ "ไม่แชร์ข้ามนักเรียน" อีกต่อไป (คนละ key = คนละ user ฝั่ง OpenRouter
#   ไม่ว่า session_id จะตั้งเหมือนกันแค่ไหนก็ตาม) แต่ยังแชร์กับ "ตัวเอง" ข้าม session ได้ตามปกติ
# เพิ่มนักเรียนคนใหม่: เพิ่ม OPENROUTER_API_KEY_<ตัวอักษร> ใน .env แล้วเพิ่ม key ในดิกชันนารีนี้
OPENROUTER_API_KEYS = {
    "A": os.getenv("OPENROUTER_API_KEY_1"),
    "B": os.getenv("OPENROUTER_API_KEY_2"),
}

# key เริ่มต้นสำหรับงานฝั่งครู/setup (main.py, extract_pdf_text.py) ที่ไม่ผูกกับนักเรียนคนใดคนหนึ่ง
OPENROUTER_API_KEY = (
    os.getenv("OPENROUTER_API_KEY")
    or OPENROUTER_API_KEYS["A"]
    or OPENROUTER_API_KEYS["B"]
)

# ปิดไว้ชั่วคราวเพื่อเทียบ token/cost แบบมี-ไม่มี prompt cache ได้ตรงๆ
# (set MENTOR_USE_CACHE=false ใน env ก่อนรัน mentor.py — ไม่ต้องแก้โค้ด)
MENTOR_USE_CACHE  = os.getenv("MENTOR_USE_CACHE", "true").strip().lower() not in ("false", "0", "no")

MODEL_MENTOR      = os.getenv("MODEL_MENTOR",      "google/gemini-2.5-flash")
MODEL_OBSERVER    = os.getenv("MODEL_OBSERVER",    "anthropic/claude-haiku-4.5")
MODEL_SYNTHESIZER = os.getenv("MODEL_SYNTHESIZER", "google/gemini-2.5-flash")
MODEL_EMBEDDING   = os.getenv("MODEL_EMBEDDING",   "openai/text-embedding-3-small")

# OCR: โมเดล vision สำหรับอ่านข้อความจากหน้า PDF
#   - qwen3-vl-32b-instruct = ถูกสุดใน lineup Qwen3-VL (~$0.008/บทเรียน 18 หน้า)
#     และเป็น 32B dense อ่านไทยได้ดีกว่ารุ่น 8B
#   - อย่าใช้รุ่น *-thinking กับงาน OCR: output แพงกว่า ~5 เท่าเพราะเสีย token ไปกับ reasoning
#   - ทางเลือกอื่น: google/gemini-2.5-flash-lite (ราคาพอกัน), qwen/qwen2.5-vl-72b-instruct (แพงกว่า ~8 เท่า)
#   - qwen/qwen-2.5-vl-7b-instruct:free ถูกถอดออกจาก OpenRouter แล้ว
MODEL_OCR         = os.getenv("MODEL_OCR",         "qwen/qwen3-vl-32b-instruct")