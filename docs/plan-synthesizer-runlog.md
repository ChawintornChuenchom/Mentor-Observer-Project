# แผน: Synthesizer อ่านเนื้อหาทั้งบท + prompt cache และ Run Log ทุก process

## Context
1. Synthesizer ดึงเนื้อหาด้วย RAG แค่ ≤20 chunk จาก query ตายตัว 4 คำ — ชีวะ บทที่ 1 (31,537 ตัวอักษร ~32 chunk)
   เห็นไม่ถึงครึ่งบท และ chunk เรียงตามความคล้าย ไม่ใช่ลำดับในเอกสาร → LO/rubric ไม่ครอบคลุมทั้งบท
2. log กระจัดกระจาย: ขั้นของ Synthesizer มีแค่ `print`, ค่าเงินทุกขั้นแสดงเป็น "Synthesizer", ค่า embedding ไม่ถูกนับ,
   ปิด terminal แล้วหาย (CSV มีเฉพาะแชท Mentor และเขียนตอน quit เท่านั้น)
เป้าหมาย: ให้ Synthesizer เห็นเนื้อหาทั้งบทในราคาต่ำด้วย prompt cache และมี log บน terminal + CSV ทุก process
เพื่อตรวจคุณภาพ rubric และค่าใช้จ่ายย้อนหลัง

**ขั้นแรกของการทำงาน:** คัดลอกแผนนี้ไปไว้ในโปรเจกต์ที่ `docs/plan-synthesizer-runlog.md`

---

## A. Synthesizer ใหม่ (4 ขั้น + cache)

```
0. อ่านเนื้อหาทั้งบท (ไม่ใช่ LLM) — ยาวเกิน SYNTH_MAX_CHARS → ดูหัวข้อ "เนื้อหายาวเกิน"
1. [เนื้อหาเต็ม 🔒 เขียน cache] + _index.md 18 หมวด → prompt_groups ≤2
2. [เนื้อหาเต็ม 🔒 อ่าน cache ] + template ที่เลือก   → main_lo + sub_los + rubric hard 0–3
3. consolidate ทุกครั้ง (รายการ LO เท่านั้น — โค้ดเดิม) → ≤6 ข้อ ไม่มีคู่ขนาน
     └ มีการรวม → สร้าง rubric ใหม่เฉพาะข้อที่ถูกรวม (อ่าน cache)
4. [เนื้อหาเต็ม 🔒 อ่าน cache ] + LO ชุดสุดท้าย + S01–S12 → softskills ครบ 12
```
- ขั้น 1 กับ 2 ต้องแยก: template ของขั้น 2 ขึ้นกับหมวดที่เลือกในขั้น 1 (ส่ง template ทั้ง 18 หมวด ~45k token แพงกว่า)
- ไม่ใช้การสรุปคั่นกลางในกรณีปกติ (สรุปเสียข้อมูล, สรุปหลอนแล้วลามทุกขั้น, ค่า output ทำให้แพงกว่า cache ~30%)
- จำนวนการเรียก LLM: 4 ครั้ง (5 ถ้ามีการรวม LO) จากเดิม 5

### A1. อ่านเนื้อหา ([rag.py](rag.py))
- เพิ่ม `lesson_full_text(lesson_path)`: ไฟล์ชุดเดียวกับ `RAG.index_files` (.txt/.md/.docx/.pptx — PDF/รูปถูก OCR เป็น .txt แล้ว)
  ผ่าน `extract_text()` เดิม เรียงตามชื่อไฟล์
- แบ่งด้วย `chunk_text()` เดิม แล้วติดป้าย `[c1] … [cN]` ตามลำดับเอกสาร → `evidence_chunks` ของ Sub LO อ้างอิงได้จริง
- Synthesizer ไม่ใช้ RAG query / embedding อีก

### A2. เนื้อหายาวเกิน (`SYNTH_MAX_CHARS` ใน [config.py](config.py), ค่าเริ่มต้น 200,000 ตัวอักษร)
แบ่งเนื้อหาเป็นก้อน (`SYNTH_PART_CHARS` ~60,000 ตัวอักษร ตัดที่รอยต่อ chunk) → สรุปทีละก้อนด้วย LLM → ต่อสรุปทุกก้อนเป็น "เนื้อหา" ของขั้น 1–4 (cache แบบเดียวกัน)
- สรุปแบบมีโครงสร้าง ไม่ใช่ความเรียง ต่อหัวข้อ: `id` (คง `[cN]` เดิม), หัวข้อ, key_concepts, ตัวอย่าง, ตัวเลข/ข้อมูล,
  ความเข้าใจผิดที่พบบ่อย, สัญญาณ soft skill (การทดลอง ตัวเลข จริยธรรม กรณีศึกษา) — เก็บสิ่งที่ rubric และการเลือกหมวดต้องใช้
