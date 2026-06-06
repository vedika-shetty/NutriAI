prompts.md - key AI prompts used during NutriAI development

Tool: Claude Code (claude-sonnet-4-6), used interactively via CLI throughout the week.
All architectural decisions, validation, and correctness checks were reviewed by me.

---

1. "I need to build a diet planning app for BAX-423 that handles clinical conditions like IBS and GERD, allergens, and dietary preferences. I want to use the USDA FoodData Central API. What's the best database structure to support all 4 clinical personas and run under 60 seconds? Should I use SQLite or Postgres or something else for a Streamlit deployment?"

Used this to decide on SQLite with pre-enriched clinical tags stored as integer columns; I cut several USDA nutrient IDs from the suggested schema that weren't clinically relevant to the 4 test personas (e.g. dropped sugars_added, cholesterol_mg since none of the personas required them).

---

2. "For the BAX-423 techniques requirement I need at least 2 from different lectures. I'm thinking bloom filters for allergen checking and ANN-based embeddings for food recommendation. Does this pairing make sense for a diet planner or should I use Spark + ranking instead? I need to benchmark each one."

Settled on bloom filter + embeddings + multi-objective ranking + streaming as the core techniques; I dropped the Spark suggestion because the dataset is 13k rows and per-request latency mattered more than batch throughput, and went with sklearn NearestNeighbors over FAISS because FAISS has no stable Python 3.13 wheels.

---

3. "Write build_database.py to fetch foods from USDA FoodData Central using their API. Use Foundation, SR Legacy, and Survey FNDDS data types. Store in SQLite at data/processed/nutriai_foods.db. Columns I need: fdc_id, name, data_type, food_category, calories, protein, carbs, fat, fiber, calcium, iron, sodium, potassium, magnesium, zinc, vitamin_b12, vitamin_d, vitamin_c, omega3_ala, omega3_epa, omega3_dha. Handle 429 rate limits with retry logic. Get at least 10,000 foods."

Used as the core ingestion script; I added deduplication logic to keep the record with the most complete nutrient profile when the same food appeared in multiple data types (SR Legacy and FNDDS both include many staples), and tuned the batch size from 50 to 200 IDs per request to cut runtime from ~45 min to ~13 min.

---

4. "Write enrich_database.py that adds clinical and diet tags to every food already in the database. Tags needed: allergen flags using word-boundary regex guards (has_gluten, has_dairy, has_tree_nuts, has_shellfish, has_soy, has_eggs, has_peanuts, has_fish, has_pork, has_honey), diet type flags (is_vegan, is_vegetarian, is_pescatarian), clinical flags (is_high_fodmap from Monash University keywords, is_gerd_trigger, gi_estimate 0-100 from a lookup table, is_low_gi for GI<=55), food_group_tag, and meal slot suitability columns. Run in under 60 seconds using batch executemany."

Used as enricher.py after auditing the FODMAP keyword list against actual Monash data and adding about 40 missing trigger foods; I also added a rule to force-clear is_vegetarian when has_pork=1 because processed pork products like salami and pepperoni weren't caught by the meat keywords alone.

---

5. "Implement AllergenFilterBank in src/ml/bloom_filter.py. Build 11 filters (one per allergen), each with m=1,200,000 bits using bitarray, k=7 hash functions using the two-hash trick (MD5 and SHA256 seeds). build(db_path) populates filters from fdc_ids in the database. is_safe(fdc_id, allergen_list) returns True only if the food doesn't appear in any of the requested filters. Must guarantee zero false negatives."

Used directly; I added a fallback SQL safety check for any fdc_id not present at filter build time, to guarantee safety even if new records were added to the database after the filter was built.

---

6. "I want to recommend foods that fill the user's remaining nutrient gap for the day. My idea is to embed each food as a 15-dimensional nutrient vector and use cosine ANN search with the daily gap as the query. But I'm worried calcium (1000mg daily target) will completely dominate fiber (25g) in the distance calculation. What's the right way to normalize this?"

Used the analysis to confirm a log1p + L2 approach for food embeddings; the insight about scale imbalance led me to also apply fractional normalization to the gap vector itself - returning 0 to 1 fractions of RDA remaining instead of absolute values - which I implemented separately in rda.py and which turned out to be the most impactful single fix in the whole project.

---

7. "Implement NutrientEmbedder in src/ml/embeddings.py. 15-dimensional vectors over calories, protein, carbs, fat, fiber, calcium, iron, potassium, magnesium, zinc, vitamin_b12, vitamin_d, vitamin_c, omega3_ala, omega3_epa. Transform pipeline: apply feature weights (B12=2.0, vitD=1.8, iron=1.5, fiber=1.3, others=1.0), then log1p, then L2 normalize. Build a sklearn NearestNeighbors index with cosine metric. query(gap_vector, k=120) returns top-k food IDs by cosine similarity to the gap."

