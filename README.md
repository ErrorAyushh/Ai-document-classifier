# GFS AI Cover Intake Service

An intelligent document processing service that automatically extracts structured project information from incoming lead documents — emails, PDFs, and attachments — received by Glass Flooring Systems (GFS).

When a new lead arrives via Outlook or Zoho, the Node.js backend uploads the documents to Azure Blob Storage and sends a message to an Azure Service Bus queue. This service picks up that message, downloads every document, runs a multi-stage AI extraction pipeline, and sends a structured result back to the backend via webhook. Estimators then review and approve the extracted data before any downstream workflow continues.

The service is built on FastAPI, runs a persistent Azure Service Bus listener as a background task, and uses a hybrid extraction strategy: direct text extraction via Docling first, with Azure OpenAI Vision as a fallback when critical fields cannot be found in the text layer.

---

## Architecture

```
Outlook / Zoho / Customer Upload
            │
            ▼
    Node.js Backend
            │  Uploads files to Azure Blob Storage
            │  Sends message to Azure Service Bus
            ▼
Azure Service Bus Queue (ai-ocr-ingestion)
            │
            ▼
   Listener  (listener/worker.py)
            │  Receives message
            │  Validates schema
            │  Registers lock renewal
            │  POSTs full payload to FastAPI
            ▼
   FastAPI  (fastapi_app.py — POST /process-payload)
            │  Downloads blobs from Azure Blob Storage
            │  Saves files to temp directory
            │  Calls process_lead_files()
            ▼
   Cover Pipeline  (cover_pipeline.py)
            │
            ├── Docling extraction  (non_ai/docling_extractv2.py)
            │         Text, headings, paragraphs, figures extracted per page
            │
            ├── Content parser  (non_ai/content_parser.py)
            │         Regex + rule-based extraction of project_address,
            │         customer_name, quote_number, glass_type, etc.
            │
            ├── Sufficiency check
            │         Fast path: if project_address found → return immediately
            │         No AI call made, confidence = 1.0
            │
            └── Azure OpenAI Vision fallback  (vision_client.py)
                      Renders cover pages as images
                      Sends to Azure OpenAI GPT-4 Vision
                      Returns structured JSON
            │
            ▼
   Structured JSON result
            │
            ▼
   Webhook POST → Node.js Backend
   (https://…/api/v1/outlook/ai-result)
            │
            ▼
   MongoDB (via Node.js)
            │
            ▼
   Frontend estimator review UI
```

---

## Repository Structure

```
gfs-ai/
│
├── fastapi_app.py          Entry point for the FastAPI application.
│                           Exposes GET /health, POST /process,
│                           POST /process-payload. Owns blob downloading,
│                           temp file management, pipeline invocation,
│                           and webhook dispatch.
│
├── main.py                 FastAPI app with Service Bus listener embedded
│                           via lifespan. Run this with uvicorn in production.
│                           The listener starts automatically on startup.
│
├── cover_pipeline.py       Core AI pipeline orchestrator.
│                           Accepts a list of file paths, runs Docling
│                           extraction, content parsing, sufficiency check,
│                           and Vision fallback. Returns structured JSON.
│
├── vision_client.py        Azure OpenAI Vision client.
│                           Handles authentication, image encoding,
│                           batch API calls, and response parsing.
│
├── vision_extract.py       PDF-to-image rendering and page chunking.
│                           Used by the Vision fallback path to convert
│                           PDF pages into PNG images for the Vision API.
│
├── listener/
│   ├── __init__.py
│   ├── worker.py           Azure Service Bus receive loop.
│                           Receives messages, validates schema, registers
│                           AutoLockRenewer, POSTs payload to FastAPI,
│                           and settles messages (complete/abandon/dead-letter).
│   └── message_schema.py   Pydantic schema for the incoming Service Bus
│                           message. Mirrors ProcessPayloadRequest in
│                           fastapi_app.py exactly.
│
├── non_ai/
│   ├── __init__.py
│   ├── content_parser.py   Regex and rule-based field extractor.
│                           Extracts project_address, customer_name,
│                           quote_number, glass_type, system_type from
│                           raw text without calling any API.
│   └── docling_extractv2.py
│                           Docling document converter wrapper.
│                           Converts PDFs and email files into structured
│                           dicts with headings, paragraphs, figures,
│                           and full_text_by_page.
│
├── services/
│   └── blob_processor.py   DEPRECATED. Previously handled blob download
│                           and pipeline invocation directly. Now superseded
│                           by fastapi_app._download_blob() and
│                           process_lead_files(). Retained for reference
│                           until fully removed.
│
├── .env                    Your local secrets. Never commit this file.
├── .env.example            Template showing all required environment
│                           variables with descriptions but no secrets.
├── requirements.txt        All Python dependencies.
├── Dockerfile              Container definition for production deployment.
│                           Runs both FastAPI and the listener in a single
│                           container via startup.sh.
├── startup.sh              Container entrypoint script. Starts uvicorn
│                           with main.py so the listener boots alongside
│                           the API automatically.
└── README.md               This file.
```

