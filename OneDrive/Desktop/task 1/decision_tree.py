"""
decision_tree.py
----------------
Orchestrator that combines glass_selector and frame_selector to produce
a complete job_data dict ready for excel_filler.fill_workbook().

Pure-Python (stdlib only: re, typing). No API calls.
No azure/anthropic/httpx imports.
"""

import re
from typing import Any, Dict, List, Optional

from frame_selector import select_frame
from glass_selector import select_glass

# Keywords that indicate glass-only replacement (no frame)
_GLASS_ONLY_KEYWORDS: List[str] = [
    "glass only",
    "no frame",
]

# Keywords that indicate an existing structure / retrofit
_EXISTING_STRUCT_KEYWORDS: List[str] = [
    "existing",
    "retrofit",
    "econoframe",
    "ecoframe",
    "existing structure",
    "existing opening",
    "existing frame",
    "existing curb",
]

# Keywords that indicate interior location
_INTERIOR_KEYWORDS: List[str] = [
    "interior",
    "indoor",
    "inside",
    "internal",
    "main level",
    "floor level",
    "basement",
]

# Walkable keywords that apply regardless of series
_WALKABLE_UNCONDITIONAL: List[str] = [
    "nanodot",
    "nano dot",
    "gramercy",
    "hudson",
    "soho",
    "sevasa",
    "walkable",
]

# Walkable keywords that only apply when series is NOT Series 3000 / CityScape
_WALKABLE_CONDITIONAL: List[str] = [
    "anti-slip",
    "anti slip",
]

# Series values that are never walkable
_NON_WALKABLE_SERIES: List[str] = [
    "series 3000",
    "cityscape",
]


def _is_non_walkable_series(series: str) -> bool:
    """Return True if the series is one that is never walkable."""
    series_lower = (series or "").lower()
    return any(nw in series_lower for nw in _NON_WALKABLE_SERIES)


def _detect_walkable(glass_makeup: str, series: str) -> bool:
    """
    Determine if the glass makeup indicates a walkable surface.

    Rules:
    - If series is Series 3000 or CityScape → always False
    - "nanodot", "nano dot", "gramercy", "hudson", "soho", "sevasa", "walkable"
      → True regardless of series (but still blocked by non-walkable series above)
    - "anti-slip" / "anti slip" → True ONLY if series is NOT Series 3000 / CityScape
    """
    if _is_non_walkable_series(series):
        return False

    gm_lower = (glass_makeup or "").lower()

    # Unconditional walkable keywords (series already confirmed not 3000/CityScape)
    if any(kw in gm_lower for kw in _WALKABLE_UNCONDITIONAL):
        return True

    # Conditional walkable keywords (anti-slip only for non-3000/CityScape)
    if any(kw in gm_lower for kw in _WALKABLE_CONDITIONAL):
        return True

    return False


