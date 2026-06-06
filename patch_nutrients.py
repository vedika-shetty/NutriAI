#!/usr/bin/env python3
"""
patch_nutrients.py
Re-fetches nutrient values for all records already in the DB and UPDATEs them in place.
Run this once after fixing the parser — no need to rebuild from scratch.
"""
import sqlite3, time, sys
from tqdm import tqdm
from src.data_pipeline.usda_fetcher import init_db, batch_fetch, parse_food
from src.config import DB_PATH, BATCH_SIZE, REQUEST_DELAY

UPDATE_SQL = """
UPDATE foods SET
    calories=:calories, protein=:protein, carbs=:carbs, fat=:fat, fiber=:fiber,
    calcium=:calcium, iron=:iron, sodium=:sodium, potassium=:potassium,
    magnesium=:magnesium, zinc=:zinc, vitamin_b12=:vitamin_b12,
    vitamin_d=:vitamin_d, vitamin_c=:vitamin_c,
    omega3_ala=:omega3_ala, omega3_epa=:omega3_epa, omega3_dha=:omega3_dha
WHERE fdc_id=:fdc_id
"""

def main():
    conn = init_db(DB_PATH)
    all_ids = [r[0] for r in conn.execute("SELECT fdc_id FROM foods").fetchall()]
    print(f"Patching nutrients for {len(all_ids):,} records...")

    batches = [all_ids[i:i+BATCH_SIZE] for i in range(0, len(all_ids), BATCH_SIZE)]
    updated = 0

    for batch in tqdm(batches, desc="Patching", unit="batch"):
        raw_list = batch_fetch(batch)
        for raw in raw_list:
            rec = parse_food(raw)
            if rec:
                conn.execute(UPDATE_SQL, rec)
                updated += 1
        conn.commit()
        time.sleep(REQUEST_DELAY)

    nonzero = conn.execute("SELECT COUNT(*) FROM foods WHERE calories > 0").fetchone()[0]
    print(f"\nUpdated : {updated:,} records")
    print(f"Non-zero calories: {nonzero:,} / {len(all_ids):,}")
    conn.close()

if __name__ == "__main__":
    main()
