# -*- coding: utf-8 -*-
"""
page_validator.py -- GFS Post-Extraction Field Ownership Enforcement
=====================================================================
Validates a single page extraction dict after vision_client returns it.
Enforces field ownership rules so that no page type can carry dimension
groups that don't belong to it.

Public API
----------
validate_page(extraction: dict) -> dict

The input dict is mutated in place (forbidden dim groups set to None,
confidence downgraded) and returned.  A "_validation_warnings" key is
always present after validation.

Ownership rules
---------------
  PLAN_VIEW    : owns plan_dims only  -- section_dims + glass_detail_dims cleared
  SECTION      : owns section_dims only -- plan_dims + glass_detail_dims cleared
  GLASS_DETAIL : owns glass_detail_dims only -- plan_dims + section_dims cleared
  COVER        : owns no dim groups -- all three cleared
  OTHER        : owns no dim groups -- all three cleared
  MGDS_CATALOG : owns no dim groups -- all three cleared
  (unknown)    : all three cleared, warning added

Structural guarantees after validate_page()
-------------------------------------------
- "_validation_warnings"  : always a list (empty if clean)
- "extraction_confidence" : downgraded to "LOW" when any warning added
- All three dim group keys always present (set to None if not applicable)
- All top-level envelope keys always present (missing keys filled with None)
"""

# ---------------------------------------------------------------------------
# Expected top-level keys -- all must exist after validation
# ---------------------------------------------------------------------------

ENVELOPE_KEYS = [
    "page_type",
    "sheet_id",
    "unit_label",
    "job_type",
    "expedited",
    "project_address",
    "approval_date",
    "approved_by_name",
    "approved_by_title",
    "approved_by_company",
    "glass_makeup",
    "plan_dims",
    "section_dims",
    "glass_detail_dims",
    "extraction_confidence",
    "extraction_notes",
    "_validation_warnings",
]

# Page types that may carry job_type and expedited fields
_METADATA_OWNER_TYPES = {"COVER"}

# ---------------------------------------------------------------------------
# Which page types own which dim groups
# ---------------------------------------------------------------------------

# Maps page_type -> set of dim-group keys that are ALLOWED to be populated.
# Any dim group NOT in the allowed set will be cleared.
_ALLOWED_DIM_GROUPS = {
    "PLAN_VIEW":    {"plan_dims"},
    "SECTION":      {"section_dims"},
    "GLASS_DETAIL": {"glass_detail_dims"},
    "COVER":        set(),
    "OTHER":        set(),
    "MGDS_CATALOG": set(),
}

ALL_DIM_GROUPS = {"plan_dims", "section_dims", "glass_detail_dims"}


# ---------------------------------------------------------------------------
# Public validator
# ---------------------------------------------------------------------------

def validate_page(extraction: dict) -> dict:
    """
    Validate and normalise a single page extraction dict.

    Operations performed (in order):
    1. Ensure all ENVELOPE_KEYS exist (fills missing keys with None).
    2. Ensure _validation_warnings is a list.
    3. Determine page_type; warn if unrecognised.
    4. For each dim group not owned by this page_type:
       a. If the group is non-null and non-empty, log a warning.
       b. Set the group to None.
    5. If any warnings were added, downgrade extraction_confidence to "LOW".

    Args:
        extraction: dict returned by vision_client (the "data" field) after
                    page-type-specific prompt extraction.

    Returns:
        The same dict, mutated in place, with all ownership rules enforced.

    Non-technical: Double-checks the AI's work for a single page. It enforces "field
    ownership" rules: for example, since a Plan View page shouldn't have Section measurements,
    if the AI accidentally filled them, we erase them and log a warning. If we find any
    inconsistencies, we downgrade our confidence rating to 'LOW'.

    Technical: Fills missing top-level keys to ensure a complete schema footprint, cleans
    unrecognized page types, clears forbidden dim groups by calling _is_non_empty to check if 
    any values actually leaked, resets job_type/expedited on non-COVER pages, and sets
    extraction_confidence to "LOW" if any warnings are compiled.
    """
    # -- Step 1: Ensure all envelope keys exist --------------------------------
    for key in ENVELOPE_KEYS:
        if key not in extraction:
            extraction[key] = None

    # -- Step 2: Ensure _validation_warnings is a list ------------------------
    if not isinstance(extraction.get("_validation_warnings"), list):
        extraction["_validation_warnings"] = []

    warnings = extraction["_validation_warnings"]

    # -- Step 3: Determine page type ------------------------------------------
    raw_type = extraction.get("page_type")
    page_type = str(raw_type).upper().strip() if raw_type else "UNKNOWN"

    allowed = _ALLOWED_DIM_GROUPS.get(page_type)

    if allowed is None:
        # Unrecognised page type -- clear everything and warn
        warnings.append(
            f"Unrecognised page_type '{raw_type}' -- all dim groups cleared."
        )
        allowed = set()

    # -- Step 4: Clear dim groups not owned by this page type -----------------
    for group_key in ALL_DIM_GROUPS:
        if group_key in allowed:
            # This group belongs to the page type -- leave it alone
            continue

        group_val = extraction.get(group_key)

        # Only warn if the group actually had data (i.e. model populated it
        # despite it not being appropriate for this page type)
        if group_val and _is_non_empty(group_val):
            warnings.append(
                f"{page_type} page had '{group_key}' populated -- cleared "
                f"(belongs to a different page family)."
            )

        extraction[group_key] = None

    # -- Step 5: Clear job_type / expedited on non-COVER pages ----------------
    if page_type not in _METADATA_OWNER_TYPES:
        for field in ("job_type", "expedited"):
            if extraction.get(field) is not None:
                warnings.append(
                    f"{page_type} page had '{field}' populated -- cleared "
                    f"(owned by COVER page only)."
                )
                extraction[field] = None

    # -- Step 6: Downgrade confidence if any warnings were generated ----------
    if warnings:
        current_confidence = extraction.get("extraction_confidence")
        if current_confidence != "LOW":
            extraction["extraction_confidence"] = "LOW"
            if not any("confidence downgraded" in w for w in warnings):
                warnings.append(
                    "extraction_confidence downgraded to LOW due to validation warnings."
                )

    return extraction


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _is_non_empty(value) -> bool:
    """
    Return True if value is a non-null, non-empty dict or list with at least
    one non-None leaf value.  Used to decide whether to emit a warning.

    Non-technical: Helps us check if a data group actually contains real text or numbers
    (rather than being completely blank or full of empty spaces), which determines if we
    need to complain/raise a warning about it.

    Technical: Recursively checks dictionary keys/values or lists for non-None items.
    """
    if value is None:
        return False
    if isinstance(value, dict):
        return any(v is not None for v in value.values())
    if isinstance(value, list):
        return len(value) > 0
    return bool(value)


