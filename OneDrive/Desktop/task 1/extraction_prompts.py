# -*- coding: utf-8 -*-
"""
extraction_prompts.py -- GFS Page Extraction Prompts
=====================================================
Provides prompts for the GFS vision extraction pipeline.

Single-call design (current)
-----------------------------
COMBINED_EXTRACTION_PROMPT
    One prompt that both classifies the page type AND extracts the relevant
    fields in a single API call.  This is the prompt used by main.py.

    The model identifies the page family (COVER, PLAN_VIEW, SECTION,
    GLASS_DETAIL, MGDS_CATALOG, OTHER) and then fills only the schema
    fields that belong to that family.  All other dim groups are returned
    as null.

Legacy per-type builders (kept for reference / standalone testing)
-------------------------------------------------------------------
build_cover_prompt()
build_plan_prompt()
build_section_prompt()
build_glass_detail_prompt()
build_other_prompt()
get_prompt_for_type(page_type)
TYPE_DETECTION_PROMPT
"""


# ---------------------------------------------------------------------------
# Combined single-call prompt (used by main.py)
# ---------------------------------------------------------------------------

COMBINED_EXTRACTION_PROMPT = """\
You are a GFS (Glass Flooring Systems) shop drawing extraction engine.
Return ONLY a single valid JSON object -- no markdown fences, no preamble, no commentary.

=== STEP 1: IDENTIFY PAGE TYPE ===

Classify the page as one of these six values:

  "COVER"        -- Has a Glass Make-up layer list on the left, a frame-component
                    checklist table on the right, a large project address at top
                    centre, and an approval signature block.  Sheet ID: C-100.

  "PLAN_VIEW"    -- Top-down view of a rectangular or oval floor unit labelled
                    "UNIT A" (or B, C...).  Three concentric dimension chains
                    across BOTH width (horizontal) AND height (vertical):
                    OUT TO OUT OF FLANGE / EXPOSED FRAME / GLASS.
                    Sheet IDs start with P- (e.g. P-101).

  "SECTION"      -- Side cross-section view (Section A or B).  Shows the frame
                    stack in profile.  May include ROUGH OPENING, OUT TO OUT OF
                    FLANGE, EXPOSED FRAME, and GLASS dimension chains.
                    Sheet IDs start with S- (e.g. S-101).

  "GLASS_DETAIL" -- Labelled "PLAN - GLASS UNIT A" or "PERIMETER GLASS ASSEMBLY".
                    Shows OUTBOARD and INBOARD glass dimensions in inches and mm,
                    OR (for glass-only replacement jobs) shows the overall glass
                    panel plan with width and height dimension chains.
                    Sheet IDs start with G- (e.g. G-101).

  "MGDS_CATALOG" -- A product catalog page, component table, ISO bar spec,
                    warranty document, or delivery confirmation.

  "OTHER"        -- Any page not matching the above: framing piece details,
                    hole-location sheets, close-up callouts, glass layer
                    thickness diagrams (showing e.g. 1.3011 [33.05]), etc.

=== STEP 2: EXTRACT BASED ON PAGE TYPE ===

Based on the page_type you identified above, fill ONLY the fields listed for
that family.  Set every other dim group to null (not an object with null fields).
Do NOT guess.  Return null for any field that is not clearly visible.

  COVER only:
    Fill: project_address, approval_date, approved_by_name, approved_by_title,
          approved_by_company, glass_makeup (array of layer strings, verbatim),
          job_type, expedited (see Glass-Only Replacement section below)
    Set null: plan_dims, section_dims, glass_detail_dims

  PLAN_VIEW only:
    Fill: plan_dims -- capture BOTH width (horizontal chain) AND height
          (vertical chain) for each of:
            OUT TO OUT OF FLANGE -> out_to_out_flange_width_raw / _height_raw
            EXPOSED FRAME        -> exposed_frame_width_raw / _height_raw
            GLASS                -> glass_width_raw / glass_height_raw
    Transcribe dimension strings exactly as printed (e.g. "88 1/2", "61 1/16",
    "60 [5'-0\"]").  Do NOT convert fractions -- downstream code handles that.
    Set null: section_dims, glass_detail_dims

  SECTION only:
    Fill: section_dims -- capture BOTH width and height axes for each of:
            ROUGH OPENING        -> rough_opening_width_raw / _height_raw
            OUT TO OUT OF FLANGE -> out_to_out_flange_width_raw / _height_raw
            EXPOSED FRAME        -> exposed_frame_width_raw / _height_raw
            GLASS                -> glass_width_raw / glass_height_raw
    If two section cuts are shown: long side = width, short side = height.
    Set null: plan_dims, glass_detail_dims

  GLASS_DETAIL only:
    Fill: glass_detail_dims.
    For standard framed units: extract OUTBOARD raw+mm, INBOARD raw+mm.
    For glass-only replacement G-101 plan pages: extract glass_width_raw
      (horizontal chain) and glass_height_raw (vertical chain).
    Also fill glass_makeup (array of layer strings) if visible.
    Set null: plan_dims, section_dims

  OTHER / MGDS_CATALOG:
    Fill: sheet_id only.  Set null: all three dim groups and all metadata fields.

=== GLASS-ONLY REPLACEMENT JOB DETECTION ===

If you are processing the COVER page (C-100), check for these signals that
indicate this is a glass-only replacement job (not a standard framed unit):

  SIGNAL 1: "None - Glass only" is checked or marked Yes under Frame Components
  SIGNAL 2: The word "REPLACEMENT" appears in the project title or drawing title
  SIGNAL 3: No frame type is selected under Perimeter Frame Type

If ANY of these signals are present on the COVER page:
  - Set job_type: "GLASS_ONLY_REPLACEMENT"
  - There will be NO PLAN_VIEW or SECTION pages in this job.
  - Read project_address carefully -- do not drop any leading digits from the
    street number (e.g. "182 Robinson St" not "82 Robinson St").
  - Read approval_date from any area near "Date:", "Signed:", or approval stamp.
  - Read approved_by_name from near "Signed:" or "Initial Here".
  - Read approved_by_title from near "Title:".
  - Read approved_by_company from near "Company:".
  - If "EXPEDITED" appears anywhere on the page, set expedited: true.
    Otherwise set expedited: null.

If none of these signals are present, set job_type: "STANDARD_FRAMED_UNIT".
For all non-COVER pages, set both job_type and expedited to null.

=== GLASS-ONLY REPLACEMENT: G-101 PLAN PAGE ===

A page labelled "PLAN - GLASS UNIT A" (or similar, typically sheet G-101)
for a glass-only replacement job shows the overall glass panel outline with
two dimension chains -- one horizontal (width) and one vertical (height).

  - Classify this page as GLASS_DETAIL
  - Extract glass_width_raw from the horizontal dimension chain
  - Extract glass_height_raw from the vertical dimension chain
  - BOTH chains are present -- do not leave height null if a vertical
    dimension is visible
  - Store in glass_detail_dims.glass_width_raw and glass_detail_dims.glass_height_raw

=== GLASS-ONLY REPLACEMENT: G-102 THICKNESS PAGE ===

A page showing internal layer build-up thicknesses (e.g. "1.3011 [33.05]",
"0.060 [1.52]", "0.315 [8.0]") is a glass cross-section thickness diagram.

  - Classify this page as OTHER
  - Do NOT extract any dimensions -- these are layer thicknesses, not panel sizes
  - Set all dim groups to null

=== UNIVERSAL RULES ===
- Transcribe dimension strings exactly as printed -- preserve fractions as text.
- If a dimension appears as both inches and feet-inches (e.g. "60 [5'-0\"]"),
  copy the full string including the bracketed alternate (do not summarise).
- Return null for any value that is not clearly visible -- never guess.
- glass_makeup must be an array of strings (one per layer, in visible order) or null.
- unit_label is the unit letter (e.g. "A", "B") or null if not shown.

=== RETURN exactly this JSON structure (no extra keys, no missing keys) ===
{
  "page_type":  "<COVER|PLAN_VIEW|SECTION|GLASS_DETAIL|MGDS_CATALOG|OTHER>",
  "sheet_id":   "<from title block e.g. C-100, P-101 -- or null>",
  "unit_label": "<e.g. A or null>",
  "job_type":   "<GLASS_ONLY_REPLACEMENT|STANDARD_FRAMED_UNIT on COVER page, null elsewhere>",
  "expedited":  "<true if EXPEDITED appears on COVER page, null otherwise>",

  "project_address":     null,
  "approval_date":       null,
  "approved_by_name":    null,
  "approved_by_title":   null,
  "approved_by_company": null,
  "glass_makeup":        null,

  "plan_dims": null,

  "section_dims": null,

  "glass_detail_dims": null,

  "extraction_confidence": "<HIGH|MEDIUM|LOW>",
  "extraction_notes":      null
}

For PLAN_VIEW pages, replace "plan_dims": null with:
  "plan_dims": {
    "out_to_out_flange_width_raw":  null,
    "out_to_out_flange_height_raw": null,
    "exposed_frame_width_raw":      null,
    "exposed_frame_height_raw":     null,
    "glass_width_raw":              null,
    "glass_height_raw":             null
  }

For SECTION pages, replace "section_dims": null with:
  "section_dims": {
    "rough_opening_width_raw":      null,
    "rough_opening_height_raw":     null,
    "out_to_out_flange_width_raw":  null,
    "out_to_out_flange_height_raw": null,
    "exposed_frame_width_raw":      null,
    "exposed_frame_height_raw":     null,
    "glass_width_raw":              null,
    "glass_height_raw":             null
  }

For GLASS_DETAIL pages, replace "glass_detail_dims": null with:
  "glass_detail_dims": {
    "glass_outboard_raw": null,
    "glass_outboard_mm":  null,
    "glass_inboard_raw":  null,
    "glass_inboard_mm":   null,
    "glass_width_raw":    null,
    "glass_height_raw":   null
  }
"""


