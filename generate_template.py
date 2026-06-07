#!/usr/bin/env python3
"""
reMarkable To-Do Template Generator  v2
Device: reMarkable Paper Pro — 1620 × 2160 px portrait
Coordinate origin: top-left (SVG convention). All values in device pixels.
Overflow threshold: ~8 From Yesterday items before page 2 is needed.
"""

from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
import math, json, os

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
FTR_RULE    = HexColor('#d7dbe0')
LEGEND_INK  = HexColor('#5f6368')
PG_BORDER   = HexColor('#e2e4e8')
ARROW_BOX   = HexColor('#c9ccd1')
WHITE       = HexColor('#ffffff')

def fy(svg_y):
    """SVG top-left y  →  PDF bottom-left y."""
    return H - svg_y

# ── Drawing helpers ───────────────────────────────────────────────────────────
def hline(c, x1, y_svg, x2, color=RULE_LIGHT, lw=1.5):
    c.setStrokeColor(color); c.setLineWidth(lw)
    c.line(x1, fy(y_svg), x2, fy(y_svg))

def txt(c, s, x, y_svg, size=28, color=INK, bold=False, align='left'):
    c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
    c.setFillColor(color)
    pfy = fy(y_svg)
    if align == 'right': c.drawRightString(x, pfy, s)
    else:                 c.drawString(x, pfy, s)

def checkbox(c, x, top_svg, sz=44, color=INK):
    c.setStrokeColor(color); c.setFillColor(WHITE); c.setLineWidth(2)
    c.roundRect(x, fy(top_svg+sz), sz, sz, radius=6, stroke=1, fill=1)

def checked_box(c, x, top_svg, sz=32, color=DONE_INK):
    c.setStrokeColor(color); c.setFillColor(WHITE); c.setLineWidth(2)
    c.roundRect(x, fy(top_svg+sz), sz, sz, radius=5, stroke=1, fill=1)
    p = c.beginPath()
    p.moveTo(x+sz*.24, fy(top_svg+sz*.54))
    p.lineTo(x+sz*.44, fy(top_svg+sz*.73))
    p.lineTo(x+sz*.76, fy(top_svg+sz*.28))
    c.drawPath(p, stroke=1, fill=0)

def up_arrow(c, x, top_svg, sz=40):
    c.setStrokeColor(ARROW_BOX); c.setFillColor(WHITE); c.setLineWidth(1.5)
    c.roundRect(x, fy(top_svg+sz), sz, sz, radius=4, stroke=1, fill=1)
    cx = x + sz/2
    c.setStrokeColor(INK); c.setLineWidth(2)
    p = c.beginPath()
    p.moveTo(cx, fy(top_svg+sz*.75))
    p.lineTo(cx, fy(top_svg+sz*.27))
    p.moveTo(cx-sz*.22, fy(top_svg+sz*.47))
    p.lineTo(cx,        fy(top_svg+sz*.27))
    p.lineTo(cx+sz*.22, fy(top_svg+sz*.47))
    c.drawPath(p, stroke=1, fill=0)

def dn_arrow(c, x, top_svg, sz=40):
    c.setStrokeColor(ARROW_BOX); c.setFillColor(WHITE); c.setLineWidth(1.5)
    c.roundRect(x, fy(top_svg+sz), sz, sz, radius=4, stroke=1, fill=1)
    cx = x + sz/2
    c.setStrokeColor(INK); c.setLineWidth(2)
    p = c.beginPath()
    p.moveTo(cx, fy(top_svg+sz*.25))
    p.lineTo(cx, fy(top_svg+sz*.73))
    p.moveTo(cx-sz*.22, fy(top_svg+sz*.53))
    p.lineTo(cx,        fy(top_svg+sz*.73))
    p.lineTo(cx+sz*.22, fy(top_svg+sz*.53))
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
ML, MR = 173, 1560      # left / right margin x  (ML shifted +113 px = +0.5 in)

# Column anatomy (shared by all rows)
NUM_X    = 130           # number right-align x
CHK_X    = 152           # checkbox left edge
CHK_SZ   = 44
TXT_X    = 224           # text / ruled-line start x
LINE_END = 1430          # text / ruled-line right edge
UP_X     = 1452          # ↑ arrow left edge   (promote)
DN_X     = 1504          # ↓ arrow left edge   (demote)
ARW_SZ   = 40

