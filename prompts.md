# prompts.md: Key AI Prompts Used During NutriAI Development

Final Project · Option A · NutriAI  
AI tools used: **Claude Code (claude-sonnet-4-6)** via the Claude Code CLI

---

## How AI was used

All development was done interactively with Claude Code. The prompts below are representative of the key decisions and implementations. Claude Code had full access to read, write, and run files. All architectural decisions, data validation, and correctness checks were reviewed and approved by the developer.

---

## Prompt 1: Data Pipeline Setup

**Goal:** Build the USDA data ingestion pipeline.

> "Build a Python script `build_database.py` that fetches food data from the USDA FoodData Central API. Use the Foundation, SR Legacy, and Survey FNDDS data types. Store in SQLite at `data/processed/nutriai_foods.db`. Columns: fdc_id, name, data_type, food_category, calories, protein, carbs, fat, fiber, calcium, iron, sodium, potassium, magnesium, zinc, vitamin_b12, vitamin_d, vitamin_c, omega3_ala, omega3_epa, omega3_dha. Handle rate limits with retry logic. Target: 10,000+ foods."

**Result:** `build_database.py` - 13,620 foods ingested across 3 data types.

---

## Prompt 2: Clinical Enrichment Pipeline

**Goal:** Tag every food with clinical flags needed for filtering.

> "Write `enrich_database.py` that enriches the foods table with: (1) allergen tags (has_gluten, has_dairy, has_tree_nuts, has_shellfish, has_soy, has_eggs, has_peanuts, has_fish, has_pork, has_honey) using keyword matching with word-boundary guards; (2) diet type tags (is_vegan, is_vegetarian, is_pescatarian); (3) clinical flags: is_high_fodmap (Monash low-FODMAP list), is_gerd_trigger (acid/fried/citrus/chocolate triggers), gi_estimate (0-100 from keyword lookup), is_low_gi (gi<=55), is_high_sodium (sodium>400mg/100g); (4) food_group_tag; (5) meal slot suitability (suitable_breakfast, etc.). Use batch executemany in groups of 500. Must run in under 60 seconds."

**Result:** `src/data_pipeline/enricher.py` - enriches all 13,620 foods in ~24 seconds.

---

## Prompt 3: Bloom Filter (Sketching)

**Goal:** Implement a Bloom filter bank for O(1) allergen safety checks.

> "Implement `src/ml/bloom_filter.py` with an `AllergenFilterBank` class. Use 11 allergen filters (gluten, dairy, tree_nuts, shellfish, soy, eggs, peanuts, fish, pork, honey, meat). Each filter: m=1,200,000 bits using bitarray, k=7 hash functions using the two-hash trick (MD5 + SHA-256 seeds). `build(db_path)` loads all fdc_ids with each allergen flag set and populates the filters. `is_safe(fdc_id, allergen_list)` returns True only if the food is not in any of the specified filters. Guarantee zero false negatives."

**Result:** `src/ml/bloom_filter.py` - 11 filters, 873ms build time, 97.8µs/lookup, 0% false-negative rate.

---

## Prompt 4: Nutrient Embeddings and ANN Recommendation

**Goal:** Implement content-based recommendation using nutrient embeddings.

> "Implement `src/ml/embeddings.py` with a `NutrientEmbedder` class. Encode each food as a 15-dim vector over: calories, protein, carbs, fat, fiber, calcium, iron, potassium, magnesium, zinc, vitamin_b12, vitamin_d, vitamin_c, omega3_ala, omega3_epa. Transform: (1) apply feature weights (B12=2.0, vit_D=1.8, iron=1.5, fiber=1.3, others=1.0); (2) log1p; (3) L2-normalise. Build a sklearn NearestNeighbors index (cosine, brute-force, n_jobs=-1). `load_candidates(db_path, where)` fetches the safe food pool and builds the index. `query(gap_vector, k=120)` takes a dict of remaining fractional RDA needs and returns top-k food IDs by cosine similarity."

**Result:** `src/ml/embeddings.py` - 230% better gap-fill vs random selection.

---

## Prompt 5: Multi-Objective Ranker (Ranking)

**Goal:** Score each candidate food across 5 objectives.