---

## Features

- **Multi-document processing** — a single lead can include an email body, one or more PDF attachments, and DOCX spec sheets. All are processed together and treated as one coherent project lead.
- **Email and PDF intake** — supports `.pdf`, `.eml`, `.msg`, `.txt`, and `.docx` file formats. Email files are parsed into text and treated as a document page.
- **Azure Blob download** — blobs are downloaded from Azure Blob Storage using SAS URLs included in the Service Bus payload. Size limits (50 MB per file), 404/403 detection, and streaming are all handled.
- **Docling extraction** — structured text extraction without OCR using the Docling library. Extracts headings, paragraphs, figures, and tables per page.
- **Azure OpenAI Vision fallback** — when critical fields (primarily `project_address`) cannot be found in the text layer, cover pages are rendered as images and sent to Azure OpenAI GPT-4 Vision for inference.
- **Fast path** — if the text layer yields a valid `project_address`, the pipeline returns immediately without making any API call. Confidence is set to 1.0.
- **Structured JSON output** — every pipeline run produces a JSON file in `extraction_results/` named after the `correlationId`, and the same structure is returned to the caller and forwarded to the webhook.
- **Correlation ID tracking** — every message, pipeline run, log line, and webhook call carries the `correlationId` from the originating Service Bus message, allowing full end-to-end tracing.
- **Backend webhook integration** — after successful AI processing, the result is POSTed to the Node.js backend webhook so the lead can be created or linked in the frontend.
- **Health endpoint** — `GET /health` returns `{"status": "ok"}` and is suitable for load balancer and container health checks.
- **Azure Service Bus lock renewal** — long-running pipeline calls (Vision fallback can take several minutes) are protected by `AutoLockRenewer`, which keeps the message lock alive until the pipeline completes and the message is settled.
- **Docker support** — a `Dockerfile` and `startup.sh` are provided for containerised deployment to Azure Container Apps, App Service, or AKS.

---

## Prerequisites

- **Python 3.11** or later
- An **Azure Storage Account** with a container for project documents
- An **Azure Service Bus namespace** with a queue named `ai-ocr-ingestion` and lock duration set to 5 minutes (300 seconds)
- An **Azure OpenAI resource** with a deployment of `gpt-4o` or `gpt-4-vision-preview`
- **Docker** (optional, for containerised deployment)
- The Node.js backend running and accessible (for webhook delivery)

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/your-org/gfs-ai.git
cd gfs-ai

# 2. Create a virtual environment
python -m venv venv

# 3. Activate it
# Windows PowerShell
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt
```

---

## Environment Variables

Copy the template and fill in your values:

```bash
cp .env.example .env
```

`.env.example`:

```env
# ── Azure Service Bus ──────────────────────────────────────────────────────────
# Connection string from Azure Portal → Service Bus Namespace →
# Shared Access Policies → RootManageSharedAccessKey → Primary Connection String
AZURE_SERVICE_BUS_CONNECTION_STRING=Endpoint=sb://YOUR_NAMESPACE.servicebus.windows.net/;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=YOUR_KEY

# Name of the queue the listener consumes from.
# Must exist in your Service Bus namespace.
AZURE_SERVICE_BUS_QUEUE_NAME=ai-ocr-ingestion

# ── Azure OpenAI ───────────────────────────────────────────────────────────────
# API key from Azure Portal → Azure OpenAI resource → Keys and Endpoint
AZURE_OPENAI_API_KEY=your-azure-openai-api-key

# The endpoint for your Azure OpenAI resource.
# Format: https://YOUR_RESOURCE_NAME.openai.azure.com/
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/

