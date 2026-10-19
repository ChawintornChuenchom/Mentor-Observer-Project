"""ทดสอบขั้น consolidate (ขั้น 3): ไม่เกินเพดานไม่แตะ · เกินเพดานรวมให้น้อยที่สุด · mechanical merge
ใช้ LLM ปลอม ไม่เรียก API จริง"""
import json
from types import SimpleNamespace

from synthesizer import Synthesizer, MAX_MAIN_LOS, merge_main_los


class FakeLog:
    def __init__(self):
        self.rows = []

    def event(self, step, event, detail="", model="", **_):
        self.rows.append((step, event, detail))


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, model, messages, **kwargs):
        self.calls.append(messages)
        content = self.responses.pop(0)
        if isinstance(content, Exception):
            raise content
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                               usage=None, model=model)


def make_synth(responses):
    client  = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(responses)))
    tracker = SimpleNamespace(run_log=FakeLog(), track_synthesizer=lambda *a, **k: None)
    return Synthesizer(client, cost_tracker=tracker), client.chat.completions, tracker.run_log


def subs(n, chunks=None, overrides=None):
    out = []
    for i in range(1, n + 1):
        lo = {"id": f"s{i}", "statement": f"stmt{i}", "tag": "core", "type": "conceptual",
              "source_chunks": (chunks or {}).get(i, [f"c{i}"]),
              "prompt_group": "P16", "content_type": "HUM-CTX",
              "observable_evidence": f"e{i}", "mentor_activity": f"m{i}",
              "rubric": {"0": "a", "1": "b", "2": "c", "3": "d"}}
        lo.update((overrides or {}).get(i, {}))
        out.append(lo)
    return out


def group(ids, statement="รวม", **kw):
    return {"from_ids": ids, "new_statement": statement, "tag": "core", "type": "conceptual", **kw}


def statements(objectives):
    return [lo["statement"] for lo in objectives["main_los"]]


# 1–2. ไม่เกินเพดาน → ไม่เรียก LLM ไม่แตะอะไรเลย
def test_within_cap_does_not_call_llm():
    for n in (4, MAX_MAIN_LOS):
        synth, comp, log = make_synth([])
        original = subs(n)
        out = synth._consolidate({"main_los": [dict(lo) for lo in original]})
        assert comp.calls == []
        assert out["main_los"] == original
        step, event, detail = log.rows[0]
        assert (step, event) == ("consolidate", "skip")
        assert json.loads(detail) == {"skipped": True, "reason": "n ≤ MAX", "n": n}


# 3. 8 ข้อ โมเดลเสนอ 3 กลุ่ม → ใช้ 2 กลุ่มแรกแล้วหยุด
def test_stops_when_cap_reached():
    synth, comp, log = make_synth([{"merged_groups": [
        group(["s1", "s2"], "A"), group(["s4", "s5"], "B"), group(["s7", "s8"], "C")]}])
    out = synth._consolidate({"main_los": subs(8)})
    assert statements(out) == ["A", "stmt3", "B", "stmt6", "stmt7", "stmt8"]
    assert [lo["id"] for lo in out["main_los"]] == [f"s{i}" for i in range(1, 7)]
    assert any(e == "skip_group" and "1 กลุ่ม" in d for _, e, d in log.rows)
    # user prompt บอกจำนวนที่ต้องลด + chunks
    user = comp.calls[0][1]["content"]
    assert "ต้องลดอย่างน้อย 2 ข้อ" in user and "chunks=c1" in user


# 4. id ซ้ำกับกลุ่มก่อน / id ไม่มีจริง → ข้ามกลุ่ม
def test_skips_invalid_groups():
    synth, _, log = make_synth([{"merged_groups": [
        group(["s1", "s9"], "X"), group(["s1", "s2"], "A"),
        group(["s2", "s3"], "Y"), group(["s7", "s8"], "B")]}])
    out = synth._consolidate({"main_los": subs(8)})
    assert statements(out) == ["A", "stmt3", "stmt4", "stmt5", "stmt6", "B"]
    skipped = [d for _, e, d in log.rows if e == "skip_group"]
    assert any("s9" in d for d in skipped) and any("ซ้ำ" in d for d in skipped)


# 5–6. chunks ไม่ซ้อน → ใช้ได้แต่เตือน · source_chunks = union เรียงตามเลข
def test_non_overlapping_merge_warns_and_unions_chunks():
    synth, _, log = make_synth([{"merged_groups": [
        group(["s1", "s2"], "A"), group(["s3", "s4"], "B")]}])
    chunks = {1: ["c10", "c2"], 2: ["c1"], 3: ["c3"], 4: ["c3", "c4"]}
    out = synth._consolidate({"main_los": subs(8, chunks)})
    assert out["main_los"][0]["source_chunks"] == ["c1", "c2", "c10"]
    assert out["main_los"][1]["source_chunks"] == ["c3", "c4"]
    warns = [d for _, e, d in log.rows if e == "warn"]
    assert any("s1" in d and "คนละส่วน" in d for d in warns)
    assert not any("s3" in d and "คนละส่วน" in d for d in warns)


# 7. LLM ล้ม + 8 ข้อ → mechanical เลือกคู่ติดกันที่ chunks ซ้อนมากสุด
def test_mechanical_merge_prefers_overlapping_adjacent_pair():
    synth, _, log = make_synth([RuntimeError("down")])
    chunks = {3: ["c3", "c9"], 4: ["c3", "c9"], 6: ["c6"], 7: ["c6"]}
    out = synth._consolidate({"main_los": subs(8, chunks)})
    assert len(out["main_los"]) == MAX_MAIN_LOS
    assert statements(out) == ["stmt1", "stmt2", "stmt3 รวมถึงstmt4", "stmt5",
                               "stmt6 รวมถึงstmt7", "stmt8"]
    assert sum(e == "mechanical_merge" for _, e, _ in log.rows) == 2


def test_mechanical_merge_tie_takes_last_pair():
    synth, _, _ = make_synth(["ไม่ใช่ json"])
    out = synth._consolidate({"main_los": subs(7)})          # ไม่มีคู่ไหนซ้อน → คู่ท้ายสุด
    assert statements(out)[-1] == "stmt6 รวมถึงstmt7"


# ข้อที่ถูกรวม: ล้าง rubric · ข้อที่ไม่ถูกรวม: คงทุกฟิลด์
def test_merged_drops_rubric_untouched_keeps_all_fields():
    synth, _, _ = make_synth([{"merged_groups": [group(["s1", "s2"], "A"), group(["s3", "s4"], "B")]}])
    original = subs(8)
    out = synth._consolidate({"main_los": [dict(lo) for lo in original]})
    assert "rubric" not in out["main_los"][0] and "mentor_activity" not in out["main_los"][0]
    kept = out["main_los"][2]
    assert {k: v for k, v in kept.items() if k != "id"} == \
           {k: v for k, v in original[4].items() if k != "id"}


def test_merge_fallback_tag_type_and_differing_group():
    members = subs(2, overrides={1: {"tag": "supporting", "type": "factual", "prompt_group": "P01"},
                                 2: {"tag": "core", "type": "conceptual"}})
    merged, warnings = merge_main_los(members, "รวม", "bad", None)
    assert merged["tag"] == "core" and merged["type"] == "conceptual"
    assert merged["prompt_group"] == "P01"
    assert any("prompt_group" in w for w in warnings)
