from scoring.store import ScoreStore


def soft(session_id: str, day: int, skill_id: str, level, evidence: str) -> dict:
    return {
        "session_id": session_id,
        "lesson_id":  "ชีวะ/บท 1",
        "timestamp":  f"2026-09-{day:02d}T10:00:00+07:00",
        "skill_id":   skill_id,
        "level":      level,
        "label":      None if level else "N/E",
        "evidence":   evidence,
        "summary":    "...",
    }


def run_session(store: ScoreStore, sid: str, day: int, level, evidence: str, hard: dict):
    store.start_session(sid, "stu01", "ชีวะ/บท 1", "Ken", "hash")
    store.record_hard_event(sid, 1, "s1", 1, "e", ["s1"])
    return store.finish_session(sid, [{"role": "user", "content": "x"}], hard,
                                [soft(sid, day, "S04", level, evidence)])


def test_incremental_matches_recompute(tmp_path):
    store = ScoreStore(tmp_path / "t.db")
    run_session(store, "a", 1, 4, "strong", {"s1": 3})
    run_session(store, "b", 2, None, "none", {"s1": 2})
    d = run_session(store, "c", 3, 5, "strong", {"s1": None})
    before = store.get_display("stu01", "S04")
    assert d["S04"] == before

    store.config = {**store.config, "Q": 0.0}
    store.recompute_all()
    after_q0 = store.get_display("stu01", "S04")
    assert after_q0["score"] != before["score"]

    rows = store.conn.execute("SELECT COUNT(*) FROM soft_observations").fetchone()[0]
    assert rows == 3
    store.close()


def test_hard_results_saved(tmp_path):
    store = ScoreStore(tmp_path / "t.db")
    run_session(store, "a", 1, 3, "moderate", {"s1": 3, "s2": None})
    rows = dict(store.conn.execute("SELECT lo_id, score FROM hard_results").fetchall())
    assert rows == {"s1": 3, "s2": None}
    assert store.conn.execute("SELECT COUNT(*) FROM hard_events").fetchone()[0] == 1
    store.close()
