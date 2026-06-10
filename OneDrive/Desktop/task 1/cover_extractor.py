"""
Cover Extractor Module — Non-AI Only
======================================
This module extracts structured data from a GFS COVER page using regex
and keyword matching on PyMuPDF extracted text only.

It is strictly non-AI. There are no API calls, no vision clients,
no Azure, and no httpx imports. Only the Python standard library
modules `re` and `typing` are used.

Primary public function:
- extract_cover(text, page_number) : Parses a cover page's raw text and
  returns a structured dict containing project header, glass specification,
  frame details, and unit table data.

If any required fields (project_address, glass_makeup, units) cannot be
extracted, the result will have "needs_vision": True, signalling that a
vision-based fallback should be used.
"""

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Phrases that indicate a line belongs to the GFS company address block or
# a known contractor/architect name — not the project address.
# Extend this list as new contractor/architect names are encountered.
SKIP_PHRASES = [
    "glass flooring", "aaron way", "sparta",
    "conklin", "ernst", "6 aaron", "glazing"
]

# Keywords that indicate a line is part of a glass make-up specification.
GLASS_KEYWORDS = [
    "mm", "interlayer", "tempered", "hst", "laminated", "dg",
    "low iron", "spacer", "air", "pvb", "sgp", "layer", "glass"
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_project_address(lines: list[str]) -> Optional[str]:
    """
    Searches ALL lines for the project address using three strategies in order.
    Returns the first match found, or None if all strategies fail.

    Strategy 1 — Glazing anchor (most reliable for GFS drawings):
        Find the index of the line containing "Glazing:" (case insensitive).
        If found:
          Look at lines[index-3 : index] — the 3 lines before Glazing.
          Remove empty lines from this window.
          If 2 or more non-empty lines remain:
            second_last = the second-to-last non-empty line  (street line)
            last        = the last non-empty line            (city/state line)
            if second_last does not match SKIP_PHRASES:
              return second_last.strip() + " " + last.strip()
            else:
              return last.strip()
          If only 1 non-empty line remains:
            return that line.strip()

    Strategy 2 — GFS header anchor:
        Find the LAST occurrence of a line that contains
        "Glass Flooring Systems, Inc." and is under 40 characters.
        Look at the line immediately before it.
        If that line is not empty and does not match SKIP_PHRASES, return it.

    Strategy 3 — starts with digit:
        Search all lines for the first line that:
          - starts with a digit
          - contains a comma
          - is under 100 characters
          - does not match any phrase in SKIP_PHRASES
        If found, check the next non-empty line for a state abbreviation
        and combine if found. Return result.
    """

    state_abbr_pattern = re.compile(r"(?:^|\s)[A-Z]{2}(?:\s|$)")

    def _passes_skip_phrases(line: str) -> bool:
        """Returns True if the line does NOT contain any phrase in SKIP_PHRASES."""
        lower = line.lower()
        return not any(phrase in lower for phrase in SKIP_PHRASES)

    # ------------------------------------------------------------------
    # Strategy 1 — Glazing anchor
    # ------------------------------------------------------------------
    glazing_index = None
    for i, line in enumerate(lines):
        if "glazing:" in line.lower():
            glazing_index = i
            break

    if glazing_index is not None:
        start = max(0, glazing_index - 3)
        pre_glazing_lines = lines[start:glazing_index]

        # Remove empty lines from the window
        non_empty = [l.strip() for l in pre_glazing_lines if l.strip()]

        if len(non_empty) >= 2:
            second_last = non_empty[-2]
            last = non_empty[-1]
            if _passes_skip_phrases(second_last):
                return second_last + " " + last
            else:
                return last
        elif len(non_empty) == 1:
            return non_empty[0]

    # ------------------------------------------------------------------
    # Strategy 2 — GFS header anchor
    # ------------------------------------------------------------------
    gfs_last_index = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "glass flooring systems, inc." in stripped.lower() and len(stripped) < 40:
            gfs_last_index = i  # Keep updating to find the LAST occurrence

    if gfs_last_index is not None and gfs_last_index > 0:
        prev_line = lines[gfs_last_index - 1].strip()
        if prev_line and _passes_skip_phrases(prev_line):
            return prev_line

    # ------------------------------------------------------------------
    # Strategy 3 — starts with digit
    # ------------------------------------------------------------------
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) >= 100:
            continue
        if not stripped[0].isdigit():
            continue
        if "," not in stripped:
            continue
        if not _passes_skip_phrases(stripped):
            continue

        # Check next non-empty line for state abbreviation
        for j in range(i + 1, len(lines)):
            next_stripped = lines[j].strip()
            if next_stripped:
                if state_abbr_pattern.search(next_stripped):
                    return stripped + " " + next_stripped
                break  # Next non-empty line found but no state abbr — don't combine

        return stripped

    return None


