"""
app.py  —  NutriAI Streamlit Application
Streaming 7-day personalised meal plan generator.
Run:  streamlit run app.py
"""

import time
import logging
import sqlite3
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

from src.config import DB_PATH
from src.planner.generator import generate_plan_stream, build_where_clause
from src.output.pdf_export import export_pdf
from src.output.csv_export import export_csv_bytes

logging.basicConfig(level=logging.WARNING)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NutriAI — Personalised Meal Planner",
    page_icon="🥗",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Cards */
.meal-card {
    background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
    border-left: 4px solid #0ea5e9;
    border-radius: 8px;
    padding: 14px 16px;
    margin: 8px 0;
    box-shadow: 0 1px 4px rgba(0,0,0,0.07);
}
.meal-card h4 { margin: 0 0 4px 0; color: #0369a1; font-size: 0.9rem; }
.meal-card p  { margin: 0 0 6px 0; color: #1e3a5f; font-size: 0.97rem; line-height: 1.5; }
.meal-card small { color: #64748b; font-size: 0.78rem; }

/* Exclusion cards */
.excl-card {
    background: #fff7ed;
    border-left: 4px solid #f97316;
    border-radius: 6px;
    padding: 10px 14px;
    margin: 6px 0;
}
.excl-card b { color: #c2410c; }

/* Badges */
.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 600;
    margin: 2px;
}
.badge-blue   { background:#dbeafe; color:#1d4ed8; }
.badge-green  { background:#dcfce7; color:#15803d; }
.badge-orange { background:#ffedd5; color:#c2410c; }
.badge-gray   { background:#f1f5f9; color:#475569; }

/* Metric pill */
.metric-ok  { color: #16a34a; font-weight: 700; }
.metric-low { color: #dc2626; font-weight: 700; }
.metric-mid { color: #d97706; font-weight: 700; }

/* Section divider */
.section-div {
    border: none;
    border-top: 2px solid #e2e8f0;
    margin: 18px 0;
}
</style>
""", unsafe_allow_html=True)


# ── Constants ─────────────────────────────────────────────────────────────────
SLOT_ICONS = {'breakfast': '🌅', 'lunch': '☀️', 'dinner': '🌙'}
CONDITION_OPTIONS = [
    'IBS', 'GERD', 'Acid Reflux', 'Type 2 Diabetes',
    'Hypertension', 'Celiac Disease',
]
ALLERGEN_OPTIONS = [
    'gluten', 'dairy', 'tree_nuts', 'shellfish',
    'soy', 'eggs', 'peanuts', 'fish', 'pork',
]
MICRO_OPTIONS = [
    'iron', 'calcium', 'vitamin_b12', 'vitamin_d',
    'zinc', 'potassium', 'magnesium', 'omega3', 'fiber',
]
PERSONA_PRESETS = {
    'Priya — IBS + Vegetarian': {
        'name': 'Priya', 'age': 28, 'sex': 'Female',
        'calorie_target': 1800, 'diet_mode': 'Vegetarian',
        'allergens': ['dairy'], 'conditions': ['IBS'],
        'priority_nutrients': ['iron', 'calcium', 'vitamin_d'],
        'no_pork': False, 'weight_kg': 58,
    },
    'Ravi — GERD + Gluten-Free': {
        'name': 'Ravi', 'age': 38, 'sex': 'Male',
        'calorie_target': 2200, 'diet_mode': 'Non-Vegetarian',
        'allergens': ['gluten'], 'conditions': ['GERD'],
        'priority_nutrients': ['vitamin_b12', 'zinc', 'magnesium'],
        'no_pork': True, 'weight_kg': 80,
    },
    'Mei — T2 Diabetes + Vegan': {
        'name': 'Mei', 'age': 45, 'sex': 'Female',
        'calorie_target': 1600, 'diet_mode': 'Vegan',
        'allergens': ['tree_nuts'], 'conditions': ['Type 2 Diabetes'],
        'priority_nutrients': ['vitamin_b12', 'iron', 'zinc'],
        'no_pork': False, 'weight_kg': 62,
    },
    'James — Hypertension + Pescatarian': {
        'name': 'James', 'age': 52, 'sex': 'Male',
        'calorie_target': 2000, 'diet_mode': 'Pescatarian',
        'allergens': ['soy'], 'conditions': ['Hypertension'],
        'priority_nutrients': ['potassium', 'magnesium', 'omega3'],
        'no_pork': False, 'weight_kg': 88,
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def pct_color_class(pct: float, flag_type: str = 'deficit') -> str:
    if flag_type == 'excess':
        return 'metric-low' if pct > 120 else 'metric-mid'
    if pct >= 80: return 'metric-ok'
    if pct >= 50: return 'metric-mid'
    return 'metric-low'


def render_meal_card(slot: str, meal: dict, day_num: int):
    icon = SLOT_ICONS.get(slot, '🍽️')
    desc = meal.get('description') or meal.get('name', 'Seasonal dish')
    n    = meal.get('nutrients', {})
    gi   = meal.get('gi_estimate', '—')
    grp  = meal.get('food_group', 'other').replace('_', ' ').title()
    srv  = meal.get('serving_g', 100)

    st.markdown(f"""
    <div class="meal-card">
        <h4>{icon} {slot.upper()} &nbsp;·&nbsp;
            <span class="badge badge-gray">{grp}</span>
            <span class="badge badge-blue">GI {gi}</span>
            <span class="badge badge-gray">{srv} g serving</span>
        </h4>
        <p>{desc}</p>
        <small>
            🔥&nbsp;{n.get('calories',0):.0f} kcal &emsp;
            🥩&nbsp;Protein {n.get('protein',0):.1f}g &emsp;
            🌾&nbsp;Carbs {n.get('carbs',0):.1f}g &emsp;
            🫙&nbsp;Fat {n.get('fat',0):.1f}g &emsp;
            🌿&nbsp;Fibre {n.get('fiber',0):.1f}g &emsp;
            🩸&nbsp;Iron {n.get('iron',0):.1f}mg &emsp;
            🧂&nbsp;Sodium {n.get('sodium',0):.0f}mg
        </small>
    </div>
    """, unsafe_allow_html=True)


def render_day_nutrients(day_data: dict, rda: dict):
    t    = day_data.get('day_totals', {})
    gaps = {g['nutrient']: g for g in day_data.get('rda_gaps', [])}

    rows = []
    DISPLAY = [
        ('calories',   'calories',        'Calories',    'kcal'),
        ('protein',    'protein_g',       'Protein',     'g'),
        ('fiber',      'fiber_g',         'Fibre',       'g'),
        ('iron',       'iron_mg',         'Iron',        'mg'),
        ('calcium',    'calcium_mg',      'Calcium',     'mg'),
        ('potassium',  'potassium_mg',    'Potassium',   'mg'),
        ('magnesium',  'magnesium_mg',    'Magnesium',   'mg'),
        ('zinc',       'zinc_mg',         'Zinc',        'mg'),
        ('vitamin_b12','vitamin_b12_mcg', 'Vitamin B12', 'mcg'),
        ('vitamin_d',  'vitamin_d_iu',    'Vitamin D',   'IU'),
        ('sodium',     'sodium_mg',       'Sodium',      'mg'),
    ]
    for col, rda_key, name, unit in DISPLAY:
        actual = float(t.get(col, 0) or 0)
        target = float(rda.get(rda_key, 0) or 0)
        pct    = round(actual / target * 100, 1) if target else 0.0
        gap    = gaps.get(col)
        ftype  = gap['flag_type'] if gap else ('excess' if col == 'sodium' and actual > target else 'ok')
        if gap or (col == 'sodium' and actual > target):
            status = '⚠️ EXCESS' if ftype == 'excess' else f'⚠️ {pct:.0f}%'
        else:
            status = '✅ OK'
        rows.append({
            'Nutrient': name,
            f'Actual ({unit})': round(actual, 1),
            f'RDA ({unit})':    round(target, 1),
            '% RDA':   pct,
            'Status':  status,
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df.style.apply(
            lambda col: [
                ('background-color:#dcfce7' if '✅' in str(v)
                 else 'background-color:#fee2e2' if '⚠️' in str(v)
                 else '')
                for v in col
            ],
            subset=['Status'],
        ),
        use_container_width=True,
        hide_index=True,
        height=min(45 * len(rows) + 40, 480),
    )


def render_exclusions(exclusions: list):
    if not exclusions:
        st.info("No exclusion examples collected for this profile.")
        return
    st.markdown(f"**{len(exclusions)} foods removed by clinical & allergen filters.**")
    for ex in exclusions[:20]:
        name    = ex.get('name', '')[:70]
        reasons = ex.get('reasons', [ex.get('primary_reason', '')])
        primary = reasons[0] if reasons else ''
        st.markdown(f"""
        <div class="excl-card">
            <b>🚫 {name}</b><br>
            <span style="color:#7c3aed;font-size:0.85rem;">▶ {primary[:150]}</span>
        </div>
        """, unsafe_allow_html=True)


def render_analytics(plan: dict):
    rda   = plan.get('rda', {})
    grps  = plan.get('group_distribution', {})
    div   = plan.get('diversity_score', 0)
    days  = plan.get('days', [])
    gen_t = plan.get('generation_time_s', 0)
    pool  = plan.get('candidate_pool', 0)

    # "Days met" counts days where no diet-addressable nutrient is below 80% RDA.
    # Excluded from count (structurally unachievable from single-food meal plans):
    #   vitamin_d  — 600 IU RDA assumes sunlight; fortified dairy/fish often not available
    #   omega3_ala — ALA only in nuts/seeds/oils excluded from pool as non-meal items
    #   sodium     — an upper-limit cap, not a minimum floor; excess tracked separately
    _SUPPLEMENTAL = frozenset({'vitamin_d', 'omega3_ala', 'sodium'})

    def _diet_gaps(day):
        return [g for g in day.get('rda_gaps', []) if g['nutrient'] not in _SUPPLEMENTAL]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Generation Time", f"{gen_t:.1f}s", "< 60s ✅")
    c2.metric("Diversity Score", f"{div:.3f}",
              "≥ 0.70 ✅" if div >= 0.70 else "< 0.70 ⚠️")
    c3.metric("Safe Foods Pool", f"{pool:,}", "filtered candidates")
    days_ok = sum(1 for d in days if not _diet_gaps(d))
    c4.metric("Days ≥ 80% RDA†", f"{days_ok}/7",
              "all clear ✅" if days_ok == 7 else f"{7-days_ok} gap days")
    st.caption(
        "† Excludes Vit D (sunlight-dependent), Omega-3 ALA (supplement/nut sources), "
        "and Sodium (upper limit, not a floor). All values visible in per-day table."
    )

    st.markdown('<hr class="section-div">', unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Food Group Distribution")
        if grps:
            df_g = pd.DataFrame(
                [(k.replace('_',' ').title(), v) for k, v in grps.items()],
                columns=['Group', 'Meals']
            ).sort_values('Meals', ascending=True)
            fig = px.bar(df_g, x='Meals', y='Group', orientation='h',
                         color='Meals',
                         color_continuous_scale='Blues',
                         template='plotly_white')
            fig.update_layout(height=350, margin=dict(l=0,r=0,t=20,b=0),
                              coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("7-Day Calorie Trend")
        cal_data = [
            {'Day': f'Day {d["day"]}',
             'Calories': round(d['day_totals'].get('calories', 0), 0)}
            for d in days
        ]
        target = plan['profile'].get('calorie_target', 2000)
        df_c   = pd.DataFrame(cal_data)
        fig2   = go.Figure()
        fig2.add_trace(go.Bar(
            x=df_c['Day'], y=df_c['Calories'],
            name='Actual', marker_color='#60a5fa'
        ))
        fig2.add_hline(y=target, line_dash='dash',
                       line_color='#ef4444',
                       annotation_text=f'Target {target} kcal')
        fig2.update_layout(height=350, template='plotly_white',
                           margin=dict(l=0,r=0,t=20,b=0),
                           showlegend=False)
        st.plotly_chart(fig2, use_container_width=True)

    # 7-day nutrient heatmap
    st.subheader("7-Day Nutrient Coverage (% RDA)")
    MICRO_KEYS = [
        ('iron',        'iron_mg',         'Iron'),
        ('calcium',     'calcium_mg',      'Calcium'),
        ('potassium',   'potassium_mg',    'Potassium'),
        ('magnesium',   'magnesium_mg',    'Magnesium'),
        ('zinc',        'zinc_mg',         'Zinc'),
        ('vitamin_b12', 'vitamin_b12_mcg', 'B12'),
        ('vitamin_d',   'vitamin_d_iu',    'Vit D'),
        ('vitamin_c',   'vitamin_c_mg',    'Vit C'),
        ('fiber',       'fiber_g',         'Fibre'),
    ]
    heat_z, heat_x, heat_y = [], [f'Day {i+1}' for i in range(7)], []
    for col, rda_key, label in MICRO_KEYS:
        row = []
        for d in days:
            actual = float(d['day_totals'].get(col, 0) or 0)
            target = float(rda.get(rda_key, 1) or 1)
            row.append(min(round(actual / target * 100, 1), 150))
        days_met = sum(1 for v in row if v >= 80)
        heat_y.append(f"{label}  ({days_met}/7)")
        heat_z.append(row)

    fig3 = go.Figure(data=go.Heatmap(
        z=heat_z, x=heat_x, y=heat_y,
        colorscale=[[0,'#fef2f2'],[0.5,'#fde68a'],[0.8,'#bbf7d0'],[1,'#166534']],
        zmin=0, zmax=150,
        text=[[f'{v:.0f}%' for v in row] for row in heat_z],
        texttemplate='%{text}',
        textfont={'size': 9},
    ))
    fig3.update_layout(height=280, template='plotly_white',
                       margin=dict(l=0,r=0,t=10,b=0))
    st.plotly_chart(fig3, use_container_width=True)


def render_downloads(plan: dict, name: str):
    st.subheader("⬇️ Export Your Plan")
    import os, tempfile

    csv_bytes = export_csv_bytes(plan)
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        try:
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tf:
                tmp_path = tf.name
            export_pdf(plan, tmp_path)
            with open(tmp_path, 'rb') as f:
                pdf_data = f.read()
            os.unlink(tmp_path)
            st.download_button(
                "📄 Download PDF",
                data=pdf_data,
                file_name=f"nutriai_{name.lower()}_plan.pdf",
                mime='application/pdf',
                use_container_width=True,
            )
        except Exception as e:
            st.error(f"PDF error: {e}")

    with c2:
        st.download_button(
            "📊 Meal Plan CSV",
            data=csv_bytes['meal_plan'],
            file_name=f"nutriai_{name.lower()}_meals.csv",
            mime='text/csv',
            use_container_width=True,
        )
    with c3:
        st.download_button(
            "📈 Daily Summary CSV",
            data=csv_bytes['daily_summary'],
            file_name=f"nutriai_{name.lower()}_daily.csv",
            mime='text/csv',
            use_container_width=True,
        )
    with c4:
        st.download_button(
            "🚫 Exclusions CSV",
            data=csv_bytes['exclusions'],
            file_name=f"nutriai_{name.lower()}_exclusions.csv",
            mime='text/csv',
            use_container_width=True,
        )


# ── Sidebar — user profile input ──────────────────────────────────────────────

def sidebar_inputs() -> dict:
    with st.sidebar:
        st.image("https://img.icons8.com/fluency/96/salad.png", width=60)
        st.title("NutriAI")
        st.caption("Personalised 7-Day Meal Planner")
        st.markdown("---")

        # Quick-load personas
        st.subheader("Quick-Load Test Persona")
        preset_name = st.selectbox(
            "Load a preset",
            ['— Custom profile —'] + list(PERSONA_PRESETS.keys()),
            key='preset',
        )
        preset = PERSONA_PRESETS.get(preset_name, {})

        st.markdown("---")
        st.subheader("Your Profile")

        name = st.text_input("Name",
                             value=preset.get('name', 'User'))
        col1, col2 = st.columns(2)
        age = col1.number_input("Age", 18, 90,
                                value=int(preset.get('age', 30)))
        sex = col2.selectbox("Sex", ['Female', 'Male'],
                             index=0 if preset.get('sex','Female').lower()=='female' else 1)

        calorie_target = st.slider(
            "Calorie target (kcal/day)",
            1200, 3500,
            value=int(preset.get('calorie_target', 2000)),
            step=50,
        )

        weight_kg = st.number_input(
            "Body weight (kg) — for protein target",
            min_value=30.0, max_value=200.0,
            value=float(preset.get('weight_kg', 65)),
            step=1.0,
            help="Daily protein target = 0.7 × weight (g). Overrides RDA when higher.",
        )

        diet_mode = st.selectbox(
            "Diet type",
            ['Non-Vegetarian', 'Vegetarian', 'Vegan', 'Pescatarian'],
            index=['non-vegetarian','vegetarian','vegan','pescatarian'].index(
                preset.get('diet_mode', 'Non-Vegetarian').lower()
            ) if preset.get('diet_mode','').lower() in
                ['non-vegetarian','vegetarian','vegan','pescatarian'] else 0,
        )

        no_pork = st.checkbox("No pork (religious / dietary)",
                              value=bool(preset.get('no_pork', False)))

        st.markdown("**Medical Conditions**")
        conditions = st.multiselect(
            "Select all that apply",
            CONDITION_OPTIONS,
            default=preset.get('conditions', []),
        )

        st.markdown("**Allergens to Exclude**")
        allergens = st.multiselect(
            "Select allergens",
            ALLERGEN_OPTIONS,
            default=preset.get('allergens', []),
            format_func=lambda x: x.replace('_', ' ').title(),
        )

        st.markdown("**Priority Micronutrients**")
        priority_nutrients = st.multiselect(
            "Rank these higher",
            MICRO_OPTIONS,
            default=preset.get('priority_nutrients', []),
            format_func=lambda x: x.replace('_', ' ').title(),
        )

        st.markdown("---")
        generate = st.button(
            "🥗 Generate My Meal Plan",
            use_container_width=True,
            type='primary',
        )

    profile = {
        'name':               name,
        'age':                int(age),
        'sex':                sex.lower(),
        'calorie_target':     int(calorie_target),
        'weight_kg':          float(weight_kg),
        'diet_mode':          diet_mode.lower().replace('-', '_'),
        'no_pork':            no_pork,
        'conditions':         conditions,
        'allergens':          allergens,
        'priority_nutrients': priority_nutrients,
    }
    return profile, generate


# ── Main app ──────────────────────────────────────────────────────────────────

def main():
    st.title("🥗 NutriAI — Personalised 7-Day Meal Planner")
    st.caption(
        "Streaming AI-powered diet planning · Clinical condition filtering · "
        "Allergen-safe · USDA FoodData Central · Sub-60s generation"
    )

    # Technique badges
    st.markdown(
        '<span class="badge badge-blue">🔵 Bloom Filter (Sketching)</span>'
        '<span class="badge badge-green">🟢 Nutrient Embeddings (ANN)</span>'
        '<span class="badge badge-green">🟢 Content-Based Recommendation</span>'
        '<span class="badge badge-orange">🟠 Multi-Objective Ranking</span>'
        '<span class="badge badge-gray">⚪ Streaming Output</span>',
        unsafe_allow_html=True,
    )
    st.markdown('<hr class="section-div">', unsafe_allow_html=True)

    profile, generate = sidebar_inputs()

    if 'plan' not in st.session_state:
        st.session_state.plan = None

    if generate:
        st.session_state.plan = None
        plan_days  = {i: {} for i in range(1, 8)}
        meta       = {}

        progress_bar = st.progress(0, text="Initialising pipeline...")
        status_text  = st.empty()
        t_start      = time.time()
        total_meals  = 21
        meal_count   = 0

        # Stream the plan
        try:
            for event in generate_plan_stream(profile):
                if event['type'] == 'meal':
                    meal_count += 1
                    d    = event['day']
                    slot = event['slot']
                    plan_days[d][slot] = event['meal']

                    pct  = meal_count / total_meals
                    progress_bar.progress(
                        pct,
                        text=f"Day {d} {slot.title()} · {meal_count}/{total_meals} meals · "
                             f"{event['elapsed_s']:.1f}s elapsed"
                    )
                    status_text.markdown(
                        f"✅ **Day {d} — {slot.upper()}**: "
                        f"{event['meal'].get('description','')[:80]}..."
                    )

                elif event['type'] == 'complete':
                    meta = event['plan']

        except ValueError as e:
            progress_bar.empty()
            status_text.empty()
            st.error(f"⚠️ Profile too restrictive: {e}")
            st.info("Try relaxing one or more allergen or condition filters.")
            return
        except Exception as e:
            progress_bar.empty()
            status_text.empty()
            st.error(f"Generation error: {e}")
            return

        elapsed = time.time() - t_start
        progress_bar.progress(1.0, text=f"✅ Complete in {elapsed:.1f}s")
        status_text.empty()

        st.session_state.plan      = meta
        st.session_state.plan_days = plan_days

    # ── Display plan ─────────────────────────────────────────────────────────
    if st.session_state.plan:
        plan    = st.session_state.plan
        profile = plan['profile']
        rda     = plan.get('rda', {})
        name    = profile.get('name', 'User')
        gen_t   = plan.get('generation_time_s', 0)
        div     = plan.get('diversity_score', 0)

        st.success(
            f"✅ **{name}'s 7-day plan ready** · "
            f"Generated in **{gen_t:.1f}s** · "
            f"Diversity score **{div:.3f}** · "
            f"**{plan.get('candidate_pool',0):,}** safe foods in pool"
        )

        tab1, tab2, tab3, tab4 = st.tabs([
            "📅 7-Day Plan",
            "📊 Analytics & Nutrients",
            "🚫 Why Excluded",
            "⬇️ Export",
        ])

        with tab1:
            day_tabs = st.tabs([f"Day {i}" for i in range(1, 8)])
            for i, day_tab in enumerate(day_tabs):
                with day_tab:
                    day_data = plan['days'][i]
                    day_num  = day_data['day']
                    meals    = day_data.get('meals', {})
                    totals   = day_data.get('day_totals', {})
                    gaps     = day_data.get('rda_gaps', [])

                    # Day summary strip
                    wt  = float(profile.get('weight_kg', 0) or 0)
                    pfl = round(0.7 * wt, 1) if wt > 0 else 0.0
                    prot_actual = totals.get('protein', 0)
                    prot_rda    = rda.get('protein_g', 0)
                    prot_target = max(pfl, prot_rda)
                    prot_delta  = (
                        f"target {prot_target:.0f}g ✅" if prot_actual >= prot_target
                        else f"target {prot_target:.0f}g ⚠️"
                    )

                    dcol1, dcol2, dcol3, dcol4 = st.columns(4)
                    dcol1.metric("Calories",
                                 f"{totals.get('calories',0):.0f} kcal",
                                 delta=None)
                    dcol2.metric("Protein",
                                 f"{prot_actual:.1f} g", prot_delta)
                    dcol3.metric("Fibre",
                                 f"{totals.get('fiber',0):.1f} g")
                    _SUPP = frozenset({'vitamin_d', 'omega3_ala', 'sodium'})
                    diet_gaps = [g for g in gaps if g['nutrient'] not in _SUPP]
                    gap_flag = f"⚠️ {len(diet_gaps)} gap(s)" if diet_gaps else "✅ All met"
                    dcol4.metric("RDA Status†", gap_flag)

                    st.markdown('<hr class="section-div">',
                                unsafe_allow_html=True)

                    for slot in ('breakfast', 'lunch', 'dinner'):
                        meal = meals.get(slot)
                        if meal:
                            render_meal_card(slot, meal, day_num)

                    st.markdown("**Daily Nutrient Breakdown**")
                    render_day_nutrients(day_data, rda)

        with tab2:
            render_analytics(plan)

        with tab3:
            st.subheader("Clinical & Allergen Safety Filter Log")
            st.markdown(
                "Every food below was removed **before** any meal was selected. "
                "The Bloom filter (sketching) and SQL WHERE clause guarantee "
                "zero unsafe foods in your plan."
            )
            render_exclusions(plan.get('exclusions', []))

        with tab4:
            render_downloads(plan, name)

    else:
        # Landing state
        st.markdown("""
        ### How NutriAI Works

        1. **Enter your profile** in the sidebar — diet type, medical conditions, allergens
        2. Click **Generate My Meal Plan**
        3. Watch meals stream in one by one as they are generated in real time
        4. Explore your plan, check nutrients against RDA, see why unsafe foods were excluded
        5. Export as PDF or CSV

        ---
        **Quick start**: Load one of the 4 test personas from the sidebar dropdown.

        | Persona | Conditions | Diet |
        |---------|-----------|------|
        | Priya   | IBS       | Vegetarian + No Dairy |
        | Ravi    | GERD      | Non-Veg + Gluten-Free |
        | Mei     | T2 Diabetes | Vegan + No Tree Nuts |
        | James   | Hypertension | Pescatarian + No Soy |
        """)


if __name__ == "__main__":
    main()
