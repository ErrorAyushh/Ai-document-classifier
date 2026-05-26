import zipfile
import os
import json
from pathlib import Path
import fitz  # PyMuPDF


SKIP_FOLDERS = {"BOL", "Sales Receipt", "Transmittal"}
SKIP_PREFIXES = ("FedEx", "BOL", "Warranty", "Delivery")
SKIP_EXTENSIONS = {".msg", ".eml", ".xlsx", ".docx", ".txt"}
CAD_EXTENSIONS = {".dwg", ".dxf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

# Filenames containing these are never shop drawings
NON_DRAWING_HINTS = [
    "glass order", "proposal", "invoice", "payment",
    "deposit", "contact form", "purchase order", "change order"
]

# Filenames containing these are always shop drawings (fast pass)
GFS_FILENAME_HINTS = [
    "approved shops", "shop drawing",
    "rev_a", "rev_b", "rev a", "rev b"
]


def _should_skip(zip_path: str) -> bool:
    parts = Path(zip_path).parts
    for part in parts[:-1]:
        if part in SKIP_FOLDERS:
            return True
        if part == "__MACOSX":
            return True
    filename = Path(zip_path).name
    if filename.startswith("."):
        return True
    if any(filename.startswith(p) for p in SKIP_PREFIXES):
        return True
    if Path(zip_path).suffix.lower() in SKIP_EXTENSIONS:
        return True
    return False


def _priority(filename: str) -> int:
    name = filename.lower()
    if "approved" in name:
        return 3
    if "rev_b" in name:
        return 2
    if "rev_a" in name:
        return 1
    return 0


def _is_gfs_drawing(zip_ref: zipfile.ZipFile, zip_path: str, extract_dir: str) -> bool:
    filename = Path(zip_path).name.lower()

    # Block non-drawing docs immediately (no extraction needed)
    if any(hint in filename for hint in NON_DRAWING_HINTS):
        return False

    # Always extract first so file exists on disk for later use
    try:
        zip_ref.extract(zip_path, extract_dir)
    except Exception:
        return False

    # Fast pass after extraction
    if any(hint in filename for hint in GFS_FILENAME_HINTS):
        return True

    # Fallback — read text from extracted file
    try:
        full_path = Path(extract_dir) / Path(zip_path)
        doc = fitz.open(str(full_path))
        page = doc[0]
        text = page.get_text()
        doc.close()
        return "Glass Flooring Systems" in text
    except Exception:
        return False

def process_zip(zip_path: str) -> dict:
    extract_dir = zip_path + "_extracted"
    os.makedirs(extract_dir, exist_ok=True)

    candidates = []
    cad_files = []
    site_photos = []
    skipped = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        all_files = [f for f in zf.namelist() if not f.endswith("/")]

        for f in all_files:
            # Silently ignore ALL macOS metadata files and __MACOSX folder
            if Path(f).name.startswith(".") or "__MACOSX" in Path(f).parts:
                continue

            ext = Path(f).suffix.lower()
            filename = Path(f).name

            if _should_skip(f):
                skipped.append(filename)
                continue

            if ext in CAD_EXTENSIONS:
                cad_files.append(f)
                continue

            if ext in IMAGE_EXTENSIONS:
                site_photos.append(f)
                continue

            if ext == ".pdf":
                if _is_gfs_drawing(zf, f, extract_dir):
                    info = zf.getinfo(f)
                    candidates.append({
                        # Normalized path — no mixed slashes on Windows
                        "path": str(Path(extract_dir) / Path(f)),
                        "zip_path": f,
                        "filename": filename,
                        "modified": info.date_time,
                        "priority": _priority(filename),
                    })
                else:
                    skipped.append(filename)

    if not candidates:
        return {
            "selected_pdf": None,
            "ambiguous": False,
            "candidates": [],
            "cad_files": cad_files,
            "site_photos": site_photos,
            "skipped": skipped,
        }

    # Sort by priority desc, then modification date desc
    candidates.sort(key=lambda x: (x["priority"], x["modified"]), reverse=True)

    top_priority = candidates[0]["priority"]
    top_date = candidates[0]["modified"]
    top_group = [
        c for c in candidates
        if c["priority"] == top_priority and c["modified"] == top_date
    ]

    ambiguous = len(top_group) > 1

    return {
        "selected_pdf": top_group[0]["path"] if not ambiguous else None,
        "ambiguous": ambiguous,
        "candidates": [c["path"] for c in top_group] if ambiguous else [],
        "cad_files": cad_files,
        "site_photos": site_photos,
        "skipped": skipped,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python folder_processor.py <path_to_zip>")
    else:
        result = process_zip(sys.argv[1])
        print(json.dumps(result, indent=2))