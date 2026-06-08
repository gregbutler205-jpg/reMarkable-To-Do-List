"""
Google Sheets data layer.

Tabs:
  Tasks       — canonical task list (one row per task)
  CurrentPage — single JSON blob in A1 describing the page on the device
  Log         — one row per pipeline run
"""
import json
import os
import uuid
from datetime import date, datetime

import gspread
from google.oauth2.service_account import Credentials

import config

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _open_spreadsheet():
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=_SCOPES,
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(config.SHEET_ID)


class SheetStore:
    def __init__(self):
        sh = _open_spreadsheet()
        self._tasks = sh.worksheet(config.TASKS_TAB)
        self._cp    = sh.worksheet(config.CURRENT_PAGE_TAB)
        self._log   = sh.worksheet(config.LOG_TAB)
        self._headers = None  # lazy-loaded

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _get_headers(self):
        if self._headers is None:
            self._headers = self._tasks.row_values(1)
        return self._headers

    def _col(self, name):
        return self._get_headers().index(name) + 1

    def _row_for_id(self, task_id):
        ids = self._tasks.col_values(self._col("id"))
        try:
            return ids.index(task_id) + 1   # 1-based; ids[0] is header
        except ValueError:
            return None

    # ── Tasks ──────────────────────────────────────────────────────────────────

    def get_tasks(self, status=None):
        """Return all task rows as dicts, optionally filtered by status."""
        rows = self._tasks.get_all_records()
        if status is not None:
            return [r for r in rows if r.get("status") == status]
        return rows

    def get_tasks_by_status(self, *statuses):
        rows = self._tasks.get_all_records()
        return [r for r in rows if r.get("status") in statuses]

    def update_task(self, task_id: str, **fields):
        row = self._row_for_id(task_id)
        if row is None:
            return
        headers = self._get_headers()
        for key, val in fields.items():
            if key in headers:
                self._tasks.update_cell(row, headers.index(key) + 1, val)

    def add_task(self, text: str, status=config.STATUS_OPEN,
                 source="handwritten", confidence="high") -> str:
        task_id = str(uuid.uuid4())[:8]
        today = str(date.today())
        row = [task_id, text, status, today, "", "", 0, source, confidence]
        # Pad to header length
        headers = self._get_headers()
        row += [""] * (len(headers) - len(row))
        self._tasks.append_row(row, value_input_option="USER_ENTERED")
        return task_id

    def exact_duplicate_exists(self, text: str) -> bool:
        """Return True if an open task with identical text already exists."""
        rows = self.get_tasks(status=config.STATUS_OPEN)
        return any(r.get("text", "").strip() == text.strip() for r in rows)

    def increment_rollover(self, task_ids: list[str]):
        if not task_ids:
            return
        id_set = set(task_ids)
        all_rows = self._tasks.get_all_records()
        rc_col = self._col("rollover_count")
        id_col = self._col("id")
        for sheet_row, row in enumerate(all_rows, start=2):
            if row.get("id") in id_set:
                current = int(row.get("rollover_count") or 0)
                self._tasks.update_cell(sheet_row, rc_col, current + 1)

    # ── CurrentPage ────────────────────────────────────────────────────────────

    def get_current_page(self) -> dict | None:
        val = self._cp.acell("A1").value
        if not val:
            return None
        return json.loads(val)

    def set_current_page(self, data: dict):
        self._cp.update("A1", [[json.dumps(data, indent=2)]])

    def mark_processed(self):
        cp = self.get_current_page()
        if cp:
            cp["processed"] = True
            self.set_current_page(cp)

    def clear_processed(self):
        """Reset processed flag so the pipeline re-reads the current page."""
        cp = self.get_current_page()
        if cp:
            cp["processed"] = False
            self.set_current_page(cp)

    # ── Log ────────────────────────────────────────────────────────────────────

    def append_log(self, done=0, demoted=0, promoted=0, new=0,
                   carried=0, provider="", page_id="", errors=""):
        row = [
            datetime.utcnow().isoformat(timespec="seconds"),
            done, demoted, promoted, new, carried, provider, page_id,
            errors,
        ]
        self._log.append_row(row, value_input_option="USER_ENTERED")
