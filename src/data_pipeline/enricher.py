"""
src/data_pipeline/enricher.py

Clinical & dietary enrichment layer.
Applies keyword + nutrition-based tagging to every food in the SQLite DB.
No API calls — runs entirely locally on the 13,620-record database.

Tags applied:
  Allergens       : gluten, dairy, tree nuts, shellfish, soy, eggs, peanuts, fish, pork
  Diet type       : vegan, vegetarian, pescatarian
  Clinical        : FODMAP (IBS), GERD, glycaemic index (T2DM), sodium (hypertension)
  Meal suitability: breakfast, lunch, dinner, snack
  Diversity       : food_group_tag (13 categories)
"""
import re
import sqlite3
import logging
from tqdm import tqdm
from src.config import DB_PATH

log = logging.getLogger(__name__)

# ── Keyword match helpers ─────────────────────────────────────────────────────

_RE_CACHE: dict = {}

def _word_in(text: str, kw: str) -> bool:
    """Whole-word match for single words; substring match for multi-word phrases."""
    if ' ' in kw:
        return kw in text
    if kw not in _RE_CACHE:
        _RE_CACHE[kw] = re.compile(r'\b' + re.escape(kw) + r'\b')
    return bool(_RE_CACHE[kw].search(text))

def _any_kw(text: str, keywords) -> bool:
    return any(_word_in(text, kw) for kw in keywords)

def _match_dict(text: str, kw_dict: dict) -> list:
    """Return list of reason strings from dict whose keys match in text."""
    return [v for k, v in kw_dict.items() if _word_in(text, k)]


# ── Allergen keyword lists ────────────────────────────────────────────────────

GLUTEN_KW = [
    'wheat', 'barley', 'rye', 'spelt', 'kamut', 'triticale', 'bulgur',
    'semolina', 'farro', 'durum', 'farina', 'einkorn', 'emmer',
    'bread', 'pasta', 'noodle', 'flour tortilla', 'cracker', 'pretzel',
    'couscous', 'seitan', 'malt', 'breaded', 'stuffing', 'crouton',
    'bagel', 'muffin', 'pancake', 'waffle', 'macaroni', 'spaghetti',
    'lasagna', 'linguine', 'fettuccine', 'tortellini', 'ravioli',
    'gnocchi', 'croissant', 'bun', 'roll', 'doughnut', 'donut',
    'churro', 'crepe', 'scone', 'brioche', 'ciabatta', 'focaccia', 'pita',
]

DAIRY_KW = [
    'milk', 'cheese', 'butter', 'cream', 'yogurt', 'yoghurt', 'whey',
    'casein', 'lactose', 'ghee', 'kefir', 'buttermilk', 'custard',
    'gelato', 'sherbet', 'sour cream', 'creme', 'fromage',
    'ricotta', 'mozzarella', 'parmesan', 'cheddar', 'gouda',
    'brie', 'camembert', 'gruyere', 'feta', 'provolone', 'colby',
    'cottage cheese', 'cream cheese', 'half-and-half',
    'evaporated milk', 'condensed milk', 'ice cream', 'milkshake',
    'latte', 'cappuccino',
]

TREE_NUT_KW = [
    'almond', 'cashew', 'walnut', 'pecan', 'pistachio', 'brazil nut',
    'hazelnut', 'macadamia', 'pine nut', 'chestnut', 'praline',
    'marzipan', 'nougat', 'nut butter', 'mixed nut', 'filbert',
]

SHELLFISH_KW = [
    'shrimp', 'crab', 'lobster', 'clam', 'oyster', 'mussel',
    'scallop', 'crayfish', 'crawfish', 'prawn', 'krill',
    'abalone', 'cockle', 'whelk', 'langoustine',
]

SOY_KW = [
    'soy', 'soymilk', 'soy milk', 'tofu', 'tempeh', 'edamame',
    'miso', 'tamari', 'textured vegetable protein', 'tvp', 'natto',
    'soy sauce', 'soybean', 'soya',
]

