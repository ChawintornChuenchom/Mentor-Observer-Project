import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

MODEL_MENTOR      = os.getenv("MODEL_MENTOR",      "google/gemini-2.5-flash")
MODEL_OBSERVER    = os.getenv("MODEL_OBSERVER",    "anthropic/claude-haiku-4-5")
MODEL_SYNTHESIZER = os.getenv("MODEL_SYNTHESIZER", "google/gemini-2.5-flash")
MODEL_EMBEDDING   = os.getenv("MODEL_EMBEDDING",   "openai/text-embedding-3-small")

# OCR: โมเดล vision สำหรับอ่านข้อความจากหน้า PDF
#   - qwen3-vl-32b-instruct = ถูกสุดใน lineup Qwen3-VL (~$0.008/บทเรียน 18 หน้า)
#     และเป็น 32B dense อ่านไทยได้ดีกว่ารุ่น 8B
#   - อย่าใช้รุ่น *-thinking กับงาน OCR: output แพงกว่า ~5 เท่าเพราะเสีย token ไปกับ reasoning
#   - ทางเลือกอื่น: google/gemini-2.5-flash-lite (ราคาพอกัน), qwen/qwen2.5-vl-72b-instruct (แพงกว่า ~8 เท่า)
#   - qwen/qwen-2.5-vl-7b-instruct:free ถูกถอดออกจาก OpenRouter แล้ว
MODEL_OCR         = os.getenv("MODEL_OCR",         "qwen/qwen3-vl-32b-instruct")