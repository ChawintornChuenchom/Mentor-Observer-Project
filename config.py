import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

MODEL_MENTOR      = os.getenv("MODEL_MENTOR",      "google/gemini-2.5-flash")
MODEL_OBSERVER    = os.getenv("MODEL_OBSERVER",    "anthropic/claude-haiku-4-5")
MODEL_SYNTHESIZER = os.getenv("MODEL_SYNTHESIZER", "google/gemini-2.5-flash")
MODEL_EMBEDDING   = os.getenv("MODEL_EMBEDDING",   "openai/text-embedding-3-small")

# OCR: โมเดล vision สำหรับอ่านข้อความจากหน้า PDF
#   - qwen/qwen-2.5-vl-7b-instruct:free ถูกถอดออกจาก OpenRouter แล้ว
#   - default นี้เสียเงินนิดหน่อย (~$0.005/บทเรียน 18 หน้า) แต่เสถียรและอ่านไทยดี
#   - อยากได้ "ฟรีจริง": เพิ่ม Google AI Studio key ของตัวเองที่
#     https://openrouter.ai/settings/integrations แล้ว set MODEL_OCR ตัวเดิมไว้ได้
#   - โมเดลฟรีบน shared pool (เช่น google/gemma-4-31b-it:free) มักโดน rate-limit (429)
MODEL_OCR         = os.getenv("MODEL_OCR",         "google/gemini-2.5-flash-lite")