PEANUT_KW = ['peanut', 'groundnut', 'arachis']

FISH_KW = [
    'salmon', 'tuna', 'cod', 'tilapia', 'halibut', 'trout', 'catfish',
    'flounder', 'herring', 'mackerel', 'snapper', 'anchovy', 'sardine',
    'pollock', 'mahi', 'pike', 'carp', 'perch', 'haddock', 'sole',
    'roughy', 'swai', 'pangasius', 'sablefish', 'rockfish', 'grouper',
    'striped bass', 'sea bass', 'lingcod', 'walleye', 'bluegill',
    'whitefish', 'basa', 'cusk', 'cuttlefish', 'squid', 'octopus',
]

MEAT_KW = [
    'beef', 'pork', 'lamb', 'chicken', 'turkey', 'duck', 'goose',
    'veal', 'venison', 'bison', 'rabbit', 'mutton', 'pheasant',
    'quail', 'ostrich', 'buffalo', 'wild boar', 'game meat',
]

PORK_KW = [
    'pork', 'ham', 'bacon', 'pepperoni', 'salami', 'prosciutto',
    'chorizo', 'liverwurst', 'bologna', 'lard', 'suet', 'tallow',
    'pancetta', 'mortadella', 'coppa',
]

HONEY_KW = ['honey', 'honeycomb', 'bee pollen']


# ── Clinical condition dictionaries ──────────────────────────────────────────

# High-FODMAP: foods to avoid for IBS — keyword → human-readable reason
FODMAP_HIGH: dict = {
    'garlic':          'Garlic: high fructan content (high-FODMAP trigger for IBS)',
    'garlic powder':   'Garlic powder: concentrated fructans (high-FODMAP)',
    'onion':           'Onion: high fructan content (high-FODMAP trigger for IBS)',
    'onion powder':    'Onion powder: concentrated fructans (high-FODMAP)',
    'leek':            'Leek: fructans in white part (high-FODMAP)',
    'shallot':         'Shallot: fructans (high-FODMAP)',
    'chive':           'Chive: fructans (high-FODMAP)',
    'scallion':        'Scallion: white part is high in fructans (high-FODMAP)',
    'wheat':           'Wheat: fructan-rich grain (high-FODMAP for IBS)',
    'rye':             'Rye: fructan-rich grain (high-FODMAP for IBS)',
    'barley':          'Barley: fructans (high-FODMAP for IBS)',
    'honey':           'Honey: excess fructose (high-FODMAP)',
    'high fructose':   'High-fructose corn syrup: excess fructose (high-FODMAP)',
    'watermelon':      'Watermelon: excess fructose (high-FODMAP)',
    'nectarine':       'Nectarine: sorbitol content (high-FODMAP)',
    'mushroom':        'Mushrooms: polyol content (high-FODMAP in large servings)',
    'cauliflower':     'Cauliflower: polyols (high-FODMAP in large amounts)',
    'kidney bean':     'Kidney beans: GOS oligosaccharides (high-FODMAP)',
    'navy bean':       'Navy beans: GOS oligosaccharides (high-FODMAP)',
    'baked bean':      'Baked beans: GOS + fructans (high-FODMAP)',
    'apple juice':     'Apple juice: excess fructose (high-FODMAP)',
    'pear juice':      'Pear juice: sorbitol + fructose (high-FODMAP)',
}