def _extract_quote_number(text: str) -> Optional[str]:
    """
    Searches for patterns like:
    "Quote #", "Order #", "Quote No", "Job #"
    followed by alphanumeric characters.
    """
    pattern = re.compile(
        r"(Quote\s*#|Order\s*#|Quote\s*No\.?|Job\s*#)\s*([A-Za-z0-9\-]+)",
        re.IGNORECASE
    )
    match = pattern.search(text)
    if match:
        return match.group(2).strip()
    return None


def _extract_revision(text: str) -> Optional[str]:
    """
    Searches for "Rev " or "Rev." followed by a letter A-F.
    """
    pattern = re.compile(r"Rev\.?\s*([A-Fa-f])\b", re.IGNORECASE)
    match = pattern.search(text)
    if match:
        return match.group(1).upper()
    return None


def _extract_contractor(lines: list[str]) -> Optional[str]:
    """
    Finds the line after a "contractor:" or "client:" label (case insensitive).
    Also handles inline values: "Contractor: ABC Corp"
    """
    label_pattern = re.compile(r"^\s*(contractor|client)\s*:\s*(.*)$", re.IGNORECASE)
    for i, line in enumerate(lines):
        match = label_pattern.match(line)
        if match:
            inline_value = match.group(2).strip()
            if inline_value:
                return inline_value
            # Value on next non-empty line
            for j in range(i + 1, len(lines)):
                next_line = lines[j].strip()
                if next_line:
                    return next_line
    return None


def _extract_drawing_date(text: str) -> Optional[str]:
    """
    Searches for date patterns:
    - MM/DD/YYYY
    - Month DD, YYYY  (e.g. January 05, 2024)
    """
    # MM/DD/YYYY
    numeric_date = re.compile(r"\b(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])/(\d{4})\b")
    match = numeric_date.search(text)
    if match:
        return match.group(0)

    # Month DD, YYYY
    month_names = (
        "January|February|March|April|May|June|"
        "July|August|September|October|November|December"
    )
    written_date = re.compile(
        rf"\b({month_names})\s+(0?[1-9]|[12]\d|3[01]),?\s+(\d{{4}})\b",
        re.IGNORECASE
    )
    match = written_date.search(text)
    if match:
        return match.group(0)

    return None


