"""
src/data_pipeline/usda_fetcher.py

Fetches food records from USDA FoodData Central API and stores in SQLite.
Data types pulled: Foundation (~1,700), SR Legacy (~7,800), Survey FNDDS (~7,600)
Target: ≥10,000 records, capped at 15,000 for build speed.
All nutrient values stored per 100g as reported by USDA.
"""

import sqlite3
import time
import logging
import requests
from pathlib import Path
from tqdm import tqdm
from src.config import USDA_API_KEY, USDA_BASE_URL, NUTRIENT_IDS, DB_PATH, REQUEST_DELAY, BATCH_SIZE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

FETCH_DATA_TYPES = ["Foundation", "SR Legacy", "Survey (FNDDS)"]
MAX_TOTAL_FOODS = 15000
PAGE_SIZE = 200
NUTRIENT_ID_LIST = list(NUTRIENT_IDS.values())

# USDA abridged format returns nutrients keyed by NDB "number" (string), not nutrientId.
# Mapping: column name → NDB number string used in the API response.
NDB_NUMBERS = {
    "calories":    "208",
    "protein":     "203",
    "carbs":       "205",
    "fat":         "204",
    "fiber":       "291",
    "calcium":     "301",
    "iron":        "303",
    "sodium":      "307",
    "potassium":   "306",
    "magnesium":   "304",
    "zinc":        "309",
    "vitamin_b12": "418",
    "vitamin_d":   "328",
    "vitamin_c":   "401",
    "omega3_ala":  "851",
    "omega3_epa":  "629",
    "omega3_dha":  "621",
}

CREATE_FOODS_SQL = """
CREATE TABLE IF NOT EXISTS foods (
    fdc_id              INTEGER PRIMARY KEY,
    name                TEXT    NOT NULL,
    data_type           TEXT    DEFAULT '',
    food_category       TEXT    DEFAULT '',
    calories            REAL    DEFAULT 0,
    protein             REAL    DEFAULT 0,
    carbs               REAL    DEFAULT 0,
    fat                 REAL    DEFAULT 0,
    fiber               REAL    DEFAULT 0,
    calcium             REAL    DEFAULT 0,
    iron                REAL    DEFAULT 0,
    sodium              REAL    DEFAULT 0,
    potassium           REAL    DEFAULT 0,
    magnesium           REAL    DEFAULT 0,
    zinc                REAL    DEFAULT 0,
    vitamin_b12         REAL    DEFAULT 0,
    vitamin_d           REAL    DEFAULT 0,
    vitamin_c           REAL    DEFAULT 0,
    omega3_ala          REAL    DEFAULT 0,
    omega3_epa          REAL    DEFAULT 0,
    omega3_dha          REAL    DEFAULT 0,
    is_vegan            INTEGER DEFAULT 0,
    is_vegetarian       INTEGER DEFAULT 0,
    is_pescatarian      INTEGER DEFAULT 0,
    has_gluten          INTEGER DEFAULT 0,
    has_dairy           INTEGER DEFAULT 0,
    has_tree_nuts       INTEGER DEFAULT 0,
    has_shellfish       INTEGER DEFAULT 0,
    has_soy             INTEGER DEFAULT 0,
    has_eggs            INTEGER DEFAULT 0,
    has_peanuts         INTEGER DEFAULT 0,
    has_fish            INTEGER DEFAULT 0,
    is_high_fodmap      INTEGER DEFAULT 0,
    fodmap_triggers     TEXT    DEFAULT '',
    is_gerd_trigger     INTEGER DEFAULT 0,
    gerd_reasons        TEXT    DEFAULT '',
    gi_estimate         INTEGER DEFAULT -1,
    is_high_sodium      INTEGER DEFAULT 0,
    suitable_breakfast  INTEGER DEFAULT 1,
    suitable_lunch      INTEGER DEFAULT 1,
    suitable_dinner     INTEGER DEFAULT 1,
    suitable_snack      INTEGER DEFAULT 0,
    food_group_tag      TEXT    DEFAULT 'other',
    enriched            INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_data_type  ON foods(data_type);
CREATE INDEX IF NOT EXISTS idx_group      ON foods(food_group_tag);
CREATE INDEX IF NOT EXISTS idx_enriched   ON foods(enriched);
"""

INSERT_SQL = """
INSERT OR IGNORE INTO foods
    (fdc_id, name, data_type, food_category,
     calories, protein, carbs, fat, fiber,
     calcium, iron, sodium, potassium, magnesium, zinc,
     vitamin_b12, vitamin_d, vitamin_c, omega3_ala, omega3_epa, omega3_dha)
VALUES
    (:fdc_id, :name, :data_type, :food_category,
     :calories, :protein, :carbs, :fat, :fiber,
     :calcium, :iron, :sodium, :potassium, :magnesium, :zinc,
     :vitamin_b12, :vitamin_d, :vitamin_c, :omega3_ala, :omega3_epa, :omega3_dha)
"""


def init_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    for stmt in CREATE_FOODS_SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
    return conn


def _get(url: str, params: dict, retries: int = 3):
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                log.warning("Rate limited — sleeping 5s")
                time.sleep(5)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning(f"GET attempt {attempt+1} failed: {e}")
            time.sleep(1 + attempt)
    return None


def _post(url: str, payload: dict, params: dict, retries: int = 3):
    for attempt in range(retries):
        try:
            resp = requests.post(url, json=payload, params=params, timeout=60)
            if resp.status_code == 429:
                log.warning("Rate limited — sleeping 5s")
                time.sleep(5)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning(f"POST attempt {attempt+1} failed: {e}")
            time.sleep(1 + attempt)
    return None


