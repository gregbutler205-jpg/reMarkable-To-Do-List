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
You are reading a reMarkable tablet to-do page photo. Return ONLY strict JSON — no prose, no markdown.

KNOWN PRINTED ITEMS (these are the items the system printed; your job is to check their marks):
{items_json}

REGION BOUNDS (device pixels, origin top-left — use these to locate each item):
{regions_json}

INSTRUCTIONS:
1. For each known item, check whether the user drew a mark on it.
2. Classify by the item's region AND the mark type:
   - From Yesterday / New Tasks: checkbox ticked or text struck → done_item_ids
   - From Yesterday / New Tasks: ↑ (UP) box marked → promote_item_ids (→ Priorities)
   - From Yesterday / New Tasks: ↓ (DOWN) box marked → demote_item_ids (→ Someday)
   - Someday: checkbox ticked or text struck → sd_done_ids
   - Someday: ↑ (UP) box marked → sd_promote_ids (→ From Yesterday)
   - Priorities: checkbox ticked or text struck → priority_done_ids
   - Priorities: ↓ (DOWN) box marked → priority_demote_ids (→ From Yesterday)
3. Transcribe any HANDWRITTEN text in the New Tasks region → new_items.
4. Transcribe any HANDWRITTEN text in the Priorities region → priority_items.
5. The Done Recently strip is INERT — never include any item from it in any list.
6. Conservative rule: only apply a mark when it is CLEAR. When ambiguous, add to uncertain.

OUTPUT JSON SCHEMA (return all fields, use empty arrays when nothing applies):
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