# GERD / acid reflux triggers — keyword → reason
GERD_TRIGGERS: dict = {
    'orange juice':  'Orange juice: citric acid increases acid reflux',
    'grapefruit':    'Grapefruit: citrus acid (GERD trigger)',
    'lemonade':      'Lemonade: citric acid (GERD trigger)',
    'tomato sauce':  'Tomato sauce: acidic (GERD trigger)',
    'marinara':      'Marinara: tomato-based acidity (GERD trigger)',
    'ketchup':       'Ketchup: acidic tomato (GERD trigger)',
    'salsa':         'Salsa: tomato + citrus acid (GERD trigger)',
    'coffee':        'Coffee: caffeine relaxes esophageal sphincter (GERD trigger)',
    'espresso':      'Espresso: high caffeine (GERD trigger)',
    'cappuccino':    'Cappuccino: caffeine (GERD trigger)',
    'chocolate':     'Chocolate: methylxanthines relax esophageal sphincter (GERD trigger)',
    'cocoa':         'Cocoa: methylxanthines (GERD trigger)',
    'cacao':         'Cacao: methylxanthines (GERD trigger)',
    'hot sauce':     'Hot sauce: capsaicin irritates esophageal lining (GERD trigger)',
    'sriracha':      'Sriracha: capsaicin (GERD trigger)',
    'cayenne':       'Cayenne pepper: capsaicin (GERD trigger)',
    'peppermint':    'Peppermint: relaxes lower esophageal sphincter (GERD trigger)',
    'spearmint':     'Spearmint: relaxes esophageal sphincter (GERD trigger)',
    'wine':          'Wine: alcohol + acidity (GERD trigger)',
    'beer':          'Beer: carbonated alcohol (GERD trigger)',
    'alcohol':       'Alcohol: GERD trigger',
    'tequila':       'Tequila: alcohol (GERD trigger)',
    'vodka':         'Vodka: alcohol (GERD trigger)',
    'french fries':  'French fries: high fat + fried (delays stomach emptying, GERD trigger)',
    'fried chicken': 'Fried chicken: high fat + fried (GERD trigger)',
    'doughnut':      'Doughnut: high-fat fried food (GERD trigger)',
    'donut':         'Donut: high-fat fried food (GERD trigger)',
}

# GI values by keyword — source: Atkinson et al. (2008) Int'l GI Tables
GI_HIGH: dict = {
    'white rice': 72, 'jasmine rice': 68, 'short grain rice': 72,
    'rice cake':  82, 'corn flake': 81, 'rice crisp': 82,
    'glucose': 100, 'dextrose': 100, 'maltodextrin': 95,
    'white bread': 75, 'white bagel': 72, 'baguette': 95,
    'instant oat': 79, 'instant noodle': 67,
    'baked potato': 85, 'mashed potato': 87, 'french fries': 75,
    'waffle': 76, 'donut': 76, 'doughnut': 76, 'pretzel': 83,
    'watermelon': 76, 'sports drink': 78,
}
GI_MED: dict = {
    'brown rice': 55, 'wild rice': 57, 'basmati rice': 57,
    'whole wheat bread': 69, 'whole grain bread': 65,
    'pita': 57, 'tortilla': 52, 'corn tortilla': 52,
    'couscous': 65, 'millet': 71, 'polenta': 68,
    'oatmeal': 55, 'rolled oat': 55, 'porridge': 55,
    'banana': 51, 'mango': 51, 'pineapple': 59,
    'raisin': 64, 'papaya': 59, 'beetroot': 64, 'beet': 64,
    'corn': 52, 'pancake': 67, 'muffin': 62, 'scone': 92,
}
GI_LOW: dict = {
    'lentil': 32, 'chickpea': 28, 'kidney bean': 34,
    'black bean': 30, 'navy bean': 38, 'pinto bean': 39,
    'soybean': 15, 'edamame': 18, 'tofu': 15,
    'apple': 36, 'pear': 38, 'orange': 43,
    'grape': 46, 'strawberry': 40, 'peach': 42,
    'cherry': 22, 'kiwi': 53, 'grapefruit': 25,
    'plum': 39, 'apricot': 34, 'yogurt': 36, 'milk': 31,
    'quinoa': 53, 'bulgur': 47, 'sweet potato': 44, 'yam': 54,
    'carrot': 35, 'broccoli': 10, 'spinach': 15,
    'kale': 10, 'lettuce': 10, 'cucumber': 15, 'celery': 10,
    'zucchini': 15, 'cauliflower': 15, 'asparagus': 15,
    'pasta': 45, 'spaghetti': 45, 'macaroni': 47,
    'fettuccine': 40, 'linguine': 46, 'tortellini': 50,
    'almond': 0, 'walnut': 15, 'peanut': 14,
    'cashew': 27, 'pistachio': 15,
    'egg': 0, 'beef': 0, 'chicken': 0,
    'fish': 0, 'salmon': 0, 'tuna': 0,
    'shrimp': 0, 'pork': 0, 'lamb': 0,
}


