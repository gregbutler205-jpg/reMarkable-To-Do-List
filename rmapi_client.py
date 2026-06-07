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

    Strategies (tried in order):
      1. rmapi export /To-Do  — renders handwriting to PDF (ddvk default)
      2. rmapi get /To-Do     — downloads .rmdoc zip; we extract & render .rm files
      3. Template fallback    — no handwriting found; return empty-image placeholder
    """
    os.makedirs(dest_dir, exist_ok=True)
    original = os.getcwd()

    # ── Strategy 1: rmapi export ─────────────────────────────────────────────
    try:
        os.chdir(dest_dir)
        result = _run(["export", f"/{config.RM_DOC_NAME}"], check=False)
        print(f"rmapi export stdout: {result.stdout!r}", flush=True)
        print(f"rmapi export stderr: {result.stderr!r}", flush=True)
    finally:
        os.chdir(original)

    candidates = list(Path(dest_dir).glob("*.pdf"))
    if candidates:
        print(f"pull_as_pdf: export succeeded → {candidates[0]}", flush=True)
        return str(candidates[0])

    # ── Strategy 2: rmapi get + rmdoc parsing ────────────────────────────────
    try:
        os.chdir(dest_dir)
        result = _run(["get", f"/{config.RM_DOC_NAME}"], check=False)
        print(f"rmapi get stdout: {result.stdout!r}", flush=True)
        print(f"rmapi get stderr: {result.stderr!r}", flush=True)
    finally:
        os.chdir(original)

    rmdoc = next(Path(dest_dir).glob("*.rmdoc"), None)
    if rmdoc:
        out_pdf = str(Path(dest_dir) / f"{config.RM_DOC_NAME}.pdf")
        try:
            _rmdoc_to_pdf(str(rmdoc), out_pdf)
            print(f"pull_as_pdf: rmdoc→pdf succeeded → {out_pdf}", flush=True)
            return out_pdf
        except FileNotFoundError as e:
            # No .rm files in rmdoc — doc may be a blank template with no strokes yet.
            # Fall through to strategy 3.
            print(f"pull_as_pdf: rmdoc had no renderable content: {e}", flush=True)

    # ── Strategy 3: blank placeholder ────────────────────────────────────────
    # We have no handwriting to read.  Return a tiny placeholder PDF so the
    # rest of the pipeline (which just won't find any marks) can continue.
    print("pull_as_pdf: no handwriting found — using blank placeholder PDF", flush=True)
    placeholder = str(Path(dest_dir) / "placeholder.pdf")
    _make_blank_pdf(placeholder)
    return placeholder


def _rmdoc_to_pdf(rmdoc_path: str, out_pdf: str):
    """
    Convert a .rmdoc file to a PDF.
    Strategy:
      1. If there's an embedded PDF (base layer), extract it.
      2. Otherwise, render the .rm handwriting layer(s) to PDF via rmc.
    """
    import zipfile, tempfile
    with zipfile.ZipFile(rmdoc_path) as z:
        names = z.namelist()
        print(f"rmdoc contents: {names}", flush=True)

        # Strategy 1: embedded base PDF
        pdf_names = [n for n in names if n.endswith('.pdf')]
        if pdf_names:
            with z.open(pdf_names[0]) as src, open(out_pdf, 'wb') as dst:
                dst.write(src.read())
            return

        # Print .content JSON for debugging (shows fileType, pages, etc.)
        content_names = [n for n in names if n.endswith('.content')]
        for cn in content_names:
            try:
                import json as _json
                content_data = _json.loads(z.read(cn).decode('utf-8', errors='replace'))
                print(f"rmdoc .content ({cn}): fileType={content_data.get('fileType')}, "
                      f"pages={content_data.get('pages', [])[:3]}, "
                      f"pageCount={content_data.get('pageCount')}", flush=True)
            except Exception as ce:
                print(f"rmdoc .content parse error: {ce}", flush=True)

        # Strategy 2: render .rm handwriting layers via rmc
        rm_names = [n for n in names if n.endswith('.rm')]
        if not rm_names:
            raise FileNotFoundError(
                f"No renderable content in .rmdoc. Contents: {names}"
            )

        with tempfile.TemporaryDirectory() as tmp:
            # Extract all files
            z.extractall(tmp)
            # Render each .rm page to PDF and merge
            page_pdfs = []
            for rm_name in sorted(rm_names):
                rm_path = os.path.join(tmp, rm_name)
                page_pdf = rm_path.replace('.rm', '.pdf')
                result = subprocess.run(
                    ['rmc', '-t', 'pdf', '-o', page_pdf, rm_path],
                    capture_output=True, text=True
                )
                print(f"rmc {rm_name}: {result.stdout} {result.stderr}", flush=True)
                if os.path.exists(page_pdf):
                    page_pdfs.append(page_pdf)

            if not page_pdfs:
                raise FileNotFoundError(
                    f"rmc failed to render any .rm pages. Contents: {names}"
                )

            if len(page_pdfs) == 1:
                shutil.copy(page_pdfs[0], out_pdf)
            else:
                # Merge multiple pages with pymupdf
                import fitz
                doc = fitz.open()
                for p in page_pdfs:
                    doc.insert_pdf(fitz.open(p))
                doc.save(out_pdf)
                doc.close()


def _make_blank_pdf(out_pdf: str):
    """Create a minimal blank single-page PDF (1620×2160 pt = reMarkable size)."""
    try:
        import fitz
        doc = fitz.open()
        doc.new_page(width=1620, height=2160)
        doc.save(out_pdf)
        doc.close()
    except Exception:
        # Ultra-minimal PDF as fallback if fitz isn't available
        Path(out_pdf).write_bytes(
            b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n"
            b"0000000058 00000 n\n0000000115 00000 n\n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF\n"
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
    # ddvk rmapi: put <file> <destination folder>
    _run(["put", str(named), folder], check=False)
