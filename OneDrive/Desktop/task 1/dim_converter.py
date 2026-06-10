# -*- coding: utf-8 -*-
"""
dim_converter.py -- GFS Dimension String Normalizer
====================================================
Converts raw dimension strings from GFS shop drawings into decimal inches.

Public API
----------
convert(raw: str) -> float
    Strict converter. Raises ValueError on unrecognised format.

safe_convert(raw: str | None) -> dict
    Safe wrapper around convert(). Never raises.
    Returns:
        {"raw": str, "decimal": float, "ok": True,  "error": None, "warning": None}
        {"raw": str, "decimal": float, "ok": True,  "error": None, "warning": "<msg>"}
        {"raw": str, "decimal": None,  "ok": False, "error": str,  "warning": None}
        {"raw": None,"decimal": None,  "ok": False, "error": "null input", "warning": None}

convert_dim_group(dims: dict | None) -> dict | None
    Walks a dims sub-dict. For every *_raw key, calls safe_convert() and
    writes the result into the sibling *_in key.
    Appends failures AND dual-format mismatch warnings into dims["_conversion_warnings"].

Supported formats (all from GFS spec)
--------------------------------------
    84              plain integer inches
    37.5            plain decimal inches
    84"             with trailing inch mark
    3'-5"           feet + whole inches
    7'-3 13/16"     feet + inches + plain fraction
    10'-9 3/4"      feet + inches + plain fraction
    39 5/8          whole inches + space + plain fraction
    88 1/2          whole inches + space + plain fraction
    39⅝             integer + unicode fraction
    82¹³/₁₆         integer + superscript/subscript fraction
    4'-9⅝"          feet + inches + unicode fraction
    1600.20 mm      millimetre value
    60 [5'-0"]      dual-format (primary=inches, alternate=feet-inches)
    57 7/8 [4'-9 7/8"]   dual-format with fractional primary and alternate
"""

import re
from fractions import Fraction

# ─────────────────────────────────────────────────────────────────────────────
# Lookup tables
# ─────────────────────────────────────────────────────────────────────────────

UNICODE_FRACTIONS = {
    '\u215b': Fraction(1, 8),  # ⅛
    '\u00bc': Fraction(1, 4),  # ¼
    '\u215c': Fraction(3, 8),  # ⅜
    '\u00bd': Fraction(1, 2),  # ½
    '\u215d': Fraction(5, 8),  # ⅝
    '\u00be': Fraction(3, 4),  # ¾
    '\u215e': Fraction(7, 8),  # ⅞
}

SUPERSCRIPT_DIGITS = str.maketrans('\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079',
                                    '0123456789')
SUBSCRIPT_DIGITS   = str.maketrans('\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089',
                                    '0123456789')

# ─────────────────────────────────────────────────────────────────────────────
# Module-level pending warning
# convert() writes here when a dual-format mismatch is detected.
# safe_convert() clears this before each call, then reads any written value.
# Direct convert() callers will not see these warnings (by design).
# ─────────────────────────────────────────────────────────────────────────────
_pending_warning: list = []


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_superscript_fraction(text: str) -> Fraction:
    r"""
    Convert a superscript/subscript fraction like \u00b9\u00b3/\u2081\u2086 to Fraction.

    Non-technical: Some drawings use tiny raised numbers (superscripts) and lowered numbers
    (subscripts) for fractions like ¹³/₁₆. This translates those special characters back
    into regular numbers (like 13/16).

    Technical: Translates characters using pre-defined translation tables (SUPERSCRIPT_DIGITS,
    SUBSCRIPT_DIGITS) to normal characters, matching with regex '(\d+)/(\d+)', and returning
    a Fraction object.
    """
    text = text.translate(SUPERSCRIPT_DIGITS).translate(SUBSCRIPT_DIGITS)
    match = re.match(r'(\d+)/(\d+)', text)
    if match:
        return Fraction(int(match.group(1)), int(match.group(2)))
    return Fraction(0)


