"""
Rules Engine — Deterministic GFS Business Logic
Applies all known GFS rules to extracted data to produce final structured output.
Owner: Jignesh
Zero API calls. Pure Python only.

Sections:
    1.  parse_dim            — raw dimension string → decimal inches
    2.  detect_series        — product series from cover data
    3.  detect_glass_type    — glass type decision tree
    4.  detect_frame_type    — frame type from glass type + conditions
    5.  detect_cross_beam    — cross-beam / I-beam selection
    6.  apply_expedited_rules — expedited flag + HST removal
    7.  apply_hst_rules      — HST eligibility by weight
    8.  aggregate_units      — sqft / perimeter / weight / silicone per unit
    9.  compute_confidence   — HIGH / MEDIUM / LOW extraction confidence
    10. apply_all_rules      — master entry point
"""

import math
import re
from fractions import Fraction


# ── SECTION 1 — Dimension Parser ──────────────────────────────────────────────

def parse_dim(raw: str) -> float | None:
    """
    Convert a raw GFS dimension string to decimal inches.

    Supported formats:
        '47-5/8"'    → 47.625
        '63"'        → 63.0
        "3'-5\""     → 41.0
        '1600.20mm'  → 62.992... (mm / 25.4)
        '39.625'     → 39.625
        '5/8'        → 0.625

    Returns:
        float (decimal inches) or None if unparseable.
    """
    if not raw:
        return None

    raw = str(raw).strip()

    # ── mm conversion ──────────────────────────────────────────────────
    mm_match = re.match(r"^([\d.]+)\s*mm$", raw, re.IGNORECASE)
    if mm_match:
        try:
            return float(mm_match.group(1)) / 25.4
        except ValueError:
            return None

    # Strip trailing inch markers
    raw = raw.replace('"', '').replace("''", '').strip()

    # ── Feet + inches: 4'-3  or  4'3  or  4'-3-5/8 ────────────────────
    feet_inch = re.match(r"^(\d+)'[\-\s]?(.+)?$", raw)
    if feet_inch:
        feet = int(feet_inch.group(1)) * 12
        remainder = (feet_inch.group(2) or "0").strip()
        parsed_remainder = parse_dim(remainder)
        if parsed_remainder is None:
            parsed_remainder = 0.0
        return feet + parsed_remainder

    # ── Whole + fraction: 47-5/8 ───────────────────────────────────────
    mixed = re.match(r"^(\d+)-(\d+)/(\d+)$", raw)
    if mixed:
        whole = int(mixed.group(1))
        frac = Fraction(int(mixed.group(2)), int(mixed.group(3)))
        return whole + float(frac)

    # ── Plain fraction: 5/8 ───────────────────────────────────────────
    plain_frac = re.match(r"^(\d+)/(\d+)$", raw)
    if plain_frac:
        return float(Fraction(int(plain_frac.group(1)), int(plain_frac.group(2))))

    # ── Decimal or plain integer ───────────────────────────────────────
    try:
        return float(raw)
    except ValueError:
        return None


# ── SECTION 2 — Series Detection ─────────────────────────────────────────────

def detect_series(cover_data: dict) -> str:
    """
    Determine GFS product series from extracted cover data.

    Rule (from client call):
        Exterior walkable IGU always defaults to SERIES_2000.
        Only override if text explicitly says Series 1000 or Series 3000.

    Returns:
        "SERIES_1000" | "SERIES_2000" | "SERIES_3000" | "ECONOFRAME" | "UNKNOWN"
    """
    frame = (cover_data.get("frame") or {})
    series_raw = (frame.get("series") or "").upper()

    # Explicit text overrides take priority
    if "1000" in series_raw:
        return "SERIES_1000"
    if "3000" in series_raw or "CITYSCAPE" in series_raw:
        return "SERIES_3000"
    if "ECONOFRAME" in series_raw or "ECONO" in series_raw:
        return "ECONOFRAME"
    if "2000" in series_raw:
        return "SERIES_2000"

    # Default: exterior walkable IGU → SERIES_2000 (client call decision)
    spec = (cover_data.get("glass_specification") or {})
    glass_type = (spec.get("glass_type") or "").upper()
    location = (cover_data.get("project_header") or {})
    location_type = (location.get("location") or "").upper()

    is_exterior = "EXTERIOR" in location_type or "EXT" in location_type
    is_idu = "IGU" in glass_type

    if is_exterior and is_idu:
        return "SERIES_2000"

    return "UNKNOWN"