> "Implement `src/ml/ranker.py` with a `MealRanker` class. Score each food with a weighted sum of 5 dimensions: W1=0.20 calorie fit (Gaussian on delivered_kcal/slot_target, sigma=0.5); W2=0.32 nutrient gap-fill (fractional RDA fill weighted by urgency); W3=0.23 diversity (Simpson's D penalty 1/(1+count*decay)); W4=0.17 priority micro (nutrient-calibrated thresholds per declared priority); W5=0.08 clinical bonus (condition-specific food group rewards). Hard-exclude already-selected foods via used_ids set. Track food_group_tag counts for diversity. Include `compute_diversity_score()` returning Simpson's D."

**Result:** `src/ml/ranker.py` - diversity 0.776 vs 0.617 random baseline (+26%).

---

## Prompt 6: Streaming Generator

**Goal:** Wire all components into a streaming 7-day plan generator.

> "Implement `src/planner/generator.py` with `generate_plan_stream(profile)` as a Python generator. For each of 7 days x 3 slots: (1) compute gap vector from day totals so far; (2) ANN query k=120; (3) Bloom filter check; (4) slot suitability filter; (5) multi-objective rank; (6) yield {type:'meal', day, slot, meal, elapsed_s} immediately after selection; (7) accumulate day totals. After each day, compute rda_gaps. Final yield: {type:'complete', plan}. Include calorie-proportional serving scaling: max(1.0, calorie_target/2000) * base_g. Add per-day calorie budget guard: if a food would push day over 115% of target, prefer the next best ranked food."

**Result:** `src/planner/generator.py` - streaming generator, 21 live events, adaptive gap feedback loop.

---

## Prompt 7: RDA Gap Normalisation Fix

**Goal:** Fix units mismatch that caused calcium to dominate the gap vector.

> "The gap vector in `compute_gap_vector` returns absolute values (calcium=800mg, fiber=20g). This causes calcium (800mg gap) to be 40x larger than fiber (20g gap) in the ANN query and gap-fill score, so the system only optimises for calcium. Fix: return fractional values (0.0-1.0 of daily RDA remaining): gap[col] = max(0, (rda_val - actual) / rda_val). Also exclude sodium from the gap (it is a cap, not a floor). Update `_gap_fill_score` in ranker.py to normalize food contribution by dividing food_val by rda_val before weighting by gap fraction."

**Result:** Priya "days meeting 80% RDA" improved from 0/7 to 4/7.

---

## Prompt 8: Protein Floor and Weight Input

**Goal:** Add body-weight-based protein floor and weight input to UI.

> "Add a weight_kg field to the profile dict. In the generator, after get_rda(), apply a protein floor: if weight_kg > 0, protein_floor = 0.7 * weight_kg; if protein_floor > rda['protein_g'], override rda['protein_g'] = protein_floor. In app.py, add a number_input for body weight (30-200 kg) between the calorie slider and diet type selector. In the day summary strip, show a Protein metric with delta label showing the effective target and a checkmark/warning. Persona presets: Priya=58kg, Ravi=80kg, Mei=62kg, James=88kg."

**Result:** Dynamic protein target in UI; James overrides RDA (61.6g vs 56g); Priya does not (40.6g < 46g RDA).

---

## Prompt 9: Streamlit App and Analytics Tab

**Goal:** Build the full Streamlit UI with all 4 tabs.

> "Build app.py as a Streamlit application. Sidebar: persona quick-load buttons (Priya/Ravi/Mei/James), profile inputs (age, sex, calories, weight_kg, diet_mode, conditions, allergens, priority_nutrients). Main area: streaming progress bar that updates live as generate_plan_stream yields events. Four tabs: (1) Plan - 7 day sub-tabs, meal cards with nutrients; (2) Analytics - food group bar chart, 7-day calorie trend, nutrient heatmap, 'Days >= 80% RDA' metric excluding VitD/Omega3/Sodium with footnote; (3) Why Excluded - orange cards per excluded food with clinical reason; (4) Export - PDF and CSV download buttons."

**Result:** `app.py` - full Streamlit UI with streaming, analytics, explanations, and export.

---

## Prompt 10: Technical Brief Generation

**Goal:** Generate the 4-page PDF technical brief required by the assignment.

> "Write `generate_brief.py` that produces `brief.pdf` using fpdf2. 4 pages: (1) title/executive summary/architecture/data stats; (2) all 5 ML techniques with implementation details and benchmark tables; (3) full persona pass/fail table (4 personas x 6 capabilities) plus key outcome metrics; (4) adaptive learning explanation, deployment details, honest limitations, technical reflection. Use the NutriAI colour palette (navy, blue, light grey). Must be <=4 pages, professional layout."

**Result:** `generate_brief.py` and `brief.pdf`.
