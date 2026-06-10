import fitz  # PyMuPDF library used for rendering PDF files into images.
import os
from pathlib import Path

# This script converts PDF document pages into high-resolution PNG image files so our AI vision model can read them.

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
    # Create the output directory if it doesn't exist yet, preventing folder missing errors.
    # Non-technical: This ensures we have a folder on the computer to save the page photos we generate.
    os.makedirs(output_dir, exist_ok=True)

    # Open the PDF document using PyMuPDF.
    # Non-technical: This loads the digital drawing booklet so we can read its pages.
    doc = fitz.open(pdf_path)
    pages = []

    # Calculate zoom scale based on DPI (PyMuPDF defaults to 72 points per inch).
    # Non-technical: This scales up the drawing so text and tiny fractions remain sharp and legible for the AI.
    zoom = dpi / 72  
    mat = fitz.Matrix(zoom, zoom)

    # Loop through each page, render it, and save as a PNG image.
    # Non-technical: This saves each page of the booklet as a separate picture file on your hard drive.
    for i, page in enumerate(doc):
        page_number = i + 1
        image_path = os.path.join(output_dir, f"page_{page_number}.png")

        # Render the page to a pixel map (image buffer) using our zoom scale.
        # Non-technical: This converts the vector PDF page into a high-quality pixel image.
        pix = page.get_pixmap(matrix=mat)
        
        # Save the rendered pixel map to disk.
        # Non-technical: This writes the actual picture file to the output directory.
        pix.save(image_path)

        pages.append({
            "page_number": page_number,
            "image_path": image_path,
        })

    # Close the PDF document to release the file handle from memory.
    # Non-technical: This cleans up memory and unlocks the open document file.
    doc.close()
    return pages


if __name__ == "__main__":
    import sys
    import json

    # Command line interface safety check.
    # Non-technical: If run directly from the terminal without arguments, print usage instructions.
    if len(sys.argv) < 3:
        print("Usage: python pdf_to_images.py <pdf_path> <output_dir>")
        sys.exit(1)

    result = convert_pdf_to_images(sys.argv[1], sys.argv[2])
    print(json.dumps(result, indent=2))