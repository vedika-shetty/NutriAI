# NutriAI — Personalised 7-Day Diet Planner

BAX-423 Final Project · Option A · UC Davis GSM · Spring 2026

**Live app:** https://nutriai-bax423.streamlit.app

---

## What it does

Generates a personalised 7-day meal plan in under 2 seconds, tailored to a user's:
- Clinical conditions (IBS, GERD, Type 2 Diabetes, Hypertension)
- Dietary preferences (vegan, vegetarian, pescatarian, non-vegetarian)
- Allergen restrictions (11 types: gluten, dairy, tree nuts, soy, shellfish, eggs, peanuts, fish, pork, honey)
- Daily calorie target and body-weight-based protein floor (0.7 g/kg)

Exports a 10-page PDF plan and three CSVs. Explains why every excluded food was filtered out.

---

## Quick start (local)

```bash
# 1. Clone and enter directory
cd nutriai/

# 2. Install dependencies
pip install -r requirements.txt

# 3. Add your USDA API key (only needed if rebuilding the database)
echo "USDA_API_KEY=your_key_here" > .env

# 4. Run the app (database is pre-built and included)
streamlit run app.py
```

App opens at **http://localhost:8501**

---

## Rebuilding the database (optional — pre-built DB included)

The database `data/processed/nutriai_foods.db` is included in the submission (13,620 foods, ~10 MB).
Only run these if you want to rebuild from scratch:

```bash
python build_database.py    # ~13 minutes — fetches from USDA FoodData Central API
python enrich_database.py   # ~24 seconds — applies clinical/allergen/diet tags
```

---

## Running the test suite

```bash
python test_personas.py
```

Tests all 4 required personas (Priya, Ravi, Mei, James) against all 6 core capabilities.
Expected output: all checks PASS in ~5 seconds total.

---

## Generating the technical brief

```bash
python generate_brief.py    # produces brief.pdf (4 pages)
```

---

## Project structure

```
nutriai/
├── app.py                      Streamlit UI
├── build_database.py           One-time USDA data fetch
├── enrich_database.py          One-time clinical/allergen enrichment
├── test_personas.py            4-persona capability test suite
├── generate_brief.py           Produces brief.pdf
├── requirements.txt
├── README.md                   This file
├── brief.pdf                   4-page technical brief (generated)
├── prompts.md                  Key AI prompts used during development
├── data/
│   └── processed/
│       └── nutriai_foods.db    SQLite, 13,620 foods, 47 columns
└── src/
    ├── config.py               RDA tables, serving sizes, nutrient IDs
    ├── ml/
    │   ├── bloom_filter.py     Allergen safety (Sketching)
    │   ├── embeddings.py       15-dim nutrient vectors (Embeddings + Recommendation)
    │   └── ranker.py           5-objective meal scorer (Ranking)
    ├── planner/
    │   ├── generator.py        Streaming 7-day plan generator
    │   ├── meal_templates.py   Restaurant-style description generator
    │   └── explainer.py        "Why excluded" reason engine
    ├── nutrition/
    │   └── rda.py              RDA lookup, gap analysis, diversity score
    └── output/
        ├── pdf_export.py       Multi-page PDF (fpdf2)
        └── csv_export.py       Three CSVs + ZIP
```

---

## BAX-423 Techniques implemented

| Technique | Lecture | File |
|-----------|---------|------|
| Sketching (Bloom Filter) | Lecture 4 | `src/ml/bloom_filter.py` |
| Embeddings | Lecture 6 | `src/ml/embeddings.py` |
| Recommendation (ANN) | Lecture 7 | `src/ml/embeddings.py` |
| Ranking | Lecture 8 | `src/ml/ranker.py` |
| Streaming | Lecture 3 | `src/planner/generator.py` |

---

## Environment

- Python 3.13
- Streamlit >= 1.35
- scikit-learn >= 1.4 (NearestNeighbors ANN)
- fpdf2 >= 2.7.9 (PDF export)
- bitarray >= 2.9.2 (Bloom filter)
- See `requirements.txt` for full list

---

## Deployment (Streamlit Community Cloud)

Set the following secrets in the Streamlit Cloud dashboard under Advanced → Secrets:

```toml
USDA_API_KEY = "your_usda_key"
DB_PATH = "data/processed/nutriai_foods.db"
```
