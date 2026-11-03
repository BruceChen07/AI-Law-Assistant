"""Localized audit error specifications (pure data + lookups)."""
from typing import Optional, Dict, Any

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
