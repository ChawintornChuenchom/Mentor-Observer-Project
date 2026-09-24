"""เก็บประวัติการให้คะแนนทั้งหมดใน SQLite

soft_observations = history ของ scoring engine (ผลดิบ append-only)
skill_state        = cache ของ x/P ต่อ ผู้เรียน × สกิล สร้างใหม่ได้เสมอด้วย recompute_all()
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from scoring.kalman import ScoringEngine, get_engine, load_config, recompute_from_history

DEFAULT_DB_PATH = Path("data") / "scores.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    student_id      TEXT NOT NULL,
    lesson_id       TEXT NOT NULL,
    character       TEXT,
    objectives_hash TEXT,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    transcript_json TEXT
);

CREATE TABLE IF NOT EXISTS hard_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(session_id),
    timestamp   TEXT NOT NULL,
    turn        INTEGER,
    lo_id       TEXT NOT NULL,
    score       INTEGER,
    evidence    TEXT,
    trigger_lo  TEXT            -- JSON list ของ LO ที่ Mentor สั่งตรวจรอบนั้น
);

CREATE TABLE IF NOT EXISTS hard_results (
    session_id  TEXT NOT NULL REFERENCES sessions(session_id),
    lo_id       TEXT NOT NULL,
    score       INTEGER,
    PRIMARY KEY (session_id, lo_id)
);

CREATE TABLE IF NOT EXISTS soft_observations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id     TEXT NOT NULL,
    session_id     TEXT NOT NULL,
    lesson_id      TEXT NOT NULL,
    timestamp      TEXT NOT NULL,
    skill_id       TEXT NOT NULL,
    level          INTEGER,
    label          TEXT,
    evidence       TEXT NOT NULL,
    summary        TEXT,
    engine_version TEXT NOT NULL,
    UNIQUE (student_id, session_id, skill_id)
);

CREATE TABLE IF NOT EXISTS skill_state (
    student_id     TEXT NOT NULL,
    skill_id       TEXT NOT NULL,
    x              REAL NOT NULL,
    P              REAL NOT NULL,
    n_obs          INTEGER NOT NULL,
    n_sessions     INTEGER NOT NULL,
    engine         TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (student_id, skill_id)
);
"""

OBS_FIELDS = ("session_id", "lesson_id", "timestamp", "skill_id", "level", "label", "evidence")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ScoreStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, config: dict | None = None,
                 engine: ScoringEngine | None = None):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn   = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.config = config or load_config()
        self.engine = engine or get_engine(self.config)

    def close(self):
        self.conn.close()

    # ── session ─────────────────────────────────────────────
    def start_session(self, session_id: str, student_id: str, lesson_id: str,
                      character: str, objectives_hash: str):
        with self.conn:
            self.conn.execute(
                "INSERT INTO sessions (session_id, student_id, lesson_id, character, objectives_hash, started_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, student_id, lesson_id, character, objectives_hash, now_iso()),
            )

    def record_hard_event(self, session_id: str, turn: int, lo_id: str, score: int | None,
                          evidence: str, trigger_lo: list[str]):
        """ผลดิบจาก Observer ทุกครั้งที่ trigger (ก่อนผ่าน guard ของ Mentor)"""
        with self.conn:
            self.conn.execute(
                "INSERT INTO hard_events (session_id, timestamp, turn, lo_id, score, evidence, trigger_lo)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, now_iso(), turn, lo_id, score, evidence, json.dumps(trigger_lo)),
            )

    def finish_session(self, session_id: str, transcript: list[dict],
                       hard_max: dict[str, int | None], soft_obs: list[dict]) -> dict[str, dict]:
        """บันทึกผลตอนจบ session และอัปเดตคะแนนสะสม คืน to_display ต่อสกิล"""
        session = self.conn.execute(
            "SELECT student_id FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        student_id = session["student_id"]
        displays = {}

        with self.conn:
            self.conn.execute(
                "UPDATE sessions SET ended_at = ?, transcript_json = ? WHERE session_id = ?",
                (now_iso(), json.dumps(transcript, ensure_ascii=False), session_id),
            )
            for lo_id, score in hard_max.items():
                self.conn.execute(
                    "INSERT OR REPLACE INTO hard_results (session_id, lo_id, score) VALUES (?, ?, ?)",
                    (session_id, lo_id, score),
                )
            for obs in soft_obs:
                state = self._load_state(student_id, obs["skill_id"])
                state = self.engine.update(state, {k: obs.get(k) for k in OBS_FIELDS}, self.config)
                self.conn.execute(
                    "INSERT OR IGNORE INTO soft_observations"
                    " (student_id, session_id, lesson_id, timestamp, skill_id, level, label,"
                    "  evidence, summary, engine_version)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (student_id, obs["session_id"], obs["lesson_id"], obs["timestamp"],
                     obs["skill_id"], obs.get("level"), obs.get("label"), obs["evidence"],
                     obs.get("summary"), self.config["engine_version"]),
                )
                self._save_state(student_id, obs["skill_id"], state)
                displays[obs["skill_id"]] = self.engine.to_display(state, self.config)

        return displays

    # ── state ───────────────────────────────────────────────
    def _history(self, student_id: str, skill_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM soft_observations WHERE student_id = ? AND skill_id = ?"
            " ORDER BY timestamp, session_id",
            (student_id, skill_id),
        ).fetchall()
        return [{**{k: r[k] for k in OBS_FIELDS}, "engine_version": r["engine_version"]} for r in rows]

    def _load_state(self, student_id: str, skill_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM skill_state WHERE student_id = ? AND skill_id = ?",
            (student_id, skill_id),
        ).fetchone()
        if row is None:
            state = self.engine.initial_state(self.config)
        else:
            state = {"x": row["x"], "P": row["P"], "n_obs": row["n_obs"],
                     "n_sessions": row["n_sessions"]}
        state["history"] = self._history(student_id, skill_id)
        return state

    def _save_state(self, student_id: str, skill_id: str, state: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO skill_state"
            " (student_id, skill_id, x, P, n_obs, n_sessions, engine, engine_version, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (student_id, skill_id, state["x"], state["P"], state["n_obs"], state["n_sessions"],
             self.config["engine"], self.config["engine_version"], now_iso()),
        )

    def get_display(self, student_id: str, skill_id: str) -> dict:
        return self.engine.to_display(self._load_state(student_id, skill_id), self.config)

    def recompute_all(self) -> int:
        """สร้าง skill_state ใหม่ทั้งหมดจาก soft_observations ด้วย config ปัจจุบัน"""
        pairs = self.conn.execute(
            "SELECT DISTINCT student_id, skill_id FROM soft_observations"
        ).fetchall()
        with self.conn:
            for p in pairs:
                history = self._history(p["student_id"], p["skill_id"])
                state   = recompute_from_history(history, self.engine, self.config)
                self._save_state(p["student_id"], p["skill_id"], state)
        return len(pairs)
