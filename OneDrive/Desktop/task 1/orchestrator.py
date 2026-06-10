"""
Orchestrates the full GFS extraction pipeline.
Runs non-AI first, calls Vision only for pages that need it.
"""

import json
import os
import shutil
import tempfile

from pdf_processor import extract_pages, render_page
from page_classifier import classify_page
from cover_extractor import extract_cover
from plan_extractor import extract_plan
from ocr_plan_extractor import extract_plan_ocr
from job_aggregator import aggregate_pages
from page_validator import validate_page
from dim_converter import convert_dim_group
from rules_engine import apply_all_rules
from confidence_scorer import compute_confidence
from vision_client import call_vision


# ---------------------------------------------------------------------------
# TokenTracker — tracks Vision API token usage across all calls in one run
# ---------------------------------------------------------------------------

class TokenTracker:
    """
    Accumulates token usage across multiple Vision API calls made during
    a single pipeline run.
    """

    def __init__(self):
        self.prompt     = 0
        self.completion = 0
        self.total      = 0
        self.calls      = 0

    def add(self, usage: dict):
        """
        Adds token counts from a usage dict returned by vision_client.

        Parameters
        ----------
        usage : dict
            Dict with keys: prompt_tokens, completion_tokens, total_tokens.
            Missing keys are treated as 0.
        """
        if not usage:
            return
        self.prompt     += usage.get("prompt_tokens",     0) or 0
        self.completion += usage.get("completion_tokens", 0) or 0
        self.total      += usage.get("total_tokens",      0) or 0
        self.calls      += 1

    def summary(self) -> dict:
        """
        Returns a summary dict of all accumulated token usage.

        Returns
        -------
        dict
            Keys: api_calls, prompt_tokens, completion_tokens, total_tokens.
        """
        return {
            "api_calls":         self.calls,
            "prompt_tokens":     self.prompt,
            "completion_tokens": self.completion,
            "total_tokens":      self.total,
        }


# ---------------------------------------------------------------------------
# normalise_dims — runs convert_dim_group on known dimension fields
# ---------------------------------------------------------------------------

def normalise_dims(extraction: dict) -> dict:
    """
    Runs convert_dim_group() on plan_dims, section_dims, and glass_detail_dims
    if they exist and are not None in the extraction dict.

    Mutates the dict in place and returns it.

    Parameters
    ----------
    extraction : dict
        Extraction result dict from a non-AI extractor or Vision API call.

    Returns
    -------
    dict
        The same dict with dimension fields converted.
    """
    for field in ("plan_dims", "section_dims", "glass_detail_dims"):
        if extraction.get(field) is not None:
            extraction[field] = convert_dim_group(extraction[field])
    return extraction


# ---------------------------------------------------------------------------
# _build_targeted_prompt
# Reads the non-AI/OCR result, finds which fields are missing,
# and builds a Vision prompt asking for ONLY those fields.
# ---------------------------------------------------------------------------

