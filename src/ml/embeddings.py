"""
src/ml/embeddings.py

Nutrient vector embeddings + sklearn NearestNeighbors (cosine) ANN index.
BAX-423 Techniques: Embeddings + Recommendation (content-based filtering).

Design:
  Each food is encoded as a log-scaled, L2-normalised 15-dim nutrient vector.
  Log scaling handles the extreme range differences between nutrients
  (e.g., calories ≈ 200-500  vs  vitamin B12 ≈ 0.001-5 µg).

  At query time we pass the "gap vector" (remaining daily nutrient needs)
  through the same transform and find the k most similar foods —
  i.e., the foods whose nutrient profiles best fill what's missing for the day.
  This is content-based recommendation: recommend items whose content
  (nutrients) best matches a target (the user's remaining daily needs).
"""

import time
import logging
import sqlite3
import numpy as np
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import normalize

from src.config import DB_PATH

log = logging.getLogger(__name__)

# The 15 nutrient dimensions used in the embedding space
FEATURE_COLS = [
    'calories', 'protein', 'carbs', 'fat', 'fiber',
    'calcium', 'iron', 'potassium', 'magnesium', 'zinc',
    'vitamin_b12', 'vitamin_d', 'vitamin_c',
    'omega3_ala', 'omega3_epa',
]

# Weights that boost nutritionally important-but-rare nutrients in similarity search
FEATURE_WEIGHTS = np.array([
    0.8,   # calories      — important but not the only signal
    1.2,   # protein       — key macro
    0.7,   # carbs         — secondary
    0.7,   # fat           — secondary
    1.3,   # fiber         — often deficient
    1.2,   # calcium       — frequently deficient
    1.5,   # iron          — frequently deficient (especially female)
    1.2,   # potassium     — DASH priority
    1.2,   # magnesium     — DASH priority
    1.2,   # zinc          — often under-reported
    2.0,   # vitamin_b12   — critical for vegan/vegetarian
    1.8,   # vitamin_d     — widely deficient
    1.0,   # vitamin_c     — generally sufficient in fruits/veg
    1.5,   # omega3_ala    — important for vegan omega-3
    2.0,   # omega3_epa    — marine omega-3, pescatarian priority
], dtype=np.float32)


class NutrientEmbedder:
    """
    Builds an ANN index over a filtered candidate food pool.
    Re-built each generation run (takes < 0.5s for 5,000-8,000 foods).

    Technique 1 — Embeddings:
      Each food → 15-dim weighted log-scaled L2-normalised vector.

    Technique 2 — Content-Based Recommendation:
      Query with gap vector (remaining daily needs) →
      retrieve k foods whose nutrient profile best fills the gap.
    """

    def __init__(self):
        self.fdc_ids  = []
        self.matrix   = None
        self.nn_model = None
        self.build_ms = 0.0
        self.n_foods  = 0

    def _transform(self, raw: np.ndarray) -> np.ndarray:
        """Apply weights → log1p → L2 normalise."""
        weighted = raw * FEATURE_WEIGHTS
        logged   = np.log1p(weighted)
        return normalize(logged, norm='l2')

    def fit(self, rows: list):
        """
        Build ANN index from a list of food dicts.
        Each dict must contain all FEATURE_COLS keys.
        """
        t0 = time.perf_counter()
        self.fdc_ids = [r['fdc_id'] for r in rows]
        raw = np.array(
            [[float(r.get(f) or 0) for f in FEATURE_COLS] for r in rows],
            dtype=np.float32
        )
        self.matrix   = self._transform(raw)
        self.nn_model = NearestNeighbors(
            n_neighbors=min(60, len(rows)),
            metric='cosine',
            algorithm='brute',
            n_jobs=-1,
        )
        self.nn_model.fit(self.matrix)
        self.n_foods  = len(rows)
        self.build_ms = (time.perf_counter() - t0) * 1000
        log.info(f"NutrientEmbedder: indexed {self.n_foods:,} foods in {self.build_ms:.1f} ms")

    def query(self, gap_nutrients: dict, k: int = 60) -> list:
        """
        Find k food IDs whose nutrient profiles best fill the gap vector.
        gap_nutrients: {col → remaining_need_value}
        Returns list of fdc_ids sorted by cosine similarity (closest first).
        """
        if self.nn_model is None or self.n_foods == 0:
            return self.fdc_ids[:k]

        raw_vec = np.array(
            [float(gap_nutrients.get(f, 0)) for f in FEATURE_COLS],
            dtype=np.float32,
        ).reshape(1, -1)
        vec = self._transform(raw_vec)

        k_safe = min(k, self.n_foods)
        distances, indices = self.nn_model.kneighbors(vec, n_neighbors=k_safe)
        return [self.fdc_ids[i] for i in indices[0]]

    def load_candidates(self, db_path: str, where_clause: str,
                        params: tuple = ()) -> list:
        """
        Load filtered foods from SQLite and return as list of dicts.
        Also calls fit() on the result.
        """
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        sql = f"""
            SELECT fdc_id, name, food_category, food_group_tag,
                   calories, protein, carbs, fat, fiber,
                   calcium, iron, sodium, potassium, magnesium, zinc,
                   vitamin_b12, vitamin_d, vitamin_c,
                   omega3_ala, omega3_epa, omega3_dha,
                   gi_estimate, is_low_gi, is_high_sodium,
                   suitable_breakfast, suitable_lunch, suitable_dinner, suitable_snack,
                   is_high_fodmap, fodmap_triggers,
                   is_gerd_trigger, gerd_reasons,
                   has_gluten, has_dairy, has_tree_nuts, has_shellfish,
                   has_soy, has_eggs, has_peanuts, has_fish,
                   has_meat, has_pork, has_honey
            FROM foods
            WHERE calories > 30
              AND {where_clause}
        """
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.close()
        log.info(f"Loaded {len(rows):,} candidate foods from DB")
        if rows:
            self.fit(rows)
        return rows
