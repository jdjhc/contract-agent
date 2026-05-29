"""FastAPI entrypoint — Research Contract Adviser Agent."""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

# Load .env BEFORE importing modules that read env vars
load_dotenv(Path(__file__).parent / ".env")

from agent import chat as agent_chat
from agent import get_document, ingest, review
from api_clients import _resolve_backend, is_configured
from models import (
    ChatRequest,
    ChatResponse,
    ClassifyResponse,
    ContractReview,
    UploadResponse,
)
from services.parser import SUPPORTED_EXTENSIONS

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = REPO_ROOT / "data" / "Contract Reviewer Agent" / "Redacted examples"
EVAL_REPORT = REPO_ROOT / "eval" / "reports" / "latest.json"

# Curated samples surfaced in the UI's "Try sample" picker.
SAMPLE_REGISTRY = [
    # Confidential Disclosure Agreements
    {"id": "cda_1", "label": "CDA — Example 1", "contract_type_hint": "Confidential Disclosure Agreement", "description": "Confidential Disclosure Agreement (anonymised).", "filename": "CDA example 1.pdf"},
    {"id": "cda_2", "label": "CDA — Example 2", "contract_type_hint": "Confidential Disclosure Agreement", "description": "Confidential Disclosure Agreement (anonymised).", "filename": "CDA example 2.pdf"},
    {"id": "cda_3", "label": "CDA — Example 3", "contract_type_hint": "Confidential Disclosure Agreement", "description": "Confidential Disclosure Agreement (anonymised).", "filename": "CDA example 3.pdf"},
    {"id": "nda_1", "label": "NDA — Example 1", "contract_type_hint": "Confidential Disclosure Agreement", "description": "Non-Disclosure Agreement (anonymised).", "filename": "NDA example 1.pdf"},
    {"id": "nda_student_1", "label": "NDA — Student Work Experience 1", "contract_type_hint": "Confidential Disclosure Agreement", "description": "NDA for student work experience placement (anonymised).", "filename": "NDA student work experience example 1.pdf"},
    {"id": "nda_student_2", "label": "NDA — Student Work Experience 2", "contract_type_hint": "Confidential Disclosure Agreement", "description": "NDA for student work experience placement (anonymised).", "filename": "NDA student work experience example 2.pdf"},
    # Collaboration Agreements
    {"id": "collab_1", "label": "Collaboration Agreement — Example 1", "contract_type_hint": "Collaboration Agreement", "description": "Research Collaboration Agreement (anonymised).", "filename": "Collaboration Agreement Example 1.pdf"},
    {"id": "collab_2", "label": "Collaboration Agreement — Example 2", "contract_type_hint": "Collaboration Agreement", "description": "Research Collaboration Agreement (anonymised).", "filename": "Collaboration Agreement Example 2.pdf"},
    {"id": "collab_3", "label": "Collaboration Agreement — Example 3", "contract_type_hint": "Collaboration Agreement", "description": "Research Collaboration Agreement (anonymised).", "filename": "Collaboration Agreement Example 3.pdf"},
    {"id": "collab_4", "label": "Collaboration Agreement — Example 4", "contract_type_hint": "Collaboration Agreement", "description": "Research Collaboration Agreement (anonymised).", "filename": "Collaboration Agreement Example 4.pdf"},
    {"id": "contract_5", "label": "Collaboration Agreement — Example 5", "contract_type_hint": "Collaboration Agreement", "description": "Collaboration / Investment Agreement (anonymised).", "filename": "Contract Example 5.pdf"},
    # Material Transfer Agreements
    {"id": "mta_1", "label": "MTA — Example 1", "contract_type_hint": "Material Transfer Agreement", "description": "Material Transfer Agreement (anonymised).", "filename": "MTA Example 1.pdf"},
    {"id": "mta_2", "label": "MTA — Example 2", "contract_type_hint": "Material Transfer Agreement", "description": "Material Transfer Agreement (anonymised).", "filename": "MTA Example 2.pdf"},
    {"id": "mta_3", "label": "MTA — Example 3", "contract_type_hint": "Material Transfer Agreement", "description": "Material Transfer Agreement (anonymised).", "filename": "MTA Example 3.pdf"},
    {"id": "mta_4", "label": "MTA — Example 4", "contract_type_hint": "Material Transfer Agreement", "description": "Material Transfer Agreement (anonymised).", "filename": "MTA Example 4.pdf"},
    # Data Transfer Agreement
    {"id": "dta_1", "label": "Data Transfer Agreement — Example 1", "contract_type_hint": "Data Transfer Agreement", "description": "Data Transfer Agreement (anonymised).", "filename": "Data Transfer Agreement Example.pdf"},
    # Research Subcontracts
    {"id": "subcontract_1", "label": "Research Subcontract — Example 1", "contract_type_hint": "Research Subcontract", "description": "Research Subcontract Agreement (anonymised).", "filename": "Subcontract Example 1.pdf"},
    {"id": "contract_4", "label": "Research Subcontract — Example 2", "contract_type_hint": "Research Subcontract", "description": "Research Subcontract Agreement (anonymised).", "filename": "Contract Example 4.pdf"},
    # Commercial Research Contract
    {"id": "subcontract_2", "label": "Commercial Research Contract — Example 1", "contract_type_hint": "Commercial Research Contract", "description": "Commercial Research Contract (anonymised).", "filename": "Subcontract Example 2.pdf"},
    # Student Research Agreements
    {"id": "student_1", "label": "Student Research Agreement — Example 1", "contract_type_hint": "Student Research Agreement", "description": "Student Research Agreement (anonymised).", "filename": "Student Research Agreement Example 1.pdf"},
    {"id": "student_2", "label": "Student Research Agreement — Example 2", "contract_type_hint": "Student Research Agreement", "description": "Student Research Agreement (anonymised).", "filename": "Student Research Agreement Example 2.pdf"},
    # Provision of Services
    {"id": "psa_1", "label": "Provision of Services — Consultancy", "contract_type_hint": "Provision of Services Agreement", "description": "Consultancy Services Agreement (anonymised).", "filename": "Consultancy Services Agreement Example.pdf"},
    {"id": "psa_2", "label": "Provision of Services — Goods & Services", "contract_type_hint": "Provision of Services Agreement", "description": "Contract for Goods and Services (anonymised).", "filename": "Contract for Goods and Services Example.pdf"},
    {"id": "psa_3", "label": "Provision of Services — Service Provider", "contract_type_hint": "Provision of Services Agreement", "description": "Service Provider Agreement (anonymised).", "filename": "Service Provider Agreement Example.pdf"},
    {"id": "contract_3", "label": "Provision of Services — Example 3", "contract_type_hint": "Provision of Services Agreement", "description": "Provision of Services Agreement (anonymised).", "filename": "Contract Example 3.pdf"},
    # Master Services Agreements (Example 1 and 1.5 are two parts of the same contract)
    {"id": "msa_1", "label": "Master Services Agreement — Example 1", "contract_type_hint": "Master Services Agreement", "description": "Master Services Agreement (anonymised, two parts merged).", "filename": "Master Services Agreement Example 1 (1).pdf", "fragments": ["Master Services Agreement Example 1.5.pdf"]},
    # Public Research Contracts
    {"id": "contract_1", "label": "Public Research Contract — Example 1", "contract_type_hint": "Public Research Contract", "description": "Public Research Contract (anonymised).", "filename": "Contract Example 1.pdf"},
    {"id": "contract_2", "label": "Public Research Contract — Example 2", "contract_type_hint": "Public Research Contract", "description": "Public Research Contract (anonymised).", "filename": "Contract Example 2.pdf"},
]

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
CORS_ORIGINS = [
    o.strip() for o in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
    ).split(",") if o.strip()
]