def _extract_glass_makeup(lines: list[str]) -> Optional[str]:
    """
    Searches all lines for ALL occurrences of label lines in document order:
      - strip() equals "Glazing:" exactly, OR
      - line contains "Glass Make-up" or "Glass Makeup" (case insensitive).

    For EACH label found (not just the first one):
      Tries collecting lines after it using the same stop conditions.
      If collected list is not empty → returns the joined result immediately.
      If empty → continues to the next label occurrence.

    Stop conditions for each subsequent line after a label:
      stripped = line.strip()
      STOP if:
        - stripped is empty
        - stripped is one of: "YES", "NO", "N/A", "yes", "no", "n/a"
        - stripped == "Initial Here"
        - len(stripped) < 4 and not any(c.isdigit() for c in stripped)
      Otherwise ADD the line to collected list.

    If no label produces collected lines → returns None.
    """
    label_pattern = re.compile(
        r"glass\s*make-?up",
        re.IGNORECASE
    )

    stop_values = {"YES", "NO", "N/A", "yes", "no", "n/a", "Initial Here"}

    # Collect ALL label indices in document order
    label_indices = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "Glazing:" or label_pattern.search(stripped):
            label_indices.append(i)

    # Try each label in order; return on first one that yields collected lines
    for label_index in label_indices:
        collected = []
        for j in range(label_index + 1, len(lines)):
            stripped = lines[j].strip()

            # Stop conditions
            if not stripped:
                break
            if stripped in stop_values:
                break
            if len(stripped) < 4 and not any(c.isdigit() for c in stripped):
                break

            collected.append(stripped)

        if collected:
            return " + ".join(line.strip() for line in collected)

    return None


def _extract_expedited(text: str) -> bool:
    """Returns True if "EXPEDITED" appears anywhere in the text."""
    return bool(re.search(r"EXPEDITED", text, re.IGNORECASE))


def _extract_hst(text: str) -> bool:
    """Returns True if "HST" or "HEAT SOAK" appears in the text."""
    return bool(re.search(r"\bHST\b|HEAT\s+SOAK", text, re.IGNORECASE))


def _extract_back_paint(lines: list[str]) -> bool:
    """
    Searches all lines for a line containing "Back Paint" (case insensitive).

    When found, checks that same line and the next 3 lines for:
      - "YES" (case insensitive)
      - A colour name: "black", "white", "clear", "custom" (case insensitive)

    Returns True if any of those are found, otherwise False.
    If "Back Paint" is not found anywhere, returns False.
    """
    confirm_pattern = re.compile(
        r"\b(yes|black|white|clear|custom)\b",
        re.IGNORECASE
    )

    for i, line in enumerate(lines):
        if "back paint" in line.lower():
            # Check the label line itself and the next 3 lines
            window = lines[i: i + 4]
            for candidate in window:
                if confirm_pattern.search(candidate):
                    return True
            return False

    return False


def _extract_series(text: str) -> str:
    """
    Searches for series patterns: "Series 1000", "Series 2000",
    "Series 3000", or "CityScape".
    Defaults to "Series 2000" if none found (confirmed GFS default).
    """
    pattern = re.compile(r"(Series\s*(?:1000|2000|3000)|CityScape)", re.IGNORECASE)
    match = pattern.search(text)
    if match:
        # Normalise spacing
        return re.sub(r"\s+", " ", match.group(0).strip())
    return "Series 2000"


def _extract_frame_material(lines: list[str]) -> Optional[str]:
    """
    Finds the section of text between "Frame Components" and "Frame Finish"
    labels and searches only within that section for frame material keywords.

    Steps:
    1. Find index of line containing "Frame Components" (case insensitive).
    2. Find index of line containing "Frame Finish" (case insensitive).
    3. If both found:
         search only lines[frame_components_index:frame_finish_index]
       If only Frame Components found:
         search lines[frame_components_index:frame_components_index+20]
       If neither found:
         search all lines (fallback).

    Within the search window, look for these patterns in order:
      "Non-Thermally Broken" or "Non-TB" → return "Non-Thermally Broken"
      "Thermally Broken"                 → return "Thermally Broken"
      "Steel"                            → return "Steel"
      "Recessed Interior"                → return "Non-Thermally Broken"

    Returns the first match found, or None.
    """
    # ------------------------------------------------------------------
    # Locate anchor lines
    # ------------------------------------------------------------------
    frame_components_index = None
    frame_finish_index = None

    for i, line in enumerate(lines):
        lower = line.lower()
        if frame_components_index is None and "frame components" in lower:
            frame_components_index = i
        if frame_finish_index is None and "frame finish" in lower:
            frame_finish_index = i

    # ------------------------------------------------------------------
    # Define search window
    # ------------------------------------------------------------------
    if frame_components_index is not None and frame_finish_index is not None:
        window = lines[frame_components_index:frame_finish_index]
    elif frame_components_index is not None:
        window = lines[frame_components_index:frame_components_index + 20]
    else:
        window = lines  # fallback: search all lines

    # ------------------------------------------------------------------
    # Search window for material keywords in priority order
    # ------------------------------------------------------------------
    for line in window:
        lower = line.lower()
        if re.search(r"non-thermally\s+broken|non-tb", lower, re.IGNORECASE):
            return "Non-Thermally Broken"

    for line in window:
        if re.search(r"thermally\s+broken", line, re.IGNORECASE):
            return "Thermally Broken"

    for line in window:
        if re.search(r"\bsteel\b", line, re.IGNORECASE):
            return "Steel"

    for line in window:
        if re.search(r"recessed\s+interior", line, re.IGNORECASE):
            return "Non-Thermally Broken"

    return None