# ---------------------------------------------------------------------------
# CLI smoke-test -- python page_validator.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    def _run_test(description, extraction, expect_warnings_count,
                  expect_plan_dims_null=False, expect_section_dims_null=False,
                  expect_glass_detail_dims_null=False):
        result = validate_page(extraction)
        w_count = len(result["_validation_warnings"])
        ok = True
        notes = []

        if w_count != expect_warnings_count:
            ok = False
            notes.append(f"  warnings: got {w_count}, expected {expect_warnings_count}")
        if expect_plan_dims_null and result["plan_dims"] is not None:
            ok = False
            notes.append("  plan_dims should be None")
        if expect_section_dims_null and result["section_dims"] is not None:
            ok = False
            notes.append("  section_dims should be None")
        if expect_glass_detail_dims_null and result["glass_detail_dims"] is not None:
            ok = False
            notes.append("  glass_detail_dims should be None")
        # _validation_warnings must always be a list
        if not isinstance(result["_validation_warnings"], list):
            ok = False
            notes.append("  _validation_warnings is not a list")

        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {description}")
        for note in notes:
            print(note)
        if not ok:
            for w in result["_validation_warnings"]:
                print(f"         warning: {w}")
        return ok

    all_ok = True

    # Test 1: PLAN_VIEW with correct data -- no warnings
    all_ok &= _run_test(
        "PLAN_VIEW with clean plan_dims -- no warnings",
        {
            "page_type": "PLAN_VIEW",
            "plan_dims": {"out_to_out_flange_width_raw": "88 1/2"},
            "section_dims": None,
            "glass_detail_dims": None,
        },
        expect_warnings_count=0,
        expect_section_dims_null=True,
        expect_glass_detail_dims_null=True,
    )

    # Test 2: PLAN_VIEW with section_dims polluted -- should clear + warn
    all_ok &= _run_test(
        "PLAN_VIEW with section_dims polluted -- cleared + warning",
        {
            "page_type": "PLAN_VIEW",
            "plan_dims": {"out_to_out_flange_width_raw": "88 1/2"},
            "section_dims": {"rough_opening_width_raw": "90"},
            "glass_detail_dims": None,
        },
        expect_warnings_count=2,   # section_dims cleared + confidence downgrade
        expect_section_dims_null=True,
    )

    # Test 3: SECTION with plan_dims polluted -- should clear + warn
    all_ok &= _run_test(
        "SECTION with plan_dims polluted -- cleared + warning",
        {
            "page_type": "SECTION",
            "plan_dims": {"exposed_frame_width_raw": "60"},
            "section_dims": {"rough_opening_width_raw": "64"},
            "glass_detail_dims": None,
        },
        expect_warnings_count=2,
        expect_plan_dims_null=True,
    )

    # Test 4: COVER with all dims null -- no warnings
    all_ok &= _run_test(
        "COVER with no dims -- no warnings",
        {
            "page_type": "COVER",
            "plan_dims": None,
            "section_dims": None,
            "glass_detail_dims": None,
            "project_address": "182 Robinson St",
        },
        expect_warnings_count=0,
        expect_plan_dims_null=True,
        expect_section_dims_null=True,
        expect_glass_detail_dims_null=True,
    )

    # Test 5: OTHER with all dims null -- no warnings
    all_ok &= _run_test(
        "OTHER with all dims null -- no warnings",
        {"page_type": "OTHER", "plan_dims": None, "section_dims": None,
         "glass_detail_dims": None},
        expect_warnings_count=0,
    )

    # Test 6: Unknown page type -- all dims cleared + warning
    all_ok &= _run_test(
        "Unknown page type -- all dims cleared",
        {"page_type": "BLUEPRINT", "plan_dims": {"x": "1"},
         "section_dims": None, "glass_detail_dims": None},
        expect_warnings_count=3,  # unrecognised + plan_dims cleared + confidence downgrade
        expect_plan_dims_null=True,
    )

    # Test 7: Missing keys filled in -- envelope completeness
    result = validate_page({"page_type": "COVER"})
    missing = [k for k in ["plan_dims", "sheet_id", "_validation_warnings"]
               if k not in result]
    if missing:
        print(f"  [FAIL] Missing envelope keys after validate_page: {missing}")
        all_ok = False
    else:
        print("  [PASS] Missing envelope keys auto-filled")

    print()
    if all_ok:
        print("All page_validator tests PASSED")
    else:
        print("Some page_validator tests FAILED -- review above")