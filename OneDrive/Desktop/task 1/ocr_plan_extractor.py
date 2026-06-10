"""
OCR-based plan view dimension extractor.
Uses PyMuPDF built-in Tesseract OCR on rendered page images.
Zero API calls. No PyTorch. Fast, lightweight, runs locally.
Requires Tesseract installed on the system.
"""

import re
import os
import tempfile
import fitz
from typing import Optional
from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # suppress DecompressionBombWarning


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
MIN_DIM_INCHES = 10    # filter out noise values below this
MAX_DIM_INCHES = 300   # filter out noise values above this

# Module-level flag set by Step 0 bracket cross-validation when the
# before-bracket value and the inside-bracket feet-inches value differ
# by more than 0.5 inches.
_bracket_mismatch: bool = False

# ---------------------------------------------------------------------------
# LABEL_CONFIG — defines each dimension type's detection keywords and
# exclude (noise) keywords.
# ---------------------------------------------------------------------------
LABEL_CONFIG: dict = {
    "exposed_frame": {
        "keywords": ["EXPOSED", "FRAME"],
        "exclude":  ["GLASS", "FLANGE", "OUT TO OUT", "ROUGH", "OPENING"],
    },
    "out_to_out_flange": {
        "keywords": ["OUT", "OUT"],
        "exclude":  ["GLASS", "EXPOSED", "ROUGH", "OPENING"],
    },
    "glass": {
        "keywords": ["GLASS"],
        "exclude":  ["FLANGE", "EXPOSED", "ROUGH", "OPENING", "CURB"],
    },
    "rough_opening": {
        "keywords": ["ROUGH", "OPENING"],
        "exclude":  ["GLASS", "FLANGE", "EXPOSED", "OUT TO OUT"],
    },
}

ANNOTATION_RE = re.compile(
    r'(\d+(?:[\s\-]\d+/\d+)?)'
    r'\s*[\[\({]'
    r'([^]\)}\n]{2,25})'
    r'[\]\)}\|]'
    r'\s*[\(\[{]?\s*'
    r'(EXPOSED[\s\n]*FRAME|OUT[\s\n]*TO[\s\n]*OUT(?:[\s\n]*OF[\s\n]*FLANGE)?|GLASS|ROUGH[\s\n]*OPENING)',
    re.IGNORECASE | re.DOTALL,
)

_ANNOTATION_LABEL_MAP = {
    'EXPOSED': 'exposed_frame',
    'OUT':     'out_to_out_flange',
    'GLASS':   'glass',
    'ROUGH':   'rough_opening',
}


def _annotation_label_key(s: str) -> Optional[str]:
    u = s.strip().upper()
    for k, v in _ANNOTATION_LABEL_MAP.items():
        if u.startswith(k):
            return v
    return None


def _extract_annotations_from_flat(flat: str) -> dict:
    found: dict = {}
    for m in ANNOTATION_RE.finditer(flat):
        key = _annotation_label_key(m.group(3))
        if not key:
            continue
        dec = _to_decimal(m.group(2).strip().rstrip("'\"` "))
        if dec is None or not (MIN_DIM_INCHES <= dec <= MAX_DIM_INCHES):
            dec = _to_decimal(m.group(1).strip())
        if dec and MIN_DIM_INCHES <= dec <= MAX_DIM_INCHES:
            found.setdefault(key, []).append(dec)
    return found


# Keep legacy noise constants for backward compatibility with helpers
# that still reference them directly.
_NOISE_KEYWORDS = (
    "GLASS", "FLANGE", "OUT TO OUT", "ROUGH", "OPENING", "CURB"
)
_WINDOW_NOISE_KEYWORDS = (
    "FLANGE", "GLASS", "ROUGH", "OPENING", "OUT TO OUT"
)


# ---------------------------------------------------------------------------
# Generic label detector
# ---------------------------------------------------------------------------

def _is_label(text: str, keywords: list) -> bool:
    """
    Returns True if *all* words in ``keywords`` appear in ``text``
    (case-insensitive).

    For multi-word keywords like "OUT TO OUT" the caller should pass
    them as a single element in the list; this function checks for the
    presence of that entire phrase.

    Examples
    --------
    >>> _is_label("EXPOSED FRAME WIDTH", ["EXPOSED", "FRAME"])
    True
    >>> _is_label("GLASS SIZE", ["GLASS"])
    True
    >>> _is_label("ROUGH OPENING", ["ROUGH", "OPENING"])
    True
    """
    upper = text.upper()
    return all(kw.upper() in upper for kw in keywords)