# Row heights
RH_PRI  = 60
RH_FY   = 62
RH_NT   = 58
RH_SD   = 62
RH_DONE = 52

# Priorities box (fixed — never moves)
PRI_BOX_Y     = 128
PRI_BOX_H     = 370
PRI_BOTTOM    = PRI_BOX_Y + PRI_BOX_H   # 498
PRI_LABEL_Y   = 175
PRI_ROW_TOPS  = [204, 264, 324, 384, 444]

# From Yesterday anchor (fixed — label and first row always here)
FY_LABEL_Y    = 538      # = PRI_BOTTOM + 40
FY_ROWS_START = 560      # = FY_LABEL_Y + 22

# Section gap constants
SEC_TO_DIV    = 22       # section bottom  → divider line
DIV_TO_LABEL  = 28       # divider         → label baseline
LABEL_TO_ROWS = 22       # label baseline  → first row top

# Done Recently
DONE_GAP        = 30     # sd_bot → done_box_y
DONE_LBL_OFF    = 40     # done_box_y → label baseline
DONE_ROW_OFF    = 22     # label → first row top
DONE_BOT_PAD    = 12     # last row bottom → done_box_bot

# Footer
FOOTER_H = 80            # reserved at page bottom


# ── Compute dynamic layout ────────────────────────────────────────────────────
def compute(n_fy: int) -> dict:
    fy_rows = [FY_ROWS_START + i*RH_FY for i in range(n_fy)]
    fy_bot  = (fy_rows[-1] + CHK_SZ) if fy_rows else FY_ROWS_START

    nt_div   = fy_bot + SEC_TO_DIV
    nt_lbl   = nt_div + DIV_TO_LABEL
    nt_start = nt_lbl + LABEL_TO_ROWS
    nt_rows  = [nt_start + i*RH_NT for i in range(10)]
    nt_bot   = nt_rows[-1] + CHK_SZ

    sd_div   = nt_bot + SEC_TO_DIV
    sd_lbl   = sd_div + DIV_TO_LABEL
    sd_start = sd_lbl + LABEL_TO_ROWS
    sd_rows  = [sd_start + i*RH_SD for i in range(3)]
    sd_bot   = sd_rows[-1] + CHK_SZ

    done_box_y  = sd_bot + DONE_GAP
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
        done_box_y=done_box_y, done_lbl_y=done_lbl_y,
        done_row1_y=done_row1_y, done_row2_y=done_row2_y,
        done_box_bot=done_box_bot,
        ftr_rule=ftr_rule, legend_y=legend_y,
        overflows=(done_box_bot > H - FOOTER_H),
    )


