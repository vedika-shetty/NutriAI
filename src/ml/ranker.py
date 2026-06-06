"""
src/ml/ranker.py

Multi-objective meal ranker + diversity engine.
BAX-423 Technique: Ranking.

Scoring dimensions (weighted sum → final score 0.0-1.0):
  W1 = 0.20  Calorie fit      — delivered kcal (= food × serving_g/100) vs slot target
  W2 = 0.32  Nutrient gap fill — does this food address today's deficit
  W3 = 0.23  Diversity        — penalise over-represented food groups
  W4 = 0.17  Priority micro   — reward declared priority micronutrients
  W5 = 0.08  Clinical bonus   — reward condition-specific beneficial foods

Diversity Engine:
  Tracks food_group_tag usage across all 21 meal slots.
  Penalty = 1/(1 + used_count * decay), decay=0.7
  Guarantees no group dominates the week even without explicit de-duplication.
  Hard uniqueness check on fdc_id prevents any food repeating.
"""

import logging
import math
from src.nutrition.rda import SLOT_FRACTIONS, NUTRIENT_META
from src.config import SERVING_SIZES

log = logging.getLogger(__name__)

# Slot calorie targets derived from a 2000 kcal baseline * fraction
SLOT_CAL_BASE = {
    'breakfast': 500,
    'lunch':     700,
    'dinner':    700,
    'snack':     100,
}

# Map user-visible priority names → DB column(s)
PRIORITY_NUTRIENT_MAP = {
    'iron':       'iron',
    'calcium':    'calcium',
    'vitamin_b12':'vitamin_b12',
    'vitamin_d':  'vitamin_d',
    'zinc':       'zinc',
    'potassium':  'potassium',
    'magnesium':  'magnesium',
    'omega3':     'omega3_ala',   # also checks omega3_epa in _priority_score
    'fiber':      'fiber',
    'vitamin_c':  'vitamin_c',
}

# Per-100g thresholds at which a food scores 1.0 for each priority nutrient.
# Calibrated to a "good dietary source" — high enough to distinguish rich sources
# from average foods, not set to the full daily RDA.
_PRIORITY_THRESHOLDS = {
    'iron':        5.0,    # lentils 3.3 → 0.66, beef 2.5 → 0.50, fortified 15 → 1.0
    'calcium':   200.0,    # tofu 350 → 1.0, milk 120 → 0.60, egg 56 → 0.28
    'vitamin_b12': 1.0,    # salmon 3.2 → 1.0, egg 1.1 → 1.0, beef 2.6 → 1.0
    'vitamin_d':  50.0,    # salmon 670 → 1.0, egg 82 → 1.0
    'zinc':        3.0,    # beef 8.6 → 1.0, pumpkin seed 7.8 → 1.0, shrimp 1.1 → 0.37
    'potassium': 300.0,    # potato 425 → 1.0, banana 358 → 1.0, tuna 444 → 1.0
    'magnesium':  50.0,    # oats 177 → 1.0, quinoa 64 → 1.0, brown rice 43 → 0.86
    'omega3_ala':  0.5,    # flax 22 → 1.0 (excluded), canola oil 9 → 1.0 (excluded)
    'omega3_epa':  1.0,    # salmon ~1.5 → 1.0, tuna ~0.3 → 0.30
    'fiber':       5.0,    # oats 10.6 → 1.0, lentils 7.9 → 1.0, brown rice 3.5 → 0.70
    'vitamin_c':  30.0,    # broccoli 89 → 1.0, orange 53 → 1.0, potato 19 → 0.64
}

# Per-group diversity decay rates.
# Lower decay = group stays competitive longer = appears more often.
# Legumes and vegetables are nutritionally important and should appear regularly.
_GROUP_DECAY = {
    'legume':    0.30,   # lentils/beans/chickpeas — target ~4/week
    'vegetable': 0.45,   # vegetables — important daily
    'grain':     0.55,   # grains — moderate frequency
    'fruit':     0.55,
    'dairy':     0.60,
    'fish_seafood': 0.60,
    'meat':      0.65,
    'mixed_dish': 0.65,
    'nut_seed':  0.65,
    'egg':       0.65,
    'other':     0.70,   # default
}