# ---------------------------------------------------------------------------
# Helper: check if a line contains exclude keywords
# ---------------------------------------------------------------------------

def _is_exclude_line(line: str, exclude: list) -> bool:
    """
    Returns True if the line contains any phrase from the ``exclude`` list
    (case-insensitive).  Used as the generic equivalent of ``_is_noise_line``
    inside ``_find_dims_for_label``.
    """
    upper = line.upper()
    return any(kw.upper() in upper for kw in exclude)


# ---------------------------------------------------------------------------
# Helper: check if a text line is an EXPOSED FRAME label (legacy shim)
# ---------------------------------------------------------------------------

def _is_exposed_frame_label(text: str) -> bool:
    """
    Legacy shim — delegates to the generic ``_is_label`` helper.
    Kept for any external callers that still reference this name.

    Returns True if the text contains both "EXPOSED" and "FRAME"
    (case insensitive, allowing OCR noise between them).
    """
    return _is_label(text, ["EXPOSED", "FRAME"])


# ---------------------------------------------------------------------------
# Helper: inline dimension converter
# ---------------------------------------------------------------------------

def _to_decimal(raw: str) -> Optional[float]:
    """
    Converts a raw dimension string to a decimal float (in inches).

    Supported formats:
    - "47-5/8"    → 47 + 5/8  = 47.625
    - "4'-3\""    → 4*12 + 3  = 51.0
    - "4'-3-1/2\""→ 4*12 + 3 + 1/2 = 51.5
    - "63\""      → 63.0
    - "39.625"    → 39.625
    - "47"        → 47.0

    Returns None if conversion fails.
    """
    try:
        # Strip inch marks and surrounding whitespace
        s = raw.strip().replace('"', '')

        # --- Feet + fractional inches: e.g. 4'-3-1/2 or 4'3-1/2 ---
        feet_inch_frac = re.match(
            r"^(\d+)'[\-\s]*(\d+)[\-\s](\d+)/(\d+)$", s
        )
        if feet_inch_frac:
            feet  = int(feet_inch_frac.group(1))
            whole = int(feet_inch_frac.group(2))
            num   = int(feet_inch_frac.group(3))
            den   = int(feet_inch_frac.group(4))
            return feet * 12 + whole + num / den

        # --- Feet + whole inches: e.g. 4'-3 or 4'3 ---
        feet_inch = re.match(r"^(\d+)'[\-\s]*(\d+)$", s)
        if feet_inch:
            feet  = int(feet_inch.group(1))
            whole = int(feet_inch.group(2))
            return feet * 12 + whole

        # --- Fractional inches: e.g. 47-5/8 ---
        frac_inch = re.match(r"^(\d+)[\-\s](\d+)/(\d+)$", s)
        if frac_inch:
            whole = int(frac_inch.group(1))
            num   = int(frac_inch.group(2))
            den   = int(frac_inch.group(3))
            return whole + num / den

        # --- Decimal or whole: e.g. 63 or 39.625 ---
        decimal_or_whole = re.match(r"^(\d+(?:\.\d+)?)$", s)
        if decimal_or_whole:
            return float(decimal_or_whole.group(1))

        return None

    except (ValueError, ZeroDivisionError):
        return None


# ---------------------------------------------------------------------------
# Helper: clean common OCR errors on GFS drawings
# ---------------------------------------------------------------------------

