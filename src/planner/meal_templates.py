"""
src/planner/meal_templates.py

Rule-based restaurant-style meal description generator.
Transforms USDA food names into readable, cuisine-specific meal descriptions.

Design:
  1. clean_name()        — strips USDA qualifiers, extracts core ingredient
  2. _TEMPLATES dict     — (food_group_tag, slot) → list of template strings
  3. generate_description() — picks template, fills {ingredient} placeholder
"""

import re
import random
from typing import Optional

STRIP_WORDS = [
    'nfs', 'ns as to type', 'ns as to cooking method',
    'dry heat', 'moist heat', 'pan-browned', 'pan browned',
    'fat not added in cooking', 'fat added in cooking',
    'without salt', 'with salt', 'salted', 'unsalted',
    'mature seeds', 'young seeds', 'all varieties', 'all types',
    'home recipe', 'restaurant prepared',
    'light meat', 'dark meat', 'white meat',
    'grade a', 'grade b', 'usda choice', 'usda select',
    'boneless', 'skinless', 'drained', 'dehydrated',
    'meatless', 'ns as to fat content', 'fat added',
]
PREP_WORDS = {
    'cooked','raw','fresh','frozen','dried','canned','prepared',
    'unprepared','boiled','steamed','baked','grilled','roasted',
    'braised','drained','microwaved','fried','broiled',
}

# Meaningless qualifiers that add no information to a food name.
# "Cereal, other, plain" → skip "other" → just "Cereal"
_MEANINGLESS_QUALIFIERS = {
    'other', 'plain', 'regular', 'standard', 'type', 'nfs', 'ns',
    'various', 'assorted', 'original', 'classic', 'traditional',
    'all', 'any', 'generic', 'general',
}

# USDA food category prefixes that carry no useful culinary information.
# When a name starts with one of these, the first meaningful word comes after.
_CATEGORY_HEADS = {
    'fast foods', 'restaurant', 'fish', 'mollusks', 'crustaceans',
    'game meat', 'meals', 'entrees', 'soups', 'salads',
    'seeds',   # "Seeds, pumpkin..." → skip to "pumpkin and squash seeds"
    'nuts',    # "Nuts, almonds..." → skip to "almonds"
    'legumes', # generic USDA category prefix
}
# Cuisine-type words that appear as a second part after a category head.
_CUISINE_TYPES = {
    'latino', 'chinese', 'mexican', 'italian', 'asian', 'american',
    'greek', 'indian', 'thai', 'japanese', 'korean', 'french',
    'spanish', 'mediterranean', 'caribbean', 'southern',
    'family style', 'sit-down',
}

# Single-word qualifiers placed BEFORE the main noun.
# "Salmon, Atlantic" → "Atlantic Salmon"; "Rice, Brown" → "Brown Rice"
_PREPEND_QUALIFIERS = {
    'atlantic', 'pacific', 'wild', 'farmed', 'domestic', 'jumbo', 'baby',
    'brown', 'white', 'red', 'black', 'golden', 'green', 'pink', 'blue',
    'ground', 'whole', 'lean', 'extra-lean',
    'basmati', 'jasmine', 'arborio', 'long-grain', 'short-grain',
    'sourdough', 'whole-wheat', 'multigrain',
    'low-fat', 'nonfat', 'skim', 'full-fat', 'reduced-fat',
}

# Body-part / cut words that stay AFTER the noun.
_APPEND_CUTS = {
    'breast', 'thigh', 'leg', 'wing', 'drumstick', 'tenderloin',
    'loin', 'chop', 'rib', 'flank', 'sirloin', 'chuck', 'round',
    'shoulder', 'rump', 'shank', 'belly', 'fillet', 'steak', 'cutlet',
    'roast', 'burger', 'patty',
}


