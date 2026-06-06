"""
NutriAI Global Configuration
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Try Streamlit secrets too (for cloud deployment)
try:
    import streamlit as st
    if hasattr(st, 'secrets') and 'USDA_API_KEY' in st.secrets:
        os.environ['USDA_API_KEY'] = st.secrets['USDA_API_KEY']
except Exception:
    pass

USDA_API_KEY   = os.getenv("USDA_API_KEY", "")
USDA_BASE_URL  = "https://api.nal.usda.gov/fdc/v1"
DB_PATH        = os.getenv("DB_PATH", "data/processed/nutriai_foods.db")
BATCH_SIZE     = 20
REQUEST_DELAY  = 0.08
DATA_TYPES     = ["Foundation", "SR Legacy"]

Path("data/processed").mkdir(parents=True, exist_ok=True)

NUTRIENT_IDS = {
    "calories":    1008,
    "protein":     1003,
    "carbs":       1005,
    "fat":         1004,
    "fiber":       1079,
    "calcium":     1087,
    "iron":        1089,
    "sodium":      1093,
    "potassium":   1092,
    "magnesium":   1090,
    "zinc":        1095,
    "vitamin_b12": 1178,
    "vitamin_d":   1114,
    "vitamin_c":   1162,
    "omega3_ala":  1404,
    "omega3_epa":  1405,
    "omega3_dha":  1406,
}

SERVING_SIZES = {
    "default":   150.0,
    "breakfast": 150.0,
    "lunch":     250.0,
    "dinner":    350.0,
    "snack":      50.0,
}
DAILY_CALORIE_TARGETS = {
    "default":   2000,
    "breakfast":  500,
    "lunch":      700,
    "dinner":     700,
    "snack":      100,
}
RDA_FEMALE = {
    "18-30": {"calories": 2000, "protein_g": 46,  "carbs_g": 130, "fat_g": 65,  "fiber_g": 25, "calcium_mg": 1000, "iron_mg": 18,  "sodium_mg": 1500, "potassium_mg": 2600, "magnesium_mg": 310, "zinc_mg": 8,  "vitamin_b12_mcg": 2.4, "vitamin_d_iu": 600, "vitamin_c_mg": 75,  "omega3_g": 1.1},
    "31-50": {"calories": 2000, "protein_g": 46,  "carbs_g": 130, "fat_g": 65,  "fiber_g": 25, "calcium_mg": 1000, "iron_mg": 18,  "sodium_mg": 1500, "potassium_mg": 2600, "magnesium_mg": 320, "zinc_mg": 8,  "vitamin_b12_mcg": 2.4, "vitamin_d_iu": 600, "vitamin_c_mg": 75,  "omega3_g": 1.1},
    "51+":   {"calories": 1800, "protein_g": 46,  "carbs_g": 130, "fat_g": 65,  "fiber_g": 21, "calcium_mg": 1200, "iron_mg": 8,   "sodium_mg": 1500, "potassium_mg": 2600, "magnesium_mg": 320, "zinc_mg": 8,  "vitamin_b12_mcg": 2.4, "vitamin_d_iu": 800, "vitamin_c_mg": 75,  "omega3_g": 1.1},
}
RDA_MALE = {
    "18-30": {"calories": 2500, "protein_g": 56,  "carbs_g": 130, "fat_g": 83,  "fiber_g": 38, "calcium_mg": 1000, "iron_mg": 8,   "sodium_mg": 1500, "potassium_mg": 3400, "magnesium_mg": 400, "zinc_mg": 11, "vitamin_b12_mcg": 2.4, "vitamin_d_iu": 600, "vitamin_c_mg": 90,  "omega3_g": 1.6},
    "31-50": {"calories": 2500, "protein_g": 56,  "carbs_g": 130, "fat_g": 83,  "fiber_g": 38, "calcium_mg": 1000, "iron_mg": 8,   "sodium_mg": 1500, "potassium_mg": 3400, "magnesium_mg": 420, "zinc_mg": 11, "vitamin_b12_mcg": 2.4, "vitamin_d_iu": 600, "vitamin_c_mg": 90,  "omega3_g": 1.6},
    "51+":   {"calories": 2200, "protein_g": 56,  "carbs_g": 130, "fat_g": 83,  "fiber_g": 30, "calcium_mg": 1000, "iron_mg": 8,   "sodium_mg": 1500, "potassium_mg": 3400, "magnesium_mg": 420, "zinc_mg": 11, "vitamin_b12_mcg": 2.4, "vitamin_d_iu": 800, "vitamin_c_mg": 90,  "omega3_g": 1.6},
}