def _clean_ocr_text(text: str) -> str:
    """
    Fixes common OCR misreads on GFS drawings:
    - '_' → '-'  (OCR often reads hyphens as underscores)
    - 'l' → '1'  only when surrounded by digits
    - 'O' → '0'  only when surrounded by digits
    - 'S' → '5'  only when surrounded by digits
    - '°' → "'"  (Tesseract reads apostrophe as degree symbol)
    - '|' → ''   (pipe characters are drawing artifacts, ignore)

    Also fixes common Tesseract fraction misreads on GFS drawings:
    - "1%6" → "1/16", "3%6" → "3/16", "5%6" → "5/16", "7%6" → "7/16",
      "9%6" → "9/16"  (sixteenth fractions often read as %6)
    - "1%8" → "1/8",  "3%8" → "3/8",  "5%8" → "5/8",  "7%8" → "7/8"
    - "1%4" → "1/4",  "3%4" → "3/4"
    - "%e"  → "/16"   (common misread of /16)
    - "!%"  → "1/"    (common misread)
    - "}%"  → "7/"    (common misread)
    - "—"   → "-"     (em dash misread as hyphen)
    - "'"   → "'"     (curly apostrophe to straight apostrophe)

    Returns the cleaned string.
    """
    # Replace underscores with hyphens
    text = text.replace('_', '-')

    # Replace 'l' with '1' when surrounded by digits
    text = re.sub(r'(?<=\d)l(?=\d)', '1', text)

    # Replace 'O' with '0' when surrounded by digits
    text = re.sub(r'(?<=\d)O(?=\d)', '0', text)

    # Replace 'S' with '5' when surrounded by digits
    text = re.sub(r'(?<=\d)S(?=\d)', '5', text)

    # Replace degree symbol with apostrophe (Tesseract misread)
    text = text.replace('°', "'")

    # Remove pipe characters (drawing artifacts)
    text = text.replace('|', '')

    # ------------------------------------------------------------------
    # Common Tesseract fraction misreads on GFS drawings
    # ------------------------------------------------------------------

    # Sixteenth fractions (e.g. 13%6 → 13/16)
    text = text.replace('1%6', '1/16')
    text = text.replace('3%6', '3/16')
    text = text.replace('5%6', '5/16')
    text = text.replace('7%6', '7/16')
    text = text.replace('9%6', '9/16')

    # Eighth fractions
    text = text.replace('1%8', '1/8')
    text = text.replace('3%8', '3/8')
    text = text.replace('5%8', '5/8')
    text = text.replace('7%8', '7/8')

    # Quarter fractions
    text = text.replace('1%4', '1/4')
    text = text.replace('3%4', '3/4')

    # Other common misreads
    text = text.replace('%e', '/16')
    text = text.replace('!%', '1/')
    text = text.replace('}%', '7/')

    # Em dash misread as hyphen
    text = text.replace('\u2014', '-')

    # Curly apostrophe to straight apostrophe
    text = text.replace('\u2019', "'")

    # Fix remaining % misreads adjacent to digits
    text = re.sub(r'(?<=\d)%(?=\d)', '/', text)
    text = re.sub(r'(?<=\d)%(?=\s)', '', text)
    text = re.sub(r'(?<=\d)%(?=[\[\({])', '', text)

    return text.strip()


# ---------------------------------------------------------------------------
# Helper: apply fraction pre-pass to merge split fraction lines
# ---------------------------------------------------------------------------

def _apply_fraction_prepass(lines: list) -> list:
    """
    Pre-processes a list of OCR lines to merge split fraction lines.

    If line[i] is a plain integer  (matches r'^\\s*\\d+\\s*$')
    and line[i+1] is a plain fraction (matches r'^\\s*\\d+/\\d+\\s*$'),
    they are joined into a single line: "{whole} {fraction}" and
    line[i+1] is removed.

    This handles the common Tesseract behaviour of splitting "47-5/8"
    across two lines as "47" and "5/8".

    Returns a new list with merged lines.
    """
    plain_int_re  = re.compile(r'^\s*\d+\s*$')
    plain_frac_re = re.compile(r'^\s*\d+/\d+\s*$')

    result: list = []
    i = 0
    while i < len(lines):
        if (
            i + 1 < len(lines)
            and plain_int_re.match(lines[i])
            and plain_frac_re.match(lines[i + 1])
        ):
            merged = f"{lines[i].strip()} {lines[i + 1].strip()}"
            result.append(merged)
            i += 2  # skip the fraction line — it has been merged
        else:
            result.append(lines[i])
            i += 1
    return result


# ---------------------------------------------------------------------------
# Helper: parse bracket feet-inches value from a cleaned line
# ---------------------------------------------------------------------------

def _parse_bracket_feet_inches(cleaned_line: str) -> Optional[float]:
    """
    Attempts to parse a feet-inches value from inside a [...] bracket
    on a single cleaned OCR line.

    Looks for the pattern:
        [X'-Y"] or [X'-Y-N/D"] or [X'Y"] etc.

    Returns the decimal inch value if found and valid, else None.

    Examples
    --------
    >>> _parse_bracket_feet_inches("62 [5'-2 1/4\"]")
    62.25
    >>> _parse_bracket_feet_inches("57 [4'-9\"]")
    57.0
    >>> _parse_bracket_feet_inches("no bracket here")
    None
    """
    bracket_match = re.search(r'\[([^\]]+)\]', cleaned_line)
    if not bracket_match:
        return None

    inside = bracket_match.group(1)

    # Try feet + fractional inches: e.g. 5'-2 1/4 or 5'2-1/4
    feet_frac_match = re.search(
        r"(\d+)'[\-\s]*(\d+)[\-\s](\d+)/(\d+)", inside
    )
    if feet_frac_match:
        feet  = int(feet_frac_match.group(1))
        whole = int(feet_frac_match.group(2))
        num   = int(feet_frac_match.group(3))
        den   = int(feet_frac_match.group(4))
        if den != 0:
            return feet * 12 + whole + num / den

    # Try feet + whole inches: e.g. 4'-9 or 4'9
    feet_whole_match = re.search(
        r"(\d+)'[\-\s]*(\d+)", inside
    )
    if feet_whole_match:
        feet  = int(feet_whole_match.group(1))
        whole = int(feet_whole_match.group(2))
        return feet * 12 + whole

    return None


