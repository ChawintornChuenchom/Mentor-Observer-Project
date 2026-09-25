import os
import urllib.request
import json
from dotenv import load_dotenv

load_dotenv()

LOW_BALANCE_USD = float(os.getenv("LOW_BALANCE_USD", "5.0"))


def get_key_data(api_key: str | None = None) -> dict | None:
    """ดึงข้อมูลดิบของ key จาก OpenRouter (limit, usage, limit_remaining) — None ถ้าดึงไม่ได้"""
    key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {key}"}
        )
        return json.loads(urllib.request.urlopen(req).read())["data"]
    except Exception:
        return None


def get_remaining(api_key: str | None = None) -> float | None:
    """ดึงยอดเหลือจาก OpenRouter — ใช้ได้ทั้ง import และรันตรงๆ

    ไม่ระบุ api_key จะ fallback ไปที่ env OPENROUTER_API_KEY (ใช้ฝั่งครู/setup)
    ระบุ api_key เพื่อดูยอดของ key เฉพาะ (เช่น ของนักเรียนแต่ละคนที่มี key แยกกัน)
    """
    data = get_key_data(api_key)
    return data["limit_remaining"] if data else None


def _print_key_status(label: str, api_key: str | None):
    if not api_key:
        print(f"{label}: (ไม่มี key ตั้งไว้)")
        return
    data = get_key_data(api_key)
    if data is None:
        print(f"{label}: ⚠️  ดึงข้อมูลไม่ได้ (key ผิดหรือเน็ตมีปัญหา)")
        return
    remaining = data["limit_remaining"]
    flag = " ⚠️  ใกล้หมด!" if remaining is not None and remaining < LOW_BALANCE_USD else ""
    print(f"{label}: budget=${data['limit']:.2f} used=${data['usage']:.6f} "
          f"remaining=${remaining:.4f}{flag}")


if __name__ == "__main__":
    import io
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    from config import OPENROUTER_API_KEY, OPENROUTER_API_KEYS

    print(f"(เตือนถ้ายอดเหลือต่ำกว่า ${LOW_BALANCE_USD:.2f} — ปรับได้ด้วย env LOW_BALANCE_USD)\n")
    _print_key_status("ครู/setup (OPENROUTER_API_KEY)", OPENROUTER_API_KEY)
    for label, key in OPENROUTER_API_KEYS.items():
        _print_key_status(f"นักเรียน {label}", key)