# ── Food group tags — ordered list, first match wins ─────────────────────────

FOOD_GROUPS: list = [
    ('meat',         ['beef', 'pork', 'lamb', 'veal', 'venison', 'bison', 'rabbit',
                      'chicken', 'turkey', 'duck', 'goose', 'ham', 'bacon',
                      'sausage', 'pepperoni', 'salami', 'chorizo', 'mutton']),
    ('fish_seafood', ['salmon', 'tuna', 'cod', 'tilapia', 'halibut', 'trout',
                      'catfish', 'flounder', 'herring', 'mackerel', 'snapper',
                      'sardine', 'pollock', 'mahi', 'shrimp', 'crab', 'lobster',
                      'clam', 'oyster', 'mussel', 'scallop', 'squid', 'fish', 'seafood']),
    ('legume',       ['lentil', 'chickpea', 'kidney bean', 'black bean',
                      'navy bean', 'pinto bean', 'garbanzo', 'split pea',
                      'black-eyed pea', 'fava bean', 'lima bean', 'cannellini',
                      'adzuki', 'mung bean', 'dal']),
    ('dairy',        ['milk', 'cheese', 'yogurt', 'yoghurt', 'butter',
                      'cream', 'kefir', 'ghee', 'ice cream', 'custard',
                      'ricotta', 'mozzarella', 'parmesan', 'cheddar',
                      'cottage cheese', 'sour cream']),
    ('grain',        ['rice', 'bread', 'pasta', 'oat', 'cereal', 'quinoa',
                      'noodle', 'couscous', 'tortilla', 'pita', 'bagel',
                      'granola', 'millet', 'amaranth', 'bulgur', 'cracker',
                      'pancake', 'waffle', 'grits', 'polenta']),
    ('vegetable',    ['broccoli', 'spinach', 'kale', 'lettuce', 'carrot',
                      'pepper', 'zucchini', 'squash', 'eggplant', 'cabbage',
                      'celery', 'cucumber', 'asparagus', 'artichoke',
                      'bok choy', 'arugula', 'radish', 'turnip', 'parsnip',
                      'beetroot', 'beet', 'sweet potato', 'yam', 'pumpkin',
                      'green bean', 'collard', 'swiss chard', 'watercress',
                      'mushroom', 'potato', 'okra', 'fennel']),
    ('fruit',        ['apple', 'banana', 'orange', 'berry', 'blueberry',
                      'strawberry', 'raspberry', 'mango', 'pineapple',
                      'watermelon', 'melon', 'grape', 'pear', 'peach',
                      'plum', 'apricot', 'cherry', 'kiwi', 'papaya',
                      'pomegranate', 'fig', 'date', 'raisin', 'cranberry',
                      'coconut']),
    ('nut_seed',     ['almond', 'cashew', 'walnut', 'pecan', 'pistachio',
                      'hazelnut', 'macadamia', 'peanut', 'sunflower seed',
                      'pumpkin seed', 'sesame', 'flaxseed', 'chia seed',
                      'hemp seed', 'pine nut', 'brazil nut', 'tahini']),
    ('mixed_dish',   ['stew', 'casserole', 'curry', 'soup', 'chili', 'chilli',
                      'stir-fry', 'stir fry', 'fried rice', 'bowl', 'burrito',
                      'taco', 'sandwich', 'pizza', 'lasagna', 'paella',
                      'risotto', 'biryani', 'pilaf', 'wrap', 'salad']),
    ('sweet',        ['cake', 'cookie', 'candy', 'chocolate', 'pie',
                      'dessert', 'pudding', 'brownie', 'pastry', 'tart',
                      'gelatin', 'sorbet', 'sherbet', 'jelly', 'jam']),
    ('beverage',     ['juice', 'drink', 'beverage', 'soda', 'cola',
                      'smoothie', 'shake', 'lemonade', 'sports drink',
                      'energy drink', 'coffee', 'tea']),
    ('fat_oil',      ['oil', 'margarine', 'shortening', 'mayonnaise']),
]

