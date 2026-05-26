import json
import re
from error_handler import raise_vision_api_failed
import vision_client


# ── THE CLASSIFICATION PROMPT ──────────────────────────────────────────────────

CLASSIFICATION_PROMPT = """
Look at this drawing page carefully.
Classify it as EXACTLY ONE of these types:

COVER — if you see ALL of these:
  - "Glass Make-up" text on the left side
  - A table with frame component options on the right side
  - A project address in large text at the top center

PLAN_VIEW — if you see ALL of these:
  - A title containing "PLAN - UNIT" followed by a letter
  - A top-down view of a rectangular or oval shape
  - Dimension lines with measurements

MGDS_CATALOG — if you see ANY of these:
  - "SkyFloor" branding
  - A model number like GFS32X96YL-3 or GFS32X96ND-3
  - "Modular Glass Deck System" text

OTHER — everything else:
  - Section cuts (side view of frame)
  - Glass detail pages (layer stack diagram)
  - Frame piece drawings
  - Elevation drawings
  - Detail close-ups
  - FedEx labels, warranties, BOL documents

Return JSON only:
{"page_type": "COVER" | "PLAN_VIEW" | "MGDS_CATALOG" | "OTHER",
 "confidence": "HIGH" | "LOW"}
"""



VALID_PAGE_TYPES = {"COVER", "PLAN_VIEW", "MGDS_CATALOG", "OTHER"}
VALID_CONFIDENCE = {"HIGH", "LOW"}



FALLBACK_RESULT = {
    "page_type": "OTHER",
    "confidence": "LOW"
}



def classify_page(image_path: str, page_number: int) -> dict:
    """
    Classify a single PDF page image using Claude Vision.

    Args:
        image_path:  path to the page image file (e.g. /tmp/page_1.png)
        page_number: the page number within the original PDF (1-indexed)

    Returns:
        {
            "page_number": 1,
            "image_path": "/tmp/page_1.png",
            "page_type": "COVER" | "PLAN_VIEW" | "MGDS_CATALOG" | "OTHER",
            "confidence": "HIGH" | "LOW"
        }
    """

    try:
        response = vision_client.send_to_vision(
            image_path=image_path,
            prompt=CLASSIFICATION_PROMPT
        )
    except Exception as e:
        raise_vision_api_failed(details={
            "image_path": image_path,
            "page_number": page_number,
            "error": str(e)
        })

    if "error" in response:
        raise_vision_api_failed(details={
            "image_path": image_path,
            "page_number": page_number,
            "error": response["raw"]
        })

    response_text = response["text"]   # ← moved outside the if block

    parsed = _parse_json_response(response_text)


    page_type = parsed.get("page_type", "OTHER")
    confidence = parsed.get("confidence", "LOW")

    if page_type not in VALID_PAGE_TYPES:
        page_type = FALLBACK_RESULT["page_type"]
        confidence = FALLBACK_RESULT["confidence"]

    if confidence not in VALID_CONFIDENCE:
        confidence = FALLBACK_RESULT["confidence"]

    return {
        "page_number": page_number,
        "image_path": image_path,
        "page_type": page_type,
        "confidence": confidence
    }



def _parse_json_response(text: str) -> dict:
    """
    Extract and parse a JSON object from Claude's response text.

    Handles these cases:
      1. Clean JSON:        '{"page_type": "PLAN_VIEW", "confidence": "HIGH"}'
      2. Markdown wrapped:  '```json\\n{...}\\n```'
      3. Text + JSON:       'Based on the image...\\n{...}'
      4. Unparseable:       returns FALLBACK_RESULT

    Args:
        text: raw response string from Claude Vision

    Returns:
        parsed dict, or FALLBACK_RESULT if parsing fails
    """

    if not text or not text.strip():
        return FALLBACK_RESULT

   
    cleaned = re.sub(r'```(?:json)?\s*', '', text).strip()
    cleaned = cleaned.replace('```', '').strip()

   
    match = re.search(r'\{.*?\}', cleaned, re.DOTALL)

    if not match:
        return FALLBACK_RESULT

    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return FALLBACK_RESULT



def classify_all_pages(page_images: list) -> list:
    """
    Classify a list of page images in order.
    Called by fastapi_app.py after pdf_to_images.py produces the image list.

    Args:
        page_images: list of dicts like:
                     [{"page_number": 1, "image_path": "/tmp/page_1.png"}, ...]

    Returns:
        list of classification results, one per page
    """

    results = []

    for page in page_images:
        result = classify_page(
            image_path=page["image_path"],
            page_number=page["page_number"]
        )
        results.append(result)

    return results


# ── CLI INTERFACE ──────────────────────────────────────────────────────────────
# This block only runs when you execute this file directly
# python page_classifier.py --image path/to/page.png --page_number 1
# It does NOT run when another file imports this module
if __name__ == "__main__":
    import argparse
    import json

    # argparse handles command line arguments cleanly
    parser = argparse.ArgumentParser(
        description="Classify a single GFS drawing page using Claude Vision"
    )
    parser.add_argument(
        "--image",
        required=True,
        help="Path to the page image file (PNG or JPEG)"
    )
    parser.add_argument(
        "--page_number",
        type=int,
        default=1,
        help="Page number (default: 1)"
    )
    args = parser.parse_args()

    # Run the classification
    print(f"\nClassifying: {args.image}")
    print("-" * 40)

    result = classify_page(
        image_path=args.image,
        page_number=args.page_number
    )

    # Print clean output
    print(json.dumps(result, indent=2))