def _extract_units(lines: list[str]) -> list[dict]:
    """
    Scans all lines for unit table rows.

    Matches patterns like:
    - "Unit A", "Unit B", "UNIT A"  (explicit label)
    - A single letter A-Z at the start of a table-like row

    For each unit found, attempts to extract:
    - width  : raw string preserving original format e.g. '47-5/8"'
    - length : raw string preserving original format
    - quantity : integer

    Returns a list of dicts:
    [{"unit_id": str, "width": str|None, "length": str|None, "quantity": int|None}]
    """
    units = []
    seen_ids = set()

    # Pattern 1: explicit "Unit A" or "UNIT A" label
    explicit_unit = re.compile(
        r"\bUNIT\s+([A-Z])\b"
        r"(?:.*?(\d+[\-\s]\d+/\d+\"|\d+(?:\.\d+)?\"|\d+'\s*\d+\"?|\d+(?:\.\d+)?))?"  # width
        r"(?:.*?(\d+[\-\s]\d+/\d+\"|\d+(?:\.\d+)?\"|\d+'\s*\d+\"?|\d+(?:\.\d+)?))?"  # length
        r"(?:.*?\b(\d+)\b)?",                                                            # quantity
        re.IGNORECASE
    )

    # Pattern 2: single letter at start of a row (table row heuristic)
    # e.g. "A   47-5/8"   96"   10"
    table_row = re.compile(
        r"^\s*([A-Z])\s+"                                                                # unit id
        r"(\d+[\-\s]\d+/\d+\"|\d+(?:\.\d+)?\"|\d+'\s*\d+\"?|\d+(?:\.\d+)?)\s+"        # width
        r"(\d+[\-\s]\d+/\d+\"|\d+(?:\.\d+)?\"|\d+'\s*\d+\"?|\d+(?:\.\d+)?)\s+"        # length
        r"(\d+)"                                                                          # quantity
    )

    # Dimension pattern used for targeted extraction on explicit unit lines
    dim_pattern = re.compile(
        r"(\d+[\-\s]\d+/\d+\"|\d+(?:\.\d+)?\"|\d+'\s*\d+\"?|\d+(?:\.\d+)?)"
    )
    qty_pattern = re.compile(r"\b(\d{1,4})\b")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # --- Try table_row pattern first (most structured) ---
        tr_match = table_row.match(stripped)
        if tr_match:
            unit_id = tr_match.group(1).upper()
            if unit_id not in seen_ids:
                seen_ids.add(unit_id)
                units.append({
                    "unit_id": unit_id,
                    "width": tr_match.group(2).strip() if tr_match.group(2) else None,
                    "length": tr_match.group(3).strip() if tr_match.group(3) else None,
                    "quantity": int(tr_match.group(4)) if tr_match.group(4) else None,
                })
            continue

        # --- Try explicit "Unit X" pattern ---
        eu_match = explicit_unit.search(stripped)
        if eu_match:
            unit_id = eu_match.group(1).upper()
            if unit_id not in seen_ids:
                seen_ids.add(unit_id)

                # Extract all dimensions from the line
                dims = dim_pattern.findall(stripped)
                width = dims[0].strip() if len(dims) > 0 else None
                length = dims[1].strip() if len(dims) > 1 else None

                # Extract quantity: last standalone integer on the line
                # that is not part of a dimension string
                line_no_dims = dim_pattern.sub("", stripped)
                qty_matches = qty_pattern.findall(line_no_dims)
                quantity = int(qty_matches[-1]) if qty_matches else None

                units.append({
                    "unit_id": unit_id,
                    "width": width,
                    "length": length,
                    "quantity": quantity,
                })

    return units


