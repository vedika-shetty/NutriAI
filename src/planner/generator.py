"""
src/planner/generator.py

Core 7-day meal plan generator with streaming output.

Pipeline per meal slot:
  1. Compute nutrient gap vector from meals accumulated so far today
  2. ANN query: retrieve top-80 foods by cosine similarity to gap (Embeddings + Recommendation)
  3. Bloom filter double-check on allergen safety (Sketching — zero false negatives)
  4. Slot-suitability soft filter (breakfast/lunch/dinner)
  5. Multi-objective ranking → select top food (Ranking)
  6. Generate restaurant-style description (Templates)
  7. Yield meal to caller (Streaming)

BAX-423 techniques: Sketching, Embeddings, Recommendation, Ranking, Streaming (all 5).
"""

import time
import logging
import sqlite3
from typing import Generator

from src.config import DB_PATH
from src.ml.bloom_filter import AllergenFilterBank
from src.ml.embeddings import NutrientEmbedder
from src.ml.ranker import MealRanker
from src.nutrition.rda import get_rda, compute_gap_vector, flag_rda_gaps
from src.planner.meal_templates import generate_description
from src.planner.explainer import get_exclusion_sample

log = logging.getLogger(__name__)

# Serving sizes in grams per slot; USDA nutrient values are per 100 g
SERVING_G = {'breakfast': 150, 'lunch': 250, 'dinner': 350, 'snack': 50}

NUTRIENT_COLS = [
    'calories','protein','carbs','fat','fiber',
    'calcium','iron','sodium','potassium','magnesium','zinc',
    'vitamin_b12','vitamin_d','vitamin_c',
    'omega3_ala','omega3_epa','omega3_dha',
]

ALLERGEN_COL_MAP = {
    'gluten':    'has_gluten',
    'dairy':     'has_dairy',
    'tree_nuts': 'has_tree_nuts',
    'shellfish': 'has_shellfish',
    'soy':       'has_soy',
    'eggs':      'has_eggs',
    'peanuts':   'has_peanuts',
    'fish':      'has_fish',
    'pork':      'has_pork',
    'honey':     'has_honey',
}

MEAL_SLOTS = ['breakfast', 'lunch', 'dinner']

# Keywords that identify processed/packaged breakfast items to cap at ≤2/week.
# Includes boxed cereals AND frozen waffles so they don't dominate every morning.
_BOXED_CEREAL_KW = ('cereal', 'ready-to-eat', 'corn puffs', 'rice puffs',
                    'corn flakes', 'corn squares', 'oat squares',
                    'waffle', 'pancake', 'muffin')

def _is_boxed_cereal(food: dict) -> bool:
    n = food.get('name', '').lower()
    return any(kw in n for kw in _BOXED_CEREAL_KW)


