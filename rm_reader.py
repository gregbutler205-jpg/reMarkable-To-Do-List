"""
Direct .rm stroke reader for v3 mark detection.

v3 interaction model:
  • DONE      — user draws a roughly-horizontal line THROUGH a printed task row
                (strikethrough).  Detected geometrically; no image needed.
  • PROMOTE / DEMOTE — user writes numbers in the action-zone rows.
                Detected by finding strokes in the zone, then rendering those
                strokes to a small PIL image that llm_reader can OCR directly.
                This avoids the full-page compositing alignment problem entirely.

The LLM is still used for:
  • Reading numbers from the action-zone image  (new helper in llm_reader)
  • Transcribing new handwritten task text in New Tasks / Priorities rows
"""
from __future__ import annotations
import io
import math


# ── Stroke extraction ─────────────────────────────────────────────────────────

def _load_strokes(rm_path: str) -> list[list[tuple[float, float]]]:
    """Return a list of strokes, each stroke being a list of (x, y) points."""
    import rmscene

    strokes: list[list[tuple[float, float]]] = []
    with open(rm_path, "rb") as fh:
        data = fh.read()

    # ── API v0.5+ : read_tree ─────────────────────────────────────────────
    try:
        from rmscene import read_tree
        tree = read_tree(io.BytesIO(data))
        for node in _walk_tree(tree):
            pts = _points_from_node(node)
            if pts:
                strokes.append(pts)
        if strokes:
            return strokes
    except Exception:
        pass

    # ── API v0.3–0.4 : read_blocks ────────────────────────────────────────
    try:
        from rmscene import read_blocks
        blocks = list(read_blocks(io.BytesIO(data)))
        for block in blocks:
            pts = _points_from_block(block)
            if pts:
                strokes.append(pts)
        if strokes:
            return strokes
    except Exception:
        pass

    return strokes


def _walk_tree(node):
    yield node
    children = getattr(node, "children", None) or []
    if hasattr(children, "values"):
        children = children.values()
    for child in children:
        yield from _walk_tree(child)


def _points_from_node(node) -> list[tuple[float, float]]:
    for attr in ("value", "item"):
        obj = getattr(node, attr, None)
        if obj is not None:
            pts = _extract_pts(obj)
            if pts:
                return pts
    return _extract_pts(node)


def _points_from_block(block) -> list[tuple[float, float]]:
    for attr in ("value", "item"):
        obj = getattr(block, attr, None)
        if obj is not None:
            pts = _extract_pts(obj)
            if pts:
                return pts
    return _extract_pts(block)