def _build_targeted_prompt(base_result: dict) -> str:
    """
    Inspects base_result for missing plan fields and builds a Vision
    prompt that asks for ONLY those missing fields.

    Missing field checks:
      - unit_letter         → None or empty string
      - unit_qty            → None
      - exposed_frame_width → None (the whole dict is None)
      - exposed_frame_length→ None (the whole dict is None)

    If nothing is missing, returns empty string — caller should not
    call Vision in that case.

    The JSON key names in the returned prompt must match exactly what
    _merge_plan_results() reads from vision_data. Do not rename them.

    Returns
    -------
    str
        Targeted prompt string, or "" if nothing is missing.
    """
    missing_fields   = []
    missing_sections = []

    # -- Check unit_letter ----------------------------------------------------
    if not base_result.get("unit_letter"):
        missing_fields.append(
            '- "unit_letter": single uppercase letter from the page title\n'
            '  e.g. "PLAN - UNIT A" → "A"\n'
            '  Return null if not found.'
        )
        missing_sections.append('  "unit_letter": null')

    # -- Check unit_qty -------------------------------------------------------
    if base_result.get("unit_qty") is None:
        missing_fields.append(
            '- "unit_qty": integer quantity from "QTY X" in the page title\n'
            '  e.g. "PLAN - UNIT A - QTY 1" → 1\n'
            '  Return null if not found.'
        )
        missing_sections.append('  "unit_qty": null')

    # -- Check exposed_frame_width --------------------------------------------
    if not base_result.get("exposed_frame_width"):
        missing_fields.append(
            '- "exposed_frame_width_raw": the SMALLER dimension labelled\n'
            '  "(EXPOSED FRAME)" or "(EXP. FRAME)" on this drawing.\n'
            '  Preserve raw string exactly — e.g. "47-5/8\\"", "57\\""\n'
            '  Do NOT extract GLASS, FLANGE, OUT TO OUT, or ROUGH OPENING dimensions.\n'
            '  Return null if not found.'
        )
        missing_sections.append('  "exposed_frame_width_raw": null')

    # -- Check exposed_frame_length -------------------------------------------
    if not base_result.get("exposed_frame_length"):
        missing_fields.append(
            '- "exposed_frame_length_raw": the LARGER dimension labelled\n'
            '  "(EXPOSED FRAME)" or "(EXP. FRAME)" on this drawing.\n'
            '  Preserve raw string exactly — e.g. "96\\"", "8\'-0 5/8\\""\n'
            '  Do NOT extract GLASS, FLANGE, OUT TO OUT, or ROUGH OPENING dimensions.\n'
            '  Return null if not found.'
        )
        missing_sections.append('  "exposed_frame_length_raw": null')

    # -- Nothing missing ------------------------------------------------------
    if not missing_fields:
        return ""

    # -- Build prompt ---------------------------------------------------------
    fields_block  = "\n\n".join(missing_fields)
    schema_block  = "{\n" + ",\n".join(missing_sections) + "\n}"

    prompt = (
        "You are extracting specific missing fields from a GFS glass manufacturer "
        "PLAN VIEW drawing.\n\n"
        "Extract ONLY the following fields. Do not extract anything else.\n\n"
        f"FIELDS TO EXTRACT:\n{fields_block}\n\n"
        "CRITICAL RULES:\n"
        "- Only extract dimensions explicitly labelled EXPOSED FRAME or EXP. FRAME\n"
        "- Preserve all dimension strings exactly as written — do NOT convert fractions\n"
        "- Do NOT extract dimensions labelled GLASS, FLANGE, OUT TO OUT, "
        "ROUGH OPENING, or CURB\n"
        "- Return null for any field you cannot find with confidence\n\n"
        f"Return ONLY this exact JSON — no markdown, no explanation:\n{schema_block}"
    )

    return prompt


# ---------------------------------------------------------------------------
# _merge_plan_results
# Combines non-AI/OCR base result with targeted Vision result.
# Non-AI keeps its values. Vision fills in only what is null.
# ---------------------------------------------------------------------------

def _merge_plan_results(base_result: dict, vision_data: dict) -> dict:
    """
    Merges base (non-AI or OCR) result with targeted Vision output.

    Merge rules per field:
      Non-AI has value + Vision missing  → keep Non-AI   (Case 1)
      Non-AI missing  + Vision has value → use Vision    (Case 2)
      Both have values                   → Vision wins   (Case 3)
      (Case 3 is rare — Vision was only asked for missing fields)

    Vision key → merged key mapping:
      "unit_letter"             → "unit_letter"
      "unit_qty"                → "unit_qty"
      "exposed_frame_width_raw" → builds exposed_frame_width dict
      "exposed_frame_length_raw"→ builds exposed_frame_length dict

    Parameters
    ----------
    base_result  : dict  Output from extract_plan() or extract_plan_ocr()
    vision_data  : dict  Parsed JSON from targeted Vision call

    Returns
    -------
    dict
        Merged result in non-AI flat schema format.
    """
    merged = dict(base_result)
    merged.pop("needs_vision", None)
    merged["method"] = "non_ai+vision"

    # -- unit_letter ----------------------------------------------------------
    v_letter = vision_data.get("unit_letter")
    if v_letter:
        # Vision wins if it has a value (Case 2 or 3)
        merged["unit_letter"] = str(v_letter).strip().upper()

    # -- unit_qty -------------------------------------------------------------
    v_qty = vision_data.get("unit_qty")
    if v_qty is not None:
        try:
            merged["unit_qty"] = int(v_qty)
        except (TypeError, ValueError):
            pass

    # -- exposed_frame_width --------------------------------------------------
    v_width_raw = vision_data.get("exposed_frame_width_raw")
    if v_width_raw:
        dec = _raw_to_decimal(v_width_raw)
        if dec is not None:
            merged["exposed_frame_width"] = {
                "raw":     str(v_width_raw).strip(),
                "decimal": dec
            }

    # -- exposed_frame_length -------------------------------------------------
    v_length_raw = vision_data.get("exposed_frame_length_raw")
    if v_length_raw:
        dec = _raw_to_decimal(v_length_raw)
        if dec is not None:
            merged["exposed_frame_length"] = {
                "raw":     str(v_length_raw).strip(),
                "decimal": dec
            }

    return merged


