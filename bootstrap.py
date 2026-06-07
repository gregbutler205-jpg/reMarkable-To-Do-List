#!/usr/bin/env python3
"""
One-time bootstrap — run this locally AFTER:
  - GOOGLE_SERVICE_ACCOUNT_JSON is set in your environment
  - SHEET_ID is set in your environment
  - rmapi is installed and paired (rmapi ls should work)

What it does
------------
1. Creates / verifies the three Sheet tabs with correct headers.
2. Renders a blank starter page and pushes it to the reMarkable.
3. Writes the initial CurrentPage record (processed=false).

Safe to re-run — it checks before creating.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# ── Verify env vars before doing anything ─────────────────────────────────────
missing = [v for v in ("GOOGLE_SERVICE_ACCOUNT_JSON", "SHEET_ID") if not os.getenv(v)]
if missing:
    print(f"ERROR: missing environment variables: {', '.join(missing)}")
    print("Set them and re-run.")
    sys.exit(1)

import config
import page_renderer
import rmapi_client
from sheet_store import SheetStore

from google.oauth2.service_account import Credentials
import gspread

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ── Tab definitions ───────────────────────────────────────────────────────────

TASKS_HEADERS = [
    "id", "text", "status", "created_date", "completed_date",
    "parked_date", "rollover_count", "source", "confidence",
]

LOG_HEADERS = [
    "timestamp", "done", "demoted", "promoted", "new",
    "carried", "provider", "page_id", "errors",
]

TABS = {
    config.TASKS_TAB:        TASKS_HEADERS,
    config.CURRENT_PAGE_TAB: [],          # just needs to exist; content is a JSON blob
    config.LOG_TAB:          LOG_HEADERS,
}


def _open_sheet():
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=_SCOPES,
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(config.SHEET_ID)


def step1_create_tabs(sh):
    print("\n── Step 1: Create / verify Sheet tabs ──")
    existing = {ws.title for ws in sh.worksheets()}

    for tab_name, headers in TABS.items():
        if tab_name in existing:
            print(f"  ✓ '{tab_name}' already exists — skipping")
        else:
            ws = sh.add_worksheet(title=tab_name, rows=1000, cols=max(len(headers), 10))
            print(f"  + Created '{tab_name}'")
            if headers:
                ws.append_row(headers, value_input_option="USER_ENTERED")
                print(f"    Headers written: {headers}")

    # Ensure Tasks tab has the right headers (if it pre-existed and is empty)
    tasks_ws = sh.worksheet(config.TASKS_TAB)
    existing_headers = tasks_ws.row_values(1)
    if not existing_headers:
        tasks_ws.append_row(TASKS_HEADERS, value_input_option="USER_ENTERED")
        print(f"  + Wrote Tasks headers (tab was empty)")
    elif existing_headers != TASKS_HEADERS:
        print(f"  WARNING: Tasks headers don't match expected.")
        print(f"    Expected : {TASKS_HEADERS}")
        print(f"    Found    : {existing_headers}")
        print(f"    Fix the sheet headers manually before running the pipeline.")


def step2_push_blank_page():
    print("\n── Step 2: Render + push blank starter page ──")
    with tempfile.TemporaryDirectory() as tmpdir:
        # Render with empty task lists
        pdf_bytes, current_page = page_renderer.build_page(
            open_tasks=[],
            priority_tasks=[],
            someday_tasks=[],
            done_recently=[],
        )
        pdf_path = str(Path(tmpdir) / "starter.pdf")
        Path(pdf_path).write_bytes(pdf_bytes)
        print(f"  Page rendered ({len(pdf_bytes):,} bytes)")

        print("  Pushing to reMarkable via rmapi …")
        try:
            rmapi_client.push_pdf(pdf_path)
            print("  ✓ Push succeeded")
        except Exception as exc:
            print(f"  ✗ Push failed: {exc}")
            print("  You can push manually: copy the PDF to your device via the reMarkable app.")
            pdf_out = Path.cwd() / "starter_page.pdf"
            pdf_out.write_bytes(pdf_bytes)
            print(f"  Saved locally: {pdf_out}")

        return current_page


def step3_write_current_page(current_page):
    print("\n── Step 3: Write initial CurrentPage record ──")
    store = SheetStore()
    store.set_current_page(current_page)
    print(f"  ✓ CurrentPage written (page_id={current_page['page_id']}, processed=false)")


def main():
    print("=== reMarkable To-Do bootstrap ===")
    sh = _open_sheet()

    step1_create_tabs(sh)
    current_page = step2_push_blank_page()
    step3_write_current_page(current_page)

    print("\n=== Done ===")
    print("Tabs created, blank page on device, CurrentPage initialized.")
    print("Add GitHub secrets, then enable the workflow to start the nightly loop.")


if __name__ == "__main__":
    main()
