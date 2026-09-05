import os
import urllib.request
import json
from dotenv import load_dotenv

load_dotenv()

def get_remaining() -> float | None:
    """ดึงยอดเหลือจาก OpenRouter — ใช้ได้ทั้ง import และรันตรงๆ"""
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}"}
        )
        data = json.loads(urllib.request.urlopen(req).read())["data"]
        return data["limit_remaining"]
    except Exception:
        return None


if __name__ == "__main__":
    data_raw = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}"}
            )
        ).read()
    )["data"]

    print(f"Budget:    ${data_raw['limit']:.2f}")
    print(f"Used:      ${data_raw['usage']:.6f}")
    print(f"Remaining: ${data_raw['limit_remaining']:.4f}")