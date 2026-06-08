"""
Region-aware multimodal LLM page reader.
Provider-agnostic: config.MODEL_PROVIDER selects claude or gemini.
Returns structured JSON per the v0.4 schema.
"""
import base64
import json
import os
import re

import config

# ── Prompt ────────────────────────────────────────────────────────────────────

_PROMPT = """\
You are reading a scanned reMarkable tablet to-do page. Return ONLY strict JSON — no prose, no markdown.

COORDINATE SYSTEM: The page is {W}×{H} points, origin top-left. Each item has row_y1/row_y2 \
giving its vertical band. The image may be scaled from these coordinates but proportions are preserved.

TEMPLATE STRUCTURE (columns, left→right on every row):
  • Number label (e.g. "1.")    — far left
  • Checkbox square             — at x≈{CHK_X}, size≈{CHK_SZ}px  ← "done" mark goes here
  • Task text                   — printed text starts at x≈{TXT_X}
  • UP arrow triangle  ▲        — at x≈{UP_X}  ← "promote" mark goes here
  • DOWN arrow triangle ▽       — at x≈{DN_X}  ← "demote" mark goes here

HOW MARKS LOOK (any of these counts as "marked"):
  • Checkbox done: tick ✓, X, check, solid fill, scribble INSIDE or OVER the square, or text struck through
  • UP/DOWN arrow marked: solid fill (blacked in), tick, X, or any ink ON or INSIDE the small triangle

KNOWN PRINTED ITEMS — match marks to these by vertical position (row_y1…row_y2):
{items_json}

REGION BOUNDS (for spatial reference):
{regions_json}

CLASSIFICATION RULES:
  from_yesterday / new_tasks rows:
    checkbox or strikethrough → done_item_ids
    UP arrow marked           → promote_item_ids  (→ Priorities)
    DOWN arrow marked         → demote_item_ids   (→ Someday)
  priorities rows:
    checkbox or strikethrough → priority_done_ids
    DOWN arrow marked         → priority_demote_ids (→ From Yesterday)
  someday rows:
    checkbox or strikethrough → sd_done_ids
    UP arrow marked           → sd_promote_ids   (→ From Yesterday)

NEW HANDWRITING:
  • Handwritten text in the New Tasks region  → new_items
  • Handwritten text in the Priorities region → priority_items
  • Done Recently strip is INERT — ignore everything in it

CONSERVATIVE RULE: Only mark an item when the ink is clearly on its checkbox or arrow. \
When unsure, add to "uncertain" with a note.

OUTPUT JSON SCHEMA (all fields required, empty arrays when nothing applies):
{schema}
"""

_SCHEMA = json.dumps({
    "done_item_ids":      ["id_string"],
    "promote_item_ids":   ["id_string"],
    "demote_item_ids":    ["id_string"],
    "sd_done_ids":        ["id_string"],
    "sd_promote_ids":     ["id_string"],
    "priority_done_ids":  ["id_string"],
    "priority_demote_ids":["id_string"],
    "new_items": [{"text": "string", "region": "new_tasks", "confidence": "high|medium|low"}],
    "priority_items": [{"text": "string", "region": "priorities", "confidence": "high|medium|low"}],
    "uncertain": [{"about": "id_string_or_region", "note": "string"}],
}, indent=2)


def _build_prompt(known_items: list, region_bounds: dict) -> str:
    from generate_template import W, H, CHK_X, CHK_SZ, TXT_X, UP_X, DN_X
    cols = region_bounds.get("columns", {})
    return _PROMPT.format(
        W=W, H=H,
        CHK_X=cols.get("checkbox_x",  CHK_X),
        CHK_SZ=cols.get("checkbox_size", CHK_SZ),
        TXT_X=cols.get("text_x",      TXT_X),
        UP_X=cols.get("up_arrow_x",   UP_X),
        DN_X=cols.get("down_arrow_x", DN_X),
        items_json=json.dumps(known_items, indent=2),
        regions_json=json.dumps(region_bounds, indent=2),
        schema=_SCHEMA,
    )


def _extract_json(text: str) -> dict:
    text = text.strip()
    # Strip ```json … ``` fences if the model added them
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


# ── Claude ────────────────────────────────────────────────────────────────────

def _read_claude(img_b64: str, prompt: str) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return _extract_json(msg.content[0].text)


# ── Gemini ────────────────────────────────────────────────────────────────────

def _read_gemini(img_b64: str, prompt: str) -> dict:
    import io
    import PIL.Image
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-1.5-pro")
    img = PIL.Image.open(io.BytesIO(base64.standard_b64decode(img_b64)))
    resp = model.generate_content([prompt, img])
    return _extract_json(resp.text)


# ── Public interface ──────────────────────────────────────────────────────────

def read_page(image_path: str, known_items: list, region_bounds: dict,
              provider: str | None = None) -> dict:
    """
    Send the page image plus context to the LLM; return the structured read result.

    known_items: list of {id, text, region} dicts from CurrentPage.items
    region_bounds: the sections dict from generate_template.region_map()
    """
    provider = provider or config.MODEL_PROVIDER
    with open(image_path, "rb") as fh:
        img_b64 = base64.standard_b64encode(fh.read()).decode()

    prompt = _build_prompt(known_items, region_bounds)

    if provider == "claude":
        return _read_claude(img_b64, prompt)
    if provider == "gemini":
        return _read_gemini(img_b64, prompt)
    raise ValueError(f"Unknown MODEL_PROVIDER: {provider!r}")
