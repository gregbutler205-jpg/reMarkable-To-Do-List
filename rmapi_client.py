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


def extract_rm_file(dest_dir: str) -> str | None:
    """
    Extract the first .rm handwriting file from the cached .rmdoc in dest_dir.
    Returns the local path to the .rm file, or None if not found.
    The .rmdoc must already have been downloaded by pull_as_pdf().
    """
    import zipfile
    rmdoc = next(Path(dest_dir).glob("*.rmdoc"), None)
    if not rmdoc:
        return None
    with zipfile.ZipFile(rmdoc) as z:
        rm_names = [n for n in z.namelist() if n.endswith(".rm")]
        if not rm_names:
            return None
        rm_name = sorted(rm_names)[0]
        out_path = str(Path(dest_dir) / "page.rm")
        with z.open(rm_name) as src, open(out_path, "wb") as dst:
            dst.write(src.read())
        print(f"extract_rm_file: extracted {rm_name} → {out_path}", flush=True)
        return out_path


def _rmdoc_to_pdf(rmdoc_path: str, out_pdf: str):
    """
    Convert a .rmdoc file to a PDF with handwriting overlaid on the base template.

    reMarkable stores:
      UUID.pdf                  — base template (printed page)
      UUID/<page-UUID>.rm       — handwriting strokes (one file per page)

    We:
      1. Extract the base PDF.
      2. For each .rm page, render strokes to SVG via rmc.
      3. Composite: render SVG onto the base PDF page with pymupdf.
      4. If no .rm files (blank page), return just the base PDF.
      5. If no base PDF either, render .rm strokes alone as PDF.
    """
    import zipfile, tempfile, json as _json
    import fitz  # pymupdf

    with zipfile.ZipFile(rmdoc_path) as z:
        names = z.namelist()
        print(f"rmdoc contents: {names}", flush=True)

        # Debug: print .content JSON (fileType, page list)
        for cn in [n for n in names if n.endswith('.content')]:
            try:
                cd = _json.loads(z.read(cn).decode('utf-8', errors='replace'))
                print(f"rmdoc .content: fileType={cd.get('fileType')}, "
                      f"pageCount={cd.get('pageCount')}, "
                      f"pages={(cd.get('pages') or [])[:3]}", flush=True)
            except Exception as ce:
                print(f"rmdoc .content parse error: {ce}", flush=True)

        pdf_names = [n for n in names if n.endswith('.pdf')]
        rm_names  = [n for n in names if n.endswith('.rm')]
        print(f"rmdoc: {len(pdf_names)} PDF(s), {len(rm_names)} .rm file(s)", flush=True)

        if not pdf_names and not rm_names:
            raise FileNotFoundError(
                f"No renderable content in .rmdoc. Contents: {names}"
            )

        with tempfile.TemporaryDirectory() as tmp:
            z.extractall(tmp)

            base_pdf_path = os.path.join(tmp, pdf_names[0]) if pdf_names else None

            if not rm_names:
                # No handwriting — return base template as-is
                print("rmdoc: no .rm files (blank page) — returning base PDF", flush=True)
                shutil.copy(base_pdf_path, out_pdf)
                return

            # Render each .rm page to SVG via rmc
            # rm_names sorted so page order is preserved
            rm_svgs = []  # list of (rm_name, svg_path_or_None)
            for rm_name in sorted(rm_names):
                rm_path = os.path.join(tmp, rm_name)
                svg_path = rm_path.replace('.rm', '.svg')
                result = subprocess.run(
                    ['rmc', '-t', 'svg', '-o', svg_path, rm_path],
                    capture_output=True, text=True
                )
                print(f"rmc svg {rm_name}: rc={result.returncode} "
                      f"stdout={result.stdout!r} stderr={result.stderr!r}", flush=True)
                rm_svgs.append((rm_name, svg_path if os.path.exists(svg_path) else None))

            if base_pdf_path:
                # Composite: multiply-blend base template + handwriting SVG.
                # Pillow multiply: white(255)×X=X, black(0)×X=0 → strokes appear,
                # white SVG background disappears naturally.
                from PIL import Image, ImageChops
                import io as _io

                out_pages = []
                base_doc = fitz.open(base_pdf_path)

                for page_idx, (rm_name, svg_path) in enumerate(rm_svgs):
                    # Render base page at ~150 DPI (1620 pt → ~3375 px at 150 dpi)
                    bp = base_doc[page_idx] if page_idx < len(base_doc) else base_doc[0]
                    scale = 226 / 72  # render at reMarkable's native 226 DPI
                    base_px = bp.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    base_img = Image.frombytes("RGB",
                                              [base_px.width, base_px.height],
                                              base_px.samples)

                    if svg_path:
                        try:
                            svg_data = open(svg_path, 'rb').read()
                            svg_doc  = fitz.open("svg", svg_data)
                            svg_px   = svg_doc[0].get_pixmap(
                                matrix=fitz.Matrix(base_px.width  / svg_doc[0].rect.width,
                                                   base_px.height / svg_doc[0].rect.height),
                                alpha=False,
                            )
                            svg_img = Image.frombytes("RGB",
                                                      [svg_px.width, svg_px.height],
                                                      svg_px.samples)
                            # Resize to exact match if needed
                            if svg_img.size != base_img.size:
                                svg_img = svg_img.resize(base_img.size, Image.LANCZOS)
                            # rmc SVG has Y-axis inverted relative to the PDF render
                            svg_img = svg_img.transpose(Image.FLIP_TOP_BOTTOM)
                            composited = ImageChops.multiply(base_img, svg_img)
                            svg_doc.close()
                            print(f"rmdoc: composited {rm_name} onto page {page_idx}", flush=True)
                        except Exception as oe:
                            print(f"rmdoc: SVG composite failed for {rm_name}: {oe} — using base only", flush=True)
                            composited = base_img
                    else:
                        composited = base_img

                    out_pages.append(composited)

                base_doc.close()

                # Save all pages into a single PDF
                if out_pages:
                    buf = _io.BytesIO()
                    out_pages[0].save(buf, format='PDF', save_all=True,
                                      append_images=out_pages[1:], resolution=150)
                    with open(out_pdf, 'wb') as f:
                        f.write(buf.getvalue())
                else:
                    shutil.copy(base_pdf_path, out_pdf)

            else:
                # No base PDF — render .rm strokes alone to PDF via rmc
                page_pdfs = []
                for rm_name, svg_path in rm_svgs:
                    if svg_path is None:
                        continue
                    pg_pdf = svg_path.replace('.svg', '.pdf')
                    result = subprocess.run(
                        ['rmc', '-t', 'pdf', '-o', pg_pdf,
                         os.path.join(tmp, rm_name)],
                        capture_output=True, text=True
                    )
                    if os.path.exists(pg_pdf):
                        page_pdfs.append(pg_pdf)
                if not page_pdfs:
                    raise FileNotFoundError("rmc failed to render any .rm pages")
                merged = fitz.open()
                for p in page_pdfs:
                    merged.insert_pdf(fitz.open(p))
                merged.save(out_pdf)
                merged.close()


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