def clean_name(usda_name: str) -> str:
    parts = [p.strip() for p in usda_name.split(',')]

    first_alpha = re.sub(r"[^A-Za-z]", "", parts[0])
    is_brand = len(first_alpha) >= 3 and first_alpha.isupper()
    start_idx = 0
    if parts[0].lower().strip() in _CATEGORY_HEADS or is_brand:
        for i, part in enumerate(parts[1:], 1):
            pl = part.lower().strip()
            if (pl
                    and pl not in _CATEGORY_HEADS
                    and pl not in _CUISINE_TYPES
                    and pl not in ('nfs', 'ns')
                    and not any(q == pl for q in PREP_WORDS)
                    and not pl.startswith('with ')):
                start_idx = i
                break

    core = parts[start_idx]

    if start_idx + 1 < len(parts):
        second = parts[start_idx + 1].lower().strip()
        if (len(second) < 28
                and second not in _MEANINGLESS_QUALIFIERS
                and not any(q in second for q in PREP_WORDS)
                and not any(c.isdigit() for c in second)
                and second not in ('nfs', 'ns')):
            second_words = second.split()
            if len(second_words) == 1:
                qualifier = parts[start_idx + 1].strip()
                ql = qualifier.lower()
                if ql in _APPEND_CUTS:
                    core = core + ' ' + qualifier
                elif ql in _PREPEND_QUALIFIERS or (ql.isalpha() and ql not in _MEANINGLESS_QUALIFIERS):
                    core = qualifier + ' ' + core

    for word in STRIP_WORDS:
        core = re.sub(r'\b' + re.escape(word) + r'\b', '', core, flags=re.IGNORECASE)
    core = re.sub(r'\b\d+\s*%\b', '', core)
    core = re.sub(r',\s*,', ',', core)
    core = re.sub(r'\s+', ' ', core).strip(' ,')

    # Strip trailing parentheticals (brand names, alternate names like "(besan)", regional tags)
    core = re.sub(r'\s*\([^)]*\)\s*$', '', core).strip()

    if ',' in core:
        core = core.split(',')[0].strip()

    return core.title() if core else parts[0].strip().title()


