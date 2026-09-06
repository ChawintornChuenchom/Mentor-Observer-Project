import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

MODEL_MENTOR      = os.getenv("MODEL_MENTOR",      "google/gemini-2.5-flash")
MODEL_OBSERVER    = os.getenv("MODEL_OBSERVER",    "anthropic/claude-haiku-4-5")
MODEL_SYNTHESIZER = os.getenv("MODEL_SYNTHESIZER", "google/gemini-2.5-flash")
MODEL_EMBEDDING   = os.getenv("MODEL_EMBEDDING",   "openai/text-embedding-3-small")
MODEL_OCR         = os.getenv("MODEL_OCR",         "google/gemini-2.5-flash")