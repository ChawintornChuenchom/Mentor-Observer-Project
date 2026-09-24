"""คำนวณคะแนน Soft Skill สะสมต่อ ผู้เรียน × สกิล

ทุกฟังก์ชันเป็นฟังก์ชันบริสุทธิ์: ไม่มี I/O (ยกเว้น load_config) ไม่สุ่ม ไม่แก้ state เดิม
history เก็บเฉพาะผลดิบจาก Observer จึงไม่ขึ้นกับ engine
"""
import copy
import json
import math
from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _round_half_up(value: float, digits: int = 0) -> float:
    q = Decimal(1).scaleb(-digits)
    return float(Decimal(repr(value)).quantize(q, rounding=ROUND_HALF_UP))


def _clamp(value: float, bounds: list[float]) -> float:
    lo, hi = bounds
    return max(lo, min(hi, value))


def is_not_evaluable(observation: dict) -> bool:
    return (
        observation.get("label") == "N/E"
        or observation.get("evidence") == "none"
        or observation.get("level") is None
    )


class ScoringEngine(ABC):
    @abstractmethod
    def initial_state(self, config: dict) -> dict: ...

    @abstractmethod
    def update(self, state: dict, observation: dict, config: dict) -> dict: ...

    @abstractmethod
    def to_display(self, state: dict, config: dict) -> dict: ...


class KalmanEngine(ScoringEngine):
    name = "kalman"

    def initial_state(self, config: dict) -> dict:
        return {
            "x":          float(config["x0"]),
            "P":          float(config["P0"]),
            "n_obs":      0,
            "n_sessions": 0,
            "history":    [],
        }

    def update(self, state: dict, observation: dict, config: dict) -> dict:
        new = copy.deepcopy(state)

        # 0. ตรวจความซ้ำ
        sid = observation.get("session_id")
        if any(h.get("session_id") == sid for h in new["history"]):
            return new

        # 1. บันทึกผลดิบ
        new["history"].append({**observation, "engine_version": config["engine_version"]})

        # 2. predict
        if new["n_sessions"] == 0:
            p_prior = new["P"]
        else:
            p_prior = min(new["P"] + config["Q"], config["P0"])
        x_prior = new["x"]
        new["n_sessions"] += 1

        # 3. N/E
        if is_not_evaluable(observation):
            new["x"], new["P"] = x_prior, p_prior
            return new

        # 4. update
        r = config["R"][observation["evidence"]]
        k = p_prior / (p_prior + r)
        x = x_prior + k * (observation["level"] - x_prior)
        new["P"] = (1 - k) * p_prior
        new["x"] = _clamp(x, config["clamp"])
        new["n_obs"] += 1
        return new

    def to_display(self, state: dict, config: dict) -> dict:
        x      = state["x"]
        sigma  = math.sqrt(state["P"])
        bounds = config["clamp"]
        reportable = state["n_obs"] >= config["min_obs_to_report"]

        if sigma < 0.5:
            confidence = "high"
        elif sigma < 0.8:
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "reportable": reportable,
            "score":      _round_half_up(x, 2),
            "level":      int(_round_half_up(x, 0)),
            "sigma":      _round_half_up(sigma, 2),
            "range":      [
                _round_half_up(_clamp(x - sigma, bounds), 2),
                _round_half_up(_clamp(x + sigma, bounds), 2),
            ],
            "confidence": confidence,
            "message":    None if reportable else "ข้อมูลยังไม่พอ",
        }


ENGINES: dict[str, type[ScoringEngine]] = {"kalman": KalmanEngine}


def get_engine(config: dict) -> ScoringEngine:
    return ENGINES[config["engine"]]()


def _sort_key(observation: dict) -> tuple:
    return (datetime.fromisoformat(observation["timestamp"]), observation["session_id"])


def recompute_from_history(history: list[dict], engine: ScoringEngine, config: dict) -> dict:
    """คำนวณใหม่ทั้งหมดจากผลดิบ โดยใช้ config ที่ส่งเข้ามา (ไม่ใช่ engine_version ที่บันทึกไว้)"""
    state = engine.initial_state(config)
    for obs in sorted(history, key=_sort_key):
        raw = {k: v for k, v in obs.items() if k != "engine_version"}
        state = engine.update(state, raw, config)
    return state
