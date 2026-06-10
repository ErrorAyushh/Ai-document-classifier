"""
glass_selector.py
-----------------
Pure-Python (stdlib only: re, typing) module that selects the correct
glass type and related flags from a signals dict.

No API calls. No azure/anthropic/httpx imports.
"""

import re
from typing import Any, Dict, List


def select_glass(signals: Dict[str, Any]) -> Dict[str, Any]:
    """
    Determine glass type and related flags from extracted job signals.

    Parameters
    ----------
    signals : dict
        series       : str   – e.g. "Series 1000", "Series 2000", "Series 3000", "CityScape"
        walkable     : bool  – True if glass makeup indicates walkable surface
        interior     : bool  – True if location label says interior
        glass_only   : bool  – True if no frame in job (glass replacement only)
        glass_makeup : str   – raw glass makeup text from cover extractor
        hst          : bool  – heat-soak test flag (passed through)

    Returns
    -------
    dict
        glass_type     : str   – exact label for excel_filler
        nanodot        : bool
        seeded_organic : bool
        heat_soak      : bool  – passed through from signals["hst"]
    """
    # Fix 11: Case-insensitive series matching
    series_lower: str = (signals.get("series") or "").lower()

    walkable: bool   = bool(signals.get("walkable", False))
    interior: bool   = bool(signals.get("interior", False))
    glass_only: bool = bool(signals.get("glass_only", False))

    # Fix 12: Null-safe glass_makeup extraction
    gm: str = (signals.get("glass_makeup") or "").lower()

    # Fix 15: heat_soak passed through, defaults False if missing
    heat_soak: bool = bool(signals.get("hst", False))

    # ------------------------------------------------------------------ #
    # Glass type selection                                                 #
    # ------------------------------------------------------------------ #
    if glass_only:
        glass_type = "Glass - no IGU"

    # Fix 11: Case-insensitive Series 3000 / CityScape detection
    elif "3000" in series_lower or "cityscape" in series_lower:
        glass_type = "Non-Walkable glass"

    elif walkable and interior:
        glass_type = "Glass 2.0 system"

    elif walkable:
        glass_type = "Glass - with IGU"

    else:
        glass_type = "Glass - with IGU"  # default

    # ------------------------------------------------------------------ #
    # Derived flags                                                        #
    # ------------------------------------------------------------------ #
    # Fix 13: Nanodot detection — includes "nano-dot" variant
    nanodot: bool = any(kw in gm for kw in [
        "nanodot",
        "nano dot",
        "nano-dot",
    ])

    # Fix 14: Seeded organic detection — includes hyphenated and concatenated variants
    seeded_organic: bool = any(kw in gm for kw in [
        "seeded organic",
        "seeded-organic",
        "seededorganic",
    ])

    return {
        "glass_type":     glass_type,
        "nanodot":        nanodot,
        "seeded_organic": seeded_organic,
        "heat_soak":      heat_soak,
    }
