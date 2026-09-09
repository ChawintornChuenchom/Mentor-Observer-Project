import csv
import os
import json
from datetime import datetime
from dotenv import load_dotenv
from check_credits import get_remaining

load_dotenv()

PRICE_MENTOR_IN    = float(os.getenv("PRICE_MENTOR_IN",    "0.30"))
PRICE_MENTOR_OUT   = float(os.getenv("PRICE_MENTOR_OUT",   "2.50"))
PRICE_OBSERVER_IN  = float(os.getenv("PRICE_OBSERVER_IN",  "1.00"))
PRICE_OBSERVER_OUT = float(os.getenv("PRICE_OBSERVER_OUT", "5.00"))
PRICE_SYNTH_IN     = float(os.getenv("PRICE_SYNTH_IN",     "0.30"))
PRICE_SYNTH_OUT    = float(os.getenv("PRICE_SYNTH_OUT",    "2.50"))
PRICE_EMBED_IN     = float(os.getenv("PRICE_EMBED_IN",     "0.02"))
PRICE_OCR_IN       = float(os.getenv("PRICE_OCR_IN",       "0.104"))
PRICE_OCR_OUT      = float(os.getenv("PRICE_OCR_OUT",      "0.416"))
THB_PER_USD        = float(os.getenv("THB_PER_USD",        "34.0"))


def calc_cost(in_tok: int, out_tok: int,
              price_in: float, price_out: float) -> tuple[float, float]:
    return (in_tok * price_in / 1_000_000,
            out_tok * price_out / 1_000_000)


class CostTracker:
    def __init__(self):
        self.session_start    = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.total_spent      = 0.0
        self.ocr_cost         = 0.0
        self.embedding_cost   = 0.0
        self.synthesizer_cost = 0.0
        self.mentor_cost      = 0.0
        self.observer_cost    = 0.0
        self.chat_rows: list[dict] = []

    def _remaining(self) -> float | None:
        return get_remaining()

    def _print_cost_line(self, label: str, in_tok: int, out_tok: int,
                         in_cost: float, out_cost: float):
        remaining = self._remaining()
        rem_str   = f"${remaining:.4f}" if remaining is not None else "N/A"
        total     = in_cost + out_cost
        print(f"  💰 {label}: in={in_tok}tok(${in_cost:.6f}) "
              f"out={out_tok}tok(${out_cost:.6f}) "
              f"total=${total:.6f} | เหลือ {rem_str}")

    def track_ocr(self, in_tok: int, out_tok: int):
        in_cost, out_cost = calc_cost(in_tok, out_tok,
                                      PRICE_OCR_IN, PRICE_OCR_OUT)
        self.ocr_cost    += in_cost + out_cost
        self.total_spent += in_cost + out_cost
        self._print_cost_line("OCR", in_tok, out_tok, in_cost, out_cost)

    def track_embedding(self, in_tok: int):
        in_cost, _ = calc_cost(in_tok, 0, PRICE_EMBED_IN, 0)
        self.embedding_cost += in_cost
        self.total_spent    += in_cost
        self._print_cost_line("Embedding", in_tok, 0, in_cost, 0.0)

    def track_synthesizer(self, in_tok: int, out_tok: int):
        in_cost, out_cost = calc_cost(in_tok, out_tok,
                                      PRICE_SYNTH_IN, PRICE_SYNTH_OUT)
        self.synthesizer_cost += in_cost + out_cost
        self.total_spent      += in_cost + out_cost
        self._print_cost_line("Synthesizer", in_tok, out_tok, in_cost, out_cost)

    def track_mentor(self, in_tok: int, out_tok: int,
                     mentor_msg: str, student_msg: str) -> dict:
        in_cost, out_cost = calc_cost(in_tok, out_tok,
                                      PRICE_MENTOR_IN, PRICE_MENTOR_OUT)
        self.mentor_cost += in_cost + out_cost
        self.total_spent += in_cost + out_cost
        remaining = self._remaining() or 0.0
        self._print_cost_line("Mentor", in_tok, out_tok, in_cost, out_cost)

        row = {
            "timestamp":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ment_mes":   mentor_msg,
            "stu_mes":    student_msg,
            "obs_score":  "",
            "in_tok":     in_tok,
            "out_tok":    out_tok,
            "in_price":   round(in_cost, 8),
            "out_price":  round(out_cost, 8),
            "price_left": round(remaining, 6)
        }
        self.chat_rows.append(row)
        return row

    def track_observer(self, in_tok: int, out_tok: int,
                       obs_score_summary: str, row_ref: dict | None = None):
        in_cost, out_cost = calc_cost(in_tok, out_tok,
                                      PRICE_OBSERVER_IN, PRICE_OBSERVER_OUT)
        self.observer_cost += in_cost + out_cost
        self.total_spent   += in_cost + out_cost
        self._print_cost_line("Observer", in_tok, out_tok, in_cost, out_cost)

        if row_ref is not None:
            row_ref["obs_score"] = obs_score_summary

    def save_chat_csv(self, subject: str, lesson: str, character: str):
        if not self.chat_rows:
            return

        os.makedirs("logs", exist_ok=True)
        filename = (f"logs/chat_{subject}_{lesson}_{character}"
                    f"_{self.session_start}.csv")

        fieldnames = [
            "timestamp", "ment_mes", "stu_mes", "obs_score",
            "in_tok", "out_tok", "in_price", "out_price", "price_left"
        ]

        with open(filename, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.chat_rows)

        print(f"\n  📄 บันทึก log: {filename}")

    def print_summary(self):
        remaining = self._remaining()
        rem_str   = f"${remaining:.4f}" if remaining is not None else "N/A"

        print(f"\n{'=' * 55}")
        print(f"  💰 สรุปค่าใช้จ่าย")
        print(f"{'=' * 55}")
        print(f"  OCR:         ${self.ocr_cost:.6f}")
        print(f"  Embedding:   ${self.embedding_cost:.6f}")
        print(f"  Synthesizer: ${self.synthesizer_cost:.6f}")
        print(f"  Mentor:      ${self.mentor_cost:.6f}")
        print(f"  Observer:    ${self.observer_cost:.6f}")
        print(f"  {'─' * 30}")
        print(f"  รวมทั้งหมด:  ${self.total_spent:.6f}")
        print(f"  เหลือ:       {rem_str}")
        print(f"{'=' * 55}")