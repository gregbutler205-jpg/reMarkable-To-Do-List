# reMarkable Carry-Forward To-Do — Spec Update v0.4
**Changes from v0.3 · Template design locked**

---

## Summary of decisions made

This document records the template design session and updates the relevant spec sections. All other sections of v0.3 remain unchanged.

---

## §5 · Data model changes (Tasks sheet)

### Someday now carries forward on the page

In v0.3, `status = someday` items lived only in the Sheet and never resurfaced on the printed page (§5, §17). **This is now reversed.**

Someday is a first-class region on the page. Items with `status = someday` are printed in the Someday region each morning and carry forward until crossed off or promoted — exactly like `status = open` items. The page now has three active printed regions:

| status   | printed where       | carries forward? |
|----------|---------------------|------------------|
| `open`   | From yesterday      | yes              |
| `someday`| Someday             | yes              |
| `done`   | Done recently strip | no               |

Removing an item from Someday now requires one of: crossing it off (→ `done`) or marking the promote arrow (→ `open`, reprints in From yesterday tomorrow).

---

## §6 · LLM page-reading contract changes

### New routing marks

The template now has three mark targets per active row (checkbox + ↑ + ↓), replacing the single → arrow. The reader output schema gains `promote_item_ids` and `demote_item_ids`, and the existing `parked_item_ids` field is retired.

**Updated required output (strict JSON, no prose):**

```json
{
  "done_item_ids":    ["id1"],
  "promote_item_ids": ["id3"],
  "demote_item_ids":  ["id5"],
  "sd_done_ids":      ["sd2"],
  "sd_promote_ids":   ["sd1"],
  "new_items": [
    { "text": "Call the dentist", "region": "new_tasks", "confidence": "high" }
  ],
  "priority_items": [
    { "text": "Email Dr. Patel", "region": "priorities", "confidence": "high" }
  ],
  "uncertain": [
    { "about": "id4", "note": "mark partially drawn, unclear ↑ or ↓" }
  ]
}
```

**Field semantics:**

| field | meaning |
|---|---|
| `done_item_ids` | active (From yesterday / New tasks) items whose checkbox is ticked or text struck through |
| `promote_item_ids` | active items whose ↑ box is marked → move to Priorities tomorrow |
| `demote_item_ids` | active items whose ↓ box is marked → move to Someday |
| `sd_done_ids` | Someday items whose checkbox is ticked or text struck → done |
| `sd_promote_ids` | Someday items whose ↑ box is marked → move to From yesterday (open) tomorrow |
| `new_items` | handwritten text detected in the New Tasks region, with region tag and confidence |
| `priority_items` | handwritten text detected in the Priorities region |
| `uncertain` | ambiguous marks; surface in summary, do not change state |

**Region-specific mark logic:**

| region | checkbox | ↑ (UP_X=1452) | ↓ (DN_X=1504) |
|---|---|---|---|
| Priorities | done | — (not present) | demote → open |
| From yesterday | done | promote → Priorities | demote → Someday |
| New tasks | done | promote → Priorities | demote → Someday |
| Someday | done | promote → open | — (not present) |
| Done recently | **IGNORE — entire region inert** | — | — |

The reader must be given region bounds from `CurrentPage` each day and must classify marks by which region the row belongs to, not just by glyph type.

---

## §17 · Resolved decide-later items

All four items from §17 of v0.3 are now resolved:

| item | resolution |
|---|---|
| Show `rollover_count` on the page? | **No** — Sheet-only for now. Can revisit if a "carried N×" note is wanted later; column for it is noted in the template margins. |
| Done-recently window | **2 days** — items completed within the last 2 days appear in the strip. |
| Fuzzy-dedupe aggressiveness | **Conservative** — only suppress exact-match duplicates. Fuzzy matching deferred until OCR accuracy is validated on real handwriting. |
| Parked items resurface automatically? | **Yes, automatically** — Someday is now a carried-forward region; un-park by ticking done or marking ↑. No manual Sheet editing required. |

---

## §18 · Template requirements (full replacement)

### Device

reMarkable Paper Pro · 1620 × 2160 px portrait · 226 DPI · color e-ink

### Coordinate system

SVG convention: origin top-left. All values in device pixels. PDF is rendered at 1:1 (1 pt = 1 device px) so PDF coordinates match device pixels.

### Column anatomy (shared by all rows)

