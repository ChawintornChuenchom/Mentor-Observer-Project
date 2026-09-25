"""อ่าน logs/runs/*.csv สรุปคุณภาพ + cache hit ของรอบ setup/resynth/session ล่าสุดต่อบทเรียน

ใช้ตรวจแบบ read-only ไม่เรียก LLM เพิ่ม — รันหลัง python resynthesize.py / main.py / mentor.py
เพื่อดูว่า cache ทำงานจริงไหม (cache hit ควรสูงตั้งแต่ขั้น lo_rubric เป็นต้นไป) และมีคำเตือนจาก
การตรวจกันหลอนของ Synthesizer (evidence_chunks/rubric/soft skill ไม่ครบ) หรือไม่
"""
import csv
import glob
import io
import os
import sys

RUNS_DIR = "logs/runs"
CACHE_STEPS = ("select_groups", "lo_rubric", "rubric_remerge", "softskills")


def _read_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _cache_ratio(rows: list[dict], step: str) -> float | None:
    total_in = total_cached = 0
    for r in rows:
        if r["step"] == step and r["event"] == "cost":
            total_in     += int(r["in_tok"] or 0)
            total_cached += int(r["cached_tok"] or 0)
    return (total_cached / total_in) if total_in else None


def report_file(path: str):
    rows = _read_rows(path)
    print(f"\n{os.path.basename(path)}")
    if not rows:
        print("  (ว่าง)")
        return

    process = rows[0]["process"]
    warns   = [r for r in rows if r["event"] == "warn"]
    summary = next((r for r in rows if r["step"] == "summary"), None)

    print(f"  process={process}")
    if summary:
        print(f"  cost tracked: ${float(summary['cost_usd'] or 0):.6f}  ({summary['detail']})")

    for step in CACHE_STEPS:
        ratio = _cache_ratio(rows, step)
        if ratio is not None:
            note = "" if ratio > 0 else "  ⚠️  cache miss ทั้งขั้น"
            print(f"  cache hit {step}: {ratio:.0%}{note}")

    if warns:
        print(f"  ⚠️  {len(warns)} คำเตือนจากการตรวจกันหลอน:")
        for w in warns:
            print(f"     [{w['step']}] {w['detail']}")
    else:
        print("  ✅ ไม่มีคำเตือนจากการตรวจกันหลอน")


def latest_per_lesson(paths: list[str]) -> dict[str, str]:
    """กลุ่มไฟล์ตาม (process, subject, lesson) — เก็บเฉพาะไฟล์ล่าสุด (ชื่อไฟล์มี timestamp
    ต่อท้ายแบบ YYYYmmdd_HHMMSS ซึ่งเรียงตามตัวอักษรได้ตรงกับเวลาอยู่แล้ว)"""
    latest = {}
    for p in sorted(paths):
        base = os.path.basename(p)[:-4]          # ตัด .csv
        parts = base.split("_")
        key = "_".join(parts[:-2]) if len(parts) > 2 else base   # ตัด YYYYmmdd, HHMMSS ท้ายสุด
        latest[key] = p                            # sorted ตามชื่อ = ตามเวลา ตัวหลังทับตัวก่อน
    return latest


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    paths = glob.glob(f"{RUNS_DIR}/*.csv")
    if not paths:
        print(f"ไม่พบ run log ใน {RUNS_DIR}/ — ยังไม่เคยรัน main.py / resynthesize.py / mentor.py "
              "หลังอัปเดตเป็นเวอร์ชันที่มี run log")
        return

    for key, path in sorted(latest_per_lesson(paths).items()):
        report_file(path)


if __name__ == "__main__":
    main()
