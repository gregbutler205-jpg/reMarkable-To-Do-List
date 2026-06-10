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

ITEM NUMBERING:
  • From Yesterday items:  printed as  1.  2.  3.  etc.
  • New Tasks rows:        continue numbering from From Yesterday (e.g. 7. 8. 9. …)
  • Someday items:         printed as  S1.  S2.  S3.
  • Priority items:        printed as  P1.  P2.  P3.  P4.  P5.

ACTION BOX (amber/yellow box in the top-right corner, next to From Yesterday tasks):
  This box has 3 rows with printed labels on the left and a write-in area on the right.
  The faint gray text "write item #s (e.g. 1, 3, P2)" is a PRINTED HINT — ignore it.
  Look only for DARK HANDWRITTEN numbers/letters in each row's write-in area.

  Row "Priority"  — handwritten numbers here = promote those items to Priority.
  Row "Someday"   — handwritten numbers here = park those items to Someday.
  Row "Completed" — handwritten numbers here = mark those items as done.

  Multiple numbers per row are fine (e.g. "3, 5, P2").

KNOWN PRINTED ITEMS:
{items_json}

KNOWN PRINTED ITEMS:
{items_json}

INSTRUCTIONS:
1. ACTION BOX (amber box, top-right):
   a. Read dark handwritten numbers/letters in the "Priority" row write-in area.
      Map each to an item using the numbering above → populate promote fields.
   b. Read dark handwritten numbers in the "Someday" row → populate demote fields.
   c. Read dark handwritten numbers in the "Completed" row → populate done fields.
   The faint gray hint text is printed — ignore it.  Only read dark pen marks.

2. NEW TASKS: transcribe any dark handwritten text on blank ruled lines in the
   "New tasks" section.  Each line of text = one new item.

3. Done Recently strip at the bottom is INERT — ignore entirely.

4. Conservative rule: if you are not confident a mark is intentional, add to "uncertain".

MATCHING numbers to task IDs:
  • plain number (1, 2, 3 …) → From Yesterday / New Tasks item with that display_index
  • "S1" "S2" "S3"           → Someday item with that display_index
  • "P1"–"P5"                → Priority item with that display_index

OUTPUT JSON (all fields required, empty arrays when nothing applies):
{schema}
"""

_SCHEMA = json.dumps({
    "done_item_ids":       ["task_id_string"],
    "promote_item_ids":    ["task_id_string"],
    "demote_item_ids":     ["task_id_string"],
    "sd_done_ids":         ["task_id_string"],
    "sd_promote_ids":      ["task_id_string"],
    "priority_done_ids":   ["task_id_string"],
    "priority_demote_ids": ["task_id_string"],
    "new_items":      [{"text": "string", "confidence": "high|medium|low"}],
    "priority_items": [{"text": "string", "confidence": "high|medium|low"}],
    "uncertain":      [{"about": "id_or_region", "note": "string"}],
}, indent=2)


def _build_prompt(known_items: list, region_bounds: dict) -> str:
    return _PROMPT.format(
        items_json=json.dumps(known_items, indent=2),
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
This image shows handwriting the user wrote inside a small action box on their to-do page.
The box has three rows:

  "Priority:"  — numbers written here mean PROMOTE those items to Priority.
  "Someday:"   — numbers written here mean DEMOTE / park those items to Someday.
  "Completed:" — numbers written here mean mark those items as DONE.

Item numbering on the page:
  • Plain numbers (1, 2, 3…) = From Yesterday or New Tasks items.
  • P1–P5 = Priority items.
  • S1–S3 = Someday items.

Known items:
{items_json}

Multiple numbers per row are fine — the user may write several (e.g. "3, 5, P2").

Return ONLY strict JSON — no prose, no markdown fences:
{{
  "promote_numbers": ["1", "S2"],
  "demote_numbers":  ["P1"],
  "done_numbers":    ["2", "3", "P2"]
}}
Use empty arrays if a row has nothing written or you cannot read it confidently.
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
        "done_numbers":    raw.get("done_numbers",    []),
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
