"""
frame_selector.py
-----------------
Pure-Python (stdlib only: re, typing) module that selects the correct
frame type, job type, duty flags, and related values from a signals dict.

No API calls. No azure/anthropic/httpx imports.
"""

import re
from typing import Any, Dict, List, Optional

# Job-type constants
TYPE_STANDARD   = "TYPE_STANDARD"
TYPE_MULTI_UNIT = "TYPE_MULTI_UNIT"
TYPE_ECONOFRAME = "TYPE_ECONOFRAME"
TYPE_GLASS_ONLY = "TYPE_GLASS_ONLY"
TYPE_CUSTOM     = "TYPE_CUSTOM"

# Canadian province/territory abbreviations used for duty detection
_CANADIAN_PROVINCES: List[str] = [
    "ON", "BC", "AB", "QC", "MB", "SK",
    "NS", "NB", "NL", "PE", "NT", "YT", "NU",
]


def _detect_canadian_address(address: str) -> bool:
    """
    Return True if the address string appears to be a Canadian address,
    based on the presence of a recognised province/territory abbreviation.
    """
    # Fix 10: Null-safe address handling
    address_upper = (address or "").upper()
    for province in _CANADIAN_PROVINCES:
        if (
            f" {province} " in f" {address_upper} "
            or address_upper.endswith(f" {province}")
            or f", {province}" in address_upper
        ):
            return True
    return False


