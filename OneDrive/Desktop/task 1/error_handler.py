from fastapi import HTTPException




def raise_no_gfs_drawing(details: dict = {}):
    raise HTTPException(
        status_code=422,
        detail={
            "error_code": "NO_GFS_DRAWING_FOUND",
            "message": "No GFS shop drawing found in this folder. Please check the uploaded files.",
            "details": details
        }
    )


def raise_pdf_corrupt(details: dict = {}):
    raise HTTPException(
        status_code=422,
        detail={
            "error_code": "PDF_CORRUPT",
            "message": "Could not open PDF file. File may be corrupted.",
            "details": details
        }
    )


def raise_vision_api_failed(details: dict = {}):
    raise HTTPException(
        status_code=503,
        detail={
            "error_code": "VISION_API_FAILED",
            "message": "Could not process drawing. Please try again.",
            "details": details
        }
    )


def raise_unsupported_file_type(details: dict = {}):
    raise HTTPException(
        status_code=415,
        detail={
            "error_code": "UNSUPPORTED_FILE_TYPE",
            "message": "File type not supported. Upload a PDF or zip folder.",
            "details": details
        }
    )




def ambiguous_drawings_response(candidates: list = []):
    return {
        "error_code": "AMBIGUOUS_DRAWINGS",
        "message": "Multiple shop drawings found. Please select one.",
        "details": {
            "candidates": candidates
        }
    }


def cad_file_detected_response(details: dict = {}):
    return {
        "error_code": "CAD_FILE_DETECTED",
        "message": "CAD file detected (.dwg/.dxf). Please provide the PDF version of the shop drawings.",
        "details": details
    }

if __name__ == "__main__":
    import json

    print("\nTesting all error types...")
    print("-" * 40)

    # Test soft warnings
    print("\nSOFT WARNINGS (return 200):")
    print(json.dumps(
        ambiguous_drawings_response(candidates=["file1.pdf", "file2.pdf"]),
        indent=2
    ))
    print(json.dumps(
        cad_file_detected_response(details={"filename": "drawing.dwg"}),
        indent=2
    ))

    # Test real errors
    print("\nREAL ERRORS (raise HTTPException):")
    from fastapi import HTTPException
    for fn, kwargs in [
        (raise_no_gfs_drawing,      {}),
        (raise_pdf_corrupt,         {"details": {"filename": "test.pdf"}}),
        (raise_vision_api_failed,   {}),
        (raise_unsupported_file_type, {"details": {"received_extension": ".exe"}}),
    ]:
        try:
            fn(**kwargs)
        except HTTPException as e:
            print(f"  {e.detail['error_code']} → HTTP {e.status_code} ✓")