def _is_complete(result: dict) -> bool:
    """
    Checks whether all required fields were successfully extracted.

    Required fields:
    - project_header.project_address
    - glass_specification.glass_makeup
    - units (at least one entry)

    Returns True if complete, False if any required field is missing.
    """
    if not result["project_header"].get("project_address"):
        return False
    if not result["glass_specification"].get("glass_makeup"):
        return False
    if not result["units"]:
        return False
    return True


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def extract_cover(text: str, page_number: int) -> dict:
    """
    Extracts structured data from a GFS COVER page using regex and
    keyword matching on PyMuPDF extracted text.

    Parameters
    ----------
    text : str
        Raw extracted text from the PDF cover page (from PyMuPDF).
    page_number : int
        The 1-indexed page number of this cover page.

    Returns
    -------
    dict
        Structured extraction result with the following shape:
        {
            "page_number": int,
            "method": "non_ai",
            "needs_vision": bool,
            "project_header": {
                "project_address": str | None,
                "quote_number":    str | None,
                "revision":        str | None,
                "contractor":      str | None,
                "drawing_date":    str | None
            },
            "glass_specification": {
                "glass_makeup": str | None,
                "expedited":    bool,
                "hst":          bool,
                "back_paint":   bool
            },
            "frame": {
                "series":         str,        # defaults to "Series 2000"
                "frame_material": str | None
            },
            "units": list[dict]              # each: {unit_id, width, length, quantity}
        }

        "needs_vision" is True if any required field could not be extracted:
        - project_address is None
        - glass_makeup is None
        - units list is empty
    """
    lines = text.splitlines()

    # ------------------------------------------------------------------
    # Project Header
    # ------------------------------------------------------------------
    project_address = _extract_project_address(lines)
    quote_number    = _extract_quote_number(text)
    revision        = _extract_revision(text)
    contractor      = _extract_contractor(lines)
    drawing_date    = _extract_drawing_date(text)

    # ------------------------------------------------------------------
    # Glass Specification
    # ------------------------------------------------------------------
    glass_makeup = _extract_glass_makeup(lines)
    expedited    = _extract_expedited(text)
    hst          = _extract_hst(text)
    back_paint   = _extract_back_paint(lines)

    # ------------------------------------------------------------------
    # Frame
    # ------------------------------------------------------------------
    series         = _extract_series(text)
    frame_material = _extract_frame_material(lines)

    # ------------------------------------------------------------------
    # Units Table
    # ------------------------------------------------------------------
    units = _extract_units(lines)

    # ------------------------------------------------------------------
    # Assemble result
    # ------------------------------------------------------------------
    result = {
        "page_number": page_number,
        "method": "non_ai",
        "needs_vision": False,          # determined below
        "project_header": {
            "project_address": project_address,
            "quote_number":    quote_number,
            "revision":        revision,
            "contractor":      contractor,
            "drawing_date":    drawing_date,
        },
        "glass_specification": {
            "glass_makeup": glass_makeup,
            "expedited":    expedited,
            "hst":          hst,
            "back_paint":   back_paint,
        },
        "frame": {
            "series":         series,
            "frame_material": frame_material,
        },
        "units": units,
    }

    # ------------------------------------------------------------------
    # Completeness check — set needs_vision flag
    # ------------------------------------------------------------------
    result["needs_vision"] = not _is_complete(result)

    return result