import os
import uuid
import json
import logging
import threading
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Query, Body, Request
from fastapi.responses import FileResponse
from app.api.dependencies import (
    get_current_user,
    get_app_llm,
    get_app_embedder,
    get_app_reranker,
    get_app_translator,
)
from app.services.crud import (
    insert_document,
    insert_contract_audit,
    get_document_by_id_for_user,
    get_latest_contract_audit_by_document,
    get_latest_successful_document_digitization_by_document,
)
from app.services.audit_orchestrator import AuditServices, run_contract_pipeline_bundle
from app.services.contract_preview_assets import build_contract_preview_manifest, find_preview_page
from app.services.contract_audit import _build_contract_summary, _build_preview_clauses
from app.services.docx_renderer import render_tax_audit_docx
from app.services.pdf_pipeline import build_pdf_pipeline_summary, get_pdf_pipeline_config, resolve_pdf_audit_source
from app.services.contract_audit_stage_log import (
    clear_contract_audit_log_context,
    register_contract_audit_log_context,
    write_contract_audit_stage_log,
)
from app.services.audit_utils import _normalize_lang
from app.core.utils import extract_text_with_config
from app.core.config import get_config

logger = logging.getLogger("law_assistant")

_audit_progress: Dict[str, Dict[str, Any]] = {}
_audit_progress_lock = threading.Lock()
_AUDIT_STAGE_TITLES = {
    "received": "request_received",
    "saved": "file_saved",
    "document": "document_recorded",
    "digitizing": "pdf_digitizing",
    "digitized": "digitized_word_ready",
    "extracting": "text_extracting",
    "extract_done": "text_extract_done",
    "parsing": "contract_parsing",
    "parsing_done": "contract_parse_done",
    "retrieval": "evidence_retrieving",
    "retrieval_done": "evidence_ready",
    "auditing": "ai_auditing",
    "reviewing": "high_risk_reviewing",
    "audit_done": "audit_result_assembling",
    "done": "audit_completed",
    "failed": "audit_failed",
}


def _normalize_theme(v: Optional[str]) -> str:
    s = str(v or "").strip().lower()
    return "light" if s == "light" else "dark"


