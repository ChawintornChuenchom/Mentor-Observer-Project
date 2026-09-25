"""Run log — เขียนทุกขั้นตอนของ process (setup/resynth/session) ลง CSV ทันทีที่เกิดขึ้น

ต่างจาก chat CSV เดิม (เขียนตอน quit เท่านั้น จึงหายถ้าปิด terminal กลางคัน) — ที่นี่ event()
flush ลงไฟล์ทันทีทุกครั้ง และมีทุก process (setup/resynth) ไม่ใช่แค่ session ของ Mentor
"""
import csv
import os
import re
from datetime import datetime

from cost_tracker import THB_PER_USD

FIELDS = [
    "timestamp", "process", "step", "event", "detail",
    "model", "in_tok", "cached_tok", "out_tok",
    "cost_usd", "cost_thb", "balance_usd",
]


def _safe(name: str) -> str:
    name = re.sub(r"[^0-9A-Za-zก-๙_\-]+", "_", name or "").strip("_")
    return name or "x"


class RunLog:
    def __init__(self, process: str, subject: str = "", lesson: str = "", tracker=None,
                base_dir: str = "logs/runs"):
        self.process = process
        self.subject = subject
        self.lesson  = lesson
        self.tracker = tracker
        self._totals = {"in_tok": 0, "cached_tok": 0, "out_tok": 0, "cost_usd": 0.0}
        self.start_balance = tracker._remaining() if tracker is not None else None

        os.makedirs(base_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = f"{base_dir}/{process}_{_safe(subject)}_{_safe(lesson)}_{ts}.csv"

        self._fh     = open(self.path, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.DictWriter(self._fh, fieldnames=FIELDS)
        self._writer.writeheader()

    def event(self, step: str, event: str, detail: str = "", model: str = "",
             in_tok: int = 0, cached_tok: int = 0, out_tok: int = 0, cost_usd: float = 0.0):
        cost_usd = round(cost_usd, 8)
        self._writer.writerow({
            "timestamp":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "process":     self.process,
            "step":        step,
            "event":       event,
            "detail":      detail,
            "model":       model,
            "in_tok":      in_tok,
            "cached_tok":  cached_tok,
            "out_tok":     out_tok,
            "cost_usd":    cost_usd,
            "cost_thb":    round(cost_usd * THB_PER_USD, 4),
            "balance_usd": "",
        })
        self._fh.flush()

        if event == "cost":
            self._totals["in_tok"]     += in_tok
            self._totals["cached_tok"] += cached_tok
            self._totals["out_tok"]    += out_tok
            self._totals["cost_usd"]   += cost_usd

        line = f"  · [{step}] {event}"
        if detail:
            line += f" — {detail}"
        print(line)

    def close(self):
        """แถวสรุปท้ายไฟล์: ยอดก่อน/หลังจริง (จาก OpenRouter), ผลรวม usage ที่ track ได้,
        และส่วนต่างที่ track ไม่ได้ (ตัวเลขไม่เป็น 0 พอดี = มี cost บางส่วนหลุด log ไป)"""
        end_balance = self.tracker._remaining() if self.tracker is not None else None
        tracked = round(self._totals["cost_usd"], 8)

        actual_spent = untracked = None
        if self.start_balance is not None and end_balance is not None:
            actual_spent = round(self.start_balance - end_balance, 8)
            untracked    = round(actual_spent - tracked, 8)

        detail = (
            f"start={self.start_balance} end={end_balance} "
            f"actual_spent={actual_spent} tracked={tracked} untracked={untracked} "
            f"in_tok={self._totals['in_tok']} cached_tok={self._totals['cached_tok']} "
            f"out_tok={self._totals['out_tok']}"
        )
        self._writer.writerow({
            "timestamp":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "process":     self.process,
            "step":        "summary",
            "event":       "close",
            "detail":      detail,
            "model":       "",
            "in_tok":      self._totals["in_tok"],
            "cached_tok":  self._totals["cached_tok"],
            "out_tok":     self._totals["out_tok"],
            "cost_usd":    tracked,
            "cost_thb":    round(tracked * THB_PER_USD, 4),
            "balance_usd": end_balance if end_balance is not None else "",
        })
        self._fh.close()

        print(f"  📄 run log: {self.path}")
        if untracked is not None:
            print(f"     จ่ายจริง ${actual_spent:.6f} | track ได้ ${tracked:.6f} "
                  f"| ส่วนต่าง ${untracked:.6f}")