def build_where_clause(profile: dict) -> str:
    """
    Build a SQL WHERE clause from user profile.
    Clinical + diet filters applied at SQL level.
    Allergen filters applied here AND via Bloom filter (belt-and-suspenders).
    """
    clauses = [
        "calories > 60",         # allow lower-calorie whole foods (oysters ~67, legumes ~120)
        "calories < 500",       # exclude hyper-caloric concentrated foods (>500 kcal/100g)
        "fiber <= 15",          # exclude bran/psyllium supplements that aren't standalone meals
        "iron <= 45",           # cap hyper-fortified cereals (>45 mg/100g is supplement-level)
        "food_group_tag NOT IN ('beverage','sweet','fat_oil')",
        # Non-meal items that slip through group tagging
        "name NOT LIKE 'Beverage%'",
        "name NOT LIKE 'Beverages%'",
        "name NOT LIKE '%coleslaw%'",           # side salad, not a standalone meal
        "name NOT LIKE '%for use on%'",         # "Avocado, for use on a sandwich"
        "name NOT LIKE '%, sun-dried'",         # sun-dried tomatoes = condiment
        "name NOT LIKE 'Sun-dried%'",           # alternate capitalisation
        "name NOT LIKE 'Tomatoes, sun-dried%'", # USDA SR legacy name
        "name NOT LIKE 'Incaparina%'",          # Central American supplement powder, not a meal
        "name NOT LIKE 'Cured %'",              # "Cured beef", "Cured ham" — processed/industrial
        "name NOT LIKE '%, cured'",             # "Beef, cured" USDA pattern
        "name NOT LIKE '%, cured,%'",           # "Beef, cured, dried"
        "name NOT LIKE '%dry milk%'",           # industrial ingredient, not a meal
        "name NOT LIKE 'Milk, dry%'",           # USDA SR Legacy dry milk naming
        "name NOT LIKE '%milk powder%'",        # powdered milk variants
        "name NOT LIKE '%cheese product%'",     # processed cheese product (not real cheese)
        "name NOT LIKE '%cheese spread%'",      # condiment cheese
        "name NOT LIKE '%kanpyo%'",             # Japanese dried gourd strips — cooking ingredient
        "name NOT LIKE '%breakfast bar%'",      # granola/date bars not standalone meals
        "name NOT LIKE '% cereal bar%'",        # cereal bar variants
        "name NOT LIKE '% fruit bar%'",         # pressed fruit bars
        "name NOT LIKE '% flour'",              # names ending in " flour" (oat flour, etc.)
        "name NOT LIKE '%Flour, %'",            # "Flour, wheat, bleached" USDA pattern
        "name NOT LIKE '% bran, crude'",        # raw unprocessed bran — not a standalone meal
        "name NOT LIKE '%Gums, seed%'",         # industrial food gums, not meals
        "name NOT LIKE '%freeze-dried%'",       # dehydrated concentrate — too calorie-dense at meal portions
        "name NOT LIKE 'Chickpea flour%'",      # raw ingredient (387 kcal/100g) mis-selected as a meal
        "name NOT LIKE '%french toast%'",       # always contains eggs (FNDDS tags has_eggs=0 incorrectly)
        "name != 'Steak teriyaki'",             # FNDDS Survey food mis-tagged is_vegetarian=1
        "name NOT LIKE '%Italian sandwich%'",   # restaurant sub with deli meats mis-tagged is_vegetarian=1
        "name NOT LIKE '%pizza%meat%'",         # "Pizza, meat topping", "Pizza with extra meat" mis-tagged
        "name NOT LIKE '%extra meat%'",         # "Pizza with extra meat..." variants
        "name NOT LIKE 'Paella%'",              # Spanish rice dish with seafood/chicken mis-tagged
        "name NOT LIKE '%hot pocket%'",         # pocket pastries with meat filling mis-tagged
        "name NOT LIKE '%pepperoni%'",          # pepperoni pizza mis-tagged (pork sausage)
        "name NOT LIKE 'Chimichanga%'",         # fried burrito with meat mis-tagged

        # Single-ingredient fresh herbs / seasonings selected as meals (USDA enrichment misses these)
        "name NOT LIKE 'Rosemary%'",
        "name NOT LIKE 'Thyme%'",
        "name NOT LIKE 'Basil%'",
        "name NOT LIKE 'Oregano%'",
        "name NOT LIKE 'Parsley%'",
        "name NOT LIKE 'Cilantro%'",
        "name NOT LIKE 'Dill%'",
        "name NOT LIKE 'Chive%'",
        "name NOT LIKE 'Tarragon%'",
        "name NOT LIKE 'Marjoram%'",
        "name NOT LIKE 'Spearmint%'",
        "name NOT LIKE 'Peppermint%'",
        "name NOT LIKE 'Coriander%'",

        # Survey FNDDS single-word game meats (mis-tagged is_vegetarian=1)
        "name NOT LIKE 'Beaver%'",
        "name NOT LIKE 'Raccoon%'",
        "name NOT LIKE 'Squirrel%'",
        "name NOT LIKE 'Muskrat%'",
        "name NOT LIKE 'Caribou%'",
        "name NOT LIKE 'Opossum%'",
        "name NOT LIKE 'Dove%'",       # Dove/squab (bird) mis-tagged vegetarian
        "name NOT LIKE 'Bison%'",
        "name NOT LIKE 'Venison%'",
        "name NOT LIKE 'Game meat%'",

        # Cookies, pastries, desserts — not standalone meals
        "name NOT LIKE '%cookie%'",
        "name NOT LIKE '%Cookie%'",
        "name NOT LIKE '%Oreo%'",
        "name NOT LIKE 'Danish pastry%'",
        "name NOT LIKE '%danish pastry%'",
        "name NOT LIKE '%croissant%'",
        "name NOT LIKE '%Croissant%'",
        "name NOT LIKE '%donut%'",
        "name NOT LIKE '%doughnut%'",
        "name NOT LIKE '%brownie%'",
        "name NOT LIKE '%Brownie%'",
        "name NOT LIKE 'Liver%'",       # liver paste/pate mis-tagged vegetarian
        "name NOT LIKE '%swiss steak%'",
        "name NOT LIKE '%Swiss steak%'",
        "name NOT LIKE '%sirloin steak%'",
        "name NOT LIKE '%sirloin%steak%'",
        "name NOT LIKE '%steak tartare%'",
        "name NOT LIKE '%Steak tartare%'",

        # Cakes and dessert items mis-tagged into fruit/other food groups
        "name NOT LIKE 'Cake,%'",
        "name NOT LIKE '%cupcake%'",
        "name NOT LIKE '% cake,%'",     # "Cheesecake, ..." etc.
        "name NOT LIKE '% cake'",       # names ending in " cake"

        # Meat-filled pasta mis-tagged as vegetarian in Survey FNDDS
        "name NOT LIKE '%meat-filled%'",
        "name NOT LIKE '%meat filled%'",

        # Standalone FNDDS organ meat entries (mis-tagged is_vegetarian=1)
        # Use != to exclude exact single-word names only (safe: won't exclude
        # 'Kidney beans' or 'Hearts of palm' which are legitimate foods)
        "name != 'Kidney'",
        "name != 'Heart'",
        "name != 'Tongue'",
        "name != 'Tripe'",
        "name != 'Spleen'",

        # Sandwich spreads / condiment-type spreads — not standalone meals
        "name NOT LIKE '%sandwich spread%'",
        "name NOT LIKE 'Sandwich spread%'",

        # Branded dry baking mixes (not ready-to-eat meals)
        "name NOT LIKE 'Continental Mills%'",
        "name NOT LIKE '%Muffin Mix%'",

        # Natto deduplication — keep SR Legacy (172443), drop Survey FNDDS duplicate
        "fdc_id != 2707440",

        # Quiche/custard always contain eggs — filter for egg-free profiles
        "name NOT LIKE '%quiche%'",
        "name NOT LIKE '%Quiche%'",


        # Shellfish and meat items mis-tagged as vegetarian in Survey FNDDS
        "name NOT LIKE '%Clams Casino%'",
        "name NOT LIKE '%clams casino%'",
        "name NOT LIKE '%Sloppy joe%'",
        "name NOT LIKE '%sloppy joe%'",
        "name NOT LIKE '%cold cut%'",    # deli meat sub sandwich mis-tagged

        # Industrial seed ingredients (meal, flour) — not standalone meals
        "name NOT LIKE 'Seeds,%meal%'",
        "name NOT LIKE 'Seeds,%flour%'",
        "name NOT LIKE '%cottonseed%'",
        "name NOT LIKE '%safflower seed meal%'",

        # Fast-food branded sandwiches with likely meat/dairy content
        "name NOT LIKE '%reuben%'",
        "name NOT LIKE '%Reuben%'",
        "name NOT LIKE '%caviar%'",             # fish eggs (may be tagged other/mixed)
        "name NOT LIKE '% roe%'",               # fish roe
        "name NOT LIKE 'Fish roe%'",
        "name NOT LIKE '%fish roe%'",
        "name NOT LIKE '%, pickled'",           # pickled condiments as standalone
        "name NOT LIKE 'Pickle%'",
        "name NOT LIKE '%relish%'",
        "name NOT LIKE '%, stuffing'",          # stuffing mix
        "name NOT LIKE 'Stuffing%'",
        "name NOT LIKE 'Babyfood%'",
        "name NOT LIKE 'Baby food%'",
        "name NOT LIKE 'Baby %'",
        "name NOT LIKE 'Toddler%'",
        "name NOT LIKE 'Infant%'",
        "name NOT LIKE '%formula%'",
        "name NOT LIKE '%Gerber%'",
        "name NOT LIKE '%Slim Fast%'",
        "name NOT LIKE '%SlimFast%'",
        "name NOT LIKE '%meal replacement%'",
        "name NOT LIKE '%Nutritional powder%'",
        "name NOT LIKE '%supplement%'",
        "name NOT LIKE '%Margarine%'",
        "name NOT LIKE '%margarine%'",
        "name NOT LIKE '%marshmallow%'",
        "name NOT LIKE '%Marshmallow%'",
        "name NOT LIKE '%Nutrition bar%'",
        "name NOT LIKE '%nutrition bar%'",
        "name NOT LIKE '%protein bar%'",
        "name NOT LIKE '%energy bar%'",
        "name NOT LIKE '%granola bar%'",
        "name NOT LIKE '%Granola bar%'",
        "name NOT LIKE '%chips, salted%'",
        "name NOT LIKE '%Chips, salted%'",
        "name NOT LIKE '% chips%'",
        "name NOT LIKE 'Snacks,%'",
        "name NOT LIKE 'Snack,%'",
        "name NOT LIKE 'Snack mix%'",
        "name NOT LIKE 'Snack bar%'",   # "Snack bar, oatmeal" — not a standalone meal
        "name NOT LIKE 'Topping from%'",
        "name NOT LIKE '%, dried'",    # concentrated dried whole foods (too calorie-dense)
        "name NOT LIKE 'Spices,%'",    # spices are not meals
        "name NOT LIKE 'Herbs,%'",
        "name NOT LIKE '%, sauce'",    # standalone sauces
        "name NOT LIKE 'Sauce,%'",
        "name NOT LIKE '%, gravy'",    # gravies
        "name NOT LIKE 'Gravy,%'",
        "name NOT LIKE '%, dressing'", # salad dressings as standalone
        "name NOT LIKE '% dressing'",
        "name NOT LIKE 'Dressing%'",
        "name NOT LIKE 'Salad dressing%'",  # "Salad dressing, ranch, ..."
        "name NOT LIKE '%mousse%'",    # dessert mousse
        "name NOT LIKE '%Mousse%'",
        "name NOT LIKE '% dip'",       # dips/condiments
        "name NOT LIKE 'Vinegar%'",    # condiments, not meals
        "name NOT LIKE 'Lemon juice%'",
        "name NOT LIKE 'Lime juice%'",
        "name NOT LIKE 'Sugar%'",      # pure sugar is not a meal
        "name NOT LIKE 'Salt%'",       # pure salt is not a meal
        "name NOT LIKE 'Baking%'",     # baking powder/soda
        "name NOT LIKE '%pudding mix%'",
        "name NOT LIKE '% mix, dry'",  # dry mixes are not meals
        "name NOT LIKE '%, raw'",      # raw unprocessed ingredients (e.g. "Beef, raw")
        "name NOT LIKE '%, raw (%'",   # raw items with parenthetical origin (Alaska Native, etc.)
        "name NOT LIKE '%(Alaska Native)%'", # wild/unusual regional ingredients
        "name NOT LIKE '%(Shoshone Bannock)%'",
        "name NOT LIKE '%(USDA%'",     # FDPIR distribution program items
        "name NOT LIKE '%variety meats%'",  # organ meats: brain, tongue, kidney, tripe, etc.
        "name NOT LIKE '%, liver%'",   # fish liver, organ liver

        # Exotic / game meats — restrict to common supermarket cuts
        # (beef, pork, lamb, mutton, chicken, turkey are allowed)
        "name NOT LIKE '%veal%'",
        "name NOT LIKE '%venison%'",
        "name NOT LIKE '%bison%'",
        "name NOT LIKE '%rabbit%'",
        "name NOT LIKE '%pheasant%'",
        "name NOT LIKE '%quail%'",
        "name NOT LIKE '%ostrich%'",
        "name NOT LIKE 'Emu%'",         # exotic meat — incorrectly tagged is_vegetarian=1
        "name NOT LIKE '%goose%'",
        "name NOT LIKE '%duck%'",       # not a standard household meat
        "name NOT LIKE '%turtle%'",     # not a standard household meat
        "name NOT LIKE '%moose%'",
        "name NOT LIKE '%elk%'",       # also catches whelk (exotic shellfish — intended)
        "name NOT LIKE '%wild boar%'",
        "name NOT LIKE '%bear%'",
        "name NOT LIKE '%frog%'",
        "name NOT LIKE '%alligator%'",

        # Exotic / uncommon seafood — restrict to common supermarket fish & shellfish
        # (salmon, tuna, cod, tilapia, shrimp, crab, lobster, clams, etc. remain)
        "name NOT LIKE '%octopus%'",
        "name NOT LIKE '%squid%'",
        "name NOT LIKE '%cuttlefish%'",
        "name NOT LIKE 'Fish, eel%'",  # avoids 'peel/heel/steel' false positives
        "name NOT LIKE '%with eel%'",
        "name NOT LIKE '%, eel'",
        "name NOT LIKE '%abalone%'",
        "name NOT LIKE '%conch%'",
        "name NOT LIKE '%crayfish%'",
        "name NOT LIKE '%crawfish%'",
        "name NOT LIKE '%escargot%'",

        # Branded restaurant-chain and packaged-food items
        # Pattern 1: ALL-CAPS brand prefix (APPLEBEE'S, BURGER KING, DENNY'S, …)
        "name NOT GLOB '[A-Z][A-Z][A-Z]*'",
        # Pattern 2: Brand in parentheses suffix ("Cheeseburger (Burger King)")
        "name NOT LIKE '%(Burger King)%'",
        "name NOT LIKE '%(McDonalds)%'",
        "name NOT LIKE '%(Wendy%'",
        "name NOT LIKE '%(Ritz)%'",
        "name NOT LIKE '%(Cheez-It)%'",
        "name NOT LIKE '%(Goldfish)%'",
        "name NOT LIKE '%(Wheat Thins)%'",
        "name NOT LIKE '%(Triscuit)%'",
        # Regional indigenous foods not available in standard household kitchens
        "name NOT LIKE '%(Apache)%'",
        "name NOT LIKE '%(Navajo)%'",
        "name NOT LIKE '%(Hopi)%'",
        "name NOT LIKE '%(Southwest)%'",
        "name NOT LIKE '%(Northern Plains%'",
        # Crackers are not standalone meals
        "name NOT LIKE 'Crackers,%'",

        "name NOT LIKE 'Pork rind%'",  # snack rinds
        "name NOT LIKE 'Beef jerky%'",
        "name NOT LIKE '%jerky%'",

        "name NOT LIKE '%sausage%'",   # belt-and-suspenders meat safety
        "name NOT LIKE '%kielbasa%'",
        "name NOT LIKE '%bratwurst%'",
        "name NOT LIKE '%hot dog%'",
        "name NOT LIKE '%frankfurter%'",
        "name NOT LIKE 'Egg, yolk%'",  # ingredient-level, not a meal
        "name NOT LIKE 'Egg, white%'",
        "name NOT LIKE 'Oil,%'",        # pure oils are not meals
        "name NOT LIKE 'Fat,%'",
        "enriched = 1",
    ]

    # Diet type — use exact match after normalising to hyphen form to avoid
    # substring collision ('vegetarian' is a substring of 'non_vegetarian').
    diet = profile.get('diet_mode', 'non-vegetarian').lower().replace('_', '-')
    if diet == 'vegan':
        clauses.append('is_vegan=1')
        clauses.append('is_vegetarian=1')  # vegan is a strict subset of vegetarian — belt-and-suspenders
        clauses.append("food_group_tag NOT IN ('fish_seafood','meat','egg')")
    elif diet == 'vegetarian':
        clauses.append('is_vegetarian=1')
        # Vegetarian: no meat, no fish/seafood. Eggs and dairy are permitted.
        clauses.append("food_group_tag NOT IN ('fish_seafood','meat')")
    elif diet == 'pescatarian':
        clauses.append('is_pescatarian=1')
        clauses.append("food_group_tag != 'meat'")
    # 'non-vegetarian': no diet tag filter — all foods are candidates

    # Belt-and-suspenders name guards for non-meat diets
    if diet in ('vegan', 'vegetarian', 'pescatarian'):
        clauses.extend([
            "name NOT LIKE 'Meat %'",        # "Meat loaf", "Meat sauce"
            "name NOT LIKE '%, meat'",        # "Pupusa, meat"
            "name NOT LIKE '%, meat,%'",      # "Stew, meat, ..."
            "name NOT LIKE '%meat loaf%'",
            "name NOT LIKE '%meat ball%'",
            "name NOT LIKE '% beef %'",
            "name NOT LIKE '% lamb %'",
            "name NOT LIKE '% pork %'",
            "name NOT LIKE '% veal %'",
        ])

    # Additional fish name guards for pure vegetarian/vegan (fish-tagged 'other' foods)
    if diet in ('vegan', 'vegetarian'):
        clauses.extend([
            "name NOT LIKE '%codfish%'",
            "name NOT LIKE '%catfish%'",
            "name NOT LIKE '% fish%'",   # "fried fish", "biscayne fish"
            "name NOT LIKE '%shrimp%'",
            "name NOT LIKE '%lobster%'",
            "name NOT LIKE '%crab%'",
            "name NOT LIKE '%tuna%'",
            "name NOT LIKE '%salmon%'",
            "name NOT LIKE '%anchov%'",
            "name NOT LIKE '%mussel%'",
            "name NOT LIKE '%scallop%'",
            "name NOT LIKE '%clam%'",
            # Survey FNDDS oyster entries are tagged is_vegetarian=1 incorrectly;
            # exclude by name. 'Mushrooms, oyster' starts with 'Mushrooms' so it's safe.
            "name NOT LIKE 'Oysters%'",
            "name NOT LIKE 'Oyster sauce%'",
            "name NOT LIKE 'Dressing with oysters%'",
            # Meat products mis-tagged as vegetarian/vegan in FNDDS
            "name NOT LIKE '%pastrami%'",
            "name NOT LIKE '%prosciutto%'",
            "name NOT LIKE '%chorizo%'",
            "name NOT LIKE 'Breakfast meat%'",
            "name NOT LIKE '%breakfast meat%'",
        ])

    # Additional egg and animal-product guards for strict vegan
    if diet == 'vegan':
        clauses.extend([
            # Egg-containing FNDDS composite dishes mis-tagged is_vegan=1
            "name NOT LIKE 'Egg,%'",           # "Egg, scrambled" etc.
            "name NOT LIKE 'Eggs%'",           # "Eggs Benedict" etc.
            "name NOT LIKE 'Egg on%'",         # "Egg on a bagel"
            "name NOT LIKE '% with egg%'",     # "Pizza with egg", "Noodles with egg"
            "name NOT LIKE '% egg,%'",         # "Burrito, egg, and cheese"
            "name NOT LIKE '%, egg %'",        # "Sandwich, egg salad"
            # Steak / meat-cut words mis-tagged in FNDDS composite foods
            "name NOT LIKE '% steak%'",        # "Beef steak" with wrong food_group
        ])

    # Pork restriction
    if profile.get('no_pork', False):
        clauses.append('has_pork=0')

    # Clinical conditions
    conds = ' '.join(profile.get('conditions', [])).lower()
    if 'ibs' in conds:
        clauses.append('is_high_fodmap=0')
    if any(k in conds for k in ('gerd','acid reflux','acidity')):
        clauses.append('is_gerd_trigger=0')
    if 'diabet' in conds:
        clauses.append('is_low_gi=1')
    if any(k in conds for k in ('hypertension','blood pressure')):
        clauses.append('is_high_sodium=0')
    # Celiac Disease requires strict gluten-free — add SQL guard even if the user
    # did not separately select 'gluten' in the allergen list.
    allergens_norm = [a.lower().replace(' ','_').replace('-','_')
                      for a in profile.get('allergens', [])]
    if 'celiac' in conds and 'gluten' not in allergens_norm:
        clauses.append('has_gluten=0')

    # Allergens (SQL belt)
    for allergen in profile.get('allergens', []):
        norm = allergen.lower().replace(' ','_').replace('-','_')
        col  = ALLERGEN_COL_MAP.get(norm)
        if col:
            clauses.append(f'{col}=0')

    return ' AND '.join(clauses)


