import fitz  # PyMuPDF
import os
from pathlib import Path


def convert_pdf_to_images(pdf_path: str, output_dir: str, dpi: int = 200) -> list:
    """
    Convert each page of a PDF to a PNG image.

    Args:
        pdf_path: path to the PDF file
        output_dir: directory to save PNG images
        dpi: resolution for rendering (default 200)

    Returns:
        list of dicts with page_number and image_path
    """
    os.makedirs(output_dir, exist_ok=True)

    doc = fitz.open(pdf_path)
    pages = []

    zoom = dpi / 72  # PyMuPDF default is 72 DPI
    mat = fitz.Matrix(zoom, zoom)

    for i, page in enumerate(doc):
        page_number = i + 1
        image_path = os.path.join(output_dir, f"page_{page_number}.png")

        pix = page.get_pixmap(matrix=mat)
        pix.save(image_path)

        pages.append({
            "page_number": page_number,
            "image_path": image_path,
        })

    doc.close()
    return pages


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 3:
        print("Usage: python pdf_to_images.py <pdf_path> <output_dir>")
        sys.exit(1)

    result = convert_pdf_to_images(sys.argv[1], sys.argv[2])
    print(json.dumps(result, indent=2))