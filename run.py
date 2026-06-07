#!/usr/bin/env python3
"""
Nightly orchestrator — implements the §3 daily loop from the v0.3 spec,
with v0.4 mark-schema (checkbox + ↑ promote + ↓ demote) and Someday carry-forward.

Steps
-----
1. Pull current page from reMarkable cloud.
2. Idempotency gate — skip reconciliation if already processed.
3. Read page with LLM (region-aware).
4. Reconcile: update Sheet (done / promote / demote / new / rollover).
5. Render tomorrow's page.
6. Push to device + optional archive.
7. Notify (summary email with PDF attached) + log.
"""

import os
import sys
import tempfile
import traceback
from datetime import date, timedelta
from pathlib import Path

import config
import llm_reader
import notifier
import page_renderer
import pdf_to_image
import rmapi_client
from sheet_store import SheetStore


def main():
    store = SheetStore()
    errors: list[str] = []
    provider = config.MODEL_PROVIDER

    # ── 1. Pull ───────────────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            pulled_pdf = rmapi_client.pull_as_pdf(tmpdir)
        except Exception as exc:
            msg = f"rmapi pull failed: {exc}"
            errors.append(msg)
            notifier.send_alert("pull failed", msg)
            sys.exit(1)

        # ── 2. Idempotency gate ───────────────────────────────────────────────
        cp = store.get_current_page()
        if cp and cp.get("processed"):
            print("Page already processed — re-rendering idempotently and skipping reconcile.")
            _render_and_push(store, tmpdir, errors, provider, skip_email=True)
            return

        # ── 3. Read ───────────────────────────────────────────────────────────
        try:
            images = pdf_to_image.pdf_to_images(pulled_pdf)
        except Exception as exc:
            errors.append(f"pdf_to_image failed: {exc}")
            notifier.send_alert("rasterize failed", str(exc))
            sys.exit(1)

        read_result = {"done_item_ids": [], "promote_item_ids": [], "demote_item_ids": [],
                       "sd_done_ids": [], "sd_promote_ids": [],
                       "priority_done_ids": [], "priority_demote_ids": [],
                       "new_items": [], "priority_items": [], "uncertain": []}

        if cp and images:
            known_items   = cp.get("items", [])
            region_bounds = cp.get("region_bounds", {})
            try:
                read_result = llm_reader.read_page(
                    images[0], known_items, region_bounds, provider=provider
                )
            except Exception as exc:
                errors.append(f"LLM read failed: {exc}")
                notifier.send_alert("LLM read failed", traceback.format_exc())
                sys.exit(1)

        # ── 4. Reconcile ──────────────────────────────────────────────────────
        today = str(date.today())

        # Gather all printed item IDs from the current page so we know what
        # remained untouched (→ rollover).
        printed_ids: set[str] = {
            item["task_id"] for item in (cp.get("items", []) if cp else [])
        }

        done_set    = set(read_result.get("done_item_ids",      []))
        promote_set = set(read_result.get("promote_item_ids",   []))
        demote_set  = set(read_result.get("demote_item_ids",    []))
        sd_done     = set(read_result.get("sd_done_ids",        []))
        sd_promote  = set(read_result.get("sd_promote_ids",     []))
        pri_done    = set(read_result.get("priority_done_ids",  []))
        pri_demote  = set(read_result.get("priority_demote_ids",[]))

        # Apply transitions for active (From Yesterday / New Tasks) items
        for tid in done_set:
            store.update_task(tid, status=config.STATUS_DONE, completed_date=today)
        for tid in promote_set:
            store.update_task(tid, status=config.STATUS_PRIORITY)
        for tid in demote_set:
            store.update_task(tid, status=config.STATUS_SOMEDAY, parked_date=today)

        # Someday items
        for tid in sd_done:
            store.update_task(tid, status=config.STATUS_DONE, completed_date=today)
        for tid in sd_promote:
            store.update_task(tid, status=config.STATUS_OPEN)

        # Priority items
        for tid in pri_done:
            store.update_task(tid, status=config.STATUS_DONE, completed_date=today)
        for tid in pri_demote:
            store.update_task(tid, status=config.STATUS_OPEN)

        # New handwritten items (conservative exact-match dedupe)
        new_added = []
        for item in read_result.get("new_items", []):
            text = item.get("text", "").strip()
            if not text:
                continue
            if store.exact_duplicate_exists(text):
                continue
            store.add_task(text, status=config.STATUS_OPEN,
                           source="handwritten",
                           confidence=item.get("confidence", "medium"))
            new_added.append(item)

        # New priority handwritten items
        for item in read_result.get("priority_items", []):
            text = item.get("text", "").strip()
            if not text:
                continue
            if store.exact_duplicate_exists(text):
                continue
            store.add_task(text, status=config.STATUS_PRIORITY,
                           source="handwritten",
                           confidence=item.get("confidence", "medium"))

        # Rollover: every open/someday/priority item that wasn't acted on
        mutated = done_set | promote_set | demote_set | sd_done | sd_promote | pri_done | pri_demote
        carry_ids = [tid for tid in printed_ids if tid not in mutated]
        store.increment_rollover(carry_ids)

        # Mark the page consumed
        store.mark_processed()

        # ── 5–6. Render + push ────────────────────────────────────────────────
        pdf_path = _render_and_push(store, tmpdir, errors, provider, skip_email=True)

        # ── 7. Notify + log ───────────────────────────────────────────────────
        uncertain = read_result.get("uncertain", [])
        notifier.send_summary(
            pdf_path=pdf_path,
            done=len(done_set | sd_done | pri_done),
            demoted=len(demote_set),
            promoted=len(promote_set | sd_promote),
            new_items=new_added,
            carried=len(carry_ids),
            uncertain=uncertain,
            provider=provider,
            errors="; ".join(errors),
        )
        store.append_log(
            done=len(done_set | sd_done | pri_done),
            demoted=len(demote_set),
            promoted=len(promote_set | sd_promote),
            new=len(new_added),
            carried=len(carry_ids),
            provider=provider,
            page_id=cp.get("page_id", "") if cp else "",
            errors="; ".join(errors),
        )


