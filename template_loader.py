"""โหลด template มุมมองการประเมิน (P01–P18) และ soft skill (S01–S12)"""
import re
from functools import lru_cache
from pathlib import Path

TEMPLATES_DIR  = Path("templates")
LO_DIR         = TEMPLATES_DIR / "lo"
SOFTSKILL_DIR  = TEMPLATES_DIR / "softskills"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


def section(text: str, heading: str) -> str:
    """เนื้อหาใต้หัวข้อ '## <heading>...' จนถึงหัวข้อ ## ถัดไป"""
    m = re.search(rf"^## {re.escape(heading)}.*?\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


# ── LO (hard skill) ─────────────────────────────────────────
@lru_cache
def lo_index() -> str:
    return _read(LO_DIR / "_index.md")


@lru_cache
def lo_group_ids() -> tuple[str, ...]:
    return tuple(sorted(p.name.split("_")[0] for p in LO_DIR.glob("P[0-9][0-9]_*.md")))


def lo_template(group_id: str) -> str:
    matches = list(LO_DIR.glob(f"{group_id}_*.md"))
    if not matches:
        raise FileNotFoundError(f"ไม่พบ template มุมมอง {group_id}")
    return _read(matches[0])


# ── Soft skill ──────────────────────────────────────────────
@lru_cache
def softskill_index() -> str:
    return _read(SOFTSKILL_DIR / "_index.md")


@lru_cache
def softskill_selection_prompt() -> str:
    return _read(SOFTSKILL_DIR / "_selection_prompt.md")


@lru_cache
def softskills() -> dict[str, dict]:
    """{id: {"id", "name", "text", "not_evidence"}} เรียงตาม id"""
    result = {}
    for path in sorted(SOFTSKILL_DIR.glob("S[0-9][0-9].md")):
        text = _read(path)
        meta = _frontmatter(text)
        sid  = meta.get("id", path.stem)
        result[sid] = {
            "id":           sid,
            "name":         meta.get("name", sid),
            "text":         text,
            "not_evidence": section(text, "ไม่นับเป็นหลักฐาน"),
        }
    return result


def central_scale() -> str:
    """ตารางระดับคะแนนกลาง 1–5/N/E (ใช้กับทุกสกิล ห้ามเปลี่ยนความหมาย)"""
    return section(softskill_index(), "ระดับคะแนนกลาง")
