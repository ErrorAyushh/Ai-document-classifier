import os
import uuid
import shutil
import threading
import tempfile
from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from error_handler import (
    raise_unsupported_file_type,
    raise_pdf_corrupt,
    raise_no_gfs_drawing,
    ambiguous_drawings_response,
    cad_file_detected_response
)

from page_classifier_ayush import classify_all_pages

import pdf_to_image
import folder_processor


app = FastAPI(
    title="GFS Document Processing API",
    description="Processes GFS shop drawing PDFs — classifies pages and extracts project parameters for the estimation pipeline.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

ALLOWED_EXTENSIONS = {".pdf", ".zip"}
CAD_EXTENSIONS     = {".dwg", ".dxf"}
CLEANUP_DELAY_SEC  = 3600
TEMP_BASE          = tempfile.gettempdir()


@app.get(
    "/health",
    summary="Health check",
    description="Returns ok if the server is running."
)
def health():
    return {"status": "ok"}


@app.post(
    "/upload",
    summary="Upload a PDF or ZIP file",
    description="""
    Main entry point for the document processing pipeline.

    Accepts a PDF shop drawing or a ZIP folder containing shop drawings.

    Possible responses:
    - **classified** — pages successfully classified, ready for extraction
    - **need_selection** — multiple PDFs found in ZIP, user must pick one
    - **CAD_FILE_DETECTED** — CAD file uploaded, PDF version needed
    - **422** — no GFS drawing found, corrupt file
    - **415** — unsupported file type
    """
)
async def upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="PDF or ZIP file containing GFS shop drawings")
):
    # Handle missing filename
    if not file.filename:
        return JSONResponse(
            status_code=422,
            content={
                "error_code": "INVALID_FILENAME",
                "message":    "Uploaded file must have a filename.",
                "details":    {}
            }
        )

    # Step 1: Check file extension
    _, ext = os.path.splitext(file.filename)
    ext = ext.lower()

    # CAD file — soft warning
    if ext in CAD_EXTENSIONS:
        return JSONResponse(
            status_code=200,
            content=cad_file_detected_response(
                details={"filename": file.filename}
            )
        )

    # Unsupported file type — hard error
    if ext not in ALLOWED_EXTENSIONS:
        raise_unsupported_file_type(
            details={"filename": file.filename, "received_extension": ext}
        )

    # Step 2: Create unique job and save file
    job_id      = str(uuid.uuid4())
    temp_folder = os.path.join(TEMP_BASE, f"gfs_{job_id}")
    os.makedirs(temp_folder, exist_ok=True)

    saved_file_path = os.path.join(temp_folder, file.filename)

    try:
        with open(saved_file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    except Exception as e:
        _cleanup_now(temp_folder)
        raise_pdf_corrupt(
            details={"filename": file.filename, "error": str(e)}
        )

    # Step 3: Route based on file type
    if ext == ".zip":

        # Extract ZIP
        try:
            shutil.unpack_archive(saved_file_path, temp_folder)
        except Exception as e:
            _cleanup_now(temp_folder)
            raise_pdf_corrupt(
                details={"filename": file.filename, "error": str(e)}
            )

        # Find GFS drawing inside
        result = folder_processor.find_shop_drawing(temp_folder)

        # Multiple PDFs found — ask user to pick
        if result.get("ambiguous"):
            background_tasks.add_task(_cleanup, temp_folder)
            return JSONResponse(
                status_code=200,
                content={
                    "status":     "need_selection",
                    "job_id":     job_id,
                    "candidates": result.get("candidates", [])
                }
            )

        # No drawing found
        if not result.get("pdf_path"):
            _cleanup_now(temp_folder)
            raise_no_gfs_drawing(
                details={"folder": temp_folder}
            )

        pdf_path = result["pdf_path"]

    else:
        # Direct PDF upload
        pdf_path = saved_file_path

    # Step 4: Classify all pages
    response = _classify_and_respond(
        pdf_path=pdf_path,
        job_id=job_id,
        temp_folder=temp_folder
    )

    # Step 5: Schedule cleanup after 1 hour
    background_tasks.add_task(_cleanup, temp_folder)

    # Step 6: Return results
    return JSONResponse(status_code=200, content=response)


@app.post(
    "/select",
    summary="Select a PDF from multiple candidates",
    description="""
    Called when /upload returns need_selection because multiple PDFs
    were found in the uploaded ZIP.

    The user picks one PDF path from the candidates list and submits
    it here along with the job_id.
    """
)
async def select(body: dict, background_tasks: BackgroundTasks):
    job_id       = body.get("job_id")
    selected_pdf = body.get("selected_pdf")

    if not job_id or not selected_pdf:
        return JSONResponse(
            status_code=422,
            content={
                "error_code": "INVALID_REQUEST",
                "message":    "job_id and selected_pdf are required.",
                "details":    {}
            }
        )

    if not os.path.exists(selected_pdf):
        return JSONResponse(
            status_code=422,
            content={
                "error_code": "FILE_NOT_FOUND",
                "message":    "Selected PDF path does not exist.",
                "details":    {"selected_pdf": selected_pdf}
            }
        )

    temp_folder = os.path.join(TEMP_BASE, f"gfs_{job_id}")

    response = _classify_and_respond(
        pdf_path=selected_pdf,
        job_id=job_id,
        temp_folder=temp_folder
    )

    background_tasks.add_task(_cleanup, temp_folder)

    return JSONResponse(status_code=200, content=response)


@app.post(
    "/extract",
    summary="Submit classified pages for extraction",
    description="""
    Receives the classified page list from /upload or /select.
    Confirms receipt and returns job_id for tracking.
    Actual extraction happens downstream.
    """
)
async def extract(body: dict):
    job_id = body.get("job_id")
    pages  = body.get("pages", [])

    if not job_id:
        return JSONResponse(
            status_code=422,
            content={
                "error_code": "INVALID_REQUEST",
                "message":    "job_id is required.",
                "details":    {}
            }
        )

    # TODO: wire to extraction pipeline here
    return JSONResponse(
        status_code=200,
        content={
            "status":     "received",
            "job_id":     job_id,
            "page_count": len(pages)
        }
    )


def _classify_and_respond(pdf_path: str, job_id: str, temp_folder: str) -> dict:
    """
    Shared helper used by both /upload and /select.
    Converts PDF to images then classifies each page.
    Returns structured result dict.
    """

    # FIX 2: call pdf_to_image.convert_pdf_to_images
    # matches the actual function name in your pdf_to_image.py file
    try:
        page_images = pdf_to_image.convert_pdf_to_images(
            pdf_path=pdf_path,
            output_dir=temp_folder
        )
    except Exception as e:
        _cleanup_now(temp_folder)
        raise_pdf_corrupt(
            details={"pdf_path": pdf_path, "error": str(e)}
        )

    # Classify each page using Vision AI
    classified_pages = classify_all_pages(page_images)

    return {
        "status": "classified",
        "job_id": job_id,
        "pages":  classified_pages
    }


def _cleanup(folder_path: str):
    """Deletes temp folder after 1 hour. Runs in background."""
    timer = threading.Timer(
        CLEANUP_DELAY_SEC,
        shutil.rmtree,
        args=[folder_path],
        kwargs={"ignore_errors": True}
    )
    timer.daemon = True
    timer.start()


def _cleanup_now(folder_path: str):
    """Deletes temp folder immediately. Used in error paths."""
    shutil.rmtree(folder_path, ignore_errors=True)


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(
        description="Run the GFS FastAPI server"
    )
    parser.add_argument("--host",   default="0.0.0.0")
    parser.add_argument("--port",   type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    print(f"\nStarting GFS API server...")
    print(f"  Host:       {args.host}")
    print(f"  Port:       {args.port}")
    print(f"  Docs:       http://localhost:{args.port}/docs")
    print(f"  Reload:     {args.reload}")
    print(f"  Temp dir:   {TEMP_BASE}")
    print(f"  Classifier: page_classifier_ayush.py")
    print(f"  PDF→Images: pdf_to_image.py")
    print("-" * 40)

    uvicorn.run(
        "fastapi_app:app",
        host=args.host,
        port=args.port,
        reload=args.reload
    )