- log: ใช้โหมดสรุป, จำนวนก้อน, ขนาดก่อน/หลังสรุป, cost ต่อก้อน
- บันทึกสรุปเป็น `summary.json` ในโฟลเดอร์บทเรียนให้ครูตรวจได้

### A3. Prompt cache
- message แรก = เนื้อหา byte เดียวกันทุกขั้น + `cache_control` (รูปแบบเดียวกับ `_system_message` ใน [observer.py](observer.py)) → คำสั่งของแต่ละขั้นต่อท้าย
  (ลำดับ "เอกสารก่อน คำสั่งทีหลัง" ช่วยคุณภาพกับเนื้อหายาวด้วย)
- ทุกขั้นใช้ `MODEL_SYNTHESIZER` เดียวกัน + `extra_body={"session_id": ...}` เดียวกัน (sticky provider แบบ mentor.py)
- log `cached_tokens` ของขั้น 2–4 — ต้อง ≈ ขนาดเนื้อหา ถ้า 0 = miss (แสดงเตือน)

### A4. การเปลี่ยนใน prompt / ค่าคงที่
- **`MAX_SUB_LOS = 6`** ([synthesizer.py](synthesizer.py)) — `CONSOLIDATE_PROMPT` และ mechanical merge ใช้ตัวแปรนี้อยู่แล้ว
  แต่ `SYNTHESIZER_PROMPT` เขียน "ไม่เกิน 5 ข้อ" ตายตัว → เปลี่ยนเป็น f-string ใช้ `MAX_SUB_LOS`
- รวม "สร้าง LO" กับ "rubric hard" เป็นขั้นเดียว (`HARD_RUBRIC_PROMPT` ย้ายไปอยู่ใน prompt ขั้น 2; คงไว้สำหรับสร้างใหม่หลัง consolidate)
- ขั้น 4 ส่งเนื้อหาเต็มให้ soft → แก้บรรทัด "ไม่ต้องส่งเนื้อหาบทเรียนเต็ม" ใน `templates/softskills/_selection_prompt.md`
- consolidate เรียก**ทุกครั้ง** (เหมือนเดิม — จับคู่ขนานได้)

### A5. กันหลอน
- `temperature=0` ทุกขั้น
- prompt: ยึดเนื้อหาในบทเท่านั้น, ส่วนที่ OCR อ่านไม่ออกให้ข้าม ห้ามเติมเอง, rubric ระดับ 0 ใช้ความเข้าใจผิดที่เกี่ยวกับเนื้อหาในบท
- ตรวจด้วยโค้ด → ไม่ผ่านให้ log เตือน: `evidence_chunks` มีอยู่จริง, `prompt_group` อยู่ในหมวดที่เลือก, rubric ครบ 0–3, soft ครบ 1–5 × 12, LO ≤ 6

**ค่าใช้จ่ายโดยประมาณ** (ชีวะ บทที่ 1, ราคา fallback ใน cost_tracker): ส่วนเนื้อหา ~$0.046 ต่อบท

---

## B. Run Log (terminal + CSV)

### B1. `run_log.py` (ใหม่) — `RunLog`
```python
log = RunLog(process="setup", subject=..., lesson=..., tracker=tracker)
log.event(step, event, detail="", **extra)   # print + เขียน CSV ทันที (crash ก็ยังมี log)
log.close()                                   # แถวสรุป: ยอดก่อน/หลัง, ผลรวม usage, ส่วนต่าง
```
- ไฟล์ `logs/runs/<process>_<subject>_<lesson>_<YYYYmmdd_HHMMSS>.csv` (utf-8-sig เปิดใน Excel ได้)
- คอลัมน์: `timestamp, process, step, event, detail, model, in_tok, cached_tok, out_tok, cost_usd, cost_thb, balance_usd`
- เปิด log ดึงยอดตั้งต้นด้วย `get_remaining()` ของ [check_credits.py](check_credits.py)

### B2. `CostTracker` ([cost_tracker.py](cost_tracker.py))
- `self.run_log = None`; `_spend` / `track_ocr` / `track_embedding` ส่งแถว cost เข้า run_log เมื่อมี
- `track_synthesizer(usage, step="")` → label `Synthesizer/<ขั้น>`
- เพิ่ม `track_summary(usage)` ให้ `summarize_history` ใน [mentor.py](mentor.py) ใช้ (ตอนนี้ถูกนับเป็น Synthesizer)

### B3. Embedding cost ([rag.py](rag.py))
chromadb เรียก embedding เองภายในจึงไม่เห็น usage → RAG คำนวณเองแล้วส่งให้ chroma:
- `RAG._embed(texts, step)` เรียก `self.client.embeddings.create(model=MODEL_EMBEDDING, input=texts)` →
  `track_embedding(usage.prompt_tokens, cost=getattr(usage, "cost", None), step=step)` (ไม่มี cost → ประมาณจาก `PRICE_EMBED_IN`)
