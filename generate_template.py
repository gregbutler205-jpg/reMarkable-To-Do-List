#!/usr/bin/env python3
"""
reMarkable To-Do Template Generator  v3
Device: reMarkable Paper Pro — 1620 × 2160 px portrait

Interaction model (v3):
  • Cross out / strike through task text  → done
  • Write item # in "→ Priority" zone     → promote to Priority
  • Write item # in "→ Someday" zone      → demote to Someday
  • Write new text in New Tasks area      → new task added

Item numbering is GLOBAL and sequential across the page so every
number written in an action zone is unambiguous:
  Priorities  : P1 … P5
  From Yest.  : 1 … N
  New Tasks   : N+1 … N+10
  Someday     : S1 … S3
"""

from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import math, json, os

# ── Font setup ────────────────────────────────────────────────────────────────
_FONT_TASK = 'Helvetica'          # fallback if TTF not found
_FONT_TASK_BOLD = 'Helvetica-Bold'

def _load_fonts():
    global _FONT_TASK, _FONT_TASK_BOLD
    here = os.path.dirname(os.path.abspath(__file__))
    ttf  = os.path.join(here, 'fonts', 'Kalam-Regular.ttf')
    if os.path.exists(ttf):
        try:
            pdfmetrics.registerFont(TTFont('Kalam', ttf))
            _FONT_TASK      = 'Kalam'
            _FONT_TASK_BOLD = 'Kalam'   # Kalam has no bold; use regular
        except Exception as e:
            print(f"Font load warning: {e}")

_load_fonts()

# ── Page ──────────────────────────────────────────────────────────────────────
W, H = 1620, 2160

# ── Colors ────────────────────────────────────────────────────────────────────
INK         = HexColor('#111111')
ACCENT      = HexColor('#2f6fb0')
RULE_LIGHT  = HexColor('#c9ccd1')
SEC_DIV     = HexColor('#eceef1')
DONE_BG     = HexColor('#f6f7f8')
DONE_INK    = HexColor('#9aa0a6')
PRI_BG      = HexColor('#f2f7fc')
PRI_BORDER  = HexColor('#cadcf0')
ACT_BG      = HexColor('#fffbf0')
ACT_BORDER  = HexColor('#f0d080')
FTR_RULE    = HexColor('#d7dbe0')
LEGEND_INK  = HexColor('#5f6368')
PG_BORDER   = HexColor('#e2e4e8')
WHITE       = HexColor('#ffffff')

def fy(svg_y):
    """SVG top-left y  →  PDF bottom-left y."""
    return H - svg_y

# ── Drawing helpers ───────────────────────────────────────────────────────────
def hline(c, x1, y_svg, x2, color=RULE_LIGHT, lw=1.5):
    c.setStrokeColor(color); c.setLineWidth(lw)
    c.line(x1, fy(y_svg), x2, fy(y_svg))

def txt(c, s, x, y_svg, size=28, color=INK, bold=False, align='left',
        handwriting=False):
    if handwriting:
        c.setFont(_FONT_TASK, size)
    else:
        c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
    c.setFillColor(color)
    pfy = fy(y_svg)
    if align == 'right': c.drawRightString(x, pfy, s)
    else:                 c.drawString(x, pfy, s)

def checked_box(c, x, top_svg, sz=32, color=DONE_INK):
    c.setStrokeColor(color); c.setFillColor(WHITE); c.setLineWidth(2)
    c.roundRect(x, fy(top_svg+sz), sz, sz, radius=5, stroke=1, fill=1)
    p = c.beginPath()
    p.moveTo(x+sz*.24, fy(top_svg+sz*.54))
    p.lineTo(x+sz*.44, fy(top_svg+sz*.73))
    p.lineTo(x+sz*.76, fy(top_svg+sz*.28))
    c.drawPath(p, stroke=1, fill=0)

def star_shape(c, cx, cy_svg, sz=22, color=ACCENT):
    ro, ri = sz/2, sz/4.2
    pts = []
    for i in range(10):
        a = math.radians(-90 + i*36)
        r = ro if i%2==0 else ri
        pts.append((cx + r*math.cos(a), cy_svg + r*math.sin(a)))
    p = c.beginPath()
    p.moveTo(pts[0][0], fy(pts[0][1]))
    for px,py_s in pts[1:]: p.lineTo(px, fy(py_s))
    p.close()
    c.setStrokeColor(color); c.setFillColor(color); c.setLineWidth(1)
    c.drawPath(p, stroke=1, fill=1)

# ── Layout constants ──────────────────────────────────────────────────────────
ML, MR = 173, 1560

# Column anatomy — no checkbox/arrows, wider text area
NUM_X    = 148           # number right-align x
TXT_X    = 168           # text / ruled-line start x  (closer since no checkbox)
LINE_END = 1560          # text / ruled-line right edge (full width now)