_TEMPLATES = {
    # ── BREAKFAST ────────────────────────────────────────────────────────────
    ('grain', 'breakfast'): [
        "Warm {ingredient} porridge with cinnamon, sliced banana, and toasted pumpkin seeds",
        "Creamy {ingredient} bowl topped with fresh mixed berries, chia seeds, and a drizzle of maple syrup",
        "Golden {ingredient} with almond butter, sliced strawberries, and crushed flaxseeds",
        "Spiced {ingredient} upma with mustard seeds, curry leaves, and diced tomatoes",
        "Toasted {ingredient} with tahini, sliced avocado, and a pinch of za'atar",
    ],
    ('fruit', 'breakfast'): [
        "Fresh {ingredient} bowl with coconut yogurt, rolled oats, and toasted sunflower seeds",
        "Chilled {ingredient} smoothie bowl with baby spinach, frozen banana, and chia seeds",
        "Sliced {ingredient} with warm cinnamon oats, crushed walnuts, and a drizzle of honey",
        "Mixed {ingredient} fruit salad with lime zest, fresh mint, and a sprinkle of chaat masala",
        "{ingredient} with creamy tahini dip, toasted seeds, and a light drizzle of honey",
    ],
    ('legume', 'breakfast'): [
        "Spiced {ingredient} toast with creamy tahini, sliced avocado, and fresh lemon zest",
        "Warm {ingredient} bowl with cumin-tempered olive oil, fresh coriander, and gluten-free flatbread",
        "Savory {ingredient} patties with roasted cherry tomatoes and a fresh herb yogurt",
        "Classic {ingredient} poha with mustard seeds, curry leaves, and roasted peanuts",
        "Spiced {ingredient} chilla (savoury pancake) with mint-coriander chutney and sliced cucumber",
    ],
    ('dairy', 'breakfast'): [
        "Creamy {ingredient} parfait with homemade granola, fresh blueberries, and a drizzle of honey",
        "Smooth {ingredient} bowl layered with rolled oats, sliced kiwi, and toasted pumpkin seeds",
        "Chilled {ingredient} with seasonal stone fruit, chia seeds, and a pinch of cardamom",
        "Warm {ingredient} with roasted figs, walnuts, and a light drizzle of raw honey",
        "{ingredient} lassi with fresh mango, a pinch of cardamom, and toasted almonds",
    ],
    ('nut_seed', 'breakfast'): [
        "Toasted {ingredient} butter on rice cakes with sliced banana and a dusting of cinnamon",
        "Warm {ingredient} and oat bowl with dried cranberries, coconut flakes, and maple syrup",
        "Blended {ingredient} smoothie with oat milk, frozen mango, and a pinch of ground turmeric",
        "{ingredient} energy bowl with rolled oats, grated apple, cinnamon, and a drizzle of honey",
        "Soaked {ingredient} with overnight oats, fresh berries, and coconut milk",
    ],
    ('vegetable', 'breakfast'): [
        "Warm {ingredient} bowl with herbed quinoa, sliced avocado, and a lemon-herb dressing",
        "Sauteed {ingredient} hash with crispy chickpeas, fresh herbs, and whole-grain toast",
        "Roasted {ingredient} with creamy tahini, toasted seeds, and fresh dill",
        "Spiced {ingredient} sabzi with turmeric and cumin, served with warm roti",
        "Pan-tossed {ingredient} with mustard seeds, curry leaves, and grated coconut",
    ],
    ('mixed_dish', 'breakfast'): [
        "Warm {ingredient} bowl with sauteed greens, sesame seeds, and a side of sliced avocado",
        "Breakfast {ingredient} with roasted cherry tomatoes, fresh coriander, and lime wedge",
        "Hearty {ingredient} with warm flatbread, sliced cucumber, and fresh mint chutney",
        "Savoury {ingredient} with turmeric-roasted cauliflower and a cooling tahini dip",
        "Light {ingredient} bowl with toasted seeds, fresh seasonal fruit, and oat milk",
    ],
    ('other', 'breakfast'): [
        "Nourishing {ingredient} bowl with toasted seeds, fresh seasonal fruit, and oat milk",
        "Warm {ingredient} with sliced avocado, micro herbs, and a sprinkle of dukkah",
        "{ingredient} breakfast bowl with house granola, banana slices, and a drizzle of honey",
        "{ingredient} with coconut yogurt, roasted fruit compote, and toasted pumpkin seeds",
    ],

    # ── LUNCH ────────────────────────────────────────────────────────────────
    ('meat', 'lunch'): [
        "Herb-marinated {ingredient} with roasted sweet potato wedges and wilted garlic spinach",
        "Slow-braised {ingredient} with caramelised root vegetables and a herbed brown rice pilaf",
        "Pan-seared {ingredient} with lemon-garlic dressing, steamed tenderstem broccoli, and quinoa",
        "Grilled {ingredient} with chimichurri sauce, roasted peppers, and herbed couscous",
    ],
    ('fish_seafood', 'lunch'): [
        "Pan-seared {ingredient} with lemon-caper dressing, steamed asparagus, and wild rice",
        "Ginger-glazed {ingredient} with sesame-dressed cucumber ribbons and herbed brown rice",
        "Herb-crusted {ingredient} with roasted cherry tomatoes, wilted spinach, and quinoa tabbouleh",
        "Tandoori {ingredient} with cucumber raita, lemon wedge, and warm naan",
    ],
    ('vegetable', 'lunch'): [
        "Roasted {ingredient} bowl with golden tahini, spiced chickpeas, and herbed quinoa",
        "Charred {ingredient} and farro bowl with miso-ginger dressing and pickled cucumber",
        "Warm {ingredient} and lentil salad with smoked paprika, fresh parsley, and lemon vinaigrette",
        "Spiced {ingredient} sabzi wrap with mint chutney, thinly sliced red onion, and warm roti",
        "Grilled {ingredient} mezze plate with hummus, warm pita, olives, and pickled peppers",
        "Roasted {ingredient} and quinoa bowl with pomegranate seeds, avocado slices, and fresh herbs",
    ],
    ('legume', 'lunch'): [
        "Slow-simmered {ingredient} stew with warming spices, fresh parsley, and warm flatbread",
        "Spiced {ingredient} and roasted vegetable curry with basmati rice and cooling mint chutney",
        "Hearty {ingredient} soup with smoked paprika, fresh thyme, and a squeeze of preserved lemon",
        "Classic {ingredient} dal tadka with cumin-tempered ghee, crispy fried onions, and basmati rice",
        "Mediterranean {ingredient} falafel bowl with tahini sauce, roasted peppers, and warm pita",
        "{ingredient} rajma with caramelised tomatoes, warming spices, and steamed brown rice",
        "Smoky {ingredient} hummus bowl with charred flatbread, cucumber sticks, and za'atar oil",
    ],
    ('grain', 'lunch'): [
        "Nutty {ingredient} salad with roasted root vegetables, spiced chickpeas, and lemon-herb dressing",
        "Warm {ingredient} bowl with sliced avocado, pickled ginger, and sesame dressing",
        "Herbed {ingredient} pilaf with caramelised shallots, dried apricot, and toasted pine nuts",
        "Fragrant {ingredient} biryani with caramelised onions, whole spices, and fresh mint chutney",
        "Mediterranean {ingredient} tabbouleh with fresh parsley, tomatoes, mint, and lemon dressing",
        "{ingredient} bowl with roasted pumpkin, baby spinach, toasted pepitas, and tahini",
    ],
    ('dairy', 'lunch'): [
        "Creamy {ingredient} and roasted vegetable galette with a crisp peppery side salad",
        "Chilled {ingredient} and herb dip with rainbow crudites, olives, and seeded crackers",
        "Warm {ingredient} soup with roasted garlic, fresh thyme, and crusty gluten-free bread",
        "Grilled {ingredient} paneer with tandoori spices, minted yogurt, and warm chapati",
        "{ingredient} bowl with roasted beets, walnuts, fresh dill, and a balsamic glaze",
    ],
    ('fruit', 'lunch'): [
        "Fresh {ingredient} and mixed leaf salad with candied walnuts and aged balsamic dressing",
        "Warm {ingredient} and grain bowl with peppery arugula, roasted beets, and toasted pepitas",
        "Chilled {ingredient} and chickpea salad with cucumber, mint, and toasted sesame",
        "{ingredient} and legume salad with fresh herbs, lemon dressing, and warm flatbread",
    ],
    ('nut_seed', 'lunch'): [
        "Toasted {ingredient} and roasted vegetable bowl with golden tahini and lemon zest",
        "Warm {ingredient} and lacinato kale salad with dried cranberries, orange zest, and olive oil",
        "Crushed {ingredient} and herb-crusted baked falafel with cucumber-tahini sauce",
        "{ingredient} and roasted beetroot grain bowl with avocado slices and herb dressing",
    ],
    ('mixed_dish', 'lunch'): [
        "Hearty {ingredient} with a fresh herb garnish, crisp side salad, and gluten-free bread",
        "Spiced {ingredient} with roasted cauliflower florets, fresh coriander, and mint chutney",
        "Warm {ingredient} bowl with steamed greens, sesame oil, and a lemon-ginger dressing",
        "{ingredient} with pickled cucumber, fresh mint, and a side of brown rice",
    ],
    ('other', 'lunch'): [
        "Nourishing {ingredient} bowl with roasted seasonal vegetables, tahini, and fresh herbs",
        "Warm {ingredient} with steamed broccolini, brown rice, and a lemon-ginger dressing",
        "{ingredient} with spiced roasted vegetables, fresh coriander chutney, and warm flatbread",
        "Herb-dressed {ingredient} with mixed leaves, toasted seeds, and a balsamic reduction",
    ],

    # ── DINNER ───────────────────────────────────────────────────────────────
    ('meat', 'dinner'): [
        "Slow-roasted {ingredient} with a herb crust, caramelised root vegetables, and a rich pan jus",
        "Oven-braised {ingredient} with caramelised onions, wild rice pilaf, and steamed seasonal greens",
        "Grilled {ingredient} with fresh chimichurri, roasted sweet potato mash, and sauteed cavolo nero",
        "Tandoori {ingredient} with cumin-spiced basmati rice, mint chutney, and pickled onions",
    ],
    ('fish_seafood', 'dinner'): [
        "Oven-baked {ingredient} with lemon-dill butter sauce, roasted asparagus, and herbed quinoa pilaf",
        "Pan-seared {ingredient} with mango-avocado salsa, steamed jasmine rice, and wilted greens",
        "Herb-crusted {ingredient} fillet with roasted vine tomatoes, capers, and wilted baby spinach",
        "Tandoori {ingredient} with cucumber raita, saffron rice, and charred lemon",
        "Mediterranean {ingredient} with olives, capers, roasted peppers, and herbed couscous",
    ],
    ('legume', 'dinner'): [
        "Aromatic {ingredient} dal with golden turmeric, cumin-tempered ghee, and fragrant basmati rice",
        "Smoky {ingredient} and roasted vegetable tagine with herbed couscous and fresh coriander",
        "Warming {ingredient} and sweet potato curry with coconut milk and steamed brown rice",
        "Classic {ingredient} chana masala with diced tomatoes, warming spices, and soft puri",
        "Spiced {ingredient} lentil soup with preserved lemon, fresh parsley, and warm flatbread",
        "Creamy {ingredient} makhani with warming spices, charred naan, and sliced cucumber",
        "{ingredient} rajma with caramelised onions, garam masala, and steamed basmati rice",
    ],
    ('vegetable', 'dinner'): [
        "Oven-roasted {ingredient} with za'atar, creamy tahini, spiced chickpeas, and warm flatbread",
        "Slow-roasted {ingredient} with caramelised onions, green lentils, and pomegranate molasses",
        "Spiced {ingredient} sabzi with cumin seeds, dried mango powder, and warm roti",
        "Classic {ingredient} aloo with turmeric, mustard seeds, and steamed basmati rice",
        "Mediterranean {ingredient} stew with chickpeas, olives, tomatoes, and crusty bread",
        "Charred {ingredient} with farro, miso-glazed aubergine, and pickled ginger",
    ],
    ('grain', 'dinner'): [
        "Herbed {ingredient} pilaf with caramelised shallots, roasted wild mushrooms, and wilted spinach",
        "Warm {ingredient} and roasted vegetable bake with golden tahini and toasted pine nuts",
        "Saffron-scented {ingredient} pulao with dried apricots, toasted almonds, and fresh herbs",
        "Fragrant {ingredient} biryani with whole spices, caramelised onions, and cucumber mint relish",
        "{ingredient} khichdi with cumin, ginger, bay leaf, and a side of crispy papad and mango pickle",
    ],
    ('mixed_dish', 'dinner'): [
        "Slow-simmered {ingredient} with aromatic whole spices, fluffy basmati rice, and fresh mint",
        "Warming {ingredient} with coconut milk, fresh coriander, steamed greens, and jasmine rice",
        "Oven-baked {ingredient} with roasted root vegetables and a fragrant herb dressing",
        "Spiced {ingredient} with caramelised onions, warm flatbread, and cooling mint chutney",
        "{ingredient} with roasted seasonal vegetables, lemon-herb tahini, and herbed quinoa",
    ],
    ('dairy', 'dinner'): [
        "Baked {ingredient} and roasted vegetable gratin with fresh thyme, garlic, and a garden salad",
        "Creamy {ingredient} and herb sauce over gluten-free pasta with roasted cherry tomatoes",
        "Grilled {ingredient} paneer tikka with spiced peppers, cooling raita, and warm naan",
        "Warm {ingredient} fondue with gluten-free bread soldiers, roasted broccoli, and cornichons",
        "{ingredient} korma with warming spices, toasted cashews, and fragrant basmati rice",
    ],
    ('nut_seed', 'dinner'): [
        "Toasted {ingredient} crusted baked tofu with miso-glazed vegetables and steamed brown rice",
        "Warm {ingredient} and roasted beetroot salad with avocado cream, peppery arugula, and balsamic",
        "Blended {ingredient} romesco sauce over courgette noodles with roasted cherry tomatoes and basil",
        "{ingredient} crusted roasted cauliflower with herbed couscous and pomegranate dressing",
    ],
    ('fruit', 'dinner'): [
        "Warm {ingredient} and spinach salad with toasted walnuts, pomegranate seeds, and balsamic glaze",
        "Slow-roasted {ingredient} and red cabbage with balsamic, served with roasted root vegetables",
        "{ingredient} and lentil salad with rocket, avocado slices, toasted pepitas, and honey-mustard",
        "Warm {ingredient} chutney with roasted beetroot, herbed quinoa, and toasted pumpkin seeds",
    ],
    ('other', 'dinner'): [
        "Oven-roasted {ingredient} with a herb crust, steamed seasonal greens, and roasted sweet potato",
        "Spiced {ingredient} with warming cumin and coriander, fragrant basmati rice, and fresh mint chutney",
        "Baked {ingredient} with lemon-herb dressing, quinoa pilaf, and sauteed asparagus tips",
        "Warm {ingredient} with caramelised onions, roasted vegetables, and herbed flatbread",
    ],
}

