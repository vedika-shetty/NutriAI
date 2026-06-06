#!/usr/bin/env python3
"""
build_database.py
One-time script: pulls ≥10,000 food records from USDA FoodData Central
and writes nutriai_foods.db.  Expected runtime: 10-15 minutes.
"""
import sys, time

def main():
    print("=" * 60)
    print("  NutriAI — USDA Database Builder")
    print("=" * 60)
    print("Fetching Foundation + SR Legacy + Survey (FNDDS) foods.")
    print("Do NOT close this window. Expected: 10-15 minutes.\n")

    t0 = time.time()
    from src.data_pipeline.usda_fetcher import run_fetch
    from src.config import DB_PATH

    count = run_fetch(DB_PATH)
    elapsed = time.time() - t0

    if count >= 10000:
        print(f"\n✅  SUCCESS: {count:,} records built in {elapsed/60:.1f} min")
        print(f"   Saved to: {DB_PATH}")
    else:
        print(f"\n⚠️  Only {count:,} records (target ≥10,000). Check API key in .env.")
        sys.exit(1)

if __name__ == "__main__":
    main()
