# folder_processor.py
# This script scans customer ZIP archives to find and select the correct GFS shop drawing PDF, skipping non-drawing documents.

import zipfile
import os
import json
from pathlib import Path
import fitz  # PyMuPDF library used for reading PDF files.

# List of folders to completely ignore during extraction.
# Non-technical: We skip folders like "BOL" (Bill of Lading) or "Sales Receipt" because they never contain engineering drawings.
SKIP_FOLDERS = {
    "BOL", "Sales Receipt", "Transmittal", "Shipping",
    "Photos", "Payment", "Invoice", "Warranty"
}

# Substrings to match anywhere in the filename — not just at the start.
# Non-technical: We skip files whose name contains any of these words, regardless of where they appear.
SKIP_SUBSTRINGS = ("fedex", "bol", "warranty", "delivery", "receipt", "invoice")

# List of file extensions to ignore.
# Non-technical: We skip Excel files, emails, and text files because they are not shop drawing PDFs.
SKIP_EXTENSIONS = {".msg", ".eml", ".xlsx", ".docx", ".txt"}

# List of CAD drawing extensions.
# Non-technical: These are AutoCAD design files which we collect but do not process directly.
CAD_EXTENSIONS = {".dwg", ".dxf"}

# List of image extensions.
# Non-technical: These are site pictures or shipping photos which we collect but do not parse for dimensions.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

# Words or phrases in filenames that indicate the document is not a shop drawing.
# Non-technical: If a file has these words in its name (like "invoice" or "payment"), we know it's not a drawing.
NON_DRAWING_PHRASES = [
    "glass order", "proposal", "invoice", "payment",
    "deposit", "contact form", "purchase order", "change order",
    "customer contact", "invitation to bid", "invitation_to_bid", 
    "partial waiver", "final pay", "final_pay"
]

# Set of individual administrative keywords to block (case-insensitive, matching full words).
# Non-technical: Excludes documents that are clearly contracts, billing statements, or emails.
IGNORED_WORDS = {
    "contact", "po", "quote", "rfq", "pricing", "email", "waiver", "specs", "plans",
    "bill", "receipt", "warranty", "transmittal", "correspondence", "bid", "addendum",
    "agreement", "contract", "admin", "commercial", "contractor", "awarded", "award",
    "permit"
}

# Words in filenames that indicate the document is highly likely a GFS shop drawing.
# Non-technical: These keywords help us quickly identify and approve potential shop drawings.
GFS_FILENAME_HINTS = [
    "approved shops", "shop drawing",
    "rev_a", "rev_b", "rev a", "rev b"
]


def _should_skip_basic(zip_path: str) -> bool:
    # Check if a file in the zip archive should be ignored based on parent folders or filename prefixes.
    # Non-technical: Skips macOS metadata, skipped folders (like "BOL"), and administrative prefixes.
    parts = Path(zip_path).parts
    
    # Skip macOS metadata folders
    for part in parts:
        if part == "__MACOSX":
            return True
            
    # Check if any parent folder is on the skip list (case-insensitive check)
    skip_folders_lower = {sf.lower() for sf in SKIP_FOLDERS}
    for part in parts[:-1]:
        if part.lower() in skip_folders_lower:
            return True
            
    filename = Path(zip_path).name
    
    # Skip hidden files.
    if filename.startswith("."):
        return True
        
    # Skip files whose name contains any blocked substring (case-insensitive, anywhere in the name)
    filename_lower = filename.lower()
    if any(sub in filename_lower for sub in SKIP_SUBSTRINGS):
        return True
        
    return False


def _has_non_drawing_hints(filename: str) -> bool:
    # Check if the filename contains administrative or commercial indicators.
    # Non-technical: Identifies files containing "purchase order" or "payment" to filter them early.
    name_lower = filename.lower()
    
    # 1. Direct phrase checking
    if any(phrase in name_lower for phrase in NON_DRAWING_PHRASES):
        return True
        
    # 2. Tokenized word checking
    norm_name = "".join(c if (c.isalnum() or c == " ") else " " for c in name_lower)
    words = set(norm_name.split())
    if words.intersection(IGNORED_WORDS):
        return True
        
    return False


