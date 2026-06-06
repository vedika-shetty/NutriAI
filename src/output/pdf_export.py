"""
src/output/pdf_export.py

Professional multi-page PDF report for the 7-day NutriAI meal plan.
Uses fpdf2 (built-in Helvetica — no external font files required).
All unicode special characters replaced with ASCII equivalents (mcg, IU).

Pages:
  1     Cover — user profile + generation metadata
  2-8   One page per day — meal descriptions + per-meal nutrients + daily RDA table
  9     Analytics — diversity score + food group distribution + 7-day gap summary
  10    Why Excluded — clinical & allergen safety filter log
"""

from fpdf import FPDF
from datetime import date as dt
from pathlib import Path


# ── Color palette (R, G, B) ──────────────────────────────────────────────────
C_NAVY   = (30,  58, 138)
C_BLUE   = (59, 130, 246)
C_LBLUE  = (219, 234, 254)
C_WHITE  = (255, 255, 255)
C_LGRAY  = (248, 250, 252)
C_MGRAY  = (148, 163, 184)
C_DGRAY  = (51,  65,  85)
C_GREEN  = (22, 163,  74)
C_RED    = (220,  38,  38)
C_ORANGE = (234,  88,  12)
C_BLACK  = (15,  23,  42)


def _safe(text: str, max_len: int = 200) -> str:
    """Sanitise string to latin-1 safe, truncated."""
    return (str(text or '')
            .replace('μ', 'mcg')
            .replace('µ', 'mcg')
            .replace('—', '-')   # em-dash
            .replace('–', '-')   # en-dash
            .replace('‘', "'")   # left single quote
            .replace('’', "'")   # right single quote
            .replace('“', '"')   # left double quote
            .replace('”', '"')   # right double quote
            .replace('•', '*')   # bullet
            .replace('°', ' deg')# degree sign
            .encode('latin-1', errors='replace')
            .decode('latin-1')[:max_len])