_AUDIT_ERROR_SPECS = [
    {
        "code": "AUDIT_STORAGE_PERMISSION_DENIED",
        "category": "storage_permission",
        "retryable": True,
        "keywords": [
            "permissionerror",
            "permission denied",
            "winerror 5",
            "拒绝访问",
            "access is denied",
        ],
        "title": {
            "zh": "服务目录没有写入权限",
            "en": "The service cannot write to the target directory",
        },
        "message": {
            "zh": "服务在写入缓存、日志或调试目录时被系统拒绝，本次审计已中止。",
            "en": "The service was denied while writing cache, log, or debug artifacts, so the audit was stopped.",
        },
        "user_message": {
            "zh": "服务目录权限不足，请检查 data/logs 相关目录的写权限后重试。",
            "en": "The service does not have enough directory permissions. Check write access for data/log directories and retry.",
        },
        "operator_scenarios": {
            "zh": [
                "调试目录、日志目录或缓存目录被解析到了项目外层路径，当前进程没有写权限。",
                "Windows 对当前运行用户限制了目标目录的创建或写入。",
            ],
            "en": [
                "A debug, log, or cache directory was resolved outside the project tree and the current process cannot write there.",
                "Windows denied directory creation or write access for the current service user.",
            ],
        },
        "operator_actions": {
            "zh": [
                "确认 rag_trace_dir、llm_trace_dir、data_dir 都被解析到项目工作区内。",
                "检查当前 PowerShell/服务进程用户是否拥有目标目录写权限。",
            ],
            "en": [
                "Confirm rag_trace_dir, llm_trace_dir, and data_dir all resolve inside the project workspace.",
                "Check whether the current PowerShell or service user has write access to the target directory.",
            ],
        },
    },
    {
        "code": "AUDIT_PDF_DIGITIZATION_FAILED",
        "category": "pdf_digitization",
        "retryable": True,
        "keywords": [
            "digitize_",
            "mineru cli not found",
            "content_list_missing",
            "content_list_empty",
            "doclayout_yolo",
            "mineru",
        ],
        "title": {
            "zh": "PDF 数字化失败",
            "en": "PDF digitization failed",
        },
        "message": {
            "zh": "审计前的 PDF 数字化处理未完成，本次审计已中止。",
            "en": "The PDF digitization step did not complete before the audit, so the audit was stopped.",
        },
        "user_message": {
            "zh": "PDF 数字化失败，请确认 OCR 组件与 MinerU 环境可用后重试。",
            "en": "PDF digitization failed. Confirm the MinerU and OCR runtime are available, then retry.",
        },
        "operator_scenarios": {
            "zh": [
                "MinerU CLI 未安装或未命中当前项目虚拟环境。",
                "MinerU pipeline 缺少 doclayout_yolo 等布局依赖。",
                "MinerU 运行后未产出 _content_list.json 等结构化结果文件。",
            ],
            "en": [
                "MinerU CLI is missing or the service is not using the project virtual environment.",
                "The MinerU pipeline is missing layout dependencies such as doclayout_yolo.",
                "MinerU ran but did not generate structured files such as _content_list.json.",
            ],
        },
        "operator_actions": {
            "zh": [
                "确认服务使用项目 .venv 中的 mineru 可执行文件。",
                "检查 requirements 与本地虚拟环境是否都安装了 MinerU 扩展依赖。",
                "查看 main.log 中 digitize_pdf_start 之后的 stdout/stderr 详情。",
            ],
            "en": [
                "Confirm the service is using the MinerU executable from the project .venv.",
                "Verify both requirements and the local virtual environment include MinerU optional dependencies.",
                "Inspect stdout/stderr after digitize_pdf_start in main.log.",
            ],
        },
    },
    {
        "code": "AUDIT_LLM_AUTH_FAILED",
        "category": "llm_auth",
        "retryable": False,
        "keywords": [
            "invalid_api_key",
            "incorrect api key",
            "authentication",
            "unauthorized",
            "401",
        ],
        "title": {
            "zh": "模型鉴权失败",
            "en": "Model authentication failed",
        },
        "message": {
            "zh": "当前模型服务鉴权失败，本次审计未能继续执行。",
            "en": "Authentication with the active model provider failed, so the audit could not continue.",
        },
        "user_message": {
            "zh": "模型鉴权失败，请检查当前生效的密钥配置后重试。",
            "en": "Model authentication failed. Check the active credentials and retry.",
        },
        "operator_scenarios": {
            "zh": [
                "Windows 环境变量中的 API Key 缺失、过期或与当前 provider 不匹配。",
                "运行时路由命中了云端 provider，但对应 base/model/key 组合无效。",
            ],
            "en": [
                "The API key from Windows environment variables is missing, expired, or does not match the current provider.",
                "The runtime routed to a cloud provider but the active base/model/key combination is invalid.",
            ],
        },
        "operator_actions": {
            "zh": [
                "检查当前进程读取到的 provider、api_base、model 与 key 是否一致。",
                "确认测试环境与内网环境没有混用不同的密钥来源。",
            ],
            "en": [
                "Verify the provider, api_base, model, and key read by the current process are aligned.",
                "Make sure test credentials and intranet credentials are not mixed.",
            ],
        },
    },
    {
        "code": "AUDIT_LLM_TIMEOUT",
        "category": "llm_timeout",
        "retryable": True,
        "keywords": [
            "timeout",
            "timed out",
            "readtimeout",
            "read timeout",
        ],
        "title": {
            "zh": "模型响应超时",
            "en": "Model request timed out",
        },
        "message": {
            "zh": "模型在限定时间内未返回结果，本次审计已中止。",
            "en": "The model did not return a result within the configured timeout, so the audit was stopped.",
        },
        "user_message": {
            "zh": "模型响应超时，请检查当前服务可用性或调整超时配置后重试。",
            "en": "The model request timed out. Check service availability or increase the timeout, then retry.",
        },
        "operator_scenarios": {
            "zh": [
                "云端 provider 在长合同 multipass 审计时响应过慢。",
                "本地模型可用，但当前并发或上下文长度导致推理时间过长。",
            ],
            "en": [
                "The cloud provider responded too slowly for a long-contract multipass audit.",
                "The local model is available, but the current concurrency or context size caused an excessive response time.",
            ],
        },
        "operator_actions": {
            "zh": [
                "查看当前路由到的模型与 timeout/timeout_retry 配置。",
                "长合同场景优先降低并发或切换到更稳定的模型服务。",
            ],
            "en": [
                "Inspect the active model route and timeout/timeout_retry settings.",
                "For long contracts, reduce concurrency or switch to a more stable model service.",
            ],
        },
    },
    {
        "code": "AUDIT_OCR_FAILED",
        "category": "ocr_runtime",
        "retryable": True,
        "keywords": [
            "ocr_parse_failed",
            "ocr_failed",
            "torchvision",
            "onnxruntime",
        ],
        "title": {
            "zh": "OCR 运行失败",
            "en": "OCR runtime failed",
        },
        "message": {
            "zh": "OCR 未能正确提取 PDF 文本，本次审计已中止。",
            "en": "OCR failed to extract PDF text correctly, so the audit was stopped.",
        },
        "user_message": {
            "zh": "OCR 运行失败，请检查 OCR 依赖与模型文件后重试。",
            "en": "OCR failed. Check OCR dependencies and model files, then retry.",
        },
        "operator_scenarios": {
            "zh": [
                "torchvision、onnxruntime 等 OCR 依赖缺失或版本不兼容。",
                "扫描版 PDF 需要 OCR，但当前 OCR 引擎未正确初始化。",
            ],
            "en": [
                "OCR dependencies such as torchvision or onnxruntime are missing or incompatible.",
                "The scanned PDF requires OCR, but the current OCR engine did not initialize correctly.",
            ],
        },
        "operator_actions": {
            "zh": [
                "核对 requirements 与当前虚拟环境中的 OCR 依赖版本。",
                "在终端单独运行 OCR/MinerU 命令确认模型文件可以正常加载。",
            ],
            "en": [
                "Compare the OCR dependency versions in requirements with the active virtual environment.",
                "Run the OCR or MinerU command directly in a terminal to confirm model files can be loaded.",
            ],
        },
    },
    {
        "code": "AUDIT_LLM_CONNECTION_FAILED",
        "category": "llm_connection",
        "retryable": True,
        "keywords": [
            "connecterror",
            "connection refused",
            "actively refused",
            "目标计算机积极拒绝",
            "winerror 10061",
            "failed to establish a new connection",
        ],
        "title": {
            "zh": "模型服务连接失败",
            "en": "Cannot connect to the model service",
        },
        "message": {
            "zh": "服务未能连接到当前生效的模型地址，本次审计已中止。",
            "en": "The service could not connect to the active model endpoint, so the audit was stopped.",
        },
        "user_message": {
            "zh": "模型服务连接失败，请检查当前生效的 provider、地址和端口后重试。",
            "en": "The model service connection failed. Check the active provider, endpoint, and port, then retry.",
        },
        "operator_scenarios": {
            "zh": [
                "本地 Ollama 没有启动，或 11434 端口当前没有监听。",
                "运行时路由仍然命中了本地模型，但用户预期应使用 DeepSeek 等云端 provider。",
                "模型地址、端口或代理配置错误，导致 TCP 连接被拒绝。",
            ],
            "en": [
                "Local Ollama is not running, or nothing is listening on port 11434.",
                "The runtime still routed to a local model even though a cloud provider such as DeepSeek was expected.",
                "The configured model endpoint, port, or proxy is incorrect, so the TCP connection was refused.",
            ],
        },
        "operator_actions": {
            "zh": [
                "查看 main.log 中 llm_request_start 的 provider、api_base 和 selected_role。",
                "如果是本地模型，确认 Ollama 进程和端口监听状态；如果是云端测试，确认合同审计是否已切到 cloud_fallback。",
                "检查环境变量与 app/config.json 中 local_llm 路由策略是否一致。",
            ],
            "en": [
                "Inspect provider, api_base, and selected_role in llm_request_start inside main.log.",
                "If this is a local model, confirm the Ollama process and port listener; if it is a cloud test, confirm contract audit has switched to cloud_fallback.",
                "Verify environment variables and the local_llm routing policy in app/config.json are aligned.",
            ],
        },
    },
    {
        "code": "AUDIT_SERVICE_UNAVAILABLE",
        "category": "service_unavailable",
        "retryable": True,
        "keywords": [
            "llm service unavailable",
            "embedding service unavailable",
            "reranker service unavailable",
            "translation service unavailable",
            "service unavailable",
        ],
        "title": {
            "zh": "依赖服务不可用",
            "en": "A required service is unavailable",
        },
        "message": {
            "zh": "审计所需的模型或检索服务当前不可用，本次审计已中止。",
            "en": "A model or retrieval dependency required for the audit is unavailable, so the audit was stopped.",
        },
        "user_message": {
            "zh": "依赖服务当前不可用，请检查模型或检索服务状态后重试。",
            "en": "A required service is unavailable. Check the model or retrieval runtime, then retry.",
        },
        "operator_scenarios": {
            "zh": [
                "LLM、Embedding、Reranker 或 Translation 服务未启动。",
                "服务已启动，但当前配置指向了错误的地址或模型角色。",
            ],
            "en": [
                "The LLM, embedding, reranker, or translation service is not running.",
                "The service is running, but the current configuration points to the wrong endpoint or model role.",
            ],
        },
        "operator_actions": {
            "zh": [
                "优先检查 /health 与当前 runtime 读取到的配置。",
                "确认端口没有被其他本机服务占用。",
            ],
            "en": [
                "Check /health and confirm the active runtime configuration first.",
                "Make sure the expected ports are not occupied by another local service.",
            ],
        },
    },
    {
        "code": "AUDIT_RUNTIME_FAILED",
        "category": "runtime",
        "retryable": True,
        "keywords": [],
        "title": {
            "zh": "审计运行失败",
            "en": "Audit runtime failed",
        },
        "message": {
            "zh": "审计过程中发生未归类异常，本次审计已中止。",
            "en": "An uncategorized runtime error occurred during the audit, so the audit was stopped.",
        },
        "user_message": {
            "zh": "合同审计失败，请检查当前服务日志后重试。",
            "en": "Contract audit failed. Check the current service logs, then retry.",
        },
        "operator_scenarios": {
            "zh": [
                "合同解析、检索、导出或审计编排链路中出现未覆盖的异常。",
                "最近代码改动后，某个中间产物字段与下游预期不一致。",
            ],
            "en": [
                "An uncovered exception occurred in parsing, retrieval, export, or audit orchestration.",
                "A recent code change introduced a mismatch between an intermediate payload and downstream expectations.",
            ],
        },
        "operator_actions": {
            "zh": [
                "查看 main.log 中本次 audit_id 对应的异常栈。",
                "结合错误码与最近变更，补充更细的分类分支或回退策略。",
            ],
            "en": [
                "Inspect the exception stack for the current audit_id in main.log.",
                "Use the error code and recent changes to add a more specific classification or fallback strategy.",
            ],
        },
    },
]


