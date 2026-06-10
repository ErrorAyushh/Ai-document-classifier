# -*- coding: utf-8 -*-
"""
job_aggregator.py -- GFS Authority-Based Job-Level Merge
=========================================================
Combines validated page-level extraction results into a single
structured job-level record, using an authority table to decide
which page type is the canonical source for each field group.

Public API
----------
aggregate_pages(pages: list) -> dict

Input: list of page dicts (each with an "extraction" key holding the
       validated+normalized page extraction).

Output: a merged_result dict containing:
  - job_type      : "GLASS_ONLY_REPLACEMENT" | "STANDARD_FRAMED_UNIT"
  - expedited     : True | None
  - All authoritative field values from the designated source pages
  - A "_sources" dict recording which sheet_id each value came from
  - A "_merge_warnings" list of any structural issues detected
    (NOTE: for GLASS_ONLY_REPLACEMENT jobs, missing PLAN_VIEW and SECTION
     pages do NOT generate warnings -- they are expected to be absent)

Authority table
---------------
  project_address, approval_date, approved_by_*  <- COVER (fallback: first non-null)
  expedited                                      <- COVER
  job_type                                       <- COVER
  glass_makeup                                   <- GLASS_DETAIL (fallback: COVER)
  plan_dims                                      <- PLAN_VIEW
  section_dims                                   <- SECTION
  glass_detail_dims                              <- GLASS_DETAIL
  MGDS_CATALOG pages                             <- skipped for all business fields
  OTHER pages                                    <- skipped for all business fields
"""

from typing import Optional

# ---------------------------------------------------------------------------
# Authority definitions
# ---------------------------------------------------------------------------

# page_type -> list of (field_key, fallback_page_types)
# The first page of the listed type with a non-null value wins.
METADATA_AUTHORITY = [
    ("project_address",    ["COVER"]),
    ("approval_date",      ["COVER"]),
    ("approved_by_name",   ["COVER"]),
    ("approved_by_title",  ["COVER"]),
    ("approved_by_company",["COVER"]),
]

GLASS_MAKEUP_AUTHORITY = ["GLASS_DETAIL", "COVER"]  # fallback chain

DIM_GROUP_AUTHORITY = {
    "plan_dims":         "PLAN_VIEW",
    "section_dims":      "SECTION",
    "glass_detail_dims": "GLASS_DETAIL",
}

# Page types that contribute nothing to the merged output
SKIP_TYPES = {"OTHER", "MGDS_CATALOG"}

# Dim groups that are expected to be absent for glass-only replacement jobs
_GLASS_ONLY_ABSENT_GROUPS = {"plan_dims", "section_dims"}


# ---------------------------------------------------------------------------
# Public aggregator
# ---------------------------------------------------------------------------