# The deployment name you created for your GPT-4 Vision model.
# Found in Azure OpenAI Studio → Deployments
AZURE_OPENAI_DEPLOYMENT=gpt-4o

# API version string for the Azure OpenAI REST API.
AZURE_OPENAI_API_VERSION=2024-02-01

# ── FastAPI ────────────────────────────────────────────────────────────────────
# The URL the Service Bus listener will POST messages to.
# Development: http://localhost:8000
# Production: https://your-deployed-service.azurewebsites.net
FASTAPI_BASE_URL=http://localhost:8000

# Timeout in seconds for the listener's POST to /process-payload.
# Must be longer than the slowest expected pipeline run (Vision fallback
# can take 60–120 seconds per document). 300 seconds is a safe default.
FASTAPI_CALL_TIMEOUT=300

# ── Webhook ────────────────────────────────────────────────────────────────────
# URL of the Node.js backend endpoint that receives the AI result.
WEBHOOK_URL=https://poppy-cork-autistic.ngrok-free.dev/api/v1/outlook/ai-result
```

---

## Running Locally

The service has two components that must both be running during development: the FastAPI application and the Service Bus listener.

**Start FastAPI first** — the listener POSTs to FastAPI, so FastAPI must be ready before any messages are processed.

```bash
# Terminal 1 — FastAPI application (port 8000)
uvicorn fastapi_app:app --reload --port 8000

# Confirm it is healthy before starting the listener
curl http://localhost:8000/health
# Expected: {"status":"ok"}
```

```bash
# Terminal 2 — Service Bus listener (port 8001, any available port)
uvicorn main:app --reload --port 8001
```

Expected startup output in Terminal 2:

```
INFO  gfs.main      FastAPI starting up — launching Service Bus listener...
INFO  gfs.listener  Listener starting | queue=ai-ocr-ingestion | fastapi_url=http://localhost:8000
INFO  gfs.listener  Connected to Service Bus | queue=ai-ocr-ingestion
```

In production, `main.py` runs as a single process. The listener starts automatically inside the FastAPI lifespan, so only one `uvicorn` command is needed. See the Docker section below.

---

## API Endpoints

### `GET /health`

Returns 200 OK when the service is running. Used by load balancers and container health checks.

**Response:**
```json
{"status": "ok"}
```

---

### `POST /process`

Manual upload endpoint for demo and testing. Accepts one or more files as multipart form data and runs them through the full pipeline without requiring a Service Bus message.

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|---|---|---|---|
| `files` | File(s) | Yes | One or more `.pdf`, `.eml`, `.msg`, `.txt`, `.docx` files |
| `lead_id` | string | No | Custom identifier for the output JSON filename |

**curl example:**
```bash
curl -X POST http://localhost:8000/process \
  -F "files=@/path/to/lead_email.eml" \
  -F "files=@/path/to/attachment.pdf"