| element | x position | size |
|---|---|---|
| Number (right-align) | x = 130 | 25 px bold |
| Checkbox | x = 152 | 44 × 44 px, rx 6 |
| Text / ruled line start | x = 224 | — |
| Text / ruled line end (active) | x = 1430 | — |
| ↑ Promote arrow | x = 1452 | 40 × 40 px |
| ↓ Demote arrow | x = 1504 | 40 × 40 px |
| Right page edge | x = 1560 | — |

### Fixed regions (identical pixel coordinates every day)

#### Header band · y 0–108
- "To-do" left (Helvetica-Bold 40 px, ink)
- Date string right (28 px, accent blue)
- Accent rule at y = 108, 2 px

#### Priorities box · y 128–498
- Tinted blue fill (#f2f7fc), accent border (#cadcf0)
- Label "★ Priorities · top 5" at y_baseline = 175 (accent bold 29 px)
- 5 rows at tops [204, 264, 324, 384, 444], RH = 60 px
- Per row: checkbox + ruled line (x 224–1430) + ↓ arrow (demote only)
- Renderer populates from `status = open` items the user has written here; blank in the template file

#### From yesterday anchor
- Section label at y_baseline = 538 (fixed regardless of item count)
- First row top at y = 560 (fixed)

### Dynamic regions (y positions shift with From Yesterday count)

All dynamic y positions are computed by `compute_layout(n_fy)` and stored in `CurrentPage.region_bounds` each day.

#### From yesterday rows
- Row height: 62 px
- Per row: number + checkbox + printed task text + ↑ + ↓
- Default (no mark): item carries forward to From yesterday tomorrow

#### New tasks · 10 rows
- Starts at: `fy_bottom + 22 + 28 + 22` below From yesterday
- Row height: 58 px
- Per row: number + checkbox + ruled blank line + ↑ + ↓
- Handwritten text OCR'd; carried to From yesterday tomorrow if no mark

#### Someday · 3 rows
- Starts at: `nt_bottom + 22 + 28 + 22` below New tasks
- Row height: 62 px
- Per row: number + checkbox + printed text (or blank line) + ↑ (promote only)
- Items carry forward in Someday until done or promoted

#### Done recently strip
- Starts at: `sd_bottom + 30` below Someday
- 2 rows × 2 items per row (4 items total); RH = 52 px
- Greyed background (#f6f7f8), greyed ink (#9aa0a6), struck text
- **INERT — the reader must ignore this region entirely**

#### Footer
- Rule at `H - 80 + 14`, legend text at `H - 80 + 48`
- Legend: "Tick or cross out = done · UP box = to Priority · DOWN box = to Someday · no mark = carried to tomorrow"

### Overflow rule

**Overflow triggers at n_fy ≥ 8** (done_recently bottom exceeds available page height).

When overflow is detected at render time:
- Page 1 prints: Priorities + From yesterday rows 1–7 + New tasks + Someday + Done recently
- Page 2 prints: "From yesterday (continued)" rows 8-N, no write-in regions, no Priorities
- Done recently moves to the last page

The renderer emits a `page_count` field in `CurrentPage` so the system knows to deliver both pages.

### Visual spec

| element | color | note |
|---|---|---|
| Task ink (all regions) | #111111 | High-contrast; reader uses geometry not hue |
| Section labels | #2f6fb0 accent | Chrome only — not task content |
| Ruled lines | #c9ccd1 light | Guides handwriting |
| Arrow box border | #c9ccd1 light | Box present even when unused — fixed column geometry |
| Priorities fill | #f2f7fc / #cadcf0 | Distinguishes priority region visually |
| Done recently fill | #f6f7f8 | Signals inert region |
| Done recently ink | #9aa0a6 | Visually suppressed |

Font: Helvetica (reportlab built-in). Task text 28 px; labels 29 px bold; numbers 25 px bold; legend 23 px.

### Deliverables produced in this session

| file | description |
|---|---|
| `todo_template_blank.pdf` | Blank template at 1620 × 2160 px for device testing |
| `region_coordinates.json` | Full coordinate map for the LLM reader; call `compute_layout(n_fy)` to get the day's actual bounds |
| `generate_template.py` | Renderer source; `render_page()` accepts live task data |

---

*Next steps: wire `render_page()` into the nightly pipeline; add `compute_layout(n_fy)` output to the `CurrentPage` Sheet tab; update the LLM prompt to include region bounds and the new routing mark schema above.*
