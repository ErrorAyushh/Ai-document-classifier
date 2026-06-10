"""
Plan Extractor Module — Non-AI Only
======================================
This module extracts structured data from a GFS PLAN_VIEW page using regex
and keyword matching on PyMuPDF extracted text only.

It is strictly non-AI. There are no API calls, no vision clients,
no Azure, and no httpx imports. Only the Python standard library
modules `re` and `fractions` are used.

Primary public function:
- extract_plan(text, page_number) : Parses a plan view page's raw text and
  returns a structured dict containing unit info, exposed frame dimensions,
  shape, panel count, and drawing notes.

If any required fields (unit_letter, exposed_frame_width, exposed_frame_length)
cannot be extracted, the result will have "needs_vision": True, signalling
that a vision-based fallback should be used.

Dimension conversion is handled inline — this file is fully self-contained.
Do NOT import from any other project module (e.g. dim_converter).
"""

import re
from fractions import Fraction
from typing import Optional


# ---------------------------------------------------------------------------
# Dimension regex — matches all supported raw dimension formats
# ---------------------------------------------------------------------------
# Matches (in order of specificity):
#   47-5/8"      → fractional inches with hyphen
#   4'-3-1/2"    → feet + fractional inches
#   4'-3"        → feet + whole inches
#   63"          → whole inches with inch mark
#   39.625       → decimal (no unit mark)
#   47           → plain integer
DIM_PATTERN = re.compile(
    r"""\d+[\-\s]\d+/\d+"    # fractional inches: 47-5/8"
      | \d+'\s*\d+(?:[\-\s]\d+/\d+)?"  # feet-inches: 4'-3" or 4'-3-1/2"
      | \d+(?:\.\d+)?"       # decimal or whole with optional inch mark: 39.625 or 63"
      | \d+                  # plain integer fallback
    """,
    re.VERBOSE,
)


# ---------------------------------------------------------------------------
# Inline dimension converter (self-contained — do NOT import dim_converter)
# ---------------------------------------------------------------------------