# ---------------------------------------------------------------------------
# Generic dimension finder
# ---------------------------------------------------------------------------

def _find_dims_for_label(lines: list, label_name: str) -> tuple:
    """
    Extracts width and height for a single dimension label using
    the annotation regex approach on flattened OCR text.
    Returns (width_raw, height_raw, needs_vision).
    needs_vision=True if total match count across all lines < 2.
    """
    flat = _clean_ocr_text(' '.join(lines))
    found = _extract_annotations_from_flat(flat)
    candidates = found.get(label_name, [])
    total_count = len(candidates)
    unique_vals = sorted(set(candidates))
    if total_count >= 2:
        return str(unique_vals[0]), str(unique_vals[-1]), False
    elif total_count == 1:
        return str(unique_vals[0]), None, True
    return None, None, True


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def extract_plan_ocr(image_path: str, page_number: int) -> dict:
    """
    Extracts all labelled dimensions from a GFS plan view page image
    using PyMuPDF + Tesseract OCR. No Vision API. No LLM. No PyTorch.

    Extracts four dimension types:
      - exposed_frame
      - out_to_out_flange
      - glass
      - rough_opening

    Runs two OCR passes:
      Pass 1 — normal orientation (0°).
      Pass 2 — image rotated 270 degrees (catches vertical dimension labels).
               Only runs if Pass 1 failed to find BOTH exposed_frame dimensions.
               Also skipped if image pixel count exceeds MAX_PIXELS
               (80,000,000) to avoid memory issues with very large images.
    Lines from both passes are combined before all extraction steps.

    Parameters
    ----------
    image_path : str
        Path to the rendered PNG of the plan view page.
    page_number : int
        1-indexed page number of this plan view page.

    Returns
    -------
    dict
        {
            "page_number": int,
            "method": "ocr_tesseract",
            "unit_letter": str | None,
            "unit_qty": int | None,
            "shape": str,
            "panel_count": int | None,
            "drawing_notes": list,
            "ocr_line_count": int,
            "dimensions": {
                "exposed_frame":     {
                    "width":  {"raw": str, "decimal": float} | None,
                    "height": {"raw": str, "decimal": float} | None,
                },
                "out_to_out_flange": {"width": ..., "height": ...},
                "glass":             {"width": ..., "height": ...},
                "rough_opening":     {"width": ..., "height": ...},
            },
            "needs_vision": bool,
            "confidence": "HIGH" | "MEDIUM" | "LOW",
        }

        needs_vision is True if exposed_frame width OR height is None.
        If both are found → needs_vision = False.
    """

    # Step 1: Open the image as a fitz.Pixmap
    pix = fitz.Pixmap(image_path)

    # Step 2: Remove alpha channel if present
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)

    # Step 3: Create a one-page PDF from the pixmap using Tesseract OCR
    pdf_bytes = pix.pdfocr_tobytes(language="eng")
    doc = fitz.open("pdf", pdf_bytes)
    page = doc[0]

    # Step 4: Extract text using Tesseract via PyMuPDF
    text = page.get_text()

    # Step 5: Split into lines and clean each line
    raw_lines = text.splitlines()
    cleaned_lines = [_clean_ocr_text(line) for line in raw_lines]

    # Step 6: Filter empty lines
    lines = [line for line in cleaned_lines if line.strip()]

    # ------------------------------------------------------------------
    # Line-join pre-pass — join lines that contain an open '[' bracket
    # but no closing ')' — these are split annotation lines from PyMuPDF OCR.
    # e.g. "58 [4'-10\"](EXPOSED" + "FRAME)" → "58 [4'-10\"](EXPOSED FRAME)"
    # ------------------------------------------------------------------
    joined_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        while i + 1 < len(lines) and '[' in line and ')' not in line:
            i += 1
            line = line.rstrip() + " " + lines[i].lstrip()
        joined_lines.append(line)
        i += 1
    lines = joined_lines

    # ------------------------------------------------------------------
    # Early check — run exposed_frame extraction on normal-pass lines.
    # If BOTH dimensions are found (needs_vision=False), skip the rotated
    # pass entirely to prevent rotated text from adjacent labels
    # contaminating the result.
    # ------------------------------------------------------------------
    early_w, early_l, early_nv = _find_dims_for_label(lines, "exposed_frame")
    normal_pass_complete = not early_nv

    # ------------------------------------------------------------------
    # Second OCR pass — rotate image 270 degrees to capture vertical
    # dimension labels that Tesseract misses in normal orientation.
    # Only runs if the normal pass did NOT find both dimensions.
    # Also skipped if image pixel count exceeds MAX_PIXELS to avoid
    # memory issues with very large high-DPI images.
    # ------------------------------------------------------------------
    MAX_PIXELS = 80_000_000

    lines_rot: list = []

    if not normal_pass_complete:
        tmp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                tmp_path = tmp.name

            img = Image.open(image_path)

            if img.width * img.height > MAX_PIXELS:
                lines_rot = []
            else:
                img_rotated = img.rotate(270, expand=True)
                img_rotated.save(tmp_path)

                pix_rot = fitz.Pixmap(tmp_path)
                if pix_rot.alpha:
                    pix_rot = fitz.Pixmap(pix_rot, 0)
                pdf_bytes_rot = pix_rot.pdfocr_tobytes(language='eng')
                doc_rot = fitz.open('pdf', pdf_bytes_rot)
                text_rot = doc_rot[0].get_text()
                doc_rot.close()

                lines_rot = [
                    _clean_ocr_text(line.strip())
                    for line in text_rot.splitlines()
                    if line.strip()
                ]
        except Exception:
            lines_rot = []
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Combine lines from both OCR passes for unit_letter / unit_qty search.
    # Keep pass-level annotations separate for count-based merge below.
    # ------------------------------------------------------------------
    all_lines = lines + lines_rot

    # ------------------------------------------------------------------
    # Step 7a: Search for unit_letter
    # ------------------------------------------------------------------
    unit_letter: Optional[str] = None
    unit_letter_pattern = re.compile(
        r"PLAN.*UNIT\s+([A-Z])\b|\bUNIT\s+([A-Z])\b",
        re.IGNORECASE,
    )
    for line in all_lines:
        match = unit_letter_pattern.search(line)
        if match:
            letter = match.group(1) or match.group(2)
            unit_letter = letter.upper()
            break

    # ------------------------------------------------------------------
    # Step 7b: Search for unit_qty
    # ------------------------------------------------------------------
    unit_qty: Optional[int] = None
    qty_pattern = re.compile(r"QTY\.?\s*[:\-]?\s*(\d+)", re.IGNORECASE)
    for line in all_lines:
        match = qty_pattern.search(line)
        if match:
            unit_qty = int(match.group(1))
            break

    # ------------------------------------------------------------------
    # Step 7c: Extract all four dimension types using count-based merge.
    #
    # For each label_name:
    #   - Collect candidates from normal pass and rotation pass separately.
    #   - passes_with_hits = number of passes that found >= 1 candidate.
    #   - total_matches    = all candidates combined.
    #   - unique_vals      = sorted(set(total_matches))
    #   - len(unique_vals) >= 2              → width=unique_vals[0],
    #                                          height=unique_vals[-1],
    #                                          needs_vision=False
    #   - len(unique_vals) == 1
    #     AND passes_with_hits >= 2          → width=height=unique_vals[0],
    #                                          needs_vision=False
    #   - Otherwise                          → width=unique_vals[0] if any,
    #                                          height=None,
    #                                          needs_vision=True
    # ------------------------------------------------------------------
    flat_normal = _clean_ocr_text(' '.join(lines))
    flat_rot    = _clean_ocr_text(' '.join(lines_rot)) if lines_rot else ''

    annotations_normal = _extract_annotations_from_flat(flat_normal)
    annotations_rot    = _extract_annotations_from_flat(flat_rot) if flat_rot else {}

    def _extract_dim_type(label_name: str) -> dict:
        """
        Builds width/height dicts for label_name using count-based
        cross-pass merge logic.
        """
        cands_normal = annotations_normal.get(label_name, [])
        cands_rot    = annotations_rot.get(label_name, [])

        passes_with_hits = (1 if cands_normal else 0) + (1 if cands_rot else 0)
        total_matches    = cands_normal + cands_rot
        unique_vals      = sorted(set(total_matches))

        width_dict:  Optional[dict] = None
        height_dict: Optional[dict] = None

        if len(unique_vals) >= 2:
            w_dec = unique_vals[0]
            l_dec = unique_vals[-1]
            width_dict  = {"raw": str(w_dec), "decimal": w_dec}
            height_dict = {"raw": str(l_dec), "decimal": l_dec}
        elif len(unique_vals) == 1 and passes_with_hits >= 2:
            # Same value seen in both passes — treat as confirmed square unit
            v_dec = unique_vals[0]
            width_dict  = {"raw": str(v_dec), "decimal": v_dec}
            height_dict = {"raw": str(v_dec), "decimal": v_dec}
        elif len(unique_vals) == 1:
            w_dec = unique_vals[0]
            width_dict = {"raw": str(w_dec), "decimal": w_dec}

        return {"width": width_dict, "height": height_dict}

    dimensions = {
        "exposed_frame":     _extract_dim_type("exposed_frame"),
        "out_to_out_flange": _extract_dim_type("out_to_out_flange"),
        "glass":             _extract_dim_type("glass"),
        "rough_opening":     _extract_dim_type("rough_opening"),
    }

    # ------------------------------------------------------------------
    # Step 8: Check full text for revision block patterns.
    # Pattern A (with spaces): "EXPOSED FRAME CHANGED TO 57"-3/8 x 63""
    # Pattern B (compact):     "EXPOSED FRAME CHANGED TO 57"x63""
    # The revision block often has the confirmed final dimension.
    # Override exposed_frame dims if a revision block is found.
    # ------------------------------------------------------------------
    full_text = "\n".join(all_lines)

    revision_match = re.search(
        r'EXPOSED\s+FRAME\s+CHANGED\s+TO\s+'
        r'(\d+(?:[\-\s]\d+/\d+)?)"?\s*[xX]\s*(\d+(?:[\-\s]\d+/\d+)?)"?',
        full_text,
        re.IGNORECASE,
    )

    if not revision_match:
        revision_match = re.search(
            r'EXPOSED\s+FRAME\s+CHANGED\s+TO\s+(\d+)"?[xX\u00d7](\d+)"?',
            full_text,
            re.IGNORECASE,
        )

    if revision_match:
        rev_w = revision_match.group(1)
        rev_l = revision_match.group(2)
        rev_w_dec = _to_decimal(rev_w)
        rev_l_dec = _to_decimal(rev_l)
        if rev_w_dec is not None and rev_l_dec is not None:
            dimensions["exposed_frame"] = {
                "width":  {"raw": rev_w, "decimal": rev_w_dec},
                "height": {"raw": rev_l, "decimal": rev_l_dec},
            }

    # ------------------------------------------------------------------
    # Step 9: Determine needs_vision and confidence.
    # ------------------------------------------------------------------
    ef = dimensions["exposed_frame"]

    # needs_vision is True if exposed_frame width OR height is missing.
    needs_vision = (ef["width"] is None or ef["height"] is None)

    # Confidence scoring:
    #   HIGH   — all four dimension types have both width and height
    #   MEDIUM — exposed_frame has both width and height
    #   LOW    — exposed_frame is incomplete
    def _has_both(dim_dict: dict) -> bool:
        return dim_dict["width"] is not None and dim_dict["height"] is not None

    all_complete = all(
        _has_both(dimensions[k]) for k in LABEL_CONFIG
    )

    if all_complete:
        confidence = "HIGH"
    elif _has_both(ef) and not needs_vision:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # ------------------------------------------------------------------
    # Step 10: Assemble and return result.
    # ------------------------------------------------------------------
    return {
        "page_number":   page_number,
        "method":        "ocr_tesseract",
        "unit_letter":   unit_letter,
        "unit_qty":      unit_qty,
        "shape":         "RECTANGULAR",
        "panel_count":   None,
        "drawing_notes": [],
        "ocr_line_count": len(all_lines),
        "dimensions":    dimensions,
        "needs_vision":  needs_vision,
        "confidence":    confidence,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python ocr_plan_extractor.py <image_path> [page_number]")
        sys.exit(1)

    result = extract_plan_ocr(
        sys.argv[1],
        int(sys.argv[2]) if len(sys.argv) > 2 else 1,
    )
    print(json.dumps(result, indent=2))