# ── SECTION 3 — Glass Type Detection ─────────────────────────────────────────

def detect_glass_type(cover_data: dict) -> dict:
    """
    Classify the glass type using a decision tree based on project conditions.

    Decision tree:
        NON_WALKABLE   — non-walkable mentioned
        STAIR_TREADS   — staircase mentioned
        GLASS_NO_IGU   — interior + open below, OR exterior + open below
        GLASS_2_0      — interior + wine cellar, OR exterior + conditioned + no wall condition,
                         OR IGU=YES + Nanodot top
        GLASS_IGU      — exterior + conditioned + wall condition
        EGR            — secondary flag if EGR mentioned anywhere

    Returns:
        dict: {"glass_type": str, "egr": bool}
    """
    spec = (cover_data.get("glass_specification") or {})
    header = (cover_data.get("project_header") or {})
    notes = (cover_data.get("notes") or "")

    # Build a combined searchable text blob (uppercase)
    blob = " ".join([
        str(spec.get("glass_makeup") or ""),
        str(spec.get("glass_type") or ""),
        str(spec.get("notes") or ""),
        str(header.get("location") or ""),
        str(header.get("application") or ""),
        str(notes),
    ]).upper()

    egr = "EGR" in blob

    # ── NON_WALKABLE ───────────────────────────────────────────────────
    if "NON-WALKABLE" in blob or "NON WALKABLE" in blob or "NONWALKABLE" in blob:
        return {"glass_type": "NON_WALKABLE", "egr": egr}

    # ── STAIR_TREADS ───────────────────────────────────────────────────
    if "STAIR" in blob or "STAIRCASE" in blob or "STAIR TREAD" in blob:
        return {"glass_type": "STAIR_TREADS", "egr": egr}

    is_interior = "INTERIOR" in blob or "INT." in blob
    is_exterior = "EXTERIOR" in blob or "EXT." in blob
    is_open_below = "OPEN BELOW" in blob or "OPEN-BELOW" in blob
    is_conditioned = "CONDITIONED" in blob
    is_wall_condition = "WALL CONDITION" in blob or "WALL COND" in blob
    is_pedestal = "PEDESTAL" in blob
    is_wine_cellar = "WINE CELLAR" in blob or "WINE ROOM" in blob
    has_idu = "IGU" in blob
    has_nanodot_top = ("NANODOT" in blob and "TOP" in blob)

    # ── GLASS_NO_IGU ──────────────────────────────────────────────────
    if is_open_below and (is_interior or is_exterior):
        return {"glass_type": "GLASS_NO_IGU", "egr": egr}

    # ── GLASS_2_0 ─────────────────────────────────────────────────────
    if is_interior and is_wine_cellar:
        return {"glass_type": "GLASS_2_0", "egr": egr}
    if is_exterior and is_conditioned and not is_wall_condition:
        return {"glass_type": "GLASS_2_0", "egr": egr}
    if has_idu and has_nanodot_top:
        return {"glass_type": "GLASS_2_0", "egr": egr}

    # ── GLASS_IGU ─────────────────────────────────────────────────────
    if is_exterior and is_conditioned and is_wall_condition:
        return {"glass_type": "GLASS_IGU", "egr": egr}

    # ── Fallback ──────────────────────────────────────────────────────
    return {"glass_type": "UNKNOWN", "egr": egr}


# ── SECTION 4 — Frame Type Detection ─────────────────────────────────────────