# Row heights
RH_PRI  = 60
RH_FY   = 62
RH_NT   = 58
RH_SD   = 62
RH_DONE = 52
ROW_CY_OFF = 20          # offset from row top to text baseline centre

# Priorities box (fixed — never moves)
PRI_BOX_Y     = 128
PRI_BOX_H     = 370
PRI_BOTTOM    = PRI_BOX_Y + PRI_BOX_H   # 498
PRI_LABEL_Y   = 175
PRI_ROW_TOPS  = [204, 264, 324, 384, 444]

# From Yesterday anchor (fixed)
FY_LABEL_Y    = 538
FY_ROWS_START = 562

# Section gap constants
SEC_TO_DIV    = 22
DIV_TO_LABEL  = 28
LABEL_TO_ROWS = 22

# Action zone
ACT_GAP       = 28       # sd_bot → action box top
ACT_ROW_H     = 62       # height of each action row
ACT_PAD       = 16       # internal vertical padding
ACT_LBL_W    = 280       # width of the label column
ACT_BOX_ROWS  = 2        # Priority + Someday

# Done Recently
DONE_GAP        = 20
DONE_LBL_OFF    = 40
DONE_ROW_OFF    = 22
DONE_BOT_PAD    = 12

# Footer
FOOTER_H = 80


# ── Compute dynamic layout ────────────────────────────────────────────────────
def compute(n_fy: int) -> dict:
    fy_rows = [FY_ROWS_START + i*RH_FY for i in range(n_fy)]
    fy_bot  = (fy_rows[-1] + RH_FY) if fy_rows else FY_ROWS_START

    nt_div   = fy_bot + SEC_TO_DIV
    nt_lbl   = nt_div + DIV_TO_LABEL
    nt_start = nt_lbl + LABEL_TO_ROWS
    nt_rows  = [nt_start + i*RH_NT for i in range(10)]
    nt_bot   = nt_rows[-1] + RH_NT

    sd_div   = nt_bot + SEC_TO_DIV
    sd_lbl   = sd_div + DIV_TO_LABEL
    sd_start = sd_lbl + LABEL_TO_ROWS
    sd_rows  = [sd_start + i*RH_SD for i in range(3)]
    sd_bot   = sd_rows[-1] + RH_SD

    # Action zone (two rows: Priority + Someday)
    act_box_y   = sd_bot + ACT_GAP
    act_rows    = [act_box_y + ACT_PAD + i*ACT_ROW_H for i in range(ACT_BOX_ROWS)]
    act_box_bot = act_rows[-1] + ACT_ROW_H + ACT_PAD

    done_box_y  = act_box_bot + DONE_GAP
    done_lbl_y  = done_box_y + DONE_LBL_OFF
    done_row1_y = done_lbl_y + DONE_ROW_OFF
    done_row2_y = done_row1_y + RH_DONE
    done_box_bot = done_row2_y + RH_DONE + DONE_BOT_PAD

    ftr_rule = H - FOOTER_H + 14
    legend_y = H - FOOTER_H + 48

    return dict(
        fy_rows=fy_rows, fy_bot=fy_bot,
        nt_div=nt_div, nt_lbl=nt_lbl, nt_rows=nt_rows, nt_bot=nt_bot,
        sd_div=sd_div, sd_lbl=sd_lbl, sd_rows=sd_rows, sd_bot=sd_bot,
        act_box_y=act_box_y, act_rows=act_rows, act_box_bot=act_box_bot,
        done_box_y=done_box_y, done_lbl_y=done_lbl_y,
        done_row1_y=done_row1_y, done_row2_y=done_row2_y,
        done_box_bot=done_box_bot,
        ftr_rule=ftr_rule, legend_y=legend_y,
        overflows=(done_box_bot > H - FOOTER_H),
    )