_SNACK_TEMPLATES = {
    'nut_seed': [
        "A generous handful of roasted {ingredient} with dried cranberries and dark chocolate chips",
        "Toasted {ingredient} trail mix with pumpkin seeds, coconut flakes, and a drizzle of honey",
        "Spiced {ingredient} with cumin, smoked paprika, and a squeeze of lemon",
    ],
    'fruit': [
        "Fresh {ingredient} slices with almond butter and a dusting of cinnamon",
        "Chilled {ingredient} with coconut yogurt dip, granola, and a mint garnish",
        "{ingredient} with a small wedge of cheese and a few walnuts",
    ],
    'dairy': [
        "Creamy {ingredient} with crisp cucumber sticks, cherry tomatoes, and rice crackers",
        "Smooth {ingredient} with a drizzle of honey, crushed pistachios, and fresh raspberries",
        "{ingredient} with sliced apple, cinnamon, and a sprinkle of granola",
    ],
    'vegetable': [
        "Crisp {ingredient} sticks with smoky hummus, kalamata olives, and gluten-free crackers",
        "Oven-roasted {ingredient} with flaky sea salt and a fresh herb dipping sauce",
        "Raw {ingredient} with a tahini-lemon dip and toasted sesame seeds",
    ],
    'grain': [
        "Warm {ingredient} with smashed avocado, sliced tomato, and a pinch of za'atar",
        "Crunchy {ingredient} with creamy tahini dip and a platter of fresh vegetable sticks",
    ],
    'legume': [
        "Crispy oven-roasted {ingredient} dusted with smoked paprika and cumin",
        "Warm {ingredient} hummus with fresh vegetable sticks and gluten-free pita wedges",
        "Spiced {ingredient} chaat with tamarind chutney, sev, and diced onion",
    ],
    'other': [
        "A serving of {ingredient} with a small handful of mixed seeds and fresh fruit",
        "Fresh {ingredient} with seeded rice crackers and a smear of nut butter",
    ],
}


def generate_description(food: dict, slot: str,
                         profile: Optional[dict] = None) -> str:
    """
    Generate a restaurant-style meal description.
    Tries (food_group, slot) key first; falls back to ('other', slot).
    """
    group    = food.get('food_group_tag', 'other')
    raw_name = food.get('name', 'Seasonal dish')
    ing      = clean_name(raw_name)

    if slot == 'snack':
        templates = _SNACK_TEMPLATES.get(group, _SNACK_TEMPLATES['other'])
        return random.choice(templates).format(ingredient=ing)

    key = (group, slot)
    if key not in _TEMPLATES:
        key = ('other', slot)
    if key not in _TEMPLATES:
        return f"Nourishing {ing} with seasonal vegetables and herbed grains"

    return random.choice(_TEMPLATES[key]).format(ingredient=ing)