Used this output as the embeddings module; I added a get_candidates_by_ids() method that the generated code hadn't included, which was needed to retrieve full food records from fdc_ids returned by ANN search.

---

8. "Implement MealRanker in src/ml/ranker.py. Score each food as a weighted sum of 5 dimensions: calorie fit 20% (Gaussian around the slot's calorie target, sigma=0.5), nutrient gap-fill 32%, diversity 23% (Simpson's D with per-group decay penalties), priority micronutrient 17%, clinical condition bonus 8%. Hard-exclude already-selected foods via a used_ids set. Track food_group_tag counts for diversity. Include compute_diversity_score() returning Simpson's D."

Used as the scoring backbone and iterated on the weights twice based on observed test plans - bumped gap-fill from 25% to 32% because early plans were calorie-correct but nutritionally shallow, and added group-specific decay constants (legume=0.30, vegetable=0.45) after noticing legumes and vegetables never appeared because grain and mixed-dish foods consistently outscored them.

---

9. "The gap vector in compute_gap_vector() is returning absolute values - calcium gap is ~800mg and fiber gap is ~20g. In the ANN cosine search calcium is effectively 40x more important than fiber. Priya is meeting her RDA targets 0/7 days even though her plan looks reasonable. How do I fix the normalization?"

Applied the fractional fix directly to rda.py (return 0.0-1.0 of daily RDA remaining per nutrient) and updated _gap_fill_score in ranker.py to normalize food contribution against the RDA before weighting; Priya's days meeting 80% RDA targets went from 0/7 to 4/7 after this change.

---

10. "Implement generate_plan_stream() in src/planner/generator.py as a Python generator. For each of 7 days x 3 meal slots: compute the gap vector from meals assigned so far today, run ANN query k=120, apply bloom filter, apply slot suitability filter, run multi-objective rank, yield a meal event right away. Include calorie-proportional serving scaling max(1.0, calorie_target/2000) * base_g and a per-day calorie budget guard at 110% of target."

Used as the streaming generator core; I added a legume-forcing step after noticing zero legumes appeared in any 7-day plan because grain and mixed-dish foods consistently outranked them - the fix searches the full candidate pool once per week when no legume has been selected yet and forces one in.

---

11. "Build app.py as a Streamlit application. Sidebar: persona quick-load presets for Priya, Ravi, Mei, and James, then profile inputs for age, sex, daily calories, body weight in kg, diet mode, clinical conditions, allergens, and priority nutrients. Main area: live progress bar that updates as the generator yields events. Four tabs: Plan (7 day sub-tabs with meal cards and nutrient tables), Analytics (food group chart, 7-day calorie trend, nutrient heatmap), Why Excluded (per-food exclusion cards with clinical reasons), Export (PDF and CSV download buttons)."

Used as the base UI; I removed a refresh button that wasn't needed since Streamlit reruns on sidebar change, added the 'Days >= 80% RDA' metric with footnote explaining why vitamin D and sodium are excluded from that count, and rewrote the meal card CSS because the generated version had font size issues in the nutrient table.

---

12. "I'm getting very strange items in Mei's vegan meal plan - Pacific oysters, 'Cereal' repeated 3 days in a row, yeast extract spread as a lunch, McDonald's pizza, candies listed as a breakfast fruit. These all have is_vegan=1 in the database. What's the systematic reason this is happening and what's the most general fix rather than blocking individual food names one by one?"

Used this analysis to understand USDA Survey FNDDS naming conventions - the database contains everything respondents reported eating including brand items, sub-ingredients, and condiments - and designed category-level SQL exclusion patterns (Mollusks%, Cereals ready-to-eat%, Fast foods%, Cereal,%, Candies,%) that excluded about 500 ineligible items in one pass without needing to block individual food names.

---

13. "Write generate_brief.py that produces brief.pdf using fpdf2. 4 pages: page 1 title, executive summary, architecture overview, and data stats. Page 2 all 5 ML techniques with implementation details and benchmark tables. Page 3 full persona pass/fail table for 4 personas x 6 capabilities plus key outcome metrics. Page 4 adaptive learning explanation, deployment details, honest limitations, and technical reflection. Use a navy and blue color palette, professional layout."

Used the output to generate the initial brief structure; I rewrote the limitations section to be more honest about the partial results for Mei's fiber target and James's sodium cap, corrected the benchmark numbers to match actual test run outputs, and removed an "adaptive RL" section the generator added that wasn't actually implemented.