def detect_frame_type(glass_type: str, cover_data: dict) -> str | None:
    """
    Determine the correct GFS frame type from glass type and project conditions.

    Rules:
        GLASS_IGU  + wall condition  → "Series 1000 Recessed TB"
        GLASS_IGU  + pedestal        → "Series 1000 Pedestal TB"
        GLASS_2_0  + exterior        → "Series 2000 Recessed TB"  (DEFAULT — client call)
        GLASS_2_0  + pedestal        → "Series 2000 Pedestal TB"
        GLASS_NO_IGU + existing str  → "EconoFrame"
        GLASS_NO_IGU + new structure → "Perimeter Non-TB"
        GLASS_NO_IGU + new deck      → "Deck Perimeter"
        NON_WALKABLE + curb mounted  → "Series 3000"
        custom/ellipse shape         → "Perimeter Non-TB"
        glass only order             → None

    Returns:
        str frame type label or None for glass-only orders.
    """
    spec = (cover_data.get("glass_specification") or {})
    header = (cover_data.get("project_header") or {})
    notes = (cover_data.get("notes") or "")

    blob = " ".join([
        str(spec.get("glass_makeup") or ""),
        str(spec.get("notes") or ""),
        str(header.get("location") or ""),
        str(header.get("application") or ""),
        str(notes),
    ]).upper()

    is_exterior = "EXTERIOR" in blob or "EXT." in blob
    is_wall_condition = "WALL CONDITION" in blob or "WALL COND" in blob
    is_pedestal = "PEDESTAL" in blob
    is_existing_structure = "EXISTING STRUCTURE" in blob or "EXIST. STRUCT" in blob
    is_new_structure = "NEW STRUCTURE" in blob or "NEW STRUCT" in blob
    is_new_deck = "NEW DECK" in blob
    is_curb_mounted = "CURB MOUNT" in blob or "CURB-MOUNT" in blob
    is_custom_shape = "ELLIPSE" in blob or "CUSTOM SHAPE" in blob or "CURVED" in blob
    is_glass_only = "GLASS ONLY" in blob or "GLASS-ONLY" in blob

    # Glass only — no frame
    if is_glass_only:
        return None

    # Custom / ellipse shape
    if is_custom_shape:
        return "Perimeter Non-TB"

    if glass_type == "GLASS_IGU":
        if is_wall_condition:
            return "Series 1000 Recessed TB"
        if is_pedestal:
            return "Series 1000 Pedestal TB"
        return "Series 1000 Recessed TB"  # safe default for IGU

    if glass_type == "GLASS_2_0":
        if is_pedestal:
            return "Series 2000 Pedestal TB"
        return "Series 2000 Recessed TB"  # DEFAULT from client call

    if glass_type == "GLASS_NO_IGU":
        if is_existing_structure:
            return "EconoFrame"
        if is_new_deck:
            return "Deck Perimeter"
        if is_new_structure:
            return "Perimeter Non-TB"
        return "EconoFrame"  # safe default for no-IGU

    if glass_type == "NON_WALKABLE":
        if is_curb_mounted:
            return "Series 3000"
        return "Series 3000"  # non-walkable always Series 3000

    return None


# ── SECTION 5 — Cross-Beam Detection ─────────────────────────────────────────

def detect_cross_beam(frame_type: str | None, units: list) -> str | None:
    """
    Determine cross-beam / I-beam requirement.

    Rules:
        Single panel (total panels = 1)          → None
        Series 1000 + multi panel                → "Series 1000 I-Beam TB"
        Series 2000 + multi panel                → "Series 2000 I-Beam"
        Perimeter Non-TB + multi panel           → "Series 1000 I-Beam NTB"
        EconoFrame + multi panel                 → "EconoFrame 3.0"

    Returns:
        str cross-beam label or None.
    """
    if not frame_type:
        return None

    # Count total panels across all units
    total_panels = sum(int(u.get("quantity") or 1) for u in (units or []))

    if total_panels <= 1:
        return None

    ft_upper = frame_type.upper()

    if "SERIES 1000" in ft_upper:
        return "Series 1000 I-Beam TB"
    if "SERIES 2000" in ft_upper:
        return "Series 2000 I-Beam"
    if "PERIMETER NON-TB" in ft_upper or "PERIMETER NON TB" in ft_upper:
        return "Series 1000 I-Beam NTB"
    if "ECONOFRAME" in ft_upper:
        return "EconoFrame 3.0"

    return None


# ── SECTION 6 — Expedited Rules ───────────────────────────────────────────────

def apply_expedited_rules(cover_data: dict) -> tuple[dict, list]:
    """
    If order is EXPEDITED:
        - Force hst = False (overrides weight-based rule)
        - Add estimator flag

    Returns:
        (updated glass_specification dict, list of flag strings)
    """
    spec = dict(cover_data.get("glass_specification") or {})
    flags = []

    if spec.get("expedited"):
        if spec.get("hst"):
            spec["hst"] = False
            flags.append("HST removed — EXPEDITED orders cannot include Heat Soak Test")
        flags.append("EXPEDITED order — verify lead time with production")

    return spec, flags


