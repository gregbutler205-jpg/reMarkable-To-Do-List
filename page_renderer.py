"""
Page renderer — wraps generate_template.render_page() with live task data.

Takes open / priority / someday / done-recently task lists from the Sheet,
calls compute() + render_page() from the existing renderer, and returns
the PDF bytes plus the CurrentPage record to write to the Sheet.
"""
import io
import uuid
from datetime import date

from reportlab.pdfgen import canvas as rl_canvas

import config
from generate_template import H, W, compute, region_map, render_page


def build_page(
    open_tasks: list[dict],
    priority_tasks: list[dict],
    someday_tasks: list[dict],
    done_recently: list[dict],
    date_str: str | None = None,
) -> tuple[bytes, dict]:
    """
    Render the next page.

    Returns
    -------
    pdf_bytes     : bytes — the rendered PDF
    current_page  : dict  — record to store in CurrentPage tab
    """
    if date_str is None:
        date_str = date.today().strftime("%A  ·  %B %d, %Y").replace(" 0", " ")

    # From Yesterday: up to MAX_FY_ROWS items (overflow handled by spec at 8)
    fy_tasks = open_tasks[: config.MAX_FY_ROWS]
    fy_items = [t["text"] for t in fy_tasks]
    n_fy = len(fy_items)

    # Priorities: up to 5 rows
    pri_tasks = priority_tasks[:5]
    pri_items = [t["text"] for t in pri_tasks]

    # Someday: up to 3 rows
    sd_tasks = someday_tasks[:3]
    sd_items = [t["text"] for t in sd_tasks]

    # Done recently: 2 rows × 2 columns = 4 items max
    flat_done = [t["text"] for t in done_recently[:4]]
    done_pairs: list[tuple[str, str]] = []
    for i in range(0, 4, 2):
        l = flat_done[i]     if i     < len(flat_done) else ""
        r = flat_done[i + 1] if i + 1 < len(flat_done) else ""
        done_pairs.append((l, r))

    L = compute(n_fy)

    buf = io.BytesIO()
    cv = rl_canvas.Canvas(buf, pagesize=(W, H))
    render_page(
        cv,
        date_str=date_str,
        L=L,
        fy_items=fy_items,
        sd_items=sd_items,
        done_items=done_pairs,
        priority_items=pri_items,
    )
    cv.save()

    page_id = str(uuid.uuid4())[:12]
    rm = region_map(n_fy)

    # Build the items list for CurrentPage (what the LLM will be given tomorrow)
    # Include per-row y1/y2 so the LLM can precisely match marks to task IDs
    secs     = rm.get("sections", rm)   # region_map nests under "sections"
    fy_rows  = secs["from_yesterday"]["rows"]
    pri_rows = secs["priorities"]["rows"]
    sd_rows  = secs["someday"]["rows"]

    items = []
    for i, t in enumerate(fy_tasks):
        row = fy_rows[i] if i < len(fy_rows) else {}
        items.append({
            "display_index": i + 1,
            "task_id": t["id"],
            "text": t["text"],
            "region": "from_yesterday",
            "row_y1": row.get("y1"),
            "row_y2": row.get("y2"),
        })
    for i, t in enumerate(pri_tasks):
        row = pri_rows[i] if i < len(pri_rows) else {}
        items.append({
            "display_index": i + 1,
            "task_id": t["id"],
            "text": t["text"],
            "region": "priorities",
            "row_y1": row.get("y1"),
            "row_y2": row.get("y2"),
        })
    for i, t in enumerate(sd_tasks):
        row = sd_rows[i] if i < len(sd_rows) else {}
        items.append({
            "display_index": i + 1,
            "task_id": t["id"],
            "text": t["text"],
            "region": "someday",
            "row_y1": row.get("y1"),
            "row_y2": row.get("y2"),
        })

    current_page = {
        "page_id": page_id,
        "generated_date": str(date.today()),
        "processed": False,
        "page_count": 1,
        "items": items,
        "region_bounds": rm,
    }

    return buf.getvalue(), current_page
