def compute_confidence(extraction_result: dict) -> dict:
    # This function is a quality control inspector
    # After the AI extracts data from a drawing, this function checks
    # how reliable that output is and gives it a grade — HIGH, MEDIUM, or LOW
    # Think of it like a checklist a supervisor runs before approving work
    #
    # INPUT:  extraction_result — a dict containing all the AI extracted data
    # OUTPUT: a dict with confidence level, numeric score, list of issues found,
    #         and a list of things the estimator must always do manually

    # ── SPECIAL CASE: MGDS CATALOG ────────────────────────────────────────────
    # This check must come FIRST before anything else
    # MGDS catalog products are pre-configured standard sizes from a catalog
    # There are no custom dimensions to misread, no ambiguous specs to verify
    # The product model number like GFS32X96YL-3 already encodes everything
    # So we skip all 9 checks and return HIGH confidence immediately
    # This is called an early exit — we have the answer, no need to keep going
    if extraction_result.get("job_type") == "MGDS_CATALOG":
        return {
            "confidence": "HIGH",
            "score": 100,
            "issues": [],
            "flags_for_estimator": [
                # Even for MGDS, these three things always need human input
                # The AI never fills these in — they require estimator judgment
                "Man hours — estimator judgment required",
                "Silicone quantity — calculate from perimeter LF",
                "Packaging count — estimator judgment required"
            ]
        }

    # ── SETUP ─────────────────────────────────────────────────────────────────
    # issues is a list we build up as we find problems
    # Every time a check finds something wrong, it adds a message to this list
    # At the end, the number of items in this list determines the confidence level
    issues = []

    # units is the list of glass units in this job
    # Most checks loop through these units looking for problems
    # .get("units", []) means if "units" key is missing, use empty list
    # This prevents crashes on incomplete extraction results
    units = extraction_result.get("units", [])

    # ── CHECK 1: NON-RECTANGULAR SHAPE ────────────────────────────────────────
    # Standard glass panels are rectangular — L x W
    # Custom shapes like L-shaped, circular, or angled openings are much harder
    # to measure accurately from drawings
    # If any unit has a custom shape, the AI extraction is less reliable
    # break after the first one found — we only want to add this issue ONCE
    # even if multiple units have custom shapes
    for unit in units:
        if unit.get("shape") == "NON_RECTANGULAR":
            issues.append("Custom shape detected — verify dimensions manually")
            break  # stop checking — one message is enough, no duplicates

    # ── CHECK 2: MISSING DIMENSIONS ───────────────────────────────────────────
    # Width and length are the most critical fields in the whole extraction
    # Without them nothing can be calculated — not the BOM, not the price
    # If either is None it means the AI couldn't find it in the drawing
    # is None is used instead of not unit.get("width")
    # because 0 is a valid dimension but "not 0" would incorrectly flag it
    for unit in units:
        if unit.get("width") is None or unit.get("length") is None:
            issues.append("Missing dimensions on one or more units — check drawings")
            break  # one message even if multiple units are missing dimensions

    # ── CHECK 3: EXPOSED FRAME LABEL NOT FOUND ────────────────────────────────
    # The exposed frame label identifies which side of the frame is visible
    # after installation — important for finishing and pricing
    # If the AI couldn't find this label it may have identified the wrong frame type
    # .get("exposed_frame_label", False) — if the key is missing, treat as False
    # not False = True = problem found
    for unit in units:
        if not unit.get("exposed_frame_label", False):
            issues.append("Exposed frame label not found on one or more units — verify frame type")
            break  # one message even if multiple units are missing this label

    # ── CHECK 4: MULTIPLE GLASS SPECS ON COVER PAGE ───────────────────────────
    # The cover page usually has one glass specification for the whole job
    # If it lists multiple different glass specs, it means different units
    # in the job use different glass types
    # This is more complex to quote and more likely to have extraction errors
    # len(glass_specs) > 1 means more than one spec was found
    cover = extraction_result.get("cover", {})
    glass_specs = cover.get("glass_specs", [])
    if len(glass_specs) > 1:
        issues.append("Multiple glass specs detected on cover — confirm per-unit specifications")
    # Note: no break here because this is not a loop — it's a single check

    # ── CHECK 5: CROSS BEAM DETECTED ──────────────────────────────────────────
    # Cross beams are structural support beams used in multi-panel layouts
    # The type of cross beam (I-beam vs T-section) is determined by an
    # engineering load chart — the AI cannot make this determination
    # So if any unit has cross beams, the estimator must manually verify
    # which beam type is correct using the engineering chart
    # .get("cross_beam_count", 0) — if key is missing, assume 0 (no beams)
    for unit in units:
        if unit.get("cross_beam_count", 0) > 0:
            issues.append("Cross beam detected — confirm type via engineering chart")
            break  # one message even if multiple units have cross beams

    # ── CHECK 6: EXPEDITED JOB ────────────────────────────────────────────────
    # Expedited jobs use EGR glass which has a faster delivery time
    # BUT EGR glass requires a Heat Soak Test (HST) in some cases
    # The AI doesn't know whether HST is required — that's an engineering decision
    # So we flag every expedited job for the estimator to manually verify
    if extraction_result.get("expedited", False):
        issues.append("Expedited job — estimator must verify heat soak test (HST) requirements")

    # ── CHECK 7: VISION API ERRORS ────────────────────────────────────────────
    # If Claude Vision failed to process one or more pages during extraction,
    # the data from those pages is missing or incomplete
    # We count how many pages had errors and include that count in the message
    # so the estimator knows how many pages to check manually
    # f-string is used to inject the actual count into the message
    vision_errors = extraction_result.get("vision_errors", [])
    if len(vision_errors) > 0:
        issues.append(f"Vision API errors on {len(vision_errors)} page(s) — extracted data may be incomplete")

    # ── CHECK 8: MORE THAN 5 UNITS ────────────────────────────────────────────
    # Simple logic — the more units in a job, the more pages were processed
    # the more API calls were made, and the more chances there were for errors
    # Jobs with 6+ units are complex enough that extra human review is warranted
    if len(units) > 5:
        issues.append("More than 5 units in this job — complex job, review all units carefully")

    # ── CALCULATE CONFIDENCE LEVEL ────────────────────────────────────────────
    # Now we count how many issues were found and map to a confidence level
    # Thresholds come directly from the project spec:
    #   0 issues   = HIGH   — estimator can mostly trust this output
    #   1-2 issues = MEDIUM — estimator should review carefully
    #   3+ issues  = LOW    — estimator must verify everything manually
    issue_count = len(issues)

    if issue_count == 0:
        confidence = "HIGH"
        score = 100  # perfect — no problems found
    elif issue_count <= 2:
        confidence = "MEDIUM"
        # Score formula: 100 minus 15 for each issue
        # max(0, ...) clamps the result so it never goes negative
        # 1 issue = 85, 2 issues = 70
        score = max(0, 100 - (issue_count * 15))
    else:
        confidence = "LOW"
        # Same formula continues downward
        # 3 issues = 55, 4 = 40, 5 = 25, 6 = 10, 7+ = 0
        score = max(0, 100 - (issue_count * 15))

    # ── FLAGS FOR ESTIMATOR ───────────────────────────────────────────────────
    # These three items are ALWAYS included in the output
    # regardless of confidence level, regardless of what issues were found
    # They are NOT issues — they are permanent reminders
    # These are things the AI deliberately never fills in because they
    # require human judgment every single time:
    #
    # Man hours    — depends on job complexity, site conditions, installer experience
    # Silicone qty — must be calculated from actual perimeter linear feet
    # Packaging    — depends on panel sizes, shipping method, crate availability
    #
    # By always showing these, we ensure the estimator never forgets them
    flags_for_estimator = [
        "Man hours — estimator judgment required",
        "Silicone quantity — calculate from perimeter LF",
        "Packaging count — estimator judgment required"
    ]

    # ── RETURN FINAL RESULT ───────────────────────────────────────────────────
    # confidence — the traffic light: HIGH, MEDIUM, or LOW
    # score      — a number 0-100 the frontend can use for a progress bar or color
    # issues     — the specific problems found, shown to the estimator
    # flags      — the permanent reminders, always shown regardless of confidence
    return {
        "confidence": confidence,
        "score": score,
        "issues": issues,
        "flags_for_estimator": flags_for_estimator
    }

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Score confidence of a GFS extraction result"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to extraction result JSON file"
    )
    args = parser.parse_args()

    # Load the extraction result from file
    with open(args.input, "r") as f:
        extraction_result = json.load(f)

    print(f"\nScoring confidence for: {args.input}")
    print("-" * 40)

    result = compute_confidence(extraction_result)
    print(json.dumps(result, indent=2))