def aggregate_pages(pages: list) -> dict:
    """
    Merge validated page extractions into one job-level record.

    Args:
        pages: list of page result dicts.  Each dict must have an
               "extraction" key containing the validated page extraction,
               and optionally a "page_number" key for diagnostics.

    Returns:
        {
            "job_type":           "GLASS_ONLY_REPLACEMENT" | "STANDARD_FRAMED_UNIT",
            "expedited":          True | None,
            "project_address":    str | None,
            "approval_date":      str | None,
            "approved_by_name":   str | None,
            "approved_by_title":  str | None,
            "approved_by_company":str | None,
            "glass_makeup":       list | None,
            "plan_dims":          dict | None,
            "section_dims":       dict | None,
            "glass_detail_dims":  dict | None,
            "_sources": {
                "project_address":    "C-100" | None,
                "glass_makeup":       "G-101" | None,
                "plan_dims":          "P-101" | None,
                "section_dims":       "S-101" | None,
                "glass_detail_dims":  "G-101" | None,
            },
            "_merge_warnings": []
        }

    Non-technical: Combines all the individual page extractions into a single, master job
    record. It uses an "authority table" (like a set of rules) to decide which sheet's text
    wins for each field. For example, if both the Cover page and Plan View page show an address,
    the Cover page is preferred. It also keeps track of which sheet (e.g. C-100, P-101) each
    value was taken from, and flags warnings if crucial sheets are missing. If the job type
    is detected as glass-only replacement, we expect missing plan/section drawings and don't warn.

    Technical: Indexes pages by uppercase page_type, determines job_type and expedited from
    the first COVER page, uses METADATA_AUTHORITY, GLASS_MAKEUP_AUTHORITY, and DIM_GROUP_AUTHORITY
    mappings to call helpers, records sheet sources, and suppresses Plan/Section warnings for 
    GLASS_ONLY_REPLACEMENT jobs.
    """
    warnings = []
    sources  = {}

    # -- Index pages by type for easy lookup ----------------------------------
    # type_index: page_type -> list of extraction dicts (in page order)
    type_index: dict[str, list[dict]] = {}
    for page in pages:
        ext = page.get("extraction")
        if not ext:
            continue
        pt = str(ext.get("page_type", "UNKNOWN")).upper()
        type_index.setdefault(pt, []).append(ext)

    # -- Detect job type from COVER page --------------------------------------
    job_type = "STANDARD_FRAMED_UNIT"
    expedited = None
    for ext in type_index.get("COVER", []):
        jt = ext.get("job_type")
        if jt:
            job_type = str(jt).upper()
        exp = ext.get("expedited")
        if exp is True or str(exp).lower() == "true":
            expedited = True
        break  # Only use the first COVER page

    is_glass_only = (job_type == "GLASS_ONLY_REPLACEMENT")

    # -- Resolve metadata fields from COVER (fallback: first non-null) --------
    result = {
        "job_type":  job_type,
        "expedited": expedited,
    }
    for field, authority_types in METADATA_AUTHORITY:
        value, source_sheet = _first_non_null_field(
            field, authority_types, type_index, fallback_all=True
        )
        result[field]   = value
        sources[field]  = source_sheet

    # -- Resolve glass_makeup -------------------------------------------------
    gm_value, gm_source = _first_non_null_field(
        "glass_makeup", GLASS_MAKEUP_AUTHORITY, type_index, fallback_all=False
    )
    result["glass_makeup"]   = gm_value
    sources["glass_makeup"]  = gm_source

    # -- Resolve dim groups ---------------------------------------------------
    for group_key, authority_type in DIM_GROUP_AUTHORITY.items():
        dim_val, dim_source = _first_non_null_dim_group(
            group_key, authority_type, type_index
        )
        result[group_key]    = dim_val
        sources[group_key]   = dim_source

        # Warn if the expected authoritative page type was missing --
        # but suppress for glass-only jobs where PLAN_VIEW/SECTION are not expected
        if authority_type not in type_index:
            if is_glass_only and group_key in _GLASS_ONLY_ABSENT_GROUPS:
                pass  # Expected absence for glass-only replacement jobs
            else:
                warnings.append(
                    f"No {authority_type} page found -- '{group_key}' will be null in merged result."
                )

    # -- Warn about duplicate authoritative pages (multiple P-101 etc.) -------
    for authority_type in ["PLAN_VIEW", "SECTION", "GLASS_DETAIL"]:
        pages_of_type = type_index.get(authority_type, [])
        if len(pages_of_type) > 1:
            sheet_ids = [p.get("sheet_id") for p in pages_of_type]
            warnings.append(
                f"Multiple {authority_type} pages found ({sheet_ids}). "
                f"Only the first was used as authoritative source."
            )

    # -- Assemble final result ------------------------------------------------
    result["_sources"]        = sources
    result["_merge_warnings"] = warnings
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_non_null_field(
    field: str,
    authority_types: list,
    type_index: dict,
    fallback_all: bool = False,
) -> tuple[Optional[object], Optional[str]]:
    """
    Return the first non-null value for `field` from pages of the listed
    authority_types (in priority order).

    If fallback_all is True and no value found in authority_types, scans
    ALL page types (excluding SKIP_TYPES) as a last resort.

    Returns (value, sheet_id_string | None).

    Non-technical: Finds the first page in order of authority that contains a real value for
    a text field, helping us fallback safely if the primary source page is missing that info.

    Technical: Scans authority page lists inside the indexed dict. If fallback_all is True,
    it falls back to scanning other non-ignored page families.
    """
    # Priority pass: check authority types first
    for pt in authority_types:
        for ext in type_index.get(pt, []):
            val = ext.get(field)
            if val is not None:
                return val, ext.get("sheet_id")

    if not fallback_all:
        return None, None

    # Fallback pass: any page type that has the field
    for pt, exts in type_index.items():
        if pt in SKIP_TYPES:
            continue
        for ext in exts:
            val = ext.get(field)
            if val is not None:
                return val, ext.get("sheet_id")

    return None, None


def _first_non_null_dim_group(
    group_key: str,
    authority_type: str,
    type_index: dict,
) -> tuple[Optional[dict], Optional[str]]:
    """
    Return the first non-null dim group dict from pages of authority_type.

    Returns (dim_group_dict | None, sheet_id | None).

    Non-technical: Finds the first matching drawing sheet of the authoritative page type
    that actually contains filled measurements for a measurement group (like plan dimensions).

    Technical: Checks the type index for authority_type pages, filtering with
    _dim_group_has_data to verify at least one non-warning measurement field is populated.
    """
    for ext in type_index.get(authority_type, []):
        group_val = ext.get(group_key)
        if group_val and _dim_group_has_data(group_val):
            return group_val, ext.get("sheet_id")
    return None, None


def _dim_group_has_data(dims: dict) -> bool:
    """
    Return True if the dim group has at least one non-None, non-warning value.
    Ignores the internal "_conversion_warnings" key.

    Non-technical: Checks if a block of measurements is actually filled with numbers or text
    rather than being completely empty or containing only warning messages.

    Technical: Iterates over dictionary items, filtering out '_conversion_warnings'
    and checking if any other values are non-None.
    """
    if not isinstance(dims, dict):
        return False
    return any(
        k != "_conversion_warnings" and v is not None
        for k, v in dims.items()
    )


