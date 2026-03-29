# Configuration Reference

This document explains the configuration fields in `app/config.example.json`.

## 1) Core Paths

| Key | Type | Example | Description |
|---|---|---|---|
| `data_dir` | string | `../data` | Base data directory for runtime artifacts (DB, memory, cache, etc.). |
| `db_path` | string | `../data/app.db` | Main application SQLite database path. |
| `files_dir` | string | `../data/files` | Uploaded file storage directory. |
| `static_dir` | string | `../web/dist` | Frontend static assets directory. |
| `default_language` | string | `zh` | Default UI/business language (`zh` / `en`). |

## 2) Embedding Profiles

`embedding_profiles` is language-specific embedding config.

### `embedding_profiles.zh` / `embedding_profiles.en`

| Key | Type | Description |
|---|---|---|
| `embedding_model` | string | ONNX model file path. |
| `embedding_tokenizer_dir` | string | Tokenizer directory path. |
| `embedding_model_id` | string | Source model ID (for identification/download). |
| `embedding_source` | string | Model source platform (e.g. `modelscope`). |
| `embedding_max_seq_len` | int | Maximum sequence length for embedding input. |
| `embedding_pooling` | string | Embedding pooling strategy (e.g. `cls`). |
| `embedding_query_instruction` | string | Query prefix/instruction used to improve retrieval quality. |
| `embedding_threads` | int | Thread count for embedding inference. |

## 3) Logging

| Key | Type | Description |
|---|---|---|
| `log_level` | string | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `log_base_dir` | string | Base directory for log files. |
| `log_dir` | string | Effective log output directory. |
| `log_max_bytes` | int | Max size for a single log file before rotation. |
| `log_backup_count` | int | Number of rotated log files to keep. |
| `log_when` | string | Rotation policy (e.g. `midnight`). |
| `log_interval` | int | Rotation interval unit count. |

## 4) OCR

| Key | Type | Description |
|---|---|---|
| `ocr_enabled` | bool | Enable OCR fallback for low-text documents. |
| `ocr_languages` | string | OCR language pack string (e.g. `chi_sim+eng`). |
| `ocr_min_text_length` | int | Threshold to trigger OCR when extracted text is too short. |
| `ocr_dpi` | int | DPI used for OCR rendering. |
| `ocr_engine` | string | OCR engine mode (`auto` or specific engine). |
| `ocr_engine_order` | string[] | Engine fallback order. |
| `ocr_engine_by_type` | object | Per file type engine override, e.g. `pdf -> tesseract`. |
| `ocr_engines` | object | Dynamic OCR engine registry (`module` + `function`). |

## 5) MinerU OCR Engine

`mineru` controls MinerU runtime behavior.

| Key | Type | Description |
|---|---|---|
| `backend` | string | Backend selection strategy. |
| `method` | string | OCR method mode. |
| `lang` | string | OCR language mode for MinerU. |
| `device` | string | Runtime device (`cpu` / `cuda`). |
| `formula` | bool | Enable formula parsing. |
| `table` | bool | Enable table parsing. |
| `model_source` | string | Model source platform. |
| `timeout` | int | OCR timeout (seconds). |

## 6) LLM

### `llm_config`

| Key | Type | Description |
|---|---|---|
| `provider` | string | LLM provider type, currently OpenAI-compatible style. |
| `api_base` | string | Base URL of chat-completions API. |
| `api_key` | string | API key. Prefer secure store/environment in production. |
| `model` | string | Model name. |
| `temperature` | float | Sampling temperature. |
| `max_tokens` | int | Max response tokens. |
| `timeout` | int | Request timeout in seconds. |
| `headers` | object | Extra HTTP headers. |

### `secret_store`

| Key | Type | Description |
|---|---|---|
| `enabled` | bool | Enable secure key storage. |
| `backend` | string | Secret backend (`keyring`, etc.). |
| `service_name` | string | Keyring service namespace. |
| `llm_api_key_name` | string | Key name used for storing LLM key. |

## 7) Retrieval & Translation

| Key | Type | Description |
|---|---|---|
| `retrieval_regulation_language` | string | Primary language of regulation corpus (`zh` recommended if corpus is Chinese). |

### `translation_config`

