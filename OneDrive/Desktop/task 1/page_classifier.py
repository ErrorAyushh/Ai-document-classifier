"""
Page Classifier Module — Non-AI Only
======================================
This module classifies PDF pages using pure Python keyword matching
on extracted text. It is strictly non-AI.

There are no API calls, no vision clients, no Azure, and no httpx imports.
Only the Python standard library modules `re` and `typing` are used.

Classification is done by the single public function:
- classify_page(text, page_number) : Classifies a single page by matching
  keywords in a fixed priority order and returns a structured result dict.

Page types returned:
- "SCANNED"      : Page has fewer than 50 characters of extractable text.
- "COVER"        : Page contains glass make-up and frame component keywords.
- "PLAN_VIEW"    : Page contains plan view or exposed frame keywords.
- "MGDS_CATALOG" : Page contains SkyFloor, MGDS, or GFS product keywords.
- "OTHER"        : Page does not match any known category.

Confidence levels:
- "HIGH"   : Strong keyword match found.
- "MEDIUM" : Weak or indirect keyword match found (e.g., only EXPOSED FRAME).
"""

import re
from typing import Literal

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
PageType = Literal["COVER", "PLAN_VIEW", "MGDS_CATALOG", "OTHER", "SCANNED"]
Confidence = Literal["HIGH", "MEDIUM"]


def classify_page(text: str, page_number: int) -> dict:
    """
    Classifies a single PDF page based on keyword matching of its extracted text.

    Classification is performed in strict priority order:
    1. SCANNED   — text is too short (< 50 chars after strip).
    2. COVER     — contains glass make-up AND frame component keywords.
    3. PLAN_VIEW — contains plan view titles or exposed frame keyword.
    4. MGDS_CATALOG — contains SkyFloor, MGDS, or GFS product keywords.
    5. OTHER     — no keywords matched.

    Parameters
    ----------
    text : str
        Raw extracted text from the PDF page.
    page_number : int
        The 1-indexed page number (passed through into the result dict).

    Returns
    -------
    dict
        A dict with the following structure:
        {
            "page_number": int,
            "page_type": "COVER" | "PLAN_VIEW" | "MGDS_CATALOG" | "OTHER" | "SCANNED",
            "confidence": "HIGH" | "MEDIUM",
            "method": "non_ai"
        }
    """

    # ------------------------------------------------------------------
    # Priority 1: SCANNED — not enough text to classify
    # ------------------------------------------------------------------
    if len(text.strip()) < 50:
        return {
            "page_number": page_number,
            "page_type": "SCANNED",
            "confidence": "HIGH",
            "method": "non_ai",
        }

    # ------------------------------------------------------------------
    # Uppercase once for all subsequent keyword checks
    # ------------------------------------------------------------------
    upper_text = text.upper()

    # ------------------------------------------------------------------
    # Priority 2: COVER
    # Must have BOTH:
    #   - "GLASS MAKE-UP" or "GLASS MAKEUP" or "GLAZING:"
    #   - "FRAME COMPONENTS" or "FRAME COMPONENT"
    # ------------------------------------------------------------------
    has_glass_makeup = (
        "GLASS MAKE-UP" in upper_text
        or "GLASS MAKEUP" in upper_text
        or "GLAZING:" in upper_text
    )
    has_frame_component = (
        "FRAME COMPONENTS" in upper_text
        or "FRAME COMPONENT" in upper_text
    )

    if has_glass_makeup and has_frame_component:
        return {
            "page_number": page_number,
            "page_type": "COVER",
            "confidence": "HIGH",
            "method": "non_ai",
        }

    # ------------------------------------------------------------------
    # Priority 3: PLAN_VIEW
    # HIGH confidence if any strong plan title keyword is present.
    #
    # For "PLAN - UNIT" specifically, a strict regex is used:
    #   "PLAN - UNIT" must be followed immediately by a single letter
    #   (A-Z), then optionally "- QTY" or end of meaningful content.
    #   Pattern: r'PLAN\s*-\s*UNIT\s+([A-Z])\s*(-\s*QTY|\s*$)'
    #
    #   This matches genuine GFS plan view titles such as:
    #     "PLAN - UNIT A - QTY 1"
    #     "PLAN - UNIT B"
    #   But does NOT match detail/template lines such as:
    #     "PLAN - UNIT A PERIMETER DETAIL"
    #     "PLAN - UNIT A HOLE LOCATION"
    #
    # "PLAN VIEW" and "PLAN-UNIT" are also accepted as HIGH confidence.
    #
    # MEDIUM confidence if only "EXPOSED FRAME" is present (no plan titles).
    # ------------------------------------------------------------------

    # Strict per-line check for "PLAN - UNIT <letter>" pattern
    plan_unit_match = False
    _strict_plan_unit = re.compile(
        r'PLAN\s*-\s*UNIT\s+([A-Z])\s*(-\s*QTY|\s*$)',
        re.IGNORECASE,
    )
    for line in upper_text.splitlines():
        if _strict_plan_unit.search(line):
            plan_unit_match = True
            break

    has_plan_title = (
        plan_unit_match
        or "PLAN VIEW" in upper_text
        or "PLAN-UNIT" in upper_text
    )

    has_exposed_frame = "EXPOSED FRAME" in upper_text

    if has_plan_title:
        return {
            "page_number": page_number,
            "page_type": "PLAN_VIEW",
            "confidence": "HIGH",
            "method": "non_ai",
        }

    if has_exposed_frame:
        return {
            "page_number": page_number,
            "page_type": "PLAN_VIEW",
            "confidence": "MEDIUM",
            "method": "non_ai",
        }

    # ------------------------------------------------------------------
    # Priority 4: MGDS_CATALOG
    # Matches any of:
    #   "SKYFLOOR", "MODULAR GLASS DECK", "MGDS"
    #   or regex pattern GFS followed by 2 digits (e.g. GFS12, GFS07)
    # ------------------------------------------------------------------
    has_mgds_keyword = (
        "SKYFLOOR" in upper_text
        or "MODULAR GLASS DECK" in upper_text
        or "MGDS" in upper_text
    )
    has_gfs_pattern = bool(re.search(r"GFS\d{2}", upper_text))

    if has_mgds_keyword or has_gfs_pattern:
        return {
            "page_number": page_number,
            "page_type": "MGDS_CATALOG",
            "confidence": "HIGH",
            "method": "non_ai",
        }

    # ------------------------------------------------------------------
    # Priority 5: OTHER — no keywords matched
    # ------------------------------------------------------------------
    return {
        "page_number": page_number,
        "page_type": "OTHER",
        "confidence": "HIGH",
        "method": "non_ai",
    }