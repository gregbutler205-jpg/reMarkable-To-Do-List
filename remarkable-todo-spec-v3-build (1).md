# reMarkable Carry-Forward To-Do — Build Specification (v0.3)

**For:** Claude Code (build target)
**Design:** Fully-automated LLM loop, natural cross-off
**Device:** reMarkable Paper Pro (1620 × 2160 px portrait, color, Connect subscriber)
**Priority:** maximum seamlessness — the only manual surface is handwriting tasks, crossing off finished ones, and (optionally) marking an item "later."

**Changed since v0.2:** added an on-device misread safety net ("Done recently" strip), idempotent reconciliation, region-aware LLM reading, always-attach-PDF emails, a "park / someday" path, and optional dated page snapshots. A new **§18 Template requirements** consolidates everything the page template (next task) must support.

---

## 1. What you're building

A nightly automated job that keeps a single reMarkable to-do page rolling forward. During the day the user handwrites new tasks, crosses out (or ticks) finished ones, and may flag an item as "later." Overnight, the job pulls the page, a multimodal LLM reads it (which printed items are done, which are parked, what new handwriting was added), updates a Google Sheet that holds the canonical task list, renders tomorrow's page, and pushes it back to the tablet. In the morning the fresh page is already there. No email, no codes, no copying by the user.

---

## 2. Locked decisions

| Decision | Choice |
|---|---|
| Done-marking | Natural strikethrough **or** checkbox tick — no codes |
| "Later" marking | A distinct **park glyph** per item → moves the task to a `Someday` list, off the daily page |
| Misread safety net | A greyed **"Done recently"** strip on the page (last 1–2 days) so a wrongly-dropped task is re-addable with one stroke |
| Page reading | Multimodal LLM, **region-aware**, provider-swappable; default **Claude**, A/B vs **Gemini** in Phase 1 |
| Reconciliation | **Idempotent** — each page's marks are applied exactly once |
| Transport (both directions) | `rmapi` against the reMarkable cloud (unofficial, accepted) |
| Device archive | Optional **dated snapshot** of each pushed page in an archive folder; the active page stays single/rolling |
| Runtime | **GitHub Actions** scheduled cron (no server) |
| Canonical store + history | **Google Sheet** (also the user-facing history view; user may edit/add rows) |
| History | Stored: created date, completed date, rollover count, status. **No nudges.** |
| Trigger | Scheduled nightly (≈ 3 AM America/Chicago). No manual trigger needed. |
| Safety net | **Every** run emails a summary **with the rendered page PDF attached**; manual fallback via the Sheet + manual workflow run |

---

## 3. The daily loop (core algorithm)

Runs once per scheduled cycle:

