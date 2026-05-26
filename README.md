# AI Document Classifier

An AI-powered FastAPI backend for processing construction shop drawing PDFs using multimodal Vision AI. The system classifies relevant drawing pages, filters unnecessary documents, and returns structured JSON outputs for downstream estimation workflows.

---

# Overview

This project was built to automate the preprocessing and classification of GFS construction shop drawings.

The pipeline:
- accepts PDF or ZIP uploads
- extracts relevant shop drawings
- converts PDF pages into images
- sends page images to Vision AI models
- classifies pages into predefined drawing categories
- returns structured JSON responses

The goal is to reduce unnecessary AI parsing costs by filtering irrelevant pages before extraction.

---

# Features

- FastAPI backend
- PDF and ZIP upload support
- PDF → image conversion
- Vision AI integration (Azure/OpenAI)
- AI-powered page classification
- Structured JSON responses
- CLI support for local testing
- Swagger API documentation
- Background temp-file cleanup
- Modular pipeline architecture

---

# Pipeline Flow

```text
ZIP/PDF Upload
      ↓
Folder Processor
      ↓
PDF to Images
      ↓
Vision AI Classifier
      ↓
Structured JSON Output
      ↓
Extraction Pipeline
'''
---
Classification Types

The Vision AI classifier categorizes pages into:

Type	Description
COVER	Main cover/project information pages
PLAN_VIEW	Plan/unit layout drawings
MGDS_CATALOG	Modular Glass Deck System catalog pages
OTHER	Detail pages, labels, elevations, warranties, etc.
---
Tech Stack
Python
FastAPI
Azure OpenAI Vision
OpenAI SDK
Pillow
PyMuPDF
Uvicorn
python-multipart
API Endpoints
Health Check
GET /health

Response:

{
  "status": "ok"
}
Upload PDF/ZIP
POST /upload

Accepts:

.pdf
.zip

Returns:

classified pages
candidate selection
validation errors

Example Response:

{
  "status": "classified",
  "job_id": "1234",
  "pages": [
    {
      "page_number": 1,
      "page_type": "PLAN_VIEW",
      "confidence": "HIGH"
    }
  ]
}
Select PDF From ZIP
POST /select

Used when multiple PDFs are detected inside a ZIP upload.

Submit for Extraction
POST /extract

Receives classified page metadata for downstream extraction workflows.

Installation
Clone Repository
git clone https://github.com/ErrorAyushh/Ai-document-classifier.git
cd Ai-document-classifier
Create Virtual Environment
python -m venv gfs_env

Activate environment:

Windows PowerShell
.\gfs_env\Scripts\Activate.ps1
Git Bash
source gfs_env/Scripts/activate
Install Dependencies
pip install -r requirements.txt
Environment Variables

Create a .env file:

AZURE_OPENAI_KEY=your_key_here
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_CHAT_DEPLOYMENT=your_deployment
AZURE_OPENAI_API_VERSION=2024-02-15-preview
Running the FastAPI Server
python fastapi_app.py --reload

Server:

http://localhost:8000

Swagger Docs:

http://localhost:8000/docs
CLI Testing
Test Vision Client
python vision_client.py test_images/page_1.png "Describe this image"
Test Page Classifier
python page_classifier_ayush.py --image test_images/page_1.png --page_number 1
Project Structure
Ai-document-classifier/
│
├── fastapi_app.py
├── page_classifier_ayush.py
├── vision_client.py
├── pdf_to_images.py
├── folder_processor.py
├── error_handler.py
├── requirements.txt
├── README.md
│
├── test_images/
│
└── sample_outputs/
Future Improvements
Async Vision inference
Batch image processing
Confidence scoring improvements
Rules engine integration
OCR fallback pipeline
Database integration
Frontend dashboard
Extraction orchestration
Disclaimer

This repository contains a simplified/sanitized implementation intended for demonstration and learning purposes. Sensitive company data, production credentials, and proprietary project files have been excluded.

Author

Ayush Balli

GitHub:
https://github.com/ErrorAyushh

DEVELOPER MODE