- `index_files`: `upsert(..., embeddings=...)`; `query`: `query(query_embeddings=...)`
- คง `embedding_function=self.ef` ตอน `get_or_create_collection` เพื่อเปิด chroma_db เดิมได้ — **ทดสอบกับ chroma_db เดิมว่า query ได้ผลเหมือนเดิม**
- mentor.py สร้าง `CostTracker` ก่อน แล้วส่งให้ `RAG(..., cost_tracker=tracker)`
- `track_embedding` ไม่ดึงยอดเหลือทุกครั้ง (HTTP 1 ครั้ง/เทิร์น ทำให้ Mentor ช้า); chat CSV เพิ่ม `emb_tok`, `emb_cost`

### B4. จุดที่เพิ่ม log
**Setup / Resynth** ([main.py](main.py), [resynthesize.py](resynthesize.py)): OCR ต่อไฟล์ (หน้า, ตัวอักษร, cost), index ต่อไฟล์ (chunk, embedding cost)

**Synthesizer** ([synthesizer.py](synthesizer.py), รับ `run_log` แบบ optional):
- อ่านเนื้อหา: ไฟล์, ตัวอักษร, จำนวน chunk, โหมด (เต็ม / สรุปเป็นก้อน)
- เลือกหมวด: `prompt_groups`, `group_reason`, หมวดที่ถูกตัด, หมวดที่ถูกกรองเพราะไม่มีจริง
- LO: main_lo + แต่ละ sub_lo (id, type, prompt_group, content_type, statement)
- consolidate: กลุ่มที่รวม (`s2+s3 → …`), รวมแบบ mechanical หรือไม่
- rubric hard: 1 แถว/(sub_lo × ระดับ 0–3) + observable_evidence, mentor_activity
- soft: 1 แถว/(สกิล × ระดับ 1–5) + required_activity, linked_sub_los
- ทุกขั้น: cost, cached_tokens, ผลตรวจกันหลอน, ล้มเหลว/parse error + raw 200 ตัวอักษรแรก
- terminal แสดง rubric แบบย่อ (~80 ตัวอักษร/บรรทัด) ข้อความเต็มอยู่ใน CSV

**Mentor** ([mentor.py](mentor.py), process `session`, คง chat CSV เดิม):
- เริ่ม: student_id, score session id, schema_version
- summary fold: ยุบกี่ข้อความ + cost (Summary)
- Observer hard: ผลดิบ vs หลัง guard, held, prompt injection
- จบ: soft ต่อสกิล + Kalman `x/P ก่อน → หลัง` (`ScoreStore.finish_session` คืน state ก่อนหน้าเพิ่ม)

**ทุก process จบ**: แถว summary — ยอดก่อน/หลัง, หักจริง (USD/บาท), ผลรวม usage, ส่วนต่างที่ไม่ได้ track

---

## ไฟล์ที่แก้
- ใหม่: `run_log.py`, `docs/plan-synthesizer-runlog.md` (สำเนาแผนนี้)
- แก้: `synthesizer.py`, `rag.py`, `cost_tracker.py`, `config.py` (`SYNTH_MAX_CHARS`, `SYNTH_PART_CHARS`), `main.py`, `resynthesize.py`, `mentor.py`,
  `scoring/store.py`, `templates/softskills/_selection_prompt.md`
- `.gitignore`: ถ้าไม่อยากให้ run log เข้า git เพิ่ม `logs/runs/`

## Verification
1. `python -m pytest tests` — ของเดิมผ่าน + test ใหม่: RunLog (เขียน CSV ทันที, แถว summary), `lesson_full_text` + ป้าย `[cN]`,
   การแบ่งก้อนเมื่อเกิน `SYNTH_MAX_CHARS`, ตัวตรวจกันหลอน
2. fake synthesizer (LLM ปลอม): ตรวจลำดับ 4 ขั้น, เนื้อหาเป็น message แรกเหมือนกันทุกขั้น (byte เดียวกัน), consolidate ≤6, CSV ครบทุก step (soft 12×5 แถว)
3. fake session: CSV ของ session มี guard/Kalman ก่อน-หลัง
4. เปิด chroma_db เดิมของ ชีวะ บทที่ 1 → query ด้วยโค้ดเก่า/ใหม่ได้ chunk ชุดเดียวกัน และมี embedding cost > 0
5. **ผู้ใช้รันเอง** หลังแก้ทั้งระบบ: `python resynthesize.py` ทั้ง 3 บท → ตรวจ `logs/runs/resynth_*.csv`:
   cached_tokens ขั้น 2–4 ≈ ขนาดเนื้อหา, ยอดหักตรงกับ OpenRouter, ส่วนต่าง "ไม่ได้ track" ใกล้ 0