def build_job_data(cover_result: Dict[str, Any], plan_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build the complete job_data dict ready for excel_filler.fill_workbook().

    Parameters
    ----------
    cover_result : dict
        Raw output from the cover extractor.  Expected top-level keys:
            project_header      : dict
            glass_specification : dict
            frame               : dict
            units               : list

    plan_result : dict
        Raw output from plan_extractor / ocr_plan_extractor.  Expected keys:
            unit_letter          : str  (used as location fallback)
            drawing_notes        : list[str]
            shape                : str  ("RECTANGULAR" | "ELLIPSE" | "CUSTOM")
            exposed_frame_width  : dict | None  (has "decimal" key)
            exposed_frame_length : dict | None  (has "decimal" key)
            panel_count          : int | None
            unit_qty             : int | None

    Returns
    -------
    dict
        Complete job_data dict compatible with excel_filler.fill_workbook().
    """
    # ------------------------------------------------------------------ #
    # Unpack cover result sections                                         #
    # ------------------------------------------------------------------ #
    ph: Dict[str, Any] = cover_result.get("project_header") or {}
    gs: Dict[str, Any] = cover_result.get("glass_specification") or {}
    fr: Dict[str, Any] = cover_result.get("frame") or {}

    # Fix 6: Null-safe string extraction
    address: str = ph.get("project_address") or ""
    glass_makeup: str = gs.get("glass_makeup") or ""
    hst: bool = bool(gs.get("hst", False))
    expedited: bool = bool(gs.get("expedited", False))
    back_paint: bool = bool(gs.get("back_paint", False))

    # Fix 5: Series fallback — Wayne Conklin confirmed Series 2000 as GFS default
    series: str = fr.get("series") or "Series 2000"
    if not series.strip():
        series = "Series 2000"

    # Fix 4: frame_material for glass-only detection
    frame_material: Optional[str] = fr.get("frame_material") or None

    # ------------------------------------------------------------------ #
    # Fix 1: Walkable detection — series-aware                            #
    # ------------------------------------------------------------------ #
    walkable: bool = _detect_walkable(glass_makeup, series)

    # ------------------------------------------------------------------ #
    # Fix 2: Interior/exterior detection — checks notes AND unit_letter   #
    # ------------------------------------------------------------------ #
    drawing_notes_list: List[str] = plan_result.get("drawing_notes") or []
    drawing_notes_text: str = " ".join(drawing_notes_list)
    unit_letter: str = plan_result.get("unit_letter") or ""

    location_text: str = " ".join([
        drawing_notes_text,
        unit_letter,
    ]).lower()

    interior: bool = any(kw in location_text for kw in _INTERIOR_KEYWORDS)
    exterior: bool = not interior  # default exterior if no interior signal found

    # ------------------------------------------------------------------ #
    # Fix 3: Glass-only detection — smarter "replacement" logic           #
    # ------------------------------------------------------------------ #
    glass_makeup_lower: str = glass_makeup.lower()

    glass_only: bool = False
    if "glass only" in glass_makeup_lower:
        glass_only = True
    elif "no frame" in glass_makeup_lower:
        glass_only = True
    elif "replacement" in glass_makeup_lower and "frame" not in glass_makeup_lower:
        glass_only = True
    elif frame_material is None and not series.strip():
        # No frame material and no series → likely glass-only replacement
        glass_only = True

    # ------------------------------------------------------------------ #
    # Fix 4: Existing structure / retrofit detection — expanded keywords  #
    # ------------------------------------------------------------------ #
    combined_text: str = (glass_makeup + " " + drawing_notes_text).lower()
    existing_struct: bool = any(kw in combined_text for kw in _EXISTING_STRUCT_KEYWORDS)

    # ------------------------------------------------------------------ #
    # Shape from plan result                                               #
    # ------------------------------------------------------------------ #
    shape: str = plan_result.get("shape") or "RECTANGULAR"

    # ------------------------------------------------------------------ #
    # Assemble signals dict for selectors                                  #
    # ------------------------------------------------------------------ #
    signals: Dict[str, Any] = {
        "series":          series,
        "exterior":        exterior,
        "interior":        interior,
        "walkable":        walkable,
        "glass_only":      glass_only,
        "existing_struct": existing_struct,
        "shape":           shape,
        "address":         address,
        "glass_makeup":    glass_makeup,
        "hst":             hst,
        "expedited":       expedited,
        "back_paint":      back_paint,
    }

    # ------------------------------------------------------------------ #
    # Run selectors                                                        #
    # ------------------------------------------------------------------ #
    frame_result: Dict[str, Any] = select_frame(signals)
    glass_result: Dict[str, Any] = select_glass(signals)

    # ------------------------------------------------------------------ #
    # Build units list from plan_result dimensions                         #
    # ------------------------------------------------------------------ #
    w: Optional[Dict[str, Any]] = plan_result.get("exposed_frame_width")
    l: Optional[Dict[str, Any]] = plan_result.get("exposed_frame_length")

    units: List[Dict[str, Any]] = [
        {
            "location":          None,
            "drawing_number":    None,
            "width_inches":      w.get("decimal") if w else None,
            "length_inches":     l.get("decimal") if l else None,
            "panel_count":       plan_result.get("panel_count") or 1,
            "unit_count":        plan_result.get("unit_qty") or 1,
            "rafter_vertical":   None,
            "rafter_horizontal": None,
        }
    ]

    # ------------------------------------------------------------------ #
    # Derive project name from first segment of address                   #
    # ------------------------------------------------------------------ #
    project_address: str = ph.get("project_address") or ""
    project_name: str = project_address.split(",")[0] if project_address else ""

    # ------------------------------------------------------------------ #
    # Assemble and return final job_data dict                              #
    # ------------------------------------------------------------------ #
    return {
        "job_type":       frame_result["job_type"],
        "project_name":   project_name,
        "address":        address,
        "quote_number":   ph.get("quote_number") or "",
        "person_quoting": "",
        "contact_name":   "",
        "contact_phone":  "",
        "contact_email":  "",
        "project_type":   "Residential",
        "architect":      None,
        "homeowner":      None,
        "units":          units,
        "frame_type":     frame_result["frame_type"],
        "glass_type":     glass_result["glass_type"],
        "nanodot":        glass_result["nanodot"],
        "heat_soak":      glass_result["heat_soak"],
        "seeded_organic": glass_result["seeded_organic"],
        "duty":           frame_result["duty"],
        "duty_rate":      frame_result["duty_rate"],
        "backpaint":      frame_result["backpaint"],
        "cross_beam":     frame_result["cross_beam"],
        "man_hours":      frame_result["man_hours"],
        "profit_margin":  frame_result["profit_margin"],
    }