def _render_and_push(store: SheetStore, tmpdir: str, errors: list,
                     provider: str, skip_email: bool = False) -> str | None:
    """
    Render tomorrow's page, push it to the device, archive it.
    Returns the local PDF path (for email attachment), or None on failure.
    """
    cutoff = str(date.today() - timedelta(days=config.DONE_RECENTLY_DAYS))

    open_tasks     = store.get_tasks(status=config.STATUS_OPEN)
    priority_tasks = store.get_tasks(status=config.STATUS_PRIORITY)
    someday_tasks  = store.get_tasks(status=config.STATUS_SOMEDAY)
    done_recent    = [
        t for t in store.get_tasks(status=config.STATUS_DONE)
        if (t.get("completed_date") or "") >= cutoff
    ]

    # Order: oldest created_date first (long-lived tasks stay near top)
    def _sort_key(t):
        return (t.get("created_date") or "")

    open_tasks.sort(key=_sort_key)
    priority_tasks.sort(key=_sort_key)
    someday_tasks.sort(key=_sort_key)
    done_recent.sort(key=lambda t: t.get("completed_date") or "", reverse=True)

    try:
        pdf_bytes, current_page = page_renderer.build_page(
            open_tasks=open_tasks,
            priority_tasks=priority_tasks,
            someday_tasks=someday_tasks,
            done_recently=done_recent,
        )
    except Exception as exc:
        errors.append(f"render failed: {exc}")
        notifier.send_alert("render failed", traceback.format_exc())
        return None

    pdf_path = str(Path(tmpdir) / "tomorrow.pdf")
    Path(pdf_path).write_bytes(pdf_bytes)

    # Store the new CurrentPage record BEFORE pushing (so a push failure
    # doesn't leave us with stale state on the next idempotency check).
    store.set_current_page(current_page)

    try:
        rmapi_client.push_pdf(pdf_path)
    except Exception as exc:
        errors.append(f"rmapi push failed: {exc}")

    try:
        rmapi_client.archive_pdf(pdf_path)
    except Exception as exc:
        errors.append(f"rmapi archive failed (non-fatal): {exc}")

    return pdf_path


if __name__ == "__main__":
    main()
