"""ทดสอบ Synthesizer.synthesize() แบบ end-to-end ด้วย LLM ปลอม (ไม่เรียก API จริง)

ตรวจ: ลำดับขั้น (1 → 2 → 3 → 3.5 summary → 4), เนื้อหาเป็น message แรกเหมือนกันทุกขั้นที่ใช้ cache
(byte เดียวกัน), consolidate <= MAX_MAIN_LOS, summary สรุปจาก main_los ชุดสุดท้าย, temperature ทุกการเรียก,
CSV มีครบทุก step
"""
import csv
import json
from types import SimpleNamespace

from config import SYNTH_TEMPERATURE
from cost_tracker import CostTracker
from run_log import RunLog
from synthesizer import Synthesizer, MAX_MAIN_LOS, SUMMARY_PROMPT
import template_loader as tpl


class FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens, cost, cached_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost = cost
        self.prompt_tokens_details = SimpleNamespace(cached_tokens=cached_tokens)


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.kwargs = []

    def create(self, model, messages, **kwargs):
        self.calls.append(messages)
        self.kwargs.append(kwargs)
        content, usage = self.responses.pop(0)
        message = SimpleNamespace(content=content)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice], usage=usage, model=model)


class FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


def usage(cached=0):
    return FakeUsage(1000, 200, 0.002, cached_tokens=cached)


def make_main_lo(i):
    return {
        "id": f"s{i}", "statement": f"stmt{i}", "tag": "core", "type": "conceptual",
        "source_chunks": ["c1"], "prompt_group": "P10", "content_type": "THAI-GRM",
        "observable_evidence": "e", "mentor_activity": "m",
        "rubric": {"0": "r0", "1": "r1", "2": "r2", "3": "r3"},
    }


SKILLS = list(tpl.softskills())
GROUPS_RESP = (json.dumps({"prompt_groups": ["P10"], "group_reason": "r", "dropped_groups": []}),
               usage(0))
SELECT_RESP = {
    "softskills": [
        {"id": "S01", "linked_main_los": ["s1"], "reason": "s1 ให้วิเคราะห์",
         "required_activity": {"main_lo": "s1", "how": "ให้วิจารณ์ข้อสรุป"}},
        {"id": "S02", "linked_main_los": ["s1"], "reason": "s1 มีโจทย์",
         "required_activity": {"main_lo": "s1", "how": "ให้โจทย์ที่ยังไม่บอกวิธี"}},
    ],
    "softskills_not_selected": [
        {"id": sid, "reason": "ไม่มี main_lo"} for sid in SKILLS if sid not in ("S01", "S02")
    ],
}
S01_RESP = {"feasible": True, "lesson_indicators": {"1": "a", "2": "b", "3": "c", "4": "d", "5": "e"}}
S02_RESP = {"feasible": False, "reason": "ไม่มีโจทย์หลายขั้นในเนื้อหา"}


def j(obj):
    return json.dumps(obj, ensure_ascii=False), usage(0)


def setup(tmp_path, responses):
    lesson_dir = tmp_path / "lessons" / "ทดสอบ" / "บท1"
    lesson_dir.mkdir(parents=True)
    (lesson_dir / "content.txt").write_text("เนื้อหาบทเรียนทดสอบ " * 20, encoding="utf-8")
    client  = FakeClient(responses)
    tracker = CostTracker()
    tracker.run_log = RunLog(process="setup", subject="t", lesson="l",
                             base_dir=str(tmp_path / "logs" / "runs"))
    return lesson_dir, client, tracker


def system_text(call) -> str:
    content = call[0]["content"]
    return content if isinstance(content, str) else "".join(b["text"] for b in content)