# ---------------------------------------------------------------------------
# Legacy per-type detection prompt (kept for reference)
# ---------------------------------------------------------------------------

TYPE_DETECTION_PROMPT = """\
You are analysing a GFS (Glass Flooring Systems) shop drawing page.
Return ONLY a valid JSON object -- no markdown, no explanation.

Identify the page type:
  "COVER"        -- Glass Make-up list + checklist table + address + approval block
  "PLAN_VIEW"    -- Top-down view with dimension chains, sheet starts with P-
  "SECTION"      -- Side cross-section, sheet starts with S-
  "GLASS_DETAIL" -- "PLAN - GLASS UNIT A" or "PERIMETER GLASS ASSEMBLY", sheet G-
  "MGDS_CATALOG" -- Product catalog, ISO bar spec, warranty/delivery doc
  "OTHER"        -- Any page not matching the above

Return exactly:
{
  "page_type": "<one of the six values above>",
  "sheet_id":  "<sheet ID from title block, e.g. C-100, or null>"
}
"""


# ---------------------------------------------------------------------------
# Legacy per-type builders (kept for reference / standalone testing)
# ---------------------------------------------------------------------------

def build_cover_prompt() -> str:
    """
    You are analysing a GFS COVER sheet (C-100). Return ONLY a valid JSON object.
    Extract: page_type="COVER", sheet_id, project_address, approval_date,
    approved_by_name, approved_by_title, approved_by_company, glass_makeup (array).
    All dim groups must be null.

    Non-technical: Builds the AI prompt instruction for cover pages. The cover page
    contains job metadata (like who signed it and where it's going) and doesn't contain
    specific dimensions.

    Technical: Returns a string prompt instructing the model to map cover-specific keys
    and set all dimension groups to null.
    """
    return """\
You are analysing a GFS COVER sheet (C-100). Return ONLY a valid JSON object.
Extract: page_type="COVER", sheet_id, project_address, approval_date,
approved_by_name, approved_by_title, approved_by_company, glass_makeup (array).
All dim groups must be null.
"""


