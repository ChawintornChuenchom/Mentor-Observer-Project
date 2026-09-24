import copy
import random
from datetime import datetime, timedelta, timezone

import pytest

from scoring.kalman import KalmanEngine, load_config, recompute_from_history

TOL = 0.002
ENGINE = KalmanEngine()
CONFIG = load_config()
T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone(timedelta(hours=7)))


def obs(i: int, level, evidence: str) -> dict:
    return {
        "session_id": f"sess_{i:04d}",
        "lesson_id":  "BIO-101-L01",
        "timestamp":  (T0 + timedelta(days=i)).isoformat(),
        "skill_id":   "S03",
        "level":      level,
        "label":      None,
        "evidence":   evidence,
    }


def ne(i: int) -> dict:
    return {**obs(i, None, "none"), "label": "N/E"}


def run(*steps) -> dict:
    """steps: (level, evidence) หรือ "NE" """
    state = ENGINE.initial_state(CONFIG)
    for i, step in enumerate(steps):
        o = ne(i) if step == "NE" else obs(i, *step)
        state = ENGINE.update(state, o, CONFIG)
    return state


def approx(v):
    return pytest.approx(v, abs=TOL)


def test_t1_single_strong_5():
    s = run((5, "strong"))
    assert s["x"] == approx(4.882)
    assert s["P"] == approx(0.235)
    assert s["n_obs"] == 1


def test_t2_single_strong_4():
    s = run((4, "strong"))
    assert s["x"] == approx(3.941)
    assert s["P"] == approx(0.235)


def test_t3_strong5_moderate4():
    s = run((5, "strong"), (4, "moderate"))
    assert s["x"] == approx(4.666)
    assert s["P"] == approx(0.245)


def test_t4_strong4_strong5():
    s = run((4, "strong"), (5, "strong"))
    assert s["x"] == approx(4.540)
    assert s["P"] == approx(0.141)


def test_t5_strong5_then_ne():
    s = run((5, "strong"), "NE")
    assert s["x"] == approx(4.882)
    assert s["P"] == approx(0.325)
    assert s["n_obs"] == 1
    assert s["n_sessions"] == 2


def test_t6_strong5_ne_weak2():
    s = run((5, "strong"), "NE", (2, "weak"))
    assert s["x"] == approx(4.433)
    assert s["P"] == approx(0.351)


def test_t7_strong5_moderate4_strong5():
    s = run((5, "strong"), (4, "moderate"), (5, "strong"))
    assert s["x"] == approx(4.857)
    assert s["P"] == approx(0.143)


def test_t8_strong4_strong5_strong5():
    s = run((4, "strong"), (5, "strong"), (5, "strong"))
    assert s["x"] == approx(4.761)
    assert s["P"] == approx(0.120)


def test_t9_each_step():
    steps = [(5, "strong"), (1, "strong"), (5, "strong"), (5, "strong")]
    expected_x = [4.882, 2.687, 3.799, 4.347]
    state = ENGINE.initial_state(CONFIG)
    for i, (step, ex) in enumerate(zip(steps, expected_x)):
        state = ENGINE.update(state, obs(i, *step), CONFIG)
        assert state["x"] == approx(ex)
    assert state["P"] == approx(0.114)


def test_t10_only_ne():
    s = run("NE")
    assert s["x"] == approx(3.000)
    assert s["P"] == approx(4.000)
    assert s["n_obs"] == 0
    assert ENGINE.to_display(s, CONFIG)["reportable"] is False


def test_t11_p_never_exceeds_p0():
    s = run((5, "strong"), *["NE"] * 50)
    assert s["P"] <= CONFIG["P0"]
    assert s["n_sessions"] == 51


def test_t12_duplicate_session_id():
    s1 = run((5, "strong"))
    before = copy.deepcopy(s1)
    s2 = ENGINE.update(s1, obs(0, 1, "strong"), CONFIG)
    assert s2 == before
    assert len(s2["history"]) == 1
    assert s1 == before  # state เดิมไม่ถูกแก้


def test_update_does_not_mutate_input():
    s = ENGINE.initial_state(CONFIG)
    before = copy.deepcopy(s)
    ENGINE.update(s, obs(0, 5, "strong"), CONFIG)
    assert s == before


def test_t13_recompute_shuffled_equals_t9():
    t9 = run((5, "strong"), (1, "strong"), (5, "strong"), (5, "strong"))
    shuffled = t9["history"][:]
    random.Random(42).shuffle(shuffled)
    assert [h["session_id"] for h in shuffled] != [h["session_id"] for h in t9["history"]]

    r = recompute_from_history(shuffled, ENGINE, CONFIG)
    assert r["x"] == t9["x"]
    assert r["P"] == t9["P"]
    assert r["n_obs"] == t9["n_obs"]
    assert r["n_sessions"] == t9["n_sessions"]
    assert r["history"] == t9["history"]


def test_t14_display_after_t8():
    d = ENGINE.to_display(run((4, "strong"), (5, "strong"), (5, "strong")), CONFIG)
    assert d["reportable"] is True
    assert d["score"] == 4.76
    assert d["level"] == 5
    assert d["sigma"] == 0.35
    assert d["confidence"] == "high"


def test_t15_display_after_t3():
    d = ENGINE.to_display(run((5, "strong"), (4, "moderate")), CONFIG)
    assert d["reportable"] is False
    assert d["message"] == "ข้อมูลยังไม่พอ"


def test_level_rounds_half_up():
    state = {**ENGINE.initial_state(CONFIG), "x": 4.5, "P": 0.1, "n_obs": 3}
    assert ENGINE.to_display(state, CONFIG)["level"] == 5
