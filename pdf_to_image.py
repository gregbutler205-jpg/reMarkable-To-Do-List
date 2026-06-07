"""
Convert a PDF page to a PNG image for the LLM reader.
Uses pymupdf (fitz) — fast, no system dependencies.
"""
import os
import fitz  # pymupdf


def pdf_to_images(pdf_path: str, dpi: int = 150) -> list[str]:
    """
    Rasterize every page of pdf_path.
    Returns a list of PNG file paths (one per page), written alongside the PDF.
    """
    doc = fitz.open(pdf_path)
    out_paths = []
    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    base = os.path.splitext(pdf_path)[0]
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=matrix)
        out = f"{base}_page{i}.png"
        pix.save(out)
        out_paths.append(out)
    doc.close()
    return out_paths