# ── SECTION 7 — HST Rules ─────────────────────────────────────────────────────

def apply_hst_rules(weight_lbs: float, expedited: bool) -> bool:
    """
    Determine whether Heat Soak Test (HST) applies.

    Rules:
        expedited          → False (always)
        weight < 400       → False
        400 <= weight <= 800 → False (optional — flag for estimator)
        weight > 800       → True

    Args:
        weight_lbs: total computed weight of the glass unit in lbs
        expedited:  whether the order is expedited

    Returns:
        bool — True if HST should be applied
    """
    if expedited:
        return False
    if weight_lbs < 400:
        return False
    if 400 <= weight_lbs <= 800:
        # Optional range — estimator should review; default to False
        return False
    # weight > 800
    return True


# ── SECTION 8 — Unit Aggregation ─────────────────────────────────────────────

def aggregate_units(units: list) -> list:
    """
    Enrich each unit dict with computed fields.

    Computed fields added:
        computed_sqft          — (width_in * length_in * qty) / 144
        computed_perimeter_ft  — 2 * (width_in + length_in) / 12
        computed_weight_lbs    — computed_sqft * 15
        computed_silicone_tubes — ceil(computed_perimeter_ft / 8)

    Raw dimension strings are preserved exactly.

    Returns:
        list of enriched unit dicts
    """
    enriched = []

    for unit in (units or []):
        w_raw = unit.get("width")
        l_raw = unit.get("length")
        qty = int(unit.get("quantity") or 1)

        w_in = parse_dim(w_raw)
        l_in = parse_dim(l_raw)

        computed_sqft = None
        computed_perimeter_ft = None
        computed_weight_lbs = None
        computed_silicone_tubes = None

        if w_in is not None and l_in is not None:
            sqft_each = (w_in * l_in) / 144
            computed_sqft = round(sqft_each * qty, 2)
            computed_perimeter_ft = round(2 * (w_in + l_in) / 12, 2)
            computed_weight_lbs = round(computed_sqft * 15, 2)
            computed_silicone_tubes = math.ceil(computed_perimeter_ft / 8)

        enriched.append({
            **unit,
            "computed_sqft": computed_sqft,
            "computed_perimeter_ft": computed_perimeter_ft,
            "computed_weight_lbs": computed_weight_lbs,
            "computed_silicone_tubes": computed_silicone_tubes,
        })

    return enriched


# ── SECTION 9 — Confidence Scoring ───────────────────────────────────────────

def compute_confidence(cover_data: dict, plan_data: dict | None) -> str:
    """
    Compute overall extraction confidence.

    HIGH   — address + glass_makeup + at least one unit with both dimensions
             AND source = "gfs_shop_drawing"
    MEDIUM — some fields missing OR source is "architect_drawing" or "email"
    LOW    — address or glass_makeup missing OR zero units

    Returns:
        "HIGH" | "MEDIUM" | "LOW"
    """
    header = (cover_data.get("project_header") or {})
    spec = (cover_data.get("glass_specification") or {})
    units = (cover_data.get("units") or [])
    source = str(cover_data.get("source") or "").lower()

    has_address = bool(header.get("project_address"))
    has_glass = bool(spec.get("glass_makeup"))

    has_unit_dims = any(
        u.get("width") and u.get("length")
        for u in units
    )
    has_units = len(units) > 0 and has_unit_dims

    # LOW conditions
    if not has_address or not has_glass or not has_units:
        return "LOW"

    # MEDIUM conditions
    degraded_sources = {"architect_drawing", "email"}
    if source in degraded_sources:
        return "MEDIUM"

    # HIGH: all core fields present + trusted source
    if source == "gfs_shop_drawing":
        return "HIGH"

    # Source unknown but data complete → MEDIUM (conservative)
    return "MEDIUM"


# ── SECTION 10 — Master Apply Rules ──────────────────────────────────────────