def _priority_score(zip_path: str, filename: str) -> int:
    # Assign a priority score to a file based on its name and path parts.
    # Non-technical: Scores drawings based on folder path ("Approved Documents") and revision naming patterns.
    # Technical: Returns scores 5 (highest) to 1. Returns 0 if none match, which acts as a fallback for page 1 inspection.
    name = filename.lower()
    parts = [p.lower() for p in Path(zip_path).parts[:-1]]

    # 1. PDF in Approved Documents/ subfolder AND filename contains "approved shops" or "shop drawing" (Priority 5)
    if "approved documents" in parts and (
        "approved shops" in name or "shop drawing" in name
    ):
        return 5

    # 2. PDF in Approved Documents/ subfolder (Priority 4)
    if "approved documents" in parts:
        return 4
        
    # 3. Filename containing Approved (Priority 3)
    if "approved" in name:
        return 3
        
    # 4. Filename containing Rev B or Rev 2 (Priority 2)
    norm_name = name.replace("_", " ").replace("-", " ")
    if "rev b" in norm_name or "rev 2" in norm_name:
        return 2
        
    # 5. Filename containing Rev A or Rev 1 (Priority 1)
    if "rev a" in norm_name or "rev 1" in norm_name:
        return 1
        
    # 6. Otherwise, falls back to GFS check (Priority 0)
    return 0


def _should_skip_file(zip_path: str, has_pdf: bool) -> bool:
    # Check if a file should be ignored based on its suffix/extension and whether a PDF is present in the archive.
    # Non-technical: Ignores non-drawing files and conditionally skips image files if we already have drawing PDFs.
    ext = Path(zip_path).suffix.lower()
    filename = Path(zip_path).name
    
    # Bypass skip for drawing-like files in skipped folders if they have priority > 0 (e.g. contain Rev B or Approved)
    if ext == ".pdf" and not _has_non_drawing_hints(filename):
        prio = _priority_score(zip_path, filename)
        if prio > 0:
            return False
            
    if _should_skip_basic(zip_path):
        return True
        
    if ext in SKIP_EXTENSIONS:
        return True
        
    # Conditionally skip images only if a PDF drawing is present in the ZIP
    if has_pdf and ext in IMAGE_EXTENSIONS:
        return True
        
    return False


def _is_valid_drawing(filename: str, full_path: Path) -> bool:
    # Verify if a PDF file has GFS relevance AND a drawing-like structure (two-pronged validation).
    # Non-technical: Ensures a file mentions "Glass Flooring Systems" and looks like a sheet drawing (e.g. has scale/drawn by keywords).
    name_lower = filename.lower()
    
    # 1. GFS Relevance Check (Filename match)
    has_gfs_in_name = any(hint in name_lower for hint in GFS_FILENAME_HINTS)
    
    # 2. Drawing-like Structure Check (Filename match)
    norm_name = name_lower.replace("_", " ").replace("-", " ")
    has_drawing_in_name = (
        "approved shops" in name_lower or
        "shop drawing" in name_lower or
        "rev a" in norm_name or
        "rev b" in norm_name or
        "rev 1" in norm_name or
        "rev 2" in norm_name or
        "rev.a" in norm_name or
        "rev.b" in norm_name or
        "rev_" in name_lower
    )
    
    # Extract page 1 text to perform internal checks
    page1_text = ""
    try:
        doc = fitz.open(str(full_path))
        if len(doc) > 0:
            page1_text = doc[0].get_text().lower()
        doc.close()
    except Exception:
        pass

    # GFS Relevance Check (Page 1 text match)
    has_gfs_in_text = "glass flooring systems" in page1_text
    
    # Must satisfy GFS Relevance
    if not (has_gfs_in_name or has_gfs_in_text):
        return False

    # Drawing-like Structure Check (Page 1 text match)
    drawing_keywords = [
        "drawing no", "drawing number", "sheet", "scale", "drawn by", 
        "project", "approved shops", "shop drawing", "rev.", "rev ", "revision",
        "c-100", "p-101", "s-101", "g-101"
    ]
    has_drawing_in_text = any(kw in page1_text for kw in drawing_keywords)
    
    # Must satisfy Drawing-like Structure
    if not (has_drawing_in_name or has_drawing_in_text):
        return False
        
    return True