# ── Page renderer ─────────────────────────────────────────────────────────────
def render_page(c, date_str, L, fy_items, sd_items=None, done_items=None,
                priority_items=None):
    sd_items       = sd_items       or []
    priority_items = priority_items or []
    done_items     = done_items     or [("", ""), ("", "")]
    n_fy = len(L['fy_rows'])

    # Page border + white fill
    c.setFillColor(WHITE); c.setStrokeColor(PG_BORDER); c.setLineWidth(2)
    c.roundRect(8, 8, W-16, H-16, radius=10, stroke=1, fill=1)

    # ── Header ────────────────────────────────────────────────────────────────
    txt(c, "To-do", ML, 82, size=40, bold=True)
    txt(c, date_str, MR, 82, size=28, color=ACCENT, align='right')
    hline(c, ML, 108, MR, color=ACCENT, lw=2)

    # ── Priorities box ────────────────────────────────────────────────────────
    c.setFillColor(PRI_BG); c.setStrokeColor(PRI_BORDER); c.setLineWidth(2)
    c.roundRect(44, fy(PRI_BOTTOM), W-88, PRI_BOX_H, radius=10, stroke=1, fill=1)
    star_shape(c, ML+13, PRI_LABEL_Y-8)
    txt(c, "   Priorities  ·  top 5", ML, PRI_LABEL_Y, size=29, color=ACCENT, bold=True)
    for i, top in enumerate(PRI_ROW_TOPS):
        cy = top + ROW_CY_OFF
        txt(c, f"P{i+1}.", NUM_X, cy+8, size=25, bold=True, align='right')
        if i < len(priority_items) and priority_items[i]:
            txt(c, priority_items[i], TXT_X, cy+8, size=28, handwriting=True)
        else:
            hline(c, TXT_X, cy+20, LINE_END)

    # ── From Yesterday ────────────────────────────────────────────────────────
    txt(c, "From yesterday", ML, FY_LABEL_Y, size=29, color=ACCENT, bold=True)
    for i, top in enumerate(L['fy_rows']):
        cy = top + ROW_CY_OFF
        txt(c, f"{i+1}.", NUM_X, cy+8, size=25, bold=True, align='right')
        if i < len(fy_items) and fy_items[i]:
            txt(c, fy_items[i], TXT_X, cy+8, size=28, handwriting=True)
        else:
            hline(c, TXT_X, cy+20, LINE_END)

    # ── New Tasks ─────────────────────────────────────────────────────────────
    hline(c, ML, L['nt_div'], MR, color=SEC_DIV)
    txt(c, "New tasks", ML, L['nt_lbl'], size=29, color=ACCENT, bold=True)
    for i, top in enumerate(L['nt_rows']):
        cy = top + ROW_CY_OFF
        n = n_fy + 1 + i
        txt(c, f"{n}.", NUM_X, cy+8, size=25, bold=True, align='right')
        hline(c, TXT_X, cy+20, LINE_END)

    # ── Someday ───────────────────────────────────────────────────────────────
    hline(c, ML, L['sd_div'], MR, color=SEC_DIV)
    txt(c, "Someday", ML, L['sd_lbl'], size=29, color=ACCENT, bold=True)
    for i, top in enumerate(L['sd_rows']):
        cy = top + ROW_CY_OFF
        txt(c, f"S{i+1}.", NUM_X, cy+8, size=25, bold=True, align='right')
        if i < len(sd_items) and sd_items[i]:
            txt(c, sd_items[i], TXT_X, cy+8, size=28, handwriting=True)
        else:
            hline(c, TXT_X, cy+20, LINE_END)

    # ── Action Zone ───────────────────────────────────────────────────────────
    act_h = L['act_box_bot'] - L['act_box_y']
    c.setFillColor(ACT_BG); c.setStrokeColor(ACT_BORDER); c.setLineWidth(1.5)
    c.roundRect(44, fy(L['act_box_bot']), W-88, act_h, radius=8, stroke=1, fill=1)

    action_rows = [
        ("★ → Priority", "write item #s to promote"),
        ("⇓ → Someday",  "write item #s to park"),
    ]
    for ri, (label, hint) in enumerate(action_rows):
        row_y = L['act_rows'][ri]
        cy    = row_y + ROW_CY_OFF
        # Label
        txt(c, label, 68, cy + 8, size=26, bold=True, color=ACCENT)
        # Write-in line
        line_x1 = 68 + ACT_LBL_W
        txt(c, hint, line_x1, cy - 6, size=19, color=RULE_LIGHT)
        hline(c, line_x1, cy + 22, LINE_END, color=RULE_LIGHT, lw=1.5)

    # ── Done Recently ─────────────────────────────────────────────────────────
    done_h = L['done_box_bot'] - L['done_box_y']
    c.setFillColor(DONE_BG); c.setLineWidth(0)
    c.roundRect(40, fy(L['done_box_bot']), W-80, done_h, radius=8, stroke=0, fill=1)
    txt(c, "Done recently", 64, L['done_lbl_y'], size=29, color=DONE_INK, bold=True)

    for ri, row_y in enumerate([L['done_row1_y'], L['done_row2_y']]):
        row_cy = row_y + 16
        left_text, right_text = done_items[ri] if ri < len(done_items) else ("", "")
        if left_text:
            checked_box(c, 64, row_y)
            txt(c, left_text, 108, row_cy, size=25, color=DONE_INK, handwriting=True)
            end_x = 108 + int(len(left_text) * 15)
            hline(c, 108, row_cy-5, min(end_x, 800), color=DONE_INK)
        if right_text:
            checked_box(c, 840, row_y)
            txt(c, right_text, 884, row_cy, size=25, color=DONE_INK, handwriting=True)
            end_x = 884 + int(len(right_text) * 15)
            hline(c, 884, row_cy-5, min(end_x, 1550), color=DONE_INK)

    # ── Footer ────────────────────────────────────────────────────────────────
    hline(c, ML, L['ftr_rule'], MR, color=FTR_RULE)
    leg = ("Cross out = done   ·   "
           "Write # in ★→Priority box = promote   ·   "
           "Write # in ⇓→Someday box = park   ·   "
           "no mark = carries forward")
    txt(c, leg, ML, L['legend_y'], size=22, color=LEGEND_INK)