def _parse_fraction_part(text: str) -> Fraction:
    """
    Parse a fraction string: '13/16', a unicode fraction char, or superscript.

    Non-technical: Detects whether a fraction is written as a normal fraction (13/16),
    a single unicode character (like ⅝), or superscript/subscript characters, and converts
    it to an exact mathematical fraction.

    Technical: Checks matching unicode tables, searches for superscript patterns, or parses
    plain fraction string structures, falling back to Fraction(0) if unrecognized.
    """
    text = text.strip()
    # Unicode single-char fraction
    for ch, val in UNICODE_FRACTIONS.items():
        if ch in text:
            return val
    # Superscript fraction
    sup_match = re.search(r'[\u2070-\u2079\u00b9\u00b2\u00b3]+/[\u2080-\u2089]+', text)
    if sup_match:
        return _parse_superscript_fraction(sup_match.group())
    # Plain fraction (e.g. 13/16)
    plain_match = re.match(r'(\d+)/(\d+)', text)
    if plain_match:
        return Fraction(int(plain_match.group(1)), int(plain_match.group(2)))
    return Fraction(0)


# ─────────────────────────────────────────────────────────────────────────────
# Core converter — strict, raises on failure
# ─────────────────────────────────────────────────────────────────────────────

def convert(raw: str) -> float:
    r"""
    Convert a raw GFS dimension string to decimal inches.

    Raises ValueError if the format is not recognised.

    Dual-format strings (e.g. "60 [5'-0\"]" or "57 7/8 [4'-9 7/8\"]") are
    pre-processed: the primary value (before the bracket) is tried first;
    if it fails, the alternate is used; if both succeed and match, the value
    is returned silently; if both succeed but differ, the primary is returned
    and a message is appended to _pending_warning (read by safe_convert).

    Non-technical: This is the strict converter. It tries to parse various dimension formats
    (feet, inches, fractions, millimeters, and even dual-format strings showing both inches
    and feet-inches like '60 [5\'-0"]'). If both parts of a dual-format string disagree, it
    uses the first one and flags a warning.

    Technical: Pre-processes dual-format brackets, tries MM regex, superscript regex, unicode
    fractions (only if no feet marker ' is present), and standard feet-inch formats, falling 
    back to basic inches. Uses the Fraction class to prevent rounding errors.
    """
    raw = raw.strip()

    # ── Dual-format pre-processor ─────────────────────────────────────────────
    # Matches:  primary [alternate]
    # Examples: "60 [5'-0\"]"   "57 7/8 [4'-9 7/8\"]"   "48 [4'-0\"]"
    dual_match = re.match(r'^(.+?)\s*\[([^\]]+)\]\s*$', raw)
    if dual_match:
        primary_str   = dual_match.group(1).strip()
        alternate_str = dual_match.group(2).strip()

        p_val = a_val = None
        try:
            p_val = convert(primary_str)
        except Exception:
            pass
        try:
            a_val = convert(alternate_str)
        except Exception:
            pass

        if p_val is not None and a_val is not None:
            # Both parsed — check agreement (tolerance: 0.02 inches ~ 0.5 mm)
            if abs(p_val - a_val) < 0.02:
                return p_val  # Clean match — no warning
            # Values differ — return primary but flag the discrepancy
            _pending_warning.append(
                f"Dual-format mismatch: primary {primary_str!r}={p_val:.4f}in, "
                f"alternate {alternate_str!r}={a_val:.4f}in; using primary"
            )
            return p_val
        elif p_val is not None:
            return p_val   # Only primary parsed
        elif a_val is not None:
            return a_val   # Only alternate parsed
        else:
            raise ValueError(
                f"Cannot parse dual-format dimension: {raw!r} "
                f"(primary={primary_str!r}, alternate={alternate_str!r})"
            )

    # ── MM conversion ────────────────────────────────────────────────────────
    mm_match = re.match(r'([\d.]+)\s*mm', raw, re.IGNORECASE)
    if mm_match:
        return round(float(mm_match.group(1)) / 25.4, 10)

    # ── Superscript fraction: 82\u00b9\u00b3/\u2081\u2086 ──────────────────────────────────────────
    sup_frac_pattern = re.compile(
        r'^(\d+)?'
        r'([\u2070\u00b9\u00b2\u00b3\u2074-\u2079]+/[\u2080-\u2089]+)'
        r'$'
    )
    sup_match = sup_frac_pattern.match(raw)
    if sup_match:
        whole = int(sup_match.group(1)) if sup_match.group(1) else 0
        frac  = _parse_superscript_fraction(sup_match.group(2))
        return float(whole + frac)

    # ── Unicode fraction: 39\u215d (guard: skip if feet-marker present) ───────
    if "'" not in raw:
        for ch, val in UNICODE_FRACTIONS.items():
            if ch in raw:
                parts = raw.split(ch)
                whole = int(parts[0]) if parts[0].strip() else 0
                return float(whole + val)

    # ── Feet-inches patterns ──────────────────────────────────────────────────
    # Pattern A: 7'-3 13/16"  or  3'-5"  (feet + integer inches + optional plain fraction)
    fi_std = re.match(r"^(\d+)'-(\d+)(?:\s+(\d+/\d+))?\"?$", raw)
    if fi_std:
        feet   = int(fi_std.group(1))
        inches = int(fi_std.group(2))
        frac   = Fraction(fi_std.group(3)) if fi_std.group(3) else Fraction(0)
        return float(feet * 12 + inches + frac)

    # Pattern B: 4'-9\u215d"  (feet + whole inches + unicode fraction)
    fi_uni = re.match(r"^(\d+)'-(\d+)\s*([^\d\s\"]+)\"?$", raw)
    if fi_uni:
        feet   = int(fi_uni.group(1))
        inches = int(fi_uni.group(2))
        frac   = _parse_fraction_part(fi_uni.group(3))
        return float(feet * 12 + inches + frac)

    # Pattern C: 5'-0"  or  5'  (feet only, zero or missing inches)
    fi_only = re.match(r"^(\d+)'-?\s*\"?$", raw)
    if fi_only:
        return float(int(fi_only.group(1)) * 12)

    # ── Plain inches + fraction: "39 13/16" ──────────────────────────────────
    plain_frac = re.match(r'(\d+)\s+(\d+/\d+)', raw)
    if plain_frac:
        return float(int(plain_frac.group(1)) + Fraction(plain_frac.group(2)))

    # ── Plain integer or decimal inches: "84" or "84.0" ──────────────────────
    plain = re.match(r'^([\d.]+)"?$', raw)
    if plain:
        return float(plain.group(1))

    raise ValueError(f"Cannot parse dimension: {raw!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Safe wrapper — never raises, returns structured result
# ─────────────────────────────────────────────────────────────────────────────

def safe_convert(raw) -> dict:
    """
    Safe wrapper around convert(). Never raises.

    Args:
        raw: raw dimension string from model output (or None)

    Returns a dict:
        {
            "raw":     original string (or None),
            "decimal": float if successful, else None,
            "ok":      True | False,
            "error":   None | error message string,
            "warning": None | dual-format mismatch message string
        }

    The "warning" field is populated when a dual-format string (e.g. "60 [5'-0\"]")
    was parsed successfully but its two representations disagreed. The decimal
    value is still returned; the warning is surfaced in _conversion_warnings.

    Non-technical: A safety blanket around the strict converter. Instead of crashing
    the program when it sees bad data (like text or drawings garbage), it safely returns
    an error description, letting other pages continue processing.

    Technical: Clears pending warnings, handles null/int/float inputs, executes 
    convert(), catches Arithmetic/Value errors, and returns a structured output dict.
    """
    global _pending_warning

    # ── Null input ────────────────────────────────────────────────────────────
    if raw is None:
        return {"raw": None, "decimal": None, "ok": False, "error": "null input", "warning": None}

    # ── Already a number (e.g. float from a previous pass) ───────────────────
    if isinstance(raw, (int, float)):
        return {"raw": str(raw), "decimal": float(raw), "ok": True, "error": None, "warning": None}

    raw_str = str(raw).strip()
    if not raw_str:
        return {"raw": raw_str, "decimal": None, "ok": False, "error": "empty string", "warning": None}

    # Clear the pending warning buffer before each conversion attempt
    _pending_warning.clear()

    try:
        decimal_val = convert(raw_str)
        warning = _pending_warning[0] if _pending_warning else None
        return {
            "raw":     raw_str,
            "decimal": decimal_val,
            "ok":      True,
            "error":   None,
            "warning": warning,
        }
    except (ValueError, ZeroDivisionError, ArithmeticError) as exc:
        return {"raw": raw_str, "decimal": None, "ok": False, "error": str(exc), "warning": None}
    except Exception as exc:
        return {"raw": raw_str, "decimal": None, "ok": False,
                "error": f"unexpected: {exc}", "warning": None}


# ─────────────────────────────────────────────────────────────────────────────
# Dims-group converter — walks a nested dims dict
# ─────────────────────────────────────────────────────────────────────────────

def convert_dim_group(dims: dict | None) -> dict | None:
    """
    Walk a dim sub-dict and normalise every *_raw field.

    For each *_raw key found:
        1. Calls safe_convert(raw_value)
        2. Writes decimal result into the sibling *_in key
        3. On parse failure, leaves *_in as None and appends a warning
        4. On dual-format mismatch (ok=True but warning set), appends the warning

    Args:
        dims: a dict like plan_dims, section_dims, or glass_detail_dims
              (or None — returns None immediately)

    Returns:
        The same dict, mutated in place, with *_in fields populated.
        Adds "_conversion_warnings" key (list of strings, empty if clean).

    Non-technical: Iterates through a group of measurements (like width, height, rough opening)
    belonging to a single page, formats all of them, and gathers any warning messages about
    parsing issues.

    Technical: Scans dict keys for '_raw' suffixes, derives the '_in' sibling key, converts
    values via safe_convert(), sets the float value or null, and appends warnings under
    `_conversion_warnings`.
    """
    if dims is None:
        return None

    warnings = []

    for key in list(dims.keys()):
        if not key.endswith("_raw"):
            continue

        # Derive sibling _in key: "out_to_out_flange_width_raw" -> "_in"
        base   = key[:-4]
        in_key = base + "_in"

        raw_val = dims.get(key)
        result  = safe_convert(raw_val)

        if result["ok"]:
            dims[in_key] = result["decimal"]
            # Surface dual-format mismatch warnings even on success
            if result.get("warning"):
                warnings.append(f"{in_key}: {result['warning']}")
        else:
            dims[in_key] = None
            if raw_val is not None:
                warnings.append(
                    f"{in_key}: parse failed for {raw_val!r} -- {result['error']}"
                )

    dims["_conversion_warnings"] = warnings
    return dims


# ─────────────────────────────────────────────────────────────────────────────
# CLI sanity check — run: venv\Scripts\python.exe -X utf8 dim_converter.py
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("convert() strict tests")
    print("=" * 60)

    strict_tests = [
        # (input,                          expected_decimal)
        ("84",                             84.0),
        ("37",                             37.0),
        ("37.5",                           37.5),
        ('84"',                            84.0),
        ("3'-5\"",                         41.0),
        ("7'-3 13/16\"",                   87.8125),
        ("2'-10 13/16\"",                  34.8125),
        ("10'-9 3/4\"",                    129.75),
        ("39\u215d",                        39.625),
        ("82\u00b9\u00b3/\u2081\u2086",    82.8125),
        ("1600.20 mm",                     round(1600.20 / 25.4, 10)),
        ("55 3/16",                        55.1875),
        ("88 1/2",                         88.5),
        ("61 1/16",                        61.0625),
        ("57 7/8",                         57.875),
        ("54 5/8",                         54.625),
        ("57\u215e",                        57.875),
        ("54\u215d",                        54.625),
        ("60",                             60.0),
        ("5'-0\"",                         60.0),
        ("4'-9\u215d\"",                   57.625),
        # ── Dual-format ────────────────────────────────────────────────────
        ("60 [5'-0\"]",                    60.0),
        ("48 [4'-0\"]",                    48.0),
        ("57 7/8 [4'-9 7/8\"]",            57.875),
        ("88 1/2 [7'-4 1/2\"]",            88.5),
        ("61 1/16 [5'-1 1/16\"]",          61.0625),
    ]

    all_ok = True
    for raw, expected in strict_tests:
        try:
            result = convert(raw)
            ok = abs(result - expected) < 1e-6
            status = "PASS" if ok else f"FAIL (got {result}, expected {expected})"
            if not ok:
                all_ok = False
        except ValueError as e:
            status = f"FAIL RAISED: {e}"
            all_ok = False
        print(f"  {raw!r:40} -> {status}")

    print()
    print("=" * 60)
    print("safe_convert() edge-case tests")
    print("=" * 60)

    safe_tests = [
        # (input,                    expect_ok,  expect_decimal,   expect_warning)
        (None,                       False,      None,             False),
        ("",                         False,      None,             False),
        ("??garbage??",              False,      None,             False),
        ("84",                       True,       84.0,             False),
        ("88 1/2",                   True,       88.5,             False),
        (84.0,                       True,       84.0,             False),
        ("1417.64 mm",               True,       round(1417.64/25.4, 10), False),
        # Dual-format — clean match (no warning)
        ("60 [5'-0\"]",              True,       60.0,             False),
        ("48 [4'-0\"]",              True,       48.0,             False),
        ("57 7/8 [4'-9 7/8\"]",      True,       57.875,           False),
    ]

    for raw, expect_ok, expect_decimal, expect_warning in safe_tests:
        res = safe_convert(raw)
        ok_match = res["ok"] == expect_ok
        decimal_match = (
            (res["decimal"] is None and expect_decimal is None) or
            (expect_decimal is not None and res["decimal"] is not None
             and abs(res["decimal"] - expect_decimal) < 1e-6)
        )
        warning_match = bool(res.get("warning")) == expect_warning
        passed = ok_match and decimal_match and warning_match
        status = "PASS" if passed else f"FAIL got {res}"
        if not passed:
            all_ok = False
        print(f"  {str(raw)!r:40} -> {status}")

    print()
    print("=" * 60)
    print("convert_dim_group() test — dual-format + bad value")
    print("=" * 60)

    sample_plan = {
        "out_to_out_flange_width_raw":  "60 [5'-0\"]",   # dual-format, clean
        "out_to_out_flange_width_in":   None,
        "out_to_out_flange_height_raw": "57 7/8 [4'-9 7/8\"]",  # dual-format, clean
        "out_to_out_flange_height_in":  None,
        "exposed_frame_width_raw":      None,
        "exposed_frame_width_in":       None,
        "glass_width_raw":              "??bad??",
        "glass_width_in":               None,
    }

    result_group = convert_dim_group(sample_plan)
    assert result_group["out_to_out_flange_width_in"]  == 60.0,    "dual 60 failed"
    assert result_group["out_to_out_flange_height_in"] == 57.875,  "dual 57 7/8 failed"
    assert result_group["exposed_frame_width_in"]      is None,    "null raw should stay null"
    assert result_group["glass_width_in"]              is None,    "bad raw should null out"
    assert len(result_group["_conversion_warnings"])   == 1,       "should have 1 warning (bad val)"
    print("  convert_dim_group() PASS")

    print()
    if all_ok:
        print("All tests PASSED")
    else:
        print("Some tests FAILED -- review output above")