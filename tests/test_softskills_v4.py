"""ทดสอบ soft skill v4: ตัวตรวจ 4A/4B, การดึงข้อความตามป้าย, Observer soft, Mentor (consolidate อยู่ใน test_consolidate.py)
ใช้ LLM ปลอม ไม่เรียก API จริง"""
import json
from types import SimpleNamespace

import pytest

import template_loader as tpl
from mentor import build_static_system_prompt
from observer import Observer, OBSERVER_SOFT_PROMPT
from synthesizer import (Synthesizer, chunks_by_label, validate_selection, validate_indicator,
                         check_before_write, sort_labels)

SKILLS = list(tpl.softskills())

# main_los บทธนบุรี (ตามเอกสาร soft skill v4 ข้อ 7)
THONBURI_MAIN_LOS = [
    {"id": "s1", "type": "factual", "tag": "core", "source_chunks": ["c1"],
     "statement": "นักเรียนสามารถเรียงลำดับเหตุการณ์ตั้งแต่เสียกรุง กอบกู้เอกราช จนถึงอายุของอาณาจักรธนบุรีได้",
     "mentor_activity": "ให้เรียงเหตุการณ์", "observable_evidence": "เรียงเหตุการณ์ได้"},
    {"id": "s2", "type": "conceptual", "tag": "core", "source_chunks": ["c1"],
     "statement": "นักเรียนสามารถวิเคราะห์เหตุผลเชิงภูมิศาสตร์และยุทธศาสตร์ที่เลือกธนบุรีเป็นราชธานีได้",
     "mentor_activity": "ถามว่าทำไมเลือกธนบุรี", "observable_evidence": "ให้เหตุผลหลายด้าน"},
    {"id": "s3", "type": "conceptual", "tag": "core", "source_chunks": ["c2"],
     "statement": "นักเรียนสามารถอธิบายความสัมพันธ์ของการปราบกบฏและสงครามกับพม่ากับความมั่นคงได้",
     "mentor_activity": "ถามความเชื่อมโยง", "observable_evidence": "เชื่อมเหตุและผล"},
    {"id": "s4", "type": "conceptual", "tag": "supporting", "source_chunks": ["c2"],
     "statement": "นักเรียนสามารถอธิบายนโยบายเศรษฐกิจ (การค้ากับจีน พระราชทรัพย์ นาปรัง) กับภาวะขาดแคลนได้",
     "mentor_activity": "ถามนโยบาย", "observable_evidence": "อธิบายนโยบาย"},
    {"id": "s5", "type": "conceptual", "tag": "supporting", "source_chunks": ["c3"],
     "statement": "นักเรียนสามารถอธิบายการฟื้นฟูสังคมวัฒนธรรมกับความมั่นคงทางจิตใจได้",
     "mentor_activity": "ถามการฟื้นฟู", "observable_evidence": "อธิบายการฟื้นฟู"},
]
SUB_IDS = [lo["id"] for lo in THONBURI_MAIN_LOS]


def sel(sid, linked=("s2",), act_lo="s2", how="ให้วิจารณ์ข้อสรุป", reason=None):
    return {"id": sid, "linked_main_los": list(linked),
            "reason": reason if reason is not None else f"{linked[0] if linked else ''} ให้วิเคราะห์",
            "required_activity": {"main_lo": act_lo, "how": how}}


def rest(*chosen):
    return [{"id": s, "reason": "ไม่มี main_lo"} for s in SKILLS if s not in chosen]


def assert_partition(selected, not_selected):
    ids = [s["id"] for s in selected] + [s["id"] for s in not_selected]
    assert sorted(ids) == sorted(SKILLS)


# ── 1. validator 4A ─────────────────────────────────────────
def test_select_normal():
    result = {"softskills": [sel("S01"), sel("S04", linked=["s3"], act_lo="s3")],
              "softskills_not_selected": rest("S01", "S04")}
    selected, not_selected, log = validate_selection(result, SUB_IDS, SKILLS)
    assert [s["id"] for s in selected] == ["S01", "S04"]
    assert_partition(selected, not_selected)
    assert log == []