def build_plan_prompt() -> str:
    """
    You are analysing a GFS PLAN VIEW page (P-101...). Return ONLY a valid JSON object.
    Extract plan_dims: out_to_out_flange width+height, exposed_frame width+height,
    glass width+height -- raw strings exactly as printed. All other dim groups null.

    Non-technical: Builds the AI prompt instruction for top-down floor layouts (Plan Views).
    Tells the model to search for horizontal and vertical dimension chains.

    Technical: Instructs the model to populate `plan_dims` keys and leave all other dim groups null.
    """
    return """\
You are analysing a GFS PLAN VIEW page (P-101...). Return ONLY a valid JSON object.
Extract plan_dims: out_to_out_flange width+height, exposed_frame width+height,
glass width+height -- raw strings exactly as printed. All other dim groups null.
"""


def build_section_prompt() -> str:
    """
    You are analysing a GFS SECTION page (S-101...). Return ONLY a valid JSON object.
    Extract section_dims: rough_opening, out_to_out_flange, exposed_frame, glass --
    width and height for each. All other dim groups null.

    Non-technical: Builds the AI prompt instruction for side cross-sections (Sections).
    Tells the model to extract horizontal and vertical measurements.

    Technical: Instructs the model to populate `section_dims` keys and leave all other dim groups null.
    """
    return """\
You are analysing a GFS SECTION page (S-101...). Return ONLY a valid JSON object.
Extract section_dims: rough_opening, out_to_out_flange, exposed_frame, glass --
width and height for each. All other dim groups null.
"""