# ---------------------------------------------------------------------------
# _map_plan_to_aggregator
# Converts the merged flat non-AI result to the schema job_aggregator expects.
# ---------------------------------------------------------------------------

def _map_plan_to_aggregator(result: dict) -> dict:
    """
    Converts a flat non-AI/OCR/merged plan result into the dict structure
    that job_aggregator.aggregate_pages() expects.

    Aggregator expects:
      extraction["page_type"]  → "PLAN_VIEW"
      extraction["sheet_id"]   → None (non-AI does not know sheet IDs)
      extraction["plan_dims"]  → dict with _raw and _in keys, or None

    The plan_dims structure mirrors what dim_converter produces so
    normalise_dims() can run on it if needed downstream.

    Parameters
    ----------
    result : dict
        Output from extract_plan(), extract_plan_ocr(), or _merge_plan_results().

    Returns
    -------
    dict
        Aggregator-compatible extraction dict.
    """
    width  = result.get("exposed_frame_width")
    length = result.get("exposed_frame_length")

    # Build plan_dims only if at least one dimension was found
    if width or length:
        plan_dims = {
            "exposed_frame_width_raw":  width["raw"]     if width  else None,
            "exposed_frame_width_in":   width["decimal"] if width  else None,
            "exposed_frame_height_raw": length["raw"]    if length else None,
            "exposed_frame_height_in":  length["decimal"] if length else None,
            "_conversion_warnings":     []
        }
    else:
        plan_dims = None

    return {
        "page_type":         "PLAN_VIEW",
        "sheet_id":          None,
        "unit_letter":       result.get("unit_letter"),
        "unit_qty":          result.get("unit_qty"),
        "shape":             result.get("shape", "RECTANGULAR"),
        "panel_count":       result.get("panel_count"),
        "plan_dims":         plan_dims,
        "section_dims":      None,
        "glass_detail_dims": None,
        "method":            result.get("method", "non_ai"),
    }


# ---------------------------------------------------------------------------
# _raw_to_decimal
# Inline dimension converter — mirrors extract_plan._to_decimal.
# Lives here to avoid circular imports between orchestrator and extractors.
# ---------------------------------------------------------------------------

def _raw_to_decimal(raw: str):
    """
    Converts a raw GFS dimension string to decimal inches.
    Returns None if conversion fails.

    Supported formats:
      "47-5/8\\"  → 47.625
      "4'-3\\""   → 51.0
      "4'-3-1/2\\"→ 51.5
      "63\\""     → 63.0
      "39.625"    → 39.625
    """
    import re as _re
    try:
        s = str(raw).strip().replace('"', '').replace('\u2019', "'")

        m = _re.match(r"^(\d+)'\s*(\d+)[\-\s](\d+)/(\d+)$", s)
        if m:
            return int(m.group(1))*12 + int(m.group(2)) + int(m.group(3))/int(m.group(4))

        m = _re.match(r"^(\d+)'\s*(\d+)$", s)
        if m:
            return int(m.group(1))*12 + int(m.group(2))

        m = _re.match(r"^(\d+)[\-\s](\d+)/(\d+)$", s)
        if m:
            return int(m.group(1)) + int(m.group(2))/int(m.group(3))

        m = _re.match(r"^(\d+(?:\.\d+)?)$", s)
        if m:
            return float(m.group(1))

        return None
    except (ValueError, ZeroDivisionError):
        return None
    
    # ---------------------------------------------------------------------------
# _merge_non_ai_and_ocr
# Combines Non-AI regex result with OCR image result.
# Called before building targeted prompt so we start with the best
# possible base — preserving everything both methods found.
# ---------------------------------------------------------------------------

