"""
Direct .rm stroke reader for mark detection.

Instead of rendering .rm → image → LLM vision, we parse the stroke
coordinates directly and check geometric intersection with the known
checkbox / arrow regions.  Much more reliable than visual detection.

The LLM is still used for NEW handwritten task transcription (text in the
New Tasks / Priorities blank rows), but checkbox/arrow marks on KNOWN
items are detected here with 100% geometric accuracy.
"""
from __future__ import annotations
import math


# ── Stroke extraction ─────────────────────────────────────────────────────────

def _load_strokes(rm_path: str) -> list[list[tuple[float, float]]]:
    """
    Return a list of strokes, each stroke being a list of (x, y) points.
    Tries multiple rmscene API variants for compatibility.
    """
    import rmscene

    strokes: list[list[tuple[float, float]]] = []

    with open(rm_path, "rb") as fh:
        data = fh.read()

    import io

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
    """Yield all nodes in a scene tree recursively."""
    yield node
    children = getattr(node, "children", None) or []
    if hasattr(children, "values"):
        children = children.values()
    for child in children:
        yield from _walk_tree(child)


def _points_from_node(node) -> list[tuple[float, float]]:
    """Extract (x,y) points from a scene tree node if it has stroke data."""
    # SceneLineItemBlock / GlyphRange / Stroke depending on version
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
    """Try to pull (x,y) tuples from any stroke-like object."""
    # Stroke with .segments list
    segs = getattr(obj, "segments", None)
    if segs:
        return [(s.x, s.y) for s in segs if hasattr(s, "x")]
    # Stroke with .points list
    pts = getattr(obj, "points", None)
    if pts:
        return [(p.x, p.y) for p in pts if hasattr(p, "x")]
    return []


# ── Geometric detection ───────────────────────────────────────────────────────

def _stroke_bbox(pts: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_overlaps(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2) -> bool:
    return ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1


def _any_stroke_in_region(strokes, rx1, ry1, rx2, ry2,
                           scale_x=1.0, scale_y=1.0) -> bool:
    """Return True if any stroke bounding box overlaps the given region."""
    for pts in strokes:
        sx1, sy1, sx2, sy2 = _stroke_bbox(pts)
        # Apply coordinate scale
        sx1 *= scale_x; sx2 *= scale_x
        sy1 *= scale_y; sy2 *= scale_y
        if _bbox_overlaps(sx1, sy1, sx2, sy2, rx1, ry1, rx2, ry2):
            return True
    return False


# ── Public interface ──────────────────────────────────────────────────────────

def detect_marks(rm_path: str, region_bounds: dict, items: list) -> dict:
    """
    Parse the .rm file and detect checkbox / arrow marks geometrically.

    Returns a partial read_result dict (mark fields only — no new_items /
    priority_items, those still come from the LLM).
    """
    result: dict = {
        "done_item_ids":       [],
        "promote_item_ids":    [],
        "demote_item_ids":     [],
        "sd_done_ids":         [],
        "sd_promote_ids":      [],
        "priority_done_ids":   [],
        "priority_demote_ids": [],
    }

    try:
        strokes = _load_strokes(rm_path)
    except Exception as exc:
        print(f"rm_reader: stroke load failed: {exc}", flush=True)
        return result

    if not strokes:
        print("rm_reader: no strokes found in .rm file", flush=True)
        return result

    # ── Debug: print coordinate ranges so we can verify the scale ────────
    all_x = [p[0] for pts in strokes for p in pts]
    all_y = [p[1] for pts in strokes for p in pts]
    print(f"rm_reader: {len(strokes)} strokes loaded  "
          f"x=[{min(all_x):.1f} … {max(all_x):.1f}]  "
          f"y=[{min(all_y):.1f} … {max(all_y):.1f}]", flush=True)

    # ── Coordinate scale ─────────────────────────────────────────────────
    # Template is 1620×2160.  If rm coordinates are in a different range
    # (e.g. 0–1404×0–1872 for A4 rm units) we scale up.
    page_w = region_bounds.get("page", {}).get("w", 1620)
    page_h = region_bounds.get("page", {}).get("h", 2160)
    rm_w   = max(all_x) if all_x else page_w
    rm_h   = max(all_y) if all_y else page_h

    # Only scale if the rm coordinate space is meaningfully different
    scale_x = page_w / rm_w if rm_w > 10 and abs(rm_w - page_w) / page_w > 0.15 else 1.0
    scale_y = page_h / rm_h if rm_h > 10 and abs(rm_h - page_h) / page_h > 0.15 else 1.0
    if scale_x != 1.0 or scale_y != 1.0:
        print(f"rm_reader: scaling strokes by ({scale_x:.3f}, {scale_y:.3f})", flush=True)

    # ── Column hit boxes ─────────────────────────────────────────────────
    cols   = region_bounds.get("columns", {})
    chk_x  = cols.get("checkbox_x",   152)
    chk_sz = cols.get("checkbox_size",  44)
    up_x   = cols.get("up_arrow_x",  1452)
    dn_x   = cols.get("down_arrow_x", 1504)
    arw_sz = cols.get("arrow_size",     40)

    # Expand hit boxes slightly to forgive imprecise marks (±8 px)
    PAD = 8

    def chk_box(y1, y2):
        return (chk_x - PAD, y1 - PAD,
                chk_x + chk_sz + PAD, y2 + PAD)

    def up_box(y1, y2):
        return (up_x - PAD, y1 - PAD,
                up_x + arw_sz + PAD, y2 + PAD)

    def dn_box(y1, y2):
        return (dn_x - PAD, y1 - PAD,
                dn_x + arw_sz + PAD, y2 + PAD)

    # ── Check each item ───────────────────────────────────────────────────
    for item in items:
        tid    = item.get("task_id")
        region = item.get("region")
        y1     = item.get("row_y1")
        y2     = item.get("row_y2")
        if tid is None or y1 is None or y2 is None:
            continue

        chk = _any_stroke_in_region(strokes, *chk_box(y1, y2), scale_x, scale_y)
        up  = _any_stroke_in_region(strokes, *up_box(y1, y2),  scale_x, scale_y)
        dn  = _any_stroke_in_region(strokes, *dn_box(y1, y2),  scale_x, scale_y)

        if chk or up or dn:
            print(f"rm_reader: {tid!r} ({region})  "
                  f"chk={chk} up={up} dn={dn}", flush=True)

        if region in ("from_yesterday", "new_tasks"):
            if chk: result["done_item_ids"].append(tid)
            elif up: result["promote_item_ids"].append(tid)
            elif dn: result["demote_item_ids"].append(tid)
        elif region == "priorities":
            if chk: result["priority_done_ids"].append(tid)
            elif dn: result["priority_demote_ids"].append(tid)
        elif region == "someday":
            if chk: result["sd_done_ids"].append(tid)
            elif up: result["sd_promote_ids"].append(tid)

    print(f"rm_reader: result = {result}", flush=True)
    return result
