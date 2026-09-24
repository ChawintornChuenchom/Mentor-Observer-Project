# scoring — คะแนน Soft Skill สะสมด้วย Kalman filter

แต่ละคู่ ผู้เรียน × สกิล มีค่าประมาณความสามารถ `x` (1–5) และความไม่แน่นอน `P`
ทุกครั้งที่จบ session ผลจาก Observer (`level` + `evidence`) จะถูกนำมาปรับ `x` —
หลักฐานยิ่งหนักแน่น และ `P` ยิ่งสูง (ยังไม่มั่นใจ) ค่า `x` ยิ่งขยับเข้าหาผลใหม่มาก

## ความหมายของ config (`scoring/config.json`)

| ค่า | ความหมาย | ปรับแล้วเกิดอะไร |
|---|---|---|
| `x0` | ค่าเริ่มต้นก่อนมีหลักฐาน (3.0 = "ตามเกณฑ์") | ไม่ค่อยมีผล เพราะ session แรกที่มีหลักฐานจะดึงออกไปเกือบหมด |
| `P0` | ความไม่แน่นอนเริ่มต้น และเพดานของ `P` | สูง = session แรกมีน้ำหนักมาก |
| `Q` | ความไม่แน่นอนที่เพิ่มทุก session (ความสามารถเปลี่ยนได้ตามเวลา) | สูง = เชื่อผลล่าสุดมากขึ้น ลืมอดีตเร็วขึ้น / 0 = ถือว่าความสามารถคงที่ |
| `R.strong/moderate/weak` | ความคลาดเคลื่อนของการวัด ตามความหนักแน่นของหลักฐาน | สูง = เชื่อผลนั้นน้อย (`weak` ขยับ x ได้น้อยกว่า `strong`) |
| `clamp` | ขอบเขตของ `x` | — |
| `min_obs_to_report` | จำนวนครั้งที่มีหลักฐานจริงขั้นต่ำก่อนแสดงคะแนน | ต่ำกว่านี้แสดง "ข้อมูลยังไม่พอ" และห้ามวาด radar chart |

N/E (ไม่มีหลักฐาน) ไม่เปลี่ยน `x` แต่ `P` ยังเพิ่มขึ้นด้วย `Q` (ไม่เกิน `P0`) — ยิ่งไม่ได้เห็นนาน ยิ่งมั่นใจน้อยลง

## เปลี่ยน config

1. แก้ค่าใน `scoring/config.json` และ **เพิ่ม `engine_version`** (เช่น `"1.1"`) เพื่อให้รู้ว่าผลไหนคำนวณด้วยค่าชุดไหน
2. รัน `python recompute_scores.py` — คำนวณ `skill_state` ใหม่ทั้งหมดจากประวัติดิบ (`soft_observations` ไม่ถูกแก้)

## เปลี่ยนกลไก

เขียน class ใหม่ที่ implement `ScoringEngine` (`initial_state`, `update`, `to_display`)
เพิ่มลง `ENGINES` ใน `scoring/kalman.py` แล้วตั้ง `"engine"` ใน config เป็นชื่อนั้น จากนั้นรัน recompute
history เก็บเฉพาะผลดิบจาก Observer จึงใช้กับ engine ใหม่ได้ทันที

## ไฟล์

- `kalman.py` — `ScoringEngine`, `KalmanEngine`, `recompute_from_history` (pure, stdlib)
- `store.py` — SQLite `data/scores.db`: sessions + transcript, hard_events, hard_results, soft_observations, skill_state
- `tests/test_kalman.py` — T1–T15 (`python -m pytest tests`)