def _merge_non_ai_and_ocr(non_ai_result: dict, ocr_result: dict) -> dict:
    """
    Merges Non-AI and OCR plan extraction results into one base result.

    Called when BOTH non_ai_result.needs_vision=True AND
    ocr_result.needs_vision=True — meaning neither method alone
    extracted all required fields.

    Merge rules per field:
      CASE 1: Non-AI has value, OCR missing  → keep Non-AI
      CASE 2: Non-AI missing,  OCR has value → use OCR
      CASE 3: Both have values               → OCR wins
              (OCR is image-based, generally more reliable than regex)

    Fields merged:
      - unit_letter
      - unit_qty
      - exposed_frame_width   (dict with raw + decimal)
      - exposed_frame_length  (dict with raw + decimal)
      - shape
      - panel_count
      - drawing_notes         (union of both lists)

    Parameters
    ----------
    non_ai_result : dict   Output from extract_plan()
    ocr_result    : dict   Output from extract_plan_ocr()

    Returns
    -------
    dict
        Merged result in non-AI flat schema format.
        method = "non_ai+ocr"
        needs_vision = True (caller checks missing fields next)
    """
    merged = {}

    # -- page_number: prefer OCR (it ran on the rendered image) ---------------
    merged["page_number"] = (
        ocr_result.get("page_number")
        or non_ai_result.get("page_number")
    )

    merged["method"]       = "non_ai+ocr"
    merged["needs_vision"] = True   # caller will re-check after merge

    # -- unit_letter ----------------------------------------------------------
    # Case 3: OCR wins if both present
    # Case 2: OCR fills if non-AI missing
    # Case 1: Non-AI kept if OCR missing
    ocr_letter    = ocr_result.get("unit_letter")
    non_ai_letter = non_ai_result.get("unit_letter")

    if ocr_letter:
        merged["unit_letter"] = ocr_letter          # Case 2 or 3
    elif non_ai_letter:
        merged["unit_letter"] = non_ai_letter       # Case 1
    else:
        merged["unit_letter"] = None

    # -- unit_qty -------------------------------------------------------------
    ocr_qty    = ocr_result.get("unit_qty")
    non_ai_qty = non_ai_result.get("unit_qty")

    if ocr_qty is not None:
        merged["unit_qty"] = ocr_qty                # Case 2 or 3
    elif non_ai_qty is not None:
        merged["unit_qty"] = non_ai_qty             # Case 1
    else:
        merged["unit_qty"] = None

    # -- exposed_frame_width --------------------------------------------------
    ocr_width    = ocr_result.get("exposed_frame_width")
    non_ai_width = non_ai_result.get("exposed_frame_width")

    if ocr_width:
        merged["exposed_frame_width"] = ocr_width   # Case 2 or 3
    elif non_ai_width:
        merged["exposed_frame_width"] = non_ai_width # Case 1
    else:
        merged["exposed_frame_width"] = None

    # -- exposed_frame_length -------------------------------------------------
    ocr_length    = ocr_result.get("exposed_frame_length")
    non_ai_length = non_ai_result.get("exposed_frame_length")

    if ocr_length:
        merged["exposed_frame_length"] = ocr_length  # Case 2 or 3
    elif non_ai_length:
        merged["exposed_frame_length"] = non_ai_length # Case 1
    else:
        merged["exposed_frame_length"] = None

    # -- shape ----------------------------------------------------------------
    # OCR currently always returns "RECTANGULAR" — prefer non-AI if it
    # detected something more specific like ELLIPSE or CUSTOM
    ocr_shape    = ocr_result.get("shape", "RECTANGULAR")
    non_ai_shape = non_ai_result.get("shape", "RECTANGULAR")

    if non_ai_shape and non_ai_shape != "RECTANGULAR":
        merged["shape"] = non_ai_shape              # Non-AI detected non-default
    else:
        merged["shape"] = ocr_shape

    # -- panel_count ----------------------------------------------------------
    ocr_count    = ocr_result.get("panel_count")
    non_ai_count = non_ai_result.get("panel_count")

    if ocr_count is not None:
        merged["panel_count"] = ocr_count           # Case 2 or 3
    elif non_ai_count is not None:
        merged["panel_count"] = non_ai_count        # Case 1
    else:
        merged["panel_count"] = None

    # -- drawing_notes --------------------------------------------------------
    # Union of both lists — keep all notes from both passes
    ocr_notes    = ocr_result.get("drawing_notes") or []
    non_ai_notes = non_ai_result.get("drawing_notes") or []
    seen         = set()
    combined     = []
    for note in (ocr_notes + non_ai_notes):
        if note and note not in seen:
            seen.add(note)
            combined.append(note)
    merged["drawing_notes"] = combined

    # -- Recalculate needs_vision after merge ---------------------------------
    # If merge filled all required fields, Vision is no longer needed
    has_width  = merged.get("exposed_frame_width")  is not None
    has_length = merged.get("exposed_frame_length") is not None
    has_letter = merged.get("unit_letter")          is not None

    merged["needs_vision"] = not (has_width and has_length and has_letter)

    return merged

# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def run(pdf_path: str) -> dict:
    """
    Runs the full GFS extraction pipeline on the given PDF.

    Parameters
    ----------
    pdf_path : str
        Absolute or relative path to the PDF file to process.

    Returns
    -------
    dict
        Structured extraction result including merged page data, rules output,
        confidence score, and token usage summary.
        On unhandled error returns {"status": "error", "error": ..., "pdf_path": ...}.
    """
    temp_dir = None

    try:
        # ------------------------------------------------------------------
        # STEP 1 — Extract text from all pages
        # ------------------------------------------------------------------
        pages = extract_pages(pdf_path)

        # ------------------------------------------------------------------
        # STEP 2 — Setup
        # ------------------------------------------------------------------
        temp_dir         = tempfile.mkdtemp()
        tracker          = TokenTracker()
        all_page_results = []   # list of {"page_number": N, "extraction": dict}

        # ------------------------------------------------------------------
        # STEP 3 — Classify and process each page
        # ------------------------------------------------------------------
        for page in pages:
            page_number = page["page_number"]
            text        = page.get("text", "")
            is_scanned  = page.get("is_scanned", False)

            # Classify the page
            classification = classify_page(text, page_number)
            page_type      = classification.get("page_type", "OTHER")

            # --------------------------------------------------------------
            # OTHER → skip entirely
            # --------------------------------------------------------------
            if page_type == "OTHER":
                continue

            # --------------------------------------------------------------
            # SCANNED (any type) → always Vision
            # --------------------------------------------------------------
            if page_type == "SCANNED" or is_scanned:
                image_path = render_page(pdf_path, page_number, temp_dir)
                result     = call_vision(image_path, COMBINED_EXTRACTION_PROMPT, max_completion_tokens=2000)
                tracker.add(result.get("token_usage", {}))

                if result["ok"]:
                    extraction = result["data"]
                else:
                    extraction = {"page_type": "OTHER", "sheet_id": None}

                validate_page(extraction)
                normalise_dims(extraction)
                all_page_results.append({"page_number": page_number, "extraction": extraction})
                continue

            # --------------------------------------------------------------
            # COVER page
            # --------------------------------------------------------------
            if page_type == "COVER":
                non_ai_result = extract_cover(text, page_number)

                if non_ai_result.get("needs_vision"):
                    image_path = render_page(pdf_path, page_number, temp_dir)
                    result     = call_vision(image_path, COMBINED_EXTRACTION_PROMPT, max_completion_tokens=2000)
                    tracker.add(result.get("token_usage", {}))

                    if result["ok"]:
                        extraction = result["data"]
                    else:
                        extraction = non_ai_result   # non-AI as fallback

                    validate_page(extraction)
                    normalise_dims(extraction)
                else:
                    extraction              = non_ai_result
                    extraction["page_type"] = "COVER"
                    extraction["sheet_id"]  = None

                all_page_results.append({"page_number": page_number, "extraction": extraction})
                continue

            # --------------------------------------------------------------
            # PLAN_VIEW page
            # --------------------------------------------------------------
            if page_type == "PLAN_VIEW":

                # ── STEP 1: Non-AI regex extraction ──────────────────────────
                non_ai_result = extract_plan(text, page_number)

                if not non_ai_result.get("needs_vision"):
                    # Non-AI got everything — done, no image needed
                    extraction = _map_plan_to_aggregator(non_ai_result)
                    all_page_results.append({"page_number": page_number, "extraction": extraction})
                    continue

                # ── STEP 2: OCR extraction ────────────────────────────────────
                image_path = render_page(pdf_path, page_number, temp_dir)
                ocr_result = extract_plan_ocr(image_path, page_number)

                if not ocr_result.get("needs_vision"):
                    # OCR got everything — done, no Vision API call needed
                    extraction = _map_plan_to_aggregator(ocr_result)
                    all_page_results.append({"page_number": page_number, "extraction": extraction})
                    continue

                # ── STEP 3: Merge Non-AI + OCR before building targeted prompt
                # Both methods ran but both have gaps. Merge preserves everything
                # both methods found — unit_letter from Non-AI, width from OCR etc.
                # The merged result is used as base for targeted prompt so Vision
                # is only asked for fields NEITHER method could extract.
                base_result = _merge_non_ai_and_ocr(non_ai_result, ocr_result)

                # If merge filled all required fields, Vision not needed
                if not base_result.get("needs_vision"):
                    extraction = _map_plan_to_aggregator(base_result)
                    all_page_results.append({"page_number": page_number, "extraction": extraction})
                    continue

                targeted_prompt = _build_targeted_prompt(base_result)

                if targeted_prompt:
                    # ── STEP 4: Vision extracts ONLY missing fields ───────────
                    result = call_vision(image_path, targeted_prompt, max_completion_tokens=500)
                    tracker.add(result.get("token_usage", {}))

                    if result["ok"]:
                        # ── STEP 5: Merge non-AI base + Vision missing fields ─
                        merged     = _merge_plan_results(base_result, result["data"])
                        extraction = _map_plan_to_aggregator(merged)
                    else:
                        # Vision failed — use best non-AI result we have
                        extraction = _map_plan_to_aggregator(base_result)
                else:
                    # No missing fields detected (safety fallback)
                    extraction = _map_plan_to_aggregator(base_result)

                # ── STEP 6: Aggregator ────────────────────────────────────────
                all_page_results.append({"page_number": page_number, "extraction": extraction})
                continue

            # --------------------------------------------------------------
            # MGDS_CATALOG, SECTION, GLASS_DETAIL → always Vision
            # --------------------------------------------------------------
            if page_type in ("MGDS_CATALOG", "SECTION", "GLASS_DETAIL"):
                image_path = render_page(pdf_path, page_number, temp_dir)
                result     = call_vision(image_path, COMBINED_EXTRACTION_PROMPT, max_completion_tokens=2000)
                tracker.add(result.get("token_usage", {}))

                if result["ok"]:
                    extraction = result["data"]
                else:
                    extraction = {"page_type": page_type, "sheet_id": None}

                validate_page(extraction)
                normalise_dims(extraction)
                all_page_results.append({"page_number": page_number, "extraction": extraction})
                continue

        # ------------------------------------------------------------------
        # STEP 4 — Aggregate all page results into one merged dict
        # ------------------------------------------------------------------
        merged = aggregate_pages(all_page_results)

        # ------------------------------------------------------------------
        # STEP 5 — Build cover_data for rules engine
        # ------------------------------------------------------------------
        cover_data = {
            "project_header": {
                "project_address": merged.get("project_address"),
                "quote_number":    None,
                "revision":        None,
                "contractor":      merged.get("approved_by_company"),
                "drawing_date":    merged.get("approval_date"),
            },
            "glass_specification": {
                "glass_makeup": merged.get("glass_makeup"),
                "expedited":    merged.get("expedited") or False,
                "hst":          False,
                "back_paint":   False,
            },
            "frame": {
                "series":        "Series 2000",
                "frame_material": None,
            },
            "units": [],
        }

        # ------------------------------------------------------------------
        # STEP 6 — Extract plan_data for rules engine
        # ------------------------------------------------------------------
        plan_data = merged.get("plan_dims")

        # ------------------------------------------------------------------
        # STEP 7 — Apply rules engine
        # ------------------------------------------------------------------
        rules_result = apply_all_rules(cover_data, plan_data)

        # ------------------------------------------------------------------
        # STEP 8 — Compute confidence
        # ------------------------------------------------------------------
        confidence_result = compute_confidence(rules_result)

        # ------------------------------------------------------------------
        # STEP 9 — Return final result
        # ------------------------------------------------------------------
        return {
            "status":            "ok",
            "pdf_path":          pdf_path,
            "pages_processed":   len(all_page_results),
            "pages_skipped":     len(pages) - len(all_page_results),
            "vision_calls_made": tracker.calls,
            "token_summary":     tracker.summary(),
            "merged_result":     merged,
            "rules_output":      rules_result,
            "confidence":        confidence_result,
            "merge_warnings":    merged.get("_merge_warnings", []),
        }

    except Exception as exc:
        return {
            "status":   "error",
            "error":    str(exc),
            "pdf_path": pdf_path,
        }

    finally:
        # Always clean up temp directory — guard against it never being set
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
