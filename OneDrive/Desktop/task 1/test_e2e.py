"""
End-to-end pipeline test for GFS-AI.
ZIP → PDF → classify → extract → decision tree → confidence → Excel

Run from D:\GFS-Ai:
    $env:PATH += ";C:\Program Files\Tesseract-OCR"
    $env:TESSDATA_PREFIX = "C:\Program Files\Tesseract-OCR\tessdata"
    python -m tests.test_e2e
"""

import os
import tempfile
import json

from ai_engine.non_ai.folder_processor import process_zip
from ai_engine.non_ai.pdf_processor import extract_pages, render_page
from ai_engine.non_ai.page_classifier import classify_page
from ai_engine.non_ai.cover_extractor import extract_cover
from ai_engine.non_ai.plan_extractor import extract_plan
from ai_engine.non_ai.ocr_plan_extractor import extract_plan_ocr
from ai_engine.decision_tree import build_job_data
from ai_engine.confidence_scorer import score_extraction
from ai_engine.excel_filler import fill_workbook

ZIPS = [
    r"D:\GFS-Ai\Gfs_projects\182 Robinson St - Series 1000.zip",
    r"D:\GFS-Ai\Gfs_projects\464 Greenwich St - Series 2000.zip",
    r"D:\GFS-Ai\Gfs_projects\21280 Avalon Replacement.zip",
    r"D:\GFS-Ai\Gfs_projects\87001 Mill Hill Rd - Custom Ovals.zip",
]

for zip_path in ZIPS:
    name = zip_path.split("\\")[-1]
    print(f"\n{'='*65}")
    print(f"E2E TEST: {name}")
    print(f"{'='*65}")

    try:
        # STEP 1 — Folder processor
        folder_result = process_zip(zip_path)
        pdf_path = folder_result.get("selected_pdf")
        if not pdf_path:
            print("  ERROR: No PDF found in ZIP")
            continue
        print(f"  [1] PDF: {pdf_path.split(chr(92))[-1]}")

        # STEP 2 — Extract pages
        pages = extract_pages(pdf_path)
        print(f"  [2] Pages: {len(pages)} total")

        cover_result = None
        plan_result = None

        with tempfile.TemporaryDirectory() as tmp_dir:

            # STEP 3 — Classify and extract
            for page in pages:
                page_num = page["page_number"]
                is_scanned = page["is_scanned"]

                if not is_scanned:
                    cls = classify_page(page["text"], page_num)
                    page_type = cls["page_type"]

                    if page_type == "COVER" and cover_result is None:
                        cover_result = extract_cover(page["text"], page_num)
                        print(f"  [3] Cover extracted from page {page_num} — needs_vision={cover_result.get('needs_vision')}")

                    elif page_type == "PLAN_VIEW" and plan_result is None:
                        plan_result = extract_plan(page["text"], page_num)
                        print(f"  [3] Plan extracted from page {page_num} (regex) — needs_vision={plan_result.get('needs_vision')}")

                else:
                    # Scanned — run OCR
                    if plan_result is None:
                        try:
                            image_path = render_page(pdf_path, page_num, tmp_dir)
                            ocr_result = extract_plan_ocr(image_path, page_num)
                            w = ocr_result.get("exposed_frame_width")
                            l = ocr_result.get("exposed_frame_length")
                            u = ocr_result.get("unit_letter")
                            if u or w or l:
                                plan_result = ocr_result
                                print(f"  [3] Plan extracted from page {page_num} (OCR) — unit={u} w={w} l={l}")
                        except Exception as e:
                            pass  # Tesseract errors suppressed

        # STEP 4 — Decision tree
        if cover_result is None:
            cover_result = {
                "page_number": 0, "method": "non_ai", "needs_vision": True,
                "project_header": {"project_address": None, "quote_number": None, "revision": None, "contractor": None, "drawing_date": None},
                "glass_specification": {"glass_makeup": None, "expedited": False, "hst": False, "back_paint": False},
                "frame": {"series": "Series 2000", "frame_material": None},
                "units": [],
            }
            print(f"  [3] Cover: not found — using empty defaults")

        if plan_result is None:
            plan_result = {
                "page_number": 0, "method": "non_ai", "needs_vision": True,
                "unit_letter": None, "unit_qty": None,
                "exposed_frame_width": None, "exposed_frame_length": None,
                "shape": "RECTANGULAR", "panel_count": None, "drawing_notes": [],
            }
            print(f"  [3] Plan: not found — using empty defaults")

        job_data = build_job_data(cover_result, plan_result)
        print(f"  [4] Decision tree:")
        print(f"      job_type:   {job_data['job_type']}")
        print(f"      frame_type: {job_data['frame_type']}")
        print(f"      glass_type: {job_data['glass_type']}")
        print(f"      nanodot:    {job_data['nanodot']}")
        print(f"      heat_soak:  {job_data['heat_soak']}")
        print(f"      duty:       {job_data['duty']} ({job_data['duty_rate']})")
        u = job_data['units'][0] if job_data['units'] else {}
        print(f"      width:      {u.get('width_inches')}")
        print(f"      length:     {u.get('length_inches')}")

        # STEP 5 — Confidence scoring
        frame_result = {"job_type": job_data["job_type"], "duty": job_data["duty"], "duty_rate": job_data["duty_rate"]}
        glass_result = {"glass_type": job_data["glass_type"], "nanodot": job_data["nanodot"]}
        confidence = score_extraction(cover_result, plan_result, frame_result, glass_result)
        print(f"  [5] Confidence: {confidence['overall']} ({confidence['score']:.2f}) — manual_review={confidence['manual_review_required']}")
        for f in confidence["flags"]:
            icon = "🔴" if f["level"] == "ERROR" else "⚠️" if f["level"] == "WARN" else "ℹ️"
            print(f"      {icon} {f['field']}: {f['message'][:80]}")

        # STEP 6 — Fill Excel
        if job_data["job_type"] == "TYPE_CUSTOM":
            print(f"  [6] Excel: SKIPPED — TYPE_CUSTOM requires manual review")
        else:
            result = fill_workbook(job_data)
            status = result.get("status")
            output = result.get("output_path")
            print(f"  [6] Excel: status={status} output={output}")

        print(f"\n  ✅ Pipeline complete for {name.split('.')[0]}")

    except Exception as e:
        import traceback
        print(f"  ❌ PIPELINE ERROR: {type(e).__name__}: {str(e)[:200]}")
        traceback.print_exc()

print(f"\n{'='*65}")
print("E2E TEST COMPLETE")
print(f"{'='*65}")