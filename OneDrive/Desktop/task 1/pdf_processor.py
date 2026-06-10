"""
PDF Processor Module — Non-AI Only
====================================
This module is strictly non-AI. It contains no API calls, no vision clients,
no Azure, and no httpx imports.

Primary entry point for all PDF processing in the pipeline:
- extract_pages()  : Extracts text from every page of a PDF.
- render_page()    : Renders a single page to a PNG image (fallback only).
- get_page_count() : Returns the total number of pages in a PDF.

Important:
- render_page() is only called as a fallback for pages identified as scanned
  (i.e., pages where extracted text is fewer than 50 characters after stripping).
- render_page() is NEVER called on every page — only on scanned pages.
"""

import os
import fitz  # PyMuPDF


def extract_pages(pdf_path: str) -> list[dict]:
    """
    Opens the PDF at pdf_path and extracts text from each page.

    For each page:
    - Calls page.get_text() to extract raw text.
    - If the extracted text has fewer than 50 characters after stripping,
      the page is marked as scanned (is_scanned = True) and text is set to
      an empty string.

    Parameters
    ----------
    pdf_path : str
        Absolute or relative path to the PDF file.

    Returns
    -------
    list[dict]
        A list of dicts, one per page, with the following structure:
        {
            "page_number": int,   # 1-indexed page number
            "text": str,          # Raw extracted text; empty string if scanned
            "is_scanned": bool,   # True if text length < 50 chars after strip
            "image_path": None    # Always None here; filled later by render_page if needed
        }
    """
    pages = []

    doc = fitz.open(pdf_path)

    for i, page in enumerate(doc):
        raw_text = page.get_text()
        stripped_text = raw_text.strip()
        is_scanned = len(stripped_text) < 50

        pages.append({
            "page_number": i + 1,           # 1-indexed
            "text": "" if is_scanned else raw_text,
            "is_scanned": is_scanned,
            "image_path": None              # Filled later by render_page if needed
        })

    doc.close()
    return pages


def render_page(pdf_path: str, page_number: int, output_dir: str) -> str:
    """
    Renders a single PDF page to a PNG image at 300 DPI.

    This function is only called as a fallback for scanned pages.
    It is NEVER called on every page of a PDF.

    Parameters
    ----------
    pdf_path : str
        Absolute or relative path to the PDF file.
    page_number : int
        The 1-indexed page number to render.
    output_dir : str
        Directory where the rendered PNG image will be saved.

    Returns
    -------
    str
        Full path to the saved PNG image (output_dir/page_{page_number}.png).
    """
    os.makedirs(output_dir, exist_ok=True)

    doc = fitz.open(pdf_path)

    # Convert 1-indexed page_number to 0-indexed
    page = doc[page_number - 1]

    # 300 DPI: fitz default is 72 DPI, so scale factor = 300 / 72
    dpi = 300
    scale = dpi / 72
    matrix = fitz.Matrix(scale, scale)

    pixmap = page.get_pixmap(matrix=matrix)

    output_filename = f"page_{page_number}.png"
    output_path = os.path.join(output_dir, output_filename)

    pixmap.save(output_path)

    doc.close()
    return output_path


def get_page_count(pdf_path: str) -> int:
    """
    Returns the total number of pages in the PDF.

    Parameters
    ----------
    pdf_path : str
        Absolute or relative path to the PDF file.

    Returns
    -------
    int
        Total number of pages in the PDF.
    """
    doc = fitz.open(pdf_path)
    count = doc.page_count
    doc.close()
    return count