def compute_meal_nutrients(food: dict, slot: str,
                           calorie_target: int = 2000) -> tuple:
    """
    Scale USDA per-100 g nutrients to a calorie-proportional serving size.
    Returns (nutrients_dict, serving_g_used).
    Higher calorie targets get proportionally larger portions.
    """
    base_g    = SERVING_G.get(slot, 150)
    # Only scale UP for high-calorie targets; keep base size for targets ≤ 2000 kcal
    # This prevents under-serving nutrient-dense foods for lower-calorie profiles
    scale     = max(1.0, calorie_target / 2000.0)
    serving_g = max(50, round(base_g * scale))
    mult      = serving_g / 100.0
    nutrients = {col: round(float(food.get(col) or 0) * mult, 3)
                 for col in NUTRIENT_COLS}
    return nutrients, serving_g


def _select_meal(
    slot:              str,
    embedder:          NutrientEmbedder,
    row_by_id:         dict,
    bloom:             AllergenFilterBank,
    ranker:            MealRanker,
    day_gap:           dict,
    allergens:         list,
    all_candidates:    list,
    cal_target:        int   = 2000,
    day_cal_running:   float = 0.0,
    exclude_cereal:    bool  = False,
    prefer_legume:     bool  = False,
) -> dict:
    """Full ML pipeline for one meal slot. Returns selected food dict or None."""

    # Step 1: Embedding ANN — retrieve top-120 by cosine similarity to gap
    candidate_ids = embedder.query(day_gap, k=120)
    candidates    = [row_by_id[fid] for fid in candidate_ids if fid in row_by_id]

    # Step 2: Bloom filter double-check (zero false negatives on allergens)
    if allergens:
        candidates = [f for f in candidates
                      if bloom.is_safe(int(f['fdc_id']), allergens)]

    # Step 3: Slot suitability (soft constraint — relax if pool thin)
    BREAKFAST_ONLY = ['cereal', 'ready-to-eat', 'oatmeal',
                      'porridge', 'granola', 'grits', 'muesli', 'waffle',
                      'pancake', 'muffin', 'french toast', 'breakfast']

    def _not_breakfast_only(f: dict) -> bool:
        return not any(kw in f.get('name', '').lower() for kw in BREAKFAST_ONLY)

    slot_key   = f'suitable_{slot}'
    slot_cands = [f for f in candidates if f.get(slot_key, 1)]
    # For lunch/dinner, remove breakfast-only foods regardless of suitability flag
    if slot in ('lunch', 'dinner'):
        slot_cands = [f for f in slot_cands if _not_breakfast_only(f)]

    # Per-slot calorie density cap — prevents concentrated/raw ingredients from
    # dominating a slot if they slip through SQL guards. Scale with user's calorie
    # target so lower-calorie users get proportionally appropriate foods.
    # Thresholds (at 2000 kcal baseline): breakfast 420, lunch 350, dinner 270 kcal/100g.
    _SLOT_DENSE_CAP = {'breakfast': 420, 'lunch': 350, 'dinner': 270, 'snack': 350}
    _density_limit = _SLOT_DENSE_CAP.get(slot, 350) * (cal_target / 2000.0)
    _dense_capped = [f for f in slot_cands
                     if float(f.get('calories', 0) or 0) <= _density_limit]
    if len(_dense_capped) >= 3:
        slot_cands = _dense_capped

    if len(slot_cands) < 3:
        # Thin pool fallback: relax suitability but keep breakfast-only guard for
        # lunch/dinner so cereals never appear there even on sparse profiles.
        slot_cands = [
            f for f in candidates
            if int(f.get('fdc_id', 0)) not in ranker.used_ids
            and (slot not in ('lunch', 'dinner') or _not_breakfast_only(f))
        ]

    # Step 3b: Cereal/waffle cap — after 2 per week, search ALL candidates for variety.
    # ANN top-120 can be dominated by cereal for restricted profiles, so we look wider.
    if exclude_cereal and slot == 'breakfast':
        no_cereal_all = [
            f for f in all_candidates
            if not _is_boxed_cereal(f)
            and int(f.get('fdc_id', 0)) not in ranker.used_ids
            and f.get('suitable_breakfast', 1)
        ]
        if bloom and allergens:
            no_cereal_all = [f for f in no_cereal_all
                             if bloom.is_safe(int(f['fdc_id']), allergens)]
        if len(no_cereal_all) >= 1:
            slot_cands = no_cereal_all

    # Step 3c: Legume minimum — if behind weekly pace, force a legume selection.
    # ANN top-120 may not include legumes (they fill fiber/iron but may not top the
    # cosine similarity for a given gap), so we look at the entire candidate pool.
    if prefer_legume and slot in ('lunch', 'dinner'):
        legume_cands = [
            f for f in all_candidates
            if f.get('food_group_tag') == 'legume'
            and int(f.get('fdc_id', 0)) not in ranker.used_ids
            and f.get(f'suitable_{slot}', 1)
            and _not_breakfast_only(f)
        ]
        if bloom and allergens:
            legume_cands = [f for f in legume_cands
                            if bloom.is_safe(int(f['fdc_id']), allergens)]
        if len(legume_cands) >= 2:
            slot_cands = legume_cands

    # Step 4: Multi-objective ranking — get top 50 to give the budget check a wide window.
    # rank() scores all candidates anyway; requesting 50 adds no extra computation.
    ranked = ranker.rank(slot_cands, slot, day_gap, top_n=50)

    if ranked:
        # Prefer a food that won't push the day over 110% of calorie target.
        budget_left = cal_target * 1.10 - day_cal_running
        cal_scale   = max(1.0, cal_target / 2000.0)
        for food in ranked:
            base_g    = SERVING_G.get(slot, 150)
            serving_g = base_g * cal_scale
            delivered = float(food.get('calories', 0) or 0) * serving_g / 100.0
            if delivered <= budget_left:
                return food
        return ranked[0]  # all top-50 over budget — best-ranked regardless of budget

    # Step 5: Emergency fallback — any unused safe candidate in full pool
    log.warning(f"  Ranking empty for {slot}; using fallback")
    unused = [
        f for f in all_candidates
        if int(f['fdc_id']) not in ranker.used_ids
        and (slot not in ('lunch', 'dinner') or _not_breakfast_only(f))
    ]
    return unused[0] if unused else None