def select_frame(signals: Dict[str, Any]) -> Dict[str, Any]:
    """
    Determine frame type, job type, duty, and related flags from job signals.

    Parameters
    ----------
    signals : dict
        series          : str   – "Series 1000" | "Series 2000" | "Series 3000" | "CityScape"
        exterior        : bool  – True if drawing is exterior application
        interior        : bool  – True if drawing is interior application
        walkable        : bool  – True if walkable unit
        existing_struct : bool  – True if retrofitting existing structure
        glass_only      : bool  – True if glass-only replacement
        shape           : str   – "RECTANGULAR" | "ELLIPSE" | "CUSTOM"
        address         : str   – full project address string
        expedited       : bool  – from cover extractor
        back_paint      : bool  – from cover extractor
        hst             : bool  – from cover extractor (heat-soak test)

    Returns
    -------
    dict
        job_type       : str         – one of the TYPE_* constants
        frame_type     : str | None  – exact label for excel_filler
        cross_beam     : str | None  – "I_Beam_TB" | "EconoFrame_30" | None
        duty           : bool
        duty_rate      : float       – 0.055 Canadian, 0.0 US/other
        backpaint      : bool
        man_hours      : int | None
        profit_margin  : float       – default 0.5
    """
    series: str      = signals.get("series", "Series 2000") or "Series 2000"
    exterior: bool   = bool(signals.get("exterior", True))
    interior: bool   = bool(signals.get("interior", False))
    walkable: bool   = bool(signals.get("walkable", False))
    existing_struct: bool = bool(signals.get("existing_struct", False))
    glass_only: bool      = bool(signals.get("glass_only", False))
    shape: str       = signals.get("shape", "RECTANGULAR") or "RECTANGULAR"
    address: str     = signals.get("address", "") or ""
    expedited: bool  = bool(signals.get("expedited", False))
    back_paint: bool = bool(signals.get("back_paint", False))

    # Fix 7: Case-insensitive series matching throughout
    series_lower: str = series.lower()

    # ------------------------------------------------------------------ #
    # Job type                                                             #
    # ------------------------------------------------------------------ #
    if shape in ("ELLIPSE", "CUSTOM"):
        job_type = TYPE_CUSTOM
    elif glass_only:
        job_type = TYPE_GLASS_ONLY
    elif "series 3000" in series_lower or "cityscape" in series_lower:
        job_type = TYPE_MULTI_UNIT
    elif existing_struct:
        job_type = TYPE_ECONOFRAME
    else:
        job_type = TYPE_STANDARD

    # ------------------------------------------------------------------ #
    # Frame type                                                           #
    # ------------------------------------------------------------------ #
    if job_type == TYPE_CUSTOM:
        frame_type: Optional[str] = None  # requires manual entry

    elif job_type == TYPE_GLASS_ONLY:
        frame_type = None

    elif job_type == TYPE_ECONOFRAME:
        frame_type = "Econoframe - lengths"

    elif job_type == TYPE_MULTI_UNIT:
        frame_type = "Series 3000 non walkable"

    elif exterior and walkable:
        # Fix 8: Wayne Conklin rule — Series 1000 exterior walkable must NOT
        # default to Series 2000. Only unknown/missing series defaults to Series 2000.
        if "2000" in series_lower:
            frame_type = "Series 2000 Recessed"
        elif "1000" in series_lower:
            frame_type = "Series 1000 Recessed thermally broken"
        else:
            # Wayne Conklin confirmed Series 2000 Recessed as default
            # when series is unknown or ambiguous for exterior walkable
            frame_type = "Series 2000 Recessed"

    elif exterior and not walkable:
        # Series 3000 is only selected when EXPLICITLY confirmed in the series field.
        # If walkable status is unknown (e.g. no glass makeup on cover), we must NOT
        # default to Series 3000 — use Series 2000 Recessed as the safe default.
        # Wayne Conklin confirmed Series 2000 Recessed as the correct safe default.
        if "3000" in series_lower or "cityscape" in series_lower:
            frame_type = "Series 3000 non walkable"
        elif "1000" in series_lower:
            frame_type = "Series 1000 Recessed thermally broken"
        else:
            # Safe default when walkable status unknown — estimator must confirm
            frame_type = "Series 2000 Recessed"

    elif interior and "series 1000" in series_lower:
        frame_type = "Series 1000 Recessed thermally broken"

    elif interior and "series 2000" in series_lower:
        frame_type = "Series 2000 Recessed"

    else:
        # Safest fallback when no condition matches — estimator must confirm
        # Series 2000 Recessed is the GFS default per Wayne Conklin
        frame_type = "Series 2000 Recessed"

    # ------------------------------------------------------------------ #
    # Cross beam                                                           #
    # ------------------------------------------------------------------ #
    if job_type == TYPE_ECONOFRAME:
        cross_beam: Optional[str] = "EconoFrame_30"
    elif job_type in (TYPE_GLASS_ONLY, TYPE_CUSTOM):
        cross_beam = None
    else:
        cross_beam = "I_Beam_TB"

    # ------------------------------------------------------------------ #
    # Duty — Canadian address detection                                    #
    # Fix 9: US commercial duty (15%) requires manual confirmation —      #
    # not auto-detected. Only Canadian duty is auto-applied here.         #
    # ------------------------------------------------------------------ #
    is_canadian: bool = _detect_canadian_address(address)
    duty: bool        = is_canadian
    duty_rate: float  = 0.055 if is_canadian else 0.0
    # NOTE: US commercial duty (15%) requires manual confirmation — not auto-detected

    # ------------------------------------------------------------------ #
    # Man hours defaults per job type                                      #
    # ------------------------------------------------------------------ #
    man_hours_map: Dict[str, Optional[int]] = {
        TYPE_GLASS_ONLY: None,
        TYPE_MULTI_UNIT: 6,
        TYPE_ECONOFRAME: 12,
        TYPE_STANDARD:   8,
        TYPE_CUSTOM:     16,
    }
    man_hours: Optional[int] = man_hours_map[job_type]

    # ------------------------------------------------------------------ #
    # Backpaint — expedited always triggers backpaint                      #
    # ------------------------------------------------------------------ #
    backpaint: bool = back_paint or expedited

    return {
        "job_type":      job_type,
        "frame_type":    frame_type,
        "cross_beam":    cross_beam,
        "duty":          duty,
        "duty_rate":     duty_rate,
        "backpaint":     backpaint,
        "man_hours":     man_hours,
        "profit_margin": 0.5,
    }