# Condition-specific beneficial food signals
CONDITION_BONUSES = {
    'ibs':           {'food_groups': ['vegetable','fruit','legume','egg'],
                      'nutrients':   {'fiber': 5.0}},
    'gerd':          {'food_groups': ['vegetable','legume','grain','fruit'],
                      'nutrients':   {'fiber': 4.0, 'protein': 3.0}},
    'diabetes':      {'food_groups': ['legume','vegetable','nut_seed'],
                      'nutrients':   {'fiber': 8.0, 'protein': 4.0}},
    'hypertension':  {'food_groups': ['fish_seafood','vegetable','fruit','legume'],
                      'nutrients':   {'potassium': 200.0, 'magnesium': 30.0}},
}


class MealRanker:
    """
    Stateful ranker that tracks what has already been selected for diversity.
    One instance per plan-generation run; reset between users.
    """

    def __init__(self, user_profile: dict, rda: dict = None):
        self.calorie_target     = int(user_profile.get('calorie_target', 2000))
        self.priority_nutrients = user_profile.get('priority_nutrients', [])
        self.conditions         = [c.lower() for c in user_profile.get('conditions', [])]
        self.rda                = rda or {}
        self.group_counts:      dict = {}
        self.used_ids:          set  = set()
        self.selected_meals:    list = []
        self.decay              = 0.7

    # ── Individual Scoring Dimensions ────────────────────────────────────────

    def _calorie_score(self, food: dict, slot: str) -> float:
        """
        Gaussian-shaped score comparing delivered calories to slot target.
        delivered = food.calories (kcal/100g) × serving_g / 100
        Peaks at 1.0 when delivered == target, σ = 0.5.
        """
        base_target = SLOT_CAL_BASE.get(slot, 600)
        scale       = self.calorie_target / 2000.0
        target      = base_target * scale
        food_cal    = float(food.get('calories', 0) or 0)
        base_g      = SERVING_SIZES.get(slot, SERVING_SIZES['default'])
        scale       = max(1.0, self.calorie_target / 2000.0)
        serving_g   = base_g * scale
        delivered   = food_cal * serving_g / 100.0
        if target <= 0 or delivered <= 0:
            return 0.3
        ratio = delivered / target
        return float(math.exp(-((ratio - 1.0) ** 2) / (2 * 0.5 ** 2)))

    def _gap_fill_score(self, food: dict, gap: dict) -> float:
        """
        Reward foods that supply nutrients still needed for the day.
        gap values are FRACTIONS of daily RDA remaining (0.0–1.0),
        so all nutrients are weighted on equal footing regardless of units.
        """
        if not gap:
            return 0.5
        total_gap = sum(gap.values())
        if total_gap <= 0:
            return 0.5
        fill = 0.0
        for nutrient, frac_remaining in gap.items():
            if frac_remaining <= 0:
                continue
            food_val = float(food.get(nutrient, 0) or 0)
            if food_val <= 0:
                continue
            # Get daily RDA for this nutrient to normalise the food's contribution
            rda_key = NUTRIENT_META.get(nutrient, (None,))[0]
            rda_val = self.rda.get(rda_key, 0) if rda_key else 0
            if rda_val <= 0:
                continue
            pct_of_rda  = food_val / rda_val          # fraction of daily RDA this food provides
            relative_fill = min(pct_of_rda / frac_remaining, 1.0)
            fill += relative_fill * (frac_remaining / total_gap)
        return min(fill, 1.0)

    def _diversity_score(self, food: dict) -> float:
        """Penalise food groups already well-represented in the plan.
        Uses per-group decay rates so nutritionally important groups
        (legumes, vegetables) stay competitive longer."""
        group = food.get('food_group_tag', 'other')
        count = self.group_counts.get(group, 0)
        decay = _GROUP_DECAY.get(group, self.decay)
        return 1.0 / (1.0 + count * decay)

    def _priority_score(self, food: dict) -> float:
        """
        Reward foods high in user-declared priority micronutrients.
        Uses nutrient-appropriate thresholds so each nutrient is scored fairly
        (potassium threshold 300mg, not 5mg; magnesium threshold 50mg, not 5mg).
        'omega3' checks both ALA and EPA so fish score well for pescatarians.
        """
        if not self.priority_nutrients:
            return 0.5
        scores = []
        for pname in self.priority_nutrients:
            col = PRIORITY_NUTRIENT_MAP.get(pname.lower())
            if col:
                val       = float(food.get(col, 0) or 0)
                threshold = _PRIORITY_THRESHOLDS.get(col, 5.0)
                scores.append(min(val / threshold, 1.0))
                # omega3: also count EPA (marine omega-3 from fish)
                if pname.lower() == 'omega3':
                    val_epa  = float(food.get('omega3_epa', 0) or 0)
                    thr_epa  = _PRIORITY_THRESHOLDS.get('omega3_epa', 1.0)
                    scores[-1] = max(scores[-1], min(val_epa / thr_epa, 1.0))
        return sum(scores) / len(scores) if scores else 0.5

    def _condition_bonus(self, food: dict) -> float:
        """Small bonus for foods that specifically benefit declared conditions."""
        bonus = 0.0
        group = food.get('food_group_tag', 'other')
        for condition in self.conditions:
            cfg = CONDITION_BONUSES.get(condition, {})
            if group in cfg.get('food_groups', []):
                bonus += 0.15
            for nutr, threshold in cfg.get('nutrients', {}).items():
                if float(food.get(nutr, 0) or 0) >= threshold * 0.5:
                    bonus += 0.10
        return min(bonus, 1.0)

    # ── Public API ───────────────────────────────────────────────────────────

    def score(self, food: dict, slot: str, day_gap: dict) -> float:
        """
        Weighted multi-objective score for one food in one meal slot.
        Weights: calorie 28%, gap-fill 22%, diversity 25%, priority 15%, condition 10%
        """
        if int(food.get('fdc_id', 0)) in self.used_ids:
            return -1.0   # Hard exclusion: never repeat a food in the 7-day plan

        s_cal  = self._calorie_score(food, slot)
        s_gap  = self._gap_fill_score(food, day_gap)
        s_div  = self._diversity_score(food)
        s_pri  = self._priority_score(food)
        s_cond = self._condition_bonus(food)

        return (0.20 * s_cal +
                0.32 * s_gap +
                0.23 * s_div +
                0.17 * s_pri +
                0.08 * s_cond)

    def rank(self, candidates: list, slot: str,
             day_gap: dict, top_n: int = 1) -> list:
        """
        Score all candidates and return top_n, sorted best-first.
        Candidates must not be empty; caller should guarantee this.
        """
        scored = []
        for food in candidates:
            s = self.score(food, slot, day_gap)
            if s >= 0:
                scored.append((s, food))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in scored[:top_n]]

    def mark_selected(self, food: dict):
        """Record a selected meal to update diversity tracking."""
        fid   = int(food.get('fdc_id', 0))
        group = food.get('food_group_tag', 'other')
        self.used_ids.add(fid)
        self.group_counts[group] = self.group_counts.get(group, 0) + 1
        self.selected_meals.append(food)

    def compute_diversity_score(self) -> float:
        """
        Simpson's D = 1 − Σ(nᵢ/N)²
        Reports diversity across all meals selected so far.
        """
        if not self.selected_meals:
            return 0.0
        N = len(self.selected_meals)
        return round(1.0 - sum((n / N) ** 2
                               for n in self.group_counts.values()), 3)

    def get_score_breakdown(self, food: dict, slot: str, day_gap: dict) -> dict:
        """Return individual dimension scores for UI display / technical brief."""
        return {
            'calorie_fit':    round(self._calorie_score(food, slot), 3),
            'gap_fill':       round(self._gap_fill_score(food, day_gap), 3),
            'diversity':      round(self._diversity_score(food), 3),
            'priority_micro': round(self._priority_score(food), 3),
            'condition_bonus': round(self._condition_bonus(food), 3),
            'weights':        {'calorie': 0.20, 'gap_fill': 0.32, 'diversity': 0.23,
                               'priority': 0.17, 'condition': 0.08},
            'total':          round(self.score(food, slot, day_gap), 3),
        }
