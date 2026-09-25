"""ทดสอบ Synthesizer.synthesize() แบบ end-to-end ด้วย LLM ปลอม (ไม่เรียก API จริง)

ตรวจตาม docs/plan-synthesizer-runlog.md หัวข้อ Verification ข้อ 2:
ลำดับ 4 ขั้น, เนื้อหาเป็น message แรกเหมือนกันทุกขั้น (byte เดียวกัน), consolidate <= MAX_SUB_LOS,
CSV มีครบทุก step
"""
import csv
import json
from types import SimpleNamespace

from cost_tracker import CostTracker
from run_log import RunLog
from synthesizer import Synthesizer, MAX_SUB_LOS, SCHEMA_VERSION
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

    def create(self, model, messages, extra_body=None):
        self.calls.append(messages)
        content, usage = self.responses.pop(0)
        message = SimpleNamespace(content=content)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice], usage=usage)


class FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


def usage(cached=0):
    return FakeUsage(1000, 200, 0.002, cached_tokens=cached)


def make_sub_lo(i):
    return {
        "id": f"s{i}", "statement": f"stmt{i}", "tag": "core", "type": "conceptual",
        "evidence_chunks": ["c1"], "prompt_group": "P10", "content_type": "THAI-R",
        "observable_evidence": "e", "mentor_activity": "m",
        "rubric": {"0": "r0", "1": "r1", "2": "r2", "3": "r3"},
    }


def test_synthesize_full_pipeline(tmp_path):
    lesson_dir = tmp_path / "lessons" / "ทดสอบ" / "บท1"
    lesson_dir.mkdir(parents=True)
    (lesson_dir / "content.txt").write_text("เนื้อหาบทเรียนทดสอบ " * 20, encoding="utf-8")

    sub_los = [make_sub_lo(i) for i in range(1, 8)]   # 7 ข้อ > MAX_SUB_LOS(6) → ต้อง consolidate
    lo_rubric_resp = {"lesson_title": "ทดสอบ", "main_lo": "หลัก", "sub_los": sub_los,
                      "missing_coverage": []}

    consolidate_resp = {"merged_groups": [
        {"from_ids": ["s2", "s3"], "new_statement": "รวม s2 s3", "tag": "core", "type": "conceptual"}
    ]}
    rubric_remerge_resp = {"rubrics": [
        {"id": "s2", "observable_evidence": "e2", "mentor_activity": "m2",
         "rubric": {"0": "r0", "1": "r1", "2": "r2", "3": "r3"}}
    ]}

    skills   = tpl.softskills()
    soft_resp = {"softskills": [
        {"id": sid, "linked_sub_los": ["s1"], "required_activity": "act",
         "lesson_indicators": {"1": "a", "2": "b", "3": "c", "4": "d", "5": "e"}}
        for sid in skills
    ]}

    responses = [
        (json.dumps({"prompt_groups": ["P10"], "group_reason": "r", "dropped_groups": []}), usage(0)),
        (json.dumps(lo_rubric_resp, ensure_ascii=False), usage(500)),
        (json.dumps(consolidate_resp), usage(0)),
        (json.dumps(rubric_remerge_resp), usage(500)),
        (json.dumps(soft_resp), usage(500)),
    ]
    client  = FakeClient(responses)
    tracker = CostTracker()
    tracker.run_log = RunLog(process="setup", subject="t", lesson="l",
                             base_dir=str(tmp_path / "logs" / "runs"))

    objectives = Synthesizer(client, cost_tracker=tracker).synthesize(str(lesson_dir))

    assert objectives["schema_version"] == SCHEMA_VERSION
    assert len(objectives["sub_los"]) <= MAX_SUB_LOS
    assert len(objectives["softskills"]) == len(skills)

    merged = next(lo for lo in objectives["sub_los"] if lo["statement"] == "รวม s2 s3")
    assert merged["rubric"] == {"0": "r0", "1": "r1", "2": "r2", "3": "r3"}
    assert merged["observable_evidence"] == "e2"

    # เนื้อหา (block แรกของทุกขั้นที่ผ่าน _ask_cached) ต้องเป็น byte เดียวกันทุกครั้ง —
    # นี่คือสิ่งที่ทำให้ prompt cache hit ได้ตั้งแต่ขั้น 2 เป็นต้นไป
    cached_calls = [c for c in client.chat.completions.calls if isinstance(c[0]["content"], list)]
    assert len(cached_calls) == 4   # select_groups, lo_rubric, rubric_remerge, softskills
    first_blocks = {c[0]["content"][0]["text"] for c in cached_calls}
    assert len(first_blocks) == 1
    assert all(c[0]["content"][0].get("cache_control") for c in cached_calls)

    tracker.run_log.close()
    rows  = list(csv.DictReader(open(tracker.run_log.path, encoding="utf-8-sig")))
    steps = {r["step"] for r in rows}
    assert {"select_groups", "lo_rubric", "consolidate", "rubric_remerge", "softskills"} <= steps

    assert (lesson_dir / "objectives.json").exists()