# ---------------------------------------------------------------------------
# CLI smoke-test -- python job_aggregator.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    # Build minimal mock pages
    mock_pages = [
        {
            "page_number": 1,
            "extraction": {
                "page_type": "COVER",
                "sheet_id": "C-100",
                "project_address": "182 Robinson St, Oakville, ON",
                "approval_date": "10/25",
                "approved_by_name": None,
                "approved_by_title": None,
                "approved_by_company": "IRON RIDGE CUSTOM HOMES",
                "glass_makeup": None,  # Not on cover for this job
                "plan_dims": None,
                "section_dims": None,
                "glass_detail_dims": None,
            },
        },
        {
            "page_number": 2,
            "extraction": {
                "page_type": "PLAN_VIEW",
                "sheet_id": "P-101",
                "project_address": None,
                "approval_date": None,
                "approved_by_name": None,
                "approved_by_title": None,
                "approved_by_company": None,
                "glass_makeup": None,
                "plan_dims": {
                    "out_to_out_flange_width_raw":  "88 1/2",
                    "out_to_out_flange_width_in":   88.5,
                    "out_to_out_flange_height_raw": "61 1/16",
                    "out_to_out_flange_height_in":  61.0625,
                    "exposed_frame_width_raw": None,
                    "exposed_frame_width_in":  None,
                    "_conversion_warnings": [],
                },
                "section_dims": None,
                "glass_detail_dims": None,
            },
        },
        {
            "page_number": 3,
            "extraction": {
                "page_type": "SECTION",
                "sheet_id": "S-101",
                "project_address": None,
                "approval_date": None,
                "approved_by_name": None,
                "approved_by_title": None,
                "approved_by_company": None,
                "glass_makeup": None,
                "plan_dims": None,
                "section_dims": {
                    "rough_opening_width_raw":  "64",
                    "rough_opening_width_in":   64.0,
                    "rough_opening_height_raw": "64",
                    "rough_opening_height_in":  64.0,
                    "_conversion_warnings": [],
                },
                "glass_detail_dims": None,
            },
        },
        {
            "page_number": 5,
            "extraction": {
                "page_type": "GLASS_DETAIL",
                "sheet_id": "G-101",
                "project_address": None,
                "approval_date": None,
                "approved_by_name": None,
                "approved_by_title": None,
                "approved_by_company": None,
                "glass_makeup": [
                    "10MM TOP LAYER - LOW IRON TEMPERED",
                    "1.52MM INTERLAYER PVB",
                ],
                "plan_dims": None,
                "section_dims": None,
                "glass_detail_dims": {
                    "glass_outboard_raw": "57 7/8",
                    "glass_outboard_in":  57.875,
                    "glass_outboard_mm":  "1470.05",
                    "_conversion_warnings": [],
                },
            },
        },
        {
            "page_number": 4,
            "extraction": {
                "page_type": "OTHER",
                "sheet_id": "S-102",
                "project_address": None,
                "plan_dims": None,
                "section_dims": None,
                "glass_detail_dims": None,
            },
        },
    ]

    merged = aggregate_pages(mock_pages)

    # Assertions
    all_ok = True

    def check(label, condition):
        global all_ok
        status = "PASS" if condition else "FAIL"
        if not condition:
            all_ok = False
        print(f"  [{status}] {label}")

    check("project_address from COVER",
          merged["project_address"] == "182 Robinson St, Oakville, ON")
    check("approved_by_company from COVER",
          merged["approved_by_company"] == "IRON RIDGE CUSTOM HOMES")
    check("glass_makeup from GLASS_DETAIL",
          merged["glass_makeup"] is not None and len(merged["glass_makeup"]) == 2)
    check("plan_dims from PLAN_VIEW",
          merged["plan_dims"] is not None and
          merged["plan_dims"]["out_to_out_flange_width_in"] == 88.5)
    check("section_dims from SECTION",
          merged["section_dims"] is not None and
          merged["section_dims"]["rough_opening_width_in"] == 64.0)
    check("glass_detail_dims from GLASS_DETAIL",
          merged["glass_detail_dims"] is not None and
          merged["glass_detail_dims"]["glass_outboard_in"] == 57.875)
    check("sources populated for plan_dims",
          merged["_sources"].get("plan_dims") == "P-101")
    check("sources populated for section_dims",
          merged["_sources"].get("section_dims") == "S-101")
    check("sources populated for glass_detail_dims",
          merged["_sources"].get("glass_detail_dims") == "G-101")
    check("no merge warnings (clean job)",
          len(merged["_merge_warnings"]) == 0)

    print()
    if all_ok:
        print("All job_aggregator tests PASSED")
    else:
        print("Some job_aggregator tests FAILED -- review above")
        import json
        print(json.dumps(merged, indent=2, default=str))