def apply_all_rules(cover_data: dict, plan_data: dict | None = None) -> dict:
    """
    Master entry point. Applies all GFS business rules in order.

    Args:
        cover_data: output from cover_extractor.extract_cover()
        plan_data:  output from plan_extractor.extract_plan() or None

    Returns:
        Final structured dict ready for frontend / estimator display.
    """
    all_flags: list[str] = []

    # ── Series ────────────────────────────────────────────────────────
    series = detect_series(cover_data)
    if series == "UNKNOWN":
        all_flags.append("Series could not be determined — check frame spec manually")

    # ── Glass type ────────────────────────────────────────────────────
    glass_result = detect_glass_type(cover_data)
    glass_type = glass_result["glass_type"]
    egr = glass_result["egr"]

    if glass_type == "UNKNOWN":
        all_flags.append("Glass type could not be determined — manual review required")
    if egr:
        all_flags.append("EGR detected — confirm EGR specification with estimator")

    # ── Frame type ────────────────────────────────────────────────────
    frame_type = detect_frame_type(glass_type, cover_data)
    if frame_type is None:
        all_flags.append("No frame type assigned — possible glass-only order")

    # ── Expedited ─────────────────────────────────────────────────────
    spec, exp_flags = apply_expedited_rules(cover_data)
    cover_data["glass_specification"] = spec
    all_flags.extend(exp_flags)

    # ── HST ───────────────────────────────────────────────────────────
    units_raw = cover_data.get("units") or []
    units_enriched = aggregate_units(units_raw)
    cover_data["units"] = units_enriched

    # Compute total weight for HST rule
    total_weight = sum(
        (u.get("computed_weight_lbs") or 0.0) for u in units_enriched
    )
    expedited = bool(spec.get("expedited"))
    hst_applies = apply_hst_rules(total_weight, expedited)

    if 400 <= total_weight <= 800 and not expedited:
        all_flags.append(
            f"Total glass weight {total_weight:.1f} lbs is in optional HST range "
            f"(400–800 lbs) — estimator to confirm"
        )

    # Update HST in spec
    spec["hst"] = hst_applies
    cover_data["glass_specification"] = spec

    # ── Cross-beam ────────────────────────────────────────────────────
    cross_beam = detect_cross_beam(frame_type, units_enriched)
    if cross_beam and "SERIES 1000" in (frame_type or "").upper():
        all_flags.append(
            f"Cross-beam assigned: {cross_beam} — verify against engineering chart"
        )

    # ── Custom shape surcharge ────────────────────────────────────────
    custom_shape_surcharge = False
    if plan_data and plan_data.get("has_custom_shape"):
        custom_shape_surcharge = True
        shapes = plan_data.get("panel_shapes", [])
        all_flags.append(
            f"Custom shape detected: {shapes} — manual review required for pricing"
        )

    # ── Add-ons ───────────────────────────────────────────────────────
    glass_makeup = spec.get("glass_makeup")

    if isinstance(glass_makeup, list):
        glass_makeup = " ".join(str(x) for x in glass_makeup)

    glass_makeup_upper = str(glass_makeup or "").upper()    
    notes_upper = (cover_data.get("notes") or "").upper()
    combined_upper = glass_makeup_upper + " " + notes_upper

    add_ons = {
        "nanodot": "NANODOT" in combined_upper,
        "seeded_organic": "SEEDED" in combined_upper or "ORGANIC" in combined_upper,
        "back_paint": bool(spec.get("back_paint")),
        "hst": hst_applies,
        "custom_shape_surcharge": custom_shape_surcharge,
        "titanium_embeds": "TITANIUM" in combined_upper or "EMBED" in combined_upper,
        "krypton_gas": "KRYPTON" in combined_upper,
        "sentry_glass": None,   # requires manual confirmation
        "duty": None,           # requires manual confirmation
        "colored_interlayer": "COLOURED INTERLAYER" in combined_upper
                               or "COLORED INTERLAYER" in combined_upper
                               or "COLOUR INTERLAYER" in combined_upper,
    }

    # ── Confidence ────────────────────────────────────────────────────
    confidence = compute_confidence(cover_data, plan_data)

    # ── Frame block ───────────────────────────────────────────────────
    frame_block = {
        "series": series,
        "frame_type": frame_type,
        "frame_material": (cover_data.get("frame") or {}).get("frame_material"),
        "finish": (cover_data.get("frame") or {}).get("finish"),
    }

    return {
        "project_header": cover_data.get("project_header"),
        "glass_specification": cover_data.get("glass_specification"),
        "frame": frame_block,
        "series": series,
        "glass_type": glass_type,
        "egr": egr,
        "cross_beam": cross_beam,
        "units": cover_data.get("units"),
        "plan_data": plan_data,
        "add_ons": add_ons,
        "confidence": confidence,
        "flags": all_flags,
    }
