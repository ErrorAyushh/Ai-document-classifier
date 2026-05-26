# vision_client.py
# This script sends processed page images to the Azure OpenAI Vision service for dimension and data extraction.

import os
import io
import json
import base64
from mimetypes import guess_type
from openai import OpenAI
from dotenv import load_dotenv

# Load API credentials and settings from the local environment (.env) file.
# Non-technical: This fetches the API passwords and keys needed to talk to the AI brain.
load_dotenv()

# Retrieve Azure OpenAI key from environment variables.
# Non-technical: This gets the security access key to log into the AI service.
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_KEY")

# Define the base URL endpoint for the Azure OpenAI resource.
# Non-technical: This is the website address where our AI request is sent.
AZURE_OPENAI_BASE_URL = "https://openaikeyforgfs.openai.azure.com/openai/v1/"

# Define the model/deployment name for the vision-capable AI model.
# Non-technical: This specifies which AI model we want to look at our drawings (defaults to gpt-5-2).
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5-2")

# Set the maximum image dimension to avoid sending overly large payloads to the API.
# Non-technical: This limits the size of the picture so it uploads quickly and doesn't overload the AI.
MAX_IMAGE_PX = 1568


def _read_image_as_data_url(image_path: str) -> str:
    # Convert a local image file into a Base64-encoded Data URL.
    # Non-technical: This reads the picture file and translates it into a text string that the AI can understand directly.
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"File not found: {image_path}")

    try:
        from PIL import Image

        # Load image, convert to RGB, and resize if it exceeds the maximum size limit.
        # Non-technical: This opens the picture, ensures standard colors, and shrinks it if it is too huge.
        img = Image.open(image_path).convert("RGB")
        if max(img.size) > MAX_IMAGE_PX:
            img.thumbnail((MAX_IMAGE_PX, MAX_IMAGE_PX))

        # Save the resized image as JPEG in a memory buffer.
        # Non-technical: This packages the compressed image in temporary memory instead of writing it to disk.
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        
        # Encode the bytes to a Base64 string and format as a standard data URL.
        # Non-technical: This turns the picture bytes into a standard text format for internet transmission.
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"

    except ImportError:
        # Fallback method if PIL (Pillow library) is not installed: read raw bytes directly.
        # Non-technical: This reads the raw file as-is if the image editing library is not available.
        mime_type, _ = guess_type(image_path)
        if mime_type is None:
            mime_type = "application/octet-stream"

        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        return f"data:{mime_type};base64,{b64}"


def send_to_vision(image_path: str, prompt: str) -> dict:
    # Send an image and text instructions to the AI model and return the extracted JSON response.
    # Non-technical: This uploads a page picture with a list of questions, then returns what the AI found.
    if not AZURE_OPENAI_API_KEY:
        return {"error": "missing_api_key", "raw": "Set AZURE_OPENAI_API_KEY in .env"}

    try:
        # Read and prepare the image format.
        # Non-technical: Convert the page picture into the text-based format required by the API.
        image_data_url = _read_image_as_data_url(image_path)
    except Exception as e:
        return {"error": "image_read_error", "raw": str(e)}

    try:
        # Initialize OpenAI client with Azure specific credentials and base URL.
        # Non-technical: Connect to the secure Azure servers hosting our AI.
        client = OpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            base_url=AZURE_OPENAI_BASE_URL,
        )

        # Call the chat completions API requesting analysis of both text prompt and image.
        # Non-technical: Ask the AI to look at the drawing picture and extract the requested dimensions.
        completion = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                }
            ],
            max_completion_tokens=8000,
        )

        # Format and return the successful response.
        # Non-technical: Return the AI's answer text and raw details so the rest of the script can parse it.
        return {
            "ok": True,
            "text": completion.choices[0].message.content,
            "raw": completion.model_dump(),
        }

    except Exception as e:
        return {"error": "api_error", "raw": str(e)}


if __name__ == "__main__":
    import sys

    # Command line interface safety check.
    # Non-technical: If run directly from the terminal without arguments, print usage instructions.
    if len(sys.argv) < 3:
        print("Usage: python vision_client.py <image_path> <prompt>")
        raise SystemExit(1)

    result = send_to_vision(sys.argv[1], sys.argv[2])
    print(json.dumps(result, indent=2))