app = FastAPI(
    title="Research Contract Adviser Agent",
    description=(
        "POC backend for the UoA Research Contracts Adviser. "
        "Upload a contract → receive a four-flag review report."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    backend = _resolve_backend()
    status_map = {
        "azure-openai": "Connected — Azure OpenAI",
        "foundry-maas": "Connected — Azure AI Foundry",
        "mock": "LLM not configured — running in mock mode",
    }
    return {
        "status": "ok",
        "llm_configured": is_configured(),
        "llm_status": status_map.get(backend, backend),
        "supported_uploads": sorted(SUPPORTED_EXTENSIONS),
        "max_upload_mb": MAX_UPLOAD_MB,
    }


@app.post("/api/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...)) -> UploadResponse:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Unsupported file type {suffix!r}. "
            f"Allowed: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD_MB} MB limit.")
    try:
        doc = await ingest(file.filename or "contract", raw)
    except Exception as e:  # noqa: BLE001 — surface parse errors to the client
        raise HTTPException(400, f"Could not parse document: {e}") from e

    return UploadResponse(
        document_id=doc.document_id,
        filename=doc.filename,
        char_count=len(doc.text),
        clause_count=len(doc.clauses),
    )


@app.post("/api/classify/{document_id}", response_model=ClassifyResponse)
async def classify_route(document_id: str) -> ClassifyResponse:
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(404, "Document not found.")
    from agent import classify as _classify
    contract_type, confidence, rationale = await _classify(doc)
    return ClassifyResponse(
        document_id=doc.document_id,
        filename=doc.filename,
        contract_type=contract_type,
        confidence=confidence,
        rationale=rationale,
    )


@app.post("/api/review/{document_id}", response_model=ContractReview)
async def review_route(document_id: str) -> ContractReview:
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(404, "Document not found.")
    return await review(doc)


@app.get("/api/samples")
async def list_samples() -> dict:
    available = []
    for s in SAMPLE_REGISTRY:
        path = SAMPLES_DIR / s["filename"]
        if path.exists():
            available.append({**s, "size_bytes": path.stat().st_size})
    return {"samples": available}


@app.post("/api/samples/{sample_id}/load", response_model=UploadResponse)
async def load_sample(sample_id: str) -> UploadResponse:
    sample = next((s for s in SAMPLE_REGISTRY if s["id"] == sample_id), None)
    if not sample:
        raise HTTPException(404, "Unknown sample.")
    path = SAMPLES_DIR / sample["filename"]
    if not path.exists():
        raise HTTPException(
            404,
            f"Sample file missing on disk: {path.relative_to(REPO_ROOT)}",
        )
    fragments = sample.get("fragments", [])
    if fragments:
        from api_clients import track_usage
        from services.parser import extract_text_async, split_clauses_async
        import uuid
        from agent import StoredDocument, _store
        with track_usage() as ingest_usage:
            parts = [await extract_text_async(sample["filename"], path.read_bytes())]
            for frag in fragments:
                fpath = SAMPLES_DIR / frag
                if fpath.exists():
                    parts.append(await extract_text_async(frag, fpath.read_bytes()))
            combined_text = "\n\n".join(parts)
            clauses = await split_clauses_async(combined_text)
        doc = StoredDocument(
            document_id=uuid.uuid4().hex,
            filename=sample["filename"],
            text=combined_text,
            clauses=clauses,
            ingest_calls=list(ingest_usage.calls),
        )
        _store[doc.document_id] = doc
    else:
        doc = await ingest(sample["filename"], path.read_bytes())
    return UploadResponse(
        document_id=doc.document_id,
        filename=doc.filename,
        char_count=len(doc.text),
        clause_count=len(doc.clauses),
    )


@app.get("/api/eval/latest")
async def eval_latest() -> dict:
    if not EVAL_REPORT.exists():
        raise HTTPException(
            404,
            "No eval report yet. Run `uv run python eval/run_eval.py --save`.",
        )
    return json.loads(EVAL_REPORT.read_text(encoding="utf-8"))


@app.post("/api/chat", response_model=ChatResponse)
async def chat_route(req: ChatRequest) -> ChatResponse:
    history = [t.model_dump() for t in req.history]
    reply = await agent_chat(req.document_id, history, req.message)
    return ChatResponse(reply=reply, citations=[])


def run() -> None:
    """Entrypoint for `uv run serve`."""
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8000")),
        reload=True,
    )


if __name__ == "__main__":
    run()