def test_select_more_than_five_keeps_first_five():
    chosen = ["S01", "S02", "S03", "S04", "S05", "S07"]
    result = {"softskills": [sel(s) for s in chosen], "softskills_not_selected": rest(*chosen)}
    selected, not_selected, log = validate_selection(result, SUB_IDS, SKILLS)
    assert [s["id"] for s in selected] == chosen[:5]
    assert {"id": "S07", "reason": "เกิน 5 สกิล"} in not_selected
    assert_partition(selected, not_selected)


def test_select_bad_id_dropped_and_missing_filled():
    result = {"softskills": [sel("s6"), sel("S01")],
              "softskills_not_selected": [s for s in rest("S01") if s["id"] != "S09"]}
    selected, not_selected, log = validate_selection(result, SUB_IDS, SKILLS)
    assert [s["id"] for s in selected] == ["S01"]
    assert {"id": "S09", "reason": "โมเดลไม่ได้ระบุ"} in not_selected
    assert any("'s6'" in m for m in log)
    assert_partition(selected, not_selected)


def test_select_activity_main_lo_not_in_linked_moves_to_not_selected():
    result = {"softskills": [sel("S01", linked=["s2"], act_lo="s3")],
              "softskills_not_selected": rest("S01")}
    selected, not_selected, _ = validate_selection(result, SUB_IDS, SKILLS)
    assert selected == []
    assert any(s["id"] == "S01" for s in not_selected)
    assert_partition(selected, not_selected)


@pytest.mark.parametrize("linked", [[], ["s9"]])
def test_select_bad_linked_moves_to_not_selected(linked):
    result = {"softskills": [sel("S01", linked=linked, act_lo="s2")],
              "softskills_not_selected": rest("S01")}
    selected, not_selected, _ = validate_selection(result, SUB_IDS, SKILLS)
    assert selected == []
    assert_partition(selected, not_selected)


def test_select_empty_how_moves_to_not_selected():
    result = {"softskills": [sel("S01", how="  ")], "softskills_not_selected": rest("S01")}
    selected, not_selected, _ = validate_selection(result, SUB_IDS, SKILLS)
    assert selected == []
    assert_partition(selected, not_selected)


def test_select_in_both_sides_counts_as_not_selected():
    result = {"softskills": [sel("S01")], "softskills_not_selected": rest()}
    selected, not_selected, _ = validate_selection(result, SUB_IDS, SKILLS)
    assert selected == []
    assert_partition(selected, not_selected)


def test_select_reason_without_main_lo_id_only_warns():
    result = {"softskills": [sel("S01", reason="วิเคราะห์เหตุผลหลายด้าน")],
              "softskills_not_selected": rest("S01")}
    selected, _, log = validate_selection(result, SUB_IDS, SKILLS)
    assert [s["id"] for s in selected] == ["S01"]
    assert any("reason" in m for m in log)


def test_select_reason_with_thai_around_id_is_ok():
    result = {"softskills": [sel("S01", reason="s2และs3ให้วิเคราะห์")],
              "softskills_not_selected": rest("S01")}
    _, _, log = validate_selection(result, SUB_IDS, SKILLS)
    assert log == []


def test_select_failed_parse_all_not_selected():
    selected, not_selected, _ = validate_selection(None, SUB_IDS, SKILLS)
    assert selected == []
    assert all(s["reason"] == "4A ล้มเหลว" for s in not_selected)
    assert_partition(selected, not_selected)


def test_select_zero_skills_is_valid():
    selected, not_selected, log = validate_selection(
        {"softskills": [], "softskills_not_selected": rest()}, SUB_IDS, SKILLS)
    assert selected == [] and log == []
    assert_partition(selected, not_selected)


# ── 2. validator 4B ─────────────────────────────────────────
GOOD = {"1": "a", "2": "b", "3": "c", "4": "d", "5": "e"}


