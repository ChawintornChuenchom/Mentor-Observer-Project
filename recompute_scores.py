"""คำนวณคะแนนสะสม soft skill ใหม่ทั้งหมดจากประวัติดิบ ด้วย scoring/config.json ปัจจุบัน

ใช้หลังแก้ x0 / P0 / Q / R หรือเปลี่ยน engine — ประวัติดิบ (soft_observations) ไม่ถูกแก้
"""
from scoring.store import ScoreStore


def main():
    store = ScoreStore()
    cfg   = store.config
    n     = store.recompute_all()
    store.close()
    print(f"คำนวณใหม่ {n} คู่ (ผู้เรียน × สกิล) ด้วย engine {cfg['engine']} v{cfg['engine_version']} ✅")


if __name__ == "__main__":
    main()
