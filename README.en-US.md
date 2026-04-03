# AI Law Assistant

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115.0-blue.svg" alt="FastAPI">
  <img src="https://img.shields.io/badge/React-18.2-brightgreen.svg" alt="React">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
</p>

A contract auditing system designed for finance, tax, and legal affairs. It includes contract upload and auditing, regulation import and retrieval, OCR-enhanced parsing, and configurable large language model (LLM) integration.

## Features

- **Contract Auditing**: Upload contracts (docx/pdf/scanned pdf), generate risk lists with evidence references, and export Word reports with precise annotations
- **OCR Enhancement**: Automatic OCR fallback for low-text PDFs with multi-engine routing
- **Regulation Management**: Admin upload, clause parsing, retrieval, and task tracking
- **Hybrid + Cross-language Retrieval**: BM25 + vector recall + optional reranking, with translation-assisted querying (`translation_config`)
- **Memory-based Audit Pipeline**: Clause-round auditing, citation whitelist mapping, unverifiable-risk filtering, and audit trace outputs
- **Secure Runtime Config**: LLM keys are intended to be loaded from secure store/environment instead of plaintext config persistence
- **Model/UI Configuration**: Centralized LLM/runtime config and UI feature toggles
- **Authentication & Admin**: Login, role-based management endpoints
- **Web UI**: React + Vite frontend with English/Chinese switching

## Technical Architecture

```text
Frontend (React + Vite)
        │ HTTP
Backend (FastAPI + Uvicorn)
  ├─ API Routes (auth, regulations, contracts, admin)
  ├─ Services (search, importer, contract_audit, crud)
  └─ Core (config, database, embedding, reranker, llm, ocr)
        │
Data Layer (SQLite + FTS5 + Files)
        │
Models (Embedding + Reranker + LLM)
```

### Tech Stack

| Layer | Technology |
|------|------|
| Backend Framework | FastAPI, Uvicorn |
| Database | SQLite + FTS5, Optional ChromaDB |
| Vector/Reranking | ONNX Runtime, Transformers, Torch |
| Models | BGE-small-zh/en, BGE-reranker |
| Frontend Framework | React 18, Vite 5 |
| Authentication | PyJWT, bcrypt, passlib |
| File Parsing | python-docx, pypdf |

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 22+
- Windows / Linux / macOS
- OCR Dependencies: tesseract, poppler, mineru (for scanned PDFs)
- Vector Database: SQLite in-memory computation is used by default. For production environments (1M+ vectors), it's highly recommended to install `chromadb` and switch engines via the admin UI.

### 1. Clone the Project

```bash
git clone https://github.com/your-repo/AI-Law-Assistant.git
cd AI-Law-Assistant
```

### 2. Install Backend Dependencies

```bash
cd app
pip install -r requirements.txt
```

### 3. Configure Environment

Copy the example configuration file:

```bash
# Linux / macOS
cp app/config.example.json app/config.json

# Windows PowerShell
Copy-Item app/config.example.json app/config.json
```

Modify `app/config.json` as needed. For production use, keep LLM API keys in secure store/environment variables rather than plaintext config.

### 4. Initialize Database

```bash
# Method 1: Using Python
python -m app.main --init

# Method 2: Using PowerShell script (Windows)
.\bin\init.ps1
```

### 5. Start Services

Start the backend service:

```bash
python -m app.main
```

The backend runs on `http://localhost:8000` by default.

Start the frontend development service:

```bash
cd web
npm install
npm run dev
```

The frontend runs on `http://localhost:5173` by default.

Frontend quality and testing commands:

```bash
cd web
npm run lint
npm run test
npm run storybook
```

### 6. Access the System

Open your browser and visit http://localhost:5173 to use the system.

## User Guide

### Contract Auditing (Main Interface)

1. Upload a contract (docx/pdf/scanned pdf).
2. Click audit to generate a summary and risk list.
3. View OCR and model metadata in the results.
4. Regulation evidence is displayed under risk items in the format: "Regulation Document Name" Article X (Summary: ...).
5. Support one-click download of the **Word version with annotations**. All detected risk points will be precisely located and attached as annotations next to the corresponding clause text.

The `citations` field in the audit result contains `citation_id`, `law_title`, `article_no`, and `excerpt`. The frontend will prioritize displaying these fields and retain evidence traceability.

### Regulation Import and Retrieval (Admin Page)

1. Go to Admin → "Regulation Upload".
2. Upload regulation documents (docx/pdf) and fill in the regulation metadata.
3. Check the import status through "Query Task".
4. Enter keywords and adjust weights in the "Retrieval" area.

Supported fields:
- **Title**: Regulation document title
- **Tag**: Regulation document number/tag
- **Issuer**: The institution that issued the regulation
- **Effective Date / Expiration Date**: Regulation validity period
- **Region**: Applicable region
- **Sub-Tag**: Industry classification

### Model Configuration (Admin Page)

1. Go to Admin → "Model Configuration".
2. Fill in the API endpoint, model, temperature, max tokens, etc.
3. It takes effect immediately after saving.

### OCR Verification

```bash
python bin/verify_ocr_env.py --pdf /path/to/sample.pdf --output reports/ocr_report.json
```

### Reranker User Guide

- **Enable**: Set `reranker_enabled: true` in `app/config.json`.
- **Model Path**:
  - Single model: `reranker_model_path`
  - Multi-language: `reranker_profiles` (e.g., `zh`/`en`)
- **Model Format**: Must be a Transformers SequenceClassification model directory.
- **Request-level Control**:
  - `rerank_enabled`: Enable/disable for a single request
  - `rerank_mode`: `on`/`off`/`ab` (A/B testing auto-routing)
  - `rerank_top_n`: Number of candidates to participate in reranking.
- **Exception Fallback**: When `rerank_fallback: true`, reranking failure will fallback to recall sorting.

### API Endpoints

| Method | Path | Description |
|------|------|------|
| GET | `/health` | Health check |
| GET | `/docs` | API documentation |
| POST | `/contracts/audit` | Contract auditing |
| POST | `/regulations/import` | Import regulations |
| GET | `/regulations/import/{job_id}` | Query import task |
| GET | `/regulations` | Get regulation list |
| GET | `/regulations/{id}/articles` | Get regulation clauses |
| POST | `/regulations/search` | Search regulations |
| POST | `/embedding/compute` | Compute embeddings |
| GET | `/api/admin/llm-config` | Get LLM config |
| PUT | `/api/admin/llm-config` | Update LLM config |

## Configuration Guide

### Configuration File (app/config.json)

Use `app/config.example.json` as the source of truth. Key groups to review:

- `llm_config`: provider/base URL/model/sampling/timeout
- `secret_store`: secure key backend (for LLM API key persistence)
- `embedding_profiles` / `reranker_profiles`: language-specific model paths
- `translation_config`: cross-language retrieval translation settings
- `retrieval_regulation_language`: primary language of the regulation corpus
- `ocr_engines` and `mineru`: OCR routing and engine options
- `ui_config`: frontend display toggles

### Environment Variables

| Variable | Default | Description |
|------|--------|------|
| `APP_PORT` | 8000 | Service port |
| `APP_CONFIG` | config.json | Configuration file path |
| `APP_PORT_AUTO_SWITCH` | 1 | Auto-switch port when conflicted |

### Running Tests

```bash
pip install pytest pytest-asyncio
pytest tests/
```

### Download Vector Sub-models

If you need to use other vector sub-models, you can run:

```bash
python bin/download_embedding_model.py
```

Or place the ONNX model file in the `models/embedding/zh/` directory.

## License

MIT License