| Key | Type | Description |
|---|---|---|
| `enabled` | bool | Enable query translation for cross-language retrieval. |
| `backend` | string | Translation backend implementation. |
| `mode` | string | `dual` or `translate_only`. |
| `cross_lang_source_query_enabled` | bool | In `dual` mode and cross-language scenario, whether to also run source-language retrieval. Set `false` to avoid duplicate retrieval when corpus is Chinese-only. |
| `model_id` | string | Translation model ID. |
| `model_dir` | string | Local model directory override. |
| `device` | string | Translation device (`cpu` / `cuda`). |
| `source_langs` | string[] | Source languages eligible for translation. |
| `target_lang` | string | Translation target language. |
| `max_source_chars` | int | Max query chars sent to translator. |
| `max_new_tokens` | int | Max generation tokens for translation model. |
| `num_beams` | int | Beam size for translation decoding. |
| `cache_size` | int | Translation result cache size. |
| `glossary` | object | Term mapping dictionary to stabilize legal terminology. |

## 8) Trace/Debug Logs

| Key | Type | Description |
|---|---|---|
| `llm_trace_enabled` | bool | Enable LLM interaction tracing. |
| `llm_trace_dir` | string | LLM trace output directory. |
| `llm_trace_max_chars` | int | Max chars stored per long text field in trace. |
| `rag_trace_enabled` | bool | Enable RAG retrieval trace logging. |
| `rag_trace_dir` | string | RAG trace output directory. |
| `rag_trace_max_chars` | int | Max chars per trace field. |
| `rag_trace_max_rows` | int | Max rows stored per retrieval-result trace event. |

## 9) UI Config

### `ui_config`

| Key | Type | Description |
|---|---|---|
| `show_citation_source` | bool | Whether to display citation source details in UI. |
| `default_theme` | string | Default theme name. |

## 10) Contract Preview

### `contract_preview`

| Key | Type | Description |
|---|---|---|
| `enabled` | bool | Enable contract preview generation. |
| `cache_dir` | string | Preview cache directory. |
| `pdf_dpi` | int | Render DPI for PDF preview. |
| `pdf_max_pages` | int | Maximum pages to process for preview. |
| `text_lines_per_page` | int | Approx lines per page in text preview mode. |
| `docx_visual_enabled` | bool | Enable DOCX visual pagination mode. |
| `docx_page_width` | int | DOCX virtual page width. |
| `docx_page_height` | int | DOCX virtual page height. |
| `docx_margin` | int | DOCX page margin. |
| `docx_line_height` | int | DOCX line height. |
| `docx_chars_per_line` | int | Estimated chars per line for layout. |
| `docx_paragraph_spacing` | int | Paragraph spacing in visual layout. |
| `coord_provider` | string | Coordinate extraction provider. |
| `docx_coord_from_pdf` | bool | Try deriving DOCX coordinates from PDF route. |
| `docx_pdf_min_text_ratio` | float | Minimum text ratio threshold for PDF route. |
| `docx_pdf_gate_min_chars` | int | Minimum chars threshold for PDF gate. |
| `docx_pdf_keep_file` | bool | Whether to keep intermediate PDF file. |

## 11) Memory Audit Behavior

| Key | Type | Description |
|---|---|---|
| `memory_use_long_hits` | bool | Whether clause prompt includes long-memory hits. |
| `memory_enable_long_memory` | bool | Enable long-term memory retrieval/indexing. |
| `memory_enable_short_memory` | bool | Enable short-term memory buffer. |
| `memory_filter_unverifiable_risks` | bool | Filter risks that cannot be mapped to valid citations. |
| `memory_filtered_risk_log_limit` | int | Max count of filtered-risk hits kept in debug/meta. |

## 12) Contract Audit Debug Lifecycle

| Key | Type | Description |
|---|---|---|
| `contract_audit_debug_retention_days` | int | Keep debug folders for N days. |
| `contract_audit_debug_cleanup_interval_sec` | int | Cleanup scan interval in seconds. |
| `contract_audit_debug_archive_before_delete` | bool | Archive old debug folders to zip before deletion. |
| `contract_audit_debug_archive_dir` | string | Archive output directory; empty means use module-local `_archive`. |

## 13) Upload & CORS

| Key | Type | Description |
|---|---|---|
| `max_upload_mb` | int | Max upload size in MB. |
| `cors_allow_origins` | string[] | Allowed frontend origins for CORS. |

---

## Recommended Profiles

### Chinese-only regulation corpus
- `retrieval_regulation_language: "zh"`
- `translation_config.enabled: true`
- `translation_config.mode: "dual"`
- `translation_config.cross_lang_source_query_enabled: false`

This setup uses translated Chinese query for RAG and avoids redundant English semantic retrieval.

### Development mode
- Enable `rag_trace_enabled` (and optionally `llm_trace_enabled`) for troubleshooting.
- Keep `contract_audit_debug_retention_days` small (e.g. `7`) to control disk usage.

### Production mode
- Prefer secure key storage (`secret_store.enabled: true`) and do not store raw API keys in config files.
- Set strict CORS origins.
- Tune trace/debug retention and archive settings based on compliance/storage policy.