def test_synthesize_full_pipeline(tmp_path):
    main_los = [make_main_lo(i) for i in range(1, 8)]   # 7 ข้อ > MAX_MAIN_LOS(6) → ต้อง consolidate
    lo_rubric_resp = {"lesson_title": "ทดสอบ", "summary": "หลักเก่าจากขั้น 2", "main_los": main_los,
                      "missing_coverage": ["เรื่องที่วัดไม่ได้"]}
    consolidate_resp = {"merged_groups": [
        {"from_ids": ["s2", "s3"], "new_statement": "รวม s2 s3", "tag": "core", "type": "conceptual"}
    ]}
    rubric_remerge_resp = {"rubrics": [
        {"id": "s2", "observable_evidence": "e2", "mentor_activity": "m2",
         "rubric": {"0": "r0", "1": "r1", "2": "r2", "3": "r3"}}
    ]}

    lesson_dir, client, tracker = setup(tmp_path, [
        GROUPS_RESP,
        (json.dumps(lo_rubric_resp, ensure_ascii=False), usage(500)),
        j(consolidate_resp),
        (json.dumps(rubric_remerge_resp), usage(500)),
        j({"summary": "หลักใหม่จาก main_los"}),
        j(SELECT_RESP), j(S01_RESP), j(S02_RESP),
    ])
    objectives = Synthesizer(client, cost_tracker=tracker).synthesize(str(lesson_dir))
    calls = client.chat.completions.calls

    assert "schema_version" not in objectives
    assert len(objectives["main_los"]) <= MAX_MAIN_LOS
    assert [s["id"] for s in objectives["softskills"]] == ["S01"]
    not_sel = {s["id"]: s["reason"] for s in objectives["softskills_not_selected"]}
    assert not_sel["S02"] == "4B: ไม่มีโจทย์หลายขั้นในเนื้อหา"
    assert len(not_sel) == len(SKILLS) - 1

    merged = next(lo for lo in objectives["main_los"] if lo["statement"] == "รวม s2 s3")
    assert merged["rubric"] == {"0": "r0", "1": "r1", "2": "r2", "3": "r3"}
    assert merged["observable_evidence"] == "e2"

    # ขั้น 2: prompt ไม่มี summary · summary ที่โมเดลส่งมาถูกทิ้ง → ค่าสุดท้ายมาจากขั้น 3.5
    assert "summary" not in system_text(calls[1])
    assert objectives["summary"] == "หลักใหม่จาก main_los"
    assert list(objectives)[:2] == ["lesson_title", "summary"]

    # rubric_remerge ไม่ได้รับ summary
    assert "summary:" not in system_text(calls[3])

    # ขั้น 3.5: หลัง consolidate ได้ main_los ชุดหลังรวม + missing_coverage · ไม่มีเนื้อหาบท/cache_control
    main_call = calls[4]
    assert main_call[0]["content"] == SUMMARY_PROMPT
    assert "รวม s2 s3" in main_call[1]["content"] and "stmt3" not in main_call[1]["content"]
    assert "เรื่องที่วัดไม่ได้" in main_call[1]["content"]
    assert "เนื้อหาบทเรียนทดสอบ" not in json.dumps(main_call, ensure_ascii=False)

    # ขั้น 4A รันหลังขั้น 3.5 และได้ summary ใหม่
    assert "หลักใหม่จาก main_los" in system_text(calls[5])

    # temperature ทุกการเรียก
    assert all(kw.get("temperature") == SYNTH_TEMPERATURE == 0
               for kw in client.chat.completions.kwargs)

    # เนื้อหา (block แรกของทุกขั้นที่ผ่าน _ask_cached) ต้องเป็น byte เดียวกันทุกครั้ง —
    # นี่คือสิ่งที่ทำให้ prompt cache hit ได้ตั้งแต่ขั้น 2 เป็นต้นไป
    cached_calls = [c for c in calls if isinstance(c[0]["content"], list)]
    assert len(cached_calls) == 3   # select_groups, lo_rubric, rubric_remerge
    assert len({c[0]["content"][0]["text"] for c in cached_calls}) == 1
    assert all(c[0]["content"][0].get("cache_control") for c in cached_calls)

    tracker.run_log.close()
    rows  = list(csv.DictReader(open(tracker.run_log.path, encoding="utf-8-sig")))
    steps = {r["step"] for r in rows}
    assert {"select_groups", "lo_rubric", "consolidate", "rubric_remerge", "summary",
            "soft_select", "soft_indicator_S01", "soft_indicator_S02"} <= steps
    assert any(r["step"] == "lo_rubric" and r["event"] == "warn" and "ทิ้ง summary" in r["detail"]
               for r in rows)
    call_rows = [r for r in rows if r["event"] == "call"]
    assert len(call_rows) == len(calls)
    assert all(r["detail"] == f"temperature={SYNTH_TEMPERATURE}" and r["model"] for r in call_rows)

    assert (lesson_dir / "objectives.json").exists()


def test_synthesize_within_cap_skips_consolidate_and_summary_failure_is_empty(tmp_path):
    main_los = [make_main_lo(i) for i in range(1, 5)]   # 4 ข้อ ≤ 6 → ไม่เรียก consolidate/remerge
    lo_rubric_resp = {"lesson_title": "ทดสอบ", "main_los": main_los, "missing_coverage": []}
    lesson_dir, client, tracker = setup(tmp_path, [
        GROUPS_RESP,
        (json.dumps(lo_rubric_resp, ensure_ascii=False), usage(500)),
        ("ไม่ใช่ json", usage(0)),        # summary ครั้งที่ 1
        j({"summary": ""}),               # summary ครั้งที่ 2 (ว่าง)
        j(SELECT_RESP), j(S01_RESP), j(S02_RESP),
    ])
    objectives = Synthesizer(client, cost_tracker=tracker).synthesize(str(lesson_dir))
    calls = client.chat.completions.calls

    assert len(calls) == 7
    assert objectives["summary"] == ""
    assert [{k: v for k, v in lo.items()} for lo in objectives["main_los"]] == main_los

    tracker.run_log.close()
    rows = list(csv.DictReader(open(tracker.run_log.path, encoding="utf-8-sig")))
    skip = next(r for r in rows if r["step"] == "consolidate")
    assert skip["event"] == "skip" and json.loads(skip["detail"])["skipped"] is True
    assert not any(r["step"] == "rubric_remerge" for r in rows)
    assert any(r["step"] == "summary" and r["event"] == "warn" for r in rows)
    assert (lesson_dir / "objectives.json").exists()
