import csv
import os

from run_log import RunLog


class FakeTracker:
    def __init__(self, balances):
        self._balances = list(balances)

    def _remaining(self):
        return self._balances.pop(0) if self._balances else self._balances[-1]


def read_rows(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_event_writes_immediately(tmp_path):
    log = RunLog(process="setup", subject="ชีวะ", lesson="บทที่ 1", base_dir=str(tmp_path))
    log.event("select_groups", "result", detail="groups=['P03']")
    log.event("lo_rubric", "cost", model="anthropic/x", in_tok=100, cached_tok=40,
             out_tok=20, cost_usd=0.001234)

    rows = read_rows(log.path)
    assert len(rows) == 2
    assert rows[0]["step"] == "select_groups"
    assert rows[0]["detail"] == "groups=['P03']"
    assert rows[1]["in_tok"] == "100"
    assert rows[1]["cost_usd"] == "0.001234"
    log.close()


def test_close_writes_summary_with_totals(tmp_path):
    tracker = FakeTracker([10.0, 9.5])
    log = RunLog(process="setup", subject="s", lesson="l", tracker=tracker, base_dir=str(tmp_path))
    log.event("a", "cost", cost_usd=0.1, in_tok=10, out_tok=5)
    log.event("b", "cost", cost_usd=0.2, in_tok=20, out_tok=8)
    log.event("c", "info", detail="not a cost row")   # ไม่นับเข้า total (event != "cost")
    log.close()

    rows = read_rows(log.path)
    summary = rows[-1]
    assert summary["step"] == "summary"
    assert summary["event"] == "close"
    assert float(summary["cost_usd"]) == 0.3
    assert summary["in_tok"] == "30"
    assert "actual_spent=0.5" in summary["detail"]
    assert "tracked=0.3" in summary["detail"]
    assert "untracked=0.2" in summary["detail"]


def test_no_tracker_still_closes(tmp_path):
    log = RunLog(process="resynth", base_dir=str(tmp_path))
    log.event("x", "info")
    log.close()
    assert os.path.exists(log.path)
