"""
Central configuration — all values come from environment variables or safe defaults.
Set secrets in GitHub Actions; override locally with a .env file.
"""
import os

# ── Model ─────────────────────────────────────────────────────────────────────
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "claude")   # "claude" | "gemini"

# ── reMarkable transport ──────────────────────────────────────────────────────
RM_DOC_NAME       = os.getenv("RM_DOC_NAME", "To-Do")
RM_ARCHIVE_FOLDER = os.getenv("RM_ARCHIVE_FOLDER", "Archive")

# ── Google Sheets ─────────────────────────────────────────────────────────────
SHEET_ID         = os.getenv("SHEET_ID", "")
TASKS_TAB        = "Tasks"
CURRENT_PAGE_TAB = "CurrentPage"
LOG_TAB          = "Log"

# Tasks sheet column names (must match the header row exactly)
TASKS_COLS = [
    "id", "text", "status", "created_date", "completed_date",
    "parked_date", "rollover_count", "source", "confidence",
]
# Valid status values
STATUS_OPEN     = "open"       # active — prints in From Yesterday
STATUS_PRIORITY = "priority"   # prints in Priorities box
STATUS_SOMEDAY  = "someday"    # prints in Someday region
STATUS_DONE     = "done"       # completed; may appear in Done Recently strip

# ── Pipeline rules ────────────────────────────────────────────────────────────
DONE_RECENTLY_DAYS = 2         # how many days back the Done Recently strip shows
MAX_FY_ROWS        = 7         # trigger page 2 at 8; render at most 7 on page 1

# ── Email / SMTP ──────────────────────────────────────────────────────────────
SMTP_HOST  = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER  = os.getenv("SMTP_USER", "")
SMTP_PASS  = os.getenv("SMTP_PASS", "")
EMAIL_TO   = os.getenv("EMAIL_TO", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", os.getenv("SMTP_USER", ""))
