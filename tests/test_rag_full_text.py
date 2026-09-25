from rag import lesson_full_text


def test_labels_chunks_in_document_order_across_files(tmp_path):
    # ตั้งชื่อไฟล์ให้เรียงตามตัวอักษร (lesson_full_text อ่านตามชื่อไฟล์)
    (tmp_path / "01_intro.txt").write_text("เนื้อหาส่วนแรกของบทเรียน", encoding="utf-8")
    (tmp_path / "02_body.txt").write_text("เนื้อหาส่วนที่สองของบทเรียน", encoding="utf-8")

    labeled, chunks = lesson_full_text(str(tmp_path))

    assert len(chunks) == 2
    assert chunks[0] == "เนื้อหาส่วนแรกของบทเรียน"
    assert chunks[1] == "เนื้อหาส่วนที่สองของบทเรียน"
    assert "[c1] เนื้อหาส่วนแรกของบทเรียน" in labeled
    assert "[c2] เนื้อหาส่วนที่สองของบทเรียน" in labeled
    assert labeled.index("[c1]") < labeled.index("[c2]")


def test_ignores_unsupported_files(tmp_path):
    (tmp_path / "notes.txt").write_text("ข้อความ", encoding="utf-8")
    (tmp_path / "chroma_db").mkdir()          # โฟลเดอร์ ไม่ใช่ไฟล์ที่รองรับ
    (tmp_path / "image.jpg").write_bytes(b"\x00")   # นามสกุลไม่อยู่ใน supported set

    labeled, chunks = lesson_full_text(str(tmp_path))
    assert chunks == ["ข้อความ"]


def test_empty_lesson_returns_empty(tmp_path):
    labeled, chunks = lesson_full_text(str(tmp_path))
    assert chunks == []
    assert labeled == ""
