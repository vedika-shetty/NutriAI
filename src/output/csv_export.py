"""
src/output/csv_export.py

Exports the 7-day meal plan to three CSV files:
  meal_plan.csv      — 21 rows, one per meal, full nutrient breakdown
  daily_summary.csv  — 7 rows, one per day, totals + RDA % + gaps
  exclusions.csv     — excluded foods with clinical/allergen reasons

Also provides export_csv_bytes() for Streamlit in-memory download buttons.
"""

import csv
import io
import zipfile
from datetime import date as dt
from pathlib import Path


MEAL_COLS = [
    'day', 'slot', 'food_name', 'description', 'food_group',
    'serving_g', 'gi_estimate',
    'calories_kcal', 'protein_g', 'carbs_g', 'fat_g', 'fiber_g',
    'iron_mg', 'calcium_mg', 'sodium_mg', 'potassium_mg',
    'magnesium_mg', 'zinc_mg', 'vitamin_b12_mcg',
    'vitamin_d_iu', 'vitamin_c_mg', 'omega3_ala_g',
]

DAY_COLS = [
    'day',
    'total_calories_kcal', 'target_calories_kcal',
    'total_protein_g',     'target_protein_g',
    'total_carbs_g',       'total_fat_g', 'total_fiber_g',
    'total_iron_mg',       'pct_iron_rda',
    'total_calcium_mg',    'pct_calcium_rda',
    'total_sodium_mg',     'sodium_cap_mg',
    'total_potassium_mg',  'pct_potassium_rda',
    'total_magnesium_mg',  'pct_magnesium_rda',
    'total_zinc_mg',       'pct_zinc_rda',
    'total_vitamin_b12_mcg', 'pct_b12_rda',
    'total_vitamin_d_iu',    'pct_vitamin_d_rda',
    'total_vitamin_c_mg',    'pct_vitamin_c_rda',
    'gaps_flagged', 'gap_details',
]

EXCL_COLS = [
    'food_name', 'food_group', 'calories_kcal',
    'primary_reason', 'all_reasons',
]


def _f(d: dict, key: str, default=0.0):
    v = d.get(key, default)
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return default


def build_meal_rows(plan: dict) -> list:
    rows = []
    for day in plan.get('days', []):
        for slot in ('breakfast', 'lunch', 'dinner'):
            meal = day.get('meals', {}).get(slot)
            if not meal:
                continue
            n = meal.get('nutrients', {})
            rows.append({
                'day':              day['day'],
                'slot':             slot,
                'food_name':        meal.get('name', ''),
                'description':      meal.get('description', ''),
                'food_group':       meal.get('food_group', ''),
                'serving_g':        meal.get('serving_g', 100),
                'gi_estimate':      meal.get('gi_estimate', ''),
                'calories_kcal':    round(_f(n, 'calories'), 1),
                'protein_g':        round(_f(n, 'protein'),  2),
                'carbs_g':          round(_f(n, 'carbs'),    2),
                'fat_g':            round(_f(n, 'fat'),      2),
                'fiber_g':          round(_f(n, 'fiber'),    2),
                'iron_mg':          round(_f(n, 'iron'),     2),
                'calcium_mg':       round(_f(n, 'calcium'),  1),
                'sodium_mg':        round(_f(n, 'sodium'),   1),
                'potassium_mg':     round(_f(n, 'potassium'),1),
                'magnesium_mg':     round(_f(n, 'magnesium'),1),
                'zinc_mg':          round(_f(n, 'zinc'),     3),
                'vitamin_b12_mcg':  round(_f(n, 'vitamin_b12'), 3),
                'vitamin_d_iu':     round(_f(n, 'vitamin_d'),   1),
                'vitamin_c_mg':     round(_f(n, 'vitamin_c'),   1),
                'omega3_ala_g':     round(_f(n, 'omega3_ala'),  3),
            })
    return rows