def test_indicator_ok():
    assert validate_indicator({"feasible": True, "lesson_indicators": GOOD}) == ("ok", GOOD, [])


def test_indicator_infeasible():
    status, reason, _ = validate_indicator({"feasible": False, "reason": "ไม่มีโจทย์"})
    assert (status, reason) == ("infeasible", "ไม่มีโจทย์")


def test_indicator_missing_level_is_invalid():
    status, _, _ = validate_indicator({"feasible": True, "lesson_indicators": {**GOOD, "4": ""}})
    assert status == "invalid"


def test_indicator_forbidden_phrase_warns():
    status, _, warnings = validate_indicator(
        {"feasible": True, "lesson_indicators": {**GOOD, "3": "ตอบถูกทุกข้อ"}})
    assert status == "ok"
    assert warnings and "ตอบถูก" in warnings[0]


# ── 3. ดึงข้อความตามป้าย ────────────────────────────────────
def test_chunks_by_label_numeric_order_no_duplicates():
    chunks = [f"text{i}" for i in range(1, 11)]
    out = chunks_by_label(chunks, ["c10", "c2", "c1", "c2", "c99"])
    assert out == "[c1] text1\n\n[c2] text2\n\n[c10] text10"


def test_sort_labels_numeric():
    assert sort_labels(["c10", "c2", "c2", "c1"]) == ["c1", "c2", "c10"]


# ── fake LLM ────────────────────────────────────────────────
class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, model, messages, **kwargs):
        self.calls.append(messages)
        content = self.responses.pop(0)
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                               usage=None)


def fake_client(responses):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(responses)))


# ── 4A + 4B ผ่าน Synthesizer ─────────────────────────────────
def test_build_softskills_sends_only_linked_chunks():
    objectives = {"summary": "หลัก", "main_los": THONBURI_MAIN_LOS}
    chunks = ["เนื้อหา c1 ธนบุรีใกล้ทะเล", "เนื้อหา c2 สงคราม", "เนื้อหา c3 วัฒนธรรม"]
    client = fake_client([
        {"softskills": [sel("S01")], "softskills_not_selected": rest("S01")},
        {"feasible": True, "lesson_indicators": GOOD},
    ])
    Synthesizer(client).build_softskills(objectives, chunks)

    select_prompt = client.chat.completions.calls[0][0]["content"]
    assert "เนื้อหา c1" not in select_prompt              # 4A ไม่ส่งเนื้อหาบทเรียน
    indicator_prompt = client.chat.completions.calls[1][0]["content"]
    assert "[c1] เนื้อหา c1" in indicator_prompt and "เนื้อหา c2" not in indicator_prompt
    assert "Rubric กลาง" in indicator_prompt and "## อ้างอิง" not in indicator_prompt
    assert objectives["softskills"][0]["lesson_indicators"] == GOOD
    check_before_write(objectives, SKILLS)


def test_build_indicator_retries_then_moves_to_not_selected():
    objectives = {"summary": "หลัก", "main_los": THONBURI_MAIN_LOS}
    client = fake_client([
        {"softskills": [sel("S01")], "softskills_not_selected": rest("S01")},
        {"feasible": True, "lesson_indicators": {"1": "a"}},
        "ไม่ใช่ json",
    ])
    Synthesizer(client).build_softskills(objectives, ["x", "y", "z"])
    assert objectives["softskills"] == []
    assert any(s["id"] == "S01" and s["reason"].startswith("4B:")
               for s in objectives["softskills_not_selected"])
    check_before_write(objectives, SKILLS)


def test_check_before_write_rejects_evidence_chunks():
    objectives = {"main_los": [{"id": "s1", "evidence_chunks": ["c1"]}],
                  "softskills": [], "softskills_not_selected": rest()}
    with pytest.raises(AssertionError):
        check_before_write(objectives, SKILLS)