# ── Region map ────────────────────────────────────────────────────────────────
def region_map(n_fy: int) -> dict:
    L = compute(n_fy)
    return {
        "_note": "v3 template. Marks: strikethrough=done, write # in action zone=move.",
        "page": {"w": W, "h": H},
        "columns": {"number_right_x": NUM_X, "text_x": TXT_X, "line_end_x": LINE_END},
        "row_heights": {"priorities": RH_PRI, "from_yesterday": RH_FY,
                        "new_tasks": RH_NT, "someday": RH_SD},
        "sections": {
            "header":        {"fixed": True, "y1": 0, "y2": 108},
            "priorities":    {"fixed": True, "y1": PRI_BOX_Y, "y2": PRI_BOTTOM,
                              "label_y": PRI_LABEL_Y,
                              "rows": [{"n": f"P{i+1}", "y1": t, "y2": t+RH_PRI}
                                       for i, t in enumerate(PRI_ROW_TOPS)],
                              "marks": {"done": "strikethrough text"}},
            "from_yesterday":{"fixed": "label+first_row fixed; bottom varies",
                              "label_y": FY_LABEL_Y, "first_row_y": FY_ROWS_START,
                              "y1": FY_LABEL_Y, "y2": L['fy_bot'],
                              "rows": [{"n": i+1, "y1": t, "y2": t+RH_FY}
                                       for i, t in enumerate(L['fy_rows'])],
                              "marks": {"done": "strikethrough text",
                                        "promote": "write number in action zone priority row",
                                        "demote":  "write number in action zone someday row"}},
            "new_tasks":     {"fixed": False, "y1": L['nt_lbl'], "y2": L['nt_bot'],
                              "rows": [{"n": n_fy+1+i, "y1": t, "y2": t+RH_NT}
                                       for i, t in enumerate(L['nt_rows'])],
                              "marks": {"done": "strikethrough text",
                                        "promote": "write number in action zone priority row",
                                        "demote":  "write number in action zone someday row",
                                        "carry":   "no mark → From yesterday tomorrow"}},
            "someday":       {"fixed": False, "y1": L['sd_lbl'], "y2": L['sd_bot'],
                              "rows": [{"n": f"S{i+1}", "y1": t, "y2": t+RH_SD}
                                       for i, t in enumerate(L['sd_rows'])],
                              "marks": {"done":    "strikethrough text",
                                        "promote": "write S# in action zone priority row"}},
            "action_zone":   {"fixed": False,
                              "y1": L['act_box_y'], "y2": L['act_box_bot'],
                              "priority_row": {"y1": L['act_rows'][0],
                                               "y2": L['act_rows'][0] + ACT_ROW_H,
                                               "label": "★ → Priority"},
                              "someday_row":  {"y1": L['act_rows'][1],
                                               "y2": L['act_rows'][1] + ACT_ROW_H,
                                               "label": "⇓ → Someday"},
                              "note": ("Numbers written here move items. "
                                       "Use item number (1,2,3…) for FY/New, "
                                       "S1/S2/S3 for Someday, P1-P5 for Priority.")},
            "done_recently": {"fixed": False, "y1": L['done_box_y'],
                              "y2": L['done_box_bot'],
                              "note": "INERT — reader must ignore entirely"},
            "footer":        {"fixed": False, "y1": L['ftr_rule'], "y2": H},
        },
    }


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    out = "/mnt/user-data/outputs"
    os.makedirs(out, exist_ok=True)

    n = 5
    L = compute(n)
    path_pdf = f"{out}/todo_template_v3.pdf"
    cv = canvas.Canvas(path_pdf, pagesize=(W, H))
    render_page(
        cv,
        date_str="Tuesday  ·  June 9, 2026",
        L=L,
        fy_items=["Make appointment with Dr. Aktar",
                  "Fix MicroFund Report",
                  "Fix floor in den",
                  "paint wall in office",
                  "Change Globe Lesson to nation information"],
        sd_items=["Call about tag - 06.10.26"],
        done_items=[("Complete To Do List project", ""), ("", "")],
        priority_items=["Call about Hydrocele appointment", "get gift bag from DG"],
    )
    cv.save()
    print(f"PDF  -> {path_pdf}")

    path_json = f"{out}/region_coordinates_v3.json"
    with open(path_json, "w") as f:
        json.dump(region_map(n), f, indent=2)
    print(f"JSON -> {path_json}")