def _audit_locale(language: str) -> str:
    return "zh" if str(language or "zh").lower().startswith("zh") else "en"


def _match_audit_error_spec(err: Exception) -> Dict[str, Any]:
    low = str(err or "").lower()
    for spec in _AUDIT_ERROR_SPECS:
        keywords = spec.get("keywords") or []
        if keywords and any(token in low for token in keywords):
            return spec
    return _AUDIT_ERROR_SPECS[-1]


def _build_audit_error_payload(err: Exception, language: str) -> Dict[str, Any]:
    locale = _audit_locale(language)
    spec = _match_audit_error_spec(err)
    return {
        "code": spec["code"],
        "category": spec["category"],
        "title": spec["title"][locale],
        "message": spec["message"][locale],
        "user_message": spec["user_message"][locale],
        "operator_scenarios": list(spec["operator_scenarios"][locale]),
        "operator_actions": list(spec["operator_actions"][locale]),
        "retryable": bool(spec.get("retryable", False)),
        "raw_reason": str(err or ""),
    }


def _build_digitization_meta(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    row = record if isinstance(record, dict) else {}
    return {
        "digitization_used": bool(row.get("digitization_used")),
        "digitization_reused": bool(row.get("reused")),
        "digitization_status": str(row.get("status") or ""),
        "digitization_version": int(row.get("version_no") or 0),
        "digitization_source_sha256": str(row.get("source_sha256") or ""),
        "digitized_docx_available": bool(str(row.get("digitized_docx_path") or "").strip()),
        "digitized_docx_path": str(row.get("digitized_docx_path") or ""),
        "digitization_text_match_ratio": float(row.get("text_match_ratio") or 0),
        "digitization_paragraph_count": int(row.get("paragraph_count") or 0),
        "digitization_table_count": int(row.get("table_count") or 0),
        "digitization_image_count": int(row.get("image_count") or 0),
        "digitization_page_count": int(row.get("page_count") or 0),
        "digitization_layout_retained": bool(row.get("layout_retained")),
        "digitization_ocr_engine": str(row.get("ocr_engine") or ""),
    }


def _set_audit_progress(audit_id: str, status: str, progress: int, stage: str, message: str = "") -> None:
    payload = {
        "audit_id": audit_id,
        "status": status,
        "progress": int(progress),
        "stage": stage,
        "message": message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    should_log = True
    with _audit_progress_lock:
        previous = _audit_progress.get(audit_id)
        if isinstance(previous, dict):
            should_log = any([
                str(previous.get("status") or "") != payload["status"],
                int(previous.get("progress") or 0) != payload["progress"],
                str(previous.get("stage") or "") != payload["stage"],
                str(previous.get("message") or "") != payload["message"],
            ])
        _audit_progress[audit_id] = payload
    if should_log:
        logger.info(
            "contract_audit_progress audit_id=%s status=%s progress=%s stage=%s task=%s message=%s",
            audit_id,
            payload["status"],
            payload["progress"],
            payload["stage"],
            _AUDIT_STAGE_TITLES.get(payload["stage"], payload["stage"]),
            payload["message"],
        )


def _resolve_request_ip(request: Optional[Request]) -> str:
    if request is None:
        return ""
    forwarded = str(request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = str(request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    client = getattr(request, "client", None)
    return str(getattr(client, "host", "") or "")


def _make_audit_progress_writer(cfg: Dict[str, Any], audit_id: str):
    def _update(
        status: str,
        progress: int,
        stage: str,
        message: str = "",
        *,
        stage_result: str = "",
        audit_opinion: str = "",
        payload: Optional[Dict[str, Any]] = None,
        occurred_at_utc: str = "",
    ) -> None:
        _set_audit_progress(audit_id, status, progress, stage, message)
        stage_payload = dict(payload or {})
        stage_payload.setdefault("progress", int(progress))
        stage_payload.setdefault("status", str(status or ""))
        resolved_stage_result = str(
            stage_result or (
                "success" if status == "done" else "failed" if status == "failed" else "running")
        )
        event_key = "contract-audit-progress:" + hashlib.sha1(
            "|".join(
                [
                    str(audit_id or ""),
                    str(status or ""),
                    str(progress),
                    str(stage or ""),
                    str(message or ""),
                    resolved_stage_result,
                ]
            ).encode("utf-8")
        ).hexdigest()
        try:
            write_contract_audit_stage_log(
                cfg,
                audit_id=audit_id,
                stage_code=stage,
                stage_status=str(status or ""),
                stage_result=resolved_stage_result,
                stage_message=message,
                audit_opinion=audit_opinion,
                payload=stage_payload,
                occurred_at_utc=occurred_at_utc,
                event_key=event_key,
            )
        except Exception:
            logger.exception(
                "contract_audit_stage_log_write_failed audit_id=%s stage=%s",
                audit_id,
                stage,
            )

    return _update


def _get_audit_progress(audit_id: str) -> Optional[Dict[str, Any]]:
    with _audit_progress_lock:
        return _audit_progress.get(audit_id)


def _build_retrieval_options(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "audit_mode": payload.get("audit_mode", "rag"),
        "risk_detection_mode": payload.get("risk_detection_mode", "balanced"),
        "region": payload.get("region", ""),
        "date": payload.get("date", ""),
        "industry": payload.get("industry", ""),
        "tax_focus": payload.get("tax_focus", "true"),
        "use_semantic": payload.get("use_semantic"),
        "semantic_weight": payload.get("semantic_weight"),
        "bm25_weight": payload.get("bm25_weight"),
        "candidate_size": payload.get("candidate_size", "25"),
        "rerank_enabled": payload.get("rerank_enabled"),
        "rerank_top_n": payload.get("rerank_top_n", "25"),
        "rerank_mode": payload.get("rerank_mode", "on"),
        "top_k_evidence": payload.get("top_k_evidence"),
        "query_char_limit": payload.get("query_char_limit"),
        "contract_chunk_size": payload.get("contract_chunk_size"),
        "contract_chunk_max": payload.get("contract_chunk_max", "5"),
        "per_chunk_top_k": payload.get("per_chunk_top_k", "5"),
    }


def build_router(cfg):
    router = APIRouter()

    @router.post("/contracts/audit")
    async def audit_contract_file(
        request: Request,
        file: UploadFile = File(...),
        title: str = Form(""),
        language: str = Form("zh"),
        audit_mode: str = Form("rag"),
        risk_detection_mode: str = Form("balanced"),
        region: str = Form(""),
        date: str = Form(""),
        industry: str = Form(""),
        tax_focus: str = Form("true"),
        audit_id: str = Form(""),
        use_semantic: Optional[str] = Form(None),
        semantic_weight: Optional[str] = Form(None),
        bm25_weight: Optional[str] = Form(None),
        candidate_size: Optional[str] = Form("25"),
        rerank_enabled: Optional[str] = Form(None),
        rerank_top_n: Optional[str] = Form("25"),
        rerank_mode: str = Form("on"),
        top_k_evidence: Optional[str] = Form(None),
        query_char_limit: Optional[str] = Form(None),
        contract_chunk_size: Optional[str] = Form(None),
        contract_chunk_max: Optional[str] = Form("5"),
        per_chunk_top_k: Optional[str] = Form("5"),
        current_user: dict = Depends(get_current_user),
        llm=Depends(get_app_llm),
        embedder=Depends(get_app_embedder),
        reranker=Depends(get_app_reranker),
        translator=Depends(get_app_translator),
    ):
        language = _normalize_lang(language, default="zh")
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in [".docx", ".pdf"]:
            raise HTTPException(
                status_code=400, detail="unsupported file type, only docx and pdf are allowed")

        audit_id = str(audit_id or "").strip() or str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        contract_name = str(title or os.path.basename(
            file.filename or "")).strip() or f"contract-{doc_id}"
        operator_id = str(current_user.get("id") or "")
        register_contract_audit_log_context(
            audit_id,
            document_id=doc_id,
            contract_name=contract_name,
            operator_id=operator_id,
            ip_address=_resolve_request_ip(request),
            source_endpoint="/contracts/audit",
            extra={"entrypoint": "contracts.audit"},
        )
        update_progress = _make_audit_progress_writer(cfg, audit_id)
        update_progress("running", 5, "received", "file received")

        cache_dir = os.path.join(
            cfg.get("data_dir", "./data"), "uploads", "cache")
        os.makedirs(cache_dir, exist_ok=True)
        save_path = os.path.join(cache_dir, f"{doc_id}{ext}")
        with open(save_path, "wb") as f:
            f.write(await file.read())

        # Also insert into new upload_log table
        from app.core.database import get_conn
        with get_conn(cfg) as conn:
            conn.execute(
                "INSERT INTO upload_log (file_id, original_filename, file_path, created_at, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (doc_id, file.filename, save_path)
            )
            conn.commit()

        update_progress("running", 10, "saved", "file saved")

        file_size = os.path.getsize(save_path)
        doc_id = insert_document(
            cfg,
            doc_id=doc_id,
            filename=os.path.basename(save_path),
            original_filename=file.filename,
            file_path=save_path,
            file_size=file_size,
            mime_type=getattr(file, "content_type", None),
            user_id=current_user["id"],
            title=contract_name,
            status="active",
            category="contract"
        )

        update_progress("running", 20, "document", "document recorded")
        model_cfg = cfg.get("llm_config") or {}
        try:
            audit_input_path = save_path
            digitization_record: Optional[Dict[str, Any]] = None
            pdf_pipeline_cfg = get_pdf_pipeline_config(cfg)
            if ext == ".pdf" and bool(pdf_pipeline_cfg.get("prefer_digitized_for_audit", True)):
                update_progress(
                    "running",
                    28,
                    "digitizing",
                    "digitizing pdf into editable word",
                )
                resolved_source = resolve_pdf_audit_source(
                    cfg,
                    save_path,
                    document_id=doc_id,
                    original_filename=file.filename,
                    prefer_digitized=True,
                )
                digitization_record = resolved_source.get(
                    "digitization_record")
                audit_input_path = str(
                    resolved_source.get("audit_input_path") or save_path
                )
                update_progress(
                    "running",
                    34,
                    "digitized",
                    "digitized word ready",
                )
            logger.info(
                "contract_audit_start audit_id=%s document_id=%s file=%s audit_input=%s size=%s lang=%s mode=%s risk_detection_mode=%s",
                audit_id,
                doc_id,
                save_path,
                audit_input_path,
                file_size,
                language,
                audit_mode,
                risk_detection_mode
            )
            retrieval_options = _build_retrieval_options({
                "audit_mode": audit_mode,
                "risk_detection_mode": risk_detection_mode,
                "region": region,
                "date": date,
                "industry": industry,
                "tax_focus": tax_focus,
                "use_semantic": use_semantic,
                "semantic_weight": semantic_weight,
                "bm25_weight": bm25_weight,
                "candidate_size": candidate_size,
                "rerank_enabled": rerank_enabled,
                "rerank_top_n": rerank_top_n,
                "rerank_mode": rerank_mode,
                "top_k_evidence": top_k_evidence,
                "query_char_limit": query_char_limit,
                "contract_chunk_size": contract_chunk_size,
                "contract_chunk_max": contract_chunk_max,
                "per_chunk_top_k": per_chunk_top_k,
            })

            def _progress_cb(stage: str, percent: int, message: str = "") -> None:
                update_progress("running", percent, stage, message)

            services = AuditServices(
                llm=llm,
                embedder=embedder,
                reranker=reranker,
                translator=translator,
            )
            result = run_contract_pipeline_bundle(
                cfg,
                services,
                file_path=audit_input_path,
                preview_file_path=save_path,
                lang=language,
                retrieval_options=retrieval_options,
                progress_cb=_progress_cb
            )
            result_meta = result.get("meta") if isinstance(
                result.get("meta"), dict) else {}
            result_meta.update(
                {
                    "source_document_ext": ext,
                    "audit_input_ext": str(os.path.splitext(audit_input_path)[1] or "").lower(),
                    **_build_digitization_meta(digitization_record),
                }
            )
            result["meta"] = result_meta
            insert_contract_audit(
                cfg,
                audit_id=audit_id,
                document_id=doc_id,
                status="done",
                result_json=json.dumps(result.get(
                    "audit"), ensure_ascii=False),
                model_provider=str(model_cfg.get("provider", "")),
                model_name=str(model_cfg.get("model", "")),
                created_at=datetime.now(timezone.utc).isoformat()
            )
            logger.info(
                "contract_audit_done audit_id=%s document_id=%s ocr_used=%s ocr_engine=%s page_count=%s text_length=%s",
                audit_id,
                doc_id,
                (result.get("meta") or {}).get("ocr_used"),
                (result.get("meta") or {}).get("ocr_engine"),
                (result.get("meta") or {}).get("page_count"),
                (result.get("meta") or {}).get("text_length")
            )
            audit_summary = str(
                (result.get("audit") or {}).get("summary") or "").strip()
            update_progress(
                "done",
                100,
                "done",
                "completed",
                stage_result="success",
                audit_opinion=audit_summary,
                payload={
                    "risk_summary": result.get("risk_summary") or {},
                    "meta": result.get("meta") or {},
                },
            )
        except Exception as e:
            logger.exception(
                "contract_audit_failed audit_id=%s document_id=%s file=%s",
                audit_id,
                doc_id,
                save_path
            )
            error_payload = _build_audit_error_payload(e, language)
            progress_message = str(
                error_payload.get("user_message")
                or error_payload.get("message")
                or "audit failed"
            )
            update_progress(
                "failed",
                100,
                "failed",
                progress_message,
                stage_result=error_payload.get("code", "failed"),
                audit_opinion=progress_message,
                payload={"error_detail": error_payload},
            )
            insert_contract_audit(
                cfg,
                audit_id=audit_id,
                document_id=doc_id,
                status="failed",
                result_json=json.dumps(
                    {"error": str(e), "detail": error_payload}, ensure_ascii=False),
                model_provider=str(model_cfg.get("provider", "")),
                model_name=str(model_cfg.get("model", "")),
                created_at=datetime.now(timezone.utc).isoformat()
            )
            raise HTTPException(status_code=500, detail=error_payload)
        finally:
            clear_contract_audit_log_context(audit_id)

        return {
            "audit_id": audit_id,
            "document_id": doc_id,
            "result": result.get("audit"),
            "meta": result.get("meta"),
            "risk_summary": result.get("risk_summary"),
        }

    @router.post("/contracts/{document_id}/pipeline/run")
    def rerun_contract_pipeline(
        request: Request,
        document_id: str,
        payload: Dict[str, Any] = Body(default={}),
        current_user: dict = Depends(get_current_user),
        llm=Depends(get_app_llm),
        embedder=Depends(get_app_embedder),
        reranker=Depends(get_app_reranker),
        translator=Depends(get_app_translator),
    ):
        doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        file_path = str(doc.get("file_path") or "")
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(
                status_code=404, detail="document file not found")

        language = _normalize_lang(
            str((payload or {}).get("language") or "zh"), default="zh")
        audit_id = str((payload or {}).get("audit_id")
                       or "").strip() or str(uuid.uuid4())
        contract_name = str(doc.get("title") or doc.get(
            "original_filename") or doc.get("filename") or document_id)
        register_contract_audit_log_context(
            audit_id,
            document_id=document_id,
            contract_name=contract_name,
            operator_id=str(current_user.get("id") or ""),
            ip_address=_resolve_request_ip(request),
            source_endpoint=f"/contracts/{document_id}/pipeline/run",
            extra={"entrypoint": "contracts.pipeline_rerun"},
        )
        update_progress = _make_audit_progress_writer(cfg, audit_id)
        update_progress("running", 5, "received", "pipeline rerun started")
        ext = str(os.path.splitext(file_path)[1] or "").lower()
        model_cfg = cfg.get("llm_config") or {}
        try:
            audit_input_path = file_path
            digitization_record: Optional[Dict[str, Any]] = None
            pdf_pipeline_cfg = get_pdf_pipeline_config(cfg)
            if ext == ".pdf" and bool(pdf_pipeline_cfg.get("prefer_digitized_for_audit", True)):
                update_progress(
                    "running",
                    18,
                    "digitizing",
                    "digitizing pdf into editable word",
                )
                resolved_source = resolve_pdf_audit_source(
                    cfg,
                    file_path,
                    document_id=document_id,
                    original_filename=str(
                        doc.get("original_filename") or doc.get("filename") or ""),
                    prefer_digitized=True,
                )
                digitization_record = resolved_source.get(
                    "digitization_record")
                audit_input_path = str(
                    resolved_source.get("audit_input_path") or file_path
                )
                update_progress(
                    "running",
                    26,
                    "digitized",
                    "digitized word ready",
                )

            retrieval_options = _build_retrieval_options(payload or {})

            def _progress_cb(stage: str, percent: int, message: str = "") -> None:
                update_progress("running", percent, stage, message)

            services = AuditServices(
                llm=llm,
                embedder=embedder,
                reranker=reranker,
                translator=translator,
            )
            result = run_contract_pipeline_bundle(
                cfg,
                services,
                file_path=audit_input_path,
                preview_file_path=file_path,
                lang=language,
                retrieval_options=retrieval_options,
                progress_cb=_progress_cb,
            )
            result_meta = result.get("meta") if isinstance(
                result.get("meta"), dict) else {}
            result_meta.update(
                {
                    "source_document_ext": ext,
                    "audit_input_ext": str(os.path.splitext(audit_input_path)[1] or "").lower(),
                    **_build_digitization_meta(digitization_record),
                }
            )
            result["meta"] = result_meta
            insert_contract_audit(
                cfg,
                audit_id=audit_id,
                document_id=document_id,
                status="done",
                result_json=json.dumps(result.get(
                    "audit"), ensure_ascii=False),
                model_provider=str(model_cfg.get("provider", "")),
                model_name=str(model_cfg.get("model", "")),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            audit_summary = str(
                (result.get("audit") or {}).get("summary") or "").strip()
            update_progress(
                "done",
                100,
                "done",
                "completed",
                stage_result="success",
                audit_opinion=audit_summary,
                payload={
                    "risk_summary": result.get("risk_summary") or {},
                    "meta": result.get("meta") or {},
                },
            )
            return {
                "audit_id": audit_id,
                "document_id": document_id,
                "result": result.get("audit"),
                "meta": result.get("meta"),
                "risk_summary": result.get("risk_summary"),
            }
        except Exception as e:
            logger.exception(
                "contract_pipeline_rerun_failed audit_id=%s document_id=%s file=%s",
                audit_id,
                document_id,
                file_path,
            )
            error_payload = _build_audit_error_payload(e, language)
            progress_message = str(
                error_payload.get("user_message")
                or error_payload.get("message")
                or "audit failed"
            )
            update_progress(
                "failed",
                100,
                "failed",
                progress_message,
                stage_result=error_payload.get("code", "failed"),
                audit_opinion=progress_message,
                payload={"error_detail": error_payload},
            )
            insert_contract_audit(
                cfg,
                audit_id=audit_id,
                document_id=document_id,
                status="failed",
                result_json=json.dumps(
                    {"error": str(e), "detail": error_payload}, ensure_ascii=False),
                model_provider=str(model_cfg.get("provider", "")),
                model_name=str(model_cfg.get("model", "")),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            raise HTTPException(status_code=500, detail=error_payload)
        finally:
            clear_contract_audit_log_context(audit_id)

    @router.get("/contracts/audit/{audit_id}/progress")
    def get_contract_audit_progress(audit_id: str, current_user: dict = Depends(get_current_user)):
        _ = current_user
        progress = _get_audit_progress(audit_id)
        if not progress:
            raise HTTPException(
                status_code=404, detail="audit progress not found")
        return progress

    @router.get("/contracts/ui-config")
    def get_contract_ui_config(current_user: dict = Depends(get_current_user)):
        _ = current_user
        cfg = get_config()
        ui_cfg = cfg.get("ui_config") if isinstance(
            cfg.get("ui_config"), dict) else {}
        return {
            "show_citation_source": bool(ui_cfg.get("show_citation_source", False)),
            "default_theme": _normalize_theme(ui_cfg.get("default_theme")),
            "preview_continuous_enabled": bool(ui_cfg.get("preview_continuous_enabled", False)),
        }

    @router.get("/contracts/pdf-pipeline/summary")
    def get_contract_pdf_pipeline_summary(current_user: dict = Depends(get_current_user)):
        _ = current_user
        return build_pdf_pipeline_summary(cfg)

    @router.get("/contracts/{document_id}/summary")
    def get_contract_summary(
        document_id: str,
        language: str = Query(default="zh"),
        current_user: dict = Depends(get_current_user),
        llm=Depends(get_app_llm),
    ):
        doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        file_path = str(doc.get("file_path") or "")
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(
                status_code=404, detail="document file not found")

        source_filename = str(
            doc.get("original_filename") or doc.get(
                "filename") or f"{document_id}.docx"
        ).strip()
        audit_input_path = file_path
        ext = str(os.path.splitext(file_path)[1] or "").lower()
        pdf_pipeline_cfg = get_pdf_pipeline_config(cfg)
        if ext == ".pdf" and bool(pdf_pipeline_cfg.get("prefer_digitized_for_audit", True)):
            resolved_source = resolve_pdf_audit_source(
                cfg,
                file_path,
                document_id=document_id,
                original_filename=source_filename,
                prefer_digitized=True,
            )
            audit_input_path = str(resolved_source.get(
                "audit_input_path") or file_path)

        summary_audit_id = f"summary_{document_id}_{uuid.uuid4().hex[:12]}"
        norm_lang = _normalize_lang(str(language or "zh"), default="zh")
        preview_clauses = []
        try:
            text, meta = extract_text_with_config(cfg, audit_input_path)
            preview_clauses = _build_preview_clauses(text)
            summary_result = _build_contract_summary(
                cfg=cfg,
                llm=llm,
                text=text,
                lang=norm_lang,
                preview_clauses=preview_clauses,
                page_count=int(meta.get("page_count") or 0),
                audit_id=summary_audit_id,
                source_filename=source_filename,
            )
        except Exception as exc:
            logger.exception(
                "contract_summary_endpoint_failed document_id=%s file=%s",
                document_id,
                audit_input_path,
            )
            summary_result = {
                "enabled": bool(cfg.get("contract_summary_enabled", False)),
                "text": "",
                "status": "error",
                "source": "error",
                "error": str(exc),
            }

        summary_text = str(summary_result.get("text") or "").strip()
        return {
            "document_id": document_id,
            "summary": summary_text,
            "meta": {
                "contract_summary_enabled": bool(summary_result.get("enabled", False)),
                "contract_summary_status": str(summary_result.get("status") or ""),
                "contract_summary_chars": len(summary_text),
                "contract_summary_source": str(summary_result.get("source") or ""),
                "contract_summary_prompt_id": str(summary_result.get("prompt_id") or ""),
                "contract_summary_error": str(summary_result.get("error") or ""),
                "contract_summary_language": norm_lang,
                "preview_clause_total": len(preview_clauses),
            },
        }

    @router.get("/contracts/{document_id}/preview-manifest")
    def get_contract_preview_manifest(
        document_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        file_path = str(doc.get("file_path") or "")
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(
                status_code=404, detail="document file not found")
        manifest = build_contract_preview_manifest(
            cfg=cfg,
            document_id=document_id,
            file_path=file_path,
            mime_type=str(doc.get("mime_type") or ""),
        )
        pages = []
        for page in manifest.get("pages") or []:
            page_no = int(page.get("page_no") or 0)
            item = {k: v for k, v in page.items() if k != "image_file"}
            item["image_api"] = f"/contracts/{document_id}/preview/pages/{page_no}/image" if page_no > 0 and page.get(
                "image_file") else ""
            pages.append(item)
        return {
            "document_id": document_id,
            "filename": doc.get("original_filename") or doc.get("filename"),
            "mime_type": doc.get("mime_type"),
            "mode": manifest.get("mode", "text"),
            "source": manifest.get("source", "text_fallback"),
            "meta": manifest.get("meta") or {},
            "coord_unit": (manifest.get("meta") or {}).get("coord_unit", ""),
            "coord_origin": (manifest.get("meta") or {}).get("coord_origin", ""),
            "coord_provider": (manifest.get("meta") or {}).get("coord_provider", ""),
            "pages": pages,
            "text": manifest.get("text", ""),
        }

    @router.get("/contracts/{document_id}/preview/pages/{page_no}/image")
    def get_contract_preview_page_image(
        document_id: str,
        page_no: int,
        current_user: dict = Depends(get_current_user),
    ):
        if page_no <= 0:
            raise HTTPException(status_code=400, detail="invalid page number")
        doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        file_path = str(doc.get("file_path") or "")
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(
                status_code=404, detail="document file not found")
        manifest = build_contract_preview_manifest(
            cfg=cfg,
            document_id=document_id,
            file_path=file_path,
            mime_type=str(doc.get("mime_type") or ""),
        )
        try:
            page = find_preview_page(manifest, page_no)
        except ValueError:
            raise HTTPException(
                status_code=404, detail="preview page not found")
        image_file = str(page.get("image_file") or "")
        if not image_file or not os.path.exists(image_file):
            raise HTTPException(
                status_code=404, detail="preview image not found")
        return FileResponse(image_file, media_type="image/png")

    @router.get("/contracts/{document_id}/source")
    def download_contract_source(
        document_id: str,
        variant: str = Query("original"),
        current_user: dict = Depends(get_current_user),
    ):
        doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        source_variant = str(variant or "original").strip().lower()
        if source_variant not in {"original", "digitized"}:
            raise HTTPException(
                status_code=400, detail="variant must be original or digitized")

        if source_variant == "digitized":
            digitization_record = get_latest_successful_document_digitization_by_document(
                cfg, document_id
            )
            digitized_path = str(
                (digitization_record or {}).get("digitized_docx_path") or ""
            ).strip()
            if not digitized_path or not os.path.exists(digitized_path):
                raise HTTPException(
                    status_code=404, detail="digitized word document not found")
            return FileResponse(
                digitized_path,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                filename=f"contract_digitized_source_{document_id}.docx",
            )

        file_path = str(doc.get("file_path") or "")
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(
                status_code=404, detail="document file not found")
        mime_type = str(doc.get("mime_type") or "").strip() or None
        filename = str(doc.get("original_filename") or doc.get(
            "filename") or os.path.basename(file_path))
        return FileResponse(file_path, media_type=mime_type, filename=filename)

    @router.get("/contracts/{document_id}/preview")
    def preview_contract_document(
        document_id: str,
        clause_limit: int = Query(2000, ge=1, le=10000),
        current_user: dict = Depends(get_current_user),
    ):
        _ = clause_limit
        doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        file_path = str(doc.get("file_path") or "")
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(
                status_code=404, detail="document file not found")
        text, meta = extract_text_with_config(cfg, file_path)
        return {
            "document_id": document_id,
            "filename": doc.get("original_filename") or doc.get("filename"),
            "mime_type": doc.get("mime_type"),
            "meta": {
                "text_length": len(text),
                "ocr_used": bool(meta.get("ocr_used")),
                "ocr_engine": meta.get("ocr_engine", ""),
                "page_count": int(meta.get("page_count") or 0),
                "line_total": len(text.splitlines()),
                "clause_total": 0,
                "preview_mode": "full_text",
            },
            "text": text,
            "clauses": [],
        }

    @router.post("/contracts/{document_id}/report/export")
    def export_contract_report(
        document_id: str,
        payload: Dict[str, Any] = Body(default={}),
        current_user: dict = Depends(get_current_user),
    ):
        doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        audit_row = get_latest_contract_audit_by_document(
            cfg, document_id, status="done")
        if not audit_row:
            raise HTTPException(
                status_code=404, detail="contract audit result not found")
        try:
            audit = json.loads(str(audit_row.get("result_json") or "{}"))
        except Exception:
            audit = {}
        if not isinstance(audit, dict):
            audit = {}
        fmt = str((payload or {}).get("export_format") or "json").lower()
        if fmt not in {"json", "docx"}:
            raise HTTPException(
                status_code=400, detail="only json/docx export is supported")
        template_version = str((payload or {}).get(
            "template_version") or "v1.0")
        locale = str((payload or {}).get("locale") or "zh-CN")
        brand = str((payload or {}).get("brand") or "")
        risks = audit.get("risks") if isinstance(
            audit.get("risks"), list) else []
        citations = audit.get("citations") if isinstance(
            audit.get("citations"), list) else []
        citation_map = {str(c.get("citation_id") or "")
                            : c for c in citations if isinstance(c, dict)}
        risk_summary = {"high": 0, "medium": 0, "low": 0}
        risk_items = []
        evidence_items = []
        for idx, r in enumerate(risks, start=1):
            if not isinstance(r, dict):
                continue
            level = str(r.get("level") or "medium").lower()
            level = level if level in {"high", "medium", "low"} else "medium"
            risk_summary[level] += 1
            location = r.get("location") if isinstance(
                r.get("location"), dict) else {}
            issue_id = f"r{idx}"
            citation_id = str(r.get("citation_id") or "")
            citation = citation_map.get(citation_id, {})
            issue_text = str(r.get("issue") or "")
            suggestion = str(r.get("suggestion") or "")
            evidence_text = str(r.get("evidence") or "")
            risk_items.append({
                "issue_id": issue_id,
                "risk_level": level,
                "issue_text": issue_text,
                "suggestion": suggestion,
                "reviewer_status": "pending",
                "reviewer_note": "",
                "clause": {
                    "clause_id": str(location.get("clause_id") or ""),
                    "clause_path": str(location.get("clause_path") or ""),
                    "clause_text": str(location.get("quote") or ""),
                    "page_no": int(location.get("page_no") or 0),
                    "paragraph_no": str(location.get("paragraph_no") or ""),
                },
                "rule": {
                    "rule_id": citation_id,
                    "law_title": str(citation.get("law_title") or r.get("law_title") or ""),
                    "article_no": str(citation.get("article_no") or r.get("article_no") or ""),
                    "source_text": str(citation.get("content") or citation.get("excerpt") or ""),
                },
            })
            if evidence_text:
                evidence_items.append({
                    "issue_id": issue_id,
                    "law_title": str(citation.get("law_title") or r.get("law_title") or ""),
                    "article_no": str(citation.get("article_no") or r.get("article_no") or ""),
                    "source_text": evidence_text,
                    "source_page": int(location.get("page_no") or 0),
                    "source_paragraph": str(location.get("paragraph_no") or ""),
                    "clause_id": str(location.get("clause_id") or ""),
                    "clause_path": str(location.get("clause_path") or ""),
                })
        report = {
            "contract_id": document_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "overview": {
                "contract_filename": doc.get("original_filename") or doc.get("filename") or "",
                "contract_parse_status": "done",
                "clause_count": 0,
                "issue_count": len(risk_items),
                "trace_count": 0,
            },
            "risk_summary": risk_summary,
            "review_summary": {"pending": len(risk_items), "confirmed": 0, "exception": 0},
            "risk_items": risk_items,
            "evidence_items": evidence_items,
            "review_conclusions": [],
            "exception_items": [],
        }
        report_dir = os.path.join(cfg["files_dir"], "contract_reports")
        os.makedirs(report_dir, exist_ok=True)
        ext = "json" if fmt == "json" else "docx"
        filename = f"contract_audit_report_{document_id}.{ext}"
        output_path = os.path.join(report_dir, filename)
        if fmt == "json":
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            media_type = "application/json"
        else:
            export_mode = str((payload or {}).get(
                "export_mode") or "report").lower()
            digitization_record = get_latest_successful_document_digitization_by_document(
                cfg, document_id
            )
            if export_mode == "digitized_source":
                digitized_path = str(
                    (digitization_record or {}).get(
                        "digitized_docx_path") or ""
                )
                if not digitized_path or not os.path.exists(digitized_path):
                    raise HTTPException(
                        status_code=404,
                        detail="digitized word document not found",
                    )
                return FileResponse(
                    digitized_path,
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    filename=f"contract_digitized_source_{document_id}.docx",
                )
            if export_mode == "comments":
                # Export original contract with comments (M2)
                from app.services.docx_modifier import insert_risk_comments
                original_file = str(doc.get("file_path") or "")
                if not original_file or not os.path.exists(original_file):
                    raise HTTPException(
                        status_code=404, detail="original contract file not found")
                comment_source = original_file
                source_ext = str(os.path.splitext(
                    original_file)[1] or "").lower()
                if source_ext != ".docx":
                    digitized_path = str(
                        (digitization_record or {}).get(
                            "digitized_docx_path") or ""
                    )
                    if not digitized_path or not os.path.exists(digitized_path):
                        raise HTTPException(
                            status_code=404,
                            detail="digitized word document not found",
                        )
                    comment_source = digitized_path
                insert_risk_comments(comment_source, output_path, risk_items)
                if comment_source == original_file:
                    filename = f"contract_with_comments_{document_id}.docx"
                else:
                    filename = f"contract_digitized_with_comments_{document_id}.docx"
            else:
                render_tax_audit_docx(
                    report, output_path, template_version=template_version, locale=locale, brand=brand)
                filename = f"contract_audit_report_{document_id}.docx"

            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return FileResponse(output_path, media_type=media_type, filename=filename)

    return router
