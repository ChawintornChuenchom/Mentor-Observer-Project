from synthesizer import split_into_parts


def test_splits_at_chunk_boundary_not_mid_chunk():
    chunks = ["a" * 40, "b" * 40, "c" * 40, "d" * 40]
    parts = split_into_parts(chunks, part_chars=70)

    # แต่ละก้อนต้องไม่ตัดกลาง chunk — ต่อ text กลับมาต้องได้ chunk เดิมครบทุกตัว
    flat = [c for part in parts for _, c in part]
    assert flat == chunks

    # เลขป้ายต้องต่อเนื่องเริ่มจาก 1 ตามลำดับเดิม ไม่ว่าจะอยู่ก้อนไหน
    ids = [i for part in parts for i, _ in part]
    assert ids == [1, 2, 3, 4]

    # ก้อนแรกยัด a+b ไม่ได้ (40+40=80 > 70) เลยมีแค่ a ก้อนเดียว
    assert len(parts[0]) == 1


def test_single_chunk_larger_than_part_chars_stays_alone():
    chunks = ["x" * 1000]
    parts = split_into_parts(chunks, part_chars=100)
    assert len(parts) == 1
    assert parts[0] == [(1, chunks[0])]


def test_empty_chunks_gives_empty_parts():
    assert split_into_parts([], part_chars=100) == []


def test_many_small_chunks_pack_into_one_part():
    chunks = ["a"] * 50
    parts = split_into_parts(chunks, part_chars=1000)
    assert len(parts) == 1
    assert len(parts[0]) == 50
