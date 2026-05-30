"""
P1 — Cover Page Extractor
Extracts all structured data from a GFS COVER page.
Owner: Jignesh
"""

import base64
import json
import httpx
import os

AZURE_API_BASE = os.environ["AZURE_AI_API_BASE"]
AZURE_API_KEY = os.environ["AZURE_AI_API_KEY"]
AZURE_API_VERSION = os.environ.get("AZURE_API_VERSION", "2024-12-01-preview")
MODEL = os.environ.get("AZURE_MODEL", "claude-sonnet-4-6")

P1_PROMPT = """You are extracting structured data from a GFS glass manufacturer shop drawing COVER page.

Extract ALL of the following:

PROJECT HEADER:
- project_address: full street address
- contractor: contractor/client name
- drawing_date: date on drawing
- quote_number: quote or order number if visible
- revision: revision letter (e.g. Rev B)

GLASS SPECIFICATION:
- glass_makeup: full glass spec string exactly as written (e.g. "5-ply 10mm LIT NanoDot")
- glass_type: e.g. LIT, IGU, monolithic
- back_paint: yes/no and color if specified
- hst: yes/no (Heat Soak Test)
- expedited: yes/no (look for EXPEDITED text anywhere on page)

FRAME:
- series: e.g. "Series 1000", "Series 2000", "Series 3000 CityScape"
- frame_material: aluminum / steel / other
- finish: anodized / powder coat / other and color

UNITS (extract each unit row from the dimensions table):
- units: array of objects, each with:
  - unit_id: label (e.g. "Unit A", "1", etc.)
  - width: raw string exactly as written (e.g. '47-5/8"')
  - length: raw string exactly as written
  - quantity: integer
  - sqft: if shown
  - perimeter: if shown
  - notes: any special notes for this unit

Respond ONLY with valid JSON matching this structure. Use null for any field not found.
Do NOT convert fractional dimensions — preserve raw strings exactly."""


def extract_cover(image_bytes: bytes) -> dict:
    """
    Extract structured data from a COVER page.

    Args:
        image_bytes: PNG image bytes of the cover page

    Returns:
        dict with project_header, glass_specification, frame, units
    """
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": MODEL,
        "max_tokens": 2000,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": P1_PROMPT},
                ],
            }
        ],
    }

    url = f"{AZURE_API_BASE}/openai/deployments/{MODEL}/messages?api-version={AZURE_API_VERSION}"
    headers = {"api-key": AZURE_API_KEY, "Content-Type": "application/json"}

    response = httpx.post(url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()

    raw = response.json()["content"][0]["text"].strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