def _to_decimal(raw: str) -> Optional[float]:
    """
    Converts a raw dimension string to a decimal float (in inches).

    Supported formats:
    - "47-5/8\""  → 47 + 5/8  = 47.625
    - "4'-3\""    → 4*12 + 3  = 51.0
    - "4'-3-1/2\""→ 4*12 + 3 + 1/2 = 51.5
    - "63\""      → 63.0
    - "39.625"    → 39.625
    - "47"        → 47.0

    Returns None if conversion fails.
    """
    try:
        # Normalise: strip surrounding whitespace
        s = raw.strip()

        # --- Feet-inches pattern: e.g. 4'-3-1/2" or 4'-3" ---
        feet_inch_frac = re.match(
            r"^(\d+)'\s*(\d+)[\-\s](\d+)/(\d+)\"?$", s
        )
        if feet_inch_frac:
            feet  = int(feet_inch_frac.group(1))
            whole = int(feet_inch_frac.group(2))
            num   = int(feet_inch_frac.group(3))
            den   = int(feet_inch_frac.group(4))
            return feet * 12 + whole + num / den

        feet_inch = re.match(r"^(\d+)'\s*(\d+)\"?$", s)
        if feet_inch:
            feet  = int(feet_inch.group(1))
            whole = int(feet_inch.group(2))
            return feet * 12 + whole

        # --- Fractional inches: e.g. 47-5/8" ---
        frac_inch = re.match(r"^(\d+)[\-\s](\d+)/(\d+)\"?$", s)
        if frac_inch:
            whole = int(frac_inch.group(1))
            num   = int(frac_inch.group(2))
            den   = int(frac_inch.group(3))
            return whole + num / den

        # --- Decimal or whole with optional inch mark: e.g. 63" or 39.625 ---
        decimal_or_whole = re.match(r"^(\d+(?:\.\d+)?)\"?$", s)
        if decimal_or_whole:
            return float(decimal_or_whole.group(1))

        return None

    except (ValueError, ZeroDivisionError):
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_unit_letter(text: str) -> Optional[str]:
    """
    Searches for unit letter patterns (A-Z):
    - "PLAN - UNIT A"
    - "PLAN-UNIT A"
    - "UNIT A"

    Returns the uppercase letter, or None if not found.
    """
    pattern = re.compile(
        r"PLAN\s*[-–]\s*UNIT\s+([A-Z])\b"   # PLAN - UNIT A  or  PLAN-UNIT A
        r"|PLAN-UNIT\s+([A-Z])\b"            # PLAN-UNIT A (no spaces)
        r"|\bUNIT\s+([A-Z])\b",              # UNIT A
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if match:
        # Return whichever group matched
        letter = match.group(1) or match.group(2) or match.group(3)
        return letter.upper()
    return None


def _extract_unit_qty(text: str) -> Optional[int]:
    """
    Searches for quantity patterns on the same line:
    - "QTY 10"
    - "QTY. 10"
    - "QUANTITY 10"

    Returns the integer quantity, or None if not found.
    """
    pattern = re.compile(
        r"(?:QTY\.?|QUANTITY)\s*[:\-]?\s*(\d+)",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if match:
        return int(match.group(1))
    return None


def _find_dims_near_label(text: str) -> list[str]:
    """
    Finds all dimension strings that appear near EXPOSED FRAME labels.

    Strategy:
    1. Locate every occurrence of "EXPOSED FRAME", "EXP. FRAME", "EXP FRAME"
       (case insensitive).
    2. For each occurrence, extract a window of text around it
       (up to 200 characters after the label).
    3. Collect all dimension strings found in those windows.

    Returns a list of raw dimension strings (may contain duplicates).
    """
    label_pattern = re.compile(
        r"EXP(?:OSED|\.?)?\s*FRAME",
        re.IGNORECASE,
    )

    found_dims: list[str] = []

    for label_match in label_pattern.finditer(text):
        start = label_match.end()
        # Take a window of 200 chars after the label
        window = text[start: start + 200]
        dims_in_window = DIM_PATTERN.findall(window)
        found_dims.extend(dims_in_window)

    return found_dims


def _extract_exposed_frame_dims(text: str) -> tuple[Optional[dict], Optional[dict]]:
    """
    Extracts exposed frame width and length from the text.

    - Searches for all dimension strings near EXPOSED FRAME labels.
    - Converts each to decimal.
    - Assigns the SMALLER decimal as width, LARGER as length.
    - Stores both raw string and decimal value.

    Returns (width_dict, length_dict) where each is:
        {"raw": str, "decimal": float}
    or (None, None) if EXPOSED FRAME label not found or no dims extracted.
    """
    label_pattern = re.compile(r"EXP(?:OSED|\.?)?\s*FRAME", re.IGNORECASE)
    if not label_pattern.search(text):
        # Label not present at all
        return None, None

    raw_dims = _find_dims_near_label(text)

    if not raw_dims:
        return None, None

    # Convert all to decimal, keep (raw, decimal) pairs where conversion succeeded
    converted: list[tuple[str, float]] = []
    for raw in raw_dims:
        dec = _to_decimal(raw)
        if dec is not None:
            converted.append((raw, dec))

    if not converted:
        return None, None

    if len(converted) == 1:
        # Only one dimension found — cannot distinguish width from length
        raw, dec = converted[0]
        single = {"raw": raw, "decimal": dec}
        return single, None

    # Sort by decimal value ascending → smaller = width, larger = length
    converted_sorted = sorted(converted, key=lambda x: x[1])

    width_raw,  width_dec  = converted_sorted[0]
    length_raw, length_dec = converted_sorted[-1]

    width_dict  = {"raw": width_raw,  "decimal": width_dec}
    length_dict = {"raw": length_raw, "decimal": length_dec}

    return width_dict, length_dict


def _extract_shape(text: str) -> str:
    """
    Detects the unit shape from keywords in the text.

    Priority:
    1. "ELLIPSE" or "OVAL"       → "ELLIPSE"
    2. "CUSTOM" or "IRREGULAR"   → "CUSTOM"
    3. Default                   → "RECTANGULAR"
    """
    upper = text.upper()

    if "ELLIPSE" in upper or "OVAL" in upper:
        return "ELLIPSE"
    if "CUSTOM" in upper or "IRREGULAR" in upper:
        return "CUSTOM"
    return "RECTANGULAR"


def _extract_panel_count(text: str, unit_qty: Optional[int]) -> Optional[int]:
    """
    Searches for panel count patterns:
    - "QTY X PANELS"  e.g. "QTY 4 PANELS"
    - "X PANELS"      e.g. "4 PANELS"

    Falls back to unit_qty if no explicit panel count found.

    Returns integer panel count or None.
    """
    # "QTY X PANELS" or "QTY. X PANELS"
    qty_panels = re.compile(
        r"(?:QTY\.?|QUANTITY)\s*[:\-]?\s*(\d+)\s*PANELS?",
        re.IGNORECASE,
    )
    match = qty_panels.search(text)
    if match:
        return int(match.group(1))

    # "X PANELS"
    x_panels = re.compile(r"(\d+)\s*PANELS?", re.IGNORECASE)
    match = x_panels.search(text)
    if match:
        return int(match.group(1))

    # Fallback to unit_qty
    return unit_qty


def _extract_drawing_notes(lines: list[str]) -> list[str]:
    """
    Collects all lines that start with "NOTE:" or "NOTES:" (case insensitive).

    Returns a list of the full note strings (with the label included).
    """
    note_pattern = re.compile(r"^\s*NOTES?\s*:", re.IGNORECASE)
    notes: list[str] = []

    for line in lines:
        if note_pattern.match(line):
            notes.append(line.strip())

    return notes


def _is_complete(
    unit_letter: Optional[str],
    exposed_frame_width: Optional[dict],
    exposed_frame_length: Optional[dict],
) -> bool:
    """
    Checks whether all required fields were successfully extracted.

    Required fields:
    - unit_letter
    - exposed_frame_width
    - exposed_frame_length

    Returns True if all are present, False if any is None.
    """
    return (
        unit_letter is not None
        and exposed_frame_width is not None
        and exposed_frame_length is not None
    )


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def extract_plan(text: str, page_number: int) -> dict:
    """
    Extracts structured data from a GFS PLAN_VIEW page using regex and
    keyword matching on PyMuPDF extracted text.

    Parameters
    ----------
    text : str
        Raw extracted text from the PDF plan view page (from PyMuPDF).
    page_number : int
        The 1-indexed page number of this plan view page.

    Returns
    -------
    dict
        Structured extraction result with the following shape:
        {
            "page_number": int,
            "method": "non_ai",
            "needs_vision": bool,
            "unit_letter": str | None,
            "unit_qty": int | None,
            "exposed_frame_width":  {"raw": str, "decimal": float} | None,
            "exposed_frame_length": {"raw": str, "decimal": float} | None,
            "shape": "RECTANGULAR" | "ELLIPSE" | "CUSTOM",
            "panel_count": int | None,
            "drawing_notes": list[str]
        }

        "needs_vision" is True if any required field could not be extracted:
        - unit_letter is None
        - exposed_frame_width is None
        - exposed_frame_length is None
    """
    lines = text.splitlines()

    # ------------------------------------------------------------------
    # Unit Info
    # ------------------------------------------------------------------
    unit_letter = _extract_unit_letter(text)
    unit_qty    = _extract_unit_qty(text)

    # ------------------------------------------------------------------
    # Exposed Frame Dimensions
    # ------------------------------------------------------------------
    exposed_frame_width, exposed_frame_length = _extract_exposed_frame_dims(text)

    # ------------------------------------------------------------------
    # Shape Detection
    # ------------------------------------------------------------------
    shape = _extract_shape(text)

    # ------------------------------------------------------------------
    # Panel Count
    # ------------------------------------------------------------------
    panel_count = _extract_panel_count(text, unit_qty)

    # ------------------------------------------------------------------
    # Drawing Notes
    # ------------------------------------------------------------------
    drawing_notes = _extract_drawing_notes(lines)

    # ------------------------------------------------------------------
    # Completeness check — set needs_vision flag
    # ------------------------------------------------------------------
    needs_vision = not _is_complete(unit_letter, exposed_frame_width, exposed_frame_length)

    # ------------------------------------------------------------------
    # Assemble and return result
    # ------------------------------------------------------------------
    return {
        "page_number":          page_number,
        "method":               "non_ai",
        "needs_vision":         needs_vision,
        "unit_letter":          unit_letter,
        "unit_qty":             unit_qty,
        "exposed_frame_width":  exposed_frame_width,
        "exposed_frame_length": exposed_frame_length,
        "shape":                shape,
        "panel_count":          panel_count,
        "drawing_notes":        drawing_notes,
    }