```

**Response (fast path — address found in text):**
```json
{
  "action": "CREATE",
  "readinessState": "SUFFICIENT_DATA",
  "proposed": {
    "customerName": "John Smith",
    "projectName": "Project at 123 Main Street New York NY",
    "projectType": "NEW_ESTIMATE",
    "summary": "Project inquiry from John Smith located at 123 Main Street New York NY."
  },
  "confidence": 1.0,
  "reasoning": "all required fields found via direct text extraction, no AI inference needed.",
  "timing": {
    "extraction_seconds": 2.14,
    "adapt_seconds": 0.003,
    "sufficiency_seconds": 0.001,
    "render_seconds": 0.0,
    "api_call_seconds": 0.0,
    "total_seconds": 2.144
  }
}
```

**Response (Vision fallback — address not in text layer):**
```json
{
  "action": "CREATE",
  "readinessState": "MISSING_DOCUMENTS",
  "proposed": {
    "customerName": "Acme Construction",
    "projectName": "Glass Floor Project - 456 Park Ave",
    "projectType": "NEW_ESTIMATE",
    "summary": "Project inquiry from Acme Construction for glass flooring at 456 Park Ave."
  },
  "confidence": 0.82,
  "reasoning": "Project address confirmed via Vision. Drawings referenced but not attached.",
  "timing": {
    "extraction_seconds": 3.21,
    "adapt_seconds": 0.008,
    "sufficiency_seconds": 0.001,
    "render_seconds": 1.44,
    "api_call_seconds": 6.87,
    "total_seconds": 11.53
  }
}
```

---

### `POST /process-payload`

Production endpoint driven by the Azure Service Bus listener. Accepts the full Service Bus message payload as JSON, downloads all referenced blobs, runs the pipeline, sends the webhook, and returns the structured result.

**Request body:**
```json
{
  "jobType": "EMAIL_PROPOSAL",
  "correlationId": "outlookEmail:AAMkADh...",
  "payload": {
    "subject": "New Project Request: 123 Main St",
    "sender": "client@firm.com",
    "documents": [
      {
        "documentId": "6a36c7a2dc5cbfa693c5299a",
        "originalName": "email-AAMkADh.txt",
        "blobUrl": "https://yourstorage.blob.core.windows.net/leads/email.txt?sv=..."
      },
      {
        "documentId": "6a36c7a2dc5cbfa693c5299b",
        "originalName": "Architectural_Plans.pdf",
        "blobUrl": "https://yourstorage.blob.core.windows.net/leads/plans.pdf?sv=..."
      }
    ]
  }
}
```

**Response:**
```json
{
  "correlationId": "outlookEmail:AAMkADh...",
  "result": {
    "action": "CREATE",
    "readinessState": "SUFFICIENT_DATA",
    "proposed": {
      "customerName": "John Smith",
      "projectName": "Project at 123 Main Street",
      "projectType": "NEW_ESTIMATE",
      "summary": "Project inquiry from John Smith located at 123 Main Street."
    },
    "confidence": 1.0,
    "reasoning": "all required fields found via direct text extraction.",
    "timing": { ... }
  }
}
```

**HTTP status codes:**

| Status | Meaning | Service Bus action |
|---|---|---|
| 200 | Pipeline succeeded | Listener calls `complete_message()` |
| 400 | No processable documents | Listener calls `dead_letter_message()` |
| 415 | Unsupported file type | Listener calls `dead_letter_message()` |
| 422 | Payload schema invalid | Listener calls `dead_letter_message()` |
| 500 | Transient error | Listener calls `abandon_message()` for retry |

---

## End-to-End Processing Flow

This section describes exactly what happens from the moment a lead arrives until the result reaches the Node.js backend.

**Step 1 — Lead arrives**
A new email or uploaded project folder is received by the Node.js backend via Outlook integration or Zoho webhook.

**Step 2 — Blob upload**
The Node.js backend uploads all lead files (email body as `.txt`, PDF attachments, DOCX specs) to Azure Blob Storage and generates SAS URLs with an expiry window.

**Step 3 — Queue message**
The Node.js backend sends a single JSON message to the `ai-ocr-ingestion` Service Bus queue containing the `correlationId`, `jobType`, and a list of `DocumentEntry` objects (each with `documentId`, `originalName`, and `blobUrl`).

**Step 4 — Message received**
`listener/worker.py` picks up the message. It reads the raw body, parses the JSON, and validates it against `IncomingMessage` (Pydantic). Malformed messages are dead-lettered immediately. Valid messages have their lock registered with `AutoLockRenewer` for up to 2 hours.

**Step 5 — POST to FastAPI**
The listener POSTs the raw message JSON to `POST /process-payload` on the FastAPI service. The request timeout is configurable via `FASTAPI_CALL_TIMEOUT` (default 300 seconds).

**Step 6 — Blob download**
`fastapi_app.py` iterates through every `DocumentEntry` and calls `_download_blob()` for each. Files are downloaded in 64 KB streaming chunks to a per-request temp directory. HTTP 404 and 403 responses are classified as permanent failures (dead-letter). Network timeouts trigger a 500 response (abandon and retry).

**Step 7 — Docling extraction**
`cover_pipeline.build_doc_pairs()` converts each file using Docling. PDFs are converted to structured dicts with headings, paragraphs, figures, and `full_text_by_page`. EML and MSG files are parsed for their text body. TXT files are wrapped as a synthetic single-page document.

**Step 8 — Content parsing**
The concatenated text from all documents is passed to `non_ai/content_parser.py`. Regex patterns attempt to extract `project_address`, `customer_name`, `quote_number`, `glass_type`, and `system_type` from the text layer without any API call.

**Step 9 — Sufficiency check**
`is_sufficient()` checks whether `project_address` was found. If yes, `build_fast_path_response()` returns immediately with `confidence=1.0`. No Azure OpenAI call is made.

**Step 10 — Vision fallback (if needed)**
If `project_address` is missing, `run_vision_fallback()` renders the cover pages as PNG images using `vision_extract.render_pdf_to_images()`, builds an image batch, and sends it to Azure OpenAI Vision with a structured extraction prompt. The response is parsed and validated.

**Step 11 — JSON output**
The pipeline result is saved to `extraction_results/{correlationId}.json` on disk and returned to the FastAPI endpoint.

**Step 12 — Webhook dispatch**
`_send_webhook()` POSTs the structured result to the Node.js backend at the configured `WEBHOOK_URL`. The webhook payload includes `correlationId`, `action`, and the full `proposed` block. Webhook failures are logged but do not affect the HTTP response to the listener.

**Step 13 — Message settlement**
FastAPI returns HTTP 200 to the listener. The listener calls `receiver.complete_message()`, permanently removing the message from the queue. The `AutoLockRenewer` context exits cleanly.

---

## Logging

All log output goes to stdout using the format:

```
2024-01-15 10:30:00 | INFO     | gfs.listener | Message validated | correlationId=... | document_count=2
2024-01-15 10:30:01 | INFO     | gfs.api      | Pipeline starting | request_id=abc123 | correlationId=...
2024-01-15 10:30:04 | INFO     | gfs.pipeline | Docling extracted | file=email.txt | n_pages=1 | paragraphs=12
2024-01-15 10:30:04 | INFO     | gfs.pipeline | Sufficiency check | sufficient=True
2024-01-15 10:30:04 | INFO     | gfs.api      | Pipeline complete | action=CREATE | confidence=1.00 | duration=3.21s
2024-01-15 10:30:04 | INFO     | gfs.api      | Webhook response  | correlationId=... | status=200
2024-01-15 10:30:04 | INFO     | gfs.listener | Message completed | correlationId=...
```

**Key logger names:**

| Logger | Source | What it covers |
|---|---|---|
| `gfs.listener` | `listener/worker.py` | Message receive, lock renewal, settlement |
| `gfs.api` | `fastapi_app.py` | Request handling, blob download, webhook |
| `gfs.pipeline` | `cover_pipeline.py` | Extraction stages, sufficiency, Vision |

In Docker and Azure deployments, stdout is captured automatically by the container runtime and available via `docker logs` or Azure Monitor.

---

## Docker

**Build:**
```bash
docker build -t gfs-ai:latest .
```

**Run:**
```bash
docker run \
  --env-file .env \
  -p 8000:8000 \
  gfs-ai:latest