def build_day_rows(plan: dict) -> list:
    rda  = plan.get('rda', {})
    rows = []

    def pct(totals, col, rda_key):
        r = float(rda.get(rda_key, 0) or 0)
        return round(_f(totals, col) / r * 100, 1) if r else 0.0

    for day in plan.get('days', []):
        t    = day.get('day_totals', {})
        gaps = day.get('rda_gaps', [])
        gap_details = '; '.join(
            f'{g["display_name"]} {g["pct"]}% ({g["flag_type"]})'
            for g in gaps
        )
        rows.append({
            'day':                   day['day'],
            'total_calories_kcal':   round(_f(t,'calories'),    1),
            'target_calories_kcal':  plan.get('profile',{}).get('calorie_target',2000),
            'total_protein_g':       round(_f(t,'protein'),     1),
            'target_protein_g':      round(float(rda.get('protein_g',0)),1),
            'total_carbs_g':         round(_f(t,'carbs'),       1),
            'total_fat_g':           round(_f(t,'fat'),         1),
            'total_fiber_g':         round(_f(t,'fiber'),       1),
            'total_iron_mg':         round(_f(t,'iron'),        1),
            'pct_iron_rda':          pct(t,'iron','iron_mg'),
            'total_calcium_mg':      round(_f(t,'calcium'),     1),
            'pct_calcium_rda':       pct(t,'calcium','calcium_mg'),
            'total_sodium_mg':       round(_f(t,'sodium'),      1),
            'sodium_cap_mg':         rda.get('sodium_mg', 1500),
            'total_potassium_mg':    round(_f(t,'potassium'),   1),
            'pct_potassium_rda':     pct(t,'potassium','potassium_mg'),
            'total_magnesium_mg':    round(_f(t,'magnesium'),   1),
            'pct_magnesium_rda':     pct(t,'magnesium','magnesium_mg'),
            'total_zinc_mg':         round(_f(t,'zinc'),        2),
            'pct_zinc_rda':          pct(t,'zinc','zinc_mg'),
            'total_vitamin_b12_mcg': round(_f(t,'vitamin_b12'), 3),
            'pct_b12_rda':           pct(t,'vitamin_b12','vitamin_b12_mcg'),
            'total_vitamin_d_iu':    round(_f(t,'vitamin_d'),   1),
            'pct_vitamin_d_rda':     pct(t,'vitamin_d','vitamin_d_iu'),
            'total_vitamin_c_mg':    round(_f(t,'vitamin_c'),   1),
            'pct_vitamin_c_rda':     pct(t,'vitamin_c','vitamin_c_mg'),
            'gaps_flagged':          len(gaps),
            'gap_details':           gap_details,
        })
    return rows


def build_exclusion_rows(plan: dict) -> list:
    rows = []
    for ex in plan.get('exclusions', []):
        rows.append({
            'food_name':      ex.get('name', ''),
            'food_group':     ex.get('group', ''),
            'calories_kcal':  ex.get('calories', ''),
            'primary_reason': ex.get('primary_reason', ''),
            'all_reasons':    ' | '.join(ex.get('reasons', [])),
        })
    return rows


def _to_csv_bytes(cols: list, rows: list) -> bytes:
    buf = io.StringIO()
    w   = csv.DictWriter(buf, fieldnames=cols, extrasaction='ignore',
                         lineterminator='\n')
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode('utf-8')


def export_csv(plan: dict, output_dir: str) -> dict:
    """
    Write three CSVs + one ZIP bundle to output_dir.
    Returns {label: filepath}.
    """
    out    = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    name   = str(plan.get('profile',{}).get('name','user')).lower().replace(' ','_')
    prefix = f'nutriai_{name}_{dt.today().isoformat()}'
    paths  = {}

    for label, cols, rows in [
        ('meal_plan',     MEAL_COLS, build_meal_rows(plan)),
        ('daily_summary', DAY_COLS,  build_day_rows(plan)),
        ('exclusions',    EXCL_COLS, build_exclusion_rows(plan)),
    ]:
        p = out / f'{prefix}_{label}.csv'
        p.write_bytes(_to_csv_bytes(cols, rows))
        paths[label] = str(p)

    zip_path = out / f'{prefix}_full_export.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for label, fpath in paths.items():
            zf.write(fpath, Path(fpath).name)
    paths['zip'] = str(zip_path)
    return paths


def export_csv_bytes(plan: dict) -> dict:
    """
    Return CSV content as bytes dicts — for Streamlit st.download_button().
    Returns: {'meal_plan': bytes, 'daily_summary': bytes, 'exclusions': bytes}
    """
    return {
        'meal_plan':     _to_csv_bytes(MEAL_COLS, build_meal_rows(plan)),
        'daily_summary': _to_csv_bytes(DAY_COLS,  build_day_rows(plan)),
        'exclusions':    _to_csv_bytes(EXCL_COLS, build_exclusion_rows(plan)),
    }