def _extract_pts(obj) -> list[tuple[float, float]]:
    segs = getattr(obj, "segments", None)
    if segs:
        return [(s.x, s.y) for s in segs if hasattr(s, "x")]
    pts = getattr(obj, "points", None)
    if pts:
        return [(p.x, p.y) for p in pts if hasattr(p, "x")]
    return []


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _stroke_bbox(pts: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _centroid(pts: list[tuple[float, float]]) -> tuple[float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _compute_scale(strokes, region_bounds) -> tuple[float, float]:
    """
    Work out the scale from .rm coordinate space to PDF screen space.
    PDF screen space: 0,0 = top-left; x in [0,W]; y in [0,H] (SVG convention,
    same orientation as .rm — both have y=0 at top).

    We can NOT use max(actual_strokes) to infer the rm page size because the
    user may have only written in a small area.  Instead we use known device
    dimensions: reMarkable Paper Pro uses 1620×2160 natively; older devices
    use 1404×1872.  We pick the bucket whose ratio best fits the strokes seen.
    """
    page = region_bounds.get("page", {})
    page_w = page.get("w", 1620)
    page_h = page.get("h", 2160)

    if not strokes:
        return 1.0, 1.0

    all_x = [p[0] for pts in strokes for p in pts]
    all_y = [p[1] for pts in strokes for p in pts]
    obs_max_x = max(all_x)
    obs_max_y = max(all_y)

    # Known rm device page sizes
    KNOWN = [
        (1620, 2160),   # Paper Pro (native)
        (1404, 1872),   # reMarkable 1 / 2
    ]
    best_sx, best_sy = 1.0, 1.0
    best_err = float("inf")
    for rm_w, rm_h in KNOWN:
        sx = page_w / rm_w
        sy = page_h / rm_h
        # How well does scaling the observed strokes fit within page bounds?
        err = abs(obs_max_x * sx - page_w) + abs(obs_max_y * sy - page_h)
        if err < best_err:
            best_err = err
            best_sx, best_sy = sx, sy

    # If scales are within 2% of 1.0, don't bother scaling
    if abs(best_sx - 1.0) < 0.02 and abs(best_sy - 1.0) < 0.02:
        return 1.0, 1.0
    return best_sx, best_sy


def _scale_strokes(strokes, sx, sy) -> list[list[tuple[float, float]]]:
    return [[(x * sx, y * sy) for x, y in pts] for pts in strokes]


# ── v3 mark detection ─────────────────────────────────────────────────────────

def _is_strikethrough(scaled_strokes: list, row_y1: float, row_y2: float,
                      text_x: float, line_end: float) -> bool:
    """
    Return True if any stroke looks like a strikethrough on this row.

    Criteria:
      • Stroke centroid Y is within the row's Y band (with ±40% row-height slop)
      • Stroke X span is at least 200 px (not just a dot or tiny squiggle)
      • Stroke is roughly horizontal: Y range < 60 % of X range
      • Stroke overlaps the text area horizontally (not entirely in the margins)
    """
    row_h  = row_y2 - row_y1
    y_pad  = row_h * 0.4

    for pts in scaled_strokes:
        if len(pts) < 2:
            continue
        sx1, sy1, sx2, sy2 = _stroke_bbox(pts)
        x_span = sx2 - sx1
        y_span = sy2 - sy1

        if x_span < 200:            # too short to be a strikethrough
            continue
        if y_span > x_span * 0.6:   # too diagonal / vertical
            continue

        cx, cy = _centroid(pts)
        if not (row_y1 - y_pad <= cy <= row_y2 + y_pad):
            continue                 # not in this row's Y band

        if sx2 < text_x - 50:       # entirely left of text area
            continue

        return True
    return False


def _strokes_in_ybands(scaled_strokes, y1: float, y2: float,
                        pad: float = 10.0) -> list:
    """Return strokes whose centroid Y falls within [y1-pad, y2+pad]."""
    result = []
    for pts in scaled_strokes:
        _, cy = _centroid(pts)
        if y1 - pad <= cy <= y2 + pad:
            result.append(pts)
    return result


# ── Action-zone image rendering ───────────────────────────────────────────────

def render_action_zone_image(scaled_strokes: list, region_bounds: dict) -> bytes | None:
    """
    Render strokes inside the fixed right-side action box into a small PNG.
    Only strokes whose centroid falls within the write-in x range (past the label
    column) are rendered — this prevents the labels themselves from being misread.
    Returns None if the box has no strokes.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    ab = region_bounds.get("action_box", {})
    if not ab:
        return None

    ab_x1  = ab.get("x1",          1375)
    ab_x2  = ab.get("x2",          1602)
    wi_x1  = ab.get("write_in_x1", 1450)
    ab_y1  = ab.get("y1",           522)
    ab_y2  = ab.get("y2",           698)

    pri_row = ab.get("priority_row", {})
    sd_row  = ab.get("someday_row",  {})
    don_row = ab.get("done_row",     {})

    # Filter: centroid must be inside the box (y) and in the write-in area (x)
    def _in_wi(pts):
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        return ab_y1 <= cy <= ab_y2 and cx >= wi_x1

    wi_strokes = [pts for pts in scaled_strokes if len(pts) > 0 and _in_wi(pts)]
    if not wi_strokes:
        print("rm_reader: no strokes in action box write-in area", flush=True)
        return None

    print(f"rm_reader: {len(wi_strokes)} strokes in action box write-in area", flush=True)

    # Render: write-in area only (wi_x1 → ab_x2)
    zone_w = ab_x2 - wi_x1
    zone_h = ab_y2 - ab_y1
    RENDER_W = 500
    rsx = RENDER_W / zone_w
    rsy = rsx
    LABEL_W = 80
    img_w = RENDER_W + LABEL_W
    img_h = max(120, int(zone_h * rsy) + 20)

    img  = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(img)

    # Row dividers + labels
    for row, label in (
        (pri_row, "★ prio:"),
        (sd_row,  "⇓ park:"),
        (don_row, "✕ done:"),
    ):
        if not row:
            continue
        ry1 = row.get("y1", ab_y1)
        ry2 = row.get("y2", ab_y1 + 52)
        div_px = int((ry2 - ab_y1) * rsy) + 10
        lbl_px = int((ry1 - ab_y1) * rsy) + 14
        draw.line([(0, div_px), (img_w, div_px)], fill=(200, 200, 200), width=1)
        draw.text((4, lbl_px), label, fill=(80, 80, 80))

    # Draw strokes
    for pts in wi_strokes:
        coords = [
            (int((p[0] - wi_x1) * rsx) + LABEL_W,
             int((p[1] - ab_y1) * rsy) + 10)
            for p in pts
        ]
        coords = [(max(LABEL_W, x), max(0, min(img_h - 1, y))) for x, y in coords]
        if len(coords) >= 2:
            draw.line(coords, fill="black", width=3)
        elif coords:
            x, y = coords[0]
            draw.ellipse([x-3, y-3, x+3, y+3], fill="black")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Public interface ──────────────────────────────────────────────────────────

def detect_marks(rm_path: str, region_bounds: dict, items: list) -> dict:
    """
    Parse the .rm file and detect v3 marks geometrically.

    Returns the standard read-result mark dict plus two extra keys:
      _action_zone_png   : bytes | None  — PNG of action-zone strokes for LLM OCR
      _has_any_strokes   : bool          — True if the file had any strokes at all
    """
    result: dict = {
        "done_item_ids":       [],
        "promote_item_ids":    [],
        "demote_item_ids":     [],
        "sd_done_ids":         [],
        "sd_promote_ids":      [],
        "priority_done_ids":   [],
        "priority_demote_ids": [],
        "_action_zone_png":    None,
        "_has_any_strokes":    False,
    }

    try:
        strokes = _load_strokes(rm_path)
    except Exception as exc:
        print(f"rm_reader: stroke load failed: {exc}", flush=True)
        return result

    if not strokes:
        print("rm_reader: no strokes found in .rm file", flush=True)
        return result

    result["_has_any_strokes"] = True

    # ── Debug: coordinate ranges ──────────────────────────────────────────
    all_x = [p[0] for pts in strokes for p in pts]
    all_y = [p[1] for pts in strokes for p in pts]
    print(f"rm_reader: {len(strokes)} strokes  "
          f"x=[{min(all_x):.0f}…{max(all_x):.0f}]  "
          f"y=[{min(all_y):.0f}…{max(all_y):.0f}]", flush=True)

    # ── Scale to PDF screen coordinates ──────────────────────────────────
    sx, sy = _compute_scale(strokes, region_bounds)
    if sx != 1.0 or sy != 1.0:
        print(f"rm_reader: scaling strokes by ({sx:.3f}, {sy:.3f})", flush=True)
    scaled = _scale_strokes(strokes, sx, sy)

    # ── Text-area bounds ─────────────────────────────────────────────────
    cols     = region_bounds.get("columns", {})
    text_x   = cols.get("text_x",    108.0)
    line_end = cols.get("line_end_x", 1560.0)

    # ── Strikethrough detection for each known item ───────────────────────
    for item in items:
        tid    = item.get("task_id")
        region = item.get("region")
        y1     = item.get("row_y1")
        y2     = item.get("row_y2")
        if tid is None or y1 is None or y2 is None:
            continue

        hit = _is_strikethrough(scaled, y1, y2, text_x, line_end)
        if hit:
            print(f"rm_reader: strikethrough → {tid!r} ({region})", flush=True)
            if region in ("from_yesterday", "new_tasks"):
                result["done_item_ids"].append(tid)
            elif region == "priorities":
                result["priority_done_ids"].append(tid)
            elif region == "someday":
                result["sd_done_ids"].append(tid)

    # ── Action-box image (for LLM to read promote/demote/done numbers) ───
    az_png = render_action_zone_image(scaled, region_bounds)
    if az_png:
        result["_action_zone_png"] = az_png
        print(f"rm_reader: action box image rendered ({len(az_png)} bytes)", flush=True)
    else:
        print("rm_reader: no action-box strokes detected", flush=True)

    print(f"rm_reader: final → done={result['done_item_ids']}  "
          f"pri_done={result['priority_done_ids']}  "
          f"sd_done={result['sd_done_ids']}", flush=True)
    return result
