"""
src/nutrition/rda.py
RDA lookup, per-meal slot targets, daily aggregation, gap analysis,
and Simpson's Diversity Index for the plan.
Source: NIH Dietary Reference Intakes https://www.ncbi.nlm.nih.gov/books/NBK56068
"""
import math
from src.config import RDA_FEMALE, RDA_MALE

SLOT_FRACTIONS = {'breakfast': 0.25, 'lunch': 0.35, 'dinner': 0.35, 'snack': 0.05}

NUTRIENT_META = {
    # col_key : (rda_dict_key, display_name, unit)
    'calories':    ('calories',        'Calories',     'kcal'),
    'protein':     ('protein_g',       'Protein',      'g'),
    'carbs':       ('carbs_g',         'Carbohydrates','g'),
    'fat':         ('fat_g',           'Fat',          'g'),
    'fiber':       ('fiber_g',         'Fibre',        'g'),
    'calcium':     ('calcium_mg',      'Calcium',      'mg'),
    'iron':        ('iron_mg',         'Iron',         'mg'),
    'sodium':      ('sodium_mg',       'Sodium',       'mg'),
    'potassium':   ('potassium_mg',    'Potassium',    'mg'),
    'magnesium':   ('magnesium_mg',    'Magnesium',    'mg'),
    'zinc':        ('zinc_mg',         'Zinc',         'mg'),
    'vitamin_b12': ('vitamin_b12_mcg', 'Vitamin B12',  'µg'),
    'vitamin_d':   ('vitamin_d_iu',    'Vitamin D',    'IU'),
    'vitamin_c':   ('vitamin_c_mg',    'Vitamin C',    'mg'),
    'omega3_ala':  ('omega3_g',        'Omega-3 (ALA)','g'),
}

def get_age_band(age: int) -> str:
    if age <= 30:   return '18-30'
    elif age <= 50: return '31-50'
    else:           return '51+'

def get_rda(age: int, sex: str) -> dict:
    """Full daily RDA dict for an age/sex profile."""
    table = RDA_FEMALE if sex.lower() in ('female','f','woman','w') else RDA_MALE
    return table[get_age_band(age)]

def get_slot_targets(age: int, sex: str, calorie_target: int, slot: str) -> dict:
    """
    Scale daily RDA to per-meal-slot targets using the user's calorie goal.
    Calorie scaling is applied to all nutrients proportionally.
    """
    rda    = get_rda(age, sex)
    frac   = SLOT_FRACTIONS.get(slot, 0.33)
    scale  = (frac * calorie_target) / max(rda.get('calories', 2000), 1)
    return {k: round(v * scale, 4) for k, v in rda.items()}

def compute_day_totals(meals: list) -> dict:
    """Sum nutrients across all meals in one day."""
    totals: dict = {}
    for meal in meals:
        for k, v in meal.get('nutrients', {}).items():
            totals[k] = totals.get(k, 0.0) + float(v or 0)
    return totals

# Nutrients that are CAPS (don't include in "still-needed" gap — they are maximums)
_GAP_EXCLUDES = frozenset({'sodium'})

def compute_gap_vector(day_totals: dict, rda: dict) -> dict:
    """
    Remaining nutrient needs as a FRACTION of daily RDA (0.0–1.0).
    Normalised so all nutrients compete on equal footing in the ANN search
    and gap-fill scoring regardless of their native units (mg, g, IU, µg).
    sodium is excluded — it is a cap, not a floor.
    """
    gap = {}
    for col, (rda_key, _, _) in NUTRIENT_META.items():
        if col in _GAP_EXCLUDES:
            continue
        rda_val = rda.get(rda_key, 0)
        if rda_val and rda_val > 0:
            actual = day_totals.get(col, 0.0)
            gap[col] = max(0.0, (rda_val - actual) / rda_val)
    return gap

def flag_rda_gaps(day_totals: dict, rda: dict, threshold: float = 0.8) -> list:
    """
    Return gap dicts for nutrients below threshold * RDA.
    Sodium is treated as a cap (excess flagged), not a floor.
    """
    gaps = []
    for col, (rda_key, display_name, unit) in NUTRIENT_META.items():
        if rda_key not in rda or rda[rda_key] <= 0:
            continue
        actual = day_totals.get(col, 0.0)
        target = rda[rda_key]
        if col == 'sodium':
            # Flag if sodium EXCEEDS 100% of cap
            if actual > target:
                gaps.append({
                    'nutrient': col, 'display_name': display_name,
                    'actual': round(actual, 1), 'target': round(target, 1),
                    'pct': round(actual / target * 100, 1), 'unit': unit,
                    'flag_type': 'excess',
                })
        else:
            pct = actual / target
            if pct < threshold:
                gaps.append({
                    'nutrient': col, 'display_name': display_name,
                    'actual': round(actual, 1), 'target': round(target, 1),
                    'pct': round(pct * 100, 1), 'unit': unit,
                    'flag_type': 'deficit',
                })
    return gaps

def compute_diversity_score(plan_meals: list) -> float:
    """
    Simpson's Diversity Index: D = 1 − Σ(nᵢ/N)²
    Range [0,1]. Higher = more diverse food groups across the plan.
    """
    if not plan_meals:
        return 0.0
    groups: dict = {}
    for m in plan_meals:
        g = m.get('food_group', 'other')
        groups[g] = groups.get(g, 0) + 1
    N = len(plan_meals)
    return round(1.0 - sum((n / N) ** 2 for n in groups.values()), 3)
