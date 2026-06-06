"""
src/planner/explainer.py

'Why excluded' explanation engine.
For every food removed from the safe candidate pool, generates a precise
clinical, allergen, or diet-based exclusion reason for the UI and PDF export.

Three exclusion categories:
  1. Diet type  — food contains meat/dairy/animal products incompatible with diet
  2. Clinical   — FODMAP trigger, GERD trigger, high GI, high sodium
  3. Allergen   — food is tagged with a declared allergen (belt + suspenders with Bloom)
"""

import sqlite3
from src.config import DB_PATH

ALLERGEN_TO_COL = {
    'gluten':    'has_gluten',
    'dairy':     'has_dairy',
    'tree_nuts': 'has_tree_nuts',
    'shellfish': 'has_shellfish',
    'soy':       'has_soy',
    'eggs':      'has_eggs',
    'peanuts':   'has_peanuts',
    'fish':      'has_fish',
    'meat':      'has_meat',
    'pork':      'has_pork',
    'honey':     'has_honey',
}

# (condition_keyword, db_column, is_exclusion_when_flag_is_1_or_0, reason_fn)
# is_exclusion_when = 1  → exclude when column = 1  (e.g., is_high_fodmap)
# is_exclusion_when = 0  → exclude when column = 0  (e.g., is_low_gi: exclude if NOT low GI)
CLINICAL_RULES = [
    ('ibs',          'is_high_fodmap',  1,
     lambda f: f'High-FODMAP ingredient(s): {f.get("fodmap_triggers") or "contains IBS-trigger compound"} — excluded for IBS-D'),

    ('gerd',         'is_gerd_trigger', 1,
     lambda f: f'GERD/acid reflux trigger: {f.get("gerd_reasons") or "acidic or esophageal-irritating food"} — excluded for GERD'),

    ('acid reflux',  'is_gerd_trigger', 1,
     lambda f: f'Acid reflux trigger: {f.get("gerd_reasons") or "irritating food"} — excluded for acid reflux'),

    ('acidity',      'is_gerd_trigger', 1,
     lambda f: f'Acidity trigger: {f.get("gerd_reasons") or "acidic food"} — excluded for acidity condition'),

    ('diabet',       'is_low_gi',       0,
     lambda f: f'High glycaemic index (GI ≈ {f.get("gi_estimate","?")}) — raises blood sugar rapidly; excluded for Type 2 Diabetes (limit GI ≤ 55)'),

    ('hypertension', 'is_high_sodium',  1,
     lambda f: f'High sodium ({round(float(f.get("sodium") or 0), 0):.0f} mg/100 g) — exceeds DASH diet sodium cap of 1,500 mg/day; excluded for hypertension'),

    ('blood pressure','is_high_sodium', 1,
     lambda f: f'High sodium ({round(float(f.get("sodium") or 0), 0):.0f} mg/100 g) — exceeds DASH sodium cap; excluded for hypertension'),
]


def _diet_reason(food: dict, diet: str) -> str:
    has_meat      = food.get('has_meat', 0)
    has_fish      = food.get('has_fish', 0)
    has_shellfish = food.get('has_shellfish', 0)
    has_dairy     = food.get('has_dairy', 0)
    has_eggs      = food.get('has_eggs', 0)
    has_honey     = food.get('has_honey', 0)

    if 'vegan' in diet:
        parts = []
        if has_meat:                  parts.append('meat')
        if has_fish or has_shellfish: parts.append('fish/seafood')
        if has_dairy:                 parts.append('dairy')
        if has_eggs:                  parts.append('eggs')
        if has_honey:                 parts.append('honey')
        animal_list = ', '.join(parts) if parts else 'animal-derived ingredient'
        return (f"Contains {animal_list} — excluded for vegan diet "
                f"(no animal products of any kind)")

    if 'vegetarian' in diet:
        parts = []
        if has_meat:                  parts.append('meat')
        if has_fish or has_shellfish: parts.append('fish/seafood')
        animal_list = ', '.join(parts) if parts else 'animal product'
        return (f"Contains {animal_list} — excluded for vegetarian diet "
                f"(no meat or fish; eggs and dairy are permitted)")

    if 'pescatarian' in diet:
        return ("Contains land meat — excluded for pescatarian diet "
                "(fish and seafood are permitted, but no other meat)")

    return "Does not meet declared dietary restriction"


