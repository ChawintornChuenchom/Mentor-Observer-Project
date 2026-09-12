import csv
import os
from datetime import datetime
from dotenv import load_dotenv
from check_credits import get_remaining

load_dotenv()

# ราคา list price ($/1M tok) — ใช้เป็น fallback เมื่อ OpenRouter ไม่ส่ง usage.cost มา
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


def usage_fields(usage) -> tuple[int, int, int, float | None]:
    """ดึง (in_tok, out_tok, cached_tok, cost) จาก usage object ของ OpenRouter

    cost = ยอดที่ OpenRouter หักจริง (รวมส่วนลด prompt cache แล้ว) — None ถ้าไม่มี
    """
    if usage is None:
        return 0, 0, 0, None
    in_tok  = getattr(usage, "prompt_tokens", 0) or 0
    out_tok = getattr(usage, "completion_tokens", 0) or 0
    details = getattr(usage, "prompt_tokens_details", None)
    cached  = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    cost    = getattr(usage, "cost", None)
    return in_tok, out_tok, cached, cost


class CostTracker:
    def __init__(self):
        self.session_start    = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.total_spent      = 0.0
        self.ocr_cost         = 0.0
        self.embedding_cost   = 0.0
        self.synthesizer_cost = 0.0
        self.mentor_cost      = 0.0
        self.observer_cost    = 0.0
        self.cached_tok_total = 0
        self.chat_rows: list[dict] = []

    def _remaining(self) -> float | None:
        return get_remaining()

    def _print_cost_line(self, label: str, in_tok: int, out_tok: int,
                         spent: float, cached: int = 0, estimated: bool = False):
        remaining = self._remaining()
        rem_str   = f"${remaining:.4f}" if remaining is not None else "N/A"
        cached_str = f" cached={cached}tok" if cached else ""
        tag        = "~" if estimated else ""          # ~ = ประมาณจาก list price
        print(f"  💰 {label}: in={in_tok}tok{cached_str} out={out_tok}tok "
              f"| จ่าย {tag}${spent:.6f} | เหลือ {rem_str}")

    def _spend(self, label, usage, price_in, price_out) -> tuple[float, int, int, int]:
        in_tok, out_tok, cached, real_cost = usage_fields(usage)
        if real_cost is not None:
            spent, estimated = real_cost, False
        else:
            ic, oc = calc_cost(in_tok, out_tok, price_in, price_out)
            spent, estimated = ic + oc, True
        self.total_spent      += spent
        self.cached_tok_total += cached
        self._print_cost_line(label, in_tok, out_tok, spent, cached, estimated)
        return spent, in_tok, out_tok, cached

    def track_ocr(self, in_tok: int, out_tok: int, cost: float | None = None):
        if cost is None:
            ic, oc = calc_cost(in_tok, out_tok, PRICE_OCR_IN, PRICE_OCR_OUT)
            cost = ic + oc
            est = True
        else:
            est = False
        self.ocr_cost    += cost
        self.total_spent += cost
        self._print_cost_line("OCR", in_tok, out_tok, cost, 0, est)

    def track_embedding(self, in_tok: int, cost: float | None = None):
        if cost is None:
            ic, _ = calc_cost(in_tok, 0, PRICE_EMBED_IN, 0)
            cost, est = ic, True
        else:
            est = False
        self.embedding_cost += cost
        self.total_spent    += cost
        self._print_cost_line("Embedding", in_tok, 0, cost, 0, est)

    def track_synthesizer(self, usage):
        spent, *_ = self._spend("Synthesizer", usage, PRICE_SYNTH_IN, PRICE_SYNTH_OUT)
        self.synthesizer_cost += spent

    def track_mentor(self, usage, mentor_msg: str, student_msg: str) -> dict:
        spent, in_tok, out_tok, cached = self._spend(
            "Mentor", usage, PRICE_MENTOR_IN, PRICE_MENTOR_OUT
        )
        self.mentor_cost += spent
        remaining = self._remaining() or 0.0

        row = {
            "timestamp":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ment_mes":   mentor_msg,
            "stu_mes":    student_msg,
            "obs_score":  "",
            "in_tok":     in_tok,
            "cached_tok": cached,
            "out_tok":    out_tok,
            "cost":       round(spent, 8),
            "price_left": round(remaining, 6),
        }
        self.chat_rows.append(row)
        return row

    def track_observer(self, usage, obs_score_summary: str,
                       row_ref: dict | None = None):
        spent, *_ = self._spend("Observer", usage, PRICE_OBSERVER_IN, PRICE_OBSERVER_OUT)
        self.observer_cost += spent
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
            "in_tok", "cached_tok", "out_tok", "cost", "price_left"
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
        print(f"  💰 สรุปค่าใช้จ่าย (จ่ายจริงจาก OpenRouter รวมส่วนลด cache)")
        print(f"{'=' * 55}")
        print(f"  OCR:         ${self.ocr_cost:.6f}")
        print(f"  Embedding:   ${self.embedding_cost:.6f}")
        print(f"  Synthesizer: ${self.synthesizer_cost:.6f}")
        print(f"  Mentor:      ${self.mentor_cost:.6f}")
        print(f"  Observer:    ${self.observer_cost:.6f}")
        print(f"  {'─' * 30}")
        print(f"  รวมทั้งหมด:  ${self.total_spent:.6f}  (~{self.total_spent * THB_PER_USD:.2f} บาท)")
        if self.cached_tok_total:
            print(f"  prompt cache hit: {self.cached_tok_total} tok")
        print(f"  เหลือ:       {rem_str}")
        print(f"{'=' * 55}")