# ── Page renderer ─────────────────────────────────────────────────────────────
def render_page(c, date_str, L, fy_items, sd_items=None, done_items=None, priority_items=None):
    sd_items       = sd_items       or []
    priority_items = priority_items or []
    done_items     = done_items     or [
        ("Completed task", "Completed task"),
        ("Completed task", "Completed task"),
    ]
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
    txt(c, "   Priorities  \u00b7  top 5", ML, PRI_LABEL_Y, size=29, color=ACCENT, bold=True)
    for i, top in enumerate(PRI_ROW_TOPS):
        cy = top + CHK_SZ//2
        txt(c, f"{i+1}.", NUM_X, cy+8, size=25, bold=True, align='right')
        checkbox(c, CHK_X, top)
        if i < len(priority_items) and priority_items[i]:
            txt(c, priority_items[i], TXT_X, cy+8, size=28)
        else:
            hline(c, TXT_X, cy+20, LINE_END)
        dn_arrow(c, DN_X, top+2, ARW_SZ)

    # ── From Yesterday ────────────────────────────────────────────────────────
    txt(c, "From yesterday", ML, FY_LABEL_Y, size=29, color=ACCENT, bold=True)
    for i, top in enumerate(L['fy_rows']):
        cy = top + CHK_SZ//2
        txt(c, f"{i+1}.", NUM_X, cy+8, size=25, bold=True, align='right')
        checkbox(c, CHK_X, top)
        if i < len(fy_items) and fy_items[i]:
            txt(c, fy_items[i], TXT_X, cy+8, size=28)
        else:
            hline(c, TXT_X, cy+20, LINE_END)
        up_arrow(c, UP_X, top+2, ARW_SZ)
        dn_arrow(c, DN_X, top+2, ARW_SZ)

    # ── New Tasks ─────────────────────────────────────────────────────────────
    hline(c, ML, L['nt_div'], MR, color=SEC_DIV)
    txt(c, "New tasks", ML, L['nt_lbl'], size=29, color=ACCENT, bold=True)
    for i, top in enumerate(L['nt_rows']):
        cy = top + CHK_SZ//2
        n = n_fy + 1 + i
        txt(c, f"{n}.", NUM_X, cy+8, size=25, bold=True, align='right')
        checkbox(c, CHK_X, top)
        hline(c, TXT_X, cy+20, LINE_END)
        up_arrow(c, UP_X, top+2, ARW_SZ)
        dn_arrow(c, DN_X, top+2, ARW_SZ)

    # ── Someday ───────────────────────────────────────────────────────────────
    hline(c, ML, L['sd_div'], MR, color=SEC_DIV)
    txt(c, "Someday", ML, L['sd_lbl'], size=29, color=ACCENT, bold=True)
    for i, top in enumerate(L['sd_rows']):
        cy = top + CHK_SZ//2
        txt(c, f"{i+1}.", NUM_X, cy+8, size=25, bold=True, align='right')
        checkbox(c, CHK_X, top)
        if i < len(sd_items) and sd_items[i]:
            txt(c, sd_items[i], TXT_X, cy+8, size=28)
        else:
            hline(c, TXT_X, cy+20, LINE_END)
        up_arrow(c, UP_X, top+2, ARW_SZ)   # promote only; at UP_X for clarity

    # ── Done Recently ─────────────────────────────────────────────────────────
    done_h = L['done_box_bot'] - L['done_box_y']
    c.setFillColor(DONE_BG); c.setLineWidth(0)
    c.roundRect(40, fy(L['done_box_bot']), W-80, done_h, radius=8, stroke=0, fill=1)
    txt(c, "Done recently", 64, L['done_lbl_y'], size=29, color=DONE_INK, bold=True)

    for ri, row_y in enumerate([L['done_row1_y'], L['done_row2_y']]):
        row_cy = row_y + 16
        left_text, right_text = done_items[ri] if ri < len(done_items) else ("", "")
        if left_text:
            checked_box(c, CHK_X, row_y)
            txt(c, left_text, 208, row_cy, size=25, color=DONE_INK)
            end_x = 208 + int(len(left_text) * 13.4)
            hline(c, 208, row_cy-5, min(end_x, 800), color=DONE_INK)
        if right_text:
            checked_box(c, 840, row_y)
            txt(c, right_text, 896, row_cy, size=25, color=DONE_INK)
            end_x = 896 + int(len(right_text) * 13.4)
            hline(c, 896, row_cy-5, min(end_x, 1550), color=DONE_INK)

    # ── Footer ────────────────────────────────────────────────────────────────
    hline(c, ML, L['ftr_rule'], MR, color=FTR_RULE)
    leg = ("Tick or cross out = done   \u00b7   "
           "UP box = to Priority   \u00b7   "
           "DOWN box = to Someday   \u00b7   "
           "no mark = carried to tomorrow")
    txt(c, leg, ML, L['legend_y'], size=23, color=LEGEND_INK)