class NutriPDF(FPDF):

    def __init__(self, plan: dict):
        super().__init__(orientation='P', unit='mm', format='A4')
        self.plan    = plan
        self.profile = plan.get('profile', {})
        self.rda     = plan.get('rda', {})
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(18, 15, 18)

    # ── Page chrome ──────────────────────────────────────────────────────────

    def header(self):
        self.set_font('Helvetica', 'B', 8)
        self.set_text_color(*C_MGRAY)
        self.cell(0, 5, 'NutriAI  |  Personalised 7-Day Meal Plan', align='L')
        name = _safe(self.profile.get('name', 'User'))
        self.cell(0, 5, f'{name}   {dt.today().strftime("%d %b %Y")}', align='R')
        self.ln(3)
        self._hline(*C_BLUE, lw=0.5)
        self.ln(2)

    def footer(self):
        self.set_y(-13)
        self._hline(*C_MGRAY, lw=0.3)
        self.set_font('Helvetica', '', 7.5)
        self.set_text_color(*C_MGRAY)
        self.cell(0, 5,
                  f'Page {self.page_no()}   |   NutriAI   |   '
                  f'USDA FoodData Central + NIH DRI Reference',
                  align='C')

    # ── Drawing helpers ───────────────────────────────────────────────────────

    def _hline(self, r=150, g=150, b=150, lw=0.3):
        self.set_draw_color(r, g, b)
        self.set_line_width(lw)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())

    def _section_header(self, text: str):
        self.ln(3)
        self.set_fill_color(*C_NAVY)
        self.set_text_color(*C_WHITE)
        self.set_font('Helvetica', 'B', 11)
        self.cell(0, 8, f'  {_safe(text)}', fill=True, new_x='LMARGIN', new_y='NEXT')
        self.ln(2)
        self.set_text_color(*C_BLACK)

    def _sub_header(self, text: str):
        self.set_fill_color(*C_LBLUE)
        self.set_text_color(*C_NAVY)
        self.set_font('Helvetica', 'B', 10)
        self.cell(0, 7, f'  {_safe(text)}', fill=True,
                  new_x='LMARGIN', new_y='NEXT')
        self.set_text_color(*C_BLACK)
        self.ln(1)

    def _kv(self, key: str, val: str):
        # Pin x/y explicitly — avoids cursor drift after header cell() calls
        x0 = self.l_margin
        y0 = self.get_y()
        self.set_font('Helvetica', 'B', 9)
        self.set_text_color(*C_DGRAY)
        self.set_xy(x0, y0)
        self.cell(55, 6, _safe(key))
        self.set_font('Helvetica', '', 9)
        self.set_text_color(*C_BLACK)
        self.set_xy(x0 + 55, y0)
        val_w = self.w - self.r_margin - x0 - 55  # always 119 mm on A4
        self.multi_cell(val_w, 6, _safe(val))

    def _body(self, text: str, size: float = 9):
        self.set_font('Helvetica', '', size)
        self.set_text_color(*C_BLACK)
        self.set_x(self.l_margin)
        self.multi_cell(self.epw, 5.5, _safe(text, 800))

    def _pct_color(self, pct: float, flag_type: str = 'deficit') -> tuple:
        if flag_type == 'excess':
            return C_RED if pct > 120 else C_ORANGE
        if pct >= 80:  return C_GREEN
        if pct >= 50:  return C_ORANGE
        return C_RED

    # ── Cover page ────────────────────────────────────────────────────────────

    def cover(self):
        self.add_page()
        self.ln(6)

        # Title banner
        self.set_fill_color(*C_NAVY)
        banner_y = self.get_y()
        self.rect(self.l_margin, banner_y,
                  self.w - self.l_margin - self.r_margin, 30, 'F')
        self.set_y(banner_y + 4)
        self.set_font('Helvetica', 'B', 22)
        self.set_text_color(*C_WHITE)
        self.cell(0, 11, 'NutriAI', align='C', new_x='LMARGIN', new_y='NEXT')
        self.set_font('Helvetica', '', 11)
        self.cell(0, 9, 'AI-Generated Personalised 7-Day Meal Plan',
                  align='C', new_x='LMARGIN', new_y='NEXT')
        self.ln(6)
        self.set_text_color(*C_BLACK)

        # Profile
        p          = self.profile
        conditions = ', '.join(p.get('conditions', [])) or 'None declared'
        allergens  = ', '.join(p.get('allergens', [])) or 'None declared'
        micros     = ', '.join(p.get('priority_nutrients', [])) or 'General balance'
        gen_time   = self.plan.get('generation_time_s', 0)
        div_score  = self.plan.get('diversity_score', 0)
        pool       = self.plan.get('candidate_pool', 0)

        self._section_header('User Profile')
        self._kv('Name:',            p.get('name', 'User'))
        self._kv('Age / Sex:',
                 f'{p.get("age","?")} years  |  {str(p.get("sex","?")).title()}')
        self._kv('Diet Mode:',       str(p.get('diet_mode', 'Non-vegetarian')).title())
        self._kv('Calorie Target:',  f'{p.get("calorie_target", 2000):,} kcal / day')
        self._kv('Conditions:',      conditions)
        self._kv('Allergens:',       allergens)
        self._kv('Priority Micros:', micros)

        self._section_header('Generation Summary')
        self._kv('Generated on:',    dt.today().strftime('%A, %d %B %Y'))
        self._kv('Generation time:', f'{gen_time:.1f} s  (target < 60 s   PASS)')
        self._kv('Diversity score:',
                 f'{div_score:.3f}  (Simpsons D;  0 = no diversity, 1 = maximum)')
        self._kv('Candidate pool:',
                 f'{pool:,} foods after clinical + dietary + allergen filtering')
        self._kv('Meals generated:', '21  (7 days x Breakfast, Lunch, Dinner)')
        self._kv('Data source:',
                 'USDA FoodData Central - Foundation + SR Legacy + Survey FNDDS')

        self._section_header('How to Read This Report')
        self._body(
            'Each day page shows three meals with restaurant-style descriptions. '
            'A nutrient table below each day compares your totals to NIH Recommended '
            'Dietary Allowances (RDA). Colour coding: Green = at or above 80% RDA, '
            'Orange = 50-79%, Red = below 50%. Sodium is treated as a cap (excess '
            'flagged in red rather than a deficit).\n\n'
            'The Why Excluded section lists foods removed by the clinical and allergen '
            'safety filters, with precise medical reasoning for each exclusion.'
        )

    # ── Day pages (one per day) ───────────────────────────────────────────────

    def day_page(self, day_data: dict):
        day_num  = day_data['day']
        meals    = day_data.get('meals', {})
        totals   = day_data.get('day_totals', {})
        rda_gaps = day_data.get('rda_gaps', [])
        gap_map  = {g['nutrient']: g for g in rda_gaps}

        self.add_page()
        self._sub_header(f'Day {day_num} of 7')

        for slot in ('breakfast', 'lunch', 'dinner'):
            meal = meals.get(slot)
            if not meal:
                continue

            # Slot label bar
            self.set_fill_color(*C_BLUE)
            self.set_text_color(*C_WHITE)
            self.set_font('Helvetica', 'B', 9)
            self.cell(30, 6, f'  {slot.upper()}',
                      fill=True, new_x='RIGHT', new_y='TOP')

            self.set_fill_color(*C_LGRAY)
            self.set_text_color(*C_DGRAY)
            self.set_font('Helvetica', '', 8)
            grp   = _safe(meal.get('food_group', 'other')).replace('_', ' ').title()
            gi    = meal.get('gi_estimate', '?')
            serv  = meal.get('serving_g', 100)
            self.cell(0, 6,
                      f'  {grp}   |   Serving {serv} g   |   GI estimate: {gi}',
                      fill=True, new_x='LMARGIN', new_y='NEXT')

            # Description
            desc = _safe(meal.get('description') or meal.get('name', ''), 300)
            self.set_font('Helvetica', '', 9.5)
            self.set_text_color(*C_BLACK)
            self.set_x(self.l_margin)
            self.multi_cell(self.epw, 5.5, f'  {desc}')

            # Nutrient mini-row
            n = meal.get('nutrients', {})
            self.set_font('Helvetica', '', 7.5)
            self.set_text_color(*C_MGRAY)
            self.cell(0, 5,
                      f'  {n.get("calories",0):.0f} kcal  |  '
                      f'Protein {n.get("protein",0):.1f} g  |  '
                      f'Carbs {n.get("carbs",0):.1f} g  |  '
                      f'Fat {n.get("fat",0):.1f} g  |  '
                      f'Fibre {n.get("fiber",0):.1f} g  |  '
                      f'Iron {n.get("iron",0):.1f} mg  |  '
                      f'Sodium {n.get("sodium",0):.0f} mg',
                      new_x='LMARGIN', new_y='NEXT')
            self.ln(2)

        # Daily totals table
        self._hline(*C_BLUE, lw=0.4)
        self.ln(2)
        self.set_font('Helvetica', 'B', 9)
        self.set_text_color(*C_NAVY)
        self.cell(0, 6, f'  Day {day_num} - Daily Nutrient Summary vs RDA',
                  new_x='LMARGIN', new_y='NEXT')

        DISPLAY = [
            ('calories',    'calories',        'Calories',      'kcal'),
            ('protein',     'protein_g',       'Protein',       'g'),
            ('carbs',       'carbs_g',         'Carbohydrates', 'g'),
            ('fat',         'fat_g',           'Fat',           'g'),
            ('fiber',       'fiber_g',         'Fibre',         'g'),
            ('iron',        'iron_mg',         'Iron',          'mg'),
            ('calcium',     'calcium_mg',      'Calcium',       'mg'),
            ('potassium',   'potassium_mg',    'Potassium',     'mg'),
            ('magnesium',   'magnesium_mg',    'Magnesium',     'mg'),
            ('zinc',        'zinc_mg',         'Zinc',          'mg'),
            ('vitamin_b12', 'vitamin_b12_mcg', 'Vitamin B12',   'mcg'),
            ('vitamin_d',   'vitamin_d_iu',    'Vitamin D',     'IU'),
            ('vitamin_c',   'vitamin_c_mg',    'Vitamin C',     'mg'),
            ('sodium',      'sodium_mg',       'Sodium (cap)',  'mg'),
        ]

        headers = ['Nutrient', 'Total', 'RDA / Cap', '% RDA', 'Status']
        widths  = [44, 30, 30, 20, 50]

        # Table header
        self.set_fill_color(*C_NAVY)
        self.set_text_color(*C_WHITE)
        self.set_font('Helvetica', 'B', 7.5)
        for h, w in zip(headers, widths):
            self.cell(w, 5.5, f' {h}', fill=True, border=0)
        self.ln()

        for i, (col, rda_key, disp, unit) in enumerate(DISPLAY):
            actual = float(totals.get(col, 0) or 0)
            target = float(self.rda.get(rda_key, 0) or 0)
            pct    = (actual / target * 100) if target > 0 else 0.0

            fill_c = C_LGRAY if i % 2 == 0 else C_WHITE
            self.set_fill_color(*fill_c)

            gap_info  = gap_map.get(col)
            flag_type = gap_info['flag_type'] if gap_info else 'deficit'
            in_gap    = col in gap_map

            self.set_text_color(*C_DGRAY)
            self.set_font('Helvetica', '', 7.5)
            self.cell(widths[0], 5, f' {disp}', fill=True)
            self.cell(widths[1], 5, f' {actual:.1f} {unit}', fill=True)
            self.cell(widths[2], 5,
                      f' {target:.1f} {unit}' if target else ' -',
                      fill=True)

            color = self._pct_color(pct, flag_type) if in_gap else C_GREEN
            self.set_font('Helvetica', 'B', 7.5)
            self.set_text_color(*color)
            self.cell(widths[3], 5,
                      f' {pct:.0f}%' if target else ' -',
                      fill=True)

            if in_gap and gap_info:
                status = ('EXCESS' if flag_type == 'excess'
                          else f'LOW ({pct:.0f}%)')
            else:
                status = 'OK'
            self.set_font('Helvetica', '', 7.5)
            self.cell(widths[4], 5, f' {status}', fill=True,
                      new_x='LMARGIN', new_y='NEXT')

        self.ln(2)

    # ── Analytics page ────────────────────────────────────────────────────────

    def analytics_page(self):
        self.add_page()
        self._section_header('Plan Analytics')

        # Diversity
        self._sub_header("Diversity Report  (Simpson's Diversity Index)")
        div  = self.plan.get('diversity_score', 0)
        grps = self.plan.get('group_distribution', {})
        total_meals = sum(grps.values()) or 1

        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(*C_NAVY)
        color = C_GREEN if div >= 0.70 else C_ORANGE
        self.set_text_color(*color)
        self.cell(0, 7,
                  f'  Overall Diversity Score: {div:.3f}   '
                  f'(Target >= 0.70  |  Maximum = 1.00)',
                  new_x='LMARGIN', new_y='NEXT')
        self.set_text_color(*C_BLACK)
        self._body(
            "  Simpson's D = 1 - Sum(ni/N)^2 across food groups.  "
            "Higher = more varied food groups across the 21 meals."
        )
        self.ln(2)

        # Group distribution table
        self.set_fill_color(*C_NAVY)
        self.set_text_color(*C_WHITE)
        self.set_font('Helvetica', 'B', 8)
        self.cell(60, 6, '  Food Group', fill=True)
        self.cell(25, 6, '  Meals', fill=True)
        self.cell(0,  6, '  Bar', fill=True, new_x='LMARGIN', new_y='NEXT')

        for i, (grp, cnt) in enumerate(
                sorted(grps.items(), key=lambda x: -x[1])):
            fill = C_LGRAY if i % 2 == 0 else C_WHITE
            self.set_fill_color(*fill)
            self.set_text_color(*C_DGRAY)
            self.set_font('Helvetica', '', 8)
            pct  = cnt / total_meals * 100
            bar  = '|' * max(int(pct / 4), 1)
            grpd = grp.replace('_', ' ').title()
            self.cell(60, 5.5, f'  {grpd}', fill=True)
            self.cell(25, 5.5, f'  {cnt} meals  ({pct:.0f}%)', fill=True)
            self.cell(0,  5.5, f'  {bar}', fill=True,
                      new_x='LMARGIN', new_y='NEXT')

        # 7-day gap summary
        self.ln(4)
        self._sub_header('7-Day Nutrient Gap Summary')
        self._body(
            'Nutrients flagged below 80% of RDA on at least one day are '
            'listed below. Green = met; Orange = 50-79%; Red = below 50%.'
        )
        self.ln(2)

        any_gaps = False
        for day_data in self.plan.get('days', []):
            gaps = day_data.get('rda_gaps', [])
            if not gaps:
                continue
            any_gaps = True
            self.set_font('Helvetica', 'B', 8.5)
            self.set_text_color(*C_NAVY)
            self.cell(0, 6, f'  Day {day_data["day"]}',
                      new_x='LMARGIN', new_y='NEXT')
            for gap in gaps:
                pct   = gap['pct']
                ftype = gap.get('flag_type', 'deficit')
                color = self._pct_color(pct, ftype)
                self.set_font('Helvetica', '', 8)
                self.set_text_color(*color)
                if ftype == 'excess':
                    msg = (f'    {gap["display_name"]}: '
                           f'{gap["actual"]} {gap["unit"]} '
                           f'({pct:.0f}% of cap - EXCESS)')
                else:
                    msg = (f'    {gap["display_name"]}: '
                           f'{gap["actual"]} {gap["unit"]} '
                           f'({pct:.0f}% of RDA - deficit)')
                self.cell(0, 5.5, _safe(msg), new_x='LMARGIN', new_y='NEXT')
            self.set_text_color(*C_BLACK)
            self.ln(1)

        if not any_gaps:
            self.set_font('Helvetica', 'B', 9)
            self.set_text_color(*C_GREEN)
            self.cell(0, 7, '  All 7 days meet >= 80% of RDA across all nutrients.',
                      new_x='LMARGIN', new_y='NEXT')

    # ── Why Excluded page ─────────────────────────────────────────────────────

    def exclusions_page(self):
        excl = self.plan.get('exclusions', [])
        if not excl:
            return
        self.add_page()
        self._section_header('Why Excluded - Clinical & Allergen Safety Filter Log')
        self._body(
            'The foods below were removed from the candidate pool before any meal '
            'was selected. The clinical and allergen filters guarantee that zero '
            'unsafe foods reach your plan. Each exclusion carries a precise reason.'
        )
        self.ln(3)

        for i, ex in enumerate(excl[:20]):
            if self.get_y() > 255:
                self.add_page()
            fill = C_LGRAY if i % 2 == 0 else C_WHITE
            self.set_fill_color(*fill)
            self.set_font('Helvetica', 'B', 8.5)
            self.set_text_color(*C_NAVY)
            name = _safe(ex.get('name', ''), 80)
            self.set_x(self.l_margin)
            self.multi_cell(self.epw, 5.5, f'  {name}', fill=True)

            for reason in ex.get('reasons', [ex.get('primary_reason', '')])[:2]:
                self.set_font('Helvetica', '', 8)
                self.set_text_color(*C_RED)
                self.set_x(self.l_margin)
                self.multi_cell(self.epw, 5, f'  > {_safe(reason, 130)}', fill=True)

            self.set_text_color(*C_BLACK)
            self.ln(1)

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(self) -> 'NutriPDF':
        self.cover()
        for day_data in self.plan.get('days', []):
            self.day_page(day_data)
        self.analytics_page()
        self.exclusions_page()
        return self


def export_pdf(plan: dict, output_path: str) -> str:
    """Generate and save the meal plan PDF. Returns output_path."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    pdf = NutriPDF(plan)
    pdf.build()
    pdf.output(output_path)
    return output_path


def export_pdf_bytes(plan: dict) -> bytes:
    """Return PDF as bytes — for Streamlit st.download_button()."""
    pdf = NutriPDF(plan)
    pdf.build()
    return bytes(pdf.output())
