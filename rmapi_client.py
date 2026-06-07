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
    The ddvk rmapi fork downloads as .rmdoc; we export to PDF using rmapi export.
    """
    os.makedirs(dest_dir, exist_ok=True)
    original = os.getcwd()
    try:
        os.chdir(dest_dir)
        # Try exporting directly as PDF first (ddvk fork supports this)
        result = _run(["export", "-f", "pdf", f"/{config.RM_DOC_NAME}"], check=False)
        print(f"rmapi export stdout: {result.stdout!r}", flush=True)
        print(f"rmapi export stderr: {result.stderr!r}", flush=True)
    finally:
        os.chdir(original)

    candidates = list(Path(dest_dir).glob("*.pdf"))
    if candidates:
        return str(candidates[0])

    # Fallback: get the .rmdoc and convert via rmapi export
    original = os.getcwd()
    try:
        os.chdir(dest_dir)
        _run(["get", f"/{config.RM_DOC_NAME}"])
        rmdoc = next(Path(dest_dir).glob("*.rmdoc"), None)
        if rmdoc:
            # rmdoc is a zip; extract the PDF page images and combine
            out_pdf = str(Path(dest_dir) / f"{config.RM_DOC_NAME}.pdf")
            _rmdoc_to_pdf(str(rmdoc), out_pdf)
            return out_pdf
    finally:
        os.chdir(original)

    all_files = list(Path(dest_dir).iterdir())
    raise FileNotFoundError(
        f"Could not get a PDF from reMarkable. "
        f"Files present: {[f.name for f in all_files]}"
    )


def _rmdoc_to_pdf(rmdoc_path: str, out_pdf: str):
    """
    Convert a .rmdoc file (zip of page data) to a PDF.
    .rmdoc contains a PDF of the base layer inside the zip.
    """
    import zipfile
    with zipfile.ZipFile(rmdoc_path) as z:
        names = z.namelist()
        print(f"rmdoc contents: {names}", flush=True)
        # Look for an embedded PDF
        pdf_names = [n for n in names if n.endswith('.pdf')]
        if pdf_names:
            with z.open(pdf_names[0]) as src, open(out_pdf, 'wb') as dst:
                dst.write(src.read())
            return
        # No embedded PDF — the document is pure handwriting with no base PDF.
        # In this case we have nothing to read; create a placeholder.
        raise FileNotFoundError(
            f"No PDF found inside .rmdoc. "
            f"The To-Do document may be a notebook (no base PDF). "
            f"Contents: {names}"
        )


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
