"""
Vision Client Module — Azure OpenAI
=====================================
All Vision API calls in this project go through this file only.

No other file in the project makes direct HTTP calls to the Vision API.
Every file in ai_engine/vision/ uses call_vision() from this module.

Environment variables read at module level:
- AZURE_OPENAI_API_KEY   : API key (falls back to AZURE_OPENAI_KEY).
- AZURE_OPENAI_BASE_URL  : Base URL for the Azure OpenAI endpoint
                           (default: "https://openaikeyforgfs.openai.azure.com/openai/v1/").
- AZURE_OPENAI_DEPLOYMENT: Model deployment name (default: "gpt-5-2").

Module-level constants:
- MAX_IMAGE_PX       : Maximum image dimension in pixels before resizing (1568).
- RATE_LIMIT_WAIT_S  : Seconds to wait before retrying on rate limit (5).
- SYSTEM_INSTRUCTION : System prompt enforcing strict JSON-only output.

API keys are never logged.
Exceptions are never raised — all errors are returned as structured dicts.
"""

import base64
import io
import json
import mimetypes
import os
import re
import time
from mimetypes import guess_type

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

# ---------------------------------------------------------------------------
# Load .env at module level
# ---------------------------------------------------------------------------
load_dotenv()

# ---------------------------------------------------------------------------
# Read environment variables at module level
# ---------------------------------------------------------------------------
AZURE_OPENAI_API_KEY    = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_KEY", "")
AZURE_OPENAI_BASE_URL   = os.environ.get("AZURE_OPENAI_BASE_URL", "https://openaikeyforgfs.openai.azure.com/openai/v1/")
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5-2")

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
MAX_IMAGE_PX       = 1568
RATE_LIMIT_WAIT_S  = 5
SYSTEM_INSTRUCTION = (
    "You are a JSON extraction engine specialising in architectural shop drawings. "
    "Output ONLY a single valid JSON object. "
    "No markdown code fences, no backticks, no preamble, no trailing commentary. "
    "Begin your response with { and end with }."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_image_as_data_url(image_path: str) -> str:
    """
    Reads an image file, resizes it if either dimension exceeds MAX_IMAGE_PX,
    and returns a base64-encoded data URL string.

    Falls back to raw bytes (no resize) if Pillow is not installed.

    Parameters
    ----------
    image_path : str
        Path to the image file.

    Returns
    -------
    str
        Base64 data URL of the form "data:<mime_type>;base64,<data>".
    """
    mime_type, _ = guess_type(image_path)
    if not mime_type:
        mime_type = "image/jpeg"

    try:
        from PIL import Image

        with Image.open(image_path) as img:
            # Convert to RGB if needed (e.g. RGBA PNG)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            # Resize if either dimension exceeds MAX_IMAGE_PX
            width, height = img.size
            if width > MAX_IMAGE_PX or height > MAX_IMAGE_PX:
                scale  = MAX_IMAGE_PX / max(width, height)
                new_w  = int(width  * scale)
                new_h  = int(height * scale)
                img    = img.resize((new_w, new_h))

            buffer = io.BytesIO()
            # Save in the appropriate format
            fmt = "PNG" if mime_type == "image/png" else "JPEG"
            img.save(buffer, format=fmt)
            image_bytes = buffer.getvalue()

    except ImportError:
        # Pillow not installed — use raw bytes without resizing
        with open(image_path, "rb") as f:
            image_bytes = f.read()

    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _extract_json_substring(text: str) -> str:
    """
    Extracts the first valid JSON object substring from the given text
    by finding the first '{' and the last '}'.

    Parameters
    ----------
    text : str
        Raw text that may contain a JSON object.

    Returns
    -------
    str
        Substring from the first '{' to the last '}' inclusive.
        Returns the original text unchanged if neither brace is found.
    """
    first_brace = text.find("{")
    last_brace  = text.rfind("}")

    if first_brace == -1 or last_brace == -1 or last_brace < first_brace:
        return text

    return text[first_brace : last_brace + 1]


def _extract_token_usage(completion) -> dict:
    """
    Extracts token usage information from an OpenAI completion object.

    Parameters
    ----------
    completion : openai.types.chat.ChatCompletion
        The completion object returned by the OpenAI API.

    Returns
    -------
    dict
        Dictionary with keys:
        - prompt_tokens     : int
        - completion_tokens : int
        - total_tokens      : int
        - reasoning_tokens  : int  (0 if not present)
    """
    usage = getattr(completion, "usage", None)
    if not usage:
        return {
            "prompt_tokens":     0,
            "completion_tokens": 0,
            "total_tokens":      0,
            "reasoning_tokens":  0,
        }

    # reasoning_tokens lives inside completion_tokens_details on some models
    reasoning_tokens = 0
    details = getattr(usage, "completion_tokens_details", None)
    if details:
        reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0

    return {
        "prompt_tokens":     getattr(usage, "prompt_tokens",     0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "total_tokens":      getattr(usage, "total_tokens",      0) or 0,
        "reasoning_tokens":  reasoning_tokens,
    }


def _call_api(client: OpenAI, image_data_url: str, prompt: str, max_completion_tokens: int) -> tuple:
    """
    Makes a single OpenAI chat completion call with a system message and a
    user message containing the image (as a data URL) and the text prompt.

    Parameters
    ----------
    client : OpenAI
        Configured OpenAI client instance.
    image_data_url : str
        Base64 data URL of the image.
    prompt : str
        Text prompt to accompany the image.
    max_completion_tokens : int
        Maximum tokens for the completion response.

    Returns
    -------
    tuple[str, dict]
        (raw_text, token_usage) where raw_text is the model's response string
        and token_usage is the dict from _extract_token_usage().
    """
    completion = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        max_completion_tokens=max_completion_tokens,
        messages=[
            {
                "role":    "system",
                "content": SYSTEM_INSTRUCTION,
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url":    image_data_url,
                            "detail": "high",
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            },
        ],
    )

    raw_text    = completion.choices[0].message.content or ""
    token_usage = _extract_token_usage(completion)
    return raw_text, token_usage


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def call_vision(image_path: str, prompt: str, max_completion_tokens: int = 1000) -> dict:
    """
    Sends an image and a prompt to the Azure OpenAI Vision API and returns
    the parsed JSON extraction result.

    This is the single shared caller for all Vision API calls in the project.
    No other file should make direct HTTP calls to the Vision API.

    Parameters
    ----------
    image_path : str
        Absolute or relative path to the image file to send.
    prompt : str
        Text prompt to accompany the image.
    max_completion_tokens : int, optional
        Maximum number of tokens in the API response (default: 1000).

    Returns
    -------
    dict
        Always returns a dict — never raises an exception.

        On success:
        {
            "ok":          True,
            "text":        str,   # raw model response text
            "data":        dict,  # parsed JSON object
            "error":       None,
            "token_usage": dict   # prompt/completion/total/reasoning tokens
        }

        On failure:
        {
            "ok":          False,
            "text":        str | None,
            "error":       str,
            "token_usage": {}
        }
    """
    # ------------------------------------------------------------------
    # Guard: API key must be set
    # ------------------------------------------------------------------
    if not AZURE_OPENAI_API_KEY:
        return {
            "ok":          False,
            "text":        None,
            "error":       "missing_api_key",
            "token_usage": {},
        }

    # ------------------------------------------------------------------
    # Read and encode the image
    # ------------------------------------------------------------------
    try:
        image_data_url = _read_image_as_data_url(image_path)
    except Exception as exc:
        return {
            "ok":          False,
            "text":        None,
            "error":       str(exc),
            "token_usage": {},
        }

    # ------------------------------------------------------------------
    # Create OpenAI client
    # ------------------------------------------------------------------
    client = OpenAI(
        api_key  = AZURE_OPENAI_API_KEY,
        base_url = AZURE_OPENAI_BASE_URL,
    )

    # ------------------------------------------------------------------
    # Two-attempt loop with rate-limit retry
    # ------------------------------------------------------------------
    for attempt in range(2):
        try:
            raw_text, token_usage = _call_api(client, image_data_url, prompt, max_completion_tokens)

        except RateLimitError:
            if attempt == 0:
                time.sleep(RATE_LIMIT_WAIT_S)
                continue
            else:
                return {
                    "ok":          False,
                    "text":        None,
                    "error":       "rate_limit_exceeded",
                    "token_usage": {},
                }

        except Exception as exc:
            return {
                "ok":          False,
                "text":        None,
                "error":       str(exc),
                "token_usage": {},
            }

        # --------------------------------------------------------------
        # Try to parse JSON from the response
        # --------------------------------------------------------------
        json_substring = _extract_json_substring(raw_text)
        try:
            parsed_dict = json.loads(json_substring)
            return {
                "ok":          True,
                "text":        raw_text,
                "data":        parsed_dict,
                "error":       None,
                "token_usage": token_usage,
            }
        except (json.JSONDecodeError, ValueError):
            if attempt == 0:
                # Retry — maybe the model will give cleaner output
                continue
            else:
                return {
                    "ok":          False,
                    "text":        raw_text,
                    "error":       "parse_failed",
                    "token_usage": token_usage,
                }

    # Should never reach here, but safety net
    return {
        "ok":          False,
        "text":        None,
        "error":       "unknown_error",
        "token_usage": {},
    }
