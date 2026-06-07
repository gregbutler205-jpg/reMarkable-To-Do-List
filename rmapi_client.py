"""
reMarkable cloud transport via rmapi.

Auth: the GitHub secret RMAPI_CONFIG holds the contents of the rmapi token
file (~/.config/rmapi/rmapi.conf). The workflow writes it before this runs.

rmapi commands used:
  rmapi get  <path>       — downloads to current working directory
  rmapi put  <file> <dir> — uploads file into the given cloud folder
  rmapi rm   <path>       — removes a document
  rmapi mkdir <path>      — creates a cloud folder (idempotent enough for us)
"""
import os
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

import config


def _run(args: list[str], check=True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["rmapi"] + args,
        capture_output=True, text=True, check=check,
    )


def pull_as_pdf(dest_dir: str) -> str:
    """
    Download the rolling To-Do document from the reMarkable cloud.
    Returns the local path of the downloaded PDF.
    """
    os.makedirs(dest_dir, exist_ok=True)
    original = os.getcwd()
    try:
        os.chdir(dest_dir)
        result = _run(["get", f"/{config.RM_DOC_NAME}"])
        print(f"rmapi get stdout: {result.stdout!r}", flush=True)
        print(f"rmapi get stderr: {result.stderr!r}", flush=True)
    finally:
        os.chdir(original)

    # List everything in dest_dir for debugging
    all_files = list(Path(dest_dir).iterdir())
    print(f"Files in dest_dir: {[f.name for f in all_files]}", flush=True)

    # Find the PDF rmapi wrote
    candidates = list(Path(dest_dir).glob("*.pdf"))
    if not candidates:
        # Also try zip files (some rmapi versions download as zip)
        candidates = list(Path(dest_dir).glob("*.zip"))
    if not candidates:
        raise FileNotFoundError(
            f"rmapi get did not produce a PDF in {dest_dir}. "
            f"Files present: {[f.name for f in all_files]}"
        )
    return str(candidates[0])


def push_pdf(pdf_path: str):
    """
    Replace the rolling To-Do document in the cloud.
    Strategy: remove the old document, then upload the new one under the same name.
    The device will sync the replacement on next connect.
    """
    # Rename locally so the uploaded doc has the right cloud name
    named = Path(pdf_path).parent / f"{config.RM_DOC_NAME}.pdf"
    if pdf_path != str(named):
        shutil.copy(pdf_path, named)

    # Remove existing doc (ignore errors — it may not exist on first run)
    _run(["rm", f"/{config.RM_DOC_NAME}"], check=False)

    # Upload to root
    _run(["put", str(named), "/"])


def archive_pdf(pdf_path: str):
    """
    Upload a dated copy into the Archive folder on the device.
    Creates the folder if it doesn't exist.
    """
    folder = f"/{config.RM_ARCHIVE_FOLDER}"
    _run(["mkdir", folder], check=False)

    today = date.today().isoformat()
    named = Path(pdf_path).parent / f"{today}.pdf"
    if pdf_path != str(named):
        shutil.copy(pdf_path, named)
    _run(["put", str(named), folder])