def generate_plan_stream(
    profile: dict,
    db_path: str = DB_PATH,
) -> Generator:
    """
    Streaming generator — yields one dict per meal, then a final 'complete' dict.

    Yield shapes:
      {'type':'meal', 'day':int, 'slot':str, 'meal':dict, 'elapsed_s':float}
      {'type':'complete', 'plan':dict, 'elapsed_s':float}

    BAX-423 Streaming: each meal is yielded to the UI as soon as it is selected,
    before the next slot is computed — user sees the plan being built in real time.
    """
    t0 = time.perf_counter()

    # ── Profile setup ─────────────────────────────────────────────────────────
    where     = build_where_clause(profile)
    age       = int(profile.get('age', 30))
    sex       = profile.get('sex', 'female')
    rda       = get_rda(age, sex)
    allergens = [a.lower().replace(' ','_').replace('-','_')
                 for a in profile.get('allergens', [])]

    # Protein floor: 0.7 g/kg body weight, overrides RDA when higher
    weight_kg = float(profile.get('weight_kg', 0) or 0)
    if weight_kg > 0:
        protein_floor = round(0.7 * weight_kg, 1)
        if protein_floor > rda.get('protein_g', 0):
            rda = dict(rda)
            rda['protein_g'] = protein_floor
            log.info(f"Protein floor applied: {protein_floor}g (0.7 × {weight_kg} kg)")

    log.info(f"Generator start | diet={profile.get('diet_mode')} | "
             f"conditions={profile.get('conditions')} | allergens={allergens}")
    log.info(f"WHERE: {where}")

    # ── Candidate pool + embedding index ────────────────────────────────────
    embedder       = NutrientEmbedder()
    all_candidates = embedder.load_candidates(db_path, where)
    row_by_id      = {r['fdc_id']: r for r in all_candidates}

    if len(all_candidates) < 21:
        raise ValueError(
            f"Only {len(all_candidates)} safe foods found — profile may be over-constrained. "
            f"WHERE: {where}"
        )

    # ── Bloom filter bank ────────────────────────────────────────────────────
    bloom = AllergenFilterBank.build(db_path)

    # ── Multi-objective ranker ───────────────────────────────────────────────
    ranker = MealRanker(profile, rda=rda)

    # ── Exclusion examples (for 'Why excluded' feature) ──────────────────────
    conn_excl  = sqlite3.connect(db_path, check_same_thread=False)
    exclusions = get_exclusion_sample(conn_excl, where, profile, n=30)
    conn_excl.close()

    # ── Plan skeleton ─────────────────────────────────────────────────────────
    plan = {
        'profile':           profile,
        'days':              [],
        'exclusions':        exclusions,
        'diversity_score':   0.0,
        'generation_time_s': 0.0,
        'candidate_pool':    len(all_candidates),
        'where_clause':      where,
        'rda':               rda,
    }

    # ── 7-day × 3-slot generation loop ───────────────────────────────────────
    cal_target         = int(profile.get('calorie_target', 2000))
    boxed_cereal_count = 0   # cap ready-to-eat cereals at 2 breakfasts per week
    week_legume_count  = 0   # target >=3 legume meals per week for nutritional balance

    for day_num in range(1, 8):
        day_totals:       dict  = {}
        day_meals:        dict  = {}
        day_cal_running:  float = 0.0

        for slot in MEAL_SLOTS:
            # Nutrient gap from meals already assigned today
            day_gap = compute_gap_vector(day_totals, rda)

            # Prefer a legume meal if we're behind the weekly target.
            # Trigger: behind pace (e.g. day 4 with only 0 legumes), or final stretch.
            legume_pace_target = max(1, day_num // 2)   # want ~1 legume per 2 days
            need_legume = (week_legume_count < legume_pace_target
                           and slot in ('lunch', 'dinner'))

            # Full ML pipeline selection
            food = _select_meal(
                slot, embedder, row_by_id, bloom,
                ranker, day_gap, allergens, all_candidates,
                cal_target=cal_target,
                day_cal_running=day_cal_running,
                exclude_cereal=(boxed_cereal_count >= 2),
                prefer_legume=need_legume,
            )

            if food is None:
                log.error(f"  Day {day_num} {slot}: no food found — skipping")
                continue

            # Score breakdown BEFORE marking selected
            score_bd = ranker.get_score_breakdown(food, slot, day_gap)
            ranker.mark_selected(food)

            # Scale nutrients to calorie-proportional serving size and accumulate
            meal_nutrients, serving_g = compute_meal_nutrients(food, slot, cal_target)
            for k, v in meal_nutrients.items():
                day_totals[k] = round(day_totals.get(k, 0.0) + v, 3)
            day_cal_running += meal_nutrients.get('calories', 0.0)

            # Weekly diversity tracking
            if slot == 'breakfast' and _is_boxed_cereal(food):
                boxed_cereal_count += 1
            if food.get('food_group_tag') == 'legume':
                week_legume_count += 1

            description = generate_description(food, slot, profile)

            meal = {
                'fdc_id':          food['fdc_id'],
                'name':            food.get('name', ''),
                'description':     description,
                'food_group':      food.get('food_group_tag', 'other'),
                'nutrients':       meal_nutrients,
                'gi_estimate':     food.get('gi_estimate', 50),
                'is_low_gi':       food.get('is_low_gi', 1),
                'sodium_100g':     round(float(food.get('sodium') or 0), 1),
                'serving_g':       serving_g,
                'slot':            slot,
                'day':             day_num,
                'score_breakdown': score_bd,
            }

            day_meals[slot] = meal

            # ── STREAM: yield this meal immediately ──────────────────────────
            yield {
                'type':      'meal',
                'day':       day_num,
                'slot':      slot,
                'meal':      meal,
                'elapsed_s': round(time.perf_counter() - t0, 2),
            }

        # Day complete: compute RDA gaps
        rda_gaps = flag_rda_gaps(day_totals, rda)
        plan['days'].append({
            'day':        day_num,
            'meals':      day_meals,
            'day_totals': day_totals,
            'rda_gaps':   rda_gaps,
        })

    # ── Final plan stats ──────────────────────────────────────────────────────
    elapsed = time.perf_counter() - t0
    plan['generation_time_s'] = round(elapsed, 2)
    plan['diversity_score']   = ranker.compute_diversity_score()
    plan['all_meal_ids']      = list(ranker.used_ids)
    plan['group_distribution']= dict(ranker.group_counts)

    log.info(f"Plan complete in {elapsed:.2f}s | "
             f"diversity={plan['diversity_score']} | "
             f"meals={len(ranker.selected_meals)}")

    yield {'type': 'complete', 'plan': plan, 'elapsed_s': round(elapsed, 2)}


def generate_plan(profile: dict, db_path: str = DB_PATH) -> dict:
    """Blocking wrapper — collects the stream and returns the final plan dict."""
    plan = None
    for item in generate_plan_stream(profile, db_path):
        if item['type'] == 'complete':
            plan = item['plan']
    return plan