def process_zip(zip_path: str) -> dict:
    # Main function to extract and categorize files inside a ZIP archive.
    # Non-technical: Scans the zip folder, groups CAD drawings/photos, and finds the best shop drawing PDF.
    extract_dir = zip_path + "_extracted"
    os.makedirs(extract_dir, exist_ok=True)

    cad_files = []
    site_photos = []
    skipped = []

    # 1. First pass: Determine if there is at least one PDF present (not matching basic skip rules)
    # Non-technical: Checks if the ZIP has any candidate drawings before we decide to skip image files.
    has_pdf = False
    with zipfile.ZipFile(zip_path, "r") as zf:
        all_files = [f for f in zf.namelist() if not f.endswith("/")]
        for f in all_files:
            if Path(f).name.startswith(".") or "__MACOSX" in Path(f).parts:
                continue
            if not _should_skip_basic(f):
                if Path(f).suffix.lower() == ".pdf":
                    has_pdf = True
                    break

    # 2. Second pass: Collect drawing candidates, photos, CAD files, and skipped files.
    # Non-technical: Unpacks drawing files to disk and groups images or design drawings.
    potential_pdfs = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for f in all_files:
            # Silently ignore ALL macOS metadata files and __MACOSX folder
            if Path(f).name.startswith(".") or "__MACOSX" in Path(f).parts:
                continue

            ext = Path(f).suffix.lower()
            filename = Path(f).name

            # Skip files matching exclusion rules.
            if _should_skip_file(f, has_pdf):
                skipped.append(filename)
                continue

            # Group CAD files (.dwg, .dxf)
            if ext in CAD_EXTENSIONS:
                cad_files.append(f)
                continue

            # Group site and shipping photos.
            if ext in IMAGE_EXTENSIONS:
                site_photos.append(f)
                continue

            # Identify candidate shop drawings.
            if ext == ".pdf":
                # Filter out obvious non-drawings (e.g. invoices, proposals, POs, contacts)
                if _has_non_drawing_hints(filename):
                    skipped.append(filename)
                    continue

                # Extract the PDF file to disk for page 1 inspection.
                try:
                    zf.extract(f, extract_dir)
                    full_path = Path(extract_dir) / Path(f)
                except Exception:
                    skipped.append(filename)
                    continue

                potential_pdfs.append({
                    "path": str(full_path),
                    "zip_path": f,
                    "filename": filename,
                })

    # Return empty response if no drawing candidates are found at all.
    if not potential_pdfs:
        return {
            "selected_pdf": None,
            "ambiguous": False,
            "candidates": [],
            "candidate_names": [],
            "cad_files": cad_files,
            "site_photos": site_photos,
            "skipped": skipped,
        }

    # Evaluate candidates using two-pronged validation (GFS Relevance + Drawing structure)
    valid_candidates = []
    for p in potential_pdfs:
        full_path = Path(p["path"])
        filename = p["filename"]
        if _is_valid_drawing(filename, full_path):
            prio = _priority_score(p["zip_path"], filename)
            valid_candidates.append({
                **p,
                "priority": prio,
            })
        else:
            skipped.append(filename)

    # Rule: If none contain Glass Flooring Systems (and no valid candidates exist), return all potential PDFs as ambiguous
    if not valid_candidates:
        return {
            "selected_pdf": None,
            "ambiguous": True,
            "candidates": [p["path"] for p in potential_pdfs],
            "candidate_names": [p["filename"] for p in potential_pdfs],
            "cad_files": cad_files,
            "site_photos": site_photos,
            "skipped": skipped,
        }

    # Identify the highest priority group among valid candidate drawings.
    max_prio = max(c["priority"] for c in valid_candidates)
    top_group = [c for c in valid_candidates if c["priority"] == max_prio]

    # Determine if we have multiple candidates in the highest priority group.
    # Non-technical: If more than one file shares the top score, we flag it as ambiguous.
    ambiguous = len(top_group) > 1

    # Any potential PDF not in our final selected/ambiguous group is skipped.
    final_skipped = list(skipped)
    
    if not ambiguous:
        # If not ambiguous, we selected top_group[0]. Any other valid candidates are skipped.
        for p in potential_pdfs:
            if p["zip_path"] != top_group[0]["zip_path"]:
                final_skipped.append(p["filename"])
                
        return {
            "selected_pdf": top_group[0]["path"],
            "ambiguous": False,
            "candidates": [],
            "candidate_names": [],
            "cad_files": cad_files,
            "site_photos": site_photos,
            "skipped": final_skipped,
        }
    else:
        # If ambiguous, we return ALL valid candidates. No valid candidates are skipped.
        for p in potential_pdfs:
            is_valid = any(p["zip_path"] == v["zip_path"] for v in valid_candidates)
            if not is_valid:
                final_skipped.append(p["filename"])
                
        return {
            "selected_pdf": None,
            "ambiguous": True,
            "candidates": [c["path"] for c in valid_candidates],
            "candidate_names": [c["filename"] for c in valid_candidates],
            "cad_files": cad_files,
            "site_photos": site_photos,
            "skipped": final_skipped,
        }