# ── Meal suitability ─────────────────────────────────────────────────────────

BREAKFAST_KW = [
    'oat', 'oatmeal', 'cereal', 'granola', 'pancake', 'waffle',
    'french toast', 'toast', 'muffin', 'bagel', 'yogurt', 'yoghurt',
    'smoothie', 'breakfast', 'porridge', 'scone', 'croissant', 'danish',
    'scrambled', 'omelet', 'omelette', 'frittata', 'quiche', 'crepe',
]

SNACK_KW = [
    'nut', 'seed', 'chip', 'snack', 'trail mix', 'popcorn',
    'protein bar', 'energy bar', 'rice cake', 'hummus', 'guacamole', 'dip',
]

BEVERAGE_KW = [
    'juice', 'soda', 'cola', 'soft drink', 'beverage', 'lemonade',
    'punch', 'energy drink', 'sports drink', 'water', 'tea',
    'coffee', 'milkshake', 'smoothie', 'kool',
]


# ── Core enrichment logic ────────────────────────────────────────────────────

def estimate_gi(text: str, carbs: float, protein: float, fiber: float) -> int:
    """
    Estimate glycaemic index from food name keywords and macro fallback.
    Returns 0-100 or 50 (default moderate) when unknown.
    Source: Atkinson et al. (2008) International GI Tables.
    """
    for kw, gi in GI_HIGH.items():
        if kw in text:
            return gi
    for kw, gi in GI_MED.items():
        if kw in text:
            return gi
    for kw, gi in GI_LOW.items():
        if _word_in(text, kw):
            return gi
    # Macro-based fallback
    if carbs < 5:
        return 5        # Negligible carbs → negligible GI impact
    if protein > 20 and carbs < 10:
        return 15       # High-protein, low-carb food
    if fiber > 8:
        return 40       # High fibre slows glucose absorption
    return 50           # Conservative default (passes ≤55 threshold safely)


def classify_food_group(text: str, has_egg: bool) -> str:
    """Return first matching food group tag; 'egg' handled separately."""
    if has_egg and 'eggplant' not in text and 'egg roll' not in text:
        return 'egg'
    for group, keywords in FOOD_GROUPS:
        for kw in keywords:
            if _word_in(text, kw):
                return group
    return 'other'