```

The container runs `startup.sh`, which starts `uvicorn main:app --host 0.0.0.0 --port 8000`. Because `main.py` includes the Service Bus listener in its lifespan, both the API and the listener run in the same process inside the container. No separate container or process is needed.

`startup.sh`:
```bash
#!/bin/bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

Note: use `--workers 1` in production. Multiple workers would each start their own listener, all competing for the same queue messages. Azure Service Bus handles this correctly (each message goes to one worker), but a single worker is simpler to reason about and avoids unnecessary connections.

---

## Deployment

The recommended production deployment target is **Azure Container Apps** or **Azure App Service (containers)**.

**Steps:**

1. Build and push the Docker image to Azure Container Registry:
```bash
az acr build --registry yourregistry --image gfs-ai:latest .
```

2. Deploy to Azure Container Apps:
```bash
az containerapp create \
  --name gfs-ai \
  --resource-group your-rg \
  --image yourregistry.azurecr.io/gfs-ai:latest \
  --env-vars AZURE_SERVICE_BUS_CONNECTION_STRING=secretref:sb-conn \
             AZURE_OPENAI_API_KEY=secretref:openai-key \
             FASTAPI_BASE_URL=http://localhost:8000 \
  --ingress external \
  --target-port 8000
```

3. Set all environment variables as secrets in the container app configuration rather than plain environment variables.

4. Set the Azure Service Bus queue lock duration to 5 minutes (300 seconds) via Azure Portal → Service Bus Namespace → Queues → `ai-ocr-ingestion` → Properties → Lock Duration.

---

## Troubleshooting

**Blob 404 — document not found**
The SAS URL has expired or the blob was not uploaded before the queue message was sent. Check that the Node.js backend uploads blobs before sending the Service Bus message. SAS tokens should have at least a 30-minute expiry to survive queue delays. The message will be dead-lettered. Inspect the dead-letter queue in Azure Portal → Service Bus → Queue → Dead-letter Messages.

