#!/usr/bin/env python3
"""
enrich_database.py
One-time local enrichment: applies clinical, allergen, diet, and diversity
tags to all 13,620 foods in nutriai_foods.db. No API calls. ~2-3 minutes.
"""
import time, logging
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")

def main():
    print("=" * 60)
    print("  NutriAI — Clinical Enrichment Engine")
    print("=" * 60)
    print("Tagging all foods with allergen, diet, and clinical flags.")
    print("No API calls. Expected: 2-3 minutes.\n")

    t0 = time.time()
    from src.data_pipeline.enricher import run_enrichment
    from src.config import DB_PATH

    stats = run_enrichment(DB_PATH)
    elapsed = time.time() - t0

    print(f"\n{'─'*50}")
    print(f"  Enrichment complete in {elapsed:.0f}s")
    print(f"{'─'*50}")
    print(f"  Vegan-safe foods      : {stats['is_vegan']:,}")
    print(f"  Vegetarian-safe foods : {stats['is_vegetarian']:,}")
    print(f"  Pescatarian-safe      : {stats['is_pescatarian']:,}")
    print(f"  Has meat              : {stats['has_meat']:,}")
    print(f"  Has fish              : {stats['has_fish']:,}")
    print(f"  Has pork              : {stats['has_pork']:,}")
    print(f"  Has gluten            : {stats['has_gluten']:,}")
    print(f"  Has dairy             : {stats['has_dairy']:,}")
    print(f"  High-FODMAP flagged   : {stats['is_high_fodmap']:,}")
    print(f"  GERD trigger flagged  : {stats['is_gerd_trigger']:,}")
    print(f"  Low GI (≤55)          : {stats['is_low_gi']:,}")
    print(f"  High sodium (>400mg)  : {stats['is_high_sodium']:,}")
    print(f"{'─'*50}")
    print("\n✅  Enrichment done. DB ready for ML layer.")

if __name__ == "__main__":
    main()