# ── Region map ────────────────────────────────────────────────────────────────
def region_map(n_fy: int) -> dict:
    L = compute(n_fy)
    return {
        "_note": "All coordinates in device pixels, origin top-left. "
                 "Fixed regions are identical every day. Dynamic regions shift with n_fy.",
        "page": {"w": W, "h": H},
        "columns": {
            "number_right_x": NUM_X,
            "checkbox_x": CHK_X, "checkbox_size": CHK_SZ,
            "text_x": TXT_X, "line_end_x": LINE_END,
            "up_arrow_x": UP_X, "down_arrow_x": DN_X, "arrow_size": ARW_SZ,
        },
        "row_heights": {"priorities": RH_PRI, "from_yesterday": RH_FY,
                        "new_tasks": RH_NT, "someday": RH_SD},
        "sections": {
            "header":        {"fixed": True, "y1": 0, "y2": 108,
                              "note": "Inert — date/title only"},
            "priorities":    {"fixed": True, "y1": PRI_BOX_Y, "y2": PRI_BOTTOM,
                              "label_y": PRI_LABEL_Y,
                              "rows": [{"n": i+1, "y1": t, "y2": t+RH_PRI}
                                       for i, t in enumerate(PRI_ROW_TOPS)],
                              "marks": {"done": "checkbox", "demote": "dn_arrow at DN_X"}},
            "from_yesterday":{"fixed": "label+first_row fixed; bottom varies",
                              "label_y": FY_LABEL_Y, "first_row_y": FY_ROWS_START,
                              "y1": FY_LABEL_Y, "y2": L['fy_bot'],
                              "rows": [{"n": i+1, "y1": t, "y2": t+RH_FY}
                                       for i, t in enumerate(L['fy_rows'])],
                              "marks": {
                                  "done": "checkbox (tick or strikethrough)",
                                  "promote": "up_arrow at UP_X → to Priorities",
                                  "demote":  "dn_arrow at DN_X → to Someday",
                                  "carry":   "no mark (default)"}},
            "new_tasks":     {"fixed": False, "y1": L['nt_lbl'], "y2": L['nt_bot'],
                              "rows": [{"n": n_fy+1+i, "y1": t, "y2": t+RH_NT}
                                       for i, t in enumerate(L['nt_rows'])],
                              "marks": {
                                  "done": "checkbox",
                                  "promote": "up_arrow at UP_X → to Priorities",
                                  "demote":  "dn_arrow at DN_X → to Someday",
                                  "carry":   "no mark → From yesterday tomorrow"}},
            "someday":       {"fixed": False, "y1": L['sd_lbl'], "y2": L['sd_bot'],
                              "rows": [{"n": i+1, "y1": t, "y2": t+RH_SD}
                                       for i, t in enumerate(L['sd_rows'])],
                              "marks": {
                                  "done":    "checkbox",
                                  "promote": "up_arrow at UP_X → From yesterday tomorrow"},
                              "note": "Items carry forward until done or promoted"},
            "done_recently": {"fixed": False, "y1": L['done_box_y'], "y2": L['done_box_bot'],
                              "note": "INERT — reader must ignore entirely"},
            "footer":        {"fixed": False, "y1": L['ftr_rule'], "y2": H,
                              "note": "Legend only"},
        },
        "reflow": {
            "fixed_anchors":   ["header", "priorities", "from_yesterday label"],
            "dynamic_anchors": ["new_tasks", "someday", "done_recently", "footer"],
            "overflow_at":     "n_fy >= 8 (done_recently exceeds page boundary)",
            "overflow_action":  ("Page 1: Priorities + From Yesterday (first 7) + New Tasks + "
                                 "Someday + Done Recently. "
                                 "Page 2: From Yesterday (continued). "
                                 "Done Recently moves to last page."),
        }
    }


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    out = "/mnt/user-data/outputs"
    os.makedirs(out, exist_ok=True)

    # ── Blank template PDF (5 sample FY rows, all blank) ──────────────────────
    n = 5
    L = compute(n)
    path_pdf = f"{out}/todo_template_blank.pdf"
    cv = canvas.Canvas(path_pdf, pagesize=(W, H))
    render_page(
        cv,
        date_str="Your Name  \u00b7  Date",
        L=L,
        fy_items=[""]*n,
        sd_items=[],
        done_items=[
            ("Renew car registration",  "Pay water bill"),
            ("Send invoice to Acme",    "Order printer ink"),
        ],
    )
    cv.save()
    print(f"PDF  -> {path_pdf}")

    # ── Region coordinate map (JSON) ──────────────────────────────────────────
    path_json = f"{out}/region_coordinates.json"
    with open(path_json, "w") as f:
        json.dump(region_map(n), f, indent=2)
    print(f"JSON -> {path_json}")

    # ── Overflow table ────────────────────────────────────────────────────────
    print("\nOverflow table (done_box_bot vs page limit 2080):")
    for n_fy in range(1, 13):
        Lo = compute(n_fy)
        flag = "  *** PAGE 2" if Lo['overflows'] else ""
        print(f"  n_fy={n_fy:2d}  done_box_bot={Lo['done_box_bot']:4d}{flag}")