**Blob 403 — access denied**
The SAS token has expired or was generated with insufficient permissions. SAS tokens need `Read` permission on the blob. Regenerate the URL and re-send the message. Like 404, this is classified as a permanent failure and the message is dead-lettered.

**Missing environment variable on startup**
The service will raise `EnvironmentError` at startup with the name of the missing variable. Check your `.env` file against `.env.example`. When running in Docker, confirm the `--env-file` flag points to the correct file.

**Azure Service Bus connection error**
Verify `AZURE_SERVICE_BUS_CONNECTION_STRING` is the full connection string including the `Endpoint=sb://` prefix. The connection string must have `Manage` or `Listen` claims. Test connectivity by sending a message via Azure Portal → Service Bus Explorer and watching the listener terminal.

**Webhook 404 or connection refused**
The ngrok tunnel URL changes on every free-tier restart. Update `WEBHOOK_URL` in your `.env` whenever the tunnel URL changes. Confirm the Node.js backend is running before testing end-to-end.

**Azure OpenAI authentication error**
Verify `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, and `AZURE_OPENAI_DEPLOYMENT` are all set correctly. The endpoint must include the trailing slash and match the resource name exactly. The deployment name must match a deployment that exists in Azure OpenAI Studio.

**`correlationId` is null in the output JSON**
`process_lead_files()` must be called with `lead_id=correlation_id`. Check that both the `/process` and `/process-payload` endpoints pass `lead_id` correctly. The saved JSON filename will also reflect whether this was set correctly.

**SAS URL expires between message receipt and processing**
If the queue is heavily backed up, messages may wait longer than the SAS token expiry. Solutions: increase the SAS token expiry window in the Node.js backend (minimum 2 hours recommended), or switch to managed identity + role-based access control for blob access, which eliminates token expiry entirely.

**Port conflict when running both terminals**
FastAPI runs on port 8000 and the listener's uvicorn instance on 8001. If either port is in use, change with `--port XXXX`. Also update `FASTAPI_BASE_URL` if you change the FastAPI port.

**`non_ai` import errors — ModuleNotFoundError**
The folder must be named `non_ai` with an underscore, not `non-ai` with a hyphen. Python cannot import packages with hyphens in their name. Rename the folder and ensure `non_ai/__init__.py` exists.

**"No valid cover pages selected"**
Docling extracted the document content as figures rather than paragraphs, so `select_cover_pages()` returned an empty list. This typically happens with scanned PDFs or image-heavy documents. Enable Docling OCR by passing `use_ocr=True` to `build_converter()`, or ensure the PDF has a text layer.

---

## Future Improvements

- **Retry policies with exponential backoff** — the current abandon logic retries immediately on the next Service Bus delivery. Adding a delay between retries (via Service Bus scheduled enqueue) would reduce load during Azure outages.
- **Parallel blob downloads** — currently blobs are downloaded sequentially. Using `asyncio.gather()` or a thread pool for concurrent downloads would reduce latency for leads with many attachments.
- **Azure Key Vault integration** — move all secrets from environment variables to Azure Key Vault and reference them via managed identity, eliminating secret rotation risk.
- **Dead-letter monitoring and alerting** — set up an Azure Monitor alert on the dead-letter message count for `ai-ocr-ingestion`. Any increase indicates documents that need manual review.
- **Idempotency via MongoDB deduplication** — if `complete_message()` fails after a successful pipeline run, Azure re-delivers the message and the pipeline runs again. Once MongoDB is integrated, check `document_id` before processing to prevent duplicate output.
- **CI/CD pipeline** — add GitHub Actions workflows for automated testing, Docker image build, and deployment to Azure Container Apps on merge to main.
- **Structured logging to Azure Monitor** — replace plain text log format with JSON structured logs so logs can be queried in Azure Log Analytics by `correlationId`, `action`, or `confidence`.
- **Horizontal scaling with competing consumers** — run multiple container instances consuming from the same queue for higher throughput. Azure Service Bus handles competing consumers natively; each message goes to exactly one instance.
- **Webhook retry queue** — if the webhook POST fails, the result is currently only logged. A simple retry queue (even in-memory with a background task) would ensure the Node.js backend always receives the result eventually.