def build_glass_detail_prompt() -> str:
    """
    You are analysing a GFS GLASS DETAIL page (G-101...). Return ONLY a valid JSON object.
    Extract glass_detail_dims: outboard raw+mm, inboard raw+mm, width+height if labelled.
    Also extract glass_makeup array. All other dim groups null.

    Non-technical: Builds the AI prompt instruction for glass fabrication details.
    These details list the sizes of the individual glass panes (inboard/outboard layers)
    in both inches and millimeters.

    Technical: Instructs the model to populate `glass_detail_dims` keys, extract the 
    `glass_makeup` array, and leave other dim groups null.
    """
    return """\
You are analysing a GFS GLASS DETAIL page (G-101...). Return ONLY a valid JSON object.
Extract glass_detail_dims: outboard raw+mm, inboard raw+mm, width+height if labelled.
Also extract glass_makeup array. All other dim groups null.
"""


def build_other_prompt() -> str:
    """
    You are analysing a GFS shop drawing page classified as OTHER or MGDS_CATALOG.
    Return ONLY a valid JSON object. Return sheet_id only; all dim groups null.

    Non-technical: Builds the AI prompt instruction for miscellaneous drawings, catalog
    sheets, or warranty information. We only want to know the sheet ID (like A-102) and
    skip extracting any dimensions.

    Technical: Instructs the model to extract only `sheet_id` and return null for all
    business or dimension keys.
    """
    return """\
You are analysing a GFS shop drawing page classified as OTHER or MGDS_CATALOG.
Return ONLY a valid JSON object. Return sheet_id only; all dim groups null.
"""


_PROMPT_MAP = {
    "COVER":        build_cover_prompt,
    "PLAN_VIEW":    build_plan_prompt,
    "SECTION":      build_section_prompt,
    "GLASS_DETAIL": build_glass_detail_prompt,
    "MGDS_CATALOG": build_other_prompt,
    "OTHER":        build_other_prompt,
}


def get_prompt_for_type(page_type: str) -> str:
    """
    Return the legacy per-type extraction prompt for a given page_type.
    Note: main.py now uses COMBINED_EXTRACTION_PROMPT instead of this function.
    Kept for standalone testing and reference.

    Non-technical: Helper function that fetches the correct specialized instruction
    prompt for the given page type.

    Technical: Maps input string to corresponding legacy prompt builder, defaulting
    to build_other_prompt for unknown types.
    """
    builder = _PROMPT_MAP.get(str(page_type).upper(), build_other_prompt)
    return builder()


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"COMBINED_EXTRACTION_PROMPT: {len(COMBINED_EXTRACTION_PROMPT)} chars")
    print(f"TYPE_DETECTION_PROMPT:      {len(TYPE_DETECTION_PROMPT)} chars")
    print()
    for t in ["COVER", "PLAN_VIEW", "SECTION", "GLASS_DETAIL", "OTHER", "UNKNOWN"]:
        p = get_prompt_for_type(t)
        print(f"  {t:<16} legacy prompt -> {len(p)} chars")
    print()
    print("extraction_prompts OK")