def test_add_softskill_moves_from_not_selected(tmp_path):
    (tmp_path / "content.txt").write_text("เนื้อหาบทเรียน", encoding="utf-8")
    objectives = {"summary": "หลัก", "main_los": THONBURI_MAIN_LOS,
                  "softskills": [], "softskills_not_selected": rest()}
    (tmp_path / "objectives.json").write_text(json.dumps(objectives, ensure_ascii=False),
                                              encoding="utf-8")
    client = fake_client([{"feasible": True, "lesson_indicators": GOOD}])
    out = Synthesizer(client).add_softskill(str(tmp_path), "S08", ["s2"],
                                            {"main_lo": "s2", "how": "ให้ชั่งหลักการ"})
    assert [s["id"] for s in out["softskills"]] == ["S08"]
    saved = json.loads((tmp_path / "objectives.json").read_text(encoding="utf-8"))
    assert "S08" not in {s["id"] for s in saved["softskills_not_selected"]}


def test_add_softskill_refuses_beyond_max(tmp_path):
    (tmp_path / "content.txt").write_text("เนื้อหาบทเรียน", encoding="utf-8")
    chosen = ["S01", "S02", "S03", "S04", "S05"]
    objectives = {"summary": "หลัก", "main_los": THONBURI_MAIN_LOS,
                  "softskills": [{**sel(s), "lesson_indicators": GOOD} for s in chosen],
                  "softskills_not_selected": rest(*chosen)}
    (tmp_path / "objectives.json").write_text(json.dumps(objectives, ensure_ascii=False),
                                              encoding="utf-8")
    with pytest.raises(ValueError):
        Synthesizer(fake_client([])).add_softskill(str(tmp_path), "S08", ["s2"],
                                                  {"main_lo": "s2", "how": "ให้ชั่งหลักการ"})


# ── 5. Observer soft ────────────────────────────────────────
def three_skill_objectives():
    return {
        "main_los": THONBURI_MAIN_LOS,
        "softskills": [
            {**sel(sid), "lesson_indicators": {lv: f"{sid}-{lv}" for lv in "12345"}}
            for sid in ("S01", "S04", "S05")
        ],
        "softskills_not_selected": rest("S01", "S04", "S05"),
    }


def test_observer_soft_prompt_only_selected_skills():
    obs = Observer(client=None, objectives=three_skill_objectives())
    assert obs._soft_lesson.count("## S") == 3
    assert "ระดับคะแนนกลาง" not in obs._soft_static + obs._soft_lesson
    assert "{" in OBSERVER_SOFT_PROMPT and "{{" not in OBSERVER_SOFT_PROMPT


def test_observer_normalize_returns_only_selected_keys():
    full = {sid: {"level": 3, "label": None, "evidence": "moderate", "e": "x"} for sid in SKILLS}
    client = fake_client([full])
    obs = Observer(client, three_skill_objectives())
    result = obs.evaluate_soft([{"role": "user", "content": "สวัสดี"}])
    assert sorted(result) == ["S01", "S04", "S05"]


def test_observer_skips_when_no_softskills():
    obs = Observer(client=None, objectives={"main_los": THONBURI_MAIN_LOS, "softskills": []})
    assert obs.evaluate_soft([]) == {}


# ── 6. Mentor ───────────────────────────────────────────────
def test_mentor_shows_required_activity_under_its_main_lo():
    objectives = {"lesson_title": "ธนบุรี", "summary": "หลัก", "main_los": THONBURI_MAIN_LOS,
                  "softskills": [{**sel("S01", linked=["s2", "s3"], act_lo="s3",
                                        how="เสนอข้อสรุปให้วิจารณ์"),
                                  "lesson_indicators": GOOD}]}
    prompt = build_static_system_prompt("ตัวละคร", objectives)
    lines = prompt.splitlines()
    i_s3  = next(i for i, l in enumerate(lines) if l.startswith("- s3 "))
    i_s4  = next(i for i, l in enumerate(lines) if l.startswith("- s4 "))
    i_act = next(i for i, l in enumerate(lines) if "สำหรับ soft skill S01" in l)
    assert i_s3 < i_act < i_s4
    assert "เสนอข้อสรุปให้วิจารณ์" in lines[i_act]