def enrich_row(row: tuple) -> tuple:
    """
    Compute all enrichment tags for one food row.
    Input: (fdc_id, name, food_category, protein, carbs, fat, fiber, sodium)
    Returns 27-element tuple matching UPDATE_SQL parameter order.
    """
    fdc_id   = row[0]
    name     = (row[1] or '').lower().strip()
    cat      = (row[2] or '').lower().strip()
    protein  = float(row[3] or 0)
    carbs    = float(row[4] or 0)
    fat      = float(row[5] or 0)
    fiber    = float(row[6] or 0)
    sodium   = float(row[7] or 0)
    text     = name + ' ' + cat      # search both name and category

    # ── Allergens ─────────────────────────────────────────────────────────────
    has_gluten    = _any_kw(text, GLUTEN_KW)
    has_dairy     = _any_kw(text, DAIRY_KW)
    has_tree_nuts = _any_kw(text, TREE_NUT_KW)
    has_shellfish = _any_kw(text, SHELLFISH_KW)
    has_soy       = _any_kw(text, SOY_KW)
    has_eggs      = bool(re.search(r'\begg\b', text)) and 'eggplant' not in text
    has_peanuts   = _any_kw(text, PEANUT_KW)
    has_fish      = _any_kw(text, FISH_KW)
    has_meat      = _any_kw(text, MEAT_KW)
    has_pork      = _any_kw(text, PORK_KW)
    has_honey     = _any_kw(text, HONEY_KW)

    # USDA category-based overrides (reliable ground truth)
    if any(k in cat for k in ('beef', 'pork', 'lamb', 'veal', 'game', 'poultry')):
        has_meat = True
    if 'finfish' in cat or 'seafood' in cat:
        has_fish = True
    if 'shellfish' in cat:
        has_shellfish = True
    if 'dairy' in cat:
        has_dairy = True
    if 'egg product' in cat:
        has_eggs = True

    # ── Diet type ─────────────────────────────────────────────────────────────
    has_seafood    = has_fish or has_shellfish
    # has_pork included explicitly: processed pork (pepperoni, salami, ham)
    # may not be caught by MEAT_KW but is always in PORK_KW
    is_vegetarian  = 0 if (has_meat or has_seafood or has_pork) else 1
    is_vegan       = 0 if (has_meat or has_seafood or has_dairy or
                           has_eggs or has_honey or has_pork) else 1
    is_pescatarian = 0 if (has_meat or has_pork) else 1

    # ── FODMAP ────────────────────────────────────────────────────────────────
    fodmap_hits    = _match_dict(text, FODMAP_HIGH)
    is_high_fodmap = 1 if fodmap_hits else 0
    fodmap_triggers = '; '.join(fodmap_hits[:4])

    # ── GERD ──────────────────────────────────────────────────────────────────
    gerd_hits = _match_dict(text, GERD_TRIGGERS)
    # Extra citrus catch (covers "orange", "lemon", "lime", "tangerine")
    for word in ('orange', 'lemon', 'lime', 'tangerine', 'clementine', 'citrus'):
        if _word_in(text, word) and 'citric acid' not in ' '.join(gerd_hits):
            gerd_hits.insert(0, f'{word.capitalize()}: citrus acid (GERD trigger)')
            break
    # Extra tomato catch
    if _word_in(text, 'tomato') and 'tomato' not in ' '.join(gerd_hits).lower():
        gerd_hits.append('Tomato: acidic food (GERD trigger)')
    # Extra spicy catch
    for word in ('spicy', 'chili', 'chilli', 'jalapeno', 'habanero'):
        if word in text and 'capsaicin' not in ' '.join(gerd_hits).lower():
            gerd_hits.append('Spicy ingredient: capsaicin irritates esophageal lining (GERD trigger)')
            break
    # Extra fried catch
    if 'fried' in text and 'stir' not in text and 'air' not in text:
        if 'fried' not in ' '.join(gerd_hits).lower():
            gerd_hits.append('Fried food: high fat delays stomach emptying (GERD trigger)')
    # Extra mint catch
    if _word_in(text, 'mint') and 'peppermint' not in text and 'spearmint' not in text:
        if 'sphincter' not in ' '.join(gerd_hits).lower():
            gerd_hits.append('Mint: relaxes lower esophageal sphincter (GERD trigger)')

    is_gerd_trigger = 1 if gerd_hits else 0
    gerd_reasons    = '; '.join(gerd_hits[:4])

    # ── Glycaemic index ───────────────────────────────────────────────────────
    gi        = estimate_gi(text, carbs, protein, fiber)
    is_low_gi = 1 if gi <= 55 else 0

    # ── Sodium ────────────────────────────────────────────────────────────────
    is_high_sodium = 1 if sodium > 400 else 0

    # ── Meal suitability ─────────────────────────────────────────────────────
    is_beverage        = _any_kw(text, BEVERAGE_KW)
    suitable_breakfast = 1 if _any_kw(text, BREAKFAST_KW) and not is_beverage else 0
    suitable_snack     = 1 if _any_kw(text, SNACK_KW) else 0
    suitable_lunch     = 0 if is_beverage else 1
    suitable_dinner    = 0 if is_beverage else 1

    # ── Food group ────────────────────────────────────────────────────────────
    food_group = classify_food_group(text, has_eggs)

    return (
        is_vegan, is_vegetarian, is_pescatarian,
        int(has_gluten), int(has_dairy), int(has_tree_nuts),
        int(has_shellfish), int(has_soy), int(has_eggs),
        int(has_peanuts), int(has_fish),
        int(has_meat), int(has_pork), int(has_honey),
        is_high_fodmap, fodmap_triggers,
        is_gerd_trigger, gerd_reasons,
        gi, is_low_gi, is_high_sodium,
        suitable_breakfast, suitable_lunch, suitable_dinner, suitable_snack,
        food_group,
        fdc_id,
    )