def explain_food_exclusion(food: dict, profile: dict) -> list:
    """
    Return list of human-readable exclusion reason strings for one food.
    Checks diet type, clinical conditions, and allergens in that order.
    """
    reasons = []
    diet = profile.get('diet_mode', 'non-vegetarian').lower()

    # 1. Diet type
    if 'vegan' in diet and not food.get('is_vegan', 0):
        reasons.append(_diet_reason(food, 'vegan'))
    elif 'vegetarian' in diet and not food.get('is_vegetarian', 0):
        reasons.append(_diet_reason(food, 'vegetarian'))
    elif 'pescatarian' in diet and not food.get('is_pescatarian', 0):
        reasons.append(_diet_reason(food, 'pescatarian'))

    # 2. Clinical conditions
    conditions_str = ' '.join(profile.get('conditions', [])).lower()
    for cond_kw, col, when_val, reason_fn in CLINICAL_RULES:
        if cond_kw not in conditions_str:
            continue
        food_val = food.get(col, 0)
        triggered = (when_val == 1 and food_val == 1) or \
                    (when_val == 0 and food_val == 0)
        if triggered:
            reason = reason_fn(food)
            if reason not in reasons:
                reasons.append(reason)

    # 3. Allergens
    for allergen in profile.get('allergens', []):
        normalised = allergen.lower().replace(' ', '_').replace('-', '_')
        col        = ALLERGEN_TO_COL.get(normalised)
        if col and food.get(col, 0):
            display = allergen.replace('_', ' ').title()
            if normalised == 'gluten':
                reasons.append(
                    f"Contains gluten — excluded for gluten/coeliac restriction; "
                    f"cross-contamination risk also flagged"
                )
            else:
                reasons.append(
                    f"Contains {display} — excluded due to declared {display} allergy"
                )

    # 4. Explicit pork exclusion
    if profile.get('no_pork', False) and food.get('has_pork', 0):
        reasons.append("Contains pork — excluded per dietary or religious restriction")

    return reasons


def get_exclusion_sample(
    conn:       sqlite3.Connection,
    safe_where: str,
    profile:    dict,
    n:          int = 30,
) -> list:
    """
    Query foods excluded by safe_where clause; derive and return their reasons.
    Returns list of dicts: {fdc_id, name, group, calories, reasons, primary_reason}
    """
    try:
        rows = conn.execute(f"""
            SELECT fdc_id, name, food_category,
                   is_high_fodmap, fodmap_triggers,
                   is_gerd_trigger, gerd_reasons,
                   gi_estimate, is_low_gi,
                   sodium, is_high_sodium,
                   has_gluten, has_dairy, has_tree_nuts, has_shellfish,
                   has_soy, has_eggs, has_peanuts, has_fish,
                   has_meat, has_pork, has_honey,
                   is_vegan, is_vegetarian, is_pescatarian,
                   food_group_tag, calories
            FROM foods
            WHERE NOT ({safe_where})
              AND calories > 10
              AND food_group_tag NOT IN ('beverage','sweet')
              AND name NOT LIKE 'Babyfood%'
              AND name NOT LIKE 'Baby food%'
            ORDER BY RANDOM()
            LIMIT {n * 4}
        """).fetchall()
    except Exception:
        return []

    COLS = [
        'fdc_id','name','food_category',
        'is_high_fodmap','fodmap_triggers',
        'is_gerd_trigger','gerd_reasons',
        'gi_estimate','is_low_gi',
        'sodium','is_high_sodium',
        'has_gluten','has_dairy','has_tree_nuts','has_shellfish',
        'has_soy','has_eggs','has_peanuts','has_fish',
        'has_meat','has_pork','has_honey',
        'is_vegan','is_vegetarian','is_pescatarian',
        'food_group_tag','calories',
    ]

    exclusions = []
    seen_names = set()
    for row in rows:
        food    = dict(zip(COLS, row))
        reasons = explain_food_exclusion(food, profile)
        if reasons and food['name'] not in seen_names:
            seen_names.add(food['name'])
            exclusions.append({
                'fdc_id':         food['fdc_id'],
                'name':           food['name'],
                'group':          food.get('food_group_tag', 'other'),
                'calories':       round(float(food.get('calories') or 0), 1),
                'reasons':        reasons,
                'primary_reason': reasons[0],
            })
        if len(exclusions) >= n:
            break

    return exclusions
