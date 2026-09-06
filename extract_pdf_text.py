import csv
import os
import sys
from datetime import datetime
from pathlib import Path
from openai import OpenAI
from config import OPENROUTER_API_KEY
from cost_tracker import THB_PER_USD
from check_credits import get_remaining
from rag import extract_text


def main():
    if len(sys.argv) > 1:
        targets = [Path(sys.argv[1])]
    else:
        targets = list(Path("lessons").rglob("*.pdf"))

    if not targets:
        print("ไม่พบไฟล์ PDF")
        return

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY
    )

    balance_before = get_remaining()
    print(f"ยอดเงินก่อนเริ่ม: {'$%.4f' % balance_before if balance_before is not None else 'N/A'}")

    for pdf_path in targets:
        txt_path = pdf_path.with_suffix(".txt")
        text = extract_text(str(pdf_path), client)
        txt_path.write_text(text, encoding="utf-8")
        print(f"[OK] {pdf_path} -> {txt_path}")

    balance_after = get_remaining()
    print(f"ยอดเงินหลังเสร็จ: {'$%.4f' % balance_after if balance_after is not None else 'N/A'}")

    if balance_before is None or balance_after is None:
        print("ไม่สามารถคำนวณค่าใช้จ่ายได้ (ดึงยอดเงินจาก OpenRouter ไม่สำเร็จ)")
        return

    spent_usd = balance_before - balance_after
    spent_thb = spent_usd * THB_PER_USD
    print(f"ค่าใช้จ่ายรวม: ${spent_usd:.4f} (~{spent_thb:.2f} บาท)")

    os.makedirs("cost", exist_ok=True)
    csv_path = f"cost/extract_pdf_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "timestamp", "files", "balance_before_usd", "balance_after_usd",
            "spent_usd", "thb_per_usd", "spent_thb"
        ])
        writer.writeheader()
        writer.writerow({
            "timestamp":          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "files":              "; ".join(p.name for p in targets),
            "balance_before_usd": round(balance_before, 6),
            "balance_after_usd":  round(balance_after, 6),
            "spent_usd":          round(spent_usd, 6),
            "thb_per_usd":        THB_PER_USD,
            "spent_thb":          round(spent_thb, 2)
        })

    print(f"บันทึกค่าใช้จ่าย: {csv_path}")


if __name__ == "__main__":
    main()