1. **Pull.** Use `rmapi` to download the current rolling to-do document from the reMarkable cloud as PDF; rasterize each page to an image.
2. **Idempotency gate.** Look up the `page_id` of the page currently on the device (from `CurrentPage`). If its marks have already been applied (`processed = true`), skip steps 3 and re-render idempotently only if needed. This prevents a retry or a manual run from double-counting.
3. **Read (LLM), region-aware.** Send the page image(s) **plus** (a) the list of items the system printed in the active region (`id` + `text`) and (b) the pixel bounds of each page region (active list / new-tasks / done-recently). Ask the model to:
   - per active item: is it **struck or ticked** (done)? is its **park glyph** marked (later)?
   - transcribe any **new handwritten lines in the new-tasks region only**, each with a confidence rating;
   - ignore the **done-recently** region entirely (it is inert).
   (The model judges mark-state on text it's been given and only truly OCRs new handwriting in a known region — this is what keeps it reliable and stops stray annotations from becoming phantom tasks.)
4. **Reconcile (write Sheet), once.**
   - Done items → `status = done`, `completed_date = run date`.
   - Parked items → `status = someday` (moves to the `Someday` view, off the daily page); not completed, not deleted.
   - New items → new `open` rows (`created_date = run date`, `rollover_count = 0`, `source = handwritten`, plus confidence). Fuzzy-dedupe against existing open items.
   - Every remaining `open` item → `rollover_count += 1`.
   - Mark the consumed page `processed = true`.
5. **Render.** Generate tomorrow's PDF (Paper Pro size) from the current `open` list, including the greyed **"Done recently"** strip (items completed in the last 1–2 days). Assign the new page a fresh `page_id`.
6. **Push + archive.** Replace the rolling document on the reMarkable cloud via `rmapi`. Optionally also write a dated copy into an `Archive/` folder. Record the new page's `id`, printed set, order, and region bounds as the new `CurrentPage` (`processed = false`).
7. **Notify + log.** Email a short summary (done / parked / new / carried counts; any low-confidence or ambiguous reads) **with the rendered page attached as a PDF**. Append a run-log row.

**Conservative mark-rule:** apply *done* or *park* only when the mark is **clear**. When ambiguous, leave the item open and flag it in the summary. Never silently drop or move a task on a maybe — the "Done recently" strip and the attached PDF are the backstops.

---

## 4. Architecture & components

```
GitHub Actions (cron)
  └─ run.py orchestrates:
       rmapi_client   → pull current page (PDF) / push next page (PDF) / archive snapshot
       pdf_to_image   → rasterize page for the model
       llm_reader     → region-aware; provider-agnostic; returns structured JSON (Claude | Gemini)
       sheet_store    → tasks + history + Someday + "current page" record (with page_id/processed)
       page_renderer  → build the Paper Pro PDF (active list + new-tasks + done-recently)
       notifier       → summary email WITH attached PDF
       logger         → run log
```

Each component is independently testable so the build phases (§13) can validate them one at a time.

---

## 5. Data model (Google Sheet)

**`Tasks`** — one row per task (canonical list + history):

| column | meaning |
|---|---|
| id | stable unique id |
| text | task text |
| status | `open` / `done` / `someday` |
| created_date | first seen |
| completed_date | when marked done (blank otherwise) |
| parked_date | when moved to someday (blank otherwise) |
| rollover_count | times carried to a new page while open |
| source | `handwritten` / `typed` (typed = user added a row by hand) |
| confidence | model's transcription confidence for handwritten items |

- The **daily page** prints `status = open` items.
- **`Someday`** is simply a filtered view of `status = someday`. Un-park by editing the row back to `open` in the Sheet.
- **Done recently** strip = `status = done` with `completed_date` within the last 1–2 days.

**`CurrentPage`** — the page now on the device, for matching + idempotency: `page_id`, `generated_date`, `processed` (bool), ordered list of `(display_index, task_id, text)`, and the **region bounds** (pixel rectangles for active / new-tasks / done-recently).

**`Log`** — per run: timestamp, counts (done/parked/new/carried), provider used, page_id processed, errors.

The Sheet **is** the history view; the user may also add a row by hand to inject a task without writing it on the tablet.

---

## 6. LLM page-reading contract (the crux)

**Input:**
- the page image(s);
- known active items `[{id, text}]` from `CurrentPage`;
- region bounds `{active: [...], new_tasks: [...], done_recently: [...]}` so the model classifies marks by *where* they are.

**Required output (strict JSON, no prose):**

```json
{
  "done_item_ids": ["id1"],
  "parked_item_ids": ["id4"],
  "new_items": [
    { "text": "Call the dentist", "confidence": "high" }
  ],
  "uncertain": [
    { "about": "id3", "note": "line only partially crossed out" }
  ]
}
```

- `done_item_ids`: active items clearly struck through or ticked.
- `parked_item_ids`: active items whose park glyph is clearly marked.
- `new_items`: handwritten lines **in the new-tasks region** not matching a known item, transcribed, rated `high|medium|low`.
- `uncertain`: anything ambiguous (including a mark that could be done-vs-park); surfaced to the summary email; does **not** itself change state.
- The model must **ignore the done-recently region** — those items are inert and must never appear in any list above.

Provider interface is identical for Claude and Gemini (same prompt + same schema) so the winner is a one-line config switch (`MODEL_PROVIDER`). Phase 1 evaluates both on the user's real handwriting; pick the more accurate one. Both are capable — the deciding factor is legibility of the actual pages, which is why this is tested, not assumed.

---

## 7. Page template (Paper Pro) — overview

Detailed requirements are in **§18** (input to the template task). At a glance, the page has four fixed regions: a **header** (weekday + date), an **active list** (printed open items, each with a done target and a park glyph), a **new-tasks** handwriting area, and a greyed **done-recently** strip. Footer micro-legend explains the two marks. Overflow continues onto a second page.

---

## 8. Transport — `rmapi`

- **Auth:** one-time device pairing — generate a code at the reMarkable account site, run `rmapi` init locally once, store the resulting token as a GitHub secret.
- **Pull:** download the rolling document as PDF.
- **Push:** replace the rolling document so the device shows the new page. Implementation detail to confirm against `rmapi`'s current capabilities: update the existing document if supported, otherwise delete-then-upload a fresh PDF under a fixed name (e.g. `To-Do`).
- **Archive (optional):** also upload a dated copy (e.g. `Archive/2026-06-06`) so the device keeps a daily record without cluttering the active page.
- **Risk:** unofficial API; can break on a reMarkable sync-protocol change and need a tool update or re-pair. The always-attached PDF (below) means a push failure still leaves you a page to import by hand.

---

## 9. Runtime — GitHub Actions

- **Schedule:** cron is UTC. `0 9 * * *` ≈ 3 AM CST / 4 AM CDT. The 1-hour DST drift is harmless. Make the time configurable.
- **Also enable `workflow_dispatch`** for on-demand runs (manual fallback + testing). The idempotency gate (§3.2) makes extra runs safe.
- **Job shape:** check out repo → install `rmapi` binary + Python deps → run `run.py` → state lives in the Sheet (nothing to commit back; optionally cache the `rmapi` binary).
- **Secrets:** see §14.

---

## 10. Notifications & logging

- **Every-run summary email, PDF attached:** counts (done / parked / new / carried), the newly added items as transcribed (so a misread is caught at a glance), anything `uncertain` or low-confidence, **and the rendered next-page PDF as an attachment** — so even if the push fails, you always have today's page to import manually.
- **Alert on failure:** if `rmapi` auth fails, the LLM errors, or the Sheet is unreachable, send an alert email and **do not mutate state**.
- **Log tab:** append a row each run for an audit trail.

---

## 11. Edge cases & rules

- **Idempotency:** marks from a given `page_id` are applied exactly once (§3.2 / `processed` flag). Retries and manual runs cannot double-count.
- **No changes:** if the model reports nothing done/parked/new, the new page equals the old one — skip the push (skip-if-identical) to avoid replacing a page the user may still be writing on.
- **Ambiguous mark:** keep the item open, flag `uncertain` (conservative mark-rule, §3).
- **Done-vs-park confusion:** if a mark could be either, treat as `uncertain` (no state change) and surface it — never guess between completing and parking.
- **Duplicate new items:** fuzzy-match new transcriptions against existing open tasks; skip near-duplicates.
- **Manual Sheet edits:** rows the user adds/edits/un-parks by hand are authoritative and flow onto the next page like any other open task.
- **First run / bootstrap:** seed empty `Tasks`; push a blank starter page; set `CurrentPage.processed = false`.
- **Ordering:** open tasks ordered oldest-`created_date` first (long-lived items stay near the top); newly added items appended.

---

## 12. Failure handling & manual fallback

If the automated loop is down for any reason: the user edits the Sheet directly (mark done / park / add tasks), then triggers the GitHub Action manually (`workflow_dispatch`) to re-render and push. If `rmapi` itself is the failure, the always-attached PDF from the last email can be imported via the reMarkable app. State is never destroyed on error.

---

## 13. Build phases (ordered — with acceptance checks)

**Phase 0 — Render + push.** Generate a page from a hand-seeded Sheet (including a sample done-recently strip and park glyphs) and push it via `rmapi`.
*Done when:* a correctly laid-out page appears on the Paper Pro and reads cleanly at e-ink size; regions are visually unmistakable.

**Phase 1 — Pull + read (A/B the model).** Pull the page, rasterize, send to the LLM with region bounds, print the JSON. Run real pages through **both Claude and Gemini**; compare done-detection, park-detection, and handwriting transcription.
*Done when:* JSON reliably reflects a real page (done vs park vs new correctly classified) and a default provider is chosen.

**Phase 2 — Reconcile (idempotent).** Wire JSON to Sheet updates (done / park / new / rollover) with the conservative rule, fuzzy-dedupe, and the `processed` gate.
*Done when:* a day's marks update the Sheet correctly, and re-running the same page changes nothing.

**Phase 3 — Schedule + notify.** Put it on the GitHub Actions cron; add the summary email **with attached PDF**, run log, and optional archive snapshot.
*Done when:* an unattended nightly run produces tomorrow's page, the email arrives with the PDF, and the archive copy lands.

**Phase 4 — Polish.** Overflow pagination, low-confidence flagging, failure alerts, skip-if-identical, done-recently window tuning.
*Done when:* the loop has run unattended for a week without manual help.

---

## 14. Secrets & one-time setup checklist

GitHub Actions secrets:
- `RMAPI_CONFIG` (or device token) — from a one-time `rmapi` pairing.
- `ANTHROPIC_API_KEY` and `GEMINI_API_KEY` (both, for the A/B).
- `GOOGLE_SERVICE_ACCOUNT_JSON` — a service account; **share the Sheet with its email**.
- `SHEET_ID`.
- Email creds — e.g. `SMTP_USER` / `SMTP_PASS` (Gmail app password) + recipient.

One-time human setup: create the Sheet and share it with the service account; pair `rmapi` and store the token; push a blank starter page (Phase 0); set the secrets; enable the workflow.

---

## 15. Suggested stack & repo layout

Python in the Action. Likely libraries: PDF render (`reportlab` or similar), PDF→image (`pymupdf`/`pdf2image`), Sheets (`gspread` + service account), models (`anthropic`, `google-generativeai`), email (`smtplib` with attachment, or an email API). `rmapi` installed as a binary step. Suggested, not mandatory.

```
/.github/workflows/todo.yml      # cron + workflow_dispatch
run.py                           # orchestrator (the §3 loop)
rmapi_client.py                  # pull / push / archive
pdf_to_image.py
llm_reader.py                    # region-aware; claude.py / gemini.py behind it
sheet_store.py                   # tasks + someday + currentpage (page_id/processed)
page_renderer.py                 # active list + new-tasks + done-recently
notifier.py                      # summary + PDF attachment
config.py                        # schedule, MODEL_PROVIDER, layout constants, done-recently window
```

---

## 16. Out of scope for v1

Multiple lists/projects, sub-tasks, due dates/scheduling, reminders, priorities, collaboration, editing an existing task's wording on-device (cross out + rewrite instead). Layerable later.

---

## 17. Decide-later items

- Show `rollover_count` on the page, or keep it Sheet-only?
- Done-recently window: 1 day vs 2 days vs "since last completed batch."
- How aggressive fuzzy-dedupe should be.
- Whether parked items ever resurface automatically (default: no — Sheet-only un-park, to honor "no nudges").

---

## 18. Template requirements (input to the next task)

The template is what makes the features above actually work; it must satisfy all of the following.

**Fixed, bounded regions (same coordinates every day):**
1. **Header band** — weekday + date; a thin colored rule is fine (chrome only).
2. **Active list** — printed open items, one per row.
3. **New-tasks region** — clearly labeled, ruled/dotted blank lines for handwriting.
4. **Done-recently strip** — greyed, visually inert, bottom of page (or last page on overflow).

Region boundaries must sit at consistent pixel coordinates so the renderer can record them in `CurrentPage` and the model can be told exactly where each region is. Consider small corner registration marks if any cropping/region extraction is used.

**Per-item marking affordances (active list):**
- A **done target** — a checkbox to tick and/or room to strike the text through.
- A **distinct park glyph** — e.g., a small right-arrow box (→) or a separate "later" column — visually unmistakable from the done target so the user and the model never confuse *complete* with *defer*.
- A light leading number/bullet per item for the user's reference (matching is by id/text, so it is not load-bearing).

**Legibility / reliability constraints:**
- **Generous row spacing** so a strikethrough can't bleed into the next item.
- **High-contrast black-on-white** for all printed task text and all mark targets. Do **not** rely on color the user must produce — they may write in any pen color; the model reads geometry and contrast, not hue.
- **Color (Paper Pro)** only for non-actionable chrome (header rule, region labels).
- **e-ink-safe** font size/weight; avoid hairlines that ghost.

**New-tasks region:**
- Enough lines for a normal day; light ruling/dots to keep handwriting straight (helps OCR); a clear "New tasks" label.

**Done-recently strip:**
- Greyed text, clearly not actionable; excluded from the model's active-item list so it is never re-scored; doubles as the misread backstop and a small sense-of-progress record.

**Footer micro-legend:**
- Explains the marks: "Strike or tick = done · Arrow = later · Write new tasks below."

**Overflow:**
- If open items exceed one page, continue the active list onto page 2; the done-recently strip moves to the last page.