def fetch_food_ids(data_type: str, cap: int = MAX_TOTAL_FOODS) -> list:
    """Paginated fetch of all food IDs for one USDA data type."""
    ids = []
    page = 1
    while len(ids) < cap:
        data = _get(
            f"{USDA_BASE_URL}/foods/list",
            {"api_key": USDA_API_KEY, "dataType": data_type,
             "pageSize": PAGE_SIZE, "pageNumber": page},
        )
        if not data:
            break
        batch_ids = [item["fdcId"] for item in data if "fdcId" in item]
        ids.extend(batch_ids)
        log.info(f"  {data_type} page {page}: +{len(batch_ids)} → {len(ids)} total")
        if len(batch_ids) < PAGE_SIZE:
            break
        page += 1
        time.sleep(REQUEST_DELAY)
    return ids[:cap]


def parse_food(raw: dict):
    """
    Extract our 21-column nutrient record from a raw USDA abridged food object.
    Abridged format returns foodNutrients as [{number, name, amount, unitName, ...}]
    where 'number' is the legacy NDB number (e.g. "208" for calories), not nutrientId.
    """
    fdc_id = raw.get("fdcId")
    name = (raw.get("description") or "").strip()
    if not fdc_id or not name:
        return None

    # Build nutrient lookup: NDB number string → amount value
    nmap: dict = {}
    for n in raw.get("foodNutrients", []):
        nnum = str(n.get("number", "")).strip()
        val = float(n.get("amount") or 0)
        if nnum:
            nmap[nnum] = val

    cat = raw.get("foodCategory", "")
    if isinstance(cat, dict):
        cat = cat.get("description", "")

    record = {
        "fdc_id":        fdc_id,
        "name":          name[:500],
        "data_type":     raw.get("dataType", ""),
        "food_category": str(cat)[:200],
    }
    for col, nnum in NDB_NUMBERS.items():
        record[col] = nmap.get(nnum, 0.0)

    return record


def batch_fetch(fdc_ids: list) -> list:
    """POST /fdc/v1/foods — fetch abridged nutrient data for up to BATCH_SIZE IDs.
    No 'nutrients' filter: the filter expects legacy NDB numbers and suppresses results
    when passed nutrientIds; omitting it returns all nutrients in abridged format."""
    result = _post(
        f"{USDA_BASE_URL}/foods",
        payload={"fdcIds": fdc_ids, "format": "abridged"},
        params={"api_key": USDA_API_KEY},
    )
    return result if isinstance(result, list) else []


def run_fetch(db_path: str = DB_PATH) -> int:
    """
    Full pipeline:
      Phase 1 — collect food IDs from USDA list endpoint (Foundation, SR Legacy, Survey FNDDS)
      Phase 2 — batch-fetch nutrient details via POST /foods (20 IDs per request)
      Phase 3 — insert into SQLite, skip already-fetched IDs
    Returns total record count in DB.
    """
    t0 = time.time()
    conn = init_db(db_path)

    # ── Phase 1: Collect IDs ──────────────────────────────────────────────────
    log.info("━━━ Phase 1: Collecting food IDs ━━━")
    all_ids = []
    for dtype in FETCH_DATA_TYPES:
        remaining = MAX_TOTAL_FOODS - len(all_ids)
        if remaining <= 0:
            break
        log.info(f"Fetching IDs: {dtype}")
        ids = fetch_food_ids(dtype, cap=remaining)
        all_ids.extend(ids)
        log.info(f"  Subtotal after {dtype}: {len(all_ids):,}")

    log.info(f"Total IDs collected: {len(all_ids):,}")

    # Deduplicate; skip already-fetched
    seen = set()
    deduped = []
    for i in all_ids:
        if i not in seen:
            seen.add(i)
            deduped.append(i)
    all_ids = deduped

    existing = {r[0] for r in conn.execute("SELECT fdc_id FROM foods").fetchall()}
    new_ids = [i for i in all_ids if i not in existing]
    log.info(f"New to fetch: {len(new_ids):,}  (already in DB: {len(existing):,})")

    # ── Phase 2: Batch-fetch nutrients ────────────────────────────────────────
    log.info("\n━━━ Phase 2: Fetching nutrient data ━━━")
    batches = [new_ids[i:i+BATCH_SIZE] for i in range(0, len(new_ids), BATCH_SIZE)]
    inserted = 0

    for batch in tqdm(batches, desc="Nutrient fetch", unit="batch"):
        raw_list = batch_fetch(batch)
        records = [parse_food(r) for r in raw_list]
        records = [r for r in records if r is not None]
        if records:
            conn.executemany(INSERT_SQL, records)
            conn.commit()
            inserted += len(records)
        time.sleep(REQUEST_DELAY)

    # ── Summary ───────────────────────────────────────────────────────────────
    total = conn.execute("SELECT COUNT(*) FROM foods").fetchone()[0]
    by_type = conn.execute(
        "SELECT data_type, COUNT(*) FROM foods GROUP BY data_type ORDER BY COUNT(*) DESC"
    ).fetchall()
    elapsed = time.time() - t0

    log.info("\n━━━ Database Build Complete ━━━")
    log.info(f"Total records : {total:,}")
    for dtype, cnt in by_type:
        log.info(f"  {dtype:<22}: {cnt:,}")
    log.info(f"Elapsed       : {elapsed:.0f}s ({elapsed/60:.1f} min)")

    conn.close()
    return total