def process_zip_all(zip_path: str) -> dict:
    """
    Extract ALL valid GFS drawing PDFs from a ZIP archive.
    Unlike process_zip which picks one winner, this returns every
    PDF that passes the GFS validity check.
    Used for maximum extraction across complex project folders.
    """
    extract_dir = zip_path + "_extracted"
    os.makedirs(extract_dir, exist_ok=True)

    cad_files   = []
    site_photos = []
    skipped     = []
    drawings    = []
    seen_names  = set()  # deduplicate by filename

    # First pass: check if any PDF exists
    has_pdf = False
    with zipfile.ZipFile(zip_path, "r") as zf:
        all_files = [f for f in zf.namelist() if not f.endswith("/")]
        for f in all_files:
            if Path(f).name.startswith(".") or "__MACOSX" in Path(f).parts:
                continue
            if not _should_skip_basic(f):
                if Path(f).suffix.lower() == ".pdf":
                    has_pdf = True
                    break

    # Second pass: collect all candidates
    with zipfile.ZipFile(zip_path, "r") as zf:
        for f in all_files:
            if Path(f).name.startswith(".") or "__MACOSX" in Path(f).parts:
                continue

            ext      = Path(f).suffix.lower()
            filename = Path(f).name

            if _should_skip_file(f, has_pdf):
                skipped.append(filename)
                continue

            if ext in CAD_EXTENSIONS:
                cad_files.append(f)
                continue

            if ext in IMAGE_EXTENSIONS:
                site_photos.append(f)
                continue

            if ext == ".pdf":
                if _has_non_drawing_hints(filename):
                    skipped.append(filename)
                    continue

                # Skip duplicate filenames already seen
                if filename in seen_names:
                    continue

                try:
                    zf.extract(f, extract_dir)
                    full_path = Path(extract_dir) / Path(f)
                except Exception:
                    skipped.append(filename)
                    continue

                if _is_valid_drawing(filename, full_path):
                    priority = _priority_score(f, filename)
                    drawings.append({
                        "path":     str(full_path),
                        "filename": filename,
                        "priority": priority,
                        "zip_path": f,
                    })
                    seen_names.add(filename)
                else:
                    skipped.append(filename)

    # Sort by priority descending — highest priority first
    drawings.sort(key=lambda x: x["priority"], reverse=True)

    return {
        "drawings":    drawings,
        "cad_files":   cad_files,
        "site_photos": site_photos,
        "skipped":     skipped,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python folder_processor.py <path_to_zip>")
    else:
        result = process_zip(sys.argv[1])
        print(json.dumps(result, indent=2))