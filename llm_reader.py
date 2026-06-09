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
You are reading a reMarkable tablet to-do page. Return ONLY strict JSON — no prose, no markdown.

INTERACTION MODEL (v3 — read carefully):
  • DONE:    user drew a line THROUGH the printed task text (strikethrough).
  • PROMOTE: user wrote one or more item numbers in the "★ → Priority" action zone.
  • DEMOTE:  user wrote one or more item numbers in the "⇓ → Someday" action zone.
  • NEW TASK: user wrote new text on a blank ruled line in the "New tasks" section.
  There are NO checkboxes or arrow buttons to look for. Marks are strikethroughs and written numbers only.

ITEM NUMBERING:
  • From Yesterday items:  printed as  1.  2.  3.  etc.
  • New Tasks rows:        continue numbering from From Yesterday (e.g. 6. 7. 8. …)
  • Someday items:         printed as  S1.  S2.  S3.
  • Priority items:        printed as  P1.  P2.  P3.  P4.  P5.

ACTION ZONE (near the bottom, amber/yellow background box):
  Row 1 — "★ → Priority":  any numbers written here = those items move to Priority.
  Row 2 — "⇓ → Someday":   any numbers written here = those items move to Someday.

KNOWN PRINTED ITEMS:
{items_json}

REGION BOUNDS (for spatial reference):
{regions_json}

INSTRUCTIONS:
1. Scan every printed task row. If user drew a line through the text → add to done list.
2. Read any handwritten numbers in the "★ → Priority" zone → promote_item_ids / sd_promote_ids.
3. Read any handwritten numbers in the "⇓ → Someday" zone → demote_item_ids / priority_demote_ids.
4. Transcribe any new handwritten text on blank New Tasks lines → new_items.
5. Transcribe any new handwritten text added to Priorities blank rows → priority_items.
6. Done Recently strip is INERT — ignore it entirely.
7. Conservative rule: only act when you are confident. Uncertain → add to "uncertain".

MATCHING NUMBERS TO ITEMS:
  • A plain number (1, 2, 3…) in an action zone refers to a From Yesterday or New Tasks item
    with that display_index.
  • "S1", "S2", "S3" refers to Someday items.
  • "P1"–"P5" refers to Priority items.

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
    return _PROMPT.format(
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

_ACTION_ZONE_PROMPT = """\
This image shows the handwriting a user made inside two rows of a to-do page action zone.

Row labeled "★ Priority:" — numbers written here mean PROMOTE those items to Priority.
Row labeled "⇓ Someday:"  — numbers written here mean DEMOTE those items to Someday.

Item numbering:
  • Plain numbers (1, 2, 3…) refer to "From Yesterday" / New Tasks items.
  • S1, S2, S3  refer to Someday items.
  • P1–P5       refer to Priority items.

Known items on the page:
{items_json}

Return ONLY strict JSON — no prose, no markdown fences:
{{
  "promote_numbers": ["1", "3", "S2"],
  "demote_numbers":  ["P1", "2"]
}}
Empty arrays if you see nothing written (or can't read it confidently).
"""


def read_action_zone(png_bytes: bytes, known_items: list,
                     provider: str | None = None) -> dict:
    """
    Send the action-zone stroke image to the LLM and return
    {promote_numbers: [...], demote_numbers: [...]} as raw display-index strings.
    """
    provider = provider or config.MODEL_PROVIDER
    img_b64  = base64.standard_b64encode(png_bytes).decode()
    prompt   = _ACTION_ZONE_PROMPT.format(
        items_json=json.dumps(known_items, indent=2)
    )

    try:
        if provider == "claude":
            raw = _read_claude(img_b64, prompt)
        elif provider == "gemini":
            raw = _read_gemini(img_b64, prompt)
        else:
            raise ValueError(f"Unknown MODEL_PROVIDER: {provider!r}")
    except Exception as exc:
        print(f"llm_reader.read_action_zone failed: {exc}", flush=True)
        return {"promote_numbers": [], "demote_numbers": []}

    return {
        "promote_numbers": raw.get("promote_numbers", []),
        "demote_numbers":  raw.get("demote_numbers",  []),
    }


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