# ── DB schema additions & update SQL ─────────────────────────────────────────

ADD_COLUMNS = [
    ('has_meat',  'INTEGER DEFAULT 0'),
    ('has_pork',  'INTEGER DEFAULT 0'),
    ('has_honey', 'INTEGER DEFAULT 0'),
    ('is_low_gi', 'INTEGER DEFAULT 0'),
]

UPDATE_SQL = """
UPDATE foods SET
    is_vegan=?, is_vegetarian=?, is_pescatarian=?,
    has_gluten=?, has_dairy=?, has_tree_nuts=?,
    has_shellfish=?, has_soy=?, has_eggs=?,
    has_peanuts=?, has_fish=?,
    has_meat=?, has_pork=?, has_honey=?,
    is_high_fodmap=?, fodmap_triggers=?,
    is_gerd_trigger=?, gerd_reasons=?,
    gi_estimate=?, is_low_gi=?, is_high_sodium=?,
    suitable_breakfast=?, suitable_lunch=?, suitable_dinner=?, suitable_snack=?,
    food_group_tag=?,
    enriched=1
WHERE fdc_id=?
"""


def run_enrichment(db_path: str = DB_PATH, force: bool = False) -> dict:
    """
    Enrich all unenriched food records. Idempotent — safe to re-run.
    Returns summary statistics dict.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    # Add new columns (idempotent via try/except)
    for col, dtype in ADD_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE foods ADD COLUMN {col} {dtype}")
        except sqlite3.OperationalError:
            pass  # Already exists
    conn.commit()

    clause = "" if force else "WHERE enriched = 0"
    rows = conn.execute(
        f"""SELECT fdc_id, name, food_category,
                   protein, carbs, fat, fiber, sodium
            FROM foods {clause}"""
    ).fetchall()

    log.info(f"Enriching {len(rows):,} food records...")
    batch: list = []
    BATCH = 500

    for row in tqdm(rows, desc="Enriching", unit="food"):
        batch.append(enrich_row(row))
        if len(batch) >= BATCH:
            conn.executemany(UPDATE_SQL, batch)
            conn.commit()
            batch.clear()

    if batch:
        conn.executemany(UPDATE_SQL, batch)
        conn.commit()

    stats = {}
    for col in ('is_vegan', 'is_vegetarian', 'is_pescatarian',
                'has_gluten', 'has_dairy', 'is_high_fodmap',
                'is_gerd_trigger', 'is_low_gi', 'is_high_sodium',
                'has_meat', 'has_fish', 'has_pork'):
        stats[col] = conn.execute(
            f"SELECT COUNT(*) FROM foods WHERE {col}=1"
        ).fetchone()[0]

    conn.close()
    return stats
