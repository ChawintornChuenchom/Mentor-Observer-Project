import json


def parse_json(raw: str) -> dict:
    """แปลง output ของ LLM เป็น dict

    strict=False สำคัญมาก — Gemini มักใส่ newline/tab ตัวจริงในค่า string ของ JSON
    ซึ่ง json.loads ปกติจะ reject ("Invalid control character") ทำให้ทั้งระบบพัง
    """
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw[3:]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw, strict=False)
