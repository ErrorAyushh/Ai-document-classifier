"""
P2 — Plan View Extractor
Extracts panel layout and EXPOSED FRAME data from GFS PLAN_VIEW pages.
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

P2_PROMPT = """You are extracting structural data from a GFS glass manufacturer PLAN VIEW drawing.
This is a top-down architectural drawing showing glass panel layout.

Extract the following:

EXPOSED FRAME:
- exposed_frame_dimensions: array of all dimensions labelled "EXPOSED FRAME" or "EXP. FRAME"
  Each item: { "value": raw string exactly as written, "direction": "width"/"length"/"unknown" }
- NOTE: EXPOSED FRAME is the visible aluminum border. Extract raw strings, do NOT convert fractions.

PANELS:
- panel_count: total number of glass panels shown
- panel_layout: description of layout (e.g. "3x2 grid", "L-shape", "linear row of 5")
- panel_shapes: list of shapes detected — "rectangle", "square", "ellipse", "oval", "custom"
- has_custom_shape: true/false — if ANY non-rectangular panel exists, set true and flag for manual review

ROOM / LOCATION:
- location_label: room name or area label if shown (e.g. "Living Room", "Roof Deck", "Stairwell")
- floor_level: if specified (e.g. "Level 2", "Roof")

NOTES:
- drawing_notes: any written notes or special instructions visible on this page
- flags: array of anything that needs estimator attention (custom shapes, unclear dimensions, conflicting values)

Respond ONLY with valid JSON. Use null for missing fields. Preserve all dimension strings exactly as written."""


def extract_plan(image_bytes: bytes) -> dict:
    """
    Extract panel layout and exposed frame data from a PLAN_VIEW page.

    Args:
        image_bytes: PNG image bytes of the plan view page

    Returns:
        dict with exposed_frame, panels, room/location, notes, flags
    """
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": MODEL,
        "max_tokens": 1500,
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
                    {"type": "text", "text": P2_PROMPT},
                ],
            }
        ],
    }

    url = f"{AZURE_API_BASE}/openai/deployments/{MODEL}/messages?api-version={AZURE_API_VERSION}"
    headers = {"api-key": AZURE_API_KEY, "Content-Type": "application/json"}

    response = httpx.post(url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()

    raw = response.json()["